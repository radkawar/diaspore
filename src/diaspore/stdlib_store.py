"""Load the installed content-addressed standard library without compiling it.

Host consumers read the compact DSPS02 index and decode individual DSPB modules
on demand. Sandboxed copies use the private runtime bridge instead of host files.
Source compilation is an explicit generation operation; distribution metadata
does not participate in the canonical content identifier.
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


def _store_path():
    import os

    return os.path.join(os.path.dirname(__file__), "stdlib.dsps")


def load_stdlib_store():
    """Return the installed store with index-only startup and lazy module IR.

    The distribution must include stdlib.dsps. Missing or corrupt artifacts are
    errors, never a request to compile sources or load a whole-store JSON cache.
    """
    global _store
    if _store is None:
        from .stdlib_store_codec import load_stdlib_store as load_compact_store

        _store = load_compact_store(_store_path())
    return _store


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
    """Return isolated module IR, or None when the store has no such module."""
    if _bridge is not None:
        return _bridge.get_module(name)
    modules = load_stdlib_store()["modules"]
    if isinstance(modules, dict):
        import copy

        return copy.deepcopy(modules.get(name))
    return modules.get(name)


def write_stdlib_store(path, *, compress=True):
    """Compile current sources and write a fresh compact runtime store.

    Args:
        path: Destination DSPS02 artifact.
        compress: Use preset-dictionary raw deflate; false writes raw DSPB payloads.
    """
    from .stdlib_store_codec import encode_stdlib_store

    data = encode_stdlib_store(compile_stdlib_store(), compress=compress)
    with open(path, "wb") as output:
        output.write(data)
