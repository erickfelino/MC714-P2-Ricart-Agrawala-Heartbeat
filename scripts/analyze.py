"""Analisa os experimentos: métricas, tabelas e gráficos para o relatório.

Lê results/raw/exp-ra/ e results/raw/exp-hb/ (gerados por experiments.py) e produz:

  results/analysis/
    ra_execucoes.csv   uma linha por execução do experimento ra
    ra_niveis.csv      agregado por número de nós
    hb_execucoes.csv   uma linha por execução do experimento hb
    hb_niveis.csv      agregado por timeout
    resumo.md          tabelas prontas para o relatório e comparação com a teoria
    ambiente.md        hardware, sistema operacional e versões usados
  results/figures/
    ra_espera.png/.pdf       tempo de espera (p50, p95, p99) x N
    ra_mensagens.png/.pdf    mensagens por entrada x N, com a teoria 2(N-1)
    hb_deteccao.png/.pdf     tempo de detecção x timeout, com a faixa teórica
    hb_seguranca.png/.pdf    suspeitas falsas e sobreposições na RC x timeout
                             (barra = média por execução; pontos = cada execução)

As métricas usam só a biblioteca padrão. Os gráficos precisam do matplotlib
(pip install -r requirements.txt); sem ele, só os CSVs e o resumo são gerados.

Uso (na raiz do repositório):
    python scripts/analyze.py
"""

import csv
import glob
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from checker.core import (  # noqa: E402
    check_run, cs_intervals, detection_bounds_ms, load_run,
)

RAW = os.path.join(ROOT, "results", "raw")
OUT = os.path.join(ROOT, "results", "analysis")
FIG = os.path.join(ROOT, "results", "figures")

# Os primeiros segundos de carga são descartados nas métricas de espera e
# vazão: os nós ainda estão abrindo conexões e o sistema não está estável.
WARMUP_S = 2.0

HB_ROWS = []  # execuções do hb, usadas no resumo


# ---------------------------------------------------------------------------
# Estatística (sem numpy)
# ---------------------------------------------------------------------------

def pct(values, p):
    """Percentil com interpolação linear (mesma definição padrão do numpy)."""
    if not values:
        return float("nan")
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def mean(values):
    return statistics.fmean(values) if values else float("nan")


def stdev(values):
    return statistics.stdev(values) if len(values) > 1 else 0.0


