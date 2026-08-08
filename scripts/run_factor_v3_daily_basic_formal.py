"""Fail-closed entry point for the Factor V3 formal dispatcher.

The formal runner is not a normal CLI.  It may run only after an external
trusted bootstrap has attested the fixed interpreter, reviewed source ledger,
and native TCB handoff.  This small entry point deliberately does not import
the collector or resolve credentials; without that attestation it exits
without creating a run root.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys


_PYTHON_EXECUTABLE = Path(
    r"E:\AI workspace\quant-signal-lkj\.venv\Scripts\python.exe"
)
_WORKTREE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v3-daily-basic-formal-run-v4"
)
_BUILDER_MODULE_NAME = "scripts.build_factor_v3_daily_basic_formal_run_spec"
_BUILDER_RELATIVE_PATH = "scripts/build_factor_v3_daily_basic_formal_run_spec.py"
_LEDGER_ENTRY_FIELDS = frozenset(
    {
        "absolute_path",
        "byte_count",
        "is_package",
        "loader_identity",
        "module_name",
        "relative_path",
        "source_sha256",
    }
)


class FormalBootstrapError(RuntimeError):
    pass


def trusted_dispatch(context: object, frozen_action_config: object) -> int:
    validate_config = getattr(context, "validate_action_config", None)
    ledger_entry = getattr(context, "verified_ledger_entry", None)
    assert_module = getattr(context, "assert_verified_module", None)
    if (
        isinstance(context, Mapping)
        or not callable(validate_config)
        or not callable(ledger_entry)
        or not callable(assert_module)
    ):
        raise FormalBootstrapError("external trusted bootstrap context required")
    try:
        validate_config(frozen_action_config)
        entry = ledger_entry(_BUILDER_MODULE_NAME)
    except BaseException as exc:
        raise FormalBootstrapError(
            "external trusted bootstrap context rejected"
        ) from exc
    if (
        not isinstance(entry, Mapping)
        or set(entry) != _LEDGER_ENTRY_FIELDS
        or entry.get("module_name") != _BUILDER_MODULE_NAME
        or entry.get("relative_path") != _BUILDER_RELATIVE_PATH
        or entry.get("absolute_path")
        != str(_WORKTREE_ROOT / Path(*_BUILDER_RELATIVE_PATH.split("/")))
        or type(entry.get("source_sha256")) is not str
        or len(entry["source_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in entry["source_sha256"])
        or type(entry.get("byte_count")) is not int
        or entry["byte_count"] <= 0
        or entry.get("is_package") is not False
        or type(entry.get("loader_identity")) is not str
        or not entry["loader_identity"]
    ):
        raise FormalBootstrapError("external verified builder ledger rejected")
    try:
        assert_module(
            _BUILDER_MODULE_NAME,
            _BUILDER_RELATIVE_PATH,
            entry["source_sha256"],
        )
    except BaseException as exc:
        raise FormalBootstrapError("external verified builder ledger rejected") from exc
    builder = sys.modules.get(_BUILDER_MODULE_NAME)
    dispatch = getattr(builder, "trusted_dispatch", None)
    if not callable(dispatch):
        raise FormalBootstrapError("external verified builder module required")
    return int(dispatch(context, frozen_action_config))


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
