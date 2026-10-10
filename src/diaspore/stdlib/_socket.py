"""Supply the blocking IPv4 TCP backend for the unmodified socket module.

Native net combines bind and listen, so bind records an address and listen
reserves its port. Listeners enable SO_REUSEADDR and use an advisory backlog.
Only dotted-decimal addresses, blocking streams, and enabling SO_REUSEADDR
are supported. DNS, UDP, IPv6, timed I/O, half-closes, local-address queries,
and operating-system descriptors raise NotImplementedError.

An accepted connection transfers through a private, single-use ownership token.
Connected sockets and listeners expose their VM-private registry identifier
through fileno() for select; these are never host operating-system descriptors.
Unconnected sockets have no selectable handle. Native net capability checks
remain in force.
"""

import net as _net

AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 10
SOCK_STREAM = 1
SOCK_DGRAM = 2
SOL_SOCKET = 1
SO_REUSEADDR = 2
IPPROTO_IP = 0
IPPROTO_TCP = 6
IPPROTO_UDP = 17
SHUT_RD = 0
SHUT_WR = 1
SHUT_RDWR = 2
MSG_OOB = 1
MSG_PEEK = 2
MSG_WAITALL = 256
AI_PASSIVE = 1
AI_CANONNAME = 2
AI_NUMERICHOST = 4
AI_NUMERICSERV = 1024
error = OSError
timeout = TimeoutError
has_ipv6 = False

__all__ = [
    "AF_UNSPEC",
    "AF_INET",
    "AF_INET6",
    "SOCK_STREAM",
    "SOCK_DGRAM",
    "SOL_SOCKET",
    "SO_REUSEADDR",
    "IPPROTO_IP",
    "IPPROTO_TCP",
    "IPPROTO_UDP",
    "SHUT_RD",
    "SHUT_WR",
    "SHUT_RDWR",
    "MSG_OOB",
    "MSG_PEEK",
    "MSG_WAITALL",
    "AI_PASSIVE",
    "AI_CANONNAME",
    "AI_NUMERICHOST",
    "AI_NUMERICSERV",
    "error",
    "timeout",
    "herror",
    "gaierror",
    "has_ipv6",
    "socket",
    "SocketType",
    "getdefaulttimeout",
    "setdefaulttimeout",
    "getaddrinfo",
    "gethostname",
    "gethostbyname",
    "gethostbyname_ex",
    "gethostbyaddr",
    "getnameinfo",
    "getservbyname",
    "getservbyport",
    "getprotobyname",
    "close",
    "dup",
]


class herror(OSError):
    """Represent a host lookup error."""


class gaierror(OSError):
    """Represent an address lookup error."""


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
        if (
            not part
            or not all("0" <= c <= "9" for c in part)
            or len(part) > 3
            or int(part) > 255
            or (len(part) > 1 and part[0] == "0")
        ):
            raise NotImplementedError("only dotted-decimal IPv4 addresses are supported")
    return host, port


def _peer(sid):
    host, separator, port = _net.peer(sid).rpartition(":")
    return host, int(port)


class _AcceptedSocket:
    __slots__ = ("sid",)

    def __init__(self, sid):
        self.sid = sid