def r(x, nd=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


# ---------------------------------------------------------------------------
# Métricas de uma execução
# ---------------------------------------------------------------------------

def run_metrics(run_dir):
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as f:
        meta = json.load(f)
    run = load_run(run_dir)
    _, results, passed = check_run(run_dir)
    params = meta["params"]
    n = meta["n"]

    # Janela de medição: do primeiro REQUEST (+ aquecimento) até o fim da carga
    first_req = min((e["t_us"] for evs in run.nodes.values() for e in evs if e["event"] == "REQUEST"), default=None)
    if first_req is None:
        raise RuntimeError(f"{run_dir}: nenhum REQUEST")
    w_start = first_req + WARMUP_S * 1e6
    w_end = first_req + params["DURATION_S"] * 1e6

    # Tempo de espera REQUEST -> ENTER (ms), pedidos feitos dentro da janela
    waits = []
    for evs in run.nodes.values():
        req = {e["req_ts"]: e["t_us"] for e in evs if e["event"] == "REQUEST"}
        for e in evs:
            if e["event"] == "ENTER" and e["req_ts"] in req:
                t_req = req[e["req_ts"]]
                if w_start <= t_req <= w_end:
                    waits.append((e["t_us"] - t_req) / 1000)

    # Mensagens do Ricart-Agrawala por entrada na RC. Envios para um nó morto
    # depois do KILL não contam (CONTRATO.md, Seção 8.2: SEND não é entrega).
    sends = 0
    hb_sends = 0
    for evs in run.nodes.values():
        for e in evs:
            if e["event"] != "SEND":
                continue
            to = e.get("to")
            if to in run.kills and e["t_us"] >= run.kills[to]:
                continue
            if e["msg"] in ("REQUEST", "REPLY"):
                sends += 1
            elif e["msg"] == "HEARTBEAT":
                hb_sends += 1
    enters = sum(1 for evs in run.nodes.values() for e in evs if e["event"] == "ENTER")

    exits_in_window = sum(1 for evs in run.nodes.values() for e in evs
                          if e["event"] == "EXIT" and w_start <= e["t_us"] <= w_end)
    throughput = exits_in_window / ((w_end - w_start) / 1e6)

    # Heartbeats por segundo (todos os nós), durante a janela de carga
    hb_in_window = sum(1 for evs in run.nodes.values() for e in evs
                       if e["event"] == "SEND" and e["msg"] == "HEARTBEAT" and w_start <= e["t_us"] <= w_end)
    hb_rate = hb_in_window / ((w_end - w_start) / 1e6)

    # Detecção: primeiro SUSPECT de cada nó vivo sobre o nó morto, depois do KILL
    detections = []
    false_suspicions = 0
    for node, evs in run.nodes.items():
        seen = set()
        for e in evs:
            if e["event"] != "SUSPECT":
                continue
            peer = e["peer"]
            if peer in run.kills and e["t_us"] >= run.kills[peer]:
                if peer not in seen:
                    seen.add(peer)
                    detections.append((e["t_us"] - run.kills[peer]) / 1000)
            else:
                false_suspicions += 1

    # Violações de exclusão mútua: pares de intervalos sobrepostos
    intervals, _ = cs_intervals(run)
    overlaps = 0
    current = None
    for iv in intervals:
        if current is not None and iv[0] < current[1] and iv[2] != current[2]:
            overlaps += 1
        if current is None or iv[1] > current[1]:
            current = iv

    low, high = detection_bounds_ms(run)
    checks = {c.name: c.passed for c in results}
    return {
        "run": os.path.basename(run_dir),
        "n": n,
        "seed": params["SEED"],
        "hb_timeout_ms": params["HB_TIMEOUT_MS"],
        "hb_interval_ms": params["HB_INTERVAL_MS"],
        "net_delay_ms": params["NET_DELAY_MS"],
        "net_jitter_ms": params["NET_JITTER_MS"],
        "duration_s": params["DURATION_S"],
        "entries": enters,
        "wait_n": len(waits),
        "wait_mean_ms": mean(waits),
        "wait_std_ms": stdev(waits),
        "wait_p50_ms": pct(waits, 50),
        "wait_p95_ms": pct(waits, 95),
        "wait_p99_ms": pct(waits, 99),
        "msgs_per_entry": sends / enters if enters else float("nan"),
        "msgs_theory": 2 * (n - 1),
        "throughput_per_s": throughput,
        "hb_per_s": hb_rate,
        "hb_per_s_theory": n * (n - 1) / (params["HB_INTERVAL_MS"] / 1000),
        "detections_ms": detections,
        "detection_low_ms": low,
        "detection_high_ms": high,
        "false_suspicions": false_suspicions,
        "overlaps": overlaps,
        "counter_ok": checks.get("Contador correto"),
        "checker_passed": passed,
        "clock_offset_ms": meta.get("clock_offset_us", 0) / 1000,
        "kill_cmd_ms": (meta.get("kill") or {}).get("kill_cmd_ms"),
        "_waits": waits,
    }


def load_experiment(name):
    exp_dir = os.path.join(RAW, f"exp-{name}")
    info_path = os.path.join(exp_dir, "experiment.json")
    if not os.path.exists(info_path):
        return None, []
    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    rows = []
    for run_dir in sorted(glob.glob(os.path.join(exp_dir, "*"))):
        if os.path.exists(os.path.join(run_dir, "check.json")):
            try:
                rows.append(run_metrics(run_dir))
            except Exception as e:  # noqa: BLE001
                print(f"[analyze] AVISO: {run_dir} ignorada: {e}")
    return info, rows


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: (r(v, 3) if isinstance(v, float) else v) for k, v in row.items()})


