# CONTRATO.md — Interfaces entre o nó (Pessoa A) e a infraestrutura (Pessoa B)

**Versão:** 2 (07/11/2026)

Este documento é a referência única entre as duas frentes do trabalho:
- O **nó real** (Pessoa A) deve produzir e consumir exatamente o que está aqui.
- Os **scripts, o verificador, o contador e o nó falso** (Pessoa B) também.

**Regra:** qualquer mudança é feita **primeiro neste arquivo**, com nova versão no histórico no final, e avisada ao outro.

---

## 1. Convenções gerais

| Item | Definição |
|---|---|
| Linguagem | Python 3.12, apenas biblioteca padrão no nó, no contador e no nó falso. |
| IDs dos nós | Inteiros `1..N`. |
| Codificação | UTF-8. |
| Serialização na rede | **JSON, um objeto por linha** (terminado em `\n`), via TCP. |
| Horário nos logs | `t_us` = `time.time_ns() // 1000` (microssegundos, relógio de parede). Todos os contêineres usam o relógio da mesma máquina, então os horários são comparáveis. |
| Unidades de tempo na configuração | Sufixo indica a unidade: `_MS` = milissegundos, `_S` = segundos. |

---

## 2. Estrutura do repositório

```
CONTRATO.md            ← este arquivo
compose.yaml           ← padrão: 3 nós + contador (B)
Dockerfile             ← imagem única para node, fake_node e counter (B)
Makefile / README.md   ← build, up, test, down (B)
node/                  ← nó real (A)       → executado com: python -m node
fake_node/             ← nó falso (B)      → executado com: python -m fake_node
counter/               ← contador (B)      → executado com: python -m counter
checker/               ← verificador de logs (B)
scripts/               ← injeção de falhas, geração de compose, experimentos (B)
results/raw/<run_id>/  ← logs e dados brutos de cada execução (B)
report/                ← relatório (A e B)
```

**Imagem Docker:** uma só, baseada em `python:3.12-slim`, com o repositório copiado em `/app` e o diretório de trabalho `/app`. Cada serviço do compose só muda o `command`.

**O que A precisa garantir:** `python -m node` (rodando em `/app`) inicia o nó lendo apenas variáveis de ambiente.

---

## 3. Endereços

| Serviço | Hostname no compose | Porta |
|---|---|---|
| Nó `i` | `node<i>` (`node1`, `node2`, ...) | `5000` |
| Contador | `counter` | `6000` |

Os endereços **não são fixos no código**. Eles chegam por configuração (Seção 6), o que permite rodar sem Docker (por exemplo, `127.0.0.1:5001`, `127.0.0.1:5002`, ...) e, se um dia for preciso, na nuvem do IC mudando apenas a configuração.

---

## 4. Mensagens entre nós (TCP, porta do nó)

**Conexões**
- Cada nó escuta na porta da sua própria entrada em `PEERS`.
- Para enviar, abre ou reutiliza uma conexão TCP com o destino.
- Se a conexão falhar, tenta reconectar no próximo envio.
- Toda operação de conexão e de envio usa timeout (`CONNECT_TIMEOUT_MS`).
- **A ordem FIFO por destino deve ser mantida**, inclusive com atraso injetado (Seção 4.2).
- **Desacoplamento de Concorrência Local:** Chamadas de E/S de rede (`send_message`) devem ser executadas **fora** do lock de exclusão mútua local (`_lock`) do nó, garantindo que o processamento imediato de mensagens recebidas pela thread de rede não sofra bloqueios indesejados.

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
- **A ordem FIFO por destino deve ser preservada.** Sugestão: o horário de envio de cada mensagem é `max(agora + atraso, horário de envio da mensagem anterior para o mesmo destino)`.
- Com `NET_DELAY_MS=0` e `NET_JITTER_MS=0`, não há atraso.

---

## 5. Protocolo do contador (TCP, porta 6000)

O contador é o **recurso compartilhado**. Ele **não tem lock nem incremento atômico**: oferece apenas `READ` e `WRITE` separados. Cada operação individual é atômica, mas nada impede outro nó de escrever entre o `READ` e o `WRITE` de alguém. É exatamente isso que a exclusão mútua precisa evitar.

**Conexão e Servidor Multi-thread**
- O servidor do contador deve aceitar conexões escutando em `0.0.0.0:6000` e tratar cada conexão TCP em **threads dedicadas ou concorrentes** (`threading.Thread`), garantindo que chamadas `accept()` não fiquem bloqueadas por requisições síncronas de um único cliente.
- O cliente (`counter_client`) deve implementar mecanismo de **reconexão automática com retentativas** em caso de indisponibilidade inicial durante a subida do ambiente Docker.
- As mensagens de requisição e resposta obrigatoriamente devem ser delimitadas por quebra de linha (`\n`).

| Requisição | Resposta |
|---|---|
| `{"op": "READ", "node": 2}` | `{"ok": true, "value": 41}` |
| `{"op": "WRITE", "node": 2, "value": 42}` | `{"ok": true}` |

**Regras**
- O valor inicial é `0`.
- Em caso de erro, a resposta é `{"ok": false, "error": "<mensagem>"}`.
- O contador grava `counter.jsonl` em `LOG_DIR`, uma linha por operação:

```json
{"t_us": 1762358400123456, "event": "READ",  "node": 2, "value": 41}
{"t_us": 1762358400175012, "event": "WRITE", "node": 2, "value": 42}
```

