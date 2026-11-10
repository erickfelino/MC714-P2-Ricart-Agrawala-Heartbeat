"""Testes de correção automatizados: um cenário normal e um de falha para cada algoritmo.

  ra-normal  5 nós, sem falhas.
             Exclusão mútua, contador exato e todo pedido atendido.
  ra-falha   3 nós, o nó 2 é morto no meio da carga.
             Os sobreviventes continuam entrando na RC, sem sobreposição.
  hb-normal  3 nós, sem falhas, com atraso de rede de 100 a 400 ms
             (abaixo do timeout de 2 s). Nenhuma suspeita falsa.
  hb-falha   5 nós, o nó 4 é morto, com atraso de rede de 20 a 50 ms.
             Os 4 sobreviventes detectam, dentro do prazo teórico.

Cada cenário roda com scripts/run.py e é avaliado pelo verificador.
Os resultados ficam em results/raw/correctness/<cenário>/.

Uso (na raiz do repositório):
    python scripts/correctness.py                # duração de 30 s por cenário
    python scripts/correctness.py --duration 15  # mais rápido
    python scripts/correctness.py --only ra-falha hb-falha

Sai com código 0 só se todos os cenários passarem.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run as runner  # noqa: E402

OUT = os.path.join(ROOT, "results", "raw", "correctness")

SCENARIOS = {
    "ra-normal": ["--n", "5"],
    "ra-falha": ["--n", "3", "--kill", "2"],
    "hb-normal": ["--n", "3", "--set", "NET_DELAY_MS=100", "NET_JITTER_MS=300"],
    "hb-falha": ["--n", "5", "--kill", "4", "--set", "NET_DELAY_MS=20", "NET_JITTER_MS=30"],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duration", type=float, default=30, help="segundos de carga por cenário (padrão 30)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--only", nargs="*", choices=list(SCENARIOS), help="roda só estes cenários")
    args = ap.parse_args()

    names = args.only or list(SCENARIOS)
    results = {}
    for i, name in enumerate(names):
        print(f"\n{'=' * 70}\n  Cenário {name}\n{'=' * 70}", flush=True)
        argv = SCENARIOS[name] + [
            "--duration", str(args.duration), "--seed", str(args.seed),
            "--run-id", name, "--out", OUT, "--force",
        ]
        if i > 0:
            argv.append("--no-build")
        results[name] = runner.main(argv) == 0

    print(f"\n{'=' * 70}\n  Resumo dos testes de correção\n{'=' * 70}")
    for name in names:
        check = os.path.join(OUT, name, "check.json")
        summary = ""
        if os.path.exists(check):
            with open(check, encoding="utf-8") as f:
                data = json.load(f)
            failed = [c["name"] for c in data["checks"] if not c["passed"]]
            summary = "falhou em: " + ", ".join(failed) if failed else ""
        print(f"  {'[ OK ]' if results[name] else '[FALHOU]'} {name} {summary}")
    ok = all(results.values())
    print(f"\n  {'TODOS PASSARAM' if ok else 'HÁ FALHAS'} ({sum(results.values())}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
