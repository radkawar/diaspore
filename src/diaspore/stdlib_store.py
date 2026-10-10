"""Compile and cache the installed, content-addressed standard-library store.

Sandboxed copies use the private runtime bridge instead of reading host files.
Host processes retain one immutable installed-source snapshot; the persistent
cache key includes all source bytes and the compiler implementation.
Optional licenses and manifest metadata preserve distribution notices; only the
diaspore_stdlib version and modules mapping participate in the store identifier.
"""

try:
    import _diaspore_stdlib as _bridge
except ImportError:
    _bridge = None

_store = None


def _canonical(value):
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8", "backslashreplace")


def _sources():
    import os

    from .compiler import STDLIB_DIRS

    sources = {}
    for root in STDLIB_DIRS:
        for directory, directories, filenames in os.walk(root):
            directories[:] = sorted(name for name in directories if name != "__pycache__")
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                name = relative[:-3].replace("/", ".")
                package = name.endswith(".__init__")
                if package:
                    name = name[:-9]
                if name not in sources:
                    with open(path, "rb") as source:
                        sources[name] = (relative, package, source.read())
    return sources


def _distribution_metadata():
    import json
    import os

    from .compiler import STDLIB_DIRS

    files = {}
    for root in STDLIB_DIRS:
        for name in ("LICENSE", "UNICODE_LICENSE", "stdlib_manifest.json"):
            path = os.path.join(root, name)
            if name not in files and os.path.isfile(path):
                with open(path, encoding="utf-8") as source:
                    files[name] = source.read()
    metadata = {}
    licenses = {}
    for name, key in (("LICENSE", "cpython"), ("UNICODE_LICENSE", "unicode")):
        if name in files:
            licenses[key] = files[name]
    if licenses:
        metadata["licenses"] = licenses
    if "stdlib_manifest.json" in files:
        metadata["manifest"] = json.loads(files["stdlib_manifest.json"])
    return metadata


def _compile(sources, metadata):
    import hashlib

    from .compiler import ModuleCompiler, compact_bundle

    modules = {}
    for name in sorted(sources):
        relative, package, data = sources[name]
        source = data.decode("utf-8")
        context = name if package else name.rpartition(".")[0]
        compiler = ModuleCompiler(name, "<stdlib>/" + relative, source,
                                  package_ctx=context, is_module=lambda value: value in sources)
        module = compiler.compile()
        module["src"] = source.splitlines()
        modules[name] = module
    compact_bundle({"modules": modules})
    payload = {"diaspore_stdlib": 1, "modules": modules}
    identifier = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    return {"diaspore_stdlib": 1, "id": identifier, "modules": modules, **metadata}


def compile_stdlib_store():
    """Compile every shipped source module independently into a new store."""
    return _compile(_sources(), _distribution_metadata())


def _validate_store(store):
    import hashlib

    from .validator import validate_bundle

    if not isinstance(store, dict) or store.get("diaspore_stdlib") != 1:
        raise ValueError("cached standard-library store has an unsupported format")
    modules = store.get("modules")
    if not isinstance(modules, dict):
        raise ValueError("cached standard-library store modules must be an object")
    if modules:
        validate_bundle({"diaspore": 1, "main": next(iter(modules)), "modules": modules})
    for name, module in modules.items():
        if module["name"] != name:
            raise ValueError("cached standard-library module name differs from its key: " + name)
    payload = {"diaspore_stdlib": 1, "modules": modules}
    actual = "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()
    if store.get("id") != actual:
        raise ValueError("cached standard-library content id mismatch: declared "
                         + str(store.get("id")) + ", actual " + actual)


def load_stdlib_store():
    """Return the host's cached immutable compiled standard-library store.

    The source and compiler digest selects the on-disk cache. Runtime consumers
    must not mutate returned IR; each machine maintains its own module globals.
    """
    global _store
    if _store is not None:
        return _store
    import gzip
    import hashlib
    import json
    import os
    import tempfile

    from . import compiler

    sources = _sources()
    metadata = _distribution_metadata()
    fingerprint = hashlib.sha256()
    for path in (compiler.__file__, __file__):
        with open(path, "rb") as implementation:
            data = implementation.read()
        fingerprint.update(len(data).to_bytes(8, "big"))
        fingerprint.update(data)
    for name in sorted(sources):
        relative, package, data = sources[name]
        fingerprint.update(_canonical([name, relative, package, len(data)]))
        fingerprint.update(data)
    fingerprint.update(_canonical(metadata))
    cache_root = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    directory = os.path.join(cache_root, "diaspore", "stdlib")
    path = os.path.join(directory, fingerprint.hexdigest() + ".json.gz")
    if os.path.isfile(path):
        with gzip.open(path, "rt", encoding="utf-8") as cached:
            store = json.load(cached)
        _validate_store(store)
        _store = store
        return _store
    store = _compile(sources, metadata)
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = output.name
            output.write(gzip.compress(_canonical(store), mtime=0))
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    _store = store
    return store


def get_stdlib_id():
    """Return the exact content identifier of the installed compiled library."""
    if _bridge is not None:
        return _bridge.get_id()
    return load_stdlib_store()["id"]


def stdlib_module_names():
    """Return the installed module names without exposing mutable runtime state."""
    if _bridge is not None:
        return _bridge.module_names()
    return sorted(load_stdlib_store()["modules"])


def get_stdlib_module(name):
    """Return immutable module IR, or None when the store has no such module."""
    if _bridge is not None:
        return _bridge.get_module(name)
    return load_stdlib_store()["modules"].get(name)


def write_stdlib_store(path):
    """Write a runtime store artifact, gzip-compressed for a .gz destination."""
    import gzip

    data = _canonical(load_stdlib_store()) + b"\n"
    if path.endswith(".gz"):
        data = gzip.compress(data, mtime=0)
    with open(path, "wb") as output:
        output.write(data)
