"""Narrow temporal authority for provider evidence reconstruction only."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from app.research_partitions import load_temporal_partition_contract


class ProviderEvidenceAuthorityError(ValueError):
    """The provider-evidence temporal authority is invalid or exceeded."""


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PARENT_RELATIVE_PATH = "data/research_partitions/frozen-v1.json"
_PARENT_FILE_SHA256 = "c810e1d19f5296f8e7edb7b437e487130473aade359982409ca3c7038a055c2b"
_PARENT_CONTRACT_SHA256 = "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227"
_SOURCE_THREAD_ID = "019f4cd6-ebd1-7900-9aec-cb7c08a6006c"
_AUTHORIZATION_SCOPE = (
    "Allow provider evidence-only collect and publish from 2026-07-04 through "
    "2026-07-10 for research evidence reconstruction; forbid signals, training, "
    "diagnostic selection, backtest, validation, purged validation, recommendations, "
    "eligible pool, paper/live, final_oos, deployment, orders, and push."
)
_AUTHORIZATION_SCOPE_SHA256 = hashlib.sha256(_AUTHORIZATION_SCOPE.encode("utf-8")).hexdigest()
_START_DATE = "2026-07-04"
_END_DATE = "2026-07-10"
_OPERATIONS = ["provider_evidence_collect", "provider_evidence_publish"]

_TOP_LEVEL_KEYS = {
    "schema_version",
    "policy_version",
    "evidence_role",
    "parent",
    "authorization",
    "official_exchange_pit",
    "safety",
    "contract_sha256",
}
_PARENT_KEYS = {"path", "file_sha256", "contract_sha256"}
_AUTHORIZATION_KEYS = {
    "source_kind",
    "source_thread_id",
    "scope",
    "scope_sha256",
    "start_date",
    "end_date",
    "operations",
}
_SAFETY_KEYS = {
    "strategy_signals_authorized",
    "training_authorized",
    "diagnostic_selection_authorized",
    "backtest_authorized",
    "validation_authorized",
    "purged_validation_authorized",
    "recommendations_authorized",
    "eligible_pool_count",
    "production_recommendation_eligible",
    "paper_trading_validated",
    "live_ready",
    "final_oos_authorized",
    "deployment_authorized",
    "orders_authorized",
    "push_authorized",
}


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "contract_sha256"}
    try:
        encoded = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderEvidenceAuthorityError("contract is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _expected_contract() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "research-temporal-provider-evidence/v1",
        "policy_version": "provider-evidence-only-embargo-tail/v1",
        "evidence_role": "research_development_provider_pit",
        "parent": {
            "path": _PARENT_RELATIVE_PATH,
            "file_sha256": _PARENT_FILE_SHA256,
            "contract_sha256": _PARENT_CONTRACT_SHA256,
        },
        "authorization": {
            "source_kind": "explicit_user_delegation",
            "source_thread_id": _SOURCE_THREAD_ID,
            "scope": _AUTHORIZATION_SCOPE,
            "scope_sha256": _AUTHORIZATION_SCOPE_SHA256,
            "start_date": _START_DATE,
            "end_date": _END_DATE,
            "operations": list(_OPERATIONS),
        },
        "official_exchange_pit": False,
        "safety": {
            "strategy_signals_authorized": False,
            "training_authorized": False,
            "diagnostic_selection_authorized": False,
            "backtest_authorized": False,
            "validation_authorized": False,
            "purged_validation_authorized": False,
            "recommendations_authorized": False,
            "eligible_pool_count": 0,
            "production_recommendation_eligible": False,
            "paper_trading_validated": False,
            "live_ready": False,
            "final_oos_authorized": False,
            "deployment_authorized": False,
            "orders_authorized": False,
            "push_authorized": False,
        },
    }
    payload["contract_sha256"] = _canonical_sha256(payload)
    return payload


_EXPECTED_CONTRACT = _expected_contract()
EXPECTED_PROVIDER_EVIDENCE_CONTRACT_SHA256 = _EXPECTED_CONTRACT["contract_sha256"]
PROVIDER_EVIDENCE_CONTRACT_PATH = (
    _REPO_ROOT
    / "data"
    / "research_partitions"
    / "provider-evidence-authorities"
    / EXPECTED_PROVIDER_EVIDENCE_CONTRACT_SHA256
    / "contract.json"
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderEvidenceAuthorityError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ProviderEvidenceAuthorityError(f"non-finite JSON constant is forbidden: {value}")


def _require_exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProviderEvidenceAuthorityError(f"{label} has missing or unknown fields")


def _assert_regular_file(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProviderEvidenceAuthorityError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ProviderEvidenceAuthorityError(f"{label} must be a regular non-link file")
    if int(getattr(info, "st_nlink", 1)) != 1:
        raise ProviderEvidenceAuthorityError(f"{label} must not be a hardlink")
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if attributes & reparse:
        raise ProviderEvidenceAuthorityError(f"{label} must not be a reparse point")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ProviderEvidenceAuthorityError(f"unable to hash file: {path.name}") from exc
    return digest.hexdigest()


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime) or not isinstance(value, str):
        raise ProviderEvidenceAuthorityError("date must be a canonical ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProviderEvidenceAuthorityError("date must be a canonical ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise ProviderEvidenceAuthorityError("date must be a canonical ISO calendar date")
    return parsed


def _validate_contract(contract: Any) -> None:
    _require_exact_keys(contract, _TOP_LEVEL_KEYS, "contract")
    _require_exact_keys(contract["parent"], _PARENT_KEYS, "parent")
    _require_exact_keys(contract["authorization"], _AUTHORIZATION_KEYS, "authorization")
    _require_exact_keys(contract["safety"], _SAFETY_KEYS, "safety")
    claimed = contract["contract_sha256"]
    if not isinstance(claimed, str) or claimed != _canonical_sha256(contract):
        raise ProviderEvidenceAuthorityError("contract hash mismatch")
    if contract != _EXPECTED_CONTRACT:
        raise ProviderEvidenceAuthorityError("contract differs from explicit provider evidence authority")


def load_provider_evidence_temporal_contract(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Load the content-addressed successor and verify its frozen v1 parent."""

    contract_path = Path(path)
    _assert_regular_file(contract_path, "provider evidence contract")
    try:
        payload = json.loads(
            contract_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProviderEvidenceAuthorityError("unable to load provider evidence contract") from exc
    _validate_contract(payload)
    if (
        contract_path.name != "contract.json"
        or contract_path.parent.name != payload["contract_sha256"]
    ):
        raise ProviderEvidenceAuthorityError("provider evidence contract content-address mismatch")

    root = Path(repo_root).resolve()
    parent_path = (root / payload["parent"]["path"]).resolve()
    expected_parent = (root / _PARENT_RELATIVE_PATH).resolve()
    if parent_path != expected_parent:
        raise ProviderEvidenceAuthorityError("provider evidence parent path mismatch")
    _assert_regular_file(parent_path, "provider evidence parent")
    if _file_sha256(parent_path) != payload["parent"]["file_sha256"]:
        raise ProviderEvidenceAuthorityError("provider evidence parent file hash mismatch")
    parent = load_temporal_partition_contract(parent_path)
    if parent["contract_sha256"] != payload["parent"]["contract_sha256"]:
        raise ProviderEvidenceAuthorityError("provider evidence parent contract hash mismatch")
    return copy.deepcopy(payload)


def assert_provider_evidence_range_allowed(
    contract: Mapping[str, Any],
    start: Any,
    end: Any,
    operation: str,
) -> None:
    """Permit only the two evidence operations within the explicitly authorized tail."""

    _validate_contract(contract)
    if operation not in _OPERATIONS:
        raise ProviderEvidenceAuthorityError(f"operation {operation!r} is forbidden")
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date > end_date:
        raise ProviderEvidenceAuthorityError("range start is after range end")
    if start_date < _parse_date(_START_DATE) or end_date > _parse_date(_END_DATE):
        raise ProviderEvidenceAuthorityError("range exceeds provider evidence authority")