# ---------------------------------------------------------------------------
# Agregação por nível
# ---------------------------------------------------------------------------

def aggregate_ra(rows):
    levels = []
    for n in sorted({row["n"] for row in rows}):
        rs = [row for row in rows if row["n"] == n]
        pooled = [w for row in rs for w in row["_waits"]]
        levels.append({
            "n": n,
            "runs": len(rs),
            "runs_passed": sum(row["checker_passed"] for row in rs),
            "entries_total": sum(row["entries"] for row in rs),
            "wait_mean_ms": mean(pooled),
            "wait_std_ms": stdev(pooled),
            "wait_p50_ms": pct(pooled, 50),
            "wait_p95_ms": pct(pooled, 95),
            "wait_p99_ms": pct(pooled, 99),
            # variabilidade entre sementes: desvio dos percentis de cada execução
            "wait_p50_std_seeds": stdev([row["wait_p50_ms"] for row in rs]),
            "wait_p95_std_seeds": stdev([row["wait_p95_ms"] for row in rs]),
            "wait_p99_std_seeds": stdev([row["wait_p99_ms"] for row in rs]),
            "msgs_per_entry_mean": mean([row["msgs_per_entry"] for row in rs]),
            "msgs_per_entry_std": stdev([row["msgs_per_entry"] for row in rs]),
            "msgs_theory": 2 * (n - 1),
            "throughput_mean": mean([row["throughput_per_s"] for row in rs]),
            "throughput_std": stdev([row["throughput_per_s"] for row in rs]),
            "hb_per_s_mean": mean([row["hb_per_s"] for row in rs]),
            "hb_per_s_theory": rs[0]["hb_per_s_theory"],
        })
    return levels


def aggregate_hb(rows):
    levels = []
    for t in sorted({row["hb_timeout_ms"] for row in rows}):
        rs = [row for row in rows if row["hb_timeout_ms"] == t]
        det = [d for row in rs for d in row["detections_ms"]]
        levels.append({
            "hb_timeout_ms": t,
            "runs": len(rs),
            "runs_passed": sum(row["checker_passed"] for row in rs),
            "detections": len(det),
            "det_mean_ms": mean(det),
            "det_std_ms": stdev(det),
            "det_min_ms": min(det) if det else float("nan"),
            "det_p50_ms": pct(det, 50),
            "det_p95_ms": pct(det, 95),
            "det_p99_ms": pct(det, 99),
            "det_max_ms": max(det) if det else float("nan"),
            "theory_low_ms": rs[0]["detection_low_ms"],
            "theory_high_ms": rs[0]["detection_high_ms"],
            "false_susp_total": sum(row["false_suspicions"] for row in rs),
            "false_susp_mean": mean([row["false_suspicions"] for row in rs]),
            "runs_with_false_susp": sum(row["false_suspicions"] > 0 for row in rs),
            "overlaps_total": sum(row["overlaps"] for row in rs),
            "runs_with_overlap": sum(row["overlaps"] > 0 for row in rs),
            "runs_counter_wrong": sum(row["counter_ok"] is False for row in rs),
        })
    return levels


# ---------------------------------------------------------------------------
# Resumo em Markdown
# ---------------------------------------------------------------------------

def f1(x):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1f}"


