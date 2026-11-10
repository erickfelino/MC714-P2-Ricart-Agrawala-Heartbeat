"""Experimentos: um parâmetro variado em 3 níveis, várias sementes por nível.

  ra   Ricart-Agrawala: número de nós N = 3, 5, 7, sem falhas.
       Mede tempo de espera para entrar na RC, mensagens por entrada e vazão.
       Teoria: 2(N-1) mensagens por entrada; espera cresce com N.

  hb   Detector por heartbeat: HB_TIMEOUT_MS = 750, 1500, 3000, com 3 nós,
       atraso de rede variável (100 a 700 ms) e o nó 3 morto no meio da carga.
       Mede tempo de detecção, suspeitas falsas e violações de exclusão mútua.
       Teoria: detecção entre timeout - T e timeout + atraso máximo;
       timeout curto detecta mais rápido, mas gera suspeitas falsas,
       que quebram a exclusão mútua (sincronia parcial violada).

Cada execução é uma chamada de scripts/run.py. Resultados em
results/raw/exp-<experimento>/<nível>-s<semente>/. Execuções que já existem
são puladas, então dá para interromper e continuar depois.

Nos experimentos, o verificador FALHAR não é erro do script: no hb com
timeout curto, as violações são justamente o resultado esperado. Por isso
este script não para nem sai com erro quando uma execução falha.

Uso (na raiz do repositório):
    python scripts/experiments.py               # os dois (~25 min)
    python scripts/experiments.py --only ra
    python scripts/experiments.py --seeds 3     # menos repetições, mais rápido
    python scripts/experiments.py --force       # refaz tudo
Depois:
    python scripts/analyze.py
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import run as runner  # noqa: E402

RAW = os.path.join(ROOT, "results", "raw")

EXPERIMENTS = {
    "ra": {
        "description": "Ricart-Agrawala: variação do número de nós",
        "parameter": "N",
        "levels": [
            {"label": "n3", "value": 3, "args": ["--n", "3"]},
            {"label": "n5", "value": 5, "args": ["--n", "5"]},
            {"label": "n7", "value": 7, "args": ["--n", "7"]},
        ],
        "common": [],
    },
    "hb": {
        "description": "Heartbeat: variação do timeout com atraso de rede variável e um nó morto",
        "parameter": "HB_TIMEOUT_MS",
        "levels": [
            {"label": "t750", "value": 750, "args": ["--set", "HB_TIMEOUT_MS=750"]},
            {"label": "t1500", "value": 1500, "args": ["--set", "HB_TIMEOUT_MS=1500"]},
            {"label": "t3000", "value": 3000, "args": ["--set", "HB_TIMEOUT_MS=3000"]},
        ],
        # NET_* vão no mesmo --set do nível (ver build_argv)
        "common_set": ["NET_DELAY_MS=100", "NET_JITTER_MS=600"],
        "common": ["--n", "3", "--kill", "3"],
    },
}


def build_argv(exp, level, seed, duration, out_dir, run_id):
    args = list(exp["common"]) + list(level["args"])
    extra_set = exp.get("common_set", [])
    if extra_set:
        if "--set" in args:
            i = args.index("--set") + 1
            while i < len(args) and not args[i].startswith("--"):
                i += 1
            args[i:i] = extra_set
        else:
            args += ["--set"] + extra_set
    return args + [
        "--seed", str(seed), "--duration", str(duration),
        "--run-id", run_id, "--out", out_dir, "--force",
        "--quiet", "--no-build", "--timeout-margin", "20",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=list(EXPERIMENTS), nargs="*", help="roda só estes experimentos")
    ap.add_argument("--seeds", type=int, default=5, help="sementes por nível (padrão 5)")
    ap.add_argument("--duration", type=float, default=30, help="segundos de carga por execução (padrão 30)")
    ap.add_argument("--force", action="store_true", help="refaz execuções que já existem")
    args = ap.parse_args()

    names = args.only or list(EXPERIMENTS)
    seeds = list(range(1, args.seeds + 1))
    total = sum(len(EXPERIMENTS[n]["levels"]) for n in names) * len(seeds)

    print("[exp] construindo a imagem...", flush=True)
    runner.sh(["docker", "build", "-q", "-t", runner.IMAGE, ROOT])

    done = 0
    t0 = time.time()
    for name in names:
        exp = EXPERIMENTS[name]
        out_dir = os.path.join(RAW, f"exp-{name}")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "experiment.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "description": exp["description"], "parameter": exp["parameter"],
                       "levels": [{"label": l["label"], "value": l["value"]} for l in exp["levels"]],
                       "seeds": seeds, "duration_s": args.duration,
                       "common_args": exp["common"] + exp.get("common_set", [])},
                      f, ensure_ascii=False, indent=2)

        for level in exp["levels"]:
            for seed in seeds:
                done += 1
                run_id = f"{level['label']}-s{seed}"
                run_dir = os.path.join(out_dir, run_id)
                prefix = f"[exp {done}/{total}] {name} {run_id}"
                if os.path.exists(os.path.join(run_dir, "check.json")) and not args.force:
                    print(f"{prefix}: já existe, pulando", flush=True)
                    continue
                code = runner.main(build_argv(exp, level, seed, args.duration, out_dir, run_id))
                elapsed = time.time() - t0
                print(f"{prefix}: verificador {'passou' if code == 0 else 'FALHOU'} "
                      f"({elapsed / 60:.1f} min decorridos)", flush=True)

    print(f"\n[exp] concluído em {(time.time() - t0) / 60:.1f} min. Agora rode: python scripts/analyze.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
