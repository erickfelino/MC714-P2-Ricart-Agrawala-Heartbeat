"""Gera um compose.yaml para N nós com uma configuração específica.

O compose.yaml da raiz é o ambiente padrão (3 nós). Os experimentos
precisam de outros N e de parâmetros diferentes, então o run.py gera um
compose por execução e o salva junto com os resultados (isso também
registra exatamente a configuração usada).

Uso direto (opcional):
    python scripts/gen_compose.py --n 5 --logs ./meus_logs > compose5.yaml
"""

import argparse
import os
import sys
from typing import Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Valores padrão do contrato (CONTRATO.md, Seção 6.1)
DEFAULTS = {
    "SEED": 1,
    "DURATION_S": 60,
    "START_DELAY_S": 3,
    "DRAIN_S": 5,
    "REQ_INTERVAL_MS": 200,
    "CS_DURATION_MS": 50,
    "HB_INTERVAL_MS": 500,
    "HB_TIMEOUT_MS": 2000,
    "HB_CHECK_MS": 100,
    "NET_DELAY_MS": 0,
    "NET_JITTER_MS": 0,
    "CONNECT_TIMEOUT_MS": 1000,
}

IMAGE = "mc714-ra-hb:latest"
NODE_PORT = 5000
COUNTER_PORT = 6000


def _q(value) -> str:
    """Valor entre aspas para YAML (caminhos no Windows têm espaços e ':')."""
    s = str(value).replace("\\", "/").replace('"', '\\"')
    return f'"{s}"'


def build_compose(n: int, params: Dict[str, object], logs_host: str, run_id: str = "") -> str:
    if n < 1:
        raise ValueError("n precisa ser pelo menos 1")
    env = dict(DEFAULTS)
    env.update(params)
    peers = ",".join(f"{i}@node{i}:{NODE_PORT}" for i in range(1, n + 1))
    logs = os.path.abspath(logs_host)

    lines = [
        f"# Gerado por scripts/gen_compose.py (n={n}, run_id={run_id or '-'})",
        "services:",
        "  counter:",
        f"    image: {IMAGE}",
        "    init: true",
        '    command: ["python", "-m", "counter"]',
        "    environment:",
        f"      COUNTER_ADDR: {_q(f'counter:{COUNTER_PORT}')}",
        '      LOG_DIR: "/logs"',
        "    volumes:",
        "      - type: bind",
        f"        source: {_q(logs)}",
        "        target: /logs",
    ]
    for i in range(1, n + 1):
        lines += [
            f"  node{i}:",
            f"    image: {IMAGE}",
            "    init: true",
            '    command: ["python", "-m", "node"]',
            "    depends_on: [counter]",
            "    environment:",
            f"      NODE_ID: {_q(i)}",
            f"      PEERS: {_q(peers)}",
            f"      COUNTER_ADDR: {_q(f'counter:{COUNTER_PORT}')}",
            '      LOG_DIR: "/logs"',
            f"      RUN_ID: {_q(run_id)}",
        ]
        lines += [f"      {k}: {_q(v)}" for k, v in env.items()]
        lines += [
            "    volumes:",
            "      - type: bind",
            f"        source: {_q(logs)}",
            "        target: /logs",
        ]
    return "\n".join(lines) + "\n"


def parse_params(pairs):
    """Converte ["HB_TIMEOUT_MS=1000", ...] em dicionário, validando os nomes."""
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"parâmetro inválido {p!r}: use NOME=VALOR")
        k, v = p.split("=", 1)
        k = k.strip().upper()
        if k not in DEFAULTS:
            raise SystemExit(f"parâmetro desconhecido {k!r}. Válidos: {', '.join(DEFAULTS)}")
        out[k] = v.strip()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=3, help="número de nós")
    ap.add_argument("--logs", default=os.path.join(ROOT, "results", "raw", "_dev", "logs"),
                    help="pasta deste computador onde os logs serão gravados")
    ap.add_argument("--set", nargs="*", default=[], metavar="NOME=VALOR",
                    help="parâmetros do contrato, ex.: --set HB_TIMEOUT_MS=1000 SEED=2")
    args = ap.parse_args()
    sys.stdout.write(build_compose(args.n, parse_params(args.set), args.logs))


if __name__ == "__main__":
    main()
