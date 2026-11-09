import random
import time
from node.config import Config
from node.counter_client import CounterClient
from node.heartbeat import HeartbeatDetector
from node.logger import Logger
from node.network import NetworkManager
from node.ricart_agrawala import RicartAgrawala

def main():

    # inicializa Config e Logger
    config = Config()
    logger = Logger(config.LOG_DIR, config.NODE_ID)
    
    # Log de START
    logger.log("START", 0, config=config.to_dict())

    counter_client = CounterClient(
        config.COUNTER_HOST, config.COUNTER_PORT, config.NODE_ID, config.CONNECT_TIMEOUT_MS
    )
    
    # Forward declarations para injetar dependencias circulares
    ra = None

    def get_lamport_fn():
        return ra.get_lamport() if ra else 0

    net = NetworkManager(config, logger, get_lamport_fn)
    
    def on_suspect_fn(peer, last_hb_us):
        if ra:
            ra.on_suspect(peer, last_hb_us)

    hb = HeartbeatDetector(config, logger, net, on_suspect_fn)
    ra = RicartAgrawala(config, logger, net, counter_client)

    def incoming_msg_handler(msg: dict):
        mtype = msg.get("type")
        if mtype == "HEARTBEAT":
            hb.handle_heartbeat(msg["from"], msg["seq"])
        else:
            ra.process_message(msg)

    net.set_message_handler(incoming_msg_handler)

    print(f"\n==========================================", flush=True)
    print(f"[NÓ {config.NODE_ID}] Processo inicializado com sucesso!", flush=True)
    print(f"==========================================\n", flush=True)

    # 1. Abre o servidor TCP e comeca a mandar heartbeats
    net.start_server()
    hb.start()

    # 2. Aguarda START_DELAY_S e ativa detecção de suspeitos
    time.sleep(config.START_DELAY_S)
    hb.enable_suspect_checking()

    # 3. Gerador de carga (Carga Exponencial com Semente)
    load_rng = random.Random(f"load-{config.SEED}-{config.NODE_ID}")
    start_time = time.time()
    
    while time.time() - start_time < config.DURATION_S:
        ra.execute_critical_section()
        
        # Intervalo com distribuicao exponencial
        interval_ms = load_rng.expovariate(1.0 / config.REQ_INTERVAL_MS)
        time.sleep(interval_ms / 1000.0)

    # 4. Encerramento (DRAIN_S): para de suspeitar, mas continua mandando
    # heartbeats e respondendo REQUESTs ate o fim do DRAIN
    hb.disable_suspect_checking()
    time.sleep(config.DRAIN_S)
    hb.stop()

    # 5. Finalizacao
    logger.log("STOP", ra.get_lamport(), reason="duration")
    net.stop()
    logger.close()

    print(f"\n==========================================", flush=True)
    print(f"[NÓ {config.NODE_ID}] Processo finalizado com sucesso!", flush=True)
    print(f"==========================================\n", flush=True)

    return 0

if __name__ == "__main__":
    main()