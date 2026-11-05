# MC714 — Trabalho 2: Ricart-Agrawala + Detector de Falhas por Heartbeat

**Entrega:** 13/11/2026, no Classroom. Uma pessoa da dupla envia o relatório em PDF.
**Linguagem:** Python.
**Algoritmos escolhidos:**

| Grupo exigido pelo enunciado | Algoritmo |
|---|---|
| Ordenação de eventos, exclusão mútua, eleição ou snapshot | **Ricart-Agrawala** (exclusão mútua distribuída), usando relógio lógico de Lamport |
| Consenso, commit, replicação, **detecção de falhas**, P2P ou coordenação | **Detector de falhas por heartbeat** |

**Por que são complementares:** o Ricart-Agrawala trava se um nó cai, porque todos ficam esperando uma resposta que nunca chega. O detector de falhas identifica o nó morto e permite que os outros parem de esperar por ele.

---

## 1. Visão geral do sistema

- **N nós** (mínimo 3). Cada nó é um processo Python no seu próprio contêiner, e todos rodam o mesmo código com IDs diferentes.
- Os nós se comunicam **somente pela rede** (sockets TCP), com mensagens definidas por nós. Não há memória ou arquivo compartilhado entre eles.
- Cada nó roda os dois algoritmos ao mesmo tempo:
  - **Ricart-Agrawala:** de tempos em tempos o nó quer acessar o recurso compartilhado e pede permissão a todos os outros.
  - **Heartbeat:** a cada intervalo `T` o nó envia "estou vivo" aos outros e marca como suspeito quem fica mais que `timeout` sem enviar.
- **Recurso compartilhado:** um serviço de **contador** em um contêiner separado. Dentro da região crítica, o nó lê o valor, espera um pouco e escreve valor+1.
  - Sem exclusão mútua, as escritas se atropelam e o contador termina errado.
  - Com exclusão mútua, ele termina exato.
  - **O contador NÃO pode oferecer incremento atômico nem lock.** Ele expõe apenas `READ` e `WRITE` separados. Se ele próprio garantisse exclusão, estaríamos usando um componente que já implementa a propriedade principal, o que o enunciado proíbe.
  - O contador é o *recurso protegido*, não parte da coordenação. Mesmo assim, é uma instância única e isso deve ser mencionado como limitação no relatório.

---

## 2. Implementação

### 2.1 Ricart-Agrawala
- Estado de cada nó:
  - relógio de Lamport;
  - estado (`RELEASED`, `WANTED` ou `HELD`);
  - timestamp do próprio pedido;
  - fila de pedidos adiados;
  - conjunto de respostas recebidas.
- Mensagens:
  - `REQUEST(ts, id)`;
  - `REPLY`.
- Ao receber `REQUEST`:
  - responde imediatamente se estiver `RELEASED`, ou se estiver `WANTED` e o pedido do outro tiver prioridade;
  - caso contrário, adia.
  - A prioridade é o menor `(ts, id)`, com o ID desempatando timestamps iguais.
- Entra na região crítica ao receber `REPLY` de **todos os nós não suspeitos**.
- Ao sair, responde a todos os pedidos adiados.
- Um **gerador de carga** dentro do nó decide quando pedir a região crítica, com intervalos aleatórios a partir de uma **semente registrada**.

### 2.2 Detector por heartbeat
- Envia `HEARTBEAT` a todos a cada `T`.
- Marca um nó como suspeito se não recebeu heartbeat dele há mais de `timeout`.
- Modelo crash-stop: o nó suspeito não volta.

### 2.3 Integração entre os dois
- Quando um nó passa a ser suspeito:
  - ele sai do conjunto de nós dos quais se espera `REPLY`;
  - os pedidos dele saem da fila de adiados.
- Se o nó que morreu estava **dentro** da região crítica, os outros seguem após a detecção. O recurso pode ficar inconsistente, por exemplo com um `READ` feito sem o `WRITE` correspondente. Isso é discutido no relatório como limitação.

### 2.4 Regras do enunciado sobre bibliotecas e comunicação
- **Podem** ser usadas bibliotecas de comunicação, serialização (JSON), persistência e logs.
- **Não podem** ser usadas bibliotecas que já implementem exclusão mútua ou detecção de falhas: locks do Redis, locks de arquivo, ZooKeeper etc.
- O enunciado exige timeouts explícitos para chamadas gRPC. Nós usamos sockets, mas **vamos usar timeouts mesmo assim**, porque sem eles um nó pode ficar preso esperando um par morto.
- **Atraso de rede** para os experimentos (ver Seção 5): será injetado **no nível da aplicação**, na camada de envio do nó. É um atraso aleatório configurável com semente registrada, o que funciona em qualquer sistema operacional. A alternativa seria `tc/netem` nos contêineres, mas exige Linux e permissões extras. A escolha deve ser documentada.