def summary_md(info_ra, lv_ra, info_hb, lv_hb):
    out = ["# Resumo dos experimentos", ""]
    out.append(f"Aquecimento descartado: {WARMUP_S:.0f} s no início da carga de cada execução. "
               "Percentis calculados sobre as amostras de todas as sementes juntas; "
               "o desvio entre sementes mostra a variabilidade entre repetições.")
    out.append("")
    if lv_ra:
        out += ["## Ricart-Agrawala: número de nós", "",
                f"{info_ra['duration_s']:.0f} s de carga por execução, sementes {info_ra['seeds']}.", "",
                "| N | Execuções (passaram) | Entradas | Espera p50 (ms) | p95 | p99 | Média ± desvio | "
                "Msgs/entrada | Teoria 2(N−1) | Vazão (entradas/s) |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for lv in lv_ra:
            out.append(
                f"| {lv['n']} | {lv['runs']} ({lv['runs_passed']}) | {lv['entries_total']} | "
                f"{f1(lv['wait_p50_ms'])} ± {f1(lv['wait_p50_std_seeds'])} | "
                f"{f1(lv['wait_p95_ms'])} ± {f1(lv['wait_p95_std_seeds'])} | "
                f"{f1(lv['wait_p99_ms'])} ± {f1(lv['wait_p99_std_seeds'])} | "
                f"{f1(lv['wait_mean_ms'])} ± {f1(lv['wait_std_ms'])} | "
                f"{lv['msgs_per_entry_mean']:.2f} ± {lv['msgs_per_entry_std']:.2f} | {lv['msgs_theory']} | "
                f"{f1(lv['throughput_mean'])} ± {f1(lv['throughput_std'])} |")
        out += ["", "Custo do heartbeat (mensagens por segundo, todos os nós) — teoria N(N−1)/T:", ""]
        for lv in lv_ra:
            out.append(f"- N = {lv['n']}: medido {f1(lv['hb_per_s_mean'])}/s, teoria {f1(lv['hb_per_s_theory'])}/s")
        out.append("")
    if lv_hb:
        out += ["## Heartbeat: timeout", "",
                f"{info_hb['duration_s']:.0f} s de carga por execução, sementes {info_hb['seeds']}, "
                f"configuração comum: {' '.join(info_hb['common_args'])}.", "",
                "| Timeout (ms) | Execuções (passaram) | Detecções | Detecção p50 (ms) | p95 | p99 | Mín–máx | "
                "Teoria (ms) | Suspeitas falsas (execuções) | Sobreposições na RC (execuções) | Contador errado |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for lv in lv_hb:
            out.append(
                f"| {lv['hb_timeout_ms']:.0f} | {lv['runs']} ({lv['runs_passed']}) | {lv['detections']} | "
                f"{f1(lv['det_p50_ms'])} | {f1(lv['det_p95_ms'])} | {f1(lv['det_p99_ms'])} | "
                f"{f1(lv['det_min_ms'])}–{f1(lv['det_max_ms'])} | "
                f"{lv['theory_low_ms']:.0f}–{lv['theory_high_ms']:.0f} | "
                f"{lv['false_susp_total']} ({lv['runs_with_false_susp']}) | "
                f"{lv['overlaps_total']} ({lv['runs_with_overlap']}) | {lv['runs_counter_wrong']} |")
        kills = [row["kill_cmd_ms"] for row in HB_ROWS if row.get("kill_cmd_ms") is not None]
        out += ["", "Faixa teórica: [timeout − T − jitter, timeout + período de checagem]. "
                "O tempo medido parte do registro do KILL, feito antes do comando, então inclui "
                f"o tempo que o comando de kill leva para matar o contêiner "
                f"({f1(min(kills) if kills else None)} a {f1(max(kills) if kills else None)} ms nestas execuções). "
                "Uma suspeita falsa do nó antes do KILL não conta como detecção, por isso há menos detecções "
                "nos níveis com suspeitas falsas.", ""]
        out += ["Nos níveis com suspeitas falsas, o verificador FALHA por desenho do experimento: "
                "a suspeita falsa viola a hipótese de sincronia parcial e permite que dois nós "
                "entrem na RC ao mesmo tempo.", ""]
    return "\n".join(out) + "\n"


def _mpl_version():
    try:
        import matplotlib
        return matplotlib.__version__
    except ImportError:
        return "não instalado"


def environment_md(run_dirs):
    seen = {}
    for d in run_dirs:
        try:
            with open(os.path.join(d, "run.json"), encoding="utf-8") as f:
                m = json.load(f)
        except OSError:
            continue
        key = json.dumps({"host": m.get("host"), "versions": m.get("versions")}, sort_keys=True)
        seen.setdefault(key, []).append(m)
    out = ["# Ambiente dos experimentos", ""]
    for key, ms in seen.items():
        m = ms[0]
        h, v = m.get("host", {}), m.get("versions", {})
        offs = [x.get("clock_offset_us", 0) / 1000 for x in ms]
        rtts = [x.get("clock_offset_rtt_us", 0) / 1000 for x in ms]
        kills = [x["kill"]["kill_cmd_ms"] for x in ms if x.get("kill")]
        out += [
            f"- Execuções: {len(ms)}",
            f"- Sistema operacional: {h.get('platform')}",
            f"- Processador: {h.get('processor') or h.get('machine')} ({h.get('cpu_count')} núcleos lógicos)",
            f"- Docker: servidor {v.get('docker_server')}, cliente {v.get('docker_client')}; Compose {v.get('compose')}",
            f"- Python: {v.get('python_container')} nos contêineres; {v.get('python_host')} no computador (scripts)",
            f"- matplotlib (gráficos): {_mpl_version()}",
            f"- Diferença de relógio contêineres−computador (Cristian): {min(offs):.1f} a {max(offs):.1f} ms, "
            f"ida e volta {min(rtts):.1f} a {max(rtts):.1f} ms (erro máximo = metade)",
        ]
        if kills:
            out.append(f"- Tempo do comando de kill: {min(kills):.0f} a {max(kills):.0f} ms")
        out.append("")
    out.append("Preencher à mão: memória RAM e modelo exato do processador (o Python nem sempre informa).")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

# Paleta categórica validada (slots 1-3) e tons neutros para texto e referência
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, BAND = "#0b0b0b", "#52514e", "#e4e3df", "#e9e8e4"


def setup_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[analyze] matplotlib não instalado: gráficos não gerados (pip install -r requirements.txt)")
        return None
    plt.rcParams.update({
        "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
        "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7.5,
        "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "figure.dpi": 150, "savefig.dpi": 300,
        "lines.linewidth": 2, "pdf.fonttype": 42,
    })
    return plt


def save(fig, name):
    os.makedirs(FIG, exist_ok=True)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG, f"{name}.{ext}"), bbox_inches="tight", facecolor="white")
    print(f"[analyze] figura: results/figures/{name}.png")


