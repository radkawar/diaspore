"""Generate the browser standard library and asset fingerprint."""

import hashlib
import json
from pathlib import Path


def main() -> None:
    """Write the standard library overlay and a cache-safe asset manifest."""
    site = Path(__file__).resolve().parent
    stdlib = site.parent / "src" / "diaspore" / "stdlib"
    files = {
        "/stdlib/" + path.relative_to(stdlib).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(stdlib.rglob("*.py"))
    }
    (site / "stdlib.json").write_text(
        json.dumps(files, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
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
