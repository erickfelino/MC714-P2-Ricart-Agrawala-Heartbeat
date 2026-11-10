# MC714 — Trabalho 2: Ricart-Agrawala + Detector de Falhas por Heartbeat

Exclusão mútua distribuída com o algoritmo de **Ricart-Agrawala** (relógios de Lamport), combinada com um **detector de falhas por heartbeat**: quando um nó cai, o detector o marca como suspeito e o Ricart-Agrawala para de esperar a permissão dele.

Cada nó é um processo Python num contêiner próprio. Os nós disputam um **contador compartilhado** (`READ` e `WRITE` separados, sem lock): sem exclusão mútua o contador termina errado; com ela, termina exato.

As interfaces entre as partes (mensagens, logs, configuração) estão em [`CONTRATO.md`](CONTRATO.md).

## Requisitos

- Docker com Docker Compose v2 (no Windows e no Mac: Docker Desktop aberto).
- Python 3.8+ no computador, para os scripts e o verificador (só biblioteca padrão).
- Para os gráficos: `pip install -r requirements.txt` (só o matplotlib; sem ele, a análise gera só tabelas).

Nos comandos abaixo, use `python` no Windows e `python3` no Linux/Mac. Todos são executados na **raiz do repositório**.

## Comandos principais

| Ação | Comando |
|---|---|
| **Construir** a imagem | `docker compose build` |
| **Iniciar** o ambiente padrão (3 nós + contador) | `docker compose up -d` |
| **Testar** (verificador + 4 cenários de correção) | `python -m unittest discover -s tests -v` e `python scripts/correctness.py` |
| **Remover** o ambiente | `docker compose down` |

## Estrutura

```
compose.yaml, Dockerfile   ambiente padrão (3 nós + contador)
node/                      o nó: comunicação, Lamport, Ricart-Agrawala, heartbeat
counter/                   o contador compartilhado (recurso protegido)
checker/                   verificador de logs (propriedades de correção)
scripts/
  gen_compose.py           gera um compose para N nós com uma configuração
  run.py                   roda UMA execução (com falha opcional) e verifica
  correctness.py           os 4 testes de correção (normal/falha de cada algoritmo)
  experiments.py           os experimentos (um parâmetro em 3 níveis, várias sementes)
  analyze.py               métricas, tabelas e gráficos dos experimentos
tests/
  fixtures/                logs de exemplo, corretos e com erros de propósito
  make_fixtures.py         gera os logs de exemplo
  test_checker.py          prova que o verificador detecta cada tipo de erro
results/raw/<run_id>/      dados brutos de cada execução
results/analysis/          tabelas (CSV), resumo e ambiente dos experimentos
results/figures/           gráficos (PNG e PDF) para o relatório
docs/                      plano do trabalho
```

## Ambiente padrão

```
docker compose up --build -d
docker compose ps -a          # os nós terminam sozinhos com "Exited (0)"
docker compose kill node3     # simula a queda do nó 3
docker compose down
```

Os logs ficam em `results/raw/_dev/logs/`. Os parâmetros do contrato podem ser trocados por variável de ambiente, sem editar o arquivo:

```
# PowerShell
$env:DURATION_S=15; $env:HB_TIMEOUT_MS=1000; docker compose up -d
# Linux/Mac
DURATION_S=15 HB_TIMEOUT_MS=1000 docker compose up -d
```

## Uma execução completa: `scripts/run.py`

Sobe N nós, opcionalmente mata um deles, espera todos terminarem, guarda tudo e roda o verificador.

```
python scripts/run.py --run-id teste                      # 3 nós, sem falha
python scripts/run.py --n 5 --seed 2 --duration 30 --run-id n5
python scripts/run.py --kill 3 --kill-at 15 --run-id falha # mata o nó 3 aos 15 s
python scripts/run.py --set HB_TIMEOUT_MS=1000 NET_DELAY_MS=50 NET_JITTER_MS=100 --run-id hb
python scripts/run.py --help
```

Sem `--kill-at`, o instante do kill é sorteado a partir da semente, no meio do período de carga, então a mesma semente reproduz o mesmo cenário.

Cada execução gera `results/raw/<run_id>/`:

| Arquivo | Conteúdo |
|---|---|
| `logs/node-*.jsonl` | log de cada nó (CONTRATO.md, Seção 8) |
| `logs/counter.jsonl` | operações no contador |
| `logs/injector.jsonl` | instante do `KILL`, se houve falha |
| `compose.yaml` | o compose exato usado |
| `run.json` | parâmetros, semente, kill, códigos de saída, hardware, sistema operacional e versões |
| `check.json` | resultado do verificador |
| `docker.log` | saída dos contêineres |

