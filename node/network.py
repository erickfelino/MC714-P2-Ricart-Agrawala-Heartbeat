import json
import queue
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
        
        # Envio FIFO por destino: uma fila e uma thread de envio para cada par.
        # O horario de envio e calculado quando a mensagem entra na fila,
        # entao o atraso injetado nao se acumula entre mensagens.
        self._enqueue_lock = threading.Lock()
        self._queues: Dict[int, queue.Queue] = {
            p: queue.Queue() for p in config.PEERS if p != config.NODE_ID
        }
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

        for peer in self._queues:
            threading.Thread(target=self._sender_loop, args=(peer,), daemon=True).start()

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

        # connect fora do lock global: um par morto nao trava o envio aos outros
        host, port = self.config.PEERS[to_node]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.config.CONNECT_TIMEOUT_MS / 1000.0)
        try:
            s.connect((host, port))
        except Exception:
            s.close()
            raise
        with self._sockets_lock:
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
        """Coloca a mensagem na fila do destino com o horario de envio ja calculado."""
        with self._enqueue_lock:
            delay_ms = self.config.NET_DELAY_MS
            if self.config.NET_JITTER_MS > 0:
                delay_ms += self.net_rng.uniform(0, self.config.NET_JITTER_MS)
            now_us = time.time_ns() // 1000
            target_send_us = max(now_us + int(delay_ms * 1000), self._last_send_time_us[to_node])
            self._last_send_time_us[to_node] = target_send_us
            self._queues[to_node].put((target_send_us, msg))

    def _sender_loop(self, to_node: int):
        q = self._queues[to_node]
        while self._running:
            try:
                target_send_us, msg = q.get(timeout=0.5)
            except queue.Empty:
                continue
            sleep_us = target_send_us - (time.time_ns() // 1000)
            if sleep_us > 0:
                time.sleep(sleep_us / 1_000_000.0)
            self._deliver(to_node, msg)

    def _deliver(self, to_node: int, msg: dict):
        extra = {"msg": msg["type"], "to": to_node}
        if "req_ts" in msg:
            extra["req_ts"] = msg["req_ts"]
        try:
            sock = self._get_connection(to_node)
            payload = json.dumps(msg, ensure_ascii=False) + "\n"
            sock.sendall(payload.encode("utf-8"))
            self.logger.log("SEND", self.get_lamport(), **extra)
        except Exception as e:
            self._drop_connection(to_node)
            self.logger.log("SEND_FAIL", self.get_lamport(), error=str(e), **extra)

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