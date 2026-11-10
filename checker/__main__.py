"""Uso:
    python -m checker <pasta da execução ou pasta de logs> [--json saida.json] [--quiet]

Sai com código 0 se todas as propriedades passaram e 1 se alguma falhou,
para poder ser usado como teste automatizado.
"""

import argparse
import json
import sys

from checker.core import check_run


def print_report(run, results, passed, out=sys.stdout):
    print(f"Verificação de {run.logs_dir}", file=out)
    nodes = sorted(run.nodes)
    kills = ", ".join(f"nó {k}" for k in sorted(run.kills)) or "nenhum"
    print(f"Nós: {nodes}   Mortos: {kills}", file=out)
    for w in run.warnings:
        print(f"  aviso: {w}", file=out)
    print(file=out)
    for r in results:
        tag = "[ -- ]" if not r.applicable else ("[ OK ]" if r.passed else "[FALHOU]")
        print(f"{tag} {r.name}: {r.summary}", file=out)
        for p in r.problems:
            print(f"         - {p}", file=out)
    print(file=out)
    print("RESULTADO: " + ("PASSOU" if passed else "FALHOU"), file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m checker", description="Verifica os logs de uma execução.")
    ap.add_argument("path", help="pasta da execução (results/raw/<run_id>) ou pasta de logs")
    ap.add_argument("--json", help="salva o resultado em JSON neste arquivo")
    ap.add_argument("--quiet", action="store_true", help="não imprime o relatório")
    args = ap.parse_args(argv)

    run, results, passed = check_run(args.path)

    if not args.quiet:
        print_report(run, results, passed)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "logs_dir": run.logs_dir,
                "passed": passed,
                "nodes": sorted(run.nodes),
                "kills": {str(k): v for k, v in run.kills.items()},
                "warnings": run.warnings,
                "checks": [r.to_dict() for r in results],
            }, f, ensure_ascii=False, indent=2)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
