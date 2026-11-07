import json
import socket
import threading
import time

class CounterClient:
    def __init__(self, host: str, port: int, node_id: int, timeout_ms: float):
        self.host = host
        self.port = port
        self.node_id = node_id
        self.timeout_s = timeout_ms / 1000.0
        self._sock = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._sock is None:
            # faz 5 tentativas de conexão com os hosts a cada 0.5 segundos
            max_retries = 5

            for attempt in range(max_retries):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(self.timeout_s)
                    s.connect((self.host, self.port))
                    self._sock = s
                    return
                except (ConnectionRefusedError, OSError):
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(0.5)

    def _disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send_cmd(self, payload: dict) -> dict:
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    raw = json.dumps(payload, ensure_ascii=False) + "\n"
                    self._sock.sendall(raw.encode("utf-8"))
                    
                    # Le ate encontrar \n
                    buffer = ""
                    while "\n" not in buffer:
                        chunk = self._sock.recv(4096).decode("utf-8")
                        if not chunk:
                            raise ConnectionError("Socket do contador fechado")
                        buffer += chunk
                    line = buffer.split("\n", 1)[0].strip()
                    res = json.loads(line)
                    if not res.get("ok"):
                        raise RuntimeError(f"Erro no contador: {res.get('error')}")
                    return res
                except Exception:
                    self._disconnect()
                    if attempt == 1:
                        raise

    def read(self) -> int:
        res = self._send_cmd({"op": "READ", "node": self.node_id})
        return res["value"]

    def write(self, value: int):
        self._send_cmd({"op": "WRITE", "node": self.node_id, "value": value})