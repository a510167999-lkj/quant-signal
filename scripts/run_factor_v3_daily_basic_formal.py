"""Fail-closed entry point for the Factor V3 formal dispatcher.

The formal runner is not a normal CLI.  It may run only after an external
trusted bootstrap has attested the fixed interpreter, reviewed source ledger,
and native TCB handoff.  This small entry point deliberately does not import
the collector or resolve credentials; without that attestation it exits
without creating a run root.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
import sys


_PYTHON_EXECUTABLE = Path(
    r"E:\AI workspace\quant-signal-lkj\.venv\Scripts\python.exe"
)


def main(argv: list[str] | None = None) -> int:
    del argv
    if (
        sys.flags.isolated != 1
        or not sys.dont_write_bytecode
        or sys.pycache_prefix is not None
        or Path(sys.executable) != _PYTHON_EXECUTABLE
    ):
        print("formal bootstrap requires fixed Python -I -B", file=sys.stderr)
        return 2
    print(
        "formal bootstrap claim unavailable; external trusted bootstrap context required",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
