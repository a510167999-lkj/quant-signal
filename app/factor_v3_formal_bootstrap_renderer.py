"""Freeze a reviewed configuration into a self-contained Windows bootstrap."""

from __future__ import annotations

import ast
import base64
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import errno
from functools import lru_cache
import hashlib
import hmac
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping

from app.factor_v3_formal_control_contract import (
    EXECUTION_REPLAY_SCOPE,
    PUBLICATION_COMPLETION_SCHEMA,
    WORKER_PROTOCOL,
    WORKER_TERMINAL_SCHEMA,
    canonical_stdlib_policy,
    control_contract_descriptor,
    control_contract_descriptor_sha256,
    stdlib_policy_root_sha256,
    validate_stdlib_policy,
    worker_protocol_descriptor,
)


class FormalBootstrapRenderError(RuntimeError):
    pass


CONFIG_SCHEMA = "factor-v3-formal-bootstrap-render-config/v1"
RUNTIME_TEMPLATE_SHA256 = "078881dc426951b9ddd212bca74ef13d1371e73696c03413b6ab82f69aaa151a"
AUTHORIZATION_SCHEMA = "factor-v3-formal-bootstrap-execution-authorization/v2"
PUBLICATION_RECEIPT_SCHEMA = "factor-v3-formal-bootstrap-publication-receipt/v1"
COMPLETION_SCHEMA = PUBLICATION_COMPLETION_SCHEMA
PRODUCTION_EXECUTION_AUTHORIZATION_PUBLIC_KEY_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\.secrets"
    r"\factor_v3_execution_authorization_rsa3072_public.pem"
)
PRODUCTION_EXECUTION_AUTHORIZATION_SPKI_SHA256 = (
    "70c8ad8f74cddfe363175d76c433cb145aadcd7cbd8af669768e697d99cccce8"
)
_EXECUTION_AUTHORIZATION_KEY_ROLE = "factor-v3-bootstrap-execution-authorization"
_REPLAY_SCOPE = EXECUTION_REPLAY_SCOPE
_SUPERVISOR_PROTOCOL = WORKER_PROTOCOL
_WORKER_TERMINAL_SCHEMA = WORKER_TERMINAL_SCHEMA
_RUNTIME_TEMPLATE_NAME = "factor_v3_formal_bootstrap_runtime.py"
_CONFIG_MARKER = b"_EMBEDDED_CONFIG_JSON: bytes | None = None"
_RENDERED_CONFIG_PREFIX = b"_EMBEDDED_CONFIG_JSON: bytes = "
_CONTROL_CONTRACT_MARKER = b"_EMBEDDED_CONTROL_CONTRACT_JSON: bytes | None = None"
_RENDERED_CONTROL_CONTRACT_PREFIX = b"_EMBEDDED_CONTROL_CONTRACT_JSON: bytes = "
_EARLY_STDLIB_ENTRIES_MARKER = b"_EARLY_STDLIB_ENTRIES: tuple[dict[str, object], ...] | None = None"
_RENDERED_EARLY_STDLIB_ENTRIES_PREFIX = b"_EARLY_STDLIB_ENTRIES: tuple[dict[str, object], ...] = "
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_RUNTIME_BYTES = 8 * 1024 * 1024
_MAX_EXECUTION_AUTHORIZATION_LIFETIME_SECONDS = 24 * 60 * 60
_REPARSE_ATTRIBUTE = 0x00000400
_CONFIG_FIELDS = {
    "action",
    "base_python_executable_path",
    "base_python_executable_sha256",
    "bootstrap_claim_path",
    "bootstrap_claim_sha256",
    "builder_relative_path",
    "builder_sha256",
    "expected_branch",
    "expected_commit",
    "formal_input_root",
    "formal_input_root_sha256",
    "formal_output_root",
    "git_executable_path",
    "git_executable_sha256",
    "project_id",
    "python_executable_path",
    "python_executable_sha256",
    "repo_root",
    "review_payload_sha256",
    "review_protocol_sha256",
    "review_public_key_spki_der_base64",
    "review_public_key_spki_sha256",
    "review_receipt_path",
    "review_receipt_sha256",
    "run_root",
    "run_spec_path",
    "schema",
    "shim_relative_path",
    "shim_sha256",
    "source_manifest",
    "source_root_sha256",
}
_AUTHORIZATION_FIELDS = {
    "action",
    "authorization_id_sha256",
    "authorization_nonce_sha256",
    "base_python_executable_path",
    "base_python_executable_sha256",
    "bootstrap_claim_path",
    "bootstrap_claim_sha256",
    "bootstrap_output_root",
    "builder_relative_path",
    "builder_sha256",
    "control_contract_descriptor_sha256",
    "expected_branch",
    "expected_commit",
    "execution_authorization_key_id",
    "execution_authorization_key_role",
    "expires_at_utc",
    "feature_attestation_sha256",
    "formal_input_root_path",
    "formal_input_root_sha256",
    "formal_output_root",
    "formal_runner_sha256",
    "git_executable_path",
    "git_executable_sha256",
    "issued_at_utc",
    "not_before_utc",
    "project_id",
    "python_executable_path",
    "python_executable_sha256",
    "repo_root",
    "replay_scope",
    "review_payload_sha256",
    "review_protocol_sha256",
    "review_public_key_spki_der_base64",
    "review_public_key_spki_sha256",
    "review_receipt_path",
    "review_receipt_sha256",
    "run_root",
    "run_spec_path",
    "run_spec_sha256",
    "runtime_template_sha256",
    "schema",
    "shim_relative_path",
    "shim_sha256",
    "source_manifest",
    "source_root_sha256",
    "stdlib_policy",
    "stdlib_policy_root_sha256",
    "supervisor_protocol",
}
_AUTHORIZED_CONFIG_FIELDS = _CONFIG_FIELDS | {
    "authorization_id_sha256",
    "authorization_issued_at_utc",
    "authorization_nonce_sha256",
    "bootstrap_output_root",
    "control_contract_descriptor_sha256",
    "execution_authorization_path",
    "execution_authorization_sha256",
    "execution_authorization_public_key_spki_der_base64",
    "execution_authorization_public_key_spki_sha256",
    "execution_authorization_key_id",
    "execution_authorization_key_role",
    "expires_at_utc",
    "feature_attestation_sha256",
    "formal_runner_sha256",
    "not_before_utc",
    "replay_scope",
    "run_spec_sha256",
    "runtime_template_sha256",
    "stdlib_policy",
    "stdlib_policy_root_sha256",
    "supervisor_protocol",
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
        raise FormalBootstrapRenderError("bootstrap configuration is not canonical JSON") from exc


def _strict_canonical_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise FormalBootstrapRenderError("rendered bootstrap configuration rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                FormalBootstrapRenderError("rendered bootstrap configuration rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        FormalBootstrapRenderError,
    ) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap configuration rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FormalBootstrapRenderError("rendered bootstrap configuration rejected")
    return value


def _reject_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            compact = re.sub(r"[^a-z]", "", str(key).casefold())
            if (
                normalized
                in {
                    "api_key",
                    "credential",
                    "credential_material",
                    "password",
                    "publication_capability",
                    "route_credential",
                    "secret",
                    "token",
                }
                or "capability" in normalized
                or "privatekey" in compact
                or normalized.endswith(("_api_key", "_password", "_secret", "_token"))
            ):
                raise FormalBootstrapRenderError(
                    "bootstrap configuration contains a credential shape"
                )
            _reject_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _reject_credential_shape(nested)


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise FormalBootstrapRenderError(f"{label} rejected")
    return value


def _parsed_utc(value: Any, *, label: str) -> datetime:
    if type(value) is not str:
        raise FormalBootstrapRenderError(f"{label} rejected")
    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S+00:00",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        raise FormalBootstrapRenderError(f"{label} rejected") from None
    return parsed


def _validated_replay_claim(
    value: Mapping[str, Any],
    *,
    now_utc: str | None = None,
) -> dict[str, Any]:
    fields = {
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "expires_at_utc",
        "issued_at_utc",
        "not_before_utc",
        "replay_scope",
    }
    if type(value) is not dict or set(value) != fields:
        raise FormalBootstrapRenderError("authorization replay claim rejected")
    output = dict(value)
    for field in (
        "authorization_id_sha256",
        "authorization_nonce_sha256",
    ):
        _require_sha256(output.get(field), label="authorization replay claim")
    if output.get("replay_scope") != _REPLAY_SCOPE:
        raise FormalBootstrapRenderError("authorization replay scope rejected")
    issued = _parsed_utc(output.get("issued_at_utc"), label="authorization replay claim")
    not_before = _parsed_utc(
        output.get("not_before_utc"),
        label="authorization replay claim",
    )
    expires = _parsed_utc(
        output.get("expires_at_utc"),
        label="authorization replay claim",
    )
    now = (
        _parsed_utc(now_utc, label="authorization replay clock")
        if now_utc is not None
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if (
        not issued <= not_before < expires
        or not not_before <= now <= expires
        or (expires - issued).total_seconds() > _MAX_EXECUTION_AUTHORIZATION_LIFETIME_SECONDS
    ):
        raise FormalBootstrapRenderError("authorization replay window rejected")
    return output


def _validate_distinct_authorization_key_roles(
    *,
    review_public_key_spki_sha256: str,
    execution_public_key_spki_sha256: str,
) -> None:
    _require_sha256(
        review_public_key_spki_sha256,
        label="review public key identity",
    )
    _require_sha256(
        execution_public_key_spki_sha256,
        label="execution public key identity",
    )
    if hmac.compare_digest(
        review_public_key_spki_sha256,
        execution_public_key_spki_sha256,
    ):
        raise FormalBootstrapRenderError(
            "review and execution authorization key roles must be distinct"
        )


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
    )


def _safe_existing_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalBootstrapRenderError(f"{label} rejected")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise FormalBootstrapRenderError(f"{label} rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(current):
            raise FormalBootstrapRenderError(f"{label} rejected")
    try:
        return path.resolve(strict=True)
    except OSError:
        raise FormalBootstrapRenderError(f"{label} rejected") from None


def _absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str:
        raise FormalBootstrapRenderError(f"{label} rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalBootstrapRenderError(f"{label} rejected")
    return path


def _read_safe_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    allow_hardlinks: bool = False,
) -> bytes:
    parent = _safe_existing_directory(path.parent, label=f"{label} parent")
    candidate = parent / path.name
    try:
        before = candidate.lstat()
    except OSError:
        raise FormalBootstrapRenderError(f"{label} unavailable") from None
    link_count = int(getattr(before, "st_nlink", 1))
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(candidate)
        or before.st_size <= 0
        or before.st_size > max_bytes
        or (link_count < 1 if allow_hardlinks else link_count != 1)
    ):
        raise FormalBootstrapRenderError(f"{label} rejected")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise FormalBootstrapRenderError(f"{label} unavailable") from None
    try:
        opened = os.fstat(descriptor)
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise FormalBootstrapRenderError(f"{label} rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FormalBootstrapRenderError(f"{label} rejected")
        after = os.fstat(descriptor)
        terminal = candidate.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
            or not os.path.samestat(opened, terminal)
            or _is_reparse(candidate)
            or after.st_size != opened.st_size
        ):
            raise FormalBootstrapRenderError(f"{label} drifted")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _stdlib_entry_kind_and_module(relative_path: str) -> tuple[str, str]:
    filename = relative_path.rsplit("/", 1)[-1]
    for entry_kind, suffixes in (
        ("source", importlib.machinery.SOURCE_SUFFIXES),
        ("bytecode", importlib.machinery.BYTECODE_SUFFIXES),
        ("extension", importlib.machinery.EXTENSION_SUFFIXES),
        ("dll", (".dll",)),
    ):
        for suffix in sorted(suffixes, key=len, reverse=True):
            if filename.endswith(suffix):
                stem_path = relative_path[: -len(suffix)]
                parts = stem_path.split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                module = ".".join(parts) or "__stdlib_root__"
                return entry_kind, module
    raise FormalBootstrapRenderError("stdlib inventory module rejected")


def _stdlib_inventory(
    path: Path,
    *,
    kind: str,
) -> tuple[list[dict[str, Any]], str]:
    if kind == "stdlib_zip":
        if not path.exists():
            return [], hashlib.sha256(
                _canonical_bytes(
                    {
                        "entries": [],
                        "kind": kind,
                        "path": str(path),
                        "state": "absent",
                    }
                )
            ).hexdigest()
        raw = _read_safe_file(
            path,
            label="stdlib zip",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_hardlinks=True,
        )
        entries = [
            {
                "bytes": len(raw),
                "kind": "stdlib_zip",
                "module": "__stdlib_zip__",
                "path": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ]
        return entries, hashlib.sha256(
            _canonical_bytes(
                {
                    "entries": entries,
                    "kind": kind,
                    "path": str(path),
                }
            )
        ).hexdigest()
    root = _safe_existing_directory(path, label=f"{kind} root")
    entries: list[dict[str, Any]] = []
    allowed_suffixes = tuple(
        dict.fromkeys(
            (
                ".py",
                *importlib.machinery.EXTENSION_SUFFIXES,
                ".dll",
            )
        )
    )
    for directory, names, filenames in os.walk(root, topdown=True):
        current = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name not in {"__pycache__", "site-packages"} and not _is_reparse(current / name)
        )
        for filename in sorted(filenames):
            candidate = current / filename
            if not filename.endswith(allowed_suffixes) or _is_reparse(candidate):
                continue
            metadata = candidate.lstat()
            if metadata.st_size == 0:
                if not stat.S_ISREG(metadata.st_mode):
                    raise FormalBootstrapRenderError(f"{kind} inventory file rejected")
                raw = b""
            else:
                raw = _read_safe_file(
                    candidate,
                    label=f"{kind} inventory file",
                    max_bytes=_MAX_EXECUTABLE_BYTES,
                    allow_hardlinks=True,
                )
            relative_path = candidate.relative_to(root).as_posix()
            entry_kind, module = _stdlib_entry_kind_and_module(relative_path)
            entries.append(
                {
                    "bytes": len(raw),
                    "kind": entry_kind,
                    "module": module,
                    "path": relative_path,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    return entries, hashlib.sha256(
        _canonical_bytes(
            {
                "entries": entries,
                "kind": kind,
                "path": str(root),
            }
        )
    ).hexdigest()


def _stdlib_inventory_sha256(path: Path, *, kind: str) -> str:
    return _stdlib_inventory(path, kind=kind)[1]


@lru_cache(maxsize=4)
def _trusted_stdlib_inventory_for_base_python(
    base_python_executable: Path,
) -> tuple[tuple[dict[str, str], ...], tuple[dict[str, Any], ...], str]:
    executable = _absolute_path(
        str(base_python_executable),
        label="base Python executable",
    )
    base_root = _safe_existing_directory(
        executable.parent,
        label="base Python root",
    )
    roots = [
        {"path": str(base_root / "DLLs"), "role": "platstdlib"},
        {"path": str(base_root / "Lib"), "role": "stdlib"},
    ]
    entries: list[dict[str, Any]] = []
    for root in roots:
        root_path = Path(root["path"])
        inventory, _inventory_sha256 = _stdlib_inventory(
            root_path,
            kind=str(root["role"]),
        )
        for item in inventory:
            if item["kind"] == "bytecode":
                raise FormalBootstrapRenderError("stdlib bytecode inventory rejected")
            relative_path = str(item["path"])
            kind = str(item["kind"])
            entries.append(
                {
                    "bytes": item["bytes"],
                    "is_package": (
                        kind != "dll" and relative_path.rsplit("/", 1)[-1].startswith("__init__.")
                    ),
                    "kind": kind,
                    "module": None if kind == "dll" else item["module"],
                    "path": str(root_path / Path(*relative_path.split("/"))),
                    "relative_path": relative_path,
                    "root": str(root_path),
                    "sha256": item["sha256"],
                }
            )
    absent_zip = base_root / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    if absent_zip.exists():
        raise FormalBootstrapRenderError("stdlib zip must be absent")
    return tuple(roots), tuple(entries), str(absent_zip)


def _trusted_stdlib_policy_for_base_python(
    base_python_executable: Path,
    pycache_prefix: Path,
) -> dict[str, Any]:
    roots, entries, absent_zip = _trusted_stdlib_inventory_for_base_python(base_python_executable)
    blocker_path = _absolute_path(
        str(pycache_prefix),
        label="signed pycache blocker",
    )
    _read_safe_file(
        blocker_path,
        label="signed pycache blocker",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    try:
        policy = canonical_stdlib_policy(
            roots=roots,
            entries=entries,
            absent_paths=[absent_zip],
            pycache_prefix=str(blocker_path),
        )
        return validate_stdlib_policy(policy, require_filesystem=True)
    except ValueError as exc:
        raise FormalBootstrapRenderError("stdlib policy rejected") from exc


def _compact_stdlib_policy_binding(policy: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_stdlib_policy(dict(policy))
    except ValueError as exc:
        raise FormalBootstrapRenderError("stdlib policy rejected") from exc


def _supervisor_protocol_descriptor() -> dict[str, Any]:
    return worker_protocol_descriptor()


def _production_execution_authorization_public_key_der() -> bytes:
    raw = _read_safe_file(
        PRODUCTION_EXECUTION_AUTHORIZATION_PUBLIC_KEY_PATH,
        label="production execution authorization public key",
        max_bytes=64 * 1024,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise FormalBootstrapRenderError(
            "production execution authorization public key rejected"
        ) from None
    lines = text.replace("\r\n", "\n").strip().splitlines()
    if (
        len(lines) < 3
        or lines[0] != "-----BEGIN PUBLIC KEY-----"
        or lines[-1] != "-----END PUBLIC KEY-----"
    ):
        raise FormalBootstrapRenderError("production execution authorization public key rejected")
    body = "".join(lines[1:-1])
    try:
        public_der = base64.b64decode(body.encode("ascii"), validate=True)
    except ValueError:
        raise FormalBootstrapRenderError(
            "production execution authorization public key rejected"
        ) from None
    if hashlib.sha256(public_der).hexdigest() != PRODUCTION_EXECUTION_AUTHORIZATION_SPKI_SHA256:
        raise FormalBootstrapRenderError("production execution authorization public key rejected")
    _parse_rsa3072_spki(public_der)
    return public_der


def _der_length(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    first = raw[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(raw) or raw[offset + 1] == 0:
        raise FormalBootstrapRenderError("embedded public key rejected")
    length = int.from_bytes(raw[offset + 1 : offset + 1 + count], "big")
    if length < 0x80:
        raise FormalBootstrapRenderError("embedded public key rejected")
    return length, offset + 1 + count


def _der_value(
    raw: bytes,
    offset: int,
    *,
    tag: int,
) -> tuple[bytes, int]:
    if offset >= len(raw) or raw[offset] != tag:
        raise FormalBootstrapRenderError("embedded public key rejected")
    length, value_offset = _der_length(raw, offset + 1)
    end = value_offset + length
    if end > len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    return raw[value_offset:end], end


def _positive_der_integer(raw: bytes, offset: int) -> tuple[int, int]:
    value, end = _der_value(raw, offset, tag=0x02)
    if not value or value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
        raise FormalBootstrapRenderError("embedded public key rejected")
    return int.from_bytes(value, "big"), end


def _parse_rsa3072_spki(raw: bytes) -> tuple[int, int]:
    outer, end = _der_value(raw, 0, tag=0x30)
    if end != len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    algorithm, offset = _der_value(outer, 0, tag=0x30)
    if algorithm != bytes.fromhex("06092a864886f70d0101010500"):
        raise FormalBootstrapRenderError("embedded public key rejected")
    bit_string, end = _der_value(outer, offset, tag=0x03)
    if end != len(outer) or not bit_string or bit_string[0] != 0:
        raise FormalBootstrapRenderError("embedded public key rejected")
    sequence, end = _der_value(bit_string[1:], 0, tag=0x30)
    if end != len(bit_string) - 1:
        raise FormalBootstrapRenderError("embedded public key rejected")
    modulus, offset = _positive_der_integer(sequence, 0)
    exponent, end = _positive_der_integer(sequence, offset)
    if (
        end != len(sequence)
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise FormalBootstrapRenderError("embedded public key rejected")
    return modulus, exponent


def _verify_rsa3072_signature(
    payload: bytes,
    signature: bytes,
    *,
    public_der: bytes,
    label: str,
) -> None:
    modulus, exponent = _parse_rsa3072_spki(public_der)
    if type(signature) is not bytes or len(signature) != 384:
        raise FormalBootstrapRenderError(f"{label} signature rejected")
    signature_int = int.from_bytes(signature, "big")
    if not 0 < signature_int < modulus:
        raise FormalBootstrapRenderError(f"{label} signature rejected")
    encoded = pow(signature_int, exponent, modulus).to_bytes(384, "big")
    digest_info = (
        bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
    )
    expected = b"\x00\x01" + b"\xff" * (384 - len(digest_info) - 3) + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise FormalBootstrapRenderError(f"{label} signature rejected")


def _validate_cas_path(path: Path, digest: str, *, label: str) -> None:
    if (
        path.name != f"{digest}.json"
        or path.parent.name != digest[:2]
        or path.parent.parent.name != "sha256"
    ):
        raise FormalBootstrapRenderError(f"{label} CAS rejected")


def _validated_manifest(
    value: Any,
    *,
    repo_root: Path,
    builder_relative_path: str,
    builder_sha256: str,
    shim_relative_path: str,
    shim_sha256: str,
    expected_root_sha256: str,
) -> list[dict[str, Any]]:
    if type(value) is not list or not value:
        raise FormalBootstrapRenderError("source manifest rejected")
    output = []
    seen: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != {"bytes", "path", "sha256"}:
            raise FormalBootstrapRenderError("source manifest rejected")
        relative_path = item.get("path")
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        if (
            type(relative_path) is not str
            or relative_path in seen
            or "\\" in relative_path
            or relative_path.startswith("/")
            or ".." in relative_path.split("/")
            or not relative_path.endswith(".py")
            or not (
                relative_path.startswith("app/")
                or relative_path in {builder_relative_path, shim_relative_path}
            )
            or type(byte_count) is not int
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= _MAX_SOURCE_BYTES
        ):
            raise FormalBootstrapRenderError("source manifest rejected")
        digest = _require_sha256(digest, label="source manifest SHA")
        raw = _read_safe_file(
            repo_root / Path(*relative_path.split("/")),
            label="reviewed source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
            raise FormalBootstrapRenderError("source manifest rejected")
        seen.add(relative_path)
        output.append(
            {
                "bytes": byte_count,
                "path": relative_path,
                "sha256": digest,
            }
        )
    by_path = {item["path"]: item for item in output}
    if (
        "app/__init__.py" not in by_path
        or by_path.get(builder_relative_path, {}).get("sha256") != builder_sha256
        or by_path.get(shim_relative_path, {}).get("sha256") != shim_sha256
        or hashlib.sha256(_canonical_bytes(output)).hexdigest() != expected_root_sha256
    ):
        raise FormalBootstrapRenderError(
            "source manifest must bind app/__init__.py, builder, and shim"
        )
    return output


def _validated_config(value: Mapping[str, Any]) -> dict[str, Any]:
    if os.name != "nt" or type(value) is not dict:
        raise FormalBootstrapRenderError(
            "bootstrap renderer requires an exact Windows configuration"
        )
    config = json.loads(_canonical_bytes(value))
    _reject_credential_shape(config)
    if (
        set(config) != _CONFIG_FIELDS
        or config.get("schema") != CONFIG_SCHEMA
        or config.get("project_id") != "quant-signal-lkj"
        or config.get("action") not in {"build-spec", "run", "verify"}
        or type(config.get("expected_branch")) is not str
        or not config["expected_branch"]
        or _COMMIT_RE.fullmatch(str(config.get("expected_commit"))) is None
    ):
        raise FormalBootstrapRenderError("bootstrap configuration rejected")
    for field in (
        "base_python_executable_sha256",
        "bootstrap_claim_sha256",
        "builder_sha256",
        "formal_input_root_sha256",
        "git_executable_sha256",
        "python_executable_sha256",
        "review_payload_sha256",
        "review_protocol_sha256",
        "review_public_key_spki_sha256",
        "review_receipt_sha256",
        "shim_sha256",
        "source_root_sha256",
    ):
        _require_sha256(config.get(field), label=field)
    paths = {
        field: _absolute_path(config.get(field), label=field)
        for field in (
            "base_python_executable_path",
            "bootstrap_claim_path",
            "formal_input_root",
            "formal_output_root",
            "git_executable_path",
            "python_executable_path",
            "repo_root",
            "review_receipt_path",
            "run_root",
            "run_spec_path",
        )
    }
    repo_root = _safe_existing_directory(
        paths["repo_root"],
        label="repository root",
    )
    for field in (
        "formal_input_root",
        "formal_output_root",
        "run_root",
    ):
        _safe_existing_directory(paths[field], label=field)
    for field in (
        "builder_relative_path",
        "shim_relative_path",
    ):
        relative_path = config.get(field)
        if (
            type(relative_path) is not str
            or not relative_path.startswith("scripts/")
            or not relative_path.endswith(".py")
            or "\\" in relative_path
            or ".." in relative_path.split("/")
        ):
            raise FormalBootstrapRenderError(f"{field} rejected")
    config["source_manifest"] = _validated_manifest(
        config["source_manifest"],
        repo_root=repo_root,
        builder_relative_path=config["builder_relative_path"],
        builder_sha256=config["builder_sha256"],
        shim_relative_path=config["shim_relative_path"],
        shim_sha256=config["shim_sha256"],
        expected_root_sha256=config["source_root_sha256"],
    )
    for path_field, sha_field, max_bytes, allow_hardlinks in (
        (
            "python_executable_path",
            "python_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "base_python_executable_path",
            "base_python_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "git_executable_path",
            "git_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "bootstrap_claim_path",
            "bootstrap_claim_sha256",
            _MAX_CONFIG_BYTES,
            False,
        ),
        (
            "review_receipt_path",
            "review_receipt_sha256",
            _MAX_CONFIG_BYTES,
            False,
        ),
    ):
        raw = _read_safe_file(
            paths[path_field],
            label=path_field,
            max_bytes=max_bytes,
            allow_hardlinks=allow_hardlinks,
        )
        if hashlib.sha256(raw).hexdigest() != config[sha_field]:
            raise FormalBootstrapRenderError(f"{path_field} identity rejected")
    _validate_cas_path(
        paths["bootstrap_claim_path"],
        config["bootstrap_claim_sha256"],
        label="bootstrap claim",
    )
    _validate_cas_path(
        paths["review_receipt_path"],
        config["review_receipt_sha256"],
        label="review receipt",
    )
    try:
        public_der = base64.b64decode(
            config["review_public_key_spki_der_base64"].encode("ascii"),
            validate=True,
        )
    except (AttributeError, UnicodeEncodeError, ValueError):
        raise FormalBootstrapRenderError("embedded public key rejected") from None
    if (
        base64.b64encode(public_der).decode("ascii") != config["review_public_key_spki_der_base64"]
        or hashlib.sha256(public_der).hexdigest() != config["review_public_key_spki_sha256"]
    ):
        raise FormalBootstrapRenderError("embedded public key rejected")
    _parse_rsa3072_spki(public_der)
    return config


def _decoded_signature(value: Any, *, label: str) -> bytes:
    if type(value) is not str:
        raise FormalBootstrapRenderError(f"{label} signature rejected")
    try:
        signature = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise FormalBootstrapRenderError(f"{label} signature rejected") from None
    if base64.b64encode(signature).decode("ascii") != value:
        raise FormalBootstrapRenderError(f"{label} signature rejected")
    return signature


def _validate_claim_and_review_receipt(
    payload: Mapping[str, Any],
    *,
    claim_raw: bytes,
    receipt_raw: bytes,
) -> None:
    claim = _strict_canonical_json(claim_raw)
    expected_claim = {
        "base_python_executable_path": payload["base_python_executable_path"],
        "base_python_executable_sha256": payload["base_python_executable_sha256"],
        "branch": payload["expected_branch"],
        "builder_sha256": payload["builder_sha256"],
        "formal_input_root_sha256": payload["formal_input_root_sha256"],
        "git_executable_path": payload["git_executable_path"],
        "git_executable_sha256": payload["git_executable_sha256"],
        "project_id": payload["project_id"],
        "python_executable_path": payload["python_executable_path"],
        "python_executable_sha256": payload["python_executable_sha256"],
        "review_payload_sha256": payload["review_payload_sha256"],
        "review_public_key_spki_sha256": payload["review_public_key_spki_sha256"],
        "review_receipt_sha256": payload["review_receipt_sha256"],
        "reviewed_commit": payload["expected_commit"],
        "reviewed_source_root_sha256": payload["source_root_sha256"],
        "schema": "factor-v3-daily-basic-formal-bootstrap-claim/v1",
        "shim_sha256": payload["shim_sha256"],
    }
    if claim != expected_claim:
        raise FormalBootstrapRenderError("bootstrap claim rejected")
    outer = _strict_canonical_json(receipt_raw)
    if set(outer) != {"payload", "signature_base64"}:
        raise FormalBootstrapRenderError("review receipt rejected")
    review_payload = outer.get("payload")
    review_fields = {
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
    if type(review_payload) is dict:
        if (
            review_payload.get("feature_attestation_sha256")
            != payload["feature_attestation_sha256"]
        ):
            raise FormalBootstrapRenderError("feature attestation sha256 semantic field rejected")
        if review_payload.get("formal_runner_sha256") != payload["formal_runner_sha256"]:
            raise FormalBootstrapRenderError("formal runner sha256 semantic field rejected")
    if (
        type(review_payload) is not dict
        or set(review_payload) != review_fields
        or review_payload.get("schema") != "factor-v3-daily-basic-formal-review-signed-payload/v1"
        or review_payload.get("project_id") != payload["project_id"]
        or review_payload.get("branch") != payload["expected_branch"]
        or review_payload.get("reviewed_commit") != payload["expected_commit"]
        or review_payload.get("decision") != "APPROVED_NO_P0_P1_P2"
        or review_payload.get("reviewed_source_manifest") != payload["source_manifest"]
        or review_payload.get("reviewed_source_root_sha256") != payload["source_root_sha256"]
        or review_payload.get("formal_input_root_sha256") != payload["formal_input_root_sha256"]
        or review_payload.get("review_protocol_sha256") != payload["review_protocol_sha256"]
        or review_payload.get("reviewer_key_id")
        != f"sha256:{payload['review_public_key_spki_sha256']}"
        or review_payload.get("signature_scheme") != "RSASSA-PKCS1-v1_5-SHA256"
        or _SHA256_RE.fullmatch(str(review_payload.get("review_nonce_sha256"))) is None
    ):
        raise FormalBootstrapRenderError("review receipt semantic field rejected")
    review_payload_raw = _canonical_bytes(review_payload)
    if hashlib.sha256(review_payload_raw).hexdigest() != payload["review_payload_sha256"]:
        raise FormalBootstrapRenderError("review receipt payload rejected")
    try:
        review_public_der = base64.b64decode(
            str(payload["review_public_key_spki_der_base64"]).encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise FormalBootstrapRenderError("review public key rejected") from None
    if (
        base64.b64encode(review_public_der).decode("ascii")
        != payload["review_public_key_spki_der_base64"]
        or hashlib.sha256(review_public_der).hexdigest() != payload["review_public_key_spki_sha256"]
    ):
        raise FormalBootstrapRenderError("review public key rejected")
    _verify_rsa3072_signature(
        review_payload_raw,
        _decoded_signature(outer.get("signature_base64"), label="review"),
        public_der=review_public_der,
        label="review",
    )


def _validated_execution_authorization(
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> tuple[dict[str, Any], bytes, str]:
    path = _absolute_path(str(authorization_path), label="execution authorization path")
    raw = _read_safe_file(
        path,
        label="execution authorization",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    digest = hashlib.sha256(raw).hexdigest()
    _validate_cas_path(path, digest, label="execution authorization")
    outer = _strict_canonical_json(raw)
    if set(outer) != {"payload", "signature_base64"}:
        raise FormalBootstrapRenderError("execution authorization rejected")
    payload = outer.get("payload")
    if (
        type(payload) is not dict
        or set(payload) != _AUTHORIZATION_FIELDS
        or payload.get("schema") != AUTHORIZATION_SCHEMA
        or payload.get("project_id") != "quant-signal-lkj"
        or payload.get("action") not in {"build-spec", "run", "verify"}
        or type(payload.get("expected_branch")) is not str
        or not payload["expected_branch"]
        or _COMMIT_RE.fullmatch(str(payload.get("expected_commit"))) is None
    ):
        raise FormalBootstrapRenderError("execution authorization rejected")
    _reject_credential_shape(payload)
    for field in (
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "base_python_executable_sha256",
        "bootstrap_claim_sha256",
        "builder_sha256",
        "control_contract_descriptor_sha256",
        "feature_attestation_sha256",
        "formal_input_root_sha256",
        "formal_runner_sha256",
        "git_executable_sha256",
        "python_executable_sha256",
        "review_payload_sha256",
        "review_protocol_sha256",
        "review_public_key_spki_sha256",
        "review_receipt_sha256",
        "run_spec_sha256",
        "runtime_template_sha256",
        "shim_sha256",
        "source_root_sha256",
        "stdlib_policy_root_sha256",
    ):
        _require_sha256(payload.get(field), label=field.replace("_", " "))
    if payload["runtime_template_sha256"] != RUNTIME_TEMPLATE_SHA256:
        raise FormalBootstrapRenderError("runtime template SHA rejected")
    if type(trusted_public_key_spki_der) is not bytes:
        raise FormalBootstrapRenderError("authorization public key rejected")
    execution_key_sha256 = hashlib.sha256(trusted_public_key_spki_der).hexdigest()
    if (
        payload.get("execution_authorization_key_id") != f"sha256:{execution_key_sha256}"
        or payload.get("execution_authorization_key_role") != _EXECUTION_AUTHORIZATION_KEY_ROLE
    ):
        raise FormalBootstrapRenderError("execution authorization key role rejected")
    _validate_distinct_authorization_key_roles(
        review_public_key_spki_sha256=payload["review_public_key_spki_sha256"],
        execution_public_key_spki_sha256=execution_key_sha256,
    )
    _validated_replay_claim(
        {
            field: payload[field]
            for field in (
                "authorization_id_sha256",
                "authorization_nonce_sha256",
                "expires_at_utc",
                "issued_at_utc",
                "not_before_utc",
                "replay_scope",
            )
        }
    )
    supplied_policy = payload.get("stdlib_policy")
    if type(supplied_policy) is not dict:
        raise FormalBootstrapRenderError("stdlib policy rejected")
    expected_stdlib_policy = _trusted_stdlib_policy_for_base_python(
        Path(str(payload["base_python_executable_path"])),
        Path(str(supplied_policy.get("pycache_prefix"))),
    )
    if (
        supplied_policy != expected_stdlib_policy
        or payload.get("stdlib_policy_root_sha256")
        != stdlib_policy_root_sha256(expected_stdlib_policy)
        or payload.get("control_contract_descriptor_sha256") != control_contract_descriptor_sha256()
    ):
        raise FormalBootstrapRenderError("stdlib policy rejected")
    if payload.get("supervisor_protocol") != _supervisor_protocol_descriptor():
        raise FormalBootstrapRenderError("supervisor protocol rejected")
    _verify_rsa3072_signature(
        _canonical_bytes(payload),
        _decoded_signature(outer.get("signature_base64"), label="authorization"),
        public_der=trusted_public_key_spki_der,
        label="authorization",
    )
    runner_entry = next(
        (
            item
            for item in payload["source_manifest"]
            if type(item) is dict and item.get("path") == "app/factor_v3_daily_basic_runner.py"
        ),
        None,
    )
    if runner_entry is None or runner_entry.get("sha256") != payload["formal_runner_sha256"]:
        raise FormalBootstrapRenderError("formal runner sha256 semantic field rejected")
    return dict(payload), raw, digest


def _authorized_config(
    *,
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    payload, _authorization_raw, authorization_sha256 = _validated_execution_authorization(
        authorization_path,
        trusted_public_key_spki_der,
    )
    legacy = {
        "action": payload["action"],
        "base_python_executable_path": payload["base_python_executable_path"],
        "base_python_executable_sha256": payload["base_python_executable_sha256"],
        "bootstrap_claim_path": payload["bootstrap_claim_path"],
        "bootstrap_claim_sha256": payload["bootstrap_claim_sha256"],
        "builder_relative_path": payload["builder_relative_path"],
        "builder_sha256": payload["builder_sha256"],
        "expected_branch": payload["expected_branch"],
        "expected_commit": payload["expected_commit"],
        "formal_input_root": payload["formal_input_root_path"],
        "formal_input_root_sha256": payload["formal_input_root_sha256"],
        "formal_output_root": payload["formal_output_root"],
        "git_executable_path": payload["git_executable_path"],
        "git_executable_sha256": payload["git_executable_sha256"],
        "project_id": payload["project_id"],
        "python_executable_path": payload["python_executable_path"],
        "python_executable_sha256": payload["python_executable_sha256"],
        "repo_root": payload["repo_root"],
        "review_payload_sha256": payload["review_payload_sha256"],
        "review_protocol_sha256": payload["review_protocol_sha256"],
        "review_public_key_spki_der_base64": payload["review_public_key_spki_der_base64"],
        "review_public_key_spki_sha256": payload["review_public_key_spki_sha256"],
        "review_receipt_path": payload["review_receipt_path"],
        "review_receipt_sha256": payload["review_receipt_sha256"],
        "run_root": payload["run_root"],
        "run_spec_path": payload["run_spec_path"],
        "schema": CONFIG_SCHEMA,
        "shim_relative_path": payload["shim_relative_path"],
        "shim_sha256": payload["shim_sha256"],
        "source_manifest": payload["source_manifest"],
        "source_root_sha256": payload["source_root_sha256"],
    }
    config = _validated_config(legacy)
    bootstrap_output_root = _safe_existing_directory(
        _absolute_path(payload["bootstrap_output_root"], label="bootstrap output root"),
        label="bootstrap output root",
    )
    if str(bootstrap_output_root) != payload["bootstrap_output_root"]:
        raise FormalBootstrapRenderError("bootstrap output root rejected")
    run_spec_raw = _read_safe_file(
        _absolute_path(payload["run_spec_path"], label="run spec path"),
        label="run spec",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    if hashlib.sha256(run_spec_raw).hexdigest() != payload["run_spec_sha256"]:
        raise FormalBootstrapRenderError("run spec identity rejected")
    claim_raw = _read_safe_file(
        _absolute_path(payload["bootstrap_claim_path"], label="bootstrap claim path"),
        label="bootstrap claim",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    receipt_raw = _read_safe_file(
        _absolute_path(payload["review_receipt_path"], label="review receipt path"),
        label="review receipt",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    _validate_claim_and_review_receipt(
        payload,
        claim_raw=claim_raw,
        receipt_raw=receipt_raw,
    )
    public_der_sha256 = hashlib.sha256(trusted_public_key_spki_der).hexdigest()
    config.update(
        {
            "execution_authorization_path": str(Path(authorization_path)),
            "execution_authorization_sha256": authorization_sha256,
            "execution_authorization_public_key_spki_der_base64": base64.b64encode(
                trusted_public_key_spki_der
            ).decode("ascii"),
            "execution_authorization_public_key_spki_sha256": public_der_sha256,
            "feature_attestation_sha256": payload["feature_attestation_sha256"],
            "formal_runner_sha256": payload["formal_runner_sha256"],
            "authorization_id_sha256": payload["authorization_id_sha256"],
            "authorization_issued_at_utc": payload["issued_at_utc"],
            "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
            "bootstrap_output_root": payload["bootstrap_output_root"],
            "control_contract_descriptor_sha256": payload["control_contract_descriptor_sha256"],
            "execution_authorization_key_id": payload["execution_authorization_key_id"],
            "execution_authorization_key_role": payload["execution_authorization_key_role"],
            "expires_at_utc": payload["expires_at_utc"],
            "not_before_utc": payload["not_before_utc"],
            "replay_scope": payload["replay_scope"],
            "run_spec_sha256": payload["run_spec_sha256"],
            "runtime_template_sha256": payload["runtime_template_sha256"],
            "stdlib_policy": _compact_stdlib_policy_binding(payload["stdlib_policy"]),
            "stdlib_policy_root_sha256": payload["stdlib_policy_root_sha256"],
            "supervisor_protocol": payload["supervisor_protocol"],
        }
    )
    if set(config) != _AUTHORIZED_CONFIG_FIELDS:
        raise FormalBootstrapRenderError("authorized bootstrap configuration rejected")
    return config


def _runtime_template_bytes() -> bytes:
    path = Path(__file__).resolve().with_name(_RUNTIME_TEMPLATE_NAME)
    raw = _canonical_runtime_template_bytes(
        _read_safe_file(
            path,
            label="bootstrap runtime template",
            max_bytes=1024 * 1024,
        )
    )
    if (
        hashlib.sha256(raw).hexdigest() != RUNTIME_TEMPLATE_SHA256
        or raw.count(_CONFIG_MARKER) != 1
        or raw.count(_CONTROL_CONTRACT_MARKER) != 1
        or raw.count(_EARLY_STDLIB_ENTRIES_MARKER) != 1
    ):
        raise FormalBootstrapRenderError("bootstrap runtime template rejected")
    return raw


def _canonical_runtime_template_bytes(raw: bytes) -> bytes:
    if type(raw) is not bytes or not raw or b"\x00" in raw:
        raise FormalBootstrapRenderError("bootstrap runtime template line endings rejected")
    if b"\r" not in raw:
        return raw
    without_crlf = raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf or b"\n" in without_crlf:
        raise FormalBootstrapRenderError("bootstrap runtime template line endings rejected")
    return raw.replace(b"\r\n", b"\n")


def _render_embedded_config(config: Mapping[str, Any]) -> bytes:
    config_raw = _canonical_bytes(config)
    if not config_raw or len(config_raw) > _MAX_CONFIG_BYTES:
        raise FormalBootstrapRenderError("bootstrap configuration rejected")
    replacement = _RENDERED_CONFIG_PREFIX + repr(config_raw).encode("ascii")
    runtime = _runtime_template_bytes().replace(_CONFIG_MARKER, replacement)
    control_raw = _canonical_bytes(control_contract_descriptor())
    runtime = runtime.replace(
        _CONTROL_CONTRACT_MARKER,
        _RENDERED_CONTROL_CONTRACT_PREFIX + repr(control_raw).encode("ascii"),
    )
    stdlib_policy = config.get("stdlib_policy")
    if type(stdlib_policy) is not dict or type(stdlib_policy.get("entries")) is not list:
        raise FormalBootstrapRenderError("stdlib policy rejected")
    early_entries = tuple(
        dict(entry)
        for entry in stdlib_policy["entries"]
        if type(entry) is dict
        and entry.get("module") is not None
        and entry.get("kind") in {"source", "extension"}
    )
    if not early_entries:
        raise FormalBootstrapRenderError("stdlib policy rejected")
    runtime = runtime.replace(
        _EARLY_STDLIB_ENTRIES_MARKER,
        _RENDERED_EARLY_STDLIB_ENTRIES_PREFIX + ascii(early_entries).encode("ascii"),
    )
    runtime = runtime.rstrip(b"\r\n")
    rendered = runtime
    if not rendered or rendered.endswith(b"\n") or len(rendered) > _MAX_RUNTIME_BYTES:
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    try:
        compile(rendered, "<factor-v3-formal-bootstrap>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap rejected") from exc
    return rendered


def render_factor_v3_formal_bootstrap(
    _configuration: Mapping[str, Any],
) -> bytes:
    raise FormalBootstrapRenderError(
        "execution authorization is required; naked configuration rejected"
    )


def render_factor_v3_formal_bootstrap_from_authorization(
    *,
    authorization_path: Path | str,
) -> bytes:
    return _render_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=(_production_execution_authorization_public_key_der()),
    )


def validate_factor_v3_formal_bootstrap_execution_authorization(
    *,
    authorization_path: Path | str,
) -> dict[str, Any]:
    return _authorized_config(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=(_production_execution_authorization_public_key_der()),
    )


def _render_factor_v3_formal_bootstrap_with_test_trust(
    *,
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> bytes:
    return _render_embedded_config(
        _authorized_config(
            authorization_path=authorization_path,
            trusted_public_key_spki_der=trusted_public_key_spki_der,
        )
    )


def _write_all(
    descriptor: int,
    raw: bytes,
    *,
    writer: Any = os.write,
) -> None:
    view = memoryview(raw)
    offset = 0
    while offset < len(view):
        try:
            written = writer(descriptor, view[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if type(written) is not int or isinstance(written, bool) or written <= 0:
            raise FormalBootstrapRenderError("content-addressed write rejected")
        offset += written


def _ensure_safe_child_directory(root: Path, parts: tuple[str, ...]) -> Path:
    current = _safe_existing_directory(root, label="bootstrap output root")
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part:
            raise FormalBootstrapRenderError("bootstrap output directory rejected")
        candidate = current / part
        try:
            os.mkdir(candidate)
        except FileExistsError:
            pass
        except OSError as exc:
            raise FormalBootstrapRenderError("bootstrap output directory rejected") from exc
        try:
            metadata = candidate.lstat()
        except OSError:
            raise FormalBootstrapRenderError("bootstrap output directory rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(candidate):
            raise FormalBootstrapRenderError("bootstrap output directory rejected")
        current = candidate
    return current


@contextmanager
def _held_win32_directory_chain(
    *,
    root: Path,
    directories: tuple[Path, ...],
) -> Any:
    if os.name != "nt" or not directories:
        raise FormalBootstrapRenderError("Win32 directory handle chain rejected")
    trusted_root = _safe_existing_directory(
        root,
        label="directory handle root",
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class FileTime(ctypes.Structure):
        _fields_ = (
            ("low", wintypes.DWORD),
            ("high", wintypes.DWORD),
        )

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation_time", FileTime),
            ("last_access_time", FileTime),
            ("last_write_time", FileTime),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("link_count", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )

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
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[tuple[Path, wintypes.HANDLE, dict[str, Any]]] = []
    seen: set[str] = set()

    def information(
        path: Path,
        handle: wintypes.HANDLE,
    ) -> dict[str, Any]:
        value = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(value)):
            raise FormalBootstrapRenderError("Win32 directory handle identity rejected")
        reparse = bool(value.attributes & _REPARSE_ATTRIBUTE)
        if not value.attributes & 0x00000010 or reparse:
            raise FormalBootstrapRenderError("Win32 directory handle identity rejected")
        return {
            "file_id": (int(value.file_index_high) << 32) | int(value.file_index_low),
            "path": str(path),
            "reparse": reparse,
            "volume_serial": int(value.volume_serial),
        }

    def reopen_identity(path: Path) -> dict[str, Any]:
        handle = create_file(
            str(path),
            0x00000080,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        if handle in (None, invalid_handle):
            raise FormalBootstrapRenderError("Win32 directory current path rejected")
        try:
            return information(path, handle)
        finally:
            close_handle(handle)

    requested_paths: list[Path] = []
    root_normalized = os.path.normcase(str(trusted_root))
    for requested in directories:
        path = _safe_existing_directory(
            requested,
            label="held directory",
        )
        normalized = os.path.normcase(str(path))
        try:
            within_root = os.path.commonpath((normalized, root_normalized)) == root_normalized
        except ValueError:
            within_root = False
        if not within_root:
            raise FormalBootstrapRenderError("Win32 directory handle chain rejected")
        current = Path(path.anchor)
        requested_paths.append(current)
        for part in path.parts[1:]:
            current /= part
            requested_paths.append(current)

    try:
        for path in requested_paths:
            normalized = os.path.normcase(str(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            handle = create_file(
                str(path),
                0x80000000,
                0x00000001 | 0x00000002,
                None,
                3,
                0x00200000 | 0x02000000,
                None,
            )
            if handle in (None, invalid_handle):
                raise FormalBootstrapRenderError("Win32 directory handle chain rejected")
            try:
                identity = information(path, handle)
            except BaseException:
                close_handle(handle)
                raise
            handles.append((path, handle, identity))

        class HeldDirectoryChain:
            def __len__(self) -> int:
                return len(handles)

            def __iter__(self) -> Any:
                return iter(identity for _path, _handle, identity in handles)

            def __getitem__(self, index: int) -> dict[str, Any]:
                return handles[index][2]

            def handle_for(self, path: Path) -> wintypes.HANDLE:
                normalized = os.path.normcase(str(path))
                for held_path, handle, _identity in reversed(handles):
                    if os.path.normcase(str(held_path)) == normalized:
                        return handle
                raise FormalBootstrapRenderError("Win32 publication directory handle unavailable")

        yield HeldDirectoryChain()
        for path, handle, identity in handles:
            if information(path, handle) != identity:
                raise FormalBootstrapRenderError("Win32 directory handle chain drifted")
            if reopen_identity(path) != identity:
                raise FormalBootstrapRenderError("Win32 directory handle chain drifted")
    finally:
        for _path, handle, _identity in reversed(handles):
            close_handle(handle)


@contextmanager
def _held_publication_directory_tree(
    *,
    root: Path,
    targets: tuple[tuple[str, str], ...],
) -> Any:
    trusted_root = _safe_existing_directory(
        root,
        label="bootstrap output root",
    )
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    flush_handles: dict[str, wintypes.HANDLE] = {}

    def hold_flush_handle(path: Path) -> None:
        normalized = os.path.normcase(str(path))
        if normalized in flush_handles:
            return
        handle = create_file(
            str(path),
            0x00000002,
            0x00000001 | 0x00000002,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        if handle in (None, invalid_handle):
            raise FormalBootstrapRenderError("Win32 publication directory flush handle rejected")
        flush_handles[normalized] = handle

    with ExitStack() as stack:
        chains = [
            stack.enter_context(
                _held_win32_directory_chain(
                    root=trusted_root,
                    directories=(trusted_root,),
                )
            )
        ]
        held = {os.path.normcase(str(trusted_root))}
        for category, digest in targets:
            current = trusted_root
            for part in (category, "sha256", digest[:2]):
                if not part or part in {".", ".."} or "/" in part or "\\" in part:
                    raise FormalBootstrapRenderError("bootstrap output directory rejected")
                candidate = current / part
                try:
                    os.mkdir(candidate)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise FormalBootstrapRenderError("bootstrap output directory rejected") from exc
                candidate = _safe_existing_directory(
                    candidate,
                    label="bootstrap output directory",
                )
                normalized = os.path.normcase(str(candidate))
                if normalized not in held:
                    chains.append(
                        stack.enter_context(
                            _held_win32_directory_chain(
                                root=trusted_root,
                                directories=(candidate,),
                            )
                        )
                    )
                    held.add(normalized)
                    hold_flush_handle(candidate)
                current = candidate

        class HeldPublicationDirectoryTree:
            def handle_for(self, path: Path) -> wintypes.HANDLE:
                flush_handle = flush_handles.get(os.path.normcase(str(path)))
                if flush_handle is not None:
                    return flush_handle
                for chain in reversed(chains):
                    try:
                        return chain.handle_for(path)
                    except FormalBootstrapRenderError:
                        continue
                raise FormalBootstrapRenderError("Win32 publication directory handle unavailable")

        try:
            yield HeldPublicationDirectoryTree()
        finally:
            for handle in reversed(tuple(flush_handles.values())):
                close_handle(handle)


class _HeldWin32PublishedFile:
    def __init__(self, *, path: Path, descriptor: int) -> None:
        self.path = path
        self.descriptor = descriptor
        self.identity = self._validated_metadata()

    def _validated_metadata(self) -> os.stat_result:
        try:
            metadata = os.fstat(self.descriptor)
        except OSError as exc:
            raise FormalBootstrapRenderError("content-addressed file handle rejected") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(getattr(metadata, "st_nlink", 1)) != 1
            or bool(int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE)
        ):
            raise FormalBootstrapRenderError("content-addressed file handle rejected")
        return metadata

    def write(self, raw: bytes) -> None:
        _write_all(self.descriptor, raw)
        os.fsync(self.descriptor)

    def read(self, *, expected_bytes: int) -> bytes:
        try:
            os.lseek(self.descriptor, 0, os.SEEK_SET)
        except OSError as exc:
            raise FormalBootstrapRenderError("content-addressed file handle rejected") from exc
        remaining = expected_bytes
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(self.descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise FormalBootstrapRenderError("content-addressed file handle rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(self.descriptor, 1):
            raise FormalBootstrapRenderError("content-addressed file handle rejected")
        return b"".join(chunks)

    def terminal_verify(self, expected_raw: bytes) -> None:
        terminal = self._validated_metadata()
        try:
            current = self.path.lstat()
        except OSError as exc:
            raise FormalBootstrapRenderError(
                "content-addressed terminal identity rejected"
            ) from exc
        if (
            not os.path.samestat(self.identity, terminal)
            or not os.path.samestat(terminal, current)
            or terminal.st_size != len(expected_raw)
            or _is_reparse(self.path)
            or not hmac.compare_digest(
                self.read(expected_bytes=len(expected_raw)),
                expected_raw,
            )
        ):
            raise FormalBootstrapRenderError("content-addressed terminal identity rejected")

    def close(self) -> None:
        if self.descriptor >= 0:
            descriptor = self.descriptor
            self.descriptor = -1
            os.close(descriptor)


def _open_held_win32_published_file(
    *,
    path: Path,
    create_if_missing: bool = True,
) -> tuple[_HeldWin32PublishedFile, bool]:
    if os.name != "nt":
        raise FormalBootstrapRenderError("Win32 content-addressed file rejected")
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    common_flags = 0x00200000 | 0x02000000 | 0x80000000
    handle = None
    created = False
    if create_if_missing:
        handle = create_file(
            str(path),
            0x80000000 | 0x40000000,
            0x00000001,
            None,
            1,
            common_flags,
            None,
        )
        created = handle not in (None, invalid_handle)
    if not created:
        error = ctypes.get_last_error()
        if create_if_missing and error not in {80, 183}:
            raise FormalBootstrapRenderError("content-addressed output rejected")
        handle = create_file(
            str(path),
            0x80000000,
            0x00000001,
            None,
            3,
            common_flags,
            None,
        )
        created = False
    if handle in (None, invalid_handle):
        raise FormalBootstrapRenderError("content-addressed output rejected")
    descriptor: int | None = None
    try:
        descriptor = msvcrt.open_osfhandle(
            int(handle),
            ((os.O_RDWR if created else os.O_RDONLY) | getattr(os, "O_BINARY", 0)),
        )
        return (
            _HeldWin32PublishedFile(
                path=path,
                descriptor=descriptor,
            ),
            created,
        )
    except BaseException:
        if descriptor is None:
            close_handle(handle)
        else:
            os.close(descriptor)
        raise


def _flush_held_win32_directory(
    *,
    path: Path,
    handle: wintypes.HANDLE,
) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    if not flush(handle):
        raise FormalBootstrapRenderError(f"Win32 publication directory flush rejected: {path}")


def _safe_cas_publish(
    *,
    root: Path,
    category: str,
    digest: str,
    suffix: str,
    raw: bytes,
    held_files: list[tuple[_HeldWin32PublishedFile, bytes]] | None = None,
) -> tuple[Path, str]:
    if hashlib.sha256(raw).hexdigest() != digest:
        raise FormalBootstrapRenderError("content-addressed payload rejected")
    directory = _ensure_safe_child_directory(
        root,
        (category, "sha256", digest[:2]),
    )
    path = directory / f"{digest}{suffix}"
    held: _HeldWin32PublishedFile | None = None
    retained = False
    try:
        held, created = _open_held_win32_published_file(path=path)
        if created:
            held.write(raw)
        terminal = held.read(expected_bytes=len(raw))
        if not hmac.compare_digest(terminal, raw):
            raise FormalBootstrapRenderError("content-addressed output collision")
        held.terminal_verify(raw)
        if held_files is not None:
            held_files.append((held, raw))
            retained = True
        relative = path.relative_to(root).as_posix()
        return path, relative
    finally:
        if held is not None and not retained:
            held.close()


def _publication_materials(
    *,
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> tuple[dict[str, Any], bytes, bytes, bytes, dict[str, Any]]:
    config = _authorized_config(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    rendered = _render_embedded_config(config)
    validate_rendered_factor_v3_formal_bootstrap(rendered)
    bootstrap_sha256 = hashlib.sha256(rendered).hexdigest()
    bootstrap_relative_path = f"bootstraps/sha256/{bootstrap_sha256[:2]}/{bootstrap_sha256}.py"
    receipt_payload = {
        "action": config["action"],
        "bootstrap_bytes": len(rendered),
        "bootstrap_relative_path": bootstrap_relative_path,
        "bootstrap_sha256": bootstrap_sha256,
        "execution_authorization_sha256": config["execution_authorization_sha256"],
        "runtime_template_sha256": RUNTIME_TEMPLATE_SHA256,
        "schema": PUBLICATION_RECEIPT_SCHEMA,
    }
    receipt_raw = _canonical_bytes(receipt_payload)
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    receipt_relative_path = (
        f"publication_receipts/sha256/{receipt_sha256[:2]}/{receipt_sha256}.json"
    )
    stdlib_policy_raw = _canonical_bytes(config["stdlib_policy"])
    stdlib_policy_sha256 = hashlib.sha256(stdlib_policy_raw).hexdigest()
    stdlib_policy_relative_path = (
        f"stdlib_policies/sha256/{stdlib_policy_sha256[:2]}/{stdlib_policy_sha256}.json"
    )
    completion_payload = {
        "action": config["action"],
        "authorization_id_sha256": config["authorization_id_sha256"],
        "authorization_nonce_sha256": config["authorization_nonce_sha256"],
        "bootstrap_bytes": len(rendered),
        "bootstrap_relative_path": bootstrap_relative_path,
        "bootstrap_sha256": bootstrap_sha256,
        "bootstrap_output_root": config["bootstrap_output_root"],
        "control_contract_descriptor_sha256": config["control_contract_descriptor_sha256"],
        "execution_authorization_sha256": config["execution_authorization_sha256"],
        "receipt_bytes": len(receipt_raw),
        "receipt_relative_path": receipt_relative_path,
        "receipt_sha256": receipt_sha256,
        "runtime_template_sha256": RUNTIME_TEMPLATE_SHA256,
        "schema": COMPLETION_SCHEMA,
        "status": "completed",
        "stdlib_inventory_root_sha256": config["stdlib_policy_root_sha256"],
        "stdlib_policy_bytes": len(stdlib_policy_raw),
        "stdlib_policy_relative_path": stdlib_policy_relative_path,
        "stdlib_policy_sha256": stdlib_policy_sha256,
    }
    return config, rendered, receipt_raw, stdlib_policy_raw, completion_payload


def _validated_completion_authorization(
    path_value: Path | str,
    *,
    expected_payload: Mapping[str, Any],
    trusted_public_key_spki_der: bytes,
) -> tuple[bytes, str]:
    path = _absolute_path(
        str(path_value),
        label="completion authorization path",
    )
    raw = _read_safe_file(
        path,
        label="completion authorization",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    digest = hashlib.sha256(raw).hexdigest()
    _validate_cas_path(path, digest, label="completion authorization")
    outer = _strict_canonical_json(raw)
    if set(outer) != {"payload", "signature_base64"} or outer.get("payload") != dict(
        expected_payload
    ):
        raise FormalBootstrapRenderError("completion authorization rejected")
    _verify_rsa3072_signature(
        _canonical_bytes(dict(expected_payload)),
        _decoded_signature(
            outer.get("signature_base64"),
            label="completion authorization",
        ),
        public_der=trusted_public_key_spki_der,
        label="completion authorization",
    )
    return raw, digest


def _plan_factor_v3_formal_bootstrap_publication_with_test_trust(
    *,
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    _config, _rendered, _receipt_raw, _stdlib_policy_raw, completion_payload = (
        _publication_materials(
            authorization_path=authorization_path,
            trusted_public_key_spki_der=trusted_public_key_spki_der,
        )
    )
    return completion_payload


def plan_factor_v3_formal_bootstrap_publication(
    *,
    authorization_path: Path | str,
) -> dict[str, Any]:
    return _plan_factor_v3_formal_bootstrap_publication_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=(_production_execution_authorization_public_key_der()),
    )


def _publish_prevalidated_completion_for_test(
    *,
    root: Path,
    bootstrap_raw: bytes,
    receipt_raw: bytes,
    stdlib_policy_raw: bytes,
    completion_raw: bytes,
) -> dict[str, tuple[Path, str]]:
    bootstrap_sha256 = hashlib.sha256(bootstrap_raw).hexdigest()
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    stdlib_policy_sha256 = hashlib.sha256(stdlib_policy_raw).hexdigest()
    completion_sha256 = hashlib.sha256(completion_raw).hexdigest()
    held_files: list[tuple[_HeldWin32PublishedFile, bytes]] = []
    with _held_publication_directory_tree(
        root=root,
        targets=(
            ("bootstraps", bootstrap_sha256),
            ("publication_receipts", receipt_sha256),
            ("stdlib_policies", stdlib_policy_sha256),
            ("completion_markers", completion_sha256),
        ),
    ) as directories:
        try:
            bootstrap = _safe_cas_publish(
                root=root,
                category="bootstraps",
                digest=bootstrap_sha256,
                suffix=".py",
                raw=bootstrap_raw,
                held_files=held_files,
            )
            receipt = _safe_cas_publish(
                root=root,
                category="publication_receipts",
                digest=receipt_sha256,
                suffix=".json",
                raw=receipt_raw,
                held_files=held_files,
            )
            stdlib_policy = _safe_cas_publish(
                root=root,
                category="stdlib_policies",
                digest=stdlib_policy_sha256,
                suffix=".json",
                raw=stdlib_policy_raw,
                held_files=held_files,
            )
            completion = _safe_cas_publish(
                root=root,
                category="completion_markers",
                digest=completion_sha256,
                suffix=".json",
                raw=completion_raw,
                held_files=held_files,
            )
            if held_files:
                flushed: set[str] = set()
                for held, _expected_raw in held_files:
                    parent = held.path.parent
                    normalized = os.path.normcase(str(parent))
                    if normalized in flushed:
                        continue
                    _flush_held_win32_directory(
                        path=parent,
                        handle=directories.handle_for(parent),
                    )
                    flushed.add(normalized)
                for held, expected_raw in held_files:
                    held.terminal_verify(expected_raw)
        finally:
            for held, _expected_raw in reversed(held_files):
                held.close()
    return {
        "bootstrap": bootstrap,
        "completion": completion,
        "receipt": receipt,
        "stdlib_policy": stdlib_policy,
    }


def _publish_factor_v3_formal_bootstrap_with_test_trust(
    *,
    authorization_path: Path | str,
    completion_authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    config, rendered, receipt_raw, stdlib_policy_raw, completion_payload = _publication_materials(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    completion_raw, completion_sha256 = _validated_completion_authorization(
        completion_authorization_path,
        expected_payload=completion_payload,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    root = _safe_existing_directory(
        Path(str(config["bootstrap_output_root"])),
        label="bootstrap output root",
    )
    published = _publish_prevalidated_completion_for_test(
        root=root,
        bootstrap_raw=rendered,
        receipt_raw=receipt_raw,
        stdlib_policy_raw=stdlib_policy_raw,
        completion_raw=completion_raw,
    )
    bootstrap_path, bootstrap_relative_path = published["bootstrap"]
    receipt_path, receipt_relative_path = published["receipt"]
    completion_path, completion_relative_path = published["completion"]
    stdlib_policy_path, stdlib_policy_relative_path = published["stdlib_policy"]
    if (
        bootstrap_relative_path != completion_payload["bootstrap_relative_path"]
        or receipt_relative_path != completion_payload["receipt_relative_path"]
        or stdlib_policy_relative_path != completion_payload["stdlib_policy_relative_path"]
    ):
        raise FormalBootstrapRenderError("publication path identity rejected")
    return {
        **completion_payload,
        "bootstrap_path": str(bootstrap_path),
        "completion_authorization_sha256": completion_sha256,
        "completion_marker_path": str(completion_path),
        "completion_marker_relative_path": completion_relative_path,
        "receipt_path": str(receipt_path),
        "stdlib_policy_path": str(stdlib_policy_path),
    }


def publish_factor_v3_formal_bootstrap(
    *,
    authorization_path: Path | str,
    completion_authorization_path: Path | str,
) -> dict[str, Any]:
    return _publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_authorization_path,
        trusted_public_key_spki_der=(_production_execution_authorization_public_key_der()),
    )


def _validated_completion_marker_payload(value: Any) -> dict[str, Any]:
    fields = {
        "action",
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "bootstrap_bytes",
        "bootstrap_output_root",
        "bootstrap_relative_path",
        "bootstrap_sha256",
        "control_contract_descriptor_sha256",
        "execution_authorization_sha256",
        "receipt_bytes",
        "receipt_relative_path",
        "receipt_sha256",
        "runtime_template_sha256",
        "schema",
        "status",
        "stdlib_inventory_root_sha256",
        "stdlib_policy_bytes",
        "stdlib_policy_relative_path",
        "stdlib_policy_sha256",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value.get("schema") != COMPLETION_SCHEMA
        or value.get("status") != "completed"
        or value.get("action") not in {"build-spec", "run", "verify"}
        or value.get("runtime_template_sha256") != RUNTIME_TEMPLATE_SHA256
    ):
        raise FormalBootstrapRenderError("completion marker payload rejected")
    payload = json.loads(_canonical_bytes(value))
    _reject_credential_shape(payload)
    for field in (
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "bootstrap_sha256",
        "control_contract_descriptor_sha256",
        "execution_authorization_sha256",
        "receipt_sha256",
        "runtime_template_sha256",
        "stdlib_inventory_root_sha256",
        "stdlib_policy_sha256",
    ):
        _require_sha256(payload.get(field), label=f"completion marker {field}")
    for field in ("bootstrap_bytes", "receipt_bytes", "stdlib_policy_bytes"):
        size = payload.get(field)
        if type(size) is not int or isinstance(size, bool) or not 0 < size <= _MAX_RUNTIME_BYTES:
            raise FormalBootstrapRenderError("completion marker payload rejected")
    root = _absolute_path(
        payload.get("bootstrap_output_root"),
        label="completion marker bootstrap output root",
    )
    expected_bootstrap = (
        f"bootstraps/sha256/{payload['bootstrap_sha256'][:2]}/{payload['bootstrap_sha256']}.py"
    )
    expected_receipt = (
        f"publication_receipts/sha256/{payload['receipt_sha256'][:2]}/"
        f"{payload['receipt_sha256']}.json"
    )
    expected_stdlib_policy = (
        f"stdlib_policies/sha256/{payload['stdlib_policy_sha256'][:2]}/"
        f"{payload['stdlib_policy_sha256']}.json"
    )
    if (
        payload.get("bootstrap_relative_path") != expected_bootstrap
        or payload.get("receipt_relative_path") != expected_receipt
        or payload.get("stdlib_policy_relative_path") != expected_stdlib_policy
        or payload.get("control_contract_descriptor_sha256") != control_contract_descriptor_sha256()
    ):
        raise FormalBootstrapRenderError("completion marker payload rejected")
    payload["bootstrap_output_root"] = str(root)
    return payload


def _opened_completion_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[_HeldWin32PublishedFile, bytes]:
    held, created = _open_held_win32_published_file(
        path=path,
        create_if_missing=False,
    )
    if created:
        held.close()
        raise FormalBootstrapRenderError(f"{label} rejected")
    try:
        byte_count = int(held.identity.st_size)
        if not 0 < byte_count <= max_bytes:
            raise FormalBootstrapRenderError(f"{label} rejected")
        raw = held.read(expected_bytes=byte_count)
        held.terminal_verify(raw)
        return held, raw
    except BaseException:
        held.close()
        raise


def _validate_factor_v3_formal_bootstrap_completion_marker_with_test_trust(
    *,
    completion_marker_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    marker_path = _absolute_path(
        str(completion_marker_path),
        label="completion marker path",
    )
    if (
        marker_path.suffix != ".json"
        or _SHA256_RE.fullmatch(marker_path.stem) is None
        or marker_path.parent.parent.parent.name != "completion_markers"
    ):
        raise FormalBootstrapRenderError("completion marker CAS rejected")
    marker_digest = marker_path.stem
    _validate_cas_path(
        marker_path,
        marker_digest,
        label="completion marker",
    )
    marker, marker_raw = _opened_completion_file(
        marker_path,
        label="completion marker",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    held_files: list[tuple[_HeldWin32PublishedFile, bytes]] = [(marker, marker_raw)]
    try:
        if hashlib.sha256(marker_raw).hexdigest() != marker_digest:
            raise FormalBootstrapRenderError("completion marker CAS rejected")
        outer = _strict_canonical_json(marker_raw)
        if set(outer) != {"payload", "signature_base64"}:
            raise FormalBootstrapRenderError("completion marker rejected")
        payload = _validated_completion_marker_payload(outer.get("payload"))
        _verify_rsa3072_signature(
            _canonical_bytes(payload),
            _decoded_signature(
                outer.get("signature_base64"),
                label="completion marker",
            ),
            public_der=trusted_public_key_spki_der,
            label="completion marker",
        )
        root = _safe_existing_directory(
            Path(payload["bootstrap_output_root"]),
            label="completion marker bootstrap output root",
        )
        expected_marker = (
            root / "completion_markers" / "sha256" / marker_digest[:2] / f"{marker_digest}.json"
        )
        if os.path.normcase(str(marker_path)) != os.path.normcase(str(expected_marker)):
            raise FormalBootstrapRenderError("completion marker root rejected")
        bootstrap_path = root / Path(*payload["bootstrap_relative_path"].split("/"))
        receipt_path = root / Path(*payload["receipt_relative_path"].split("/"))
        stdlib_policy_path = root / Path(*payload["stdlib_policy_relative_path"].split("/"))
        with _held_win32_directory_chain(
            root=root,
            directories=(
                marker_path.parent,
                bootstrap_path.parent,
                receipt_path.parent,
                stdlib_policy_path.parent,
            ),
        ):
            bootstrap, bootstrap_raw = _opened_completion_file(
                bootstrap_path,
                label="completion bootstrap",
                max_bytes=_MAX_RUNTIME_BYTES,
            )
            held_files.append((bootstrap, bootstrap_raw))
            receipt, receipt_raw = _opened_completion_file(
                receipt_path,
                label="completion receipt",
                max_bytes=_MAX_CONFIG_BYTES,
            )
            held_files.append((receipt, receipt_raw))
            try:
                stdlib_policy, stdlib_policy_raw = _opened_completion_file(
                    stdlib_policy_path,
                    label="completion stdlib policy",
                    max_bytes=_MAX_CONFIG_BYTES,
                )
            except FormalBootstrapRenderError as exc:
                raise FormalBootstrapRenderError("completion stdlib policy rejected") from exc
            held_files.append((stdlib_policy, stdlib_policy_raw))
            if (
                len(bootstrap_raw) != payload["bootstrap_bytes"]
                or hashlib.sha256(bootstrap_raw).hexdigest() != payload["bootstrap_sha256"]
                or len(receipt_raw) != payload["receipt_bytes"]
                or hashlib.sha256(receipt_raw).hexdigest() != payload["receipt_sha256"]
                or len(stdlib_policy_raw) != payload["stdlib_policy_bytes"]
                or hashlib.sha256(stdlib_policy_raw).hexdigest() != payload["stdlib_policy_sha256"]
            ):
                raise FormalBootstrapRenderError("completion content rejected")
            validate_rendered_factor_v3_formal_bootstrap(bootstrap_raw)
            receipt_payload = _strict_canonical_json(receipt_raw)
            expected_receipt = {
                "action": payload["action"],
                "bootstrap_bytes": payload["bootstrap_bytes"],
                "bootstrap_relative_path": payload["bootstrap_relative_path"],
                "bootstrap_sha256": payload["bootstrap_sha256"],
                "execution_authorization_sha256": payload["execution_authorization_sha256"],
                "runtime_template_sha256": payload["runtime_template_sha256"],
                "schema": PUBLICATION_RECEIPT_SCHEMA,
            }
            if receipt_payload != expected_receipt:
                raise FormalBootstrapRenderError("completion receipt rejected")
            try:
                published_policy = _strict_canonical_json(stdlib_policy_raw)
                normalized_policy = validate_stdlib_policy(
                    published_policy,
                    expected_root_sha256=payload["stdlib_inventory_root_sha256"],
                )
            except (FormalBootstrapRenderError, ValueError) as exc:
                raise FormalBootstrapRenderError("completion stdlib policy rejected") from exc
            if normalized_policy != published_policy:
                raise FormalBootstrapRenderError("completion stdlib policy rejected")
            for held, expected_raw in held_files:
                held.terminal_verify(expected_raw)
            return {
                **payload,
                "bootstrap_path": str(bootstrap_path),
                "completion_marker_path": str(marker_path),
                "completion_marker_sha256": marker_digest,
                "receipt_path": str(receipt_path),
                "stdlib_policy_path": str(stdlib_policy_path),
            }
    finally:
        for held, _expected_raw in reversed(held_files):
            held.close()


def validate_factor_v3_formal_bootstrap_completion_marker(
    *,
    completion_marker_path: Path | str,
) -> dict[str, Any]:
    return _validate_factor_v3_formal_bootstrap_completion_marker_with_test_trust(
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=(_production_execution_authorization_public_key_der()),
    )


def validate_rendered_factor_v3_formal_bootstrap(raw: bytes) -> bytes:
    if (
        type(raw) is not bytes
        or not raw
        or raw.endswith(b"\n")
        or len(raw) > _MAX_RUNTIME_BYTES
        or not raw.startswith(b"# ruff: noqa: E402\nfrom __future__ import annotations")
    ):
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename="<factor-v3-formal-bootstrap>")
    except (
        UnicodeDecodeError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap rejected") from exc
    values = []
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_EMBEDDED_CONFIG_JSON"
            and node.value is not None
        ):
            try:
                values.append(ast.literal_eval(node.value))
            except (ValueError, SyntaxError):
                raise FormalBootstrapRenderError("rendered bootstrap rejected") from None
    if len(values) != 1 or type(values[0]) is not bytes:
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    config = _strict_canonical_json(values[0])
    if set(config) != _AUTHORIZED_CONFIG_FIELDS:
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    expected = _render_embedded_config(config)
    if expected != raw:
        raise FormalBootstrapRenderError("rendered bootstrap drifted")
    return raw