**Comportamento do nó na região crítica**
1. `READ`, obtendo `v`.
2. Espera `CS_DURATION_MS`.
3. `WRITE` com `v + 1`.
4. **Resiliência a Falhas (Try-Finally):** Todas as operações na Região Crítica devem ser englobadas em blocos `try...finally`. Mesmo em caso de timeout ou erro de I/O na comunicação com o contador, o nó deve executar a liberação da Região Crítica (`EXIT`) e processar sua fila de solicitações adiadas (`deferred_queue`).

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
| `DURATION_S` | `60` | Tempo, a partir do `START`, durante o qual o nó gera pedidos. |
| `START_DELAY_S` | `3` | Espera antes de gerar o primeiro pedido e de começar a suspeitar de pares, para dar tempo de todos subirem. |
| `DRAIN_S` | `5` | Depois de `DURATION_S`, tempo em que o nó continua respondendo a pedidos antes de sair. |
| `REQ_INTERVAL_MS` | `200` | Média do intervalo entre o fim de uma região crítica e o próximo pedido, com distribuição exponencial. |
| `CS_DURATION_MS` | `50` | Espera entre o `READ` e o `WRITE` dentro da região crítica. |
| `HB_INTERVAL_MS` | `500` | Intervalo `T` entre heartbeats. |
| `HB_TIMEOUT_MS` | `2000` | Tempo sem heartbeat de um par até marcá-lo como suspeito. |
| `HB_CHECK_MS` | `100` | Período em que o detector verifica os timeouts. |
| `NET_DELAY_MS` | `0` | Atraso fixo injetado em cada mensagem entre nós. |
| `NET_JITTER_MS` | `0` | Atraso aleatório adicional máximo (uniforme). |
| `CONNECT_TIMEOUT_MS` | `1000` | Timeout de conexão e de envio TCP, também usado com o contador. |

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
   - começa a gerar pedidos;
   - começa a contar os timeouts de heartbeat de todos os pares a partir desse instante.
4. Gera pedidos até completar `DURATION_S` desde o `START`. Um pedido em andamento nesse momento é concluído normalmente.
5. Depois de `DURATION_S`:
   - **não faz novos pedidos**;
   - **o detector para de marcar suspeitos**, para que nós encerrando normalmente não sejam confundidos com falhas;
   - interrompe a transmissão ativa de novos `HEARTBEAT` (`hb.stop()`);
   - continua respondendo `REQUEST` pendentes por `DRAIN_S`.
6. Executa a limpeza dos componentes em bloco `finally`, registra obrigatoriamente `STOP` e encerra com código de saída 0.

**Nós suspeitos (modelo crash-stop):** um par suspeito é treated como morto para sempre.
- Não se espera mais `REPLY` dele.
- Os pedidos dele são removidos da fila de adiados.
- Mensagens recebidas dele são registradas como `RECV` e ignoradas.

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
| `EXIT` | `req_ts`, `read`, `written` | **Depois** da confirmação do `WRITE` e **antes** de enviar os `REPLY` adiados. |
| `SEND` | `msg` (`REQUEST`, `REPLY` ou `HEARTBEAT`), `to`, `req_ts` (se houver) | No momento em que a mensagem é efetivamente escrita no socket, depois do atraso injetado. |
| `SEND_FAIL` | `msg`, `to`, `error` | Quando o envio falha (conexão recusada, timeout). |
| `RECV` | `msg`, `from`, `req_ts` (se houver) | Ao receber uma mensagem. |
| `SUSPECT` | `peer`, `last_hb_us` | Ao marcar um par como suspeito. `last_hb_us` é o `t_us` do último heartbeat recebido dele, ou `null` se nunca recebeu nenhum. |
| `STOP` | `reason` (`"duration"`) | Ao encerrar normalmente. |

**Por que a ordem importa:** com essas regras, para dois nós que se revezam na região crítica, o `EXIT` de um sempre tem `t_us` menor que o `ENTER` do outro. Assim o verificador detecta sobreposição comparando apenas os horários.

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

**Regra:** registrar `KILL` imediatamente **antes** de executar `docker compose kill node<target>`.

---

## 10. O que o verificador vai checar

O nó real (A) precisa cumprir isto, e o verificador (B) testa exatamente isto.

| Propriedade | Como é verificada |
|---|---|
| **Exclusão mútua** | Os intervalos `[ENTER, EXIT]` de nós diferentes nunca se sobrepõem. Para um nó morto na região crítica, o intervalo vai do `ENTER` até o `KILL`. |
| **Contador correto** | Valor do último `WRITE` em `counter.jsonl` = número total de eventos `EXIT`. |
| **Vivacidade** | Todo `REQUEST` de um nó que não foi morto tem `ENTER` correspondente (mesmo `req_ts`) antes do `STOP` desse nó. |
| **Sem suspeitas falsas** | Em execuções sem `KILL`, não existe nenhum `SUSPECT`. Com `KILL`, só existe `SUSPECT` do nó morto e sempre depois do `KILL`. |
| **Completude** | Com `KILL` do nó `k`, todo nó que não foi morto registra `SUSPECT` com `peer = k`. |

---

## 11. O que o nó falso (B) deve imitar

O nó falso respeita as Seções 2, 3, 5, 6, 7 e 8, mas **sem algoritmo nenhum**:
- Gera `REQUEST`, `ENTER` e `EXIT` com os tempos da configuração.
- Acessa o contador de verdade.
- Envia e recebe heartbeats de verdade.

---

## Histórico de versões

| Versão | Data | Mudança |
|---|---|---|
| 1 | 05/11/2026 | Versão inicial. |
| 2 | 07/11/2026 | Atualização da resiliência do contador (bind em `0.0.0.0`, concorrência multi-thread e retentativas no cliente), desacoplamento do envio de E/S fora dos locks do Ricart-Agrawala, tratamento defensivo (`try...finally`) na Seção Crítica e encerramento garantido de `HEARTBEAT`/`STOP` no ciclo de vida. |