"""Diaspore: compile a Python subset to portable JSON; execute it in sandboxed VMs.

The runtime has no third-party dependencies. For embedding:

    from diaspore import compile_bundle, validate_bundle, Machine

    bundle = compile_bundle("plugin.py")
    m = Machine(bundle, bindings={"host": {"greet": lambda n: f"hi {n}"}},
                max_steps=1_000_000)
    m.run()

Use Machine.run_durable() and Machine.resume() with durable=True for durable
execution. Schema generation loads Pydantic lazily from diaspore.ir; install
it with `uv sync --group dev`.
"""

from .compiler import DiasporeCompileError, compile_bundle, dumps
from .encoding import load_bundle_bytes, load_bundle_file, write_bundle_file
from .validator import BundleValidationError, validate_bundle
from .vm import (
    Completed,
    DiasporeError,
    DiasporeException,
    Machine,
    Suspended,
    bundle_digest,
)

__version__ = "0.3.0"

__all__ = [
    "BundleValidationError",
    "Completed",
    "DiasporeCompileError",
    "DiasporeError",
    "DiasporeException",
    "Machine",
    "Suspended",
    "bundle_digest",
    "bundle_schema",
    "compile_bundle",
    "dumps",
    "load_bundle_bytes",
    "load_bundle_file",
    "validate_bundle",
    "write_bundle_file",
    "__version__",
]


def __getattr__(name: str):
    """Load optional schema exports on demand.

    Args:
        name: Requested module attribute.

    Returns:
        The schema function or bundle model.

    Raises:
        AttributeError: The requested attribute is not an optional export.
    """
    if name == "bundle_schema":
        from .ir import bundle_schema
        return bundle_schema
    if name == "Bundle":
        from .ir import Bundle
        return Bundle
    raise AttributeError(f"module 'diaspore' has no attribute {name!r}")
