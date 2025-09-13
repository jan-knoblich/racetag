import socket

def connect_event_channel(ip, port=50008):
    print(f"Connecting to the event channel at {ip}:{port}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((ip, port))
    print("Connected. Waiting for events...\n")

    buffer = ""
    try:
        while True:
            data = s.recv(1024).decode("utf-8", errors="ignore")
            if not data:
                print("Connection closed by the reader.")
                break

            buffer += data

            # Events end with \r\n\r\n
            while "\r\n\r\n" in buffer:
                event, buffer = buffer.split("\r\n\r\n", 1)
                print(f"[Event received]: {event.strip()}\n")
                print(f"[Event received]: {event}\n")
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        s.close()

if __name__ == "__main__":
    reader_ip = "192.168.1.130"  # Current IP of your reader
    connect_event_channel(reader_ip)
