from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any


class FactorV3FormalNativeBrokerError(RuntimeError):
    pass


BROKER_CANDIDATE_SCHEMA = "factor-v3-formal-native-broker-candidate/v1"
BROKER_CHILD_PROTOCOL = "factor-v3-formal-native-broker-child/v1"
RUNTIME_MANIFEST_SCHEMA = "factor-v3-formal-native-broker-runtime-manifest/v1"
SOURCE_MANIFEST_SCHEMA = "factor-v3-formal-native-broker-source-manifest/v1"
SIGNING_KEY_SLOT_ID = "factor-v3-execution-authorization"
CREDENTIAL_SLOT_ID = "points-primary"

_ACTIONS = {"build-spec", "resume", "run", "verify"}
_CANDIDATE_FIELDS = (
    "schema",
    "action",
    "authorization_path",
    "completion_marker_path",
    "publication_receipt_path",
    "execution_ledger_root",
    "resume_authorization_path",
    "resume_status_path",
    "signing_key_slot_id",
    "credential_slot_id",
    "runtime_manifest_schema",
    "source_manifest_schema",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _path_text(value: Path | str, *, label: str) -> str:
    if not isinstance(value, (Path, str)):
        raise FactorV3FormalNativeBrokerError(f"{label} candidate path rejected")
    original = str(value)
    path = Path(value)
    text = str(path)
    if (
        original != text
        or len(text) < 4
        or not text[0].isalpha()
        or text[1:3] != ":\\"
        or "/" in text
        or ":" in text[2:]
    ):
        raise FactorV3FormalNativeBrokerError(f"{label} candidate path rejected")
    components = text[3:].split("\\")
    if not components or any(
        not component
        or component in {".", ".."}
        or component[-1] in {".", " "}
        or "~" in component
        or any(ord(character) < 32 or ord(character) == 127 for character in component)
        or component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        for component in components
    ):
        raise FactorV3FormalNativeBrokerError(f"{label} candidate path rejected")
    return text


def _candidate_bytes(fields: dict[str, str]) -> bytes:
    if tuple(fields) != _CANDIDATE_FIELDS:
        raise FactorV3FormalNativeBrokerError("native broker candidate fields rejected")
    for value in fields.values():
        if type(value) is not str or any(marker in value for marker in ("\x00", "\r", "\n")):
            raise FactorV3FormalNativeBrokerError("native broker candidate value rejected")
    return "".join(f"{name}={fields[name]}\n" for name in _CANDIDATE_FIELDS).encode(
        "utf-8"
    )


def _validated_candidate(raw: bytes) -> dict[str, str]:
    if type(raw) is not bytes or not raw or len(raw) > 64 * 1024:
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected") from exc
    if "\r" in text or "\x00" in text or not text.endswith("\n"):
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    lines = text[:-1].split("\n")
    if len(lines) != len(_CANDIDATE_FIELDS):
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    fields: dict[str, str] = {}
    for expected, line in zip(_CANDIDATE_FIELDS, lines, strict=True):
        name, separator, value = line.partition("=")
        if separator != "=" or name != expected:
            raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
        fields[name] = value
    if _candidate_bytes(fields) != raw:
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    if (
        fields["schema"] != BROKER_CANDIDATE_SCHEMA
        or fields["action"] not in _ACTIONS
        or fields["signing_key_slot_id"] != SIGNING_KEY_SLOT_ID
        or fields["runtime_manifest_schema"] != RUNTIME_MANIFEST_SCHEMA
        or fields["source_manifest_schema"] != SOURCE_MANIFEST_SCHEMA
    ):
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    expected_credential = (
        CREDENTIAL_SLOT_ID if fields["action"] in {"run", "resume"} else "none"
    )
    if fields["credential_slot_id"] != expected_credential:
        raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    for name in (
        "authorization_path",
        "completion_marker_path",
        "publication_receipt_path",
        "execution_ledger_root",
    ):
        if _path_text(fields[name], label=name) != fields[name]:
            raise FactorV3FormalNativeBrokerError("native broker candidate rejected")
    resume_fields = (
        fields["resume_authorization_path"],
        fields["resume_status_path"],
    )
    if fields["action"] == "resume":
        if any(not value for value in resume_fields):
            raise FactorV3FormalNativeBrokerError("native broker resume candidate rejected")
        for name in ("resume_authorization_path", "resume_status_path"):
            if _path_text(fields[name], label=name) != fields[name]:
                raise FactorV3FormalNativeBrokerError(
                    "native broker resume candidate rejected"
                )
    elif any(resume_fields):
        raise FactorV3FormalNativeBrokerError("native broker resume candidate rejected")
    return fields


def build_factor_v3_formal_native_broker_candidate(
    *,
    action: str,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    execution_ledger_root: Path | str,
    resume_authorization_path: Path | str | None = None,
    resume_status_path: Path | str | None = None,
) -> bytes:
    if action not in _ACTIONS:
        raise FactorV3FormalNativeBrokerError("native broker candidate action rejected")
    if action == "resume":
        if resume_authorization_path is None or resume_status_path is None:
            raise FactorV3FormalNativeBrokerError(
                "native broker resume candidate rejected"
            )
        resume_authorization = _path_text(
            resume_authorization_path,
            label="resume authorization",
        )
        resume_status = _path_text(resume_status_path, label="resume status")
    else:
        if resume_authorization_path is not None or resume_status_path is not None:
            raise FactorV3FormalNativeBrokerError(
                "native broker resume candidate rejected"
            )
        resume_authorization = ""
        resume_status = ""
    raw = _candidate_bytes(
        {
            "schema": BROKER_CANDIDATE_SCHEMA,
            "action": action,
            "authorization_path": _path_text(
                authorization_path,
                label="bootstrap authorization",
            ),
            "completion_marker_path": _path_text(
                completion_marker_path,
                label="bootstrap completion",
            ),
            "publication_receipt_path": _path_text(
                publication_receipt_path,
                label="supervisor publication",
            ),
            "execution_ledger_root": _path_text(
                execution_ledger_root,
                label="execution ledger",
            ),
            "resume_authorization_path": resume_authorization,
            "resume_status_path": resume_status,
            "signing_key_slot_id": SIGNING_KEY_SLOT_ID,
            "credential_slot_id": (
                CREDENTIAL_SLOT_ID if action in {"run", "resume"} else "none"
            ),
            "runtime_manifest_schema": RUNTIME_MANIFEST_SCHEMA,
            "source_manifest_schema": SOURCE_MANIFEST_SCHEMA,
        }
    )
    _validated_candidate(raw)
    return raw


def publish_factor_v3_formal_native_broker_candidate(
    *,
    candidate_output_root: Path | str,
    candidate: bytes,
    expected_candidate_path: Path | str | None = None,
) -> dict[str, Any]:
    _validated_candidate(candidate)
    root = Path(_path_text(candidate_output_root, label="candidate output root"))
    digest = hashlib.sha256(candidate).hexdigest()
    if _SHA256_RE.fullmatch(digest) is None:
        raise FactorV3FormalNativeBrokerError("native broker candidate digest rejected")
    path = (
        root
        / "candidates"
        / "sha256"
        / digest[:2]
        / f"{digest}.candidate"
    )
    if expected_candidate_path is not None and (
        _path_text(expected_candidate_path, label="expected candidate") != str(path)
    ):
        raise FactorV3FormalNativeBrokerError("native broker candidate path rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        try:
            offset = 0
            while offset < len(candidate):
                written = os.write(descriptor, candidate[offset:])
                if written <= 0:
                    raise FactorV3FormalNativeBrokerError(
                        "native broker candidate publication rejected"
                    )
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    try:
        metadata = path.lstat()
        observed = path.read_bytes()
    except OSError as exc:
        raise FactorV3FormalNativeBrokerError(
            "native broker candidate publication rejected"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or int(getattr(metadata, "st_nlink", 1)) != 1
        or observed != candidate
        or hashlib.sha256(observed).hexdigest() != digest
    ):
        raise FactorV3FormalNativeBrokerError(
            "native broker candidate publication rejected"
        )
    return {
        "candidate_path": str(path),
        "candidate_sha256": digest,
        "status": "published",
    }
