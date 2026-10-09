"""Provide blocking IPv4 TCP sockets using native net.

Supported operations are bind/listen/accept, connect/create_connection,
recv/sendall, getpeername, close, and context management. Addresses must use
dotted-decimal IPv4 hosts; native connect cannot constrain DNS to AF_INET.
An empty bind host means all IPv4 interfaces.

Native net combines bind and listen, so bind only records the address; port
reservation and operating-system bind errors occur at listen. Listeners always
use SO_REUSEADDR. Only enabling that option is supported. The integer listen
backlog is advisory; native runtimes choose the actual queue size. Applications
must not rely on port reservation between bind and listen.

Timeouts, nonblocking I/O, local-address queries, source-address selection,
file descriptors, receive/send flags, and other socket families or types are
unsupported and raise NotImplementedError. Port zero can be bound, but its
chosen local port cannot be queried. Networking requires the net capability;
see docs/spec.md section 11.6.
"""

import net as _net

AF_INET = 2
AF_INET6 = 10
SOCK_STREAM = 1
SOCK_DGRAM = 2
SOL_SOCKET = 1
SO_REUSEADDR = 2
IPPROTO_TCP = 6
error = OSError

__all__ = [
    "AF_INET", "AF_INET6", "SOCK_STREAM", "SOCK_DGRAM", "SOL_SOCKET",
    "SO_REUSEADDR", "IPPROTO_TCP", "error", "socket", "create_connection",
]


def _address(address, binding=False):
    if not isinstance(address, tuple) or len(address) != 2:
        raise TypeError("AF_INET address must be a pair (host, port)")
    host, port = address
    if not isinstance(host, str):
        raise TypeError("host must be a string")
    if not isinstance(port, int):
        raise TypeError("port must be an integer")
    if port < 0 or port > 65535:
        raise OverflowError("port must be 0-65535")
    if host == "" and binding:
        host = "0.0.0.0"
    parts = host.split(".")
    if len(parts) != 4:
        raise NotImplementedError("only dotted-decimal IPv4 addresses are supported")
    for part in parts:
        if not part or not all("0" <= c <= "9" for c in part) or int(part) > 255:
            raise NotImplementedError("only dotted-decimal IPv4 addresses are supported")
    return host, port


def _peer(sid):
    host, separator, port = _net.peer(sid).rpartition(":")
    return host, int(port)


