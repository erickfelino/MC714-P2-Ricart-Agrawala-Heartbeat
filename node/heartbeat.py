import threading
import time
from typing import Callable, Dict, Optional

class HeartbeatDetector:
    def __init__(self, config, logger, network_manager, on_suspect_callback: Callable[[int, Optional[int]], None]):
        self.config = config
        self.logger = logger
        self.net = network_manager
        self.on_suspect_callback = on_suspect_callback
        
        self.seq = 0
        self._last_hb_us: Dict[int, Optional[int]] = {
            p: None for p in config.PEERS if p != config.NODE_ID
        }
        self._suspected_nodes = set()
        
        self.active_detection = False
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        t_sender = threading.Thread(target=self._sender_loop, daemon=True)
        t_checker = threading.Thread(target=self._checker_loop, daemon=True)
        t_sender.start()
        t_checker.start()

    def handle_heartbeat(self, from_node: int, seq: int):
        now_us = time.time_ns() // 1000
        with self._lock:
            if from_node not in self._suspected_nodes:
                self._last_hb_us[from_node] = now_us

    def _sender_loop(self):
        interval_s = self.config.HB_INTERVAL_MS / 1000.0
        while self._running:
            for peer in self.config.PEERS:
                if peer != self.config.NODE_ID:
                    hb_msg = {
                        "type": "HEARTBEAT",
                        "from": self.config.NODE_ID,
                        "to": peer,
                        "seq": self.seq,
                    }
                    self.net.send_message(peer, hb_msg)
            self.seq += 1
            time.sleep(interval_s)

    def _checker_loop(self):
        check_s = self.config.HB_CHECK_MS / 1000.0
        timeout_us = int(self.config.HB_TIMEOUT_MS * 1000)
        
        while self._running:
            time.sleep(check_s)
            if not self.active_detection:
                continue
                
            now_us = time.time_ns() // 1000
            newly_suspected = []
            
            with self._lock:
                for peer, last_us in self._last_hb_us.items():
                    if peer in self._suspected_nodes:
                        continue
                    
                    # Se nunca recebeu ou estourou o timeout
                    if last_us is None:
                        # Assume inicio do monitoramento se nulo
                        continue
                    elif now_us - last_us > timeout_us:
                        self._suspected_nodes.add(peer)
                        newly_suspected.append((peer, last_us))
                        
            for peer, last_us in newly_suspected:
                self.on_suspect_callback(peer, last_us)

    def enable_suspect_checking(self):
        now_us = time.time_ns() // 1000
        with self._lock:
            for peer in self._last_hb_us:
                if self._last_hb_us[peer] is None:
                    self._last_hb_us[peer] = now_us
            self.active_detection = True

    def disable_suspect_checking(self):
        self.active_detection = False

    def stop(self):
        self._running = False