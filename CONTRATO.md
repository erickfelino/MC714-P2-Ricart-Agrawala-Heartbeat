# CONTRATO.md — Interfaces entre o nó (Pessoa A) e a infraestrutura (Pessoa B)

**Versão:** 4 (10/11/2026)

Este documento é a referência única entre as duas frentes do trabalho:
- O **nó** (Pessoa A) deve produzir e consumir exatamente o que está aqui.
- O **contador, o ambiente Docker, o verificador e os scripts** (Pessoa B) também.

**Regra:** qualquer mudança é feita **primeiro neste arquivo**, com nova versão no histórico no final, e avisada ao outro.

---

## 1. Convenções gerais

| Item | Definição |
|---|---|
| Linguagem | Python 3.12. O nó e o contador usam **apenas a biblioteca padrão**. Os scripts de análise podem usar bibliotecas externas (por exemplo, matplotlib). |
| IDs dos nós | Inteiros `1..N`. |
| Codificação | UTF-8. |
| Serialização na rede | **JSON, um objeto por linha** (terminado em `\n`), via TCP. |
| Horário nos logs | `t_us` = `time.time_ns() // 1000` (microssegundos, relógio de parede). Todos os contêineres usam o relógio da mesma máquina, então os horários são comparáveis. **Todo `t_us` está no relógio dos contêineres**, inclusive o do `KILL`, que é escrito pelo script no computador e convertido (Seção 9). |
| Unidades de tempo na configuração | Sufixo indica a unidade: `_MS` = milissegundos, `_S` = segundos. |

---

## 2. Estrutura do repositório

```
CONTRATO.md            ← este arquivo
compose.yaml           ← padrão: 3 nós + contador (B)
Dockerfile             ← imagem única para node e counter (B)
.dockerignore
.gitignore
Makefile / README.md   ← construir, iniciar, testar, remover (B)
node/                  ← nó (A)            → executado com: python -m node
counter/               ← contador (B)      → executado com: python -m counter
checker/               ← verificador de logs (B)
scripts/               ← geração de compose, execução, injeção de falhas, experimentos, análise (B)
tests/                 ← logs de exemplo (corretos e com erros de propósito) e testes do verificador (B)
results/raw/<run_id>/  ← logs, compose usado e dados brutos de cada execução (B)
results/figures/       ← gráficos e tabelas finais (B)
report/                ← relatório (A e B)
docs/                  ← plano e anotações internas
```

**Imagem Docker:** uma só, baseada em `python:3.12-slim`, com diretório de trabalho `/app`. Contém apenas `node/` e `counter/`. Cada serviço do compose só muda o `command`.

**Logs:** todos os serviços montam a mesma pasta do computador em `/logs` (variável `LOGS_HOST` no compose; padrão `./results/raw/_dev/logs`).

**O que A precisa garantir:** `python -m node` (rodando em `/app`) inicia o nó lendo apenas variáveis de ambiente.

---

## 3. Endereços

| Serviço | Hostname no compose | Porta |
|---|---|---|
| Nó `i` | `node<i>` (`node1`, `node2`, ...) | `5000` |
| Contador | `counter` | `6000` |

Os endereços **não são fixos no código**. Eles chegam por configuração (Seção 6), o que permite rodar sem Docker (por exemplo, `127.0.0.1:5001`, `127.0.0.1:5002`, ...). O contador escuta na porta indicada em `COUNTER_ADDR`.

---

## 4. Mensagens entre nós (TCP, porta do nó)

**Conexões**
- Cada nó escuta na porta da sua própria entrada em `PEERS`.
- Para enviar, abre ou reutiliza uma conexão TCP com o destino. Se a conexão falhar, ela é descartada e o próximo envio tenta reconectar.
- Toda operação de conexão e de envio usa timeout (`CONNECT_TIMEOUT_MS`).
- A abertura de conexão (`connect`) é feita **fora de qualquer lock compartilhado entre destinos**: um par morto não pode atrasar o envio aos outros.
- **Desacoplamento de concorrência local:** chamadas de envio (`send_message`) são feitas **fora** do lock do Ricart-Agrawala (`_lock`), para que o processamento de mensagens recebidas não fique bloqueado.
- **A ordem FIFO por destino deve ser mantida**, inclusive com atraso injetado (Seção 4.3).

### 4.1 Formatos

Campos comuns a todas as mensagens:

| Campo | Tipo | Descrição |
|---|---|---|
| `type` | string | `"REQUEST"`, `"REPLY"` ou `"HEARTBEAT"` |
| `from` | int | ID de quem envia |
| `to` | int | ID do destino |

