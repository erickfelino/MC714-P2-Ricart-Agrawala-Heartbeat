import json
import os
import threading
import time

class Logger:
    def __init__(self, log_dir: str, node_id: int):
        self.node_id = node_id
        os.makedirs(log_dir, exist_ok=True)
        self.filepath = os.path.join(log_dir, f"node-{node_id}.jsonl")
        self._lock = threading.Lock()
        self._file = open(self.filepath, "a", encoding="utf-8")

    def _get_t_us(self) -> int:
        return time.time_ns() // 1000

    def log(self, event: str, lamport: int, **extra):
        """Escreve uma linha de log JSON e faz flush imediato."""
        payload = {
            "t_us": self._get_t_us(),
            "node": self.node_id,
            "event": event,
            "lamport": lamport,
        }
        payload.update(extra)
        
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        
        with self._lock:
            self._file.write(line)
            self._file.flush()

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()