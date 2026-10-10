#!/usr/bin/env python3
"""Vendor byte-identical CPython sources selected by stdlib_manifest.json.

Run with --prepare to regenerate only the manifest from the pinned archive.
Run without flags to materialize the manifest, or --check to check the installed
files without downloading or changing anything. A matching CPython checkout at
~/dev/minor/clones/cpython is preferred over the build/cpython source cache.
"""

import argparse
import ast
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "3.12.12"
TAG = "v" + VERSION
ARCHIVE = "Python-" + VERSION + ".tgz"
URL = "https://www.python.org/ftp/python/" + VERSION + "/" + ARCHIVE
SHA256 = "487c908ddf4097a1b9ba859f25fe46d22ccaabfb335880faac305ac62bffb79b"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/stdlib_manifest.json"
DESTINATION = ROOT / "src/diaspore/cpython"
OWN = ROOT / "src/diaspore/stdlib"
CACHE = ROOT / "build/cpython"
ROOTS = (
    "_py_abc", "_pydatetime", "_pydecimal", "_pyio", "ast", "asyncio", "base64",
    "collections", "contextlib", "copy", "dataclasses", "fractions", "functools",
    "gzip", "hashlib", "io", "json", "operator", "os", "pathlib", "queue", "random",
    "socket", "string", "threading", "types", "unittest", "weakref",
)
PRESERVED = ("_pyparse.py", "_pyunparse.py", "binascii.py", "gc.py", "itertools.py", "zlib.py")
OBSOLETE = ("asyncio.py", "os/__init__.py", "os/path.py", "unittest.py")
NATIVE = ("re",)


def digest(data):
    """Return the SHA-256 used for archive and source integrity."""
    return hashlib.sha256(data).hexdigest()


def archive_path():
    """Fetch or reuse the integrity-checked official release archive."""
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / ARCHIVE
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                with urllib.request.urlopen(URL) as response:
                    shutil.copyfileobj(response, temporary)
                temporary.close()
                if digest(temporary_path.read_bytes()) != SHA256:
                    raise ValueError("downloaded CPython archive has the wrong SHA-256")
                temporary_path.replace(archive)
            finally:
                temporary_path.unlink(missing_ok=True)
    if digest(archive.read_bytes()) != SHA256:
        raise ValueError("cached CPython archive has the wrong SHA-256: " + str(archive))
    return archive


def source_path(explicit, entries):
    """Select a matching checkout, explicit tree, or extracted release cache."""
    if explicit is not None:
        if not explicit.is_dir():
            raise ValueError("CPython source directory does not exist: " + str(explicit))
        return explicit
    checkout = Path.home() / "dev/minor/clones/cpython"
    if checkout.is_dir() and all(
        (checkout / entry["source"]).is_file()
        and digest((checkout / entry["source"]).read_bytes()) == entry["sha256"]
        for entry in entries
    ):
        return checkout
    source = CACHE / ("Python-" + VERSION)
    if not source.is_dir():
        with tarfile.open(archive_path(), "r:gz") as archive:
            archive.extractall(CACHE, filter="data")
    return source


def import_names(tree, module, is_package, available):
    """Collect absolute import names without executing upstream code."""
    package = module if is_package else module.rpartition(".")[0]
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1])
                base = ".".join(part for part in (base, node.module) if part)
            else:
                base = node.module or ""
            if base:
                result.add(base)
            for alias in node.names:
                candidate = ".".join(part for part in (base, alias.name) if part)
                if candidate in available:
                    result.add(candidate)
    return result


