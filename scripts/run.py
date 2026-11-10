"""Roda UMA execução completa e verifica o resultado.

Passos:
  1. gera o compose.yaml para N nós com os parâmetros pedidos;
  2. sobe o contador e os nós no Docker;
  3. se pedido, mata um nó (registrando o KILL em injector.jsonl);
  4. espera todos os nós terminarem;
  5. salva logs, compose, saída do Docker e metadados em results/raw/<run_id>/;
  6. derruba o ambiente e roda o verificador.

Exemplos (na raiz do repositório):
    python scripts/run.py --run-id teste1
    python scripts/run.py --n 5 --seed 2 --duration 30 --run-id n5-s2
    python scripts/run.py --kill 3 --kill-at 15 --run-id falha
    python scripts/run.py --n 3 --set HB_TIMEOUT_MS=1000 NET_DELAY_MS=50 --run-id hb1

Sai com código 0 se o verificador passou e 1 caso contrário.
"""

import argparse
import datetime
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from gen_compose import DEFAULTS, IMAGE, build_compose, parse_params  # noqa: E402
from checker.core import check_run  # noqa: E402
from checker.__main__ import print_report  # noqa: E402

PROJECT = "mc714-run"
DEFAULT_OUT = os.path.join(ROOT, "results", "raw")


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

