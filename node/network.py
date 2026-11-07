import json
import random
import socket
import threading
import time
from typing import Callable, Dict, Tuple

class NetworkManager:
    def __init__(self, config, logger, lamport_supplier: Callable[[], int]):
        self.config = config
        self.logger = logger
        self.get_lamport = lamport_supplier
        
        self.net_rng = random.Random(f"net-{config.SEED}-{config.NODE_ID}")
        
        self._on_message_callback = None
        self._server_socket = None
        self._running = False
        
        self._sockets_lock = threading.Lock()
        self._peer_sockets: Dict[int, socket.socket] = {}
        
        # Controle de envio FIFO por destino
        self._fifo_locks: Dict[int, threading.Lock] = {p: threading.Lock() for p in config.PEERS}
        self._last_send_time_us: Dict[int, int] = {p: 0 for p in config.PEERS}

    def set_message_handler(self, callback: Callable[[dict], None]):
        self._on_message_callback = callback

    def start_server(self):
        self._running = True
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("0.0.0.0", self.config.MY_PORT))
        self._server_socket.listen(128)
        
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                t = threading.Thread(target=self._client_read_loop, args=(conn,), daemon=True)
                t.start()
            except Exception:
                if not self._running:
                    break

    def _client_read_loop(self, conn: socket.socket):
        with conn:
            buffer = ""
            while self._running:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line and self._on_message_callback:
                            msg = json.loads(line)
                            self._on_message_callback(msg)
                except Exception:
                    break

    def _get_connection(self, to_node: int) -> socket.socket:
        with self._sockets_lock:
            if to_node in self._peer_sockets:
                return self._peer_sockets[to_node]
            
            host, port = self.config.PEERS[to_node]
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.config.CONNECT_TIMEOUT_MS / 1000.0)
            s.connect((host, port))
            self._peer_sockets[to_node] = s
            return s

    def _drop_connection(self, to_node: int):
        with self._sockets_lock:
            s = self._peer_sockets.pop(to_node, None)
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def send_message(self, to_node: int, msg: dict):
        """Envia mensagem assincronamente injetando atraso artificial e mantendo FIFO."""
        t = threading.Thread(target=self._send_task, args=(to_node, msg), daemon=True)
        t.start()

    def _send_task(self, to_node: int, msg: dict):
        fifo_lock = self._fifo_locks[to_node]
        
        with fifo_lock:
            # Calculo do atraso sintético
            delay_ms = self.config.NET_DELAY_MS
            if self.config.NET_JITTER_MS > 0:
                delay_ms += self.net_rng.uniform(0, self.config.NET_JITTER_MS)
                
            delay_us = int(delay_ms * 1000)
            now_us = time.time_ns() // 1000
            target_send_us = max(now_us + delay_us, self._last_send_time_us[to_node])
            self._last_send_time_us[to_node] = target_send_us
            
            # Aguarda ate o momento exato de envio
            sleep_us = target_send_us - (time.time_ns() // 1000)
            if sleep_us > 0:
                time.sleep(sleep_us / 1_000_000.0)
                
            # Tentativa de envio TCP
            try:
                sock = self._get_connection(to_node)
                payload = json.dumps(msg, ensure_ascii=False) + "\n"
                sock.sendall(payload.encode("utf-8"))
                
                # Log de SEND
                extra = {"msg": msg["type"], "to": to_node}
                if "req_ts" in msg:
                    extra["req_ts"] = msg["req_ts"]
                self.logger.log("SEND", self.get_lamport(), **extra)
            except Exception as e:
                self._drop_connection(to_node)
                extra = {"msg": msg["type"], "to": to_node, "error": str(e)}
                if "req_ts" in msg:
                    extra["req_ts"] = msg["req_ts"]
                self.logger.log("SEND_FAIL", self.get_lamport(), **extra)

    def stop(self):
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        with self._sockets_lock:
            for s in self._peer_sockets.values():
                try:
                    s.close()
                except Exception:
                    pass
            self._peer_sockets.clear()