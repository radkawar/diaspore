"""Lint maintained Python code without rewriting pinned upstream sources."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Run the project's Ruff rules on maintained sources."""
    command = [sys.executable, "-m", "ruff", "check", ".", *sys.argv[1:]]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