---

## 3. Modelo do sistema

O relatório deve declarar e justificar cada item, para cada algoritmo.

| Item | Ricart-Agrawala | Heartbeat |
|---|---|---|
| **Modelo temporal** | Assíncrono para a **segurança**: ela não depende de tempo. Com falhas, a vivacidade depende do detector. | **Parcialmente síncrono**: os atrasos são limitados a partir de algum momento. Sem isso, timeouts não fazem sentido. |
| **Canais** | TCP: confiáveis, ordem FIFO por conexão, sem duplicação. Mensagens só se perdem se o destino falhar. | Idem. |
| **Falhas** | Crash-stop: o nó para e não volta. Sem falhas bizantinas e sem partições. | Crash-stop. |
| **Relógios** | Relógio lógico de Lamport. Não precisa de relógio físico sincronizado. | Apenas temporizadores locais, sem sincronização entre nós. |
| **Persistência e reinicialização** | Sem estado persistido. Nó que cai não reinicia. | Idem. |
| **Segurança** | **Exclusão mútua:** no máximo um nó na região crítica. Com falhas, só vale se o detector não suspeitar de nós vivos (ver abaixo). | **Precisão:** se os atrasos ficarem abaixo do timeout, nenhum nó vivo é suspeito. |
| **Vivacidade** | Todo pedido de um nó que não falhou acaba sendo atendido, sem deadlock nem inanição. | **Completude:** todo nó que falha acaba suspeito por todos os nós vivos. |

**Pontos obrigatórios de discussão:**
1. **Suspeita falsa quebra a exclusão mútua.** Se A suspeita de B, mas B só está lento e está na região crítica, A pode entrar junto. A segurança do sistema combinado depende da hipótese de sincronia parcial.
2. **Nó que morre na região crítica:** qual é o estado do recurso e por que os outros podem prosseguir.
3. **Contêineres numa única máquina** são nós lógicos, mas não domínios de falha independentes. Se a máquina cai, todos caem.
4. **O contador é uma instância única**, ou seja, um ponto único de falha do recurso.

---

## 4. Testes de correção (automatizados)

Cada nó grava um log estruturado (formato definido no contrato, Seção 7.1). Um **verificador** lê os logs e o valor final do contador e responde se o teste passou ou falhou.

O enunciado pede, **para cada algoritmo**, pelo menos um cenário normal e um cenário de falha.

| Teste | Cenário | O que verifica |
|---|---|---|
| RA-normal | N nós, sem falhas | (a) Nunca há sobreposição de `ENTER`/`EXIT` entre nós. (b) Contador final = número de `EXIT`. (c) Todo `REQUEST` gerou `ENTER` dentro de um prazo. |
| RA-falha | Um nó é morto no meio da execução | Os sobreviventes continuam entrando, sem sobreposição. Todo `REQUEST` de nó vivo é atendido. |
| HB-normal | N nós, sem falhas | Nenhum `SUSPECT` é registrado. |
| HB-falha | Um nó é morto | Todo nó vivo registra `SUSPECT` do nó morto dentro do tempo esperado. |

**Observação:** todos os contêineres usam o relógio da mesma máquina, então os horários dos logs são comparáveis para detectar sobreposição. Para isso funcionar, o nó deve:
- registrar `EXIT` **antes** de enviar os `REPLY` adiados;
- registrar `ENTER` **depois** de receber todas as respostas.

---

## 5. Experimentos

O enunciado exige, **para cada algoritmo**:
- uma métrica justificada;
- um parâmetro variado em **3 ou mais níveis**;
- **repetições** com sementes diferentes;
- medidas de tendência e de variabilidade, incluindo **p50, p95 e p99** para latências;
- análise comparando os resultados com a teoria.

### 5.1 Ricart-Agrawala
- **Métricas:**
  - tempo de espera para entrar na região crítica (`REQUEST` → `ENTER`), com média, desvio, p50, p95 e p99;
  - mensagens por entrada;
  - vazão (entradas por segundo).
- **Parâmetro:** número de nós, N = 3, 5 e 7. Opcionalmente, a carga (frequência de pedidos).
- **Repetições:** sugestão de 5 a 10 sementes por nível.
- **Análise:**
  - As mensagens por entrada devem ser exatamente **2(N−1)**: N−1 `REQUEST` mais N−1 `REPLY`.
  - O tempo de espera deve crescer com N e com a carga, pois mais nós disputam a região crítica e cada entrada exige respostas de todos.

### 5.2 Heartbeat
- **Métricas:**
  - tempo de detecção, do `kill` até o `SUSPECT`, com p50, p95 e p99;
  - número de suspeitas falsas;
  - custo em mensagens.
- **Parâmetro:** `timeout` em 3 níveis, com atraso de rede injetado fixo. Alternativa: atraso injetado em 3 níveis com timeout fixo.
  - Sem atraso injetado, as suspeitas falsas tendem a zero e o experimento fica sem graça.
