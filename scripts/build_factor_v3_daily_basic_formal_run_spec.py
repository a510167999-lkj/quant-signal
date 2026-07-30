from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
from datetime import date
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
from typing import Any

from app import jiaoch_points_raw_authority as raw_authority
from app.durable_io import fsync_directory


class FormalRunSpecError(RuntimeError):
    pass


FORMAL_WORKTREE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v3-daily-basic-formal-run-v2"
)
EXPECTED_BRANCH = "codex/factor-v3-daily-basic-formal-run-v2"
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
PYTHON_EXECUTABLE = Path(
    r"E:\AI workspace\quant-signal-lkj\.venv\Scripts\python.exe"
)
PYTHON_EXECUTABLE_SHA256 = (
    "5fec912cd3c47c125754cfbcb9b21ce0b415f860cfa8e2a3b98ceb9cd73bd30f"
)
FORMAL_REVIEW_SOURCE_RELATIVE_PATHS = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/current_pool.py",
    "app/current_pool_gate.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
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
    / "data/research_artifacts/factor_v3_daily_basic_formal_review_v1"
    / "review_receipts/sha256"
)
FORMAL_REVIEW_PUBLIC_KEY_PATH = (
    MAIN_REPO_ROOT
    / ".secrets/factor_v3_formal_review_rsa3072_public.pem"
)
FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256 = (
    "552852331cd6c7b0b08483b21c85fcc63b6ea9787c0c5ae8e146b246b46daaee"
)
FACTOR_V3_DAILY_BASIC_RUNNER_SHA256 = (
    "454e0f4437cdfe5283b59d1b1cf0383151fe017fa5d0a0918f1d1108a0901ce9"
)
SPEC_OUTPUT_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_run_spec_v2"
    / "run_specs"
    / "sha256"
)
PLANNED_RUN_ROOT = (
    MAIN_REPO_ROOT
    / "data"
    / "research_runs"
    / "audited_pit_factor_v3_daily_basic_collection_v2_development_733"
)
FEATURE_HISTORY_ATTESTATION_SHA256 = (
    "4f73e1e0515d7c7932ba2e8b5c8885dac56645f83c5ed3d1c6570fbdd24f184c"
)
EXACT_SET_AUTHORITY_INPUTS = {
    "feature_history_frozen_source_attestation_path": str(
        MAIN_REPO_ROOT
        / "data"
        / "research_artifacts"
        / "factor_v3_feature_history_frozen_source_attestation_v3"
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / "68"
        / "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675.json"
    ),
    "expected_feature_history_frozen_source_attestation_sha256": (
        "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675"
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
    "decision": "APPROVED_NO_P0_P1_P2",
    "public_key_spki_sha256": FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256,
    "receipt_schema": (
        "factor-v3-daily-basic-formal-review-signed-payload/v1"
    ),
    "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
    "source_manifest_fields": ["bytes", "path", "sha256"],
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
    parent = raw_authority._safe_existing_directory(
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
    parent = raw_authority._safe_existing_directory(
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


def _formal_review_source_manifest() -> list[dict[str, Any]]:
    entries = []
    for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
        raw = raw_authority._read_safe_file(
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


def _review_receipt_candidates() -> list[Path]:
    try:
        root = raw_authority._safe_existing_directory(
            FORMAL_REVIEW_RECEIPT_ROOT,
            "factor-v3 formal review receipt root",
        )
        candidates = sorted(root.glob("*/*.json"))
    except (OSError, ValueError):
        raise FormalRunSpecError("formal review receipt unavailable") from None
    if not candidates:
        raise FormalRunSpecError("formal review receipt unavailable")
    return candidates


@contextmanager
def _locked_formal_review_sources():
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
        locked_manifest = []
        for relative_path in FORMAL_REVIEW_SOURCE_RELATIVE_PATHS:
            path = FORMAL_WORKTREE_ROOT / Path(
                *relative_path.split("/")
            )
            raw = raw_authority._read_safe_file(
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
        public_raw = raw_authority._read_safe_file(
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
        for receipt_path in _review_receipt_candidates():
            raw = raw_authority._read_safe_file(
                receipt_path,
                label="factor-v3 formal review receipt",
                max_bytes=512 * 1024,
            )
            stack.enter_context(
                _open_pinned_file(
                    receipt_path,
                    expected_sha256=hashlib.sha256(raw).hexdigest(),
                    label="factor-v3 formal review receipt",
                    max_bytes=512 * 1024,
                )
            )
        yield locked_manifest


def _validated_formal_review_receipt() -> dict[str, Any]:
    public_raw = raw_authority._read_safe_file(
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
    commit = _git_output("rev-parse", "HEAD")
    source_manifest = _formal_review_source_manifest()
    accepted = []
    for path in _review_receipt_candidates():
        digest = path.stem
        if (
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or path.parent.name != digest[:2]
        ):
            raise FormalRunSpecError("formal review receipt path rejected")
        raw = raw_authority._read_safe_file(
            path,
            label="factor-v3 formal review receipt",
            max_bytes=512 * 1024,
        )
        if hashlib.sha256(raw).hexdigest() != digest:
            raise FormalRunSpecError("formal review receipt content rejected")
        try:
            accepted.append(
                _validated_signed_review_receipt(
                    raw,
                    expected_commit=commit,
                    expected_source_manifest=source_manifest,
                    expected_formal_input_root_sha256=(
                        FORMAL_INPUT_ROOT_SHA256
                    ),
                    trusted_public_key_der=public_der,
                )
            )
        except FormalRunSpecError:
            continue
    if len(accepted) != 1:
        raise FormalRunSpecError("formal review signed receipt rejected")
    return accepted[0]


def verify_formal_worktree() -> None:
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
    receipt = _validated_formal_review_receipt()
    source_root = _formal_review_source_root()
    if (
        receipt["reviewed_commit"] != commit
        or receipt["reviewed_source_root_sha256"] != source_root
    ):
        raise FormalRunSpecError("formal worktree reviewed source drifted")


def verify_planned_run_root() -> None:
    parent = PLANNED_RUN_ROOT.parent
    if not parent.is_dir() or _is_reparse(parent):
        raise FormalRunSpecError("planned run-root parent rejected")
    sidecar_prefixes = (
        f"{PLANNED_RUN_ROOT.name}.",
        f".{PLANNED_RUN_ROOT.name}.",
    )
    try:
        sidecars = [
            child for child in parent.iterdir() if child.name.startswith(sidecar_prefixes)
        ]
    except OSError as exc:
        raise FormalRunSpecError("planned run-root parent unavailable") from exc
    if sidecars:
        raise FormalRunSpecError("planned run-root sidecar exists")
    if not PLANNED_RUN_ROOT.exists():
        if _is_reparse(PLANNED_RUN_ROOT):
            raise FormalRunSpecError("planned run-root rejected")
        return
    if not PLANNED_RUN_ROOT.is_dir() or _is_reparse(PLANNED_RUN_ROOT):
        raise FormalRunSpecError("planned run-root rejected")
    try:
        if next(PLANNED_RUN_ROOT.iterdir(), None) is not None:
            raise FormalRunSpecError("planned run-root is not empty")
    except OSError as exc:
        raise FormalRunSpecError("planned run-root unavailable") from exc


def _assert_no_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
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
    with tempfile.TemporaryDirectory(prefix="factor-v3-daily-basic-spec-") as temporary:
        candidate_path = Path(temporary) / "candidate.json"
        candidate_path.write_bytes(first_bytes)
        loaded = runner_module.load_factor_v3_daily_basic_run_spec(candidate_path)
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


def _load_runner() -> ModuleType:
    runner_path = FORMAL_WORKTREE_ROOT / "app/factor_v3_daily_basic_runner.py"
    with _open_pinned_file(
        runner_path,
        expected_sha256=FACTOR_V3_DAILY_BASIC_RUNNER_SHA256,
        label="factor-v3 daily-basic runner source",
        max_bytes=4 * 1024 * 1024,
    ) as (candidate, handle):
        worktree = str(FORMAL_WORKTREE_ROOT)
        if worktree not in sys.path:
            sys.path.insert(0, worktree)
        from app import factor_v3_daily_basic_runner

        imported_path = Path(
            factor_v3_daily_basic_runner.__file__
        ).resolve(strict=True)
        if imported_path != candidate:
            raise FormalRunSpecError("formal runner import identity rejected")
        _postverify_pinned_file(
            candidate,
            handle,
            expected_sha256=FACTOR_V3_DAILY_BASIC_RUNNER_SHA256,
            label="factor-v3 daily-basic runner source",
            max_bytes=4 * 1024 * 1024,
        )
        return factor_v3_daily_basic_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    with _locked_formal_review_sources():
        verify_formal_worktree()
        verify_planned_run_root()
        candidate, content = build_and_verify_candidate(_load_runner())
        if args.write:
            publish_candidate(content)
        print(
            json.dumps(
                safe_summary(candidate, content, published=args.write),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