**REQUEST** é o pedido para entrar na região crítica. O mesmo `req_ts` vai para todos os pares.

```json
{"type": "REQUEST", "from": 2, "to": 3, "lamport": 17, "req_ts": 17}
```

**REPLY** é a permissão. `req_ts` é o timestamp do pedido **que está sendo respondido**.

```json
{"type": "REPLY", "from": 3, "to": 2, "lamport": 21, "req_ts": 17}
```

**HEARTBEAT** é o sinal de "estou vivo". Não carrega nem altera o relógio de Lamport. `seq` é um contador crescente por nó, começando em 0.

```json
{"type": "HEARTBEAT", "from": 3, "to": 2, "seq": 42}
```

### 4.2 Regras do relógio de Lamport
- **Evento local ou envio:** `L = L + 1`. O broadcast de `REQUEST` conta como **um** evento, e todos os pares recebem o mesmo `req_ts = L`.
- **Recebimento de `REQUEST` ou `REPLY`:** `L = max(L, msg.lamport) + 1`.
- **`HEARTBEAT`:** não altera `L`.
- **Prioridade entre pedidos:** o menor par `(req_ts, id)` vence.
- **`REPLY` antigo:** um `REPLY` cujo `req_ts` não corresponde ao pedido atual é ignorado (mas registrado como `RECV`).

### 4.3 Atraso injetado (para os experimentos)
- Cada mensagem **entre nós** (não para o contador) é atrasada em `NET_DELAY_MS + uniforme(0, NET_JITTER_MS)`, sorteado com o gerador `net` (Seção 6.2).
- **O horário de envio é calculado quando a mensagem entra na fila**, e não quando a anterior termina de sair. Assim o atraso **não se acumula**: 10 mensagens seguidas com `NET_DELAY_MS=50` chegam todas com ~50 ms de atraso.
- **A ordem FIFO por destino deve ser preservada.** Cada destino tem **uma fila e uma thread de envio**, e o horário de envio de cada mensagem é `max(agora + atraso, horário de envio da mensagem anterior para o mesmo destino)`.
- Com `NET_DELAY_MS=0` e `NET_JITTER_MS=0`, não há atraso.

---

## 5. Protocolo do contador (TCP, porta de `COUNTER_ADDR`, padrão 6000)

O contador é o **recurso compartilhado**. Ele **não tem lock nem incremento atômico**: oferece apenas `READ` e `WRITE` separados. Cada operação individual é atômica, mas nada impede outro nó de escrever entre o `READ` e o `WRITE` de alguém. É exatamente isso que a exclusão mútua precisa evitar.

**Servidor e cliente**
- O servidor escuta em `0.0.0.0` e trata cada conexão TCP em uma **thread própria**, para que o `accept()` nunca fique bloqueado por um cliente.
- O cliente do nó (`counter_client`) **reconecta automaticamente com retentativas** se o contador ainda não estiver disponível durante a subida do ambiente.
- Requisições e respostas são delimitadas por quebra de linha (`\n`). Cada nó usa uma conexão TCP, reutilizada.

| Requisição | Resposta |
|---|---|
| `{"op": "READ", "node": 2}` | `{"ok": true, "value": 41}` |
| `{"op": "WRITE", "node": 2, "value": 42}` | `{"ok": true}` |

**Regras**
- O valor inicial é `0`.
- Em caso de erro, a resposta é `{"ok": false, "error": "<mensagem>"}`.
- O contador grava `counter.jsonl` em `LOG_DIR`, uma linha por operação, com flush a cada linha. A ordem das linhas é exatamente a ordem em que as operações foram aplicadas.

```json
{"t_us": 1762358400123456, "event": "READ",  "node": 2, "value": 41}
{"t_us": 1762358400175012, "event": "WRITE", "node": 2, "value": 42}
```

**Comportamento do nó na região crítica**
1. `READ`, obtendo `v`.
2. Espera `CS_DURATION_MS`.
3. `WRITE` com `v + 1`.
4. **Resiliência a falhas (`try...finally`):** mesmo em caso de timeout ou erro de comunicação com o contador, o nó registra `EXIT` e responde aos pedidos adiados. Nesse caso, o `EXIT` sai com `written: null` (e `read: null` se o próprio `READ` falhou).

---

## 6. Configuração do nó (variáveis de ambiente)

### 6.1 Variáveis