def sh(cmd, check=True, capture=True, timeout=None):
    """Roda um comando e devolve o stdout (texto)."""
    try:
        r = subprocess.run(cmd, capture_output=capture, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise SystemExit("ERRO: comando 'docker' não encontrado. O Docker Desktop está instalado e aberto?")
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() if capture else ""
        raise RuntimeError(f"falhou: {' '.join(cmd)}\n{msg}")
    return (r.stdout or "").strip() if capture else ""


class Compose:
    def __init__(self, compose_file):
        self.base = ["docker", "compose", "-p", PROJECT, "-f", compose_file]

    def __call__(self, *args, **kw):
        return sh(self.base + list(args), **kw)

    def container_ids(self, services):
        return {s: self("ps", "-a", "-q", s) for s in services}


CLOCK_PROBE = (
    "import sys, time\n"
    "print('pronto', flush=True)\n"
    "for _ in sys.stdin:\n"
    "    print(time.time_ns() // 1000, flush=True)\n"
)


def clock_offset_us(compose, samples=15):
    """Diferença entre o relógio dos contêineres e o deste computador
    (método de Cristian; CONTRATO.md, Seção 9).

    No Windows e no Mac os contêineres rodam numa máquina virtual cujo
    relógio pode estar diferente do relógio do computador. Como o KILL é
    registrado por este script e o SUSPECT pelos nós, os dois precisam estar
    no mesmo relógio.

    Abre UM processo dentro do contêiner do contador e faz "ping-pong" com
    ele: em t1 manda uma linha, ele responde com a hora dele (tc), a resposta
    chega em t2. Como o processo já está aberto, ida e volta são simétricas e
    supõe-se que tc corresponde ao meio do intervalo:

        deslocamento = tc - (t1 + t2) / 2,   erro máximo = (t2 - t1) / 2

    Fica a amostra com menor ida e volta (menor erro). Nenhum relógio é
    ajustado: o deslocamento só é somado ao horário do KILL.

    Devolve (deslocamento_us, ida_e_volta_us).
    """
    proc = subprocess.Popen(compose.base + ["exec", "-T", "counter", "python", "-u", "-c", CLOCK_PROBE],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        if proc.stdout.readline().strip() != "pronto":
            raise RuntimeError("não foi possível ler o relógio do contêiner")
        best = None
        for _ in range(samples):
            t1 = time.time_ns() // 1000
            proc.stdin.write("\n")
            proc.stdin.flush()
            tc = int(proc.stdout.readline())
            t2 = time.time_ns() // 1000
            rtt = t2 - t1
            off = tc - (t1 + t2) // 2
            if best is None or rtt < best[1]:
                best = (off, rtt)
        return best
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def container_states(ids):
    """{serviço: (rodando?, código de saída)} via docker inspect."""
    valid = {s: i for s, i in ids.items() if i}
    if not valid:
        return {}
    out = sh(["docker", "inspect", "-f", "{{.Id}} {{.State.Running}} {{.State.ExitCode}}"] + list(valid.values()))
    by_id = {}
    for line in out.splitlines():
        cid, running, code = line.split()
        by_id[cid] = (running == "true", int(code))
    states = {}
    for s, i in valid.items():
        for cid, st in by_id.items():
            if cid.startswith(i):
                states[s] = st
    return states


def versions():
    def safe(cmd):
        try:
            return sh(cmd)
        except Exception as e:  # noqa: BLE001
            return f"indisponível ({e})"
    return {
        "docker_server": safe(["docker", "version", "--format", "{{.Server.Version}}"]),
        "docker_client": safe(["docker", "version", "--format", "{{.Client.Version}}"]),
        "compose": safe(["docker", "compose", "version", "--short"]),
        "python_host": platform.python_version(),
        "python_container": "3.12 (python:3.12-slim)",
    }


def host_info():
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def log(msg):
    print(f"[run] {msg}", flush=True)


def run(args) -> int:
    params = parse_params(args.set)
    params["SEED"] = args.seed
    if args.duration is not None:
        params["DURATION_S"] = args.duration
    cfg = dict(DEFAULTS)
    for k, v in params.items():
        num = float(v)
        cfg[k] = int(num) if num.is_integer() else num

    start_delay, duration, drain = cfg["START_DELAY_S"], cfg["DURATION_S"], cfg["DRAIN_S"]
    expected_s = start_delay + duration + drain

    # Momento do kill: se não informado, sorteado a partir da semente, no
    # meio do período de carga (a detecção só acontece durante a carga).
    kill_at = None
    if args.kill is not None:
        if not 1 <= args.kill <= args.n:
            raise SystemExit(f"--kill {args.kill} fora do intervalo 1..{args.n}")
        if args.kill_at is not None:
            kill_at = args.kill_at
        else:
            rng = random.Random(f"kill-{args.seed}")
            kill_at = start_delay + duration * rng.uniform(0.3, 0.6)
        last_ok = start_delay + duration - cfg["HB_TIMEOUT_MS"] / 1000 - 2
        if kill_at > last_ok:
            log(f"AVISO: kill em {kill_at:.1f}s pode ser tarde demais para a detecção "
                f"(os nós param de suspeitar em ~{start_delay + duration:.0f}s)")

    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(args.out, run_id)
    if os.path.exists(run_dir):
        if not args.force:
            raise SystemExit(f"ERRO: {run_dir} já existe. Use outro --run-id ou --force para sobrescrever.")
        shutil.rmtree(run_dir)
    logs_dir = os.path.join(run_dir, "logs")
    os.makedirs(logs_dir)

    compose_file = os.path.join(run_dir, "compose.yaml")
    with open(compose_file, "w", encoding="utf-8") as f:
        f.write(build_compose(args.n, params, logs_dir, run_id))
    compose = Compose(compose_file)
    nodes = [f"node{i}" for i in range(1, args.n + 1)]

    meta = {
        "run_id": run_id,
        "started_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "n": args.n,
        "params": cfg,
        "kill": None,
        "host": host_info(),
        "versions": versions(),
    }

    if not args.no_build:
        log("construindo a imagem (rápido se nada mudou)...")
        sh(["docker", "build", "-q", "-t", IMAGE, ROOT])

    log(f"execução {run_id}: {args.n} nós, seed {args.seed}, "
        f"duração ~{expected_s:.0f}s" + (f", kill do nó {args.kill} em {kill_at:.1f}s" if kill_at else ""))

    exit_code = 1
    try:
        compose("down", "-v", "--remove-orphans", check=False)
        compose("up", "-d")
        t_up = time.time()
        ids = compose.container_ids(nodes)

        offset, rtt = clock_offset_us(compose)
        meta["clock_offset_us"] = offset
        meta["clock_offset_rtt_us"] = rtt
        log(f"diferença de relógio contêineres - host: {offset / 1000:.1f} ms (ida e volta de {rtt / 1000:.1f} ms)")

        if kill_at is not None:
            wait = t_up + kill_at - time.time()
            if wait > 0:
                time.sleep(wait)
            target = f"node{args.kill}"
            t_kill = time.time_ns() // 1000 + offset
            # Contrato, Seção 9: registrar o KILL imediatamente ANTES de matar
            with open(os.path.join(logs_dir, "injector.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"t_us": t_kill, "event": "KILL", "target": args.kill}) + "\n")
            compose("kill", "-s", "SIGKILL", target)
            t_after = time.time_ns() // 1000 + offset
            meta["kill"] = {"node": args.kill, "at_s": round(kill_at, 3), "t_us": t_kill,
                            "kill_cmd_ms": (t_after - t_kill) / 1000}
            log(f"nó {args.kill} morto (comando levou {(t_after - t_kill) / 1000:.0f} ms)")

        # Espera todos os nós terminarem
        deadline = t_up + expected_s + args.timeout_margin
        while True:
            states = container_states(ids)
            if states and all(not running for running, _ in states.values()):
                break
            if time.time() > deadline:
                log("ERRO: tempo esgotado esperando os nós terminarem")
                break
            time.sleep(1)

        states = container_states(ids)
        meta["exit_codes"] = {s: code for s, (_, code) in states.items()}
        bad = [s for s, code in meta["exit_codes"].items()
               if code != 0 and not (args.kill is not None and s == f"node{args.kill}")]
        if bad:
            log(f"AVISO: contêineres terminaram com erro: {', '.join(f'{s}={meta['exit_codes'][s]}' for s in bad)}")

        with open(os.path.join(run_dir, "docker.log"), "w", encoding="utf-8") as f:
            f.write(compose("logs", "--no-color", check=False))
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
        meta["finished_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    run_obj, results, passed = check_run(run_dir)
    with open(os.path.join(run_dir, "check.json"), "w", encoding="utf-8") as f:
        json.dump({"passed": passed, "checks": [r.to_dict() for r in results],
                   "warnings": run_obj.warnings}, f, ensure_ascii=False, indent=2)
    if not args.quiet:
        print()
        print_report(run_obj, results, passed)
    exit_code = 0 if passed else 1
    log(f"resultados em {os.path.relpath(run_dir, ROOT)}")
    return exit_code


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=3, help="número de nós (padrão 3)")
    ap.add_argument("--seed", type=int, default=1, help="semente (padrão 1)")
    ap.add_argument("--duration", type=float, help="DURATION_S, segundos de carga (padrão do contrato: 60)")
    ap.add_argument("--kill", type=int, metavar="NÓ", help="mata este nó durante a execução")
    ap.add_argument("--kill-at", type=float, metavar="S",
                    help="segundos depois de subir o ambiente (padrão: sorteado pela semente no meio da carga)")
    ap.add_argument("--set", nargs="*", default=[], metavar="NOME=VALOR",
                    help="outros parâmetros do contrato, ex.: --set HB_TIMEOUT_MS=1000 NET_DELAY_MS=50")
    ap.add_argument("--run-id", help="nome da execução (padrão: data e hora)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="pasta dos resultados (padrão results/raw)")
    ap.add_argument("--force", action="store_true", help="sobrescreve uma execução com o mesmo nome")
    ap.add_argument("--no-build", action="store_true", help="não reconstrói a imagem")
    ap.add_argument("--timeout-margin", type=float, default=60, help="folga em segundos para esperar os nós")
    ap.add_argument("--quiet", action="store_true", help="não imprime o relatório do verificador")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
