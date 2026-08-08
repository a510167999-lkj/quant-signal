from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from datetime import date
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any


class FormalRunSpecError(RuntimeError):
    pass


FORMAL_WORKTREE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v3-daily-basic-formal-run-v4"
)
EXPECTED_BRANCH = "codex/factor-v3-daily-basic-formal-run-v4"
FORMAL_REVIEW_SOURCE_RELATIVE_PATHS = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
    "scripts/run_factor_v3_daily_basic_formal.py",
    "app/__init__.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/current_pool.py",
    "app/current_pool_gate.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/factor_v3_formal_control_contract.py",
    "app/jiaoch_credential_slots.py",
    "app/jiaoch_daily_basic_collection_set.py",
    "app/jiaoch_daily_basic_exact_set_authority.py",
    "app/jiaoch_minute_collection_set.py",
    "app/jiaoch_minute_raw_authority.py",
    "app/jiaoch_minute_reconciliation.py",
    "app/jiaoch_points_collection_set.py",
    "app/jiaoch_points_raw_authority.py",
    "app/jiaoch_points_response_normalization.py",
    "app/jiaoch_trade_cal_authority.py",
    "app/research_market_data.py",
    "app/research_membership.py",
    "app/research_partitions.py",
    "app/research_pit_collector.py",
    "app/research_pit_contracts.py",
    "app/research_pit_sources.py",
    "app/research_pit_store.py",
    "app/research_pit_transport.py",
    "app/research_provider_evidence_partitions.py",
    "app/research_provider_pit_tail.py",
    "app/research_provider_pit_tail_v2.py",
    "app/research_proxy_data.py",
    "app/research_scope.py",
    "app/research_security_code_transition.py",
    "app/research_suspension_evidence.py",
)
MAIN_REPO_ROOT = Path(r"E:\AI workspace\quant-signal-lkj")
GIT_EXECUTABLE = Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
GIT_EXECUTABLE_SHA256 = (
    "c39b1b4f7a57935bbeadf246dc2466316619453a6a9da77c4a9c6bd6d8fb21d3"
)
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
PYTHON_EXECUTABLE = Path(
    r"E:\AI workspace\quant-signal-lkj\.venv\Scripts\python.exe"
)
PYTHON_EXECUTABLE_SHA256 = (
    "5fec912cd3c47c125754cfbcb9b21ce0b415f860cfa8e2a3b98ceb9cd73bd30f"
)
BASE_PYTHON_EXECUTABLE = Path(
    r"C:\Users\51016\AppData\Local\Programs\Python\Python311\python.exe"
)
BASE_PYTHON_EXECUTABLE_SHA256 = (
    "5be4ea9f930ff31f567d0505d76fe86ad6ffe8ee50f81e2739a142829f19a10d"
)
FORMAL_REVIEW_SOURCE_RELATIVE_PATHS = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
    "scripts/run_factor_v3_daily_basic_formal.py",
    "app/__init__.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/current_pool.py",
    "app/current_pool_gate.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/factor_v3_formal_control_contract.py",
    "app/jiaoch_credential_slots.py",
    "app/jiaoch_daily_basic_collection_set.py",
    "app/jiaoch_daily_basic_exact_set_authority.py",
    "app/jiaoch_minute_collection_set.py",
    "app/jiaoch_minute_raw_authority.py",
    "app/jiaoch_minute_reconciliation.py",
    "app/jiaoch_points_collection_set.py",
    "app/jiaoch_points_raw_authority.py",
    "app/jiaoch_points_response_normalization.py",
    "app/jiaoch_trade_cal_authority.py",
    "app/research_market_data.py",
    "app/research_membership.py",
    "app/research_partitions.py",
    "app/research_pit_collector.py",
    "app/research_pit_contracts.py",
    "app/research_pit_sources.py",
    "app/research_pit_store.py",
    "app/research_pit_transport.py",
    "app/research_provider_evidence_partitions.py",
    "app/research_provider_pit_tail.py",
    "app/research_provider_pit_tail_v2.py",
    "app/research_proxy_data.py",
    "app/research_scope.py",
    "app/research_security_code_transition.py",
    "app/research_suspension_evidence.py",
)
FORMAL_REVIEW_RECEIPT_ROOT = (
    MAIN_REPO_ROOT
    / "data/research_artifacts/factor_v3_daily_basic_formal_review_v4"
    / "review_receipts/sha256"
)
FORMAL_BOOTSTRAP_CLAIM_ROOT = (
    MAIN_REPO_ROOT
    / "data/research_artifacts/factor_v3_daily_basic_formal_review_v4"
    / "bootstrap_claims/sha256"
)
FORMAL_REVIEW_PUBLIC_KEY_PATH = (
    MAIN_REPO_ROOT
    / ".secrets/factor_v3_formal_review_rsa3072_public.pem"
)
FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256 = (
    "552852331cd6c7b0b08483b21c85fcc63b6ea9787c0c5ae8e146b246b46daaee"
)
FACTOR_V3_DAILY_BASIC_RUNNER_SHA256 = (
    "dbd5c19d641bab105789477bb96cb61c61d5b121d038ca03360588e47d8a71fb"
)
SPEC_OUTPUT_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_run_spec_v3"
    / "run_specs"
    / "sha256"
)
PLANNED_RUN_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_collection_v4_development_733"
)
FEATURE_HISTORY_ATTESTATION_SHA256 = (
    "865617ade317a440a4b7ce8889da51a865ad6ed065b8f2a9ceee2931e4571632"
)
EXACT_SET_AUTHORITY_INPUTS = {
    "feature_history_frozen_source_attestation_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "factor_v3_feature_history_frozen_source_attestation_v5"
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / FEATURE_HISTORY_ATTESTATION_SHA256[:2]
        / f"{FEATURE_HISTORY_ATTESTATION_SHA256}.json"
    ),
    "expected_feature_history_frozen_source_attestation_sha256": (
        FEATURE_HISTORY_ATTESTATION_SHA256
    ),
    "feature_history_frozen_source_root": (
        r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run"
    ),
    "expected_feature_history_frozen_source_commit": (
        "b8057962f7a9754848994a6cfda9c9bf85e3db89"
    ),
    "feature_history_run_spec_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_runs"
        / "audited_pit_factor_v3_feature_history_run_spec_v1"
        / "run_specs"
        / "sha256"
        / "1d"
        / "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
    ),
    "feature_history_run_root": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_runs"
        / "audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
    ),
    "audited_development_universe_sqlite_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "audited_pit_universe_v2"
        / "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4"
        / "metadata.sqlite3"
    ),
    "expected_development_coverage_audit_sha256": (
        "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
    ),
    "expected_development_artifact_root_sha256": (
        "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
    ),
    "expected_development_temporal_contract_sha256": (
        "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
    ),
    "expected_development_temporal_role": "development_4",
    "security_code_transition_evidence_root": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "security_code_transition_evidence_v1"
    ),
    "expected_security_code_transition_contract_sha256": (
        "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
    ),
}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FormalRunSpecError("formal run spec is not canonical JSON") from exc