**Relógio:** no Windows e no Mac os contêineres rodam numa máquina virtual cujo relógio pode diferir do relógio do computador. Como o `KILL` é registrado pelo script no computador e as suspeitas pelos nós dentro do Docker, o `run.py` estima essa diferença com o **método de Cristian** (trocas pergunta/resposta com um processo no contêiner, ficando com a de menor ida e volta; erro máximo de metade da ida e volta) e converte o horário do `KILL` para o relógio dos contêineres. Nenhum relógio é ajustado. A diferença e a ida e volta ficam em `run.json` (`clock_offset_us`, `clock_offset_rtt_us`). Detalhes em `CONTRATO.md`, Seção 9.

## Verificador: `python -m checker <pasta>`

```
python -m checker results/raw/teste
```

Checa, a partir dos logs (CONTRATO.md, Seção 10):

| Propriedade | O que verifica |
|---|---|
| Exclusão mútua | nenhum par de nós na região crítica ao mesmo tempo |
| Contador correto | valor final = número de escritas, e a sequência escrita é 1, 2, 3, ... (sem atualização perdida) |
| Vivacidade | todo pedido de nó vivo entrou na região crítica; todo nó vivo terminou normalmente |
| Sem suspeitas falsas | só o nó morto é suspeito, e só depois do `KILL` |
| Completude | todo nó vivo suspeita do nó morto |
| Tempo de detecção | dentro de `[timeout − T − jitter, timeout + período de checagem]`, com 1 s de folga (cobre o tempo do comando de kill) |

Sai com código 0 se tudo passou e 1 se algo falhou.

## Testes

**Testes do verificador** (rápidos, sem Docker): 12 logs de exemplo em `tests/fixtures/`, 3 corretos e 9 com um erro de propósito cada (sobreposição, atualização perdida, pedido nunca atendido, suspeita falsa, detecção incompleta ou lenta, nó entrando enquanto o morto ainda está na RC, nó que não terminou). O teste confirma que cada erro é detectado exatamente na propriedade violada.

```
python -m unittest discover -s tests -v
```

**Testes de correção** (no Docker, ~3 minutos):

```
python scripts/correctness.py
```

| Cenário | Configuração | O que mostra |
|---|---|---|
| `ra-normal` | 5 nós, sem falhas | exclusão mútua, contador exato, todo pedido atendido |
| `ra-falha` | 3 nós, nó 2 morto | os sobreviventes continuam entrando na RC, sem sobreposição |
| `hb-normal` | 3 nós, atraso de rede de 100–400 ms | nenhuma suspeita falsa com atraso abaixo do timeout |
| `hb-falha` | 5 nós, nó 4 morto, atraso de 20–50 ms | todos os sobreviventes detectam, dentro do prazo teórico |

Resultados em `results/raw/correctness/<cenário>/`.

## Experimentos

```
python scripts/experiments.py      # ~25 minutos; pode interromper e rodar de novo para continuar
python scripts/analyze.py
```

| Experimento | Parâmetro (3 níveis) | Fixo | Métricas | Teoria |
|---|---|---|---|---|
| `ra` | N = 3, 5, 7 | sem falhas, sem atraso | espera para entrar na RC (média, desvio, p50/p95/p99), mensagens por entrada, vazão, custo do heartbeat | `2(N−1)` mensagens por entrada; `N(N−1)/T` heartbeats por segundo; espera cresce com N |
| `hb` | `HB_TIMEOUT_MS` = 750, 1500, 3000 | 3 nós, nó 3 morto, atraso de 100 a 700 ms | tempo de detecção (p50/p95/p99), suspeitas falsas, sobreposições na RC | detecção em `[timeout − T − jitter, timeout + período de checagem]`; timeout curto gera suspeitas falsas, que quebram a exclusão mútua |

Cada nível roda com 5 sementes (`--seeds` muda isso) e 30 s de carga (`--duration`). Os primeiros 2 s de carga de cada execução são descartados como aquecimento.

No experimento `hb`, o nível de 750 ms **falha no verificador de propósito**: com atraso de até 700 ms e heartbeats a cada 500 ms, a hipótese de sincronia parcial deixa de valer, os nós suspeitam uns dos outros sem falha real e entram juntos na região crítica. É a demonstração prática de que a segurança do Ricart-Agrawala com detector depende de o timeout ser maior que os atrasos.

`analyze.py` gera:

| Arquivo | Conteúdo |
|---|---|
| `results/analysis/resumo.md` | tabelas prontas para o relatório, comparadas com a teoria |
| `results/analysis/*_execucoes.csv`, `*_niveis.csv` | métricas por execução e por nível |
| `results/analysis/ambiente.md` | sistema operacional, processador, versões do Docker e do Python, diferença de relógio medida |
| `results/figures/ra_espera` | espera (p50/p95/p99) × N |
| `results/figures/ra_mensagens` | mensagens por entrada × N, com a teoria |
| `results/figures/hb_deteccao` | tempo de detecção × timeout, com a faixa teórica |
| `results/figures/hb_seguranca` | suspeitas falsas e sobreposições na RC × timeout |
