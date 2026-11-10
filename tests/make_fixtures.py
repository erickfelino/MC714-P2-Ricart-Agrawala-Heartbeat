"""Gera os logs de exemplo em tests/fixtures/.

Cada caso é uma execução pequena e escrita "à mão": um roteiro de quem
entra e sai da região crítica, quem morre e quem suspeita de quem.
Alguns casos são corretos e outros têm um erro de propósito, para provar
que o verificador detecta cada tipo de violação.

Os arquivos gerados ficam no repositório, então não é preciso rodar este
script de novo, a não ser para mudar ou criar casos:
    python tests/make_fixtures.py
"""

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")

T0 = 1_762_000_000_000_000  # t_us arbitrário de início
MS = 1000


def config(n):
    return {
        "NODE_ID": None,  # preenchido por nó
        "PEERS": ",".join(f"{i}@node{i}:5000" for i in range(1, n + 1)),
        "SEED": 1, "DURATION_S": 10, "START_DELAY_S": 1, "DRAIN_S": 1,
        "HB_INTERVAL_MS": 500, "HB_TIMEOUT_MS": 2000, "HB_CHECK_MS": 100,
        "NET_DELAY_MS": 0, "NET_JITTER_MS": 0,
    }


class Case:
    """Monta os logs de uma execução a partir de um roteiro."""

    def __init__(self, name, n, description):
        self.name, self.n, self.description = name, n, description
        self.events = {i: [] for i in range(1, n + 1)}
        self.counter = []
        self.injector = []
        self.value = 0
        self.lamport = {i: 0 for i in range(1, n + 1)}
        for i in range(1, n + 1):
            cfg = config(n)
            cfg["NODE_ID"] = i
            self.ev(i, 0, "START", config=cfg)

    def ev(self, node, t_ms, event, **extra):
        e = {"t_us": T0 + int(t_ms * MS), "node": node, "event": event, "lamport": self.lamport[node]}
        e.update(extra)
        self.events[node].append(e)

    def request(self, node, t_ms):
        self.lamport[node] += 1
        ts = self.lamport[node]
        self.ev(node, t_ms, "REQUEST", req_ts=ts)
        return ts

    def cs(self, node, req_ts, enter_ms, exit_ms, read=None, write=True):
        """Entrada completa na RC: ENTER, READ, WRITE e EXIT."""
        self.ev(node, enter_ms, "ENTER", req_ts=req_ts)
        v = self.value if read is None else read
        self.counter.append({"t_us": T0 + int((enter_ms + 1) * MS), "event": "READ", "node": node, "value": v})
        written = None
        if write:
            written = v + 1
            self.value = written
            self.counter.append({"t_us": T0 + int((exit_ms - 1) * MS), "event": "WRITE", "node": node, "value": written})
        self.ev(node, exit_ms, "EXIT", req_ts=req_ts, read=v, written=written)

    def full(self, node, req_ms, enter_ms, exit_ms, **kw):
        ts = self.request(node, req_ms)
        self.cs(node, ts, enter_ms, exit_ms, **kw)

    def kill(self, node, t_ms):
        self.injector.append({"t_us": T0 + int(t_ms * MS), "event": "KILL", "target": node})

    def suspect(self, node, peer, t_ms):
        self.ev(node, t_ms, "SUSPECT", peer=peer, last_hb_us=T0 + int((t_ms - 2000) * MS))

    def stop(self, *nodes, t_ms=20000):
        for node in nodes or range(1, self.n + 1):
            self.ev(node, t_ms, "STOP", reason="duration")

    def write(self, truncate_last_line_of=None):
        d = os.path.join(OUT, self.name)
        os.makedirs(d, exist_ok=True)
        for node, evs in self.events.items():
            evs = sorted(evs, key=lambda e: e["t_us"])
            text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs)
            if node == truncate_last_line_of:
                text = text[:-25]  # simula a última linha cortada por um "docker kill"
            with open(os.path.join(d, f"node-{node}.jsonl"), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(d, "counter.jsonl"), "w", encoding="utf-8") as f:
            f.writelines(json.dumps(op) + "\n" for op in self.counter)
        if self.injector:
            with open(os.path.join(d, "injector.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(k) + "\n" for k in self.injector)
        with open(os.path.join(d, "LEIA-ME.txt"), "w", encoding="utf-8") as f:
            f.write(self.description + "\n")


def build():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    cases = []

    # ---------- casos corretos ----------

    c = Case("ok_normal", 3, "CORRETO. 3 nós se revezando na RC, sem falhas.")
    c.full(1, 1000, 1001, 1060)
    c.full(2, 1010, 1061, 1120)   # pediu enquanto o nó 1 estava dentro: espera
    c.full(3, 1200, 1201, 1260)
    c.full(1, 1300, 1301, 1360)
    c.stop()
    cases.append((c, None))

    c = Case("ok_kill_fora_da_rc", 3,
             "CORRETO. O nó 3 é morto fora da RC; os outros suspeitam dele no prazo e continuam.")
    c.full(1, 1000, 1001, 1060)
    c.full(3, 1100, 1101, 1160)
    c.kill(3, 3000)
    c.full(2, 3100, 4801, 4860)   # esperou a detecção do nó 3 para entrar
    c.suspect(1, 3, 4750)
    c.suspect(2, 3, 4800)
    c.full(1, 5000, 5001, 5060)
    c.stop(1, 2)
    cases.append((c, 3))  # e a última linha do nó 3 ficou cortada

    c = Case("ok_kill_dentro_da_rc", 3,
             "CORRETO. O nó 3 morre DENTRO da RC (leu, não escreveu). Os outros só entram depois do KILL.")
    c.full(1, 1000, 1001, 1060)
    ts = c.request(3, 2000)
    c.ev(3, 2001, "ENTER", req_ts=ts)
    c.counter.append({"t_us": T0 + 2002 * MS, "event": "READ", "node": 3, "value": c.value})
    c.kill(3, 2020)
    c.suspect(1, 3, 3900)
    c.suspect(2, 3, 3950)
    c.full(2, 2500, 3951, 4010)
    c.stop(1, 2)
    cases.append((c, None))

    # ---------- casos com erro de propósito ----------

    c = Case("erro_sobreposicao", 3, "ERRO: os nós 1 e 2 ficam na RC ao mesmo tempo (1001-1060 e 1030-1090).")
    c.full(1, 1000, 1001, 1060)
    c.full(2, 1000, 1030, 1090)
    c.stop()
    cases.append((c, None))

    c = Case("erro_atualizacao_perdida", 3,
             "ERRO: os nós 1 e 2 leem o mesmo valor (0) e os dois escrevem 1. O contador termina em 1 com 2 EXITs.")
    c.full(1, 1000, 1001, 1060, read=0)
    c.value = 0
    c.full(2, 1100, 1101, 1160, read=0)
    c.stop()
    cases.append((c, None))

    c = Case("erro_pedido_nao_atendido", 3, "ERRO: o nó 2 faz um pedido que nunca entra na RC (inanição/deadlock).")
    c.full(1, 1000, 1001, 1060)
    c.request(2, 1100)
    c.full(3, 1200, 1201, 1260)
    c.stop()
    cases.append((c, None))

    c = Case("erro_suspeita_falsa", 3, "ERRO: o nó 1 suspeita do nó 2 sem que ninguém tenha sido morto.")
    c.full(1, 1000, 1001, 1060)
    c.suspect(1, 2, 5000)
    c.stop()
    cases.append((c, None))

    c = Case("erro_suspeita_antes_do_kill", 3, "ERRO: o nó 1 suspeita do nó 3 antes de o nó 3 ser morto.")
    c.full(1, 1000, 1001, 1060)
    c.suspect(1, 3, 2500)
    c.kill(3, 3000)
    c.suspect(2, 3, 4900)
    c.stop(1, 2)
    cases.append((c, None))

    c = Case("erro_deteccao_incompleta", 3, "ERRO: o nó 3 é morto; o nó 1 detecta, mas o nó 2 nunca detecta.")
    c.full(1, 1000, 1001, 1060)
    c.kill(3, 3000)
    c.suspect(1, 3, 4900)
    c.stop(1, 2)
    cases.append((c, None))

    c = Case("erro_deteccao_lenta", 3,
             "ERRO: o nó 3 é morto e os outros só detectam 6 s depois (timeout é 2 s).")
    c.full(1, 1000, 1001, 1060)
    c.kill(3, 3000)
    c.suspect(1, 3, 9000)
    c.suspect(2, 3, 9100)
    c.stop(1, 2)
    cases.append((c, None))

    c = Case("erro_entrou_com_morto_na_rc", 3,
             "ERRO: o nó 3 está na RC e é morto em 2020; o nó 1 entra em 2010, antes do KILL (sobreposição).")
    ts = c.request(3, 2000)
    c.ev(3, 2001, "ENTER", req_ts=ts)
    c.kill(3, 2020)
    c.full(1, 2005, 2010, 2070)
    c.suspect(1, 3, 3900)
    c.suspect(2, 3, 3950)
    c.stop(1, 2)
    cases.append((c, None))

    c = Case("erro_no_nao_terminou", 3, "ERRO: o nó 2 não foi morto, mas não registrou STOP (travou ou caiu sem aviso).")
    c.full(1, 1000, 1001, 1060)
    c.full(2, 1100, 1101, 1160)
    c.stop(1, 3)
    cases.append((c, None))

    for case, truncate in cases:
        case.write(truncate_last_line_of=truncate)
    return [c.name for c, _ in cases]


if __name__ == "__main__":
    names = build()
    print(f"{len(names)} casos gerados em {OUT}:")
    for n in names:
        print("  ", n)
