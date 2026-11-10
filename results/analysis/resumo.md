# Resumo dos experimentos

Aquecimento descartado: 2 s no início da carga de cada execução. Percentis calculados sobre as amostras de todas as sementes juntas; o desvio entre sementes mostra a variabilidade entre repetições.

## Ricart-Agrawala: número de nós

30 s de carga por execução, sementes [1, 2, 3, 4, 5].

| N | Execuções (passaram) | Entradas | Espera p50 (ms) | p95 | p99 | Média ± desvio | Msgs/entrada | Teoria 2(N−1) | Vazão (entradas/s) |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 5 (5) | 1322 | 33.4 ± 11.1 | 254.3 ± 85.2 | 447.9 ± 97.3 | 64.2 ± 89.5 | 4.00 ± 0.00 | 4 | 9.0 ± 0.7 |
| 5 | 5 (5) | 1543 | 156.3 ± 23.4 | 547.3 ± 109.4 | 716.0 ± 59.0 | 198.2 ± 158.4 | 8.00 ± 0.00 | 8 | 10.6 ± 0.9 |
| 7 | 5 (5) | 1686 | 289.0 ± 9.7 | 729.5 ± 102.4 | 913.9 ± 144.2 | 339.5 ± 190.8 | 12.00 ± 0.00 | 12 | 11.4 ± 0.6 |

Custo do heartbeat (mensagens por segundo, todos os nós) — teoria N(N−1)/T:

- N = 3: medido 12.0/s, teoria 12.0/s
- N = 5: medido 40.0/s, teoria 40.0/s
- N = 7: medido 83.9/s, teoria 84.0/s

## Heartbeat: timeout

30 s de carga por execução, sementes [1, 2, 3, 4, 5], configuração comum: --n 3 --kill 3 NET_DELAY_MS=100 NET_JITTER_MS=600.

| Timeout (ms) | Execuções (passaram) | Detecções | Detecção p50 (ms) | p95 | p99 | Mín–máx | Teoria (ms) | Suspeitas falsas (execuções) | Sobreposições na RC (execuções) | Contador errado |
|---|---|---|---|---|---|---|---|---|---|---|
| 750 | 5 (0) | 0 | — | — | — | —–— | 0–850 | 30 (5) | 284 (5) | 5 |
| 1500 | 5 (5) | 10 | 1486.5 | 1701.4 | 1718.0 | 1065.5–1722.1 | 400–1600 | 0 (0) | 0 (0) | 0 |
| 3000 | 5 (5) | 10 | 2863.3 | 3092.4 | 3135.0 | 2603.4–3145.6 | 1900–3100 | 0 (0) | 0 (0) | 0 |

Faixa teórica: [timeout − T − jitter, timeout + período de checagem]. O tempo medido parte do registro do KILL, feito antes do comando, então inclui o tempo que o comando de kill leva para matar o contêiner (325.6 a 415.0 ms nestas execuções). Uma suspeita falsa do nó antes do KILL não conta como detecção, por isso há menos detecções nos níveis com suspeitas falsas.

Nos níveis com suspeitas falsas, o verificador FALHA por desenho do experimento: a suspeita falsa viola a hipótese de sincronia parcial e permite que dois nós entrem na RC ao mesmo tempo.