| Variável | Exemplo / padrão | Descrição |
|---|---|---|
| `NODE_ID` | `2` | ID deste nó. Obrigatória. |
| `PEERS` | `1@node1:5000,2@node2:5000,3@node3:5000` | **Todos** os nós, incluindo o próprio, no formato `id@host:porta`. O nó escuta na porta da própria entrada. Obrigatória. |
| `COUNTER_ADDR` | `counter:6000` | Endereço do contador. |
| `LOG_DIR` | `/logs` | Pasta onde o log é gravado. |
| `RUN_ID` | `""` | Identificador da execução. Só é copiado para o log de `START`. |
| `SEED` | `1` | Semente base dos geradores aleatórios. |
| `START_DELAY_S` | `3` | Espera, a partir do `START`, antes de gerar o primeiro pedido e de começar a suspeitar de pares, para dar tempo de todos subirem. |
| `DURATION_S` | `60` | Tempo durante o qual o nó gera pedidos, **contado a partir do fim do `START_DELAY_S`**. |
| `DRAIN_S` | `5` | Depois de `DURATION_S`, tempo em que o nó continua respondendo a pedidos **e enviando heartbeats** antes de sair. |
| `REQ_INTERVAL_MS` | `200` | Média do intervalo entre o fim de uma região crítica e o próximo pedido, com distribuição exponencial. |
| `CS_DURATION_MS` | `50` | Espera entre o `READ` e o `WRITE` dentro da região crítica. |
| `HB_INTERVAL_MS` | `500` | Intervalo `T` entre heartbeats. |
| `HB_TIMEOUT_MS` | `2000` | Tempo sem heartbeat de um par até marcá-lo como suspeito. |
| `HB_CHECK_MS` | `100` | Período em que o detector verifica os timeouts. |
| `NET_DELAY_MS` | `0` | Atraso fixo injetado em cada mensagem entre nós. |
| `NET_JITTER_MS` | `0` | Atraso aleatório adicional máximo (uniforme). |
| `CONNECT_TIMEOUT_MS` | `1000` | Timeout de conexão e de envio TCP, também usado com o contador. |

**Duração total de um nó:** aproximadamente `START_DELAY_S + DURATION_S + DRAIN_S` (68 s com os valores padrão). Os scripts usam essa soma para saber quando a execução termina.

### 6.2 Geradores aleatórios

Para garantir reprodutibilidade, cada nó usa **dois** geradores independentes:
- `random.Random(f"load-{SEED}-{NODE_ID}")`: intervalos entre pedidos.
- `random.Random(f"net-{SEED}-{NODE_ID}")`: atraso injetado.

O mesmo `SEED` produz a mesma carga em execuções diferentes.

---

## 7. Ciclo de vida do nó

1. Lê a configuração e registra `START` com **toda a configuração**.
2. Abre o servidor TCP e começa a enviar heartbeats imediatamente.
3. Aguarda `START_DELAY_S` e então:
   - ativa a detecção de falhas, contando os timeouts de todos os pares a partir desse instante;
   - começa a gerar pedidos.
4. Gera pedidos durante `DURATION_S`. Um pedido em andamento ao fim desse tempo é concluído normalmente.
5. Depois de `DURATION_S`:
   - **não faz novos pedidos**;
   - **o detector para de marcar suspeitos**, para que nós encerrando normalmente não sejam confundidos com falhas;
   - **continua enviando heartbeats e respondendo `REQUEST`** por `DRAIN_S`. Isso evita que um nó que subiu um pouco depois dos outros (e ainda está detectando) suspeite de quem já está encerrando;
   - ao fim do `DRAIN_S`, para os heartbeats.
6. Executa a limpeza dos componentes em bloco `finally`, registra obrigatoriamente `STOP` e encerra com código de saída 0.

**Nós suspeitos (modelo crash-stop):** um par suspeito é tratado como morto para sempre.
- Não se espera mais `REPLY` dele.
- Os pedidos dele são removidos da fila de adiados.
- Mensagens `REQUEST`/`REPLY` recebidas dele são registradas como `RECV` e ignoradas.
- Heartbeats continuam sendo enviados a ele (geram `SEND` ou `SEND_FAIL` no log).

---

## 8. Formato do log

**Arquivo:** `LOG_DIR/node-<NODE_ID>.jsonl`, em JSON Lines, um evento por linha.

**Importante:** fazer **flush após cada linha**. Um nó morto com `docker kill` precisa deixar o log em disco até o último evento.

### 8.1 Campos comuns a todos os eventos

