"""Small, dependency-free READY/ACK verifier for supervised launch children."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Mapping


def _strict_json_loads(raw: bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON object contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"JSON contains a non-finite value: {value}")

    return json.loads(
        raw,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


def _ack_contract(ready_payload: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    ready_schema = ready_payload.get("schema_version")
    if ready_schema == "research-launcher-ready/v1":
        return (
            "research-launcher-ready-ack/v1",
            (
                "plan_sha256",
                "plan_file_sha256",
                "fixture_id",
                "fixture_manifest_file_sha256",
            ),
        )
    if ready_schema in {
        "research-launcher-ready/v2",
        "research-launcher-ready/v3",
        "research-launcher-ready/v4",
        "research-launcher-ready/v5",
    }:
        binding_keys = (
            "experiment_id",
            "registered_record_hash",
            "registered_sequence",
            "registration_contract_sha256",
            "publication_id",
            "publication_result_file_sha256",
            "plan_sha256",
            "plan_file_sha256",
            "fixture_id",
            "fixture_manifest_file_sha256",
            "run_claim_file_sha256",
            "control_source_bundle_sha256",
            *(
                ("ledger_path_sha256", "launch_lease_file_sha256")
                if ready_schema
                in {
                    "research-launcher-ready/v3",
                    "research-launcher-ready/v4",
                    "research-launcher-ready/v5",
                }
                else ()
            ),
            *(
                (
                    "precompute_launch_started_schema_version",
                    "launch_started_record_hash",
                    "launch_started_sequence",
                    "parent_proof_file_sha256",
                    "parent_proof_canonical_sha256",
                )
                if ready_schema
                in {"research-launcher-ready/v4", "research-launcher-ready/v5"}
                else ()
            ),
            *(
                ("legacy_quarantine_sha256",)
                if ready_schema == "research-launcher-ready/v5"
                else ()
            ),
        )
        return (
            ready_schema.replace(
                "research-launcher-ready/", "research-launcher-ready-ack/"
            ),
            binding_keys,
        )
    raise ValueError("launcher READY contract is invalid")


def wait_for_launcher_ack(
    ready_payload: Mapping[str, object], *, timeout_seconds: float = 30.0
) -> dict:
    ack_value = os.environ.get("RESEARCH_SUPERVISED_READY_ACK_PATH")
    if not isinstance(ack_value, str) or not ack_value:
        raise ValueError("supervised launch ACK path is missing")
    workspace = Path(__file__).resolve().parent.parent
    ack_path = Path(ack_value)
    if not ack_path.is_absolute():
        raise ValueError("supervised launch ACK path must be absolute")
    parent = ack_path.parent.resolve()
    try:
        parent.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("supervised launch ACK path is outside workspace") from exc
    if ack_path.parent != parent or ack_path.parent.is_symlink() or not parent.is_dir():
        raise ValueError("supervised launch ACK parent is unsafe")
    deadline = time.monotonic() + timeout_seconds
    while not ack_path.exists():
        if time.monotonic() >= deadline:
            raise ValueError("supervised launch ACK timeout")
        time.sleep(0.01)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(ack_path), flags)
    except OSError as exc:
        raise ValueError("supervised launch ACK is missing or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > 4096
        ):
            raise ValueError("supervised launch ACK size or type is invalid")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise ValueError("supervised launch ACK changed while being read")
    finally:
        os.close(descriptor)
    try:
        payload = _strict_json_loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("supervised launch ACK is invalid") from exc
    ack_schema, binding_keys = _ack_contract(ready_payload)
    expected = {
        "schema_version": ack_schema,
        "event": "READY_ACK",
        **{key: ready_payload[key] for key in binding_keys},
    }
    if payload != expected:
        raise ValueError("launcher ACK binding mismatch")
    return payload