- **Análise:**
  - O tempo de detecção deve ficar aproximadamente entre `timeout − T` e `timeout`, somado ao atraso de rede e ao período de verificação. A variação vem de onde, dentro do intervalo `T`, o crash acontece.
  - Trade-off: timeout curto detecta mais rápido, mas gera mais suspeitas falsas.
  - Custo: N(N−1) heartbeats a cada intervalo `T`.

### 5.3 Para todos os experimentos
- Rodar tudo por script, de forma automatizada e reproduzível.
- Registrar:
  - duração de cada execução;
  - tempo de aquecimento descartado;
  - hardware;
  - sistema operacional;
  - versões de Python, Docker e Compose;
  - configuração completa e sementes.
- Salvar os **dados brutos** no repositório.
- Resultados absolutos de máquinas diferentes não são comparados na correção. O que importa são as tendências e a análise.

---

## 6. Entregáveis

### 6.1 Relatório (PDF)
- Formato **IEEE**, duas colunas, **no máximo 6 páginas**, sem contar as referências.
- **Primeira página:** nome e RA dos dois, link do repositório no GitLab da disciplina e link do vídeo.
- **Seções obrigatórias:**
  - motivação;
  - modelo do sistema;
  - arquitetura;
  - algoritmos;
  - propriedades de correção;
  - testes;
  - cenários experimentais;
  - métricas;
  - resultados;
  - limitações;
  - conclusões.
- **Citar a fonte de todo código reutilizado e descrever as modificações**, inclusive código do trabalho 1, se reaproveitado.

### 6.2 Repositório (GitLab da disciplina)
- Código-fonte e testes.
- `compose.yaml` na raiz, com uma configuração padrão que sobe, por exemplo, 3 nós e o contador.
  - Um script pode gerar compose para outros N nos experimentos, mas o arquivo padrão precisa existir.
- Comandos documentados (README ou Makefile) para **construir, iniciar, testar e remover** o ambiente.
- Configuração do ambiente e instruções de reprodução.
- Scripts dos experimentos e **dados brutos**.
- Cópia do relatório.
- Versão final marcada com a **tag de entrega definida pela disciplina**. Conferir o nome exato.

### 6.3 Vídeo (~5 minutos, com os dois participando)
- Cenário normal: nós entrando e saindo da região crítica e contador correto.
- Uma falha injetada: matar um nó.
- Recuperação ou consequência esperada: detecção e os demais nós seguindo.

### 6.4 Critérios de avaliação

| Critério | Peso |
|---|---|
| Correção dos algoritmos e testes | 30% |
| Modelo, arquitetura e tratamento de falhas | 20% |
| Experimentos e reprodutibilidade | 25% |
| Relatório | 15% |
| Vídeo | 10% |

Itens bônus não serão feitos.

---

## 7. Divisão do trabalho

O trabalho é dividido em duas frentes:
- **Pessoa A** constrói o nó, ou seja, os algoritmos.
- **Pessoa B** constrói tudo em volta dele: ambiente, testes e experimentos.

As duas frentes andam em paralelo graças a um **contrato** definido em conjunto no início.

### 7.1 Passo 0: contrato (já definido em `CONTRATO.md`; os dois só revisam e confirmam)

O arquivo `CONTRATO.md` no repositório é a referência única para os dois. **Qualquer mudança é feita primeiro no contrato e avisada ao outro.** Ele define, com nomes exatos e unidades:

- [ ] **Mensagens entre nós:** `REQUEST`, `REPLY` e `HEARTBEAT`, com seus campos e a serialização (por exemplo, JSON delimitado por linha via TCP).
- [ ] **Protocolo do contador:** `READ` e `WRITE`, com formato de requisição e resposta.
- [ ] **Formato do log:**
  - onde é gravado (pasta e volume);
  - um evento por linha;
  - eventos: `START`, `REQUEST`, `ENTER`, `EXIT`, `SEND`, `RECV`, `SUSPECT`, `STOP`;
  - campos: horário de parede em ms, ID do nó, timestamp de Lamport, tipo de mensagem e par envolvido.
- [ ] **Regras de ordem do log:** `EXIT` registrado antes de enviar os `REPLY` adiados; `ENTER` registrado após receber todas as respostas.
- [ ] **Configuração por variáveis de ambiente:**
  - `NODE_ID`, `PEERS`;
  - `HB_INTERVAL_MS`, `HB_TIMEOUT_MS`;
  - frequência de pedidos;
  - duração da execução;
  - `SEED`;
  - endereço do contador;
  - atraso e jitter injetados.
- [ ] **Ciclo de vida do nó:** como inicia, quanto tempo roda e como termina (por exemplo, sai sozinho após `DURATION`).
- [ ] **Comando de execução do nó:** necessário para o Dockerfile.

