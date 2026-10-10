# diaspore-caps: net,spawn
"""Exercise the public socket wrapper over a blocking numeric IPv4 stream.

The fixed loopback port is needed because native net does not expose the local
address selected by an ephemeral bind.
"""

import _socket
import atexit
import socket
import time
from threading import Thread

PORT = 38642
records = []


class Endpoint(socket.socket):
    def __init__(self):
        super().__init__(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.label = "client"


def receive_exact(connection, length):
    result = b""
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise AssertionError("unexpected EOF")
        result += chunk
    return result


print(issubclass(socket.socket, _socket.socket), socket.SocketType is _socket.socket)
print(socket.error is OSError, socket.timeout is TimeoutError)
print(socket.setdefaulttimeout(None), socket.getdefaulttimeout())
addresses = socket.getaddrinfo(
    "127.0.0.1", str(PORT), socket.AF_INET, socket.SOCK_STREAM,
    socket.IPPROTO_TCP, socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
print([(af.name, kind.name, proto, name, address)
       for af, kind, proto, name, address in addresses])

with socket.create_server(("127.0.0.1", PORT), backlog=32) as listener:
    print(listener.family.name, listener.type.name, listener.proto)

    def serve():
        for index in range(2):
            connection, address = listener.accept()
            with connection:
                records.append((type(connection) is socket.socket,
                                connection.getpeername() == address,
                                address[0], address[1] > 0))
                records.append((connection.family == socket.AF_INET,
                                connection.type == socket.SOCK_STREAM,
                                connection.getblocking(), connection.gettimeout()))
                request = receive_exact(connection, 4)
                connection.sendall(request.upper())

    server = Thread(target=serve)
    server.start()
    with Endpoint() as client:
        print(client.label, client.family.name, client.type.name, client.proto)
        print(client.connect(("127.0.0.1", PORT)))
        print(client.send(b"ping"), client.getpeername() == ("127.0.0.1", PORT))
        result = bytearray(4)
        received = 0
        while received < len(result):
            count = client.recv_into(memoryview(result)[received:])
            if not count:
                raise AssertionError("unexpected EOF")
            received += count
        print(bytes(result), client.recv(1))
    print(client.close())
    try:
        client.send(b"closed")
    except OSError:
        print("closed connection")
    with socket.create_connection(("127.0.0.1", PORT), all_errors=True) as client:
        print(client.sendall(bytearray(b"pong")))
        print(receive_exact(client, 4), client.recv(1))
    print(server.join(), server.is_alive())
    print(records)
print(listener.close())

shutdown_order = []


def finish_thread():
    time.sleep(0.02)
    shutdown_order.append("thread")


def exit_callback():
    shutdown_order.append("atexit")
    print("shutdown order", shutdown_order)


Thread(target=finish_thread).start()
atexit.register(exit_callback)
