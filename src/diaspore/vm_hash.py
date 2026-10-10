"""Accelerate portable hash providers without exposing host hash objects."""

import hashlib

_ALGORITHMS = frozenset({"md5", "sha1", "sha224", "sha256", "sha384", "sha512"})


def make_hash_module(interp, module_type, object_type):
    """Build private incremental engines for the portable hash provider.

    Args:
        interp: Interpreter owning argument adaptation and fuel accounting.
        module_type: Constructor for sandbox native modules.
        object_type: Constructor for opaque sandbox native objects.

    Returns:
        A module exposing the supported hash engine constructor.
    """
    def wrap(state):
        def update(data, /):
            data = interp._raw(data)
            if isinstance(data, str):
                raise TypeError("Strings must be encoded before hashing")
            try:
                view = memoryview(data)
            except TypeError:
                raise TypeError("object supporting the buffer API required") from None
            with view:
                if not view.c_contiguous:
                    raise BufferError("memoryview: underlying buffer is not C-contiguous")
                interp.fuel_n(view.nbytes // state.block_size + 1)
                state.update(view)

        def digest():
            interp.fuel_n(1)
            return state.digest()

        def hexdigest():
            interp.fuel_n(1)
            return state.hexdigest()

        def copy():
            interp.fuel_n(1)
            return wrap(state.copy())

        return object_type("_diaspore_hash.HASH", {
            "update": update, "digest": digest, "hexdigest": hexdigest, "copy": copy,
        })

    def new(name, /):
        name = interp._raw(name)
        if not isinstance(name, str):
            raise TypeError("hash name must be a string")
        if name not in _ALGORITHMS:
            raise ValueError("unsupported hash type " + name)
        return wrap(hashlib.new(name, usedforsecurity=False))

    return module_type("_diaspore_hash", {"new": new})