_FORMAL_REVIEW_PROTOCOL = {
    "bootstrap_claim_schema": (
        "factor-v3-daily-basic-formal-bootstrap-claim/v1"
    ),
    "decision": "APPROVED_NO_P0_P1_P2",
    "public_key_spki_sha256": FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256,
    "receipt_schema": (
        "factor-v3-daily-basic-formal-review-signed-payload/v1"
    ),
    "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
    "source_manifest_fields": ["bytes", "path", "sha256"],
    "runtime_entrypoint": "scripts/run_factor_v3_daily_basic_formal.py",
    "runtime_python": {
        "base_executable_path": str(BASE_PYTHON_EXECUTABLE),
        "base_executable_sha256": BASE_PYTHON_EXECUTABLE_SHA256,
        "dont_write_bytecode": True,
        "executable_path": str(PYTHON_EXECUTABLE),
        "executable_sha256": PYTHON_EXECUTABLE_SHA256,
        "isolated": True,
    },
}
FORMAL_REVIEW_PROTOCOL_SHA256 = hashlib.sha256(
    _canonical_bytes(_FORMAL_REVIEW_PROTOCOL)
).hexdigest()
FORMAL_INPUT_ROOT_SHA256 = hashlib.sha256(
    _canonical_bytes(
        {
            "exact_set_authority_inputs": EXACT_SET_AUTHORITY_INPUTS,
            "feature_attestation_sha256": (
                FEATURE_HISTORY_ATTESTATION_SHA256
            ),
            "planned_run_root": str(PLANNED_RUN_ROOT),
            "schema": "factor-v3-daily-basic-formal-input-root/v1",
            "spec_output_root": str(SPEC_OUTPUT_ROOT),
        }
    )
).hexdigest()


def _script_worktree_root() -> Path:
    return Path(__file__).resolve().parents[1]


