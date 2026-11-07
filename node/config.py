import os
import sys

class Config:
    def __init__(self):
        # Obrigatorias
        self.NODE_ID = int(self._get_env("NODE_ID"))
        peers_raw = self._get_env("PEERS")
        
        # Parse de PEERS: "1@node1:5000,2@node2:5000" -> {1: ("node1", 5000), ...}
        self.PEERS = {}
        for item in peers_raw.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split("@")
            node_id = int(parts[0])
            host, port = parts[1].split(":")
            self.PEERS[node_id] = (host, int(port))
            
        if self.NODE_ID not in self.PEERS:
            raise ValueError(f"NODE_ID {self.NODE_ID} nao encontrado em PEERS")

        self.MY_HOST, self.MY_PORT = self.PEERS[self.NODE_ID]

        # Endereco do contador
        counter_raw = os.getenv("COUNTER_ADDR", "counter:6000")
        c_host, c_port = counter_raw.split(":")
        self.COUNTER_HOST = c_host
        self.COUNTER_PORT = int(c_port)

        # Opcionais e padroes
        self.LOG_DIR = os.getenv("LOG_DIR", "/logs")
        self.RUN_ID = os.getenv("RUN_ID", "")
        self.SEED = int(os.getenv("SEED", "1"))
        
        # Tempos
        self.DURATION_S = float(os.getenv("DURATION_S", "60"))
        self.START_DELAY_S = float(os.getenv("START_DELAY_S", "3"))
        self.DRAIN_S = float(os.getenv("DRAIN_S", "5"))
        
        self.REQ_INTERVAL_MS = float(os.getenv("REQ_INTERVAL_MS", "200"))
        self.CS_DURATION_MS = float(os.getenv("CS_DURATION_MS", "50"))
        
        self.HB_INTERVAL_MS = float(os.getenv("HB_INTERVAL_MS", "500"))
        self.HB_TIMEOUT_MS = float(os.getenv("HB_TIMEOUT_MS", "2000"))
        self.HB_CHECK_MS = float(os.getenv("HB_CHECK_MS", "100"))
        
        self.NET_DELAY_MS = float(os.getenv("NET_DELAY_MS", "0"))
        self.NET_JITTER_MS = float(os.getenv("NET_JITTER_MS", "0"))
        self.CONNECT_TIMEOUT_MS = float(os.getenv("CONNECT_TIMEOUT_MS", "1000"))

    def _get_env(self, key: str) -> str:
        val = os.getenv(key)
        if not val:
            print(f"[ERRO] Variavel de ambiente obrigatoria ausente: {key}", file=sys.stderr)
            sys.exit(1)
        return val

    def to_dict(self) -> dict:
        """Retorna todas as configuracoes para salvar no log de START."""
        return {
            "NODE_ID": self.NODE_ID,
            "PEERS": os.getenv("PEERS"),
            "COUNTER_ADDR": os.getenv("COUNTER_ADDR", "counter:6000"),
            "LOG_DIR": self.LOG_DIR,
            "RUN_ID": self.RUN_ID,
            "SEED": self.SEED,
            "DURATION_S": self.DURATION_S,
            "START_DELAY_S": self.START_DELAY_S,
            "DRAIN_S": self.DRAIN_S,
            "REQ_INTERVAL_MS": self.REQ_INTERVAL_MS,
            "CS_DURATION_MS": self.CS_DURATION_MS,
            "HB_INTERVAL_MS": self.HB_INTERVAL_MS,
            "HB_TIMEOUT_MS": self.HB_TIMEOUT_MS,
            "HB_CHECK_MS": self.HB_CHECK_MS,
            "NET_DELAY_MS": self.NET_DELAY_MS,
            "NET_JITTER_MS": self.NET_JITTER_MS,
            "CONNECT_TIMEOUT_MS": self.CONNECT_TIMEOUT_MS,
        }