| Campo | Tipo | Descrição |
|---|---|---|
| `t_us` | int | `time.time_ns() // 1000` |
| `node` | int | `NODE_ID` |
| `event` | string | Tipo do evento (Seção 8.2) |
| `lamport` | int | Valor do relógio de Lamport **após** o evento |

### 8.2 Eventos

| Evento | Campos extras | Quando registrar |
|---|---|---|
| `START` | `config` (objeto com todas as variáveis da Seção 6) | Logo ao iniciar. |
| `REQUEST` | `req_ts` | Ao decidir pedir a região crítica, **antes** de enviar os `REQUEST`. |
| `ENTER` | `req_ts` | **Depois** de receber todas as respostas necessárias, **antes** do `READ`. |
| `EXIT` | `req_ts`, `read`, `written` | **Depois** da confirmação do `WRITE` e **antes** de enviar os `REPLY` adiados. `read`/`written` podem ser `null` se o contador falhar (Seção 5). |
| `SEND` | `msg` (`REQUEST`, `REPLY` ou `HEARTBEAT`), `to`, `req_ts` (se houver) | No momento em que a mensagem é escrita no socket, depois do atraso injetado. |
| `SEND_FAIL` | `msg`, `to`, `error`, `req_ts` (se houver) | Quando o envio falha (conexão recusada, timeout). |
| `RECV` | `msg`, `from`, `req_ts` (se houver) | Ao receber `REQUEST` ou `REPLY`. **`HEARTBEAT` recebido não é registrado**, para não inflar o log. |
| `SUSPECT` | `peer`, `last_hb_us` | Ao marcar um par como suspeito. `last_hb_us` é o `t_us` do último heartbeat recebido dele; se nunca recebeu nenhum, é o instante em que a detecção foi ativada (passo 3 da Seção 7). |
| `STOP` | `reason` (`"duration"`) | Ao encerrar normalmente. |

**Por que a ordem importa:** com essas regras, para dois nós que se revezam na região crítica, o `EXIT` de um sempre tem `t_us` menor que o `ENTER` do outro. Assim o verificador detecta sobreposição comparando apenas os horários.

**Atenção: `SEND` não significa "entregue".** Significa que a mensagem foi escrita no socket local. Depois de um `docker kill`, os envios para o nó morto continuam aparecendo como `SEND` por um tempo, porque o TCP aceita os dados no buffer mesmo sem ninguém do outro lado. As métricas devem levar isso em conta (Seção 10).

### 8.3 Exemplos

```json
{"t_us": 1762358400000000, "node": 2, "event": "START", "lamport": 0, "config": {"NODE_ID": 2, "PEERS": "1@node1:5000,2@node2:5000,3@node3:5000", "SEED": 1, "DURATION_S": 60, "...": "..."}}
{"t_us": 1762358403100000, "node": 2, "event": "REQUEST", "lamport": 5, "req_ts": 5}
{"t_us": 1762358403100210, "node": 2, "event": "SEND", "lamport": 5, "msg": "REQUEST", "to": 1, "req_ts": 5}
{"t_us": 1762358403101950, "node": 2, "event": "RECV", "lamport": 8, "msg": "REPLY", "from": 1, "req_ts": 5}
{"t_us": 1762358403102400, "node": 2, "event": "ENTER", "lamport": 10, "req_ts": 5}
{"t_us": 1762358403155000, "node": 2, "event": "EXIT", "lamport": 10, "req_ts": 5, "read": 41, "written": 42}
{"t_us": 1762358409000000, "node": 2, "event": "SUSPECT", "lamport": 30, "peer": 3, "last_hb_us": 1762358406950000}
{"t_us": 1762358468000000, "node": 2, "event": "STOP", "lamport": 512, "reason": "duration"}
```

---

## 9. Log do injetor de falhas (Pessoa B)

**Arquivo:** `LOG_DIR/injector.jsonl`. Registra o instante exato de cada falha, usado para medir o tempo de detecção.

```json
{"t_us": 1762358407000000, "event": "KILL", "target": 3}
```