@contextmanager
def _open_pinned_file(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
    allow_hardlinks: bool = False,
):
    parent = _safe_existing_directory(
        path.parent,
        f"{label} parent",
    )
    candidate = parent / path.name
    before = candidate.lstat()
    if (
        _is_reparse(candidate)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > max_bytes
        or (
            int(getattr(before, "st_nlink", 1)) != 1
            if not allow_hardlinks
            else int(getattr(before, "st_nlink", 1)) < 1
        )
    ):
        raise FormalRunSpecError(f"{label} rejected")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(candidate),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise FormalRunSpecError(f"{label} safe open rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
    else:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        stream.seek(0)
        raw = stream.read(max_bytes + 1)
        stream.seek(0)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(getattr(opened, "st_file_attributes", 0)) & 0x00000400
            or not os.path.samestat(before, opened)
            or len(raw) != opened.st_size
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise FormalRunSpecError(f"{label} identity rejected")
        yield candidate.resolve(strict=True), stream
        _postverify_pinned_file(
            candidate,
            stream,
            expected_sha256=expected_sha256,
            label=label,
            max_bytes=max_bytes,
            allow_hardlinks=allow_hardlinks,
        )
    finally:
        stream.close()


def _postverify_pinned_file(
    path: Path,
    handle: Any,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
    allow_hardlinks: bool = False,
) -> None:
    opened = os.fstat(handle.fileno())
    parent = _safe_existing_directory(
        path.parent,
        f"{label} parent",
    )
    candidate = parent / path.name
    terminal = candidate.lstat()
    handle.seek(0)
    raw = handle.read(max_bytes + 1)
    handle.seek(0)
    if (
        not os.path.samestat(opened, terminal)
        or _is_reparse(candidate)
        or (
            int(getattr(terminal, "st_nlink", 1)) != 1
            if not allow_hardlinks
            else int(getattr(terminal, "st_nlink", 1)) < 1
        )
        or len(raw) != opened.st_size
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise FormalRunSpecError(f"{label} drifted")


def _git_output(*args: str) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    with _open_pinned_file(
        GIT_EXECUTABLE,
        expected_sha256=GIT_EXECUTABLE_SHA256,
        label="formal git executable",
        allow_hardlinks=True,
    ) as (executable, _handle):
        completed = subprocess.run(
            [str(executable), "-C", str(FORMAL_WORKTREE_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    return completed.stdout.strip()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _safe_existing_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalRunSpecError(f"{label} path rejected")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise FormalRunSpecError(f"{label} path rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(current):
            raise FormalRunSpecError(
                f"{label} contains a link or reparse point"
            )
    try:
        return path.resolve(strict=True)
    except OSError:
        raise FormalRunSpecError(f"{label} path rejected") from None


def _read_safe_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    expected_size: int | None = None,
) -> bytes:
    parent = _safe_existing_directory(path.parent, f"{label} parent")
    candidate = parent / path.name
    try:
        before = candidate.lstat()
    except OSError:
        raise FormalRunSpecError(f"{label} is missing") from None
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(candidate)
        or int(getattr(before, "st_nlink", 1)) != 1
        or before.st_size <= 0
        or before.st_size > max_bytes
        or (
            expected_size is not None
            and before.st_size != expected_size
        )
    ):
        raise FormalRunSpecError(f"{label} rejected")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise FormalRunSpecError(f"{label} safe open rejected") from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(getattr(opened, "st_nlink", 1)) != 1
            or int(getattr(opened, "st_file_attributes", 0)) & 0x00000400
            or not os.path.samestat(before, opened)
            or opened.st_size <= 0
            or opened.st_size > max_bytes
            or (
                expected_size is not None
                and opened.st_size != expected_size
            )
        ):
            raise FormalRunSpecError(f"{label} identity rejected")
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise FormalRunSpecError(f"{label} size rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FormalRunSpecError(f"{label} size rejected")
        after = os.fstat(descriptor)
        terminal = candidate.lstat()
        if (
            not os.path.samestat(opened, after)
            or not os.path.samestat(opened, terminal)
            or _is_reparse(candidate)
            or int(getattr(terminal, "st_nlink", 1)) != 1
            or after.st_size != opened.st_size
        ):
            raise FormalRunSpecError(f"{label} drifted")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validated_fixed_runtime_identity() -> dict[str, str]:
    base_executable = sys._base_executable
    pycache_prefix = sys.pycache_prefix
    if (
        os.name != "nt"
        or sys.flags.isolated != 1
        or not sys.dont_write_bytecode
        or type(base_executable) is not str
        or Path(sys.executable) != PYTHON_EXECUTABLE
        or Path(base_executable) != BASE_PYTHON_EXECUTABLE
        or type(pycache_prefix) is not str
        or not pycache_prefix
        or any(
            name == "app" or name.startswith("app.")
            for name in sys.modules
        )
    ):
        raise FormalRunSpecError("formal fixed Python runtime rejected")
    cache_root = _safe_existing_directory(
        Path(pycache_prefix),
        "formal isolated pycache",
    )
    try:
        if next(cache_root.iterdir(), None) is not None:
            raise FormalRunSpecError("formal isolated pycache is not empty")
    except OSError:
        raise FormalRunSpecError(
            "formal isolated pycache unavailable"
        ) from None
    with _open_pinned_file(
        PYTHON_EXECUTABLE,
        expected_sha256=PYTHON_EXECUTABLE_SHA256,
        label="formal python executable",
        allow_hardlinks=True,
    ):
        pass
    with _open_pinned_file(
        BASE_PYTHON_EXECUTABLE,
        expected_sha256=BASE_PYTHON_EXECUTABLE_SHA256,
        label="formal base python executable",
        allow_hardlinks=True,
    ):
        pass
    return {
        "base_python_executable_path": str(BASE_PYTHON_EXECUTABLE),
        "base_python_executable_sha256": (
            BASE_PYTHON_EXECUTABLE_SHA256
        ),
        "python_executable_path": str(PYTHON_EXECUTABLE),
        "python_executable_sha256": PYTHON_EXECUTABLE_SHA256,
        "pycache_prefix": str(cache_root),
    }


def _formal_review_source_manifest() -> list[dict[str, Any]]:
    entries = []
    for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
        raw = _read_safe_file(
            FORMAL_WORKTREE_ROOT / Path(*relative_path.split("/")),
            label="formal reviewed source",
            max_bytes=4 * 1024 * 1024,
        )
        entries.append(
            {
                "bytes": len(raw),
                "path": relative_path,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return entries


def _formal_review_source_root() -> str:
    return hashlib.sha256(
        _canonical_bytes(_formal_review_source_manifest())
    ).hexdigest()


def _strict_canonical_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise FormalRunSpecError(f"{label} duplicate key rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                FormalRunSpecError(f"{label} constant rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        FormalRunSpecError,
    ) as exc:
        raise FormalRunSpecError(f"{label} rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FormalRunSpecError(f"{label} canonical bytes rejected")
    return value


def _der_length(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise FormalRunSpecError("formal review public key DER rejected")
    first = raw[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if (
        count == 0
        or count > 4
        or offset + 1 + count > len(raw)
        or raw[offset + 1] == 0
    ):
        raise FormalRunSpecError("formal review public key DER rejected")
    length = int.from_bytes(raw[offset + 1 : offset + 1 + count], "big")
    if length < 0x80:
        raise FormalRunSpecError("formal review public key DER rejected")
    return length, offset + 1 + count


def _der_value(
    raw: bytes,
    offset: int,
    *,
    tag: int,
) -> tuple[bytes, int]:
    if offset >= len(raw) or raw[offset] != tag:
        raise FormalRunSpecError("formal review public key DER rejected")
    length, value_offset = _der_length(raw, offset + 1)
    end = value_offset + length
    if end > len(raw):
        raise FormalRunSpecError("formal review public key DER rejected")
    return raw[value_offset:end], end


def _positive_der_integer(raw: bytes, offset: int) -> tuple[int, int]:
    value, end = _der_value(raw, offset, tag=0x02)
    if (
        not value
        or value[0] & 0x80
        or (
            len(value) > 1
            and value[0] == 0
            and not value[1] & 0x80
        )
    ):
        raise FormalRunSpecError("formal review public key DER rejected")
    return int.from_bytes(value, "big"), end


def _parse_rsa3072_spki_der(raw: bytes) -> tuple[int, int]:
    if type(raw) is not bytes:
        raise FormalRunSpecError("formal review public key DER rejected")
    outer, end = _der_value(raw, 0, tag=0x30)
    if end != len(raw):
        raise FormalRunSpecError("formal review public key DER rejected")
    algorithm, offset = _der_value(outer, 0, tag=0x30)
    if algorithm != bytes.fromhex(
        "06092a864886f70d0101010500"
    ):
        raise FormalRunSpecError("formal review public key DER rejected")
    bit_string, end = _der_value(outer, offset, tag=0x03)
    if end != len(outer) or not bit_string or bit_string[0] != 0:
        raise FormalRunSpecError("formal review public key DER rejected")
    rsa_sequence, end = _der_value(bit_string[1:], 0, tag=0x30)
    if end != len(bit_string) - 1:
        raise FormalRunSpecError("formal review public key DER rejected")
    modulus, offset = _positive_der_integer(rsa_sequence, 0)
    exponent, end = _positive_der_integer(rsa_sequence, offset)
    if (
        end != len(rsa_sequence)
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise FormalRunSpecError("formal review public key rejected")
    return modulus, exponent


def _verify_rsa3072_pkcs1_v1_5_sha256(
    payload: bytes,
    signature: bytes,
    *,
    modulus: int,
    exponent: int,
) -> bool:
    if (
        type(payload) is not bytes
        or type(signature) is not bytes
        or type(modulus) is not int
        or type(exponent) is not int
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
        or len(signature) != 384
    ):
        raise FormalRunSpecError("formal review signature rejected")
    signature_int = int.from_bytes(signature, "big")
    if not 0 < signature_int < modulus:
        raise FormalRunSpecError("formal review signature rejected")
    encoded = pow(signature_int, exponent, modulus).to_bytes(384, "big")
    digest_info = bytes.fromhex(
        "3031300d060960864801650304020105000420"
    ) + hashlib.sha256(payload).digest()
    expected = (
        b"\x00\x01"
        + b"\xff" * (384 - len(digest_info) - 3)
        + b"\x00"
        + digest_info
    )
    if not hmac.compare_digest(encoded, expected):
        raise FormalRunSpecError("formal review signature rejected")
    return True


def _public_key_der_from_pem(raw: bytes) -> bytes:
    try:
        lines = raw.decode("ascii").splitlines()
        if (
            len(lines) < 3
            or lines[0] != "-----BEGIN PUBLIC KEY-----"
            or lines[-1] != "-----END PUBLIC KEY-----"
            or any(
                not line or re.fullmatch(r"[A-Za-z0-9+/=]+", line) is None
                for line in lines[1:-1]
            )
        ):
            raise ValueError
        der = base64.b64decode(
            "".join(lines[1:-1]).encode("ascii"),
            validate=True,
        )
    except (UnicodeDecodeError, ValueError):
        raise FormalRunSpecError("formal review public key rejected") from None
    _parse_rsa3072_spki_der(der)
    return der


def _validated_signed_review_receipt(
    raw: bytes,
    *,
    expected_commit: str,
    expected_source_manifest: list[dict[str, Any]],
    expected_formal_input_root_sha256: str,
    trusted_public_key_der: bytes,
) -> dict[str, Any]:
    outer = _strict_canonical_json(raw, label="formal review receipt")
    if set(outer) != {"payload", "signature_base64"}:
        raise FormalRunSpecError("formal review receipt shape rejected")
    payload = outer["payload"]
    signature_text = outer["signature_base64"]
    if type(payload) is not dict or type(signature_text) is not str:
        raise FormalRunSpecError("formal review receipt shape rejected")
    _assert_no_credential_shape(payload)
    fields = {
        "branch",
        "decision",
        "feature_attestation_sha256",
        "formal_input_root_sha256",
        "formal_runner_sha256",
        "issued_at_utc",
        "project_id",
        "review_nonce_sha256",
        "review_protocol_sha256",
        "reviewed_commit",
        "reviewed_source_manifest",
        "reviewed_source_root_sha256",
        "reviewer_key_id",
        "schema",
        "signature_scheme",
    }
    source_root = hashlib.sha256(
        _canonical_bytes(expected_source_manifest)
    ).hexdigest()
    key_id = f"sha256:{hashlib.sha256(trusted_public_key_der).hexdigest()}"
    if (
        set(payload) != fields
        or payload.get("schema")
        != "factor-v3-daily-basic-formal-review-signed-payload/v1"
        or payload.get("project_id") != "quant-signal-lkj"
        or payload.get("branch") != EXPECTED_BRANCH
        or payload.get("reviewed_commit") != expected_commit
        or payload.get("decision") != "APPROVED_NO_P0_P1_P2"
        or payload.get("reviewer_key_id") != key_id
        or payload.get("signature_scheme")
        != "RSASSA-PKCS1-v1_5-SHA256"
        or payload.get("review_protocol_sha256")
        != FORMAL_REVIEW_PROTOCOL_SHA256
        or payload.get("reviewed_source_manifest")
        != expected_source_manifest
        or payload.get("reviewed_source_root_sha256") != source_root
        or payload.get("formal_runner_sha256")
        != FACTOR_V3_DAILY_BASIC_RUNNER_SHA256
        or payload.get("feature_attestation_sha256")
        != FEATURE_HISTORY_ATTESTATION_SHA256
        or payload.get("formal_input_root_sha256")
        != expected_formal_input_root_sha256
        or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("review_nonce_sha256")))
        is None
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00",
            str(payload.get("issued_at_utc")),
        )
        is None
    ):
        raise FormalRunSpecError("formal review receipt payload rejected")
    try:
        signature = base64.b64decode(
            signature_text.encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise FormalRunSpecError("formal review signature rejected") from None
    if base64.b64encode(signature).decode("ascii") != signature_text:
        raise FormalRunSpecError("formal review signature rejected")
    modulus, exponent = _parse_rsa3072_spki_der(trusted_public_key_der)
    _verify_rsa3072_pkcs1_v1_5_sha256(
        _canonical_bytes(payload),
        signature,
        modulus=modulus,
        exponent=exponent,
    )
    return payload


def _content_addressed_json_path(
    root: Path,
    digest: str,
    *,
    label: str,
) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise FormalRunSpecError(f"{label} digest rejected")
    safe_root = _safe_existing_directory(root, f"{label} root")
    shard = _safe_existing_directory(
        safe_root / digest[:2],
        f"{label} shard",
    )
    return shard / f"{digest}.json"


def _bootstrap_claim_path(digest: str) -> Path:
    return _content_addressed_json_path(
        FORMAL_BOOTSTRAP_CLAIM_ROOT,
        digest,
        label="factor-v3 formal bootstrap claim",
    )


def _review_receipt_path(digest: str) -> Path:
    return _content_addressed_json_path(
        FORMAL_REVIEW_RECEIPT_ROOT,
        digest,
        label="factor-v3 formal review receipt",
    )


def _source_manifest_sha(
    source_manifest: list[dict[str, Any]],
    relative_path: str,
) -> str:
    matches = [
        item.get("sha256")
        for item in source_manifest
        if item.get("path") == relative_path
    ]
    if (
        len(matches) != 1
        or type(matches[0]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", matches[0]) is None
    ):
        raise FormalRunSpecError("formal bootstrap source manifest rejected")
    return matches[0]


def _validated_external_bootstrap_claim(
    raw: bytes,
    *,
    expected_claim_sha256: str,
    expected_commit: str,
    expected_source_manifest: list[dict[str, Any]],
    review_evidence: dict[str, Any],
) -> dict[str, Any]:
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_claim_sha256) is None
        or hashlib.sha256(raw).hexdigest() != expected_claim_sha256
    ):
        raise FormalRunSpecError("formal bootstrap claim identity rejected")
    claim = _strict_canonical_json(raw, label="formal bootstrap claim")
    _assert_no_credential_shape(claim)
    payload = review_evidence.get("payload")
    if type(payload) is not dict:
        raise FormalRunSpecError("formal bootstrap review evidence rejected")
    source_root = hashlib.sha256(
        _canonical_bytes(expected_source_manifest)
    ).hexdigest()
    expected = {
        "base_python_executable_path": str(BASE_PYTHON_EXECUTABLE),
        "base_python_executable_sha256": (
            BASE_PYTHON_EXECUTABLE_SHA256
        ),
        "branch": EXPECTED_BRANCH,
        "builder_sha256": _source_manifest_sha(
            expected_source_manifest,
            "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
        ),
        "formal_input_root_sha256": FORMAL_INPUT_ROOT_SHA256,
        "git_executable_path": str(GIT_EXECUTABLE),
        "git_executable_sha256": GIT_EXECUTABLE_SHA256,
        "project_id": "quant-signal-lkj",
        "python_executable_path": str(PYTHON_EXECUTABLE),
        "python_executable_sha256": PYTHON_EXECUTABLE_SHA256,
        "review_payload_sha256": review_evidence.get("payload_sha256"),
        "review_public_key_spki_sha256": (
            FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256
        ),
        "review_receipt_sha256": review_evidence.get("receipt_sha256"),
        "reviewed_commit": expected_commit,
        "reviewed_source_root_sha256": source_root,
        "schema": "factor-v3-daily-basic-formal-bootstrap-claim/v1",
        "shim_sha256": _source_manifest_sha(
            expected_source_manifest,
            "scripts/run_factor_v3_daily_basic_formal.py",
        ),
    }
    if (
        claim != expected
        or payload.get("reviewed_commit") != expected_commit
        or payload.get("reviewed_source_root_sha256") != source_root
        or payload.get("formal_input_root_sha256")
        != FORMAL_INPUT_ROOT_SHA256
    ):
        raise FormalRunSpecError("formal bootstrap claim rejected")
    return claim


def _bootstrap_receipt_sha256(claim_raw: bytes) -> str:
    claim = _strict_canonical_json(
        claim_raw,
        label="formal bootstrap claim",
    )
    receipt_sha256 = claim.get("review_receipt_sha256")
    if (
        type(receipt_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None
    ):
        raise FormalRunSpecError("formal bootstrap claim rejected")
    return receipt_sha256


@contextmanager
def _locked_formal_review_sources(
    *,
    expected_bootstrap_claim_sha256: str,
):
    if os.name != "nt":
        raise FormalRunSpecError(
            "formal review source locking requires Windows"
        )
    with ExitStack() as stack:
        stack.enter_context(
            _open_pinned_file(
                GIT_EXECUTABLE,
                expected_sha256=GIT_EXECUTABLE_SHA256,
                label="formal git executable",
                allow_hardlinks=True,
            )
        )
        stack.enter_context(
            _open_pinned_file(
                PYTHON_EXECUTABLE,
                expected_sha256=PYTHON_EXECUTABLE_SHA256,
                label="formal python executable",
                allow_hardlinks=True,
            )
        )
        stack.enter_context(
            _open_pinned_file(
                BASE_PYTHON_EXECUTABLE,
                expected_sha256=BASE_PYTHON_EXECUTABLE_SHA256,
                label="formal base python executable",
                allow_hardlinks=True,
            )
        )
        locked_manifest = []
        for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
            path = FORMAL_WORKTREE_ROOT / Path(
                *relative_path.split("/")
            )
            raw = _read_safe_file(
                path,
                label="formal reviewed source",
                max_bytes=4 * 1024 * 1024,
            )
            digest = hashlib.sha256(raw).hexdigest()
            stack.enter_context(
                _open_pinned_file(
                    path,
                    expected_sha256=digest,
                    label="formal reviewed source",
                    max_bytes=4 * 1024 * 1024,
                )
            )
            locked_manifest.append(
                {
                    "bytes": len(raw),
                    "path": relative_path,
                    "sha256": digest,
                }
            )
        public_raw = _read_safe_file(
            FORMAL_REVIEW_PUBLIC_KEY_PATH,
            label="factor-v3 formal review public key",
            max_bytes=16 * 1024,
        )
        stack.enter_context(
            _open_pinned_file(
                FORMAL_REVIEW_PUBLIC_KEY_PATH,
                expected_sha256=hashlib.sha256(public_raw).hexdigest(),
                label="factor-v3 formal review public key",
                max_bytes=16 * 1024,
            )
        )
        claim_path = _bootstrap_claim_path(
            expected_bootstrap_claim_sha256
        )
        claim_raw = _read_safe_file(
            claim_path,
            label="factor-v3 formal bootstrap claim",
            max_bytes=512 * 1024,
        )
        if (
            hashlib.sha256(claim_raw).hexdigest()
            != expected_bootstrap_claim_sha256
        ):
            raise FormalRunSpecError(
                "formal bootstrap claim content rejected"
            )
        stack.enter_context(
            _open_pinned_file(
                claim_path,
                expected_sha256=expected_bootstrap_claim_sha256,
                label="factor-v3 formal bootstrap claim",
                max_bytes=512 * 1024,
            )
        )
        receipt_sha256 = _bootstrap_receipt_sha256(claim_raw)
        receipt_path = _review_receipt_path(receipt_sha256)
        receipt_raw = _read_safe_file(
            receipt_path,
            label="factor-v3 formal review receipt",
            max_bytes=512 * 1024,
        )
        if hashlib.sha256(receipt_raw).hexdigest() != receipt_sha256:
            raise FormalRunSpecError("formal review receipt content rejected")
        stack.enter_context(
            _open_pinned_file(
                receipt_path,
                expected_sha256=receipt_sha256,
                label="factor-v3 formal review receipt",
                max_bytes=512 * 1024,
            )
        )
        yield {
            "bootstrap_claim_sha256": expected_bootstrap_claim_sha256,
            "review_receipt_sha256": receipt_sha256,
            "source_manifest": locked_manifest,
        }


def _validated_formal_review_receipt(
    *,
    expected_receipt_sha256: str,
    expected_commit: str,
    expected_source_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    public_raw = _read_safe_file(
        FORMAL_REVIEW_PUBLIC_KEY_PATH,
        label="factor-v3 formal review public key",
        max_bytes=16 * 1024,
    )
    public_der = _public_key_der_from_pem(public_raw)
    if (
        hashlib.sha256(public_der).hexdigest()
        != FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256
    ):
        raise FormalRunSpecError("formal review public key identity rejected")
    path = _review_receipt_path(expected_receipt_sha256)
    raw = _read_safe_file(
        path,
        label="factor-v3 formal review receipt",
        max_bytes=512 * 1024,
    )
    if hashlib.sha256(raw).hexdigest() != expected_receipt_sha256:
        raise FormalRunSpecError("formal review receipt content rejected")
    payload = _validated_signed_review_receipt(
        raw,
        expected_commit=expected_commit,
        expected_source_manifest=expected_source_manifest,
        expected_formal_input_root_sha256=FORMAL_INPUT_ROOT_SHA256,
        trusted_public_key_der=public_der,
    )
    return {
        "payload": payload,
        "payload_sha256": hashlib.sha256(
            _canonical_bytes(payload)
        ).hexdigest(),
        "receipt_sha256": expected_receipt_sha256,
    }


def verify_formal_worktree(
    *,
    expected_bootstrap_claim_sha256: str,
) -> dict[str, Any]:
    script_root = _script_worktree_root()
    if (
        script_root != FORMAL_WORKTREE_ROOT
        or not FORMAL_WORKTREE_ROOT.is_dir()
        or _is_reparse(FORMAL_WORKTREE_ROOT)
    ):
        raise FormalRunSpecError("formal worktree root drifted")
    try:
        git_root = Path(_git_output("rev-parse", "--show-toplevel")).resolve()
        commit = _git_output("rev-parse", "HEAD")
        branch = _git_output("branch", "--show-current")
        dirty = _git_output("status", "--porcelain=v1", "--untracked-files=all")
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise FormalRunSpecError("formal worktree git identity unavailable") from exc
    if git_root != FORMAL_WORKTREE_ROOT or branch != EXPECTED_BRANCH:
        raise FormalRunSpecError("formal worktree branch drifted")
    if _COMMIT_RE.fullmatch(commit) is None:
        raise FormalRunSpecError("formal worktree commit rejected")
    if dirty:
        raise FormalRunSpecError("formal worktree is dirty")
    source_manifest = _formal_review_source_manifest()
    source_root = hashlib.sha256(
        _canonical_bytes(source_manifest)
    ).hexdigest()
    claim_path = _bootstrap_claim_path(
        expected_bootstrap_claim_sha256
    )
    claim_raw = _read_safe_file(
        claim_path,
        label="factor-v3 formal bootstrap claim",
        max_bytes=512 * 1024,
    )
    if (
        hashlib.sha256(claim_raw).hexdigest()
        != expected_bootstrap_claim_sha256
    ):
        raise FormalRunSpecError("formal bootstrap claim content rejected")
    review_evidence = _validated_formal_review_receipt(
        expected_receipt_sha256=_bootstrap_receipt_sha256(claim_raw),
        expected_commit=commit,
        expected_source_manifest=source_manifest,
    )
    claim = _validated_external_bootstrap_claim(
        claim_raw,
        expected_claim_sha256=expected_bootstrap_claim_sha256,
        expected_commit=commit,
        expected_source_manifest=source_manifest,
        review_evidence=review_evidence,
    )
    receipt = review_evidence["payload"]
    if (
        receipt["reviewed_commit"] != commit
        or receipt["reviewed_source_root_sha256"] != source_root
    ):
        raise FormalRunSpecError("formal worktree reviewed source drifted")
    return {
        "bootstrap_claim": claim,
        "review_evidence": review_evidence,
        "source_manifest": source_manifest,
    }


_PlannedRunRootSnapshot = tuple[os.stat_result, os.stat_result | None]


def _same_file_identity(
    before: os.stat_result,
    after: os.stat_result,
) -> bool:
    return (
        stat.S_IFMT(before.st_mode) == stat.S_IFMT(after.st_mode)
        and os.path.samestat(before, after)
    )


def _planned_run_root_sidecars(parent: Path) -> list[Path]:
    sidecar_prefixes = tuple(
        value.casefold()
        for value in (
            f"{PLANNED_RUN_ROOT.name}.",
            f".{PLANNED_RUN_ROOT.name}.",
        )
    )
    try:
        return [
            child
            for child in parent.iterdir()
            if child.name.casefold().startswith(sidecar_prefixes)
        ]
    except OSError as exc:
        raise FormalRunSpecError(
            "planned run-root parent unavailable"
        ) from exc


def _planned_run_root_snapshot() -> _PlannedRunRootSnapshot:
    parent = PLANNED_RUN_ROOT.parent
    try:
        parent_before = parent.lstat()
    except OSError as exc:
        raise FormalRunSpecError(
            "planned run-root parent rejected"
        ) from exc
    if (
        not stat.S_ISDIR(parent_before.st_mode)
        or _is_reparse(parent)
    ):
        raise FormalRunSpecError("planned run-root parent rejected")
    if _planned_run_root_sidecars(parent):
        raise FormalRunSpecError("planned run-root sidecar exists")

    try:
        root_before = PLANNED_RUN_ROOT.lstat()
    except FileNotFoundError:
        root_before = None
    except OSError as exc:
        raise FormalRunSpecError(
            "planned run-root unavailable"
        ) from exc

    root_after: os.stat_result | None = None
    if root_before is None:
        if os.path.lexists(PLANNED_RUN_ROOT):
            raise FormalRunSpecError("planned run-root rejected")
    else:
        if (
            not stat.S_ISDIR(root_before.st_mode)
            or _is_reparse(PLANNED_RUN_ROOT)
        ):
            raise FormalRunSpecError("planned run-root rejected")
        try:
            if next(PLANNED_RUN_ROOT.iterdir(), None) is not None:
                raise FormalRunSpecError(
                    "planned run-root is not empty"
                )
            root_after = PLANNED_RUN_ROOT.lstat()
        except OSError as exc:
            raise FormalRunSpecError(
                "planned run-root unavailable"
            ) from exc
        if (
            not _same_file_identity(root_before, root_after)
            or _is_reparse(PLANNED_RUN_ROOT)
        ):
            raise FormalRunSpecError("planned run-root drifted")

    if _planned_run_root_sidecars(parent):
        raise FormalRunSpecError("planned run-root sidecar exists")
    try:
        parent_after = parent.lstat()
    except OSError as exc:
        raise FormalRunSpecError(
            "planned run-root parent unavailable"
        ) from exc
    if (
        not _same_file_identity(parent_before, parent_after)
        or _is_reparse(parent)
    ):
        raise FormalRunSpecError("planned run-root parent drifted")
    if root_before is None and os.path.lexists(PLANNED_RUN_ROOT):
        raise FormalRunSpecError("planned run-root drifted")
    return parent_after, root_after


def verify_planned_run_root() -> None:
    _planned_run_root_snapshot()


def _postverify_planned_run_root(
    snapshot: _PlannedRunRootSnapshot,
) -> None:
    parent_before, root_before = snapshot
    parent_after, root_after = _planned_run_root_snapshot()
    if (
        not _same_file_identity(parent_before, parent_after)
        or (root_before is None) != (root_after is None)
        or (
            root_before is not None
            and root_after is not None
            and not _same_file_identity(root_before, root_after)
        )
    ):
        raise FormalRunSpecError("planned run-root drifted")


def _assert_no_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = re.sub(r"[^a-z]", "", str(key).casefold())
            if (
                normalized
                in {
                    "token",
                    "credential",
                    "secret",
                    "password",
                    "api_key",
                    "route_credential",
                    "credential_material",
                }
                or "capability" in normalized
                or "privatekey" in compact
                or normalized.endswith(("_token", "_secret", "_password", "_api_key"))
            ):
                raise FormalRunSpecError("formal run spec contains a credential shape")
            _assert_no_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _assert_no_credential_shape(nested)


def _assert_formal_candidate(candidate: Any) -> dict[str, Any]:
    if type(candidate) is not dict:
        raise FormalRunSpecError("formal run spec rejected")
    sessions = candidate.get("sessions")
    if (
        candidate.get("schema") != "factor-v3-daily-basic-run-spec/v2"
        or candidate.get("session_count") != 733
        or type(sessions) is not list
        or len(sessions) != 733
        or len(set(sessions)) != 733
        or sessions != sorted(sessions)
    ):
        raise FormalRunSpecError("formal run spec must contain 733 collection sessions, not 732 T-1 labels")
    try:
        parsed_sessions = [date.fromisoformat(item).isoformat() for item in sessions]
    except (TypeError, ValueError) as exc:
        raise FormalRunSpecError("formal run spec sessions rejected") from exc
    if parsed_sessions != sessions:
        raise FormalRunSpecError("formal run spec sessions rejected")
    if sessions[0] != "2023-06-26":
        raise FormalRunSpecError("formal run spec must start at 2023-06-26")
    if sessions[-1] != "2026-07-03":
        raise FormalRunSpecError("formal run spec must end at 2026-07-03")
    if candidate.get("collector") != {
        "max_attempts": 3,
        "timeout_seconds": 30.0,
        "workers": 1,
    }:
        raise FormalRunSpecError("formal run spec collector drifted")
    if candidate.get("exact_set_authority_inputs") != EXACT_SET_AUTHORITY_INPUTS:
        raise FormalRunSpecError("formal run spec exact inputs drifted")
    _assert_no_credential_shape(candidate)
    return candidate


def build_and_verify_candidate(
    runner_module: ModuleType,
) -> tuple[dict[str, Any], bytes]:
    arguments = {
        "exact_set_authority_inputs": dict(EXACT_SET_AUTHORITY_INPUTS),
        "timeout_seconds": 30,
        "max_attempts": 3,
    }
    first = runner_module.build_factor_v3_daily_basic_run_spec(**arguments)
    second = runner_module.build_factor_v3_daily_basic_run_spec(**arguments)
    first_bytes = _canonical_bytes(first)
    if first_bytes != _canonical_bytes(second):
        raise FormalRunSpecError("formal run spec is not deterministic")
    candidate = _assert_formal_candidate(first)
    if first_bytes.endswith(b"\n"):
        raise FormalRunSpecError("formal run spec has a trailing newline")
    loaded = runner_module.validate_factor_v3_daily_basic_run_spec_bytes(
        first_bytes
    )
    if _canonical_bytes(loaded) != first_bytes:
        raise FormalRunSpecError("formal run spec load verification drifted")
    _assert_formal_candidate(loaded)
    return candidate, first_bytes


def _target_for(content: bytes) -> Path:
    file_sha256 = hashlib.sha256(content).hexdigest()
    return SPEC_OUTPUT_ROOT / file_sha256[:2] / f"{file_sha256}.json"


def _verify_existing_target(target: Path, content: bytes) -> None:
    try:
        metadata = target.lstat()
        existing = target.read_bytes()
    except OSError as exc:
        raise FormalRunSpecError("content-addressed target unavailable") from exc
    if (
        target.is_symlink()
        or _is_reparse(target)
        or not stat.S_ISREG(metadata.st_mode)
        or existing != content
    ):
        raise FormalRunSpecError("content-addressed target exists with different bytes")


def publish_candidate(content: bytes) -> Path:
    from app import jiaoch_points_raw_authority as raw_authority
    from app.durable_io import fsync_directory

    try:
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormalRunSpecError("content-addressed candidate rejected") from exc
    if not content or content.endswith(b"\n") or _canonical_bytes(parsed) != content:
        raise FormalRunSpecError("content-addressed candidate rejected")
    digest = hashlib.sha256(content).hexdigest()
    if SPEC_OUTPUT_ROOT.name != "sha256":
        raise FormalRunSpecError("content-addressed output directory rejected")
    authority_root = SPEC_OUTPUT_ROOT.parent.parent
    try:
        authority_parent = raw_authority._safe_existing_directory(
            authority_root.parent,
            "factor-v3 formal spec output parent",
        )
        authority_root = raw_authority._ensure_child_directory(
            authority_parent,
            authority_root.name,
        )
        target_parent = raw_authority._content_addressed_directory(
            authority_root,
            SPEC_OUTPUT_ROOT.parent.name,
            digest,
        )
        target = target_parent / f"{digest}.json"
        raw_authority._write_create_only(
            target,
            content,
            label="factor-v3 formal run spec",
            reuse_identical=True,
        )
        fsync_directory(target_parent)
        stored = raw_authority._read_safe_file(
            target,
            label="factor-v3 formal run spec",
            max_bytes=len(content),
            expected_size=len(content),
        )
    except (OSError, ValueError) as exc:
        raise FormalRunSpecError(
            "content-addressed target exists with different bytes"
        ) from exc
    if stored != content:
        raise FormalRunSpecError("content-addressed target postverify rejected")
    return target


def safe_summary(
    candidate: dict[str, Any],
    content: bytes,
    *,
    published: bool,
) -> dict[str, Any]:
    target = _target_for(content)
    return {
        "status": "preflight-verified",
        "published": published,
        "session_count": candidate["session_count"],
        "session_start": candidate["sessions"][0],
        "session_end": candidate["sessions"][-1],
        "file_sha256": hashlib.sha256(content).hexdigest(),
        "target_path": str(target),
    }


_TRUSTED_ACTION_CONFIG_FIELDS = frozenset(
    {
        "action",
        "formal_input_root",
        "formal_output_root",
        "run_spec_path",
        "run_root",
    }
)
_TRUSTED_CONTEXT_METHODS = (
    "acquire_preflight_terminal_guard",
    "assert_verified_module",
    "emit_json",
    "postverify",
    "preflight_terminal_guard_descriptor",
    "validate_action_config",
    "verified_ledger_entry",
)
_VERIFIED_LEDGER_ENTRY_FIELDS = frozenset(
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
_BUILDER_MODULE_NAME = (
    "scripts.build_factor_v3_daily_basic_formal_run_spec"
)
_BUILDER_RELATIVE_PATH = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py"
)
_SHIM_MODULE_NAME = "scripts.run_factor_v3_daily_basic_formal"
_SHIM_RELATIVE_PATH = "scripts/run_factor_v3_daily_basic_formal.py"
_RUNNER_MODULE_NAME = "app.factor_v3_daily_basic_runner"
_RUNNER_RELATIVE_PATH = "app/factor_v3_daily_basic_runner.py"
_CONTROL_CONTRACT_MODULE_NAME = "app.factor_v3_formal_control_contract"
_CONTROL_CONTRACT_RELATIVE_PATH = (
    "app/factor_v3_formal_control_contract.py"
)


def _trusted_context_method(context: Any, name: str) -> Any:
    if isinstance(context, Mapping):
        raise FormalRunSpecError(
            "formal trusted bootstrap context required"
        )
    method = getattr(context, name, None)
    if not callable(method):
        raise FormalRunSpecError(
            "formal trusted bootstrap context required"
        )
    return method


def _validated_trusted_action_config(
    context: Any,
    frozen_action_config: Any,
) -> dict[str, str]:
    for method_name in _TRUSTED_CONTEXT_METHODS:
        _trusted_context_method(context, method_name)
    if (
        isinstance(frozen_action_config, dict)
        or not isinstance(frozen_action_config, Mapping)
    ):
        raise FormalRunSpecError(
            "formal trusted bootstrap action config required"
        )
    try:
        _trusted_context_method(
            context,
            "validate_action_config",
        )(frozen_action_config)
        fields = set(frozen_action_config)
        values = {
            key: frozen_action_config[key]
            for key in _TRUSTED_ACTION_CONFIG_FIELDS
        }
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal trusted bootstrap action config rejected"
        ) from exc
    if (
        fields != _TRUSTED_ACTION_CONFIG_FIELDS
        or any(type(value) is not str or not value for value in values.values())
        or values["action"] not in {"build-spec", "preflight", "run", "verify"}
        or values["formal_input_root"] != FORMAL_INPUT_ROOT_SHA256
        or Path(values["formal_output_root"]) != SPEC_OUTPUT_ROOT
        or Path(values["run_root"]) != PLANNED_RUN_ROOT
        or not Path(values["run_spec_path"]).is_absolute()
    ):
        raise FormalRunSpecError(
            "formal trusted bootstrap action config rejected"
        )
    return values


def _module_name_for_reviewed_source(relative_path: str) -> str:
    if relative_path.endswith("/__init__.py"):
        return relative_path[: -len("/__init__.py")].replace("/", ".")
    return relative_path[:-3].replace("/", ".")


def _validated_verified_ledger_entry(
    context: Any,
    *,
    module_name: str,
    relative_path: str,
    expected_sha256: str | None = None,
    assert_loaded: bool,
) -> dict[str, Any]:
    try:
        entry = _trusted_context_method(
            context,
            "verified_ledger_entry",
        )(module_name)
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal verified loader ledger rejected"
        ) from exc
    expected_path = FORMAL_WORKTREE_ROOT / Path(
        *relative_path.split("/")
    )
    expected_package = relative_path.endswith("/__init__.py")
    if (
        not isinstance(entry, Mapping)
        or set(entry) != _VERIFIED_LEDGER_ENTRY_FIELDS
        or entry.get("module_name") != module_name
        or entry.get("relative_path") != relative_path
        or entry.get("absolute_path") != str(expected_path)
        or type(entry.get("byte_count")) is not int
        or entry["byte_count"] <= 0
        or type(entry.get("source_sha256")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", entry["source_sha256"]) is None
        or (
            expected_sha256 is not None
            and not hmac.compare_digest(
                entry["source_sha256"],
                expected_sha256,
            )
        )
        or type(entry.get("is_package")) is not bool
        or entry["is_package"] is not expected_package
        or type(entry.get("loader_identity")) is not str
        or not entry["loader_identity"]
    ):
        raise FormalRunSpecError(
            "formal verified loader ledger rejected"
        )
    if assert_loaded:
        try:
            _trusted_context_method(
                context,
                "assert_verified_module",
            )(
                module_name,
                relative_path,
                entry["source_sha256"],
            )
        except BaseException as exc:
            raise FormalRunSpecError(
                "formal verified loader ledger rejected"
            ) from exc
    return dict(entry)


def _trusted_source_manifest(context: Any) -> list[dict[str, Any]]:
    manifest = []
    for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
        module_name = _module_name_for_reviewed_source(relative_path)
        entry = _validated_verified_ledger_entry(
            context,
            module_name=module_name,
            relative_path=relative_path,
            assert_loaded=False,
        )
        manifest.append(
            {
                "bytes": entry["byte_count"],
                "path": relative_path,
                "sha256": entry["source_sha256"],
            }
        )
    return manifest


def _load_runner(context: Any) -> ModuleType:
    try:
        runner = importlib.import_module(_RUNNER_MODULE_NAME)
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal verified runner import rejected"
        ) from exc
    _validated_verified_ledger_entry(
        context,
        module_name=_RUNNER_MODULE_NAME,
        relative_path=_RUNNER_RELATIVE_PATH,
        assert_loaded=True,
    )
    return runner


def _postverify_verified_module_ledger(
    context: Any,
    source_manifest: list[dict[str, Any]],
) -> None:
    reviewed_modules = {
        _module_name_for_reviewed_source(item["path"]): item
        for item in source_manifest
        if (
            type(item) is dict
            and type(item.get("path")) is str
            and item["path"].endswith(".py")
        )
    }
    loaded_names = {
        name
        for name, module in sys.modules.items()
        if (
            module is not None
            and (
                name == "app"
                or name.startswith("app.")
                or name in {_BUILDER_MODULE_NAME, _SHIM_MODULE_NAME}
            )
        )
    }
    complete_manifest_paths = {
        item["path"]
        for item in source_manifest
        if type(item) is dict and type(item.get("path")) is str
    }
    if (
        complete_manifest_paths == set(FORMAL_REVIEW_SOURCE_RELATIVE_PATHS)
        and loaded_names - set(reviewed_modules)
    ):
        raise FormalRunSpecError(
            "formal loaded application module closure rejected"
        )
    loaded_names &= set(reviewed_modules)
    try:
        for name in sorted(loaded_names):
            expected = reviewed_modules[name]
            _validated_verified_ledger_entry(
                context,
                module_name=name,
                relative_path=expected["path"],
                expected_sha256=expected["sha256"],
                assert_loaded=True,
            )
    except FormalRunSpecError:
        raise
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal verified loader ledger rejected"
        ) from exc


def _emit_trusted_json(context: Any, value: Any) -> None:
    _assert_no_credential_shape(value)
    try:
        _trusted_context_method(context, "emit_json")(value)
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal trusted output buffer rejected"
        ) from exc


def _validated_preflight_terminal_guard(
    context: Any,
    *,
    action: str,
) -> Any:
    try:
        descriptor = _trusted_context_method(
            context,
            "preflight_terminal_guard_descriptor",
        )()
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal preflight terminal guard descriptor rejected"
        ) from exc
    try:
        control_contract = importlib.import_module(
            _CONTROL_CONTRACT_MODULE_NAME
        )
        _validated_verified_ledger_entry(
            context,
            module_name=_CONTROL_CONTRACT_MODULE_NAME,
            relative_path=_CONTROL_CONTRACT_RELATIVE_PATH,
            assert_loaded=True,
        )
        request = getattr(
            control_contract,
            "preflight_terminal_guard_request",
            None,
        )
        if not callable(request):
            raise TypeError("guard request factory unavailable")
        expected = request(
            action=action,
            run_root=str(PLANNED_RUN_ROOT),
        )
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal preflight terminal guard contract rejected"
        ) from exc
    if (
        not isinstance(descriptor, Mapping)
        or isinstance(descriptor, dict)
        or expected is None
        or set(descriptor) != set(expected)
        or dict(descriptor) != expected
    ):
        raise FormalRunSpecError(
            "formal preflight terminal guard descriptor rejected"
        )
    try:
        guard = _trusted_context_method(
            context,
            "acquire_preflight_terminal_guard",
        )(descriptor)
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal preflight terminal guard acquisition rejected"
        ) from exc
    if (
        isinstance(guard, Mapping)
        or getattr(guard, "descriptor", None) is not descriptor
        or not callable(getattr(guard, "__enter__", None))
        or not callable(getattr(guard, "__exit__", None))
        or not callable(getattr(guard, "bind_initial_snapshot", None))
        or not callable(
            getattr(guard, "terminal_postverify_and_emit", None)
        )
    ):
        raise FormalRunSpecError(
            "formal preflight terminal guard rejected"
        )
    return guard


def _guarded_preflight_or_build(
    context: Any,
    runner: Any,
    source_manifest: list[dict[str, Any]],
    *,
    action: str,
) -> None:
    guard = _validated_preflight_terminal_guard(
        context,
        action=action,
    )
    try:
        with guard as entered:
            if entered is not guard:
                raise FormalRunSpecError(
                    "formal preflight terminal guard rejected"
                )
            snapshot = _planned_run_root_snapshot()
            guard.bind_initial_snapshot(snapshot)
            candidate, content = build_and_verify_candidate(runner)
            published = action == "build-spec"
            if published:
                publish_candidate(content)
            result = safe_summary(
                candidate,
                content,
                published=published,
            )
            _postverify_verified_module_ledger(
                context,
                source_manifest,
            )
            _assert_no_credential_shape(result)
            guard.terminal_postverify_and_emit(snapshot, result)
    except FormalRunSpecError:
        raise
    except BaseException as exc:
        raise FormalRunSpecError(
            "formal preflight terminal guard rejected"
        ) from exc


def trusted_dispatch(
    context: Any,
    frozen_action_config: Any,
) -> int:
    config = _validated_trusted_action_config(
        context,
        frozen_action_config,
    )
    source_manifest = _trusted_source_manifest(context)
    for module_name, relative_path in (
        (_BUILDER_MODULE_NAME, _BUILDER_RELATIVE_PATH),
        (_SHIM_MODULE_NAME, _SHIM_RELATIVE_PATH),
    ):
        expected = next(
            item
            for item in source_manifest
            if item["path"] == relative_path
        )
        _validated_verified_ledger_entry(
            context,
            module_name=module_name,
            relative_path=relative_path,
            expected_sha256=expected["sha256"],
            assert_loaded=True,
        )
    runner = _load_runner(context)
    action = config["action"]
    if action in {"build-spec", "preflight"}:
        _guarded_preflight_or_build(
            context,
            runner,
            source_manifest,
            action=action,
        )
        return 0
    elif action == "run":
        try:
            result = runner.run_factor_v3_daily_basic_collection(
                run_spec_path=config["run_spec_path"],
                run_root=config["run_root"],
            )
        except (ValueError, RuntimeError) as exc:
            raise FormalRunSpecError(
                "formal daily-basic run rejected"
            ) from exc
    else:
        try:
            result = runner.verify_factor_v3_daily_basic_run(
                run_spec_path=config["run_spec_path"],
                run_root=config["run_root"],
            )
        except (ValueError, RuntimeError) as exc:
            raise FormalRunSpecError(
                "formal daily-basic verification rejected"
            ) from exc
    _postverify_verified_module_ledger(context, source_manifest)
    _emit_trusted_json(context, result)
    return 0


def main(argv: list[str] | None = None, **_kwargs: Any) -> int:
    del argv
    raise FormalRunSpecError(
        "formal trusted bootstrap context required"
    )


def run_locked_runner_cli(
    argv: list[str],
    **_kwargs: Any,
) -> int:
    del argv
    raise FormalRunSpecError(
        "formal trusted bootstrap context required"
    )


if __name__ == "__main__":
    raise SystemExit("external trusted bootstrap context required")
