"""Exercise registry-backed select with real sockets and upstream selectors."""

import io
import socket

import pytest

from diaspore import Machine
from diaspore.compiler import compile_bundle


def run_source(tmp_path, source, *, bindings=None, caps=()):
    path = tmp_path / "select_program.py"
    path.write_text(source)
    bundle = compile_bundle(str(path), extra_modules={"host"} if bindings else set())
    stdout, stderr = io.StringIO(), io.StringIO()
    machine = Machine(bundle, caps=caps, bindings=bindings, stdout=stdout, stderr=stderr)
    try:
        code = machine.run()
    finally:
        machine.close()
    return code, stdout.getvalue(), stderr.getvalue()


def test_select_preserves_objects_and_duplicates_with_real_socket(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    peers = []

    def accept():
        peers.append(listener.accept()[0])

    def send():
        peers[0].sendall(b"ready")

    def close_peer():
        peers[0].close()

    try:
        result = run_source(
            tmp_path,
            f"""
import host
import select
import selectors
import socket

connection = socket.socket()
connection.connect(("127.0.0.1", {port}))
host.accept()
fd = connection.fileno()
assert isinstance(fd, int) and fd >= 0
class FileObject:
    def fileno(self):
        return fd
fileobj = FileObject()
assert select.select([connection], [], [], 0) == ([], [], [])
readable, writable, exceptional = select.select([], [connection, fd, fileobj, connection], [], 0)
assert readable == [] and exceptional == []
assert writable[0] is connection and writable[1] == fd
assert writable[2] is fileobj and writable[3] is connection
host.send()
readable, writable, exceptional = select.select([fileobj, connection, fd, fileobj], [], [], 1)
assert readable[0] is fileobj and readable[1] is connection
assert readable[2] == fd and readable[3] is fileobj
assert writable == [] and exceptional == []
with selectors.DefaultSelector() as selector:
    assert isinstance(selector, selectors.SelectSelector)
    selector.register(connection, selectors.EVENT_READ, "payload")
    ready = selector.select(1)
    assert len(ready) == 1
    key, events = ready[0]
    assert key.fileobj is connection and key.data == "payload"
    assert events == selectors.EVENT_READ
assert connection.recv(5) == b"ready"
host.close_peer()
assert select.select([connection], [], [], 1)[0] == [connection]
assert connection.recv(1) == b""
connection.close()
assert connection.fileno() == -1
print("ready")
""",
            bindings={"host": {"accept": accept, "send": send, "close_peer": close_peer}},
            caps={"net"},
        )
        assert result == (0, "ready\n", "")
    finally:
        for peer in peers:
            peer.close()
        listener.close()


def test_select_listener_readiness(tmp_path):
    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()
    peers = []

    def connect():
        peers.append(socket.create_connection(("127.0.0.1", port)))

    try:
        result = run_source(
            tmp_path,
            f"""
import host
import select
import socket
listener = socket.socket()
listener.bind(("127.0.0.1", {port}))
listener.listen()
assert select.select([listener], [], [], 0) == ([], [], [])
host.connect()
assert select.select([listener, listener], [], [], 1)[0] == [listener, listener]
connection, address = listener.accept()
assert connection.fileno() >= 0
connection.close()
listener.close()
assert listener.fileno() == -1
""",
            bindings={"host": {"connect": connect}},
            caps={"net"},
        )
        assert result == (0, "", "")
    finally:
        for peer in peers:
            peer.close()


@pytest.mark.parametrize(
    "expression,exception",
    [
        ("select.select([-1], [], [], 0)", "ValueError"),
        ("select.select([1 << 40], [], [], 0)", "OverflowError"),
        ("select.select([object()], [], [], 0)", "TypeError"),
        ("select.select([], [], [], -1)", "ValueError"),
        ("select.select([], [], [], float('nan'))", "ValueError"),
        ("select.select([], [], [], float('inf'))", "OverflowError"),
        ("select.select([], [], [], '0')", "TypeError"),
    ],
)
def test_select_argument_validation_matches_cpython(tmp_path, expression, exception):
    import select

    with pytest.raises(getattr(__import__("builtins"), exception)):
        eval(expression, {"select": select})
    result = run_source(
        tmp_path,
        "import select\ntry:\n    "
        + expression
        + "\nexcept "
        + exception
        + ":\n    print('caught')\n",
        caps={"net"},
    )
    assert result == (0, "caught\n", "")


def test_select_rejects_host_descriptors_and_requires_capability(tmp_path):
    host_socket = socket.socket()
    try:
        source = f"""
import select
try:
    select.select([{host_socket.fileno()}], [], [], 0)
except OSError as error:
    assert error.errno == 9
else:
    raise AssertionError("host descriptor escaped registry validation")
"""
        assert run_source(tmp_path, source, caps={"net"}) == (0, "", "")
    finally:
        host_socket.close()
    source = """
import select
try:
    select.select([], [], [], 0)
except PermissionError:
    print("denied")
"""
    assert run_source(tmp_path, source) == (0, "denied\n", "")


def test_unconnected_socket_has_no_fabricated_handle(tmp_path):
    source = """
import socket
connection = socket.socket()
try:
    connection.fileno()
except NotImplementedError:
    print("unconnected")
connection.close()
assert connection.fileno() == -1
"""
    assert run_source(tmp_path, source) == (0, "unconnected\n", "")
