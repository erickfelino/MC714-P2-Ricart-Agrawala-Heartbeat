import socket
import json
import threading

value = 0
value_lock = threading.Lock()

def handle_client(conn, addr):
    global value
    buffer = ""
    with conn:
        while True:
            try:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    
                    msg = json.loads(line)
                    op = msg.get("op")
                    node_id = msg.get("node")

                    if op == "READ":
                        with value_lock:
                            current_val = value
                        res = {"ok": True, "value": current_val}
                        conn.sendall((json.dumps(res) + "\n").encode("utf-8"))
                        
                    elif op == "WRITE":
                        with value_lock:
                            value = msg.get("value")
                            current_val = value
                        print(f"[MOCK COUNTER] Nó {node_id} escreveu valor: {current_val}")
                        res = {"ok": True}
                        conn.sendall((json.dumps(res) + "\n").encode("utf-8"))
            except Exception as e:
                break

def run_mock_counter():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 6000))
    server.listen(128)
    
    print("[MOCK COUNTER] Escutando na porta 6000 (Multi-thread)...")

    while True:
        try:
            conn, addr = server.accept()
            # Dispara uma thread por cliente para nao travar o accept()
            client_thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            client_thread.start()
        except Exception as e:
            print(f"[MOCK COUNTER] Erro no accept: {e}")
            break

if __name__ == "__main__":
    run_mock_counter()