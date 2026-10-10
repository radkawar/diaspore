"""Provide isolated POSIX signal handlers without changing host signal state.

Handlers run synchronously with a None frame when raise_signal is called.
Process lifecycle defaults raise ENOSYS rather than terminating or suspending
an embedding host. Host timers, signal masks, waits, and wakeup FDs are absent.
Signal numbers and English descriptions follow CPython's Linux platform.
"""

import _thread
from operator import index as _index

SIG_DFL = 0
SIG_IGN = 1
NSIG = 65
SIGHUP = 1
SIGINT = 2
SIGQUIT = 3
SIGILL = 4
SIGTRAP = 5
SIGABRT = 6
SIGIOT = 6
SIGBUS = 7
SIGFPE = 8
SIGKILL = 9
SIGUSR1 = 10
SIGSEGV = 11
SIGUSR2 = 12
SIGPIPE = 13
SIGALRM = 14
SIGTERM = 15
SIGSTKFLT = 16
SIGCHLD = 17
SIGCLD = 17
SIGCONT = 18
SIGSTOP = 19
SIGTSTP = 20
SIGTTIN = 21
SIGTTOU = 22
SIGURG = 23
SIGXCPU = 24
SIGXFSZ = 25
SIGVTALRM = 26
SIGPROF = 27
SIGWINCH = 28
SIGIO = 29
SIGPOLL = 29
SIGPWR = 30
SIGSYS = 31
SIGRTMIN = 34
SIGRTMAX = 64

_descriptions = (
    None,
    "Hangup",
    "Interrupt",
    "Quit",
    "Illegal instruction",
    "Trace/breakpoint trap",
    "Aborted",
    "Bus error",
    "Floating point exception",
    "Killed",
    "User defined signal 1",
    "Segmentation fault",
    "User defined signal 2",
    "Broken pipe",
    "Alarm clock",
    "Terminated",
    "Stack fault",
    "Child exited",
    "Continued",
    "Stopped (signal)",
    "Stopped",
    "Stopped (tty input)",
    "Stopped (tty output)",
    "Urgent I/O condition",
    "CPU time limit exceeded",
    "File size limit exceeded",
    "Virtual timer expired",
    "Profiling timer expired",
    "Window changed",
    "I/O possible",
    "Power failure",
    "Bad system call",
    None,
    None,
)
_initial_thread = _thread.get_ident()


def _integer(value):
    value = _index(value)
    if not -(1 << 31) <= value < (1 << 31):
        raise OverflowError("Python int too large to convert to C int")
    return value


def _number(value):
    value = _integer(value)
    if value < 1 or value >= NSIG:
        raise ValueError("signal number out of range")
    return value


def _check_main_thread():
    main_ident = _initial_thread
    tasks_factory = getattr(_thread, "_tasks", None)
    if tasks_factory is not None:
        backend = tasks_factory()
        if backend is not None:
            identity = getattr(backend, "main_ident", None)
            if identity is not None:
                main_ident = identity()
    if _thread.get_ident() != main_ident:
        raise ValueError("signal only works in main thread of the main interpreter")


def default_int_handler(*args):
    """Raise KeyboardInterrupt for the interpreter's default SIGINT handler."""
    raise KeyboardInterrupt


_handlers = {number: SIG_DFL for number in range(1, NSIG)}
_handlers[SIGINT] = default_int_handler
_handlers[SIGPIPE] = SIG_IGN
_handlers[SIGXFSZ] = SIG_IGN
_handlers[32] = None
_handlers[33] = None


def signal(signalnum, handler, /):
    """Set a sandbox handler and return its previous value, on the main thread."""
    signalnum = _integer(signalnum)
    _check_main_thread()
    signalnum = _number(signalnum)
    if not callable(handler) and not (type(handler) is int and handler in (SIG_DFL, SIG_IGN)):
        raise TypeError(
            "signal handler must be signal.SIG_IGN, signal.SIG_DFL, or a callable object"
        )
    if signalnum in (SIGKILL, SIGSTOP, 32, 33):
        raise OSError(22, "Invalid argument")
    previous = _handlers[signalnum]
    _handlers[signalnum] = handler
    return previous


def getsignal(signalnum, /):
    """Return the sandbox's current handler for a signal number."""
    return _handlers[_number(signalnum)]


def raise_signal(signalnum, /):
    """Synchronously dispatch a local handler without delivering a host signal."""
    signalnum = _integer(signalnum)
    if signalnum == 0:
        return None
    if signalnum < 0 or signalnum >= NSIG or signalnum in (32, 33):
        raise OSError(22, "Invalid argument")
    handler = _handlers[signalnum]
    if callable(handler):
        handler(signalnum, None)
    elif handler == SIG_IGN or signalnum in (SIGCHLD, SIGURG, SIGWINCH):
        return None
    else:
        raise OSError(38, "diaspore cannot deliver default process signals")
    return None


def valid_signals():
    """Return a fresh set of supported POSIX signal numbers."""
    return set(range(1, 32)) | set(range(SIGRTMIN, SIGRTMAX + 1))


def strsignal(signalnum, /):
    """Return the signal's English description, or None for reserved numbers."""
    signalnum = _number(signalnum)
    if signalnum >= SIGRTMIN:
        return "Real-time signal " + str(signalnum - SIGRTMIN)
    return _descriptions[signalnum]