def plot_ra(plt, levels):
    xs = [lv["n"] for lv in levels]

    # 1) Tempo de espera: p50, p95, p99 por N
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    series = [("p50", "wait_p50_ms", "wait_p50_std_seeds", BLUE, "o"),
              ("p95", "wait_p95_ms", "wait_p95_std_seeds", ORANGE, "s"),
              ("p99", "wait_p99_ms", "wait_p99_std_seeds", AQUA, "^")]
    for label, key, std, color, marker in series:
        ys = [lv[key] for lv in levels]
        ax.errorbar(xs, ys, yerr=[lv[std] for lv in levels], color=color, marker=marker, markersize=5,
                    capsize=2.5, elinewidth=0.8, label=label)
        ax.annotate(label, (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=7.5, color=INK)
    ax.set_xticks(xs)
    ax.set_xlim(min(xs) - 0.5, max(xs) + 1.0)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Número de nós (N)")
    ax.set_ylabel("Espera para entrar na RC (ms)")
    ax.legend(frameon=False, loc="upper left")
    save(fig, "ra_espera")
    plt.close(fig)

    # 2) Mensagens por entrada x teoria 2(N-1)
    fig, ax = plt.subplots(figsize=(3.5, 2.3))
    ys = [lv["msgs_per_entry_mean"] for lv in levels]
    bars = ax.bar(xs, ys, width=1.1, color=BLUE, label="medido", zorder=2)
    theory = [lv["msgs_theory"] for lv in levels]
    ax.scatter(xs, theory, marker="_", s=900, linewidths=1.6, color=INK, zorder=3)
    for b, y in zip(bars, ys):
        ax.annotate(f"{y:.2f}", (b.get_x() + b.get_width() / 2, y), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=7.5, color=INK)
    ax.set_xticks(xs)
    ax.set_ylim(0, max(theory + ys) * 1.22)
    ax.set_xlabel("Número de nós (N)")
    ax.set_ylabel("Mensagens por entrada na RC")
    from matplotlib.lines import Line2D
    handles = [bars, Line2D([], [], color=INK, linewidth=1.6)]
    ax.legend(handles, ["medido", "teoria 2(N−1)"], frameon=False, loc="upper left", handlelength=1.6)
    save(fig, "ra_mensagens")
    plt.close(fig)


def plot_hb(plt, levels, rows):
    labels = [f"{lv['hb_timeout_ms']:.0f}" for lv in levels]
    pos = list(range(len(levels)))

    # 3) Tempo de detecção: pontos individuais, mediana e faixa teórica.
    # Acima da faixa, uma extensão hachurada mostra o tempo do comando de kill:
    # o KILL é registrado antes do comando, então o valor medido inclui esse tempo.
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    legend_done = set()

    def once(name):
        if name in legend_done:
            return None
        legend_done.add(name)
        return name

    for i, lv in enumerate(levels):
        lv_rows = [row for row in rows if row["hb_timeout_ms"] == lv["hb_timeout_ms"]]
        low, high = lv["theory_low_ms"], lv["theory_high_ms"]
        ax.add_patch(plt.Rectangle((i - 0.32, low), 0.64, high - low, color=BAND, zorder=1,
                                   linewidth=0, label=once("faixa teórica")))
        kill_ms = [row["kill_cmd_ms"] for row in lv_rows if row.get("kill_cmd_ms") is not None]
        if kill_ms:
            ax.add_patch(plt.Rectangle((i - 0.32, high), 0.64, max(kill_ms), facecolor="white",
                                       edgecolor="#c9c8c2", hatch="////", linewidth=0, zorder=1,
                                       label=once("+ tempo do comando de kill")))
        det = sorted(d for row in lv_rows for d in row["detections_ms"])
        if not det:
            # nenhuma detecção: os nós já tinham suspeitado do nó antes do kill
            ax.annotate("sem detecções:\nsuspeitas falsas\nantes do kill", (i, (low + high) / 2),
                        ha="center", va="center", fontsize=6.5, color=INK2, zorder=4)
            continue
        # espalha os pontos na horizontal de forma determinística para não se sobreporem
        k = len(det)
        offs = [(-0.18 + 0.36 * j / (k - 1)) if k > 1 else 0 for j in range(k)]
        ax.scatter([i + o for o in offs], det, s=14, color=BLUE, alpha=0.8, linewidths=0, zorder=3,
                   label=once("detecções"))
        ax.plot([i - 0.28, i + 0.28], [lv["det_p50_ms"]] * 2, color=INK, linewidth=1.6, zorder=4,
                label=once("mediana"))
    ax.set_xticks(pos, labels)
    ax.set_xlim(-0.6, len(levels) - 0.4)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.05)
    ax.set_xlabel("Timeout do heartbeat (ms)")
    ax.set_ylabel("Tempo de detecção (ms)")
    ax.legend(frameon=False, loc="upper left", fontsize=7)
    save(fig, "hb_deteccao")
    plt.close(fig)

    # 4) Segurança: suspeitas falsas e sobreposições por execução (dois painéis)
    fig, axes = plt.subplots(1, 2, figsize=(3.5, 2.3), sharex=True)
    panels = [("false_suspicions", "Suspeitas falsas\npor execução"),
              ("overlaps", "Sobreposições na RC\npor execução")]
    for ax, (key, title) in zip(axes, panels):
        means, tops = [], []
        for i, lv in enumerate(levels):
            vals = [row[key] for row in rows if row["hb_timeout_ms"] == lv["hb_timeout_ms"]]
            means.append(mean(vals))
            tops.append(max(vals) if vals else 0)
            k = len(vals)
            offs = [(-0.15 + 0.3 * j / (k - 1)) if k > 1 else 0 for j in range(k)]
            ax.scatter([i + o for o in offs], vals, s=12, color=INK2, zorder=3, linewidths=0)
        ax.bar(pos, means, width=0.6, color=BLUE, zorder=2)
        # rótulo da média acima do ponto mais alto, para não colidir com os pontos
        for i, (m, top) in enumerate(zip(means, tops)):
            ax.annotate(f"{m:.1f}", (i, max(m, top)), xytext=(0, 5), textcoords="offset points",
                        ha="center", fontsize=6.5, color=INK)
        ax.set_title(title, color=INK)
        ax.set_xticks(pos, labels)
        ax.set_ylim(bottom=0)
        ax.set_ylim(0, max(max(tops) * 1.3, 1))
    fig.supxlabel("Timeout do heartbeat (ms)", fontsize=8)
    save(fig, "hb_seguranca")
    plt.close(fig)


