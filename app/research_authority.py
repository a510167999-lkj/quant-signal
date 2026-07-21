"""Versioned authority envelopes for single and ordered-composite PIT stores."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from typing import Any, Mapping


SINGLE_AUTHORITY_KEYS = {
    "artifact_root_sha256",
    "coverage_audit_sha256",
    "temporal_contract_sha256",
    "temporal_role",
    "artifact_manifest_sha256",
    "market_generation_root_sha256",
    "stock_generation_lineage_sha256",
}
COMPOSITE_AUTHORITY_SCHEMA_VERSION = "research-composite-audited-authority/v1"
COMPOSITE_AUTHORITY_KEYS = {
    "schema_version",
    "authority_kind",
    "composite_root_sha256",
    "coverage_audit_root_sha256",
    "calendar_root_sha256",
    "source_manifest_root_sha256",
    "temporal_contract_sha256",
    "temporal_role",
    "composite_authority",
}


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def is_composite_universe(audited_universe: Any) -> bool:
    return (
        getattr(audited_universe, "composite_root_sha256", None) is not None
        and getattr(audited_universe, "composite_authority", None) is not None
    )


def audited_coverage_from_universe(audited_universe: Any) -> dict[str, str]:
    if is_composite_universe(audited_universe):
        coverage = {
            "start_date": str(getattr(audited_universe, "start_date", "")),
            "end_date": str(getattr(audited_universe, "end_date", "")),
        }
    else:
        manifest = getattr(audited_universe, "manifest", None)
        coverage = copy.deepcopy(manifest.get("coverage")) if isinstance(manifest, Mapping) else None
    if (
        not isinstance(coverage, dict)
        or set(coverage) != {"start_date", "end_date"}
        or not all(isinstance(coverage[key], str) and coverage[key] for key in coverage)
        or coverage["start_date"] > coverage["end_date"]
    ):
        raise ValueError("audited authority coverage is invalid")
    return coverage


def _single_authority(audited_universe: Any) -> dict[str, Any]:
    manifest = getattr(audited_universe, "manifest", None)
    if not isinstance(manifest, Mapping):
        raise ValueError("audited authority requires a verified artifact manifest")
    authority = {
        "artifact_root_sha256": getattr(audited_universe, "artifact_root_sha256", None),
        "coverage_audit_sha256": getattr(audited_universe, "coverage_audit_sha256", None),
        "temporal_contract_sha256": getattr(audited_universe, "temporal_contract_sha256", None),
        "temporal_role": getattr(audited_universe, "temporal_role", None),
        "artifact_manifest_sha256": manifest.get("manifest_sha256"),
        "market_generation_root_sha256": (manifest.get("market_generations") or {}).get(
            "root_sha256"
        ),
        "stock_generation_lineage_sha256": (manifest.get("stock_generation") or {}).get(
            "lineage_sha256"
        ),
    }
    if set(authority) != SINGLE_AUTHORITY_KEYS:
        raise ValueError("audited authority fields are incomplete")
    if authority["temporal_role"] != "development":
        raise ValueError("audited authority must use the development temporal role")
    if any(
        not _valid_sha256(value)
        for key, value in authority.items()
        if key != "temporal_role"
    ):
        raise ValueError("audited authority contains an invalid hash")
    return authority


def _composite_authority(audited_universe: Any) -> dict[str, Any]:
    composite = copy.deepcopy(getattr(audited_universe, "composite_authority", None))
    if not isinstance(composite, dict):
        raise ValueError("composite audited authority is missing")
    claimed_root = composite.get("composite_root_sha256")
    unsigned = {key: value for key, value in composite.items() if key != "composite_root_sha256"}
    if not _valid_sha256(claimed_root) or not hmac.compare_digest(claimed_root, _sha256(unsigned)):
        raise ValueError("composite audited authority root is invalid")
    if not hmac.compare_digest(
        claimed_root, str(getattr(audited_universe, "composite_root_sha256", ""))
    ):
        raise ValueError("composite audited authority does not match the live universe")
    coverage = audited_coverage_from_universe(audited_universe)
    if composite.get("coverage") != coverage:
        raise ValueError("composite audited authority coverage mismatch")
    segments = composite.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("composite audited authority has no segments")
    if [segment.get("sequence") for segment in segments if isinstance(segment, Mapping)] != list(
        range(1, len(segments) + 1)
    ):
        raise ValueError("composite audited authority segment order is invalid")
    if any(
        not isinstance(segment, Mapping)
        or segment.get("temporal_role") != "development"
        or segment.get("temporal_contract_sha256")
        != getattr(audited_universe, "temporal_contract_sha256", None)
        or any(
            not _valid_sha256(segment.get(key))
            for key in SINGLE_AUTHORITY_KEYS - {"temporal_role"}
        )
        for segment in segments
    ):
        raise ValueError("composite audited authority segment is invalid")
    authority = {
        "schema_version": COMPOSITE_AUTHORITY_SCHEMA_VERSION,
        "authority_kind": "ordered_composite",
        "composite_root_sha256": claimed_root,
        "coverage_audit_root_sha256": getattr(
            audited_universe, "coverage_audit_sha256", None
        ),
        "calendar_root_sha256": getattr(audited_universe, "calendar_sha256", None),
        "source_manifest_root_sha256": getattr(
            audited_universe, "source_manifest_sha256", None
        ),
        "temporal_contract_sha256": getattr(
            audited_universe, "temporal_contract_sha256", None
        ),
        "temporal_role": getattr(audited_universe, "temporal_role", None),
        "composite_authority": composite,
    }
    if set(authority) != COMPOSITE_AUTHORITY_KEYS:
        raise ValueError("composite audited authority fields are incomplete")
    if authority["temporal_role"] != "development" or any(
        not _valid_sha256(authority[key])
        for key in (
            "composite_root_sha256",
            "coverage_audit_root_sha256",
            "calendar_root_sha256",
            "source_manifest_root_sha256",
            "temporal_contract_sha256",
        )
    ):
        raise ValueError("composite audited authority contains an invalid hash")
    return authority


def audited_authority_from_universe(audited_universe: Any) -> dict[str, Any]:
    if is_composite_universe(audited_universe):
        return _composite_authority(audited_universe)
    return _single_authority(audited_universe)