class socket:
    """Provide the base endpoint inherited by the public socket class."""

    __slots__ = (
        "_family",
        "_type",
        "_proto",
        "_sid",
        "_bound",
        "_listening",
        "_net_closed",
        "_reuseaddr",
    )

    def __init__(self, family=AF_INET, type=SOCK_STREAM, proto=0, fileno=None):
        if fileno is not None and not isinstance(fileno, _AcceptedSocket):
            raise NotImplementedError("socket file descriptors are not supported")
        if family != AF_INET or type != SOCK_STREAM or proto not in (0, IPPROTO_TCP):
            raise NotImplementedError("only AF_INET SOCK_STREAM TCP sockets are supported")
        self._family = int(family)
        self._type = int(type)
        self._proto = int(proto)
        self._sid = None
        self._bound = None
        self._listening = False
        # The public wrapper sets _closed before calling the base close method.
        self._net_closed = False
        self._reuseaddr = False
        if fileno is not None:
            if fileno.sid is None:
                raise OSError("Bad file descriptor")
            self._sid = fileno.sid
            fileno.sid = None

    @property
    def family(self):
        return self._family

    @property
    def type(self):
        return self._type

    @property
    def proto(self):
        return self._proto

    def _check_open(self):
        if self._net_closed:
            raise OSError("Bad file descriptor")

    def _connection(self):
        self._check_open()
        if self._sid is None or self._listening:
            raise OSError("Transport endpoint is not connected")
        return self._sid

    def setsockopt(self, level, optname, value, optlen=None):
        """Enable SO_REUSEADDR on a listener, rejecting other options."""
        self._check_open()
        if level != SOL_SOCKET or optname != SO_REUSEADDR or optlen is not None:
            raise NotImplementedError("only enabling SO_REUSEADDR is supported")
        if not isinstance(value, int) or not value:
            raise NotImplementedError("SO_REUSEADDR cannot be disabled")
        if self._sid is not None and not self._listening:
            raise NotImplementedError("socket options on connections are not supported")
        self._reuseaddr = True

    def getsockopt(self, level, optname, buflen=0):
        """Reject option queries, which native net does not expose."""
        self._check_open()
        raise NotImplementedError("socket option queries are not supported")

    def bind(self, address):
        """Record the local address to reserve when listen is called."""
        self._check_open()
        if self._bound is not None or self._sid is not None:
            raise OSError("Invalid argument")
        self._bound = _address(address, True)

    def listen(self, backlog=128):
        """Bind and listen with an advisory integer backlog."""
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

    def _accept(self):
        self._check_open()
        if not self._listening:
            raise OSError("Invalid argument")
        sid = _net.accept(self._sid)
        try:
            return _AcceptedSocket(sid), _peer(sid)
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
        """Receive at most bufsize bytes, returning empty bytes at EOF."""
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

    def recv_into(self, buffer, nbytes=0, flags=0):
        """Receive into a writable, contiguous byte buffer."""
        view = memoryview(buffer)
        if view.readonly:
            raise TypeError("recv_into() argument 1 must be read-write bytes-like object")
        view = view.cast("B")
        if not isinstance(nbytes, int):
            raise TypeError("nbytes must be an integer")
        if nbytes < 0:
            raise ValueError("negative buffersize in recv_into")
        if nbytes == 0:
            nbytes = len(view)
        if nbytes > len(view):
            raise ValueError("buffer too small for requested bytes")
        data = self.recv(nbytes, flags)
        view[: len(data)] = data
        return len(data)

    def send(self, data, flags=0):
        """Send bytes and return the number transferred."""
        sid = self._connection()
        if flags != 0:
            raise NotImplementedError("socket send flags are not supported")
        if not isinstance(data, bytes):
            data = bytes(memoryview(data))
        return _net.send(sid, data)

    def sendall(self, data, flags=0):
        """Send all bytes, returning None after a successful transfer."""
        self.send(data, flags)

    def getpeername(self):
        """Return the connected peer's host and port."""
        return _peer(self._connection())

    def getsockname(self):
        """Reject local-address queries, which native net does not expose."""
        self._check_open()
        raise NotImplementedError("native net does not expose local addresses")

    def settimeout(self, value):
        """Accept blocking mode only; timed operations are unsupported."""
        self._check_open()
        if value is not None:
            raise NotImplementedError("socket timeouts are not supported")

    def gettimeout(self):
        """Return None because supported sockets block indefinitely."""
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
        """Return the live registry handle, or -1 once the socket is closed."""
        if self._net_closed:
            return -1
        if self._sid is None:
            raise NotImplementedError("unconnected sockets have no selectable registry handle")
        return self._sid

    def detach(self):
        """Reject descriptor transfer outside the native net registry."""
        raise NotImplementedError("socket file descriptors are not supported")

    def shutdown(self, how):
        """Reject half-closes, which native net does not expose."""
        self._check_open()
        raise NotImplementedError("socket shutdown is not supported")

    def close(self):
        """Close the underlying endpoint exactly once."""
        if not self._net_closed:
            self._net_closed = True
            if self._sid is not None:
                sid = self._sid
                self._sid = None
                _net.close(sid)


SocketType = socket


def getdefaulttimeout():
    """Return the blocking default timeout."""
    return None


def setdefaulttimeout(timeout):
    """Accept only the supported blocking default."""
    if timeout is not None:
        raise NotImplementedError("socket timeouts are not supported")


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Describe a numeric IPv4 TCP endpoint without performing DNS lookup."""
    if family not in (AF_UNSPEC, AF_INET) or type != SOCK_STREAM or proto not in (0, IPPROTO_TCP):
        raise NotImplementedError("address lookup supports only IPv4 SOCK_STREAM TCP")
    if flags & ~(AI_PASSIVE | AI_NUMERICHOST | AI_NUMERICSERV):
        raise NotImplementedError("address lookup flags are not supported")
    if isinstance(port, str):
        if not port or not all("0" <= c <= "9" for c in port):
            raise NotImplementedError("service name lookup is not supported")
        port = int(port)
    address = _address((host, port))
    return [(AF_INET, SOCK_STREAM, IPPROTO_TCP, "", address)]


def gethostname():
    """Reject access to the host's network identity."""
    raise NotImplementedError("hostname queries are not supported")


def gethostbyname(hostname):
    """Return a numeric IPv4 address without resolving host names."""
    return _address((hostname, 0))[0]


def gethostbyname_ex(hostname):
    """Reject canonical-name and alias lookup."""
    raise NotImplementedError("hostname queries are not supported")


def gethostbyaddr(address):
    """Reject reverse DNS lookup."""
    raise NotImplementedError("reverse hostname queries are not supported")


def getnameinfo(address, flags):
    """Reject hostname and service lookup."""
    raise NotImplementedError("hostname and service queries are not supported")


def getservbyname(servicename, protocolname=None):
    """Reject host service database access."""
    raise NotImplementedError("service name lookup is not supported")


def getservbyport(port, protocolname=None):
    """Reject host service database access."""
    raise NotImplementedError("service name lookup is not supported")


def getprotobyname(protocolname):
    """Reject host protocol database access."""
    raise NotImplementedError("protocol name lookup is not supported")


def close(fd):
    """Reject operating-system socket descriptors."""
    raise NotImplementedError("socket file descriptors are not supported")


def dup(fd):
    """Reject duplicating operating-system socket descriptors."""
    raise NotImplementedError("socket file descriptors are not supported")
