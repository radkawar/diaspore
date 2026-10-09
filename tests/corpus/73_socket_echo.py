# diaspore-caps: spawn,net
"""Exercise IPv4 TCP with ordered output and no packet-size assumptions.

The harness has no port substitution; this program needs loopback port 38641.
Native net cannot report the local port assigned by an ephemeral bind.
"""

import socket
from threading import Thread

PORT = 38641
records = []


def receive_exact(connection, size):
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise AssertionError("unexpected end of stream")
        data += chunk
    return data


with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    print(listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1))
    print(listener.bind(("127.0.0.1", PORT)), listener.listen(64))

    def serve():
        for i in range(2):
            connection, address = listener.accept()
            with connection:
                records.append((connection.getpeername() == address,
                                address[0] == "127.0.0.1", address[1] > 0))
                data = receive_exact(connection, 5)
                connection.sendall(data.upper())

    server = Thread(target=serve, daemon=True)
    server.start()
    for i in range(2):
        if i == 0:
            client = socket.create_connection(("127.0.0.1", PORT))
        else:
            client = socket.socket()
            print(client.connect(("127.0.0.1", PORT)))
        with client:
            print(client.getpeername() == ("127.0.0.1", PORT))
            print(client.settimeout(None), client.gettimeout())
            print(client.setblocking(True), client.getblocking())
            print(client.sendall(b""), client.recv(0))
            print(client.sendall(b"hello", 0))
            print(receive_exact(client, 5), client.recv(1))
        print(client.close())
        try:
            client.recv(1)
        except OSError:
            print("closed connection")
    print(server.join(), server.is_alive())
    print(records)
print(listener.close())
try:
    listener.accept()
except OSError:
    print("closed listener")
