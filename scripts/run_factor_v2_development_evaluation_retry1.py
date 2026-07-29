from __future__ import annotations

import hashlib
from pathlib import Path
import stat
from types import ModuleType
from typing import Any


BASE_MODULE_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\data\research_runs"
    r"\.run_factor_v2_development_evaluation.py"
)
EXPECTED_BASE_MODULE_SHA256 = (
    "997f89e4f38262def6f1d846fa6a47355573b76efdd65eb4a5dee651e8034a4e"
)
SOURCE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v2-eval-fix"
)
EXPECTED_SOURCE_COMMIT = (
    "b0f0a78ebef207434e5cadd85299ef529dd1ff46"
)
OUTPUT_BASENAME = (
    "audited_pit_factor_v2_development_evaluation_v1_"
    "development_4_retry_1"
)
RUNS_ROOT = BASE_MODULE_PATH.parent
OUTPUT_DIR = RUNS_ROOT / OUTPUT_BASENAME
STATUS_PATH = OUTPUT_DIR.with_name(f"{OUTPUT_BASENAME}.run.status.json")
VERIFY_STATUS_PATH = OUTPUT_DIR.with_name(
    f"{OUTPUT_BASENAME}.verify.status.json"
)
STATUS_CLAIM_PATH = STATUS_PATH.with_name(f"{STATUS_PATH.name}.claim")
WRAPPER_PATH = Path(__file__).resolve(strict=True)
OVERRIDDEN_BASE_GLOBALS = (
    "SOURCE_ROOT",
    "EXPECTED_SOURCE_COMMIT",
    "OUTPUT_DIR",
    "STATUS_PATH",
    "VERIFY_STATUS_PATH",
    "STATUS_CLAIM_PATH",
)


def _read_verified_base_source() -> bytes:
    path = BASE_MODULE_PATH
    try:
        before = path.stat(follow_symlinks=False)
        if path.is_symlink() or not stat.S_ISREG(before.st_mode):
            raise RuntimeError("base module is not a direct regular file")
        source = path.read_bytes()
        after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("base module is unavailable") from exc
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if (
        path.is_symlink()
        or not stat.S_ISREG(after.st_mode)
        or any(
            getattr(before, field) != getattr(after, field)
            for field in identity_fields
        )
    ):
        raise RuntimeError("base module changed while being read")
    if (
        hashlib.sha256(source).hexdigest()
        != EXPECTED_BASE_MODULE_SHA256
    ):
        raise RuntimeError("base module SHA-256 drifted")
    return source


def _load_base_module() -> ModuleType:
    source = _read_verified_base_source()
    module = ModuleType("_factor_v2_evaluation_retry1_base_runner")
    module.__file__ = str(BASE_MODULE_PATH)
    module.__package__ = ""
    exec(
        compile(source, str(BASE_MODULE_PATH), "exec", dont_inherit=True),
        module.__dict__,
    )
    module.SOURCE_ROOT = SOURCE_ROOT
    module.EXPECTED_SOURCE_COMMIT = EXPECTED_SOURCE_COMMIT
    module.OUTPUT_DIR = OUTPUT_DIR
    module.STATUS_PATH = STATUS_PATH
    module.VERIFY_STATUS_PATH = VERIFY_STATUS_PATH
    module.STATUS_CLAIM_PATH = STATUS_CLAIM_PATH
    module.__file__ = str(WRAPPER_PATH)
    return module


_BASE_MODULE = _load_base_module()


def __getattr__(name: str) -> Any:
    return getattr(_BASE_MODULE, name)


def main() -> int:
    return _BASE_MODULE.main()


if __name__ == "__main__":
    raise SystemExit(main())