**Regras**
- Registrar `KILL` imediatamente **antes** de executar `docker compose kill node<target>`.
- **O `t_us` do `KILL` precisa estar no relógio dos contêineres.** O injetor roda no computador, fora do Docker. No Windows e no Mac, os contêineres rodam numa máquina virtual cujo relógio pode estar diferente do relógio do computador. Sem correção, o tempo de detecção (`SUSPECT.t_us − KILL.t_us`) mistura os dois relógios.
- **Correção (método de Cristian):** antes do kill, o injetor estima o deslocamento `θ` entre o relógio dos contêineres e o seu:
  1. abre um processo dentro do contêiner do contador e faz várias trocas pergunta/resposta com ele;
  2. em cada troca, anota `t1` (envio), a hora `tc` respondida pelo contêiner e `t2` (resposta recebida);
  3. supõe que `tc` corresponde ao meio da ida e volta: `θ = tc − (t1 + t2) / 2`;
  4. fica com a amostra de menor ida e volta (`RTT = t2 − t1`), cujo erro máximo é `RTT / 2`.
- O `KILL` é gravado com `t_us = hora do computador + θ`. Nenhum relógio é ajustado: só esse horário é corrigido.
- `θ` e o `RTT` da amostra usada ficam em `run.json` (`clock_offset_us` e `clock_offset_rtt_us`) para cada execução.

---

## 10. O que o verificador vai checar

O nó (A) precisa cumprir isto, e o verificador (B) testa exatamente isto.

| Propriedade | Como é verificada |
|---|---|
| **Exclusão mútua** | Os intervalos `[ENTER, EXIT]` de nós diferentes nunca se sobrepõem. Para um nó morto na região crítica (tem `ENTER` sem `EXIT`), o intervalo vai do `ENTER` até o `KILL`. |
| **Contador correto** | Valor do último `WRITE` em `counter.jsonl` = número de eventos `EXIT` com `written` diferente de `null`. |
| **Vivacidade** | Todo `REQUEST` de um nó que não foi morto tem `ENTER` correspondente (mesmo `req_ts`) antes do `STOP` desse nó. |
| **Sem suspeitas falsas** | Em execuções sem `KILL`, não existe nenhum `SUSPECT`. Com `KILL`, só existe `SUSPECT` do nó morto e sempre depois do `KILL`. |
| **Completude** | Com `KILL` do nó `k`, todo nó que não foi morto registra `SUSPECT` com `peer = k`. |

**Métricas extraídas dos logs**
- **Tempo de espera:** `ENTER.t_us − REQUEST.t_us`, mesmo nó e mesmo `req_ts`.
- **Mensagens por entrada:** contagem de `SEND` de tipo `REQUEST` e `REPLY` dividida pelo número de `ENTER`, **ignorando os envios para um nó morto feitos depois do seu `KILL`** (ver Seção 8.2). Teoria: `2(N−1)` sem falhas.
- **Tempo de detecção:** `SUSPECT.t_us − KILL.t_us`, para cada nó vivo.
- **Suspeitas falsas:** `SUSPECT` de um nó que não foi morto.
- **Custo do heartbeat:** `SEND` de tipo `HEARTBEAT` por segundo. Teoria: `N(N−1)/T`.
- **Vazão:** número de `EXIT` por segundo, dentro do período de carga.

---

## Histórico de versões

| Versão | Data | Mudança |
|---|---|---|
| 1 | 05/11/2026 | Versão inicial. |
| 2 | 07/11/2026 | Atualização da resiliência do contador (bind em `0.0.0.0`, concorrência multi-thread e retentativas no cliente), desacoplamento do envio de E/S fora dos locks do Ricart-Agrawala, tratamento defensivo (`try...finally`) na Seção Crítica e encerramento garantido de `HEARTBEAT`/`STOP` no ciclo de vida. |
| 3 | 10/11/2026 | **Rede:** uma fila e uma thread de envio por destino, com horário de envio calculado ao enfileirar (o atraso injetado não se acumula) e `connect` fora do lock global. **Ciclo de vida:** heartbeats continuam durante o `DRAIN_S` (evita suspeitas falsas no encerramento); `DURATION_S` contado a partir do fim do `START_DELAY_S`, como no código. **Log:** `SEND` não implica entrega; `RECV` de `HEARTBEAT` não é registrado; `EXIT` pode ter `read`/`written` nulos; `last_hb_us` nunca é nulo. **Verificador:** contador comparado só com `EXIT` que escreveram; seção de métricas restaurada. **Estrutura:** contrato na raiz, nó falso removido (o nó real já existe), pastas `tests/`, `results/figures/` e `docs/` adicionadas; imagem Docker contém só `node/` e `counter/`. |
| 4 | 10/11/2026 | **Relógio do `KILL`:** o `t_us` do injetor passa a ser convertido para o relógio dos contêineres com o método de Cristian (deslocamento estimado pela amostra de menor RTT, erro máximo RTT/2), registrado em `run.json`. |
