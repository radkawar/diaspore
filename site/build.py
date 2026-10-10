"""Generate the browser asset fingerprint and embedded stdlib identity."""

import gzip
import hashlib
import json
import shutil
from pathlib import Path


def main() -> None:
    """Write a cache-safe asset manifest with the linked standard-library id."""
    site = Path(__file__).resolve().parent
    # Use the artifact embedded by the wasm build, not a newer source-tree snapshot.
    with gzip.open(site.parent / "runtimes/go/interp/stdlib.json.gz", "rt",
                   encoding="utf-8") as compiled:
        stdlib_id = json.load(compiled)["id"]
    notices = site / "cpython"
    notices.mkdir(exist_ok=True)
    for name in ("LICENSE", "UNICODE_LICENSE", "stdlib_manifest.json"):
        shutil.copyfile(site.parent / "src" / "diaspore" / "cpython" / name, notices / name)
    assets = sorted(site.glob("js/*.js")) + [
        site / "style.css",
        site / "diaspore.wasm",
        site / "wasm_exec.js",
    ]
    digest = hashlib.sha256()
    for path in assets:
        digest.update(path.relative_to(site).as_posix().encode())
        digest.update(path.read_bytes())
    version = digest.hexdigest()[:16]
    manifest = {
        "version": version,
        "stdlib": stdlib_id,
        "modules": [path.relative_to(site).as_posix() for path in assets if path.suffix == ".js"],
    }
    (site / "build.json").write_text(json.dumps(manifest) + "\n")


if __name__ == "__main__":
    main()