def prepare():
    """Generate the deterministic source inventory from the pinned archive."""
    sources = {}
    prefix = "Python-" + VERSION + "/"
    with tarfile.open(archive_path(), "r:gz") as archive:
        license_data = archive.extractfile(prefix + "LICENSE").read()
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith(prefix + "Lib/"):
                continue
            relative = member.name[len(prefix + "Lib/"):]
            parts = relative.split("/")
            if not relative.endswith(".py") or any(part in ("test", "tests") for part in parts):
                continue
            if parts[0] == "re" or relative in PRESERVED:
                continue
            module = relative[:-3].replace("/", ".")
            if module.endswith(".__init__"):
                module = module[:-9]
            sources[module] = (relative, archive.extractfile(member).read())
    selected = set()
    pending = set(ROOTS)
    imports = {}
    while pending:
        module = min(pending)
        pending.remove(module)
        if module in selected or module not in sources:
            continue
        selected.add(module)
        relative, data = sources[module]
        is_package = relative.endswith("/__init__.py")
        imports[module] = import_names(ast.parse(data), module, is_package, sources)
        dependencies = set(imports[module])
        # Package APIs may load submodules dynamically, including codec lookup.
        if is_package:
            dependencies.update(name for name in sources if name.startswith(module + "."))
        parts = module.split(".")
        dependencies.update(".".join(parts[:index]) for index in range(1, len(parts)))
        pending.update(dependencies - selected)
    modules = []
    for module in sorted(selected):
        relative, data = sources[module]
        modules.append({
            "name": module,
            "path": relative,
            "source": "Lib/" + relative,
            "sha256": digest(data),
            "imports": sorted(imports[module]),
        })
    manifest = {
        "schema": 1,
        "upstream": {"project": "CPython", "version": VERSION, "tag": TAG,
                     "url": URL, "sha256": SHA256},
        "selection": {
            "roots": list(ROOTS),
            "closure": ("All static imports and complete imported packages, "
                        "excluding test directories."),
            "native_modules": list(NATIVE),
            "preserved_paths": list(PRESERVED),
            "obsolete_paths": list(OBSOLETE),
        },
        "modules": modules,
        "external_imports": sorted(set().union(*imports.values()) - selected),
        "license": {"path": "LICENSE", "source": "LICENSE", "sha256": digest(license_data)},
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Prepared " + str(len(modules)) + " modules in " + str(MANIFEST.relative_to(ROOT)))


def load_manifest():
    """Load the inventory and require the pinned release and selection policy."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {"project": "CPython", "version": VERSION, "tag": TAG,
                "url": URL, "sha256": SHA256}
    if manifest["schema"] != 1 or manifest["upstream"] != expected:
        raise ValueError("manifest does not match the pinned CPython release")
    selection = manifest["selection"]
    if (selection["roots"] != list(ROOTS)
            or selection["native_modules"] != list(NATIVE)
            or selection["preserved_paths"] != list(PRESERVED)
            or selection["obsolete_paths"] != list(OBSOLETE)):
        raise ValueError("manifest selection differs from the vendoring policy; run --prepare")
    return manifest


def materialize(manifest, explicit):
    """Copy upstream bytes, preserving local C-layer and compiler modules."""
    entries = manifest["modules"] + [manifest["license"]]
    source = source_path(explicit, entries)
    # Check the entire reused tree before replacing any installed source.
    contents = []
    for entry in entries:
        path = source / entry["source"]
        data = path.read_bytes()
        if digest(data) != entry["sha256"]:
            raise ValueError("CPython source differs from " + TAG + ": " + str(path))
        contents.append((entry["path"], data))
    for relative in OBSOLETE:
        (OWN / relative).unlink(missing_ok=True)
    for relative, data in contents:
        path = DESTINATION / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        (OWN / relative).unlink(missing_ok=True)
    legacy_license = OWN / "UNICODE_LICENSE"
    if legacy_license.exists():
        legacy_license.replace(DESTINATION / "UNICODE_LICENSE")
    shutil.copyfile(MANIFEST, DESTINATION / MANIFEST.name)
    for directory in sorted(OWN.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    print("Vendored " + str(len(manifest["modules"])) + " CPython " + VERSION + " modules")


def check(manifest):
    """Check installed upstream files and stale shims without network access."""
    errors = []
    entries = manifest["modules"] + [manifest["license"]]
    for entry in entries:
        path = DESTINATION / entry["path"]
        if not path.is_file():
            errors.append("missing: " + entry["path"])
        elif digest(path.read_bytes()) != entry["sha256"]:
            errors.append("modified: " + entry["path"])
        if (OWN / entry["path"]).exists():
            errors.append("upstream file in overlay root: " + entry["path"])
    if not (DESTINATION / "UNICODE_LICENSE").is_file():
        errors.append("missing: UNICODE_LICENSE")
    shipped_manifest = DESTINATION / MANIFEST.name
    if not shipped_manifest.is_file() or shipped_manifest.read_bytes() != MANIFEST.read_bytes():
        errors.append("shipped manifest differs from authoritative manifest")
    expected = {entry["path"] for entry in entries}
    for path in DESTINATION.rglob("*.py"):
        if path.relative_to(DESTINATION).as_posix() not in expected:
            errors.append("unlisted upstream source: " + str(path))
    for relative in OBSOLETE:
        if (OWN / relative).exists():
            errors.append("obsolete shim: " + relative)
    if errors:
        raise ValueError("vendored stdlib differs from manifest:\n" + "\n".join(errors))
    print("CPython " + VERSION + " vendored sources match the manifest")


def main():
    """Run manifest generation, byte-for-byte vendoring, or an offline check."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="write only the module manifest")
    mode.add_argument("--check", action="store_true",
                      help="check vendored bytes without writes/network")
    parser.add_argument("--source", type=Path,
                        help="reuse a CPython source checkout or extracted tree")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare()
        elif args.check:
            check(load_manifest())
        else:
            materialize(load_manifest(), args.source)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