### 7.2 Pessoa A: o nó (algoritmos)

**Código**
- [ ] Camada de comunicação: sockets TCP com timeouts, envio e recebimento de mensagens. Pode reaproveitar o trabalho 1, citando no relatório.
- [ ] Injeção de atraso na camada de envio, configurável e com semente.
- [ ] Relógio de Lamport.
- [ ] Ricart-Agrawala: estados, fila de adiados e desempate por ID.
- [ ] Acesso ao recurso na região crítica: `READ`, espera e `WRITE` no contador.
- [ ] Gerador de carga com semente.
- [ ] Detector por heartbeat.
- [ ] Integração: ignorar suspeitos e limpar os pedidos deles.
- [ ] Logs exatamente no formato do contrato.

**Relatório**
- [ ] Arquitetura do nó.
- [ ] Descrição dos algoritmos.
- [ ] Modelo do sistema (Seção 3).
- [ ] Propriedades de segurança e vivacidade.
- [ ] Discussão da suspeita falsa e do nó que morre na região crítica.

### 7.3 Pessoa B: ambiente, testes e experimentos

**Código e scripts**
- [ ] **Nó falso:** programa simples que apenas segue o contrato. Ele gera logs no formato combinado, "entra e sai" da região crítica sem algoritmo nenhum, manda heartbeats e respeita as variáveis de ambiente. Serve para B desenvolver e testar tudo sem esperar A.
- [ ] Serviço do contador, com `READ` e `WRITE` separados e sem lock.
- [ ] Dockerfile, `compose.yaml` padrão e script que gera compose para N nós.
- [ ] Comandos documentados (Makefile e README): construir, iniciar, testar e remover.
- [ ] Verificador de logs com os testes da Seção 4. Testar primeiro com logs escritos à mão: um correto e outros com erros de propósito.
- [ ] Scripts de injeção de falha (`docker kill` de um nó escolhido, em momento definido por semente).
- [ ] Script de experimentos: varia o parâmetro, repete com sementes diferentes, descarta o aquecimento e salva os dados brutos.
- [ ] Extração das métricas (p50, p95, p99, mensagens por entrada, tempo de detecção, suspeitas falsas) e gráficos.
- [ ] Registro de hardware, sistema operacional e versões.

**Relatório**
- [ ] Ambiente e reprodutibilidade.
- [ ] Testes.
- [ ] Cenários experimentais e métricas.
- [ ] Resultados e análise comparada com a teoria. A deve revisar a parte de complexidade.

### 7.4 Tarefas dos dois
- [ ] Contrato (Passo 0).
- [ ] Integração: trocar o nó falso pelo real e rodar tudo.
- [ ] Rodar os testes de correção e os experimentos com o nó real.
- [ ] Relatório: motivação, limitações, conclusões, primeira página e revisão final (inclusive o limite de 6 páginas).
- [ ] Vídeo, com os dois falando.
- [ ] Revisão cruzada do código: os dois precisam saber explicar o sistema inteiro.
- [ ] Tag no GitLab e submissão do PDF no Classroom.

### 7.5 Dependências e independência

Com o contrato definido e o nó falso, **B trabalha sem depender de A até a integração**. O que fica pronto antes dela é a *infraestrutura* de testes e experimentos, não os *resultados*.

Pontos em que um depende do outro:

| Ponto | Quem espera | Observação |
|---|---|---|
| Contrato | Os dois | Precisa existir antes de dividir. |
| Nó real minimamente funcional | B | Testes reais e experimentos só têm valor com o nó real, e os experimentos levam tempo para rodar. |
| Mudanças no contrato | Os dois | Quem muda atualiza o `CONTRATO.md` e avisa. |
| Análise de resultados | A revisa B | A análise de complexidade depende de entender o algoritmo. |

**Recomendação: integrar cedo.** Assim que o Ricart-Agrawala de A funcionar, mesmo sem heartbeat, vale plugar nos scripts de B. Problemas de integração aparecem enquanto ainda há tempo: campo com nome diferente, nó que demora a subir, evento que ninguém registrou.

### 7.6 Equilíbrio e peças flexíveis

- A parte de A é **mais difícil de pensar**: concorrência, e o algoritmo pesa nos 30% de correção.
- A parte de B tem **mais peças, porém mais simples**, e sustenta os 25% de experimentos.

Peças que podem mudar de lado conforme o tempo disponível de cada um:
- **Serviço do contador** e **verificador de logs:** podem ir para A.
- **Gráficos** e **seções de resultados:** podem ir para quem tiver mais tempo no fim.
- **Detector por heartbeat:** é bem independente do Ricart-Agrawala e pode ir para B. Nesse caso, o contrato precisa definir também como o detector avisa o Ricart-Agrawala de que um nó é suspeito.
