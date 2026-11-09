"""Contador compartilhado: o recurso protegido pela exclusão mútua (CONTRATO.md, Seção 5).

Oferece apenas READ e WRITE separados, sem lock nem incremento atômico.
Cada operação individual é atômica, mas nada impede outro nó de escrever
entre o READ e o WRITE de alguém: é isso que o Ricart-Agrawala precisa evitar.
"""

import json
import os
import signal
import socket
import sys
import threading
import time


class CounterServer:
    def __init__(self, port: int, log_dir: str):
        self.port = port
        self.value = 0
        # Protege o valor e a escrita do log juntos, para que a ordem das
        # linhas em counter.jsonl seja exatamente a ordem das operações.
        self._lock = threading.Lock()

        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, "counter.jsonl"), "a", encoding="utf-8")

    def _record(self, event: str, node, value: int):
        line = {"t_us": time.time_ns() // 1000, "event": event, "node": node, "value": value}
        self._log.write(json.dumps(line) + "\n")
        self._log.flush()

    def _handle(self, req: dict) -> dict:
        op = req.get("op")
        node = req.get("node")

        if op == "READ":
            with self._lock:
                value = self.value
                self._record("READ", node, value)
            return {"ok": True, "value": value}

        if op == "WRITE":
            value = req.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                return {"ok": False, "error": "WRITE precisa de 'value' inteiro"}
            with self._lock:
                self.value = value
                self._record("WRITE", node, value)
            return {"ok": True}

        return {"ok": False, "error": f"operacao desconhecida: {op!r}"}

    def _client_loop(self, conn: socket.socket):
        with conn, conn.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    res = self._handle(json.loads(line))
                except json.JSONDecodeError as e:
                    res = {"ok": False, "error": f"JSON invalido: {e}"}
                try:
                    conn.sendall((json.dumps(res) + "\n").encode("utf-8"))
                except OSError:
                    return  # cliente caiu (por exemplo, nó morto com docker kill)

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(128)
        print(f"[CONTADOR] escutando em 0.0.0.0:{self.port}", flush=True)

        while True:
            conn, _ = srv.accept()
            # Uma thread por conexão para o accept() nunca ficar bloqueado
            threading.Thread(target=self._client_loop, args=(conn,), daemon=True).start()

    def close(self):
        with self._lock:
            if not self._log.closed:
                self._log.close()


def main() -> int:
    # Usa a mesma variável que os nós: o contador escuta na porta de COUNTER_ADDR
    addr = os.getenv("COUNTER_ADDR", "counter:6000")
    port = int(addr.rsplit(":", 1)[1])
    log_dir = os.getenv("LOG_DIR", "/logs")

    server = CounterServer(port, log_dir)

    def shutdown(signum, frame):
        print(f"[CONTADOR] encerrando; valor final = {server.value}", flush=True)
        server.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.serve_forever()
    return 0
