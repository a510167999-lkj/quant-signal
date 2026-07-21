"""Exact, hash-bound ledger quarantine contracts for a control successor."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


_SCHEMA = "research-control-quarantine/v1"
_RECORD_FIELDS = ("experiment_id", "sequence", "record_hash", "event_type")
_LOWER_HEX = frozenset("0123456789abcdef")
_FROZEN_RECORDS = (
    {
        "experiment_id": "auto-iter-034-current-pool-coverage-expansion",
        "sequence": 117,
        "record_hash": "68adc32daae82e7a11fe76fef888d47c8e004b3df9cc18a5fcf4547bb256f840",
        "event_type": "registered",
    },
    {
        "experiment_id": "auto-iter-038-h1-hold3-development",
        "sequence": 124,
        "record_hash": "7050a197d237b340c9f98db2ad1d02de30f36d1f7c7c39377f6e3bdb9fe70cbb",
        "event_type": "registered",
    },
)
_FROZEN_MAXIMUM_SEQUENCE = 126


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWER_HEX for character in value)
    )


def _validated_record(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_RECORD_FIELDS):
        raise ValueError("quarantine record is invalid")
    record = dict(value)
    experiment_id = record.get("experiment_id")
    sequence = record.get("sequence")
    if (
        not isinstance(experiment_id, str)
        or not experiment_id
        or experiment_id != experiment_id.strip()
        or any(not (character.isascii() and (character.isalnum() or character in "-_")) for character in experiment_id)
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= 0
        or not _is_sha256(record.get("record_hash"))
        or record.get("event_type") != "registered"
    ):
        raise ValueError("quarantine record is invalid")
    return {field: record[field] for field in _RECORD_FIELDS}


def _validated_records(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError("quarantine records are invalid")
    records = [_validated_record(value) for value in values]
    sequences = [record["sequence"] for record in records]
    identifiers = [record["experiment_id"] for record in records]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences) or len(set(identifiers)) != len(identifiers):
        raise ValueError("quarantine records are invalid")
    return records


def build_quarantine_binding_v1(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build the canonical signed-by-content representation of sealed open rows."""

    validated = _validated_records(records)
    return {
        "schema_version": _SCHEMA,
        "records": validated,
        "records_sha256": hashlib.sha256(_canonical_bytes(validated)).hexdigest(),
    }


def frozen_quarantine_binding_v1() -> dict[str, Any]:
    """Return the only legacy-open tuple that this successor may quarantine."""

    return build_quarantine_binding_v1(list(_FROZEN_RECORDS))


def validate_frozen_quarantine_binding_v1(value: Mapping[str, Any]) -> dict[str, Any]:
    return validate_quarantine_binding_v1(
        value,
        expected_records=list(_FROZEN_RECORDS),
        maximum_sequence=_FROZEN_MAXIMUM_SEQUENCE,
    )


def quarantine_binding_sha256_v1(value: Mapping[str, Any]) -> str:
    verified = validate_frozen_quarantine_binding_v1(value)
    return hashlib.sha256(_canonical_bytes(verified)).hexdigest()


def validate_quarantine_binding_v1(
    value: Mapping[str, Any],
    *,
    expected_records: Sequence[Mapping[str, Any]],
    maximum_sequence: int,
) -> dict[str, Any]:
    """Verify a frozen legacy allowlist before it is used for a ledger CAS."""

    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "records", "records_sha256"}
        or value.get("schema_version") != _SCHEMA
        or isinstance(maximum_sequence, bool)
        or not isinstance(maximum_sequence, int)
        or maximum_sequence <= 0
    ):
        raise ValueError("quarantine binding is invalid")
    records = _validated_records(value.get("records"))
    expected = _validated_records(expected_records)
    if (
        records != expected
        or any(record["sequence"] > maximum_sequence for record in records)
        or value.get("records_sha256")
        != hashlib.sha256(_canonical_bytes(records)).hexdigest()
    ):
        raise ValueError("quarantine binding is invalid")
    return {
        "schema_version": _SCHEMA,
        "records": records,
        "records_sha256": value["records_sha256"],
    }


def open_record_projection_v1(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project one ledger row into the only fields a quarantine may authorize."""

    if not isinstance(value, Mapping):
        raise ValueError("quarantine open record is invalid")
    try:
        return _validated_record({field: value[field] for field in _RECORD_FIELDS})
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("quarantine open record is invalid") from exc


def validate_exact_open_set_v1(
    values: Sequence[Mapping[str, Any]],
    *,
    binding: Mapping[str, Any],
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Allow only the frozen rows plus the one current registered target."""

    if not isinstance(binding, Mapping):
        raise ValueError("quarantine binding is invalid")
    records = _validated_records(binding.get("records"))
    if (
        binding.get("schema_version") != _SCHEMA
        or binding.get("records_sha256")
        != hashlib.sha256(_canonical_bytes(records)).hexdigest()
    ):
        raise ValueError("quarantine binding is invalid")
    projected_target = open_record_projection_v1(target)
    if (
        projected_target["experiment_id"] in {record["experiment_id"] for record in records}
        or projected_target["sequence"] <= records[-1]["sequence"]
    ):
        raise ValueError("quarantine target is invalid")
    projected = [open_record_projection_v1(value) for value in values]
    expected = [*records, projected_target]
    if projected != expected:
        raise ValueError("quarantine open set is invalid")
    return projected
