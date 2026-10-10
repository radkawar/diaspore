"""Keep native thread identity reuse from retaining exited ContextVar state."""

import importlib.util
import io
from pathlib import Path

import pytest

from diaspore import Machine
from diaspore.compiler import compile_bundle


def test_context_cleanup_preserves_retained_contexts():
    path = Path(__file__).resolve().parents[1] / "src/diaspore/stdlib/_contextvars.py"
    spec = importlib.util.spec_from_file_location("portable_contextvars", path)
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    variable = provider.ContextVar("value", default="default")
    variable.set("retained")
    saved = provider.copy_context()
    ident = provider.get_ident()
    provider._clear_thread_context(ident)
    provider._clear_thread_context(ident)
    assert ident not in provider._contexts
    assert variable.get() == "default"
    assert saved[variable] == "retained"
    assert saved.run(variable.get) == "retained"
    assert variable.get() == "default"


@pytest.mark.parametrize("backend", ["threading", "tasks"])
def test_thread_exit_clears_context_before_join_and_id_reuse(tmp_path, backend):
    path = tmp_path / "thread_context_lifecycle.py"
    path.write_text("backend = " + repr(backend) + "\n" + """
import _contextvars
import contextvars
import threading
import tasks

value = contextvars.ContextVar("value", default="default")
value.set("main")
saved = []
observations = []
identifiers = []

def worker():
    identifiers.append(_contextvars.get_ident())
    observations.append(value.get())
    context = contextvars.Context()
    def entered():
        assert value.get() == "default"
        value.set("entered")
        raise ValueError("unwind context")
    try:
        context.run(entered)
    except ValueError:
        pass
    observations.append(value.get())
    value.set("worker")
    saved.append(contextvars.copy_context())

for _ in range(6):
    if backend == "threading":
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert thread.ident not in _contextvars._contexts
    else:
        tasks.spawn(worker).join()
    assert identifiers[-1] not in _contextvars._contexts
    assert value.get() == "main"

assert observations == ["default"] * 12
assert len(saved) == 6
assert all(context[value] == "worker" for context in saved)
assert saved[0].run(value.get) == "worker"
assert value.get() == "main"
print("contexts released")
""")
    stdout, stderr = io.StringIO(), io.StringIO()
    machine = Machine(compile_bundle(str(path)), caps={"spawn"}, stdout=stdout, stderr=stderr)
    try:
        code = machine.run()
    finally:
        machine.close()
    assert (code, stdout.getvalue(), stderr.getvalue()) == (0, "contexts released\n", "")