# ---------------------------------------------------------------------------

RUN_FIELDS = ["run", "n", "seed", "hb_timeout_ms", "net_delay_ms", "net_jitter_ms", "duration_s",
              "entries", "wait_n", "wait_mean_ms", "wait_std_ms", "wait_p50_ms", "wait_p95_ms", "wait_p99_ms",
              "msgs_per_entry", "msgs_theory", "throughput_per_s", "hb_per_s", "hb_per_s_theory",
              "detection_low_ms", "detection_high_ms", "false_suspicions", "overlaps", "counter_ok",
              "checker_passed", "clock_offset_ms", "kill_cmd_ms"]


def main():
    os.makedirs(OUT, exist_ok=True)
    info_ra, rows_ra = load_experiment("ra")
    info_hb, rows_hb = load_experiment("hb")
    if not rows_ra and not rows_hb:
        print("[analyze] nenhum experimento encontrado. Rode antes: python scripts/experiments.py")
        return 1

    lv_ra = aggregate_ra(rows_ra) if rows_ra else []
    lv_hb = aggregate_hb(rows_hb) if rows_hb else []
    global HB_ROWS
    HB_ROWS = rows_hb

    if rows_ra:
        write_csv(os.path.join(OUT, "ra_execucoes.csv"), rows_ra, RUN_FIELDS)
        write_csv(os.path.join(OUT, "ra_niveis.csv"), lv_ra, list(lv_ra[0]))
    if rows_hb:
        for row in rows_hb:
            row["detections"] = ";".join(f"{d:.1f}" for d in row["detections_ms"])
        write_csv(os.path.join(OUT, "hb_execucoes.csv"), rows_hb, RUN_FIELDS + ["detections"])
        write_csv(os.path.join(OUT, "hb_niveis.csv"), lv_hb, list(lv_hb[0]))

    summary = summary_md(info_ra, lv_ra, info_hb, lv_hb)
    with open(os.path.join(OUT, "resumo.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    run_dirs = [d for name in ("ra", "hb") for d in sorted(glob.glob(os.path.join(RAW, f"exp-{name}", "*")))
                if os.path.isdir(d)]
    with open(os.path.join(OUT, "ambiente.md"), "w", encoding="utf-8") as f:
        f.write(environment_md(run_dirs))

    plt = setup_mpl()
    if plt:
        if lv_ra:
            plot_ra(plt, lv_ra)
        if lv_hb:
            plot_hb(plt, lv_hb, rows_hb)

    print()
    print(summary)
    print("[analyze] tabelas e resumo em results/analysis/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
