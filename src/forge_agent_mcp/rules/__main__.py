from __future__ import annotations

import sys
from pathlib import Path

from .cli import main

DEFAULT_CORPUS = Path("data") / "corpus"


def _run() -> int:
    argv = list(sys.argv[1:])
    if not any(arg == "--corpus" or arg.startswith("--corpus=") for arg in argv):
        argv.extend(["--corpus", str(DEFAULT_CORPUS)])
    return main(argv)


raise SystemExit(_run())
