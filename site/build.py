"""Generate the browser asset fingerprint and embedded stdlib identity."""

import hashlib
import json
import shutil
import tomllib
from pathlib import Path
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version


def _published_version(project_version):
    ceiling = Version(project_version)
    with urlopen("https://pypi.org/pypi/diaspore/json", timeout=30) as response:
        releases = json.load(response)["releases"]
    candidates = []
    for version, files in releases.items():
        try:
            parsed = Version(version)
        except InvalidVersion:
            continue
        if parsed <= ceiling and any(
            item.get("packagetype") == "bdist_wheel"
            and item.get("filename", "").endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))
            and not item.get("yanked", False)
            for item in files
        ):
            candidates.append((parsed, version))
    if not candidates:
        raise RuntimeError("No published diaspore wheel on PyPI at or below " + project_version)
    return max(candidates)[1]


def main() -> None:
    """Write a cache-safe asset manifest with the linked standard-library id."""
    site = Path(__file__).resolve().parent
    from diaspore.stdlib_store_codec import load_stdlib_store

    # Use the artifact embedded by the wasm build, not a newer source-tree snapshot.
    stdlib_id = load_stdlib_store(site.parent / "runtimes/go/interp/stdlib.dsps")["id"]
    with (site.parent / "pyproject.toml").open("rb") as project:
        package_version = _published_version(tomllib.load(project)["project"]["version"])
    notices = site / "cpython"
    notices.mkdir(exist_ok=True)
    for name in ("LICENSE", "UNICODE_LICENSE", "stdlib_manifest.json"):
        shutil.copyfile(site.parent / "src" / "diaspore" / "cpython" / name, notices / name)
    assets = sorted(site.glob("js/*.js")) + sorted(site.glob("examples/*.py")) + [
        site / "style.css",
        site / "diaspore.wasm",
        site / "wasm_exec.js",
    ]
    digest = hashlib.sha256()
    digest.update(package_version.encode())
    for path in assets:
        digest.update(path.relative_to(site).as_posix().encode())
        digest.update(path.read_bytes())
    version = digest.hexdigest()[:16]
    manifest = {
        "version": version,
        "stdlib": stdlib_id,
        "package_version": package_version,
        "modules": [path.relative_to(site).as_posix() for path in assets if path.suffix == ".js"],
    }
    (site / "build.json").write_text(json.dumps(manifest) + "\n")


if __name__ == "__main__":
    main()
