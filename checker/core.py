"""Verificador de logs (CONTRATO.md, Seção 10).

Lê os logs de UMA execução (node-*.jsonl, counter.jsonl e, se houver,
injector.jsonl) e checa as propriedades de correção:

  1. Exclusão mútua        (segurança do Ricart-Agrawala)
  2. Contador correto      (consequência observável da exclusão mútua)
  3. Vivacidade            (todo pedido de nó vivo é atendido)
  4. Sem suspeitas falsas  (precisão do detector)
  5. Completude            (todo nó morto é suspeito por todos os vivos)
  6. Tempo de detecção     (dentro do limite previsto pela teoria)

Cada checagem devolve um CheckResult. A execução passa se todas as
checagens aplicáveis passarem.
"""

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Folga para atrasos de escalonamento do sistema operacional e para o tempo
# que o "docker kill" leva para realmente matar o contêiner.
DETECTION_SLACK_MS = 1000


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    applicable: bool = True
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "applicable": self.applicable,
            "summary": self.summary,
            "problems": self.problems,
        }


@dataclass
class Run:
    logs_dir: str
    nodes: Dict[int, List[dict]]          # id do nó -> eventos em ordem
    counter: List[dict]                   # operações do contador em ordem
    kills: Dict[int, int]                 # nó morto -> t_us do KILL
    config: dict                          # configuração (do START de um nó)
    warnings: List[str]

    @property
    def expected_nodes(self) -> List[int]:
        peers = self.config.get("PEERS") or ""
        ids = [int(p.split("@")[0]) for p in peers.split(",") if p.strip()]
        return sorted(ids) if ids else sorted(self.nodes)

    def cfg(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default


# ---------------------------------------------------------------------------
# Leitura dos logs
# ---------------------------------------------------------------------------

def _read_jsonl(path: str, warnings: List[str]) -> List[dict]:
    """Lê um arquivo JSON Lines.

    Um nó morto com "docker kill" pode deixar a última linha pela metade.
    Essa linha é ignorada com um aviso. Uma linha inválida no meio do
    arquivo indica log corrompido e também gera aviso.
    """
    out = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            where = "última linha (provavelmente cortada pelo kill)" if i >= len(lines) - 2 else f"linha {i + 1}"
            warnings.append(f"{os.path.basename(path)}: {where} ignorada por não ser JSON válido")
    return out


def find_logs_dir(path: str) -> str:
    """Aceita a pasta da execução (results/raw/<id>) ou a própria pasta de logs."""
    if os.path.isdir(os.path.join(path, "logs")):
        return os.path.join(path, "logs")
    return path


def load_run(path: str) -> Run:
    logs_dir = find_logs_dir(path)
    warnings: List[str] = []

    nodes: Dict[int, List[dict]] = {}
    for f in sorted(glob.glob(os.path.join(logs_dir, "node-*.jsonl"))):
        events = _read_jsonl(f, warnings)
        if not events:
            warnings.append(f"{os.path.basename(f)} está vazio")
            continue
        node_id = events[0]["node"]
        # O log é escrito em ordem, mas ordenar por tempo deixa o verificador
        # robusto a qualquer pequena inversão entre threads.
        nodes[node_id] = sorted(events, key=lambda e: e["t_us"])

    counter_path = os.path.join(logs_dir, "counter.jsonl")
    # Para o contador a ordem do arquivo É a ordem das operações: não reordenar.
    counter = _read_jsonl(counter_path, warnings) if os.path.exists(counter_path) else []

    kills: Dict[int, int] = {}
    inj_path = os.path.join(logs_dir, "injector.jsonl")
    if os.path.exists(inj_path):
        for e in _read_jsonl(inj_path, warnings):
            if e.get("event") == "KILL":
                kills[int(e["target"])] = int(e["t_us"])

    config = {}
    for events in nodes.values():
        for e in events:
            if e["event"] == "START":
                config = e.get("config", {})
                break
        if config:
            break

    return Run(logs_dir, nodes, counter, kills, config, warnings)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------

def _fmt_t(run: Run, t_us: Optional[float]) -> str:
    """Tempo relativo ao primeiro evento da execução, em segundos."""
    if t_us is None or t_us == float("inf"):
        return "fim"
    t0 = min(evs[0]["t_us"] for evs in run.nodes.values()) if run.nodes else 0
    return f"{(t_us - t0) / 1e6:.3f}s"


def cs_intervals(run: Run):
    """Intervalos [ENTER, fim] de cada nó na região crítica.

    Devolve (intervalos, problemas). Para um nó morto dentro da região
    crítica, o fim é o instante do KILL. Um ENTER sem EXIT em nó que não
    foi morto é um problema (o nó saiu da RC sem registrar).
    """
    intervals = []
    problems = []
    for node, events in run.nodes.items():
        open_enter = None
        for e in events:
            if e["event"] == "ENTER":
                if open_enter is not None:
                    problems.append(f"nó {node}: ENTER (req_ts={e['req_ts']}) sem EXIT do ENTER anterior")
                open_enter = e
            elif e["event"] == "EXIT":
                if open_enter is None:
                    problems.append(f"nó {node}: EXIT (req_ts={e['req_ts']}) sem ENTER correspondente")
                    continue
                if e.get("req_ts") != open_enter.get("req_ts"):
                    problems.append(f"nó {node}: EXIT com req_ts={e.get('req_ts')} fecha ENTER com req_ts={open_enter.get('req_ts')}")
                intervals.append((open_enter["t_us"], e["t_us"], node, open_enter.get("req_ts")))
                open_enter = None
        if open_enter is not None:
            if node in run.kills:
                intervals.append((open_enter["t_us"], run.kills[node], node, open_enter.get("req_ts")))
            else:
                problems.append(f"nó {node}: ENTER (req_ts={open_enter['req_ts']}) sem EXIT, e o nó não foi morto")
                intervals.append((open_enter["t_us"], float("inf"), node, open_enter.get("req_ts")))
    intervals.sort()
    return intervals, problems


# ---------------------------------------------------------------------------
# Checagens
# ---------------------------------------------------------------------------

def check_mutual_exclusion(run: Run) -> CheckResult:
    intervals, problems = cs_intervals(run)

    # Varre os intervalos em ordem de início guardando o que termina mais tarde.
    overlaps = 0
    current = None  # (início, fim, nó, req_ts) com maior fim até agora
    for iv in intervals:
        if current is not None and iv[0] < current[1] and iv[2] != current[2]:
            overlaps += 1
            if overlaps <= 10:
                problems.append(
                    f"sobreposição: nó {current[2]} na RC de {_fmt_t(run, current[0])} a {_fmt_t(run, current[1])} "
                    f"e nó {iv[2]} entrou em {_fmt_t(run, iv[0])}"
                )
        if current is None or iv[1] > current[1]:
            current = iv
    if overlaps > 10:
        problems.append(f"... e mais {overlaps - 10} sobreposições")

    ok = not problems
    return CheckResult(
        "Exclusão mútua", ok,
        f"{len(intervals)} entradas na RC, {overlaps} sobreposições", problems=problems,
    )


def check_counter(run: Run) -> CheckResult:
    if not run.counter:
        return CheckResult("Contador correto", False, "counter.jsonl ausente ou vazio",
                           problems=["sem counter.jsonl não é possível checar o contador"])

    writes = [op for op in run.counter if op.get("event") == "WRITE"]
    final = writes[-1]["value"] if writes else 0

    exits_written = sum(
        1 for evs in run.nodes.values() for e in evs
        if e["event"] == "EXIT" and e.get("written") is not None
    )

    # Um nó morto dentro da RC pode ter chegado a escrever antes de morrer
    # (entre o WRITE e o registro do EXIT). Essas escritas também contam.
    killed_writes = 0
    intervals, _ = cs_intervals(run)
    for start, end, node, _ in intervals:
        if node in run.kills and end == run.kills[node]:
            exits_of_node = [e for e in run.nodes[node] if e["event"] == "EXIT"]
            if not exits_of_node or exits_of_node[-1]["t_us"] < start:
                killed_writes += sum(1 for w in writes if w.get("node") == node and w["t_us"] >= start)

    expected = exits_written + killed_writes
    problems = []
    if final != expected:
        problems.append(f"valor final {final}, mas {expected} entradas na RC escreveram no contador "
                        f"({exits_written} EXIT com escrita + {killed_writes} de nó morto na RC)")

    # Com exclusão mútua, a sequência de valores escritos é 1, 2, 3, ...
    # Uma escrita repetida ou fora de ordem indica atualização perdida.
    lost = 0
    for i, w in enumerate(writes, start=1):
        if w["value"] != i:
            lost += 1
            if lost <= 5:
                problems.append(f"escrita nº {i} foi do valor {w['value']} pelo nó {w.get('node')} "
                                f"(esperado {i}: atualização perdida)")
            break  # depois do primeiro erro, os demais são consequência
    ok = not problems
    return CheckResult("Contador correto", ok,
                       f"valor final {final}, esperado {expected}", problems=problems)


def check_liveness(run: Run) -> CheckResult:
    problems = []
    total = 0
    unserved = 0
    for node, events in run.nodes.items():
        if node in run.kills:
            continue
        entered = {e["req_ts"] for e in events if e["event"] == "ENTER"}
        stops = [e for e in events if e["event"] == "STOP"]
        if not stops:
            problems.append(f"nó {node}: não registrou STOP (não terminou normalmente)")
        for e in events:
            if e["event"] == "REQUEST":
                total += 1
                if e["req_ts"] not in entered:
                    unserved += 1
                    problems.append(f"nó {node}: pedido req_ts={e['req_ts']} em {_fmt_t(run, e['t_us'])} nunca entrou na RC")

    for node in run.expected_nodes:
        if node not in run.nodes:
            problems.append(f"nó {node}: log ausente")

    ok = not problems
    return CheckResult("Vivacidade", ok,
                       f"{total} pedidos de nós vivos, {total - unserved} atendidos",
                       problems=problems)


def check_no_false_suspicion(run: Run) -> CheckResult:
    problems = []
    n_suspects = 0
    for node, events in run.nodes.items():
        for e in events:
            if e["event"] != "SUSPECT":
                continue
            n_suspects += 1
            peer = e["peer"]
            if peer not in run.kills:
                problems.append(f"nó {node} suspeitou do nó {peer}, que não foi morto ({_fmt_t(run, e['t_us'])})")
            elif e["t_us"] < run.kills[peer]:
                problems.append(f"nó {node} suspeitou do nó {peer} antes do KILL")
    ok = not problems
    return CheckResult("Sem suspeitas falsas", ok,
                       f"{n_suspects} suspeitas, {len(problems)} falsas", problems=problems)


def check_completeness(run: Run) -> CheckResult:
    if not run.kills:
        return CheckResult("Completude", True, "não se aplica (nenhum nó foi morto)", applicable=False)
    problems = []
    for dead in run.kills:
        for node, events in run.nodes.items():
            if node in run.kills:
                continue
            if not any(e["event"] == "SUSPECT" and e["peer"] == dead for e in events):
                problems.append(f"nó {node} nunca suspeitou do nó {dead}, que foi morto")
    ok = not problems
    alive = [n for n in run.nodes if n not in run.kills]
    return CheckResult("Completude", ok,
                       f"nós mortos {sorted(run.kills)}, detectados por {len(alive) - len(problems)}/{len(alive)} vivos",
                       problems=problems)


def detection_times_ms(run: Run) -> List[dict]:
    """Tempo do KILL até cada SUSPECT do nó morto."""
    out = []
    for node, events in run.nodes.items():
        for e in events:
            if e["event"] == "SUSPECT" and e["peer"] in run.kills:
                out.append({"node": node, "peer": e["peer"],
                            "ms": (e["t_us"] - run.kills[e["peer"]]) / 1000})
    return out


def detection_bounds_ms(run: Run):
    """Limites teóricos do tempo de detecção (ver docs/plano, Seção 5.2).

    O último heartbeat chega até T antes do crash; a suspeita ocorre na
    primeira checagem depois de "último heartbeat + timeout". Logo:
        timeout - T  <=  detecção  <=  timeout + atraso máximo + período de checagem
    """
    timeout = run.cfg("HB_TIMEOUT_MS", 2000)
    interval = run.cfg("HB_INTERVAL_MS", 500)
    check = run.cfg("HB_CHECK_MS", 100)
    delay_max = run.cfg("NET_DELAY_MS", 0) + run.cfg("NET_JITTER_MS", 0)
    low = timeout - interval
    high = timeout + delay_max + check
    return low, high


def check_detection_time(run: Run) -> CheckResult:
    if not run.kills:
        return CheckResult("Tempo de detecção", True, "não se aplica (nenhum nó foi morto)", applicable=False)
    low, high = detection_bounds_ms(run)
    times = detection_times_ms(run)
    problems = []
    for d in times:
        if d["ms"] < low - DETECTION_SLACK_MS or d["ms"] > high + DETECTION_SLACK_MS:
            problems.append(f"nó {d['node']} detectou o nó {d['peer']} em {d['ms']:.0f} ms, "
                            f"fora do intervalo [{low:.0f}, {high:.0f}] ms (folga de {DETECTION_SLACK_MS} ms)")
    if not times:
        problems.append("nenhuma detecção registrada")
    shown = ", ".join(f"{d['ms']:.0f}" for d in sorted(times, key=lambda d: d["ms"]))
    return CheckResult("Tempo de detecção", not problems,
                       f"{shown} ms (teoria: {low:.0f} a {high:.0f} ms)", problems=problems)


CHECKS = [
    check_mutual_exclusion,
    check_counter,
    check_liveness,
    check_no_false_suspicion,
    check_completeness,
    check_detection_time,
]


def check_run(path: str):
    """Roda todas as checagens. Devolve (run, resultados, passou)."""
    run = load_run(path)
    if not run.nodes:
        res = CheckResult("Logs", False, f"nenhum node-*.jsonl em {run.logs_dir}")
        return run, [res], False
    results = [c(run) for c in CHECKS]
    passed = all(r.passed for r in results)
    return run, results, passed