class socket:
    """Provide a blocking AF_INET/SOCK_STREAM endpoint."""

    def __init__(self, family=AF_INET, type=SOCK_STREAM, proto=0, fileno=None):
        if family != AF_INET or type != SOCK_STREAM or proto not in (0, IPPROTO_TCP):
            raise NotImplementedError("only AF_INET SOCK_STREAM TCP sockets are supported")
        if fileno is not None:
            raise NotImplementedError("socket file descriptors are not supported")
        self.family = family
        self.type = type
        self.proto = proto
        self._sid = None
        self._bound = None
        self._listening = False
        self._closed = False
        self._reuseaddr = False

    def _check_open(self):
        if self._closed:
            raise OSError("Bad file descriptor")

    def _connection(self):
        self._check_open()
        if self._sid is None or self._listening:
            raise OSError("Transport endpoint is not connected")
        return self._sid

    def setsockopt(self, level, optname, value, optlen=None):
        """Enable SO_REUSEADDR for listeners; reject other options."""
        self._check_open()
        if level != SOL_SOCKET or optname != SO_REUSEADDR or optlen is not None:
            raise NotImplementedError("only enabling SO_REUSEADDR is supported")
        if not isinstance(value, int) or not value:
            raise NotImplementedError("SO_REUSEADDR cannot be disabled")
        if self._sid is not None and not self._listening:
            raise NotImplementedError("socket options on connections are not supported")
        self._reuseaddr = True

    def bind(self, address):
        """Record a local address for the subsequent listen call."""
        self._check_open()
        if self._bound is not None or self._sid is not None:
            raise OSError("Invalid argument")
        self._bound = _address(address, True)

    def listen(self, backlog=128):
        """Bind and listen, accepting an advisory integer backlog."""
        self._check_open()
        if not isinstance(backlog, int):
            raise TypeError("backlog must be an integer")
        if self._listening:
            return None
        if self._sid is not None:
            raise OSError("Invalid argument")
        address = ("0.0.0.0", 0) if self._bound is None else self._bound
        self._sid = _net.listen(address[0], address[1])
        self._listening = True

    def accept(self):
        """Accept a connection and return its socket and peer address."""
        self._check_open()
        if not self._listening:
            raise OSError("Invalid argument")
        sid = _net.accept(self._sid)
        try:
            address = _peer(sid)
            connection = socket(self.family, self.type, self.proto)
            connection._sid = sid
            return connection, address
        except BaseException:
            _net.close(sid)
            raise

    def connect(self, address):
        """Connect to a dotted-decimal IPv4 host and port."""
        self._check_open()
        if self._sid is not None:
            raise OSError("Transport endpoint is already connected")
        if self._bound is not None:
            raise NotImplementedError("connecting from a bound address is not supported")
        if self._reuseaddr:
            raise NotImplementedError("SO_REUSEADDR on outgoing connections is not supported")
        host, port = _address(address)
        self._sid = _net.connect(host, port)

    def recv(self, bufsize, flags=0):
        """Receive up to bufsize bytes, returning empty bytes at EOF."""
        sid = self._connection()
        if flags != 0:
            raise NotImplementedError("socket receive flags are not supported")
        if not isinstance(bufsize, int):
            raise TypeError("bufsize must be an integer")
        if bufsize < 0:
            raise ValueError("negative buffersize in recv")
        if bufsize == 0:
            return b""
        return _net.recv(sid, bufsize)

    def sendall(self, data, flags=0):
        """Send all supplied bytes, returning None on success."""
        sid = self._connection()
        if flags != 0:
            raise NotImplementedError("socket send flags are not supported")
        if not isinstance(data, bytes):
            raise TypeError("sendall requires bytes")
        _net.send(sid, data)

    def getpeername(self):
        """Return the connected peer's (host, port) pair."""
        return _peer(self._connection())

    def getsockname(self):
        """Reject local-address queries, which native net does not expose."""
        self._check_open()
        raise NotImplementedError("native net does not expose local addresses")

    def settimeout(self, value):
        """Accept blocking mode only; native net has no timed operations."""
        self._check_open()
        if value is not None:
            raise NotImplementedError("socket timeouts are not supported")

    def gettimeout(self):
        """Return None because all supported sockets block indefinitely."""
        return None

    def setblocking(self, flag):
        """Accept blocking mode only."""
        self._check_open()
        if not flag:
            raise NotImplementedError("nonblocking sockets are not supported")

    def getblocking(self):
        """Return True because nonblocking sockets are unsupported."""
        return True

    def fileno(self):
        """Reject file descriptors, which are not native net identifiers."""
        raise NotImplementedError("socket file descriptors are not supported")

    def shutdown(self, how):
        """Reject half-closes, which native net does not expose."""
        self._check_open()
        raise NotImplementedError("socket shutdown is not supported")

    def close(self):
        """Close the endpoint; repeated calls have no effect."""
        if not self._closed:
            self._closed = True
            if self._sid is not None:
                sid = self._sid
                self._sid = None
                _net.close(sid)

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def create_connection(address, timeout=None, source_address=None, *, all_errors=False):
    """Connect to an IPv4 address without a timeout or source-address override."""
    if timeout is not None:
        raise NotImplementedError("socket timeouts are not supported")
    if source_address is not None:
        raise NotImplementedError("connection source addresses are not supported")
    if all_errors:
        raise NotImplementedError("aggregated connection errors are not supported")
    connection = socket()
    try:
        connection.connect(address)
        return connection
    except BaseException:
        connection.close()
        raise
