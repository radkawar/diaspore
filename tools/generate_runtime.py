"""Generate paired compiler and standard-library artifacts from one store snapshot."""

import fcntl
import gzip
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _generate() -> None:
    from diaspore.compiler import compile_bundle, dumps
    from diaspore.stdlib_store import _canonical, load_stdlib_store

    os.chdir(ROOT)
    store = load_stdlib_store()
    bundle = compile_bundle(str(ROOT / "selfhost" / "dspc.py"))
    if bundle.get("stdlib") != store["id"]:
        raise ValueError("generated compiler and standard-library snapshot ids differ")
    source = dumps(bundle).encode("utf-8") + b"\n"
    compressed_source = gzip.compress(source, mtime=0)
    library = _canonical(store) + b"\n"
    compressed_library = gzip.compress(library, mtime=0)
    for relative, data in (
        ("runtimes/go/interp/stdlib.json.gz", compressed_library),
        ("runtimes/ts/stdlib.json.gz", compressed_library),
        ("runtimes/lua/stdlib.json", library),
        ("runtimes/go/dspc/dspc.dsp.json.gz", compressed_source),
        ("runtimes/ts/dspc.dsp.json.gz", compressed_source),
        ("runtimes/lua/dspc.dsp.json", source),
    ):
        _write(ROOT / relative, data)
    print("Generated linked runtimes: " + store["id"] + "; "
          + str(len(store["modules"])) + " standard-library modules; "
          + str(len(compressed_source)) + " compiler bytes")


def main() -> None:
    """Publish one coherent snapshot even when independent make processes overlap."""
    directory = ROOT / "build"
    directory.mkdir(exist_ok=True)
    with (directory / "runtime-generation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _generate()


if __name__ == "__main__":
    main()
