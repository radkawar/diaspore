"""Generate the browser standard library and asset fingerprint."""

import hashlib
import json
from pathlib import Path

from diaspore.compiler import write_stdlib_overlay


def main() -> None:
    """Write the standard library overlay and a cache-safe asset manifest."""
    site = Path(__file__).resolve().parent
    write_stdlib_overlay(str(site / "stdlib.json"))
    assets = sorted(site.glob("js/*.js")) + [
        site / "style.css",
        site / "diaspore.wasm",
        site / "wasm_exec.js",
        site / "stdlib.json",
    ]
    digest = hashlib.sha256()
    for path in assets:
        digest.update(path.relative_to(site).as_posix().encode())
        digest.update(path.read_bytes())
    version = digest.hexdigest()[:16]
    manifest = {
        "version": version,
        "modules": [path.relative_to(site).as_posix() for path in assets if path.suffix == ".js"],
    }
    (site / "build.json").write_text(json.dumps(manifest) + "\n")


if __name__ == "__main__":
    main()
