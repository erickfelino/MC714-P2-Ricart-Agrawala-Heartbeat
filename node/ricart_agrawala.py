import threading
import time
from typing import Optional, Set

def upper_priority(my, other):
    return my[0] < other[0] or my[0] == other[0] and my[1] < other[1]

class RicartAgrawala:
    def __init__(self, config, logger, network_manager, counter_client):
        self.config = config
        self.logger = logger
        self.net = network_manager
        self.counter = counter_client

        # relógio lógico
        self.lamport = 0
        self.state = "RELEASED"  # RELEASED, WANTED, HELD

        # momento em que o nó realizou um requisição de acesso a região crítica
        self.req_ts = 0

        # lista de respostas adiadas, pendentes de resposta (aguardando REPLY) e nós suspeitos de terem caído (heartbeat)
        self.deferred_queue = []
        self.pending_replies: Set[int] = set()
        self.suspects: Set[int] = set()

        # controle de concorrência local
        self._lock = threading.Lock()
        self._cs_condition = threading.Condition(self._lock)

    def get_lamport(self) -> int:
        with self._lock:
            return self.lamport

    def on_suspect(self, peer: int, last_hb_us: Optional[int]):
        with self._lock:
            self.suspects.add(peer)
            self.logger.log("SUSPECT", self.lamport, peer=peer, last_hb_us=last_hb_us)
            
            # Remove nó de pending_replies e deferred_queue
            if peer in self.pending_replies:
                self.pending_replies.remove(peer)
                if len(self.pending_replies) == 0:
                    self._cs_condition.notify_all()
                    
            self.deferred_queue = [item for item in self.deferred_queue if item[0] != peer]

    def process_message(self, msg: dict):
        mtype = msg["type"]
        from_node = msg["from"]
        
        with self._lock:
            # Mensagens de nós suspeitos são ignoradas
            if from_node in self.suspects:
                self.logger.log("RECV", self.lamport, msg=mtype, **{"from": from_node})
                return

            # Registra no log o recebimento de uma mensagem de requisição/reply
            if mtype in ("REQUEST", "REPLY"):
                remote_lamport = msg["lamport"]
                self.lamport = max(self.lamport, remote_lamport) + 1
                
                extra = {"from": from_node}
                if "req_ts" in msg:
                    extra["req_ts"] = msg["req_ts"]
                self.logger.log("RECV", self.lamport, msg=mtype, **extra)
                
            if mtype == "REQUEST":
                req_ts = msg["req_ts"]
                my_priority = (self.req_ts, self.config.NODE_ID)
                other_priority = (req_ts, from_node)

                # P_j adia a resposta de P_i se
                #   1 - P_j está dentro da região crítica ou
                #   2 - P_j quer entrar na região crítica e possui uma solicitação mais antiga (ou ID menor, em caso de empate) que P_i
                # Caso contrário, responde com REPLY

                defer = (self.state == "HELD") or (self.state == "WANTED" and upper_priority(my_priority, other_priority))
                
                if defer:
                    if from_node not in self.deferred_queue:
                        self.deferred_queue.append((from_node, req_ts))
                else:
                    self.lamport += 1
                    reply_msg = {
                        "type": "REPLY",
                        "from": self.config.NODE_ID,
                        "to": from_node,
                        "lamport": self.lamport,
                        "req_ts": req_ts,
                    }
                    self.net.send_message(from_node, reply_msg)
                    
            elif mtype == "REPLY":
                # recebe uma resposta para acesso a região crítica.
                # se todos os outros nós responderam, acorda o nó solicitante.
                req_ts = msg["req_ts"]
                if self.state == "WANTED" and req_ts == self.req_ts:
                    if from_node in self.pending_replies:
                        self.pending_replies.remove(from_node)
                        if len(self.pending_replies) == 0:
                            self._cs_condition.notify_all()

    def execute_critical_section(self):
        """Solicita a RC, executa a operacao no contador e sai da RC."""
        with self._lock:
            self.state = "WANTED"
            self.lamport += 1
            self.req_ts = self.lamport
            
            self.logger.log("REQUEST", self.lamport, req_ts=self.req_ts)

            # envia mensagem de solicitação para todos os outros processos
            self.pending_replies = {
                p for p in self.config.PEERS
                if p != self.config.NODE_ID and p not in self.suspects
            }
            
            req_msg = {
                "type": "REQUEST",
                "from": self.config.NODE_ID,
                "lamport": self.lamport,
                "req_ts": self.req_ts,
            }

            peers_to_send = list(self.pending_replies)

        for peer in peers_to_send:
            msg = dict(req_msg)
            msg["to"] = peer
            self.net.send_message(peer, msg)

        with self._lock:   
            # Aguarda todas as respostas
            while len(self.pending_replies) > 0:
                self._cs_condition.wait()

            self.state = "HELD"
            self.logger.log("ENTER", self.lamport, req_ts=self.req_ts)

        # Dentro da Regiao Critica (Sem lock mantido durante E/S de rede)
        written_val = None
        val = None

        try:
            val = self.counter.read()
            time.sleep(self.config.CS_DURATION_MS / 1000.0)
            written_val = val + 1
            self.counter.write(written_val)
        except Exception as e:
            print(f"[NÓ {self.config.NODE_ID}] Erro na RC ao comunicar com o contador: {e}", flush=True)
        finally:
            # Saindo da Regiao Critica
            to_reply = []
            with self._lock:
                self.logger.log("EXIT", self.lamport, req_ts=self.req_ts, read=val, written=written_val)
                self.state = "RELEASED"
                
                # Envia REPLY para todos os adiados
                to_reply = list(self.deferred_queue)
                self.deferred_queue.clear()
                
            for peer, req_ts_target in to_reply:
                with self._lock:
                    self.lamport += 1
                reply_msg = {
                    "type": "REPLY",
                    "from": self.config.NODE_ID,
                    "to": peer,
                    "lamport": self.lamport,
                    "req_ts": req_ts_target,
                }
                self.net.send_message(peer, reply_msg)
