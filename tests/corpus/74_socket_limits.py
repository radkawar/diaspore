# diaspore-caps: net
"""Pin explicit rejections for operations missing from native net."""

import socket


def unsupported(label, operation):
    try:
        operation()
    except NotImplementedError:
        print(label)
    else:
        raise AssertionError(label)


unsupported("IPv6", lambda: socket.socket(socket.AF_INET6))
unsupported("datagrams", lambda: socket.socket(type=socket.SOCK_DGRAM))
unsupported("file descriptor constructor", lambda: socket.socket(fileno=1))
unsupported("connection timeout", lambda: socket.create_connection(("127.0.0.1", 1), 1))
unsupported("source address", lambda: socket.create_connection(
    ("127.0.0.1", 1), source_address=("127.0.0.1", 0)))
unsupported("aggregated errors", lambda: socket.create_connection(
    ("127.0.0.1", 1), all_errors=True))

with socket.socket() as endpoint:
    unsupported("timeout", lambda: endpoint.settimeout(1))
    unsupported("zero timeout", lambda: endpoint.settimeout(0))
    unsupported("nonblocking", lambda: endpoint.setblocking(False))
    unsupported("local address", endpoint.getsockname)
    unsupported("file descriptor", endpoint.fileno)
    unsupported("shutdown", lambda: endpoint.shutdown(0))
    try:
        endpoint.listen("64")
    except TypeError:
        print("backlog integer")
    unsupported("disable reuse", lambda: endpoint.setsockopt(
        socket.SOL_SOCKET, socket.SO_REUSEADDR, 0))
    unsupported("unknown option", lambda: endpoint.setsockopt(socket.SOL_SOCKET, 999, 1))
    unsupported("DNS family selection", lambda: endpoint.connect(("localhost", 1)))
    try:
        endpoint.bind(("127.0.0.1", 65536))
    except OverflowError:
        print("port range")
    try:
        endpoint.bind(["127.0.0.1", 0])
    except TypeError:
        print("address tuple")
    endpoint.bind(("127.0.0.1", 0))
    unsupported("bound connection", lambda: endpoint.connect(("127.0.0.1", 1)))
    try:
        endpoint.bind(("127.0.0.1", 0))
    except OSError:
        print("already bound")

with socket.socket() as endpoint:
    endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    unsupported("outgoing reuse option", lambda: endpoint.connect(("127.0.0.1", 1)))

with socket.socket(proto=socket.IPPROTO_TCP) as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    print("repeat listen", listener.listen())
    unsupported("ephemeral local port", listener.getsockname)
    try:
        listener.getpeername()
    except OSError:
        print("listener has no peer")
print("repeated close", listener.close())
