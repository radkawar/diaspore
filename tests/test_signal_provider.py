"""Exercise sandbox-local signal state and unchanged upstream unittest hooks."""

import importlib.util
import signal
from pathlib import Path

import pytest

from .conftest import run_cpython, run_diaspore_py


@pytest.fixture
def provider():
    path = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/_signal.py"
    spec = importlib.util.spec_from_file_location("portable_signal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(function, *args):
    try:
        return function(*args)
    except Exception as error:
        return type(error).__name__, str(error)


def test_signal_constants_and_descriptions(provider):
    assert provider.valid_signals() == signal.valid_signals()
    for name in dir(provider):
        if name.startswith("SIG") or name == "NSIG":
            assert getattr(provider, name) == getattr(signal, name)
    for number in range(1, provider.NSIG):
        assert provider.strsignal(number) == signal.strsignal(number)
    for value in (0, -1, 65, 1.5, "2", 1 << 100):
        assert outcome(provider.getsignal, value) == outcome(signal.getsignal, value)
        assert outcome(provider.strsignal, value) == outcome(signal.strsignal, value)


def test_local_handlers_preserve_host_and_mutate_only_local_state(provider):
    host = signal.getsignal(signal.SIGUSR1)
    calls = []

    def handler(number, frame):
        calls.append((number, frame))
        provider.signal(number, provider.SIG_IGN)

    assert provider.signal(provider.SIGUSR1, handler) == provider.SIG_DFL
    assert provider.getsignal(provider.SIGUSR1) is handler
    assert provider.raise_signal(provider.SIGUSR1) is None
    assert calls == [(provider.SIGUSR1, None)]
    assert provider.raise_signal(provider.SIGUSR1) is None
    assert len(calls) == 1
    assert signal.getsignal(signal.SIGUSR1) is host
    with pytest.raises(KeyboardInterrupt):
        provider.raise_signal(provider.SIGINT)
    for number in (provider.SIGTERM, provider.SIGSTOP, provider.SIGKILL):
        with pytest.raises(OSError) as error:
            provider.raise_signal(number)
        assert error.value.errno == 38
    for number in (provider.SIGCHLD, provider.SIGURG, provider.SIGWINCH):
        assert provider.raise_signal(number) is None
    provider.signal(provider.SIGINT, provider.SIG_DFL)
    with pytest.raises(OSError) as error:
        provider.raise_signal(provider.SIGINT)
    assert error.value.errno == 38
    for name in (
        "alarm",
        "pause",
        "setitimer",
        "getitimer",
        "pthread_kill",
        "sigwait",
        "set_wakeup_fd",
        "siginterrupt",
        "pthread_sigmask",
    ):
        assert not hasattr(provider, name)


def test_handler_errors_and_main_thread_restriction(provider):
    import _signal
    import threading

    for number, handler in (
        (0, None),
        (9, None),
        (9, lambda *args: None),
        (32, 0),
        (2, None),
        (2, 2),
        (2, 0.0),
        (2, False),
    ):
        assert outcome(provider.signal, number, handler) == outcome(_signal.signal, number, handler)
    results = []

    def worker():
        results.append(outcome(provider.signal, provider.SIGINT, provider.SIG_IGN))
        results.append(outcome(_signal.signal, provider.SIGINT, _signal.SIG_IGN))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert results[0] == results[1]


SIGNAL_SOURCE = """
import signal
import unittest

print(signal.Signals.SIGINT.name, signal.Handlers.SIG_IGN.name)
print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)
print(signal.strsignal(signal.SIGINT), signal.SIGINT in signal.valid_signals())
seen = []
def handler(number, frame):
    seen.append(number)
old = signal.signal(signal.SIGUSR1, handler)
print(old == signal.SIG_DFL, signal.getsignal(signal.SIGUSR1) is handler)
print(signal.raise_signal(signal.SIGUSR1), seen)
print(signal.signal(signal.SIGUSR1, signal.SIG_IGN) is handler)
print(signal.raise_signal(signal.SIGUSR1), seen)
signal.signal(signal.SIGUSR1, old)
try:
    signal.raise_signal(signal.SIGINT)
except KeyboardInterrupt:
    print("interrupt")
for value in (0, -1, 65, 1.5, "2"):
    try:
        signal.getsignal(value)
    except Exception as error:
        print(type(error).__name__, str(error))
result = unittest.TestResult()
unittest.installHandler()
unittest.registerResult(result)
signal.raise_signal(signal.SIGINT)
print(result.shouldStop)
try:
    signal.raise_signal(signal.SIGINT)
except KeyboardInterrupt:
    print("second interrupt")
unittest.removeResult(result)
unittest.removeHandler()
print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)
"""


def test_signal_and_real_unittest_match_cpython(tmp_path):
    entry = tmp_path / "signal_checks.py"
    entry.write_text(SIGNAL_SOURCE)
    expected = run_cpython(entry)
    assert expected.code == 0, expected.err_line
    assert run_diaspore_py(entry) == expected
