"""Fail-closed temporal partition policy for research data and operations."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


class PartitionContractError(ValueError):
    """The frozen temporal partition contract or a requested use is invalid."""


_ROLE_NAMES = (
    "development",
    "contaminated_diagnostic",
    "embargo",
    "final_oos",
)
_ROLE_KEYS = {"name", "start_date", "end_date", "sealed", "permitted_operations"}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "policy_version",
    "roles",
    "contamination_evidence",
    "contract_sha256",
}
_EVIDENCE_KEYS = {"strategy_results", "real_pit_windows"}
_RESULT_KEYS = {"path", "sha256", "maximum_observed_date"}
_WINDOW_KEYS = {
    "start_date",
    "end_date",
    "coverage_audit_sha256",
    "artifact_root_sha256",
}
_OPERATIONS = {
    "development": ["collect", "publish", "train", "validate", "backtest"],
    "contaminated_diagnostic": ["collect", "publish", "diagnose"],
    "embargo": [],
    "final_oos": [],
}
_BOUNDS = (
    ("2016-01-01", "2023-12-31", False),
    ("2024-01-01", "2026-07-03", False),
    ("2026-07-04", "2026-07-12", True),
    ("2026-07-13", None, True),
)
_EXPECTED_RESULTS = [
    {
        "path": "data/research_cache/qualified_hold5_stop5.json",
        "sha256": "9d6b00f33cdfbb0719eb60f5d7130dbafb1d282d6d2ffee861831db021d76ac5",
        "maximum_observed_date": "2026-07-03",
    },
    {
        "path": "data/research_cache/qualified_hold5_stop5_ohlc.json",
        "sha256": "320f58b480da8ccdaff501ff26c94b55614c2b05c5776c49aebdcd3749871bf3",
        "maximum_observed_date": "2026-07-03",
    },
]
_EXPECTED_WINDOWS = [
    {
        "start_date": "2024-01-02",
        "end_date": "2024-01-05",
        "coverage_audit_sha256": "bcf2ad4a82ca798e18d89370c4b10d2f6c45cba4eb508257438b1f9a1911e9e6",
        "artifact_root_sha256": "46fc70113a3243937ac8a0ebac50bebb4609537e1390945e7c87b492c18e473f",
    }
]


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    unhashed = {key: value for key, value in payload.items() if key != "contract_sha256"}
    try:
        encoded = json.dumps(
            unhashed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PartitionContractError("contract is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        raise PartitionContractError("datetime values are forbidden; provide a calendar date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise PartitionContractError("date must be an ISO YYYY-MM-DD string or date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PartitionContractError(f"invalid ISO date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise PartitionContractError(f"date is not canonical ISO format: {value!r}")
    return parsed


def _require_exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise PartitionContractError(f"{label} has missing or unknown fields")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PartitionContractError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PartitionContractError(f"non-finite JSON constant is forbidden: {value}")


def _validate_contract(contract: Any) -> None:
    _require_exact_keys(contract, _TOP_LEVEL_KEYS, "contract")
    if contract["schema_version"] != "research-temporal-partitions/v1":
        raise PartitionContractError("unsupported schema_version")
    if contract["policy_version"] != "contamination-aware-forward-oos/v1":
        raise PartitionContractError("unsupported policy_version")
    digest = contract["contract_sha256"]
    if not isinstance(digest, str) or digest != _canonical_sha256(contract):
        raise PartitionContractError("contract hash mismatch")

    roles = contract["roles"]
    if not isinstance(roles, list) or len(roles) != 4:
        raise PartitionContractError("roles must contain exactly four entries")
    previous_end = None
    for index, (role, name, bounds) in enumerate(zip(roles, _ROLE_NAMES, _BOUNDS)):
        _require_exact_keys(role, _ROLE_KEYS, f"role {index}")
        start_text, end_text, sealed = bounds
        if role != {
            "name": name,
            "start_date": start_text,
            "end_date": end_text,
            "sealed": sealed,
            "permitted_operations": _OPERATIONS[name],
        }:
            raise PartitionContractError(
                f"role {name} sealed state or fields differ from frozen policy"
            )
        if type(role["sealed"]) is not bool:
            raise PartitionContractError(f"role {name} sealed must be a boolean")
        start = _parse_date(role["start_date"])
        end = _parse_date(role["end_date"]) if role["end_date"] is not None else None
        if index < 3 and end is None:
            raise PartitionContractError("only final_oos may be open-ended")
        if index == 3 and end is not None:
            raise PartitionContractError("final_oos must be open-ended")
        if previous_end is not None and start != previous_end + timedelta(days=1):
            raise PartitionContractError("roles have a gap or overlap")
        previous_end = end

    evidence = contract["contamination_evidence"]
    _require_exact_keys(evidence, _EVIDENCE_KEYS, "contamination_evidence")
    results = evidence["strategy_results"]
    windows = evidence["real_pit_windows"]
    if not isinstance(results, list) or len(results) != 2:
        raise PartitionContractError("strategy_results must contain exactly two entries")
    for result in results:
        _require_exact_keys(result, _RESULT_KEYS, "strategy result")
    if results != _EXPECTED_RESULTS:
        raise PartitionContractError("strategy contamination evidence differs from frozen values")
    if not isinstance(windows, list) or len(windows) != 1:
        raise PartitionContractError("real_pit_windows must contain exactly one entry")
    _require_exact_keys(windows[0], _WINDOW_KEYS, "real PIT window")
    if windows != _EXPECTED_WINDOWS:
        raise PartitionContractError("real PIT evidence differs from frozen values")


def load_temporal_partition_contract(path: Any) -> dict[str, Any]:
    """Load and validate the frozen contract, rejecting any structural drift."""

    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise PartitionContractError(f"unable to load partition contract: {path}") from exc
    _validate_contract(payload)
    return payload


def classify_date(contract: Mapping[str, Any], value: Any) -> str:
    """Return the single temporal role containing *value*."""

    _validate_contract(contract)
    target = _parse_date(value)
    for role in contract["roles"]:
        start = _parse_date(role["start_date"])
        end = _parse_date(role["end_date"]) if role["end_date"] is not None else None
        if target >= start and (end is None or target <= end):
            return role["name"]
    raise PartitionContractError(f"date is outside the contract: {target.isoformat()}")


def assert_range_allowed(
    contract: Mapping[str, Any], role: str, start: Any, end: Any, operation: str
) -> None:
    """Reject a range unless it stays in *role* and permits *operation*."""

    _validate_contract(contract)
    if role not in _ROLE_NAMES or not isinstance(operation, str):
        raise PartitionContractError("unknown role or operation")
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date > end_date:
        raise PartitionContractError("range start is after range end")
    if classify_date(contract, start_date) != role or classify_date(contract, end_date) != role:
        raise PartitionContractError("range crosses or differs from the requested role")
    role_record = next(item for item in contract["roles"] if item["name"] == role)
    if operation not in role_record["permitted_operations"]:
        raise PartitionContractError(f"operation {operation!r} is forbidden in {role}")


def assert_final_oos_sealed(contract: Mapping[str, Any]) -> None:
    """Fail unless final OOS is sealed, open-ended, and has no operations."""

    _validate_contract(contract)
    final = contract["roles"][-1]
    if (
        final.get("name") != "final_oos"
        or final.get("sealed") is not True
        or final.get("end_date") is not None
        or final.get("permitted_operations") != []
    ):
        raise PartitionContractError("final_oos is not sealed")
