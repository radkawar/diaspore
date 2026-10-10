"""Provide import control for bundled sources and sandbox-native modules.

Native modules initialize when created. The sandbox has no CPython frozen
bytecode table or native extension-file loader; their discovery inventories
are empty. CPython bytecode mutation and unmarshalling remain unsupported.
"""

# Retain bootstrap providers in self-contained bundles as well as linked stores.
import _warnings as _warnings
import _weakref as _weakref
import sys
from _thread import RLock
from operator import index as _index

_import_lock = RLock()
_lock_depth = 0
_extensions_check = 0
check_hash_based_pycs = "default"
_MASK = (1 << 64) - 1
_BOOTSTRAP_BUILTINS = ("_thread", "_warnings", "_weakref")


def _name(name):
    if not isinstance(name, str):
        raise TypeError("name must be str, not " + type(name).__name__)
    return name


def acquire_lock():
    """Acquire the recursive, logical-thread-owned import lock."""
    global _lock_depth
    _import_lock.acquire()
    _lock_depth += 1


def release_lock():
    """Release one import-lock level, requiring ownership by this thread."""
    global _lock_depth
    if not _import_lock._is_owned():
        raise RuntimeError("not holding the import lock")
    _lock_depth -= 1
    _import_lock.release()


def lock_held():
    """Return whether any logical thread currently holds the import lock."""
    return _lock_depth != 0


def is_builtin(name, /):
    """Return a native or portable builtin-provider flag, or zero if absent."""
    _name(name)
    if name not in sys.builtin_module_names and name not in _BOOTSTRAP_BUILTINS:
        return 0
    return -1 if name in ("sys", "builtins") else 1


def create_builtin(spec, /):
    """Create and initialize a sandbox builtin provider named by a module spec."""
    name = _name(spec.name)
    if not is_builtin(name):
        return None
    return __import__(name, fromlist=("*",))


def exec_builtin(mod, /):
    """Return success because sandbox-native creation already initializes modules."""
    return 0


def extension_suffixes():
    """Return no suffixes because native extension files cannot be loaded."""
    return []


def create_dynamic(spec, file=None, /):
    """Reject native extension-file loading without granting host authority."""
    name = _name(spec.name)
    raise ImportError("native extension loading is unavailable: " + name, name=name)


def exec_dynamic(mod, /):
    """Return success for objects without pending native extension initialization."""
    return 0


def is_frozen(name, /):
    """Return False because bundled source modules are not frozen bytecode."""
    _name(name)
    return False


def find_frozen(name, /, *, withdata=False):
    """Return no frozen-table entry for the requested module."""
    _name(name)
    return None


def is_frozen_package(name, /):
    """Raise ImportError because the sandbox has no frozen module table."""
    _name(name)
    raise ImportError("No such frozen object named " + repr(name), name=name)


def get_frozen_object(name, data=None, /):
    """Reject absent frozen objects and unsupported CPython marshalled bytecode."""
    _name(name)
    if data is not None:
        raise NotImplementedError("CPython marshalled bytecode is unavailable")
    raise ImportError("No such frozen object named " + repr(name), name=name)


def _frozen_module_names():
    return []


def _fix_co_filename(code, path, /):
    raise NotImplementedError("CPython code filename mutation is unavailable")


def _override_multi_interp_extensions_check(override, /):
    global _extensions_check
    override = _index(override)
    if override < -2147483648 or override > 2147483647:
        raise OverflowError("Python int too large to convert to C int")
    previous = _extensions_check
    _extensions_check = override
    return previous


def _rotate(value, shift):
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _round(state):
    v0, v1, v2, v3 = state
    v0 = (v0 + v1) & _MASK
    v1 = _rotate(v1, 13) ^ v0
    v0 = _rotate(v0, 32)
    v2 = (v2 + v3) & _MASK
    v3 = _rotate(v3, 16) ^ v2
    v0 = (v0 + v3) & _MASK
    v3 = _rotate(v3, 21) ^ v0
    v2 = (v2 + v1) & _MASK
    v1 = _rotate(v1, 17) ^ v2
    v2 = _rotate(v2, 32)
    state[0], state[1], state[2], state[3] = v0, v1, v2, v3


def source_hash(key, source, /):
    """Return CPython's eight-byte, keyed SipHash-1-3 source digest.

    Args:
        key: Signed 64-bit integer identifying the bytecode format.
        source: Contiguous bytes-like source data.

    Returns:
        The little-endian 64-bit source hash.
    """
    key = _index(key)
    if not -(1 << 63) <= key < (1 << 63):
        raise OverflowError("Python int too large to convert to C long")
    key &= _MASK
    data = memoryview(source)
    if not data.c_contiguous:
        raise BufferError("underlying buffer is not C-contiguous")
    data = data.cast("B")
    state = [
        0x736F6D6570736575 ^ key,
        0x646F72616E646F6D,
        0x6C7967656E657261 ^ key,
        0x7465646279746573,
    ]
    limit = len(data) - len(data) % 8
    for start in range(0, limit, 8):
        word = 0
        for offset in range(8):
            word |= data[start + offset] << (offset * 8)
        state[3] ^= word
        _round(state)
        state[0] ^= word
    word = (len(data) & 255) << 56
    for offset in range(len(data) - limit):
        word |= data[limit + offset] << (offset * 8)
    state[3] ^= word
    _round(state)
    state[0] ^= word
    state[2] ^= 255
    for _ in range(3):
        _round(state)
    result = state[0] ^ state[1] ^ state[2] ^ state[3]
    return bytes((result >> (offset * 8)) & 255 for offset in range(8))
