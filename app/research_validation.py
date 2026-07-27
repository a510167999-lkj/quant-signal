"""Strict validation primitives for frozen research strategies.

The development validator rejects inputs containing final-OOS rows.  A future
final-OOS consumer must therefore use a separate artifact and lifecycle rather
than silently turning the holdout into another tuning set.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import math
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from app.research_sweep import sweep_qualified_trades
from app.research_pit import verify_research_evidence_bundle
from app.research_scope import verify_trade_market_scope
from app.research_control_quarantine import (
    quarantine_binding_sha256_v1,
    validate_exact_open_set_v1,
    validate_frozen_quarantine_binding_v1,
)

try:  # Unix process locks.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by the Windows backend test.
    _fcntl = None

try:  # Windows process locks.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - unavailable on Unix.
    _msvcrt = None
from app.research_authority import (
    audited_authority_from_universe as _audited_authority_from_universe,
    audited_coverage_from_universe,
    is_composite_universe,
)


_LEDGER_LOCK_MARKER = b"\0"
_LEDGER_BOOTSTRAP_MUTEX = threading.RLock()


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _canonical_trade_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be a canonical ISO date")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _freeze_json_value(value: Any, *, label: str) -> Any:
    if isinstance(value, Mapping):
        frozen: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} JSON object keys must be strings")
            if key in frozen:
                raise ValueError(f"{label} JSON object contains a duplicate key")
            frozen[key] = _freeze_json_value(item, label=label)
        return frozen
    if isinstance(value, (list, tuple)):
        return [_freeze_json_value(item, label=label) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite JSON number")
        return value
    raise ValueError(f"{label} is not JSON serializable")


def _freeze_json_mapping(value: Any, *, label: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    frozen = _freeze_json_value(value, label=label)
    if not isinstance(frozen, dict):
        raise ValueError(f"{label} must be an object")
    return frozen


def _freeze_json_list(value: Any, *, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} are invalid")
    frozen = _freeze_json_value(value, label=label)
    if not isinstance(frozen, list):
        raise ValueError(f"{label} are invalid")
    return frozen


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def qualified_trades_sha256(trades: List[Dict[str, Any]]) -> str:
    ordered = sorted(
        (dict(item) for item in trades),
        key=lambda item: (str(item.get("signal_date") or ""), str(item.get("symbol") or "")),
    )
    return _sha256(ordered)


_AUDITED_AUTHORITY_KEYS = {
    "artifact_root_sha256",
    "coverage_audit_sha256",
    "temporal_contract_sha256",
    "temporal_role",
    "artifact_manifest_sha256",
    "market_generation_root_sha256",
    "stock_generation_lineage_sha256",
}


def audited_authority_from_universe(audited_universe: Any) -> Dict[str, Any]:
    """Derive authority only from an already verified audited artifact."""

    return _audited_authority_from_universe(audited_universe)


def _verify_contract_authority_binding(
    contract: Mapping[str, Any],
    *,
    audited_universe: Any,
    verified_authority: Mapping[str, Any],
) -> None:
    expected = audited_authority_from_universe(audited_universe)
    if dict(verified_authority) != expected:
        raise ValueError("evidence bundle is bound to a different audited artifact")
    if contract.get("audited_authority") != expected:
        raise ValueError("point-in-time contract audited authority mismatch")
    expected_projection = {
        "artifact_root_sha256": audited_universe.artifact_root_sha256,
        "coverage_audit_sha256": audited_universe.coverage_audit_sha256,
        "temporal_contract_sha256": expected["temporal_contract_sha256"],
        "temporal_role": expected["temporal_role"],
    }
    if not is_composite_universe(audited_universe):
        expected_projection.update(
            {
                "artifact_manifest_sha256": expected[
                    "artifact_manifest_sha256"
                ],
                "market_generation_root_sha256": expected[
                    "market_generation_root_sha256"
                ],
                "stock_generation_lineage_sha256": expected[
                    "stock_generation_lineage_sha256"
                ],
            }
        )
    if any(contract.get(key) != value for key, value in expected_projection.items()):
        raise ValueError("point-in-time contract authority projection mismatch")

    base_keys = {
        "schema_version",
        "artifact_role",
        "point_in_time",
        "eligible_for_development_validation",
        "eligible_for_final_validation",
        "final_oos_eligible",
        "entry_decision_cutoff",
        "known_biases",
        "qualified_trades_sha256",
        "qualified_trade_lineage_sha256",
        "universe_sha256",
        "calendar_sha256",
        "source_manifest_sha256",
        "artifact_root_sha256",
        "coverage_audit_sha256",
        "temporal_contract_sha256",
        "temporal_role",
        "audited_authority",
        "market_scope",
        "evidence_bundle_path",
        "evidence_bundle_sha256",
    }
    if not is_composite_universe(audited_universe):
        base_keys.update(
            {
                "artifact_manifest_sha256",
                "market_generation_root_sha256",
                "stock_generation_lineage_sha256",
            }
        )
    if set(contract) != base_keys:
        raise ValueError("point-in-time contract fields are invalid")


def _execution_projection(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"trade lineage {label} evidence is missing")
    if type(value.get("fillable")) is not bool:
        raise ValueError(f"trade lineage {label} fillable verdict is invalid")
    proof = value.get("generation_proof")
    if not isinstance(proof, dict) or not proof:
        raise ValueError(f"trade lineage {label} generation proof is missing")
    if value["fillable"]:
        raw_price = value.get("raw_price")
        try:
            raw_price = float(raw_price)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"trade lineage {label} raw price is invalid") from exc
        if not math.isfinite(raw_price) or raw_price <= 0:
            raise ValueError(f"trade lineage {label} raw price is invalid")
    elif value.get("raw_price") is not None:
        raise ValueError(f"trade lineage {label} blocked verdict carries a raw price")
    return {
        "fillable": value["fillable"],
        "reason": value.get("reason"),
        "raw_price": raw_price if value["fillable"] else None,
        "generation_proof": dict(proof),
    }


def _assert_execution_projection(expected: Dict[str, Any], claimed: Any, label: str) -> Dict[str, Any]:
    observed = _execution_projection(claimed, label)
    if expected["fillable"] != observed["fillable"] or expected["reason"] != observed["reason"]:
        raise ValueError(f"trade lineage {label} verdict mismatch")
    if expected["fillable"] and not math.isclose(
        float(expected["raw_price"]),
        float(observed["raw_price"]),
        rel_tol=0.0,
        abs_tol=max(1.0, abs(float(expected["raw_price"]))) * 1e-9,
    ):
        raise ValueError(f"trade lineage {label} raw price mismatch")
    if expected["generation_proof"] != observed["generation_proof"]:
        raise ValueError(f"trade lineage {label} generation proof mismatch")
    return observed


def verify_artifact_trade_lineage(audited_universe: Any, trades: List[Dict[str, Any]]) -> str:
    """Re-read every artifact-native entry/exit proof and hash the verified claims.

    Qualified-trade JSON is treated as a claim.  Even if a caller recomputes an
    outer file hash after editing it, this function asks the read-only audited
    artifact for fresh next-open verdicts and compares the exact generation
    proof, side, fillability, and raw price before returning a lineage root.
    """

    if not isinstance(trades, list):
        raise ValueError("trade lineage input must be a list")
    verify_trade_market_scope(trades)
    lineage = []
    seen_keys = set()
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("trade lineage row is invalid")
        symbol = str(trade.get("symbol") or "").strip()
        signal_date = _canonical_trade_date(trade.get("signal_date"), "signal_date")
        entry_date = _canonical_trade_date(trade.get("entry_date"), "entry_date")
        exit_date = _canonical_trade_date(trade.get("exit_date"), "exit_date")
        planned_exit_date = _canonical_trade_date(
            trade.get("planned_exit_date"), "planned_exit_date"
        )
        if not symbol or not all((signal_date, entry_date, exit_date, planned_exit_date)):
            raise ValueError("trade lineage dates or symbol are missing")
        trade_key = "|".join((symbol, signal_date, entry_date, exit_date))
        if trade_key in seen_keys:
            raise ValueError("trade lineage contains duplicate trade key")
        seen_keys.add(trade_key)
        if audited_universe.item_as_of(symbol, signal_date) is None:
            raise ValueError(f"trade lineage membership is missing: {trade_key}")
        sessions = list(audited_universe.open_sessions(signal_date, exit_date))
        if not sessions or sessions[0] != signal_date or sessions[-1] != exit_date:
            raise ValueError(f"trade lineage session range is invalid: {trade_key}")
        if len(sessions) < 2 or sessions[1] != entry_date:
            raise ValueError(f"trade lineage entry is not the next open: {trade_key}")
        if planned_exit_date > exit_date or planned_exit_date < entry_date:
            raise ValueError(f"trade lineage planned exit is invalid: {trade_key}")
        try:
            holding_days = int(trade.get("holding_days"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"trade lineage holding period is invalid: {trade_key}") from exc
        planned_position = sessions.index(planned_exit_date)
        actual_holding_sessions = len(sessions) - 2
        planned_holding_sessions = planned_position - 1
        explicit_holding_semantics = (
            trade.get("planned_holding_sessions") is not None
            or trade.get("actual_holding_sessions") is not None
        )
        if (
            holding_days <= 0
            or planned_holding_sessions <= 0
            or (
                not explicit_holding_semantics
                and holding_days
                not in {planned_holding_sessions, actual_holding_sessions}
            )
            or (
                trade.get("planned_holding_sessions") is not None
                and int(trade["planned_holding_sessions"])
                != planned_holding_sessions
            )
            or (
                trade.get("actual_holding_sessions") is not None
                and int(trade["actual_holding_sessions"])
                != actual_holding_sessions
            )
        ):
            raise ValueError(f"trade lineage holding periods are invalid: {trade_key}")
        if trade.get("exit_reason") != "time_exit_next_open":
            raise ValueError(f"trade lineage exit model is not artifact-native: {trade_key}")
        if trade.get("price_basis") != "raw_unadjusted_execution" or trade.get("return_price_basis") != "causal_total_return_open_to_open":
            raise ValueError(f"trade lineage price basis is not artifact-native: {trade_key}")
        signal_rows = audited_universe.causal_signal_bars(symbol, signal_date, signal_date)
        if not isinstance(signal_rows, list) or not signal_rows:
            raise ValueError(f"trade lineage signal frame is missing: {trade_key}")
        expected_entry = _execution_projection(
            audited_universe.next_open_execution_evidence(symbol, entry_date, "buy"),
            "fresh entry",
        )
        expected_exit = _execution_projection(
            audited_universe.next_open_execution_evidence(symbol, exit_date, "sell"),
            "fresh exit",
        )
        entry = _assert_execution_projection(
            expected_entry, trade.get("entry_executability"), "entry"
        )
        exit_evidence = _assert_execution_projection(
            expected_exit, trade.get("exit_execution_evidence"), "exit"
        )
        if not expected_exit["fillable"]:
            raise ValueError(f"trade lineage exit is not fillable: {trade_key}")
        lineage.append(
            {
                "trade_key": trade_key,
                "planned_exit_date": planned_exit_date,
                "entry": entry,
                "exit": exit_evidence,
            }
        )
    lineage.sort(key=lambda item: item["trade_key"])
    return _sha256(lineage)


def validate_point_in_time_contract(
    summary: Dict[str, Any],
    trades: List[Dict[str, Any]],
    artifact_base_dir: str = None,
    declared_start_date: str = None,
    declared_end_date: str = None,
    audited_universe: Any = None,
) -> Dict[str, Any]:
    contract = summary.get("research_data_contract") or {}
    evidence_path = contract.get("evidence_bundle_path")
    evidence_digest = contract.get("evidence_bundle_sha256")
    if not artifact_base_dir or not isinstance(evidence_path, str) or not evidence_path.strip():
        raise ValueError(
            "qualified trades point-in-time contract lacks a verified evidence bundle"
        )
    relative = Path(evidence_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(
            "qualified trades point-in-time contract lacks a verified evidence bundle"
        )
    bundle_root = Path(artifact_base_dir).resolve()
    bundle_path = bundle_root
    for part in relative.parts:
        bundle_path = bundle_path / part
        if bundle_path.is_symlink():
            raise ValueError(
                "qualified trades point-in-time contract lacks a verified evidence bundle"
            )
    try:
        bundle_path.resolve().relative_to(bundle_root)
    except ValueError as exc:
        raise ValueError(
            "qualified trades point-in-time contract lacks a verified evidence bundle"
        ) from exc
    try:
        verified_evidence = verify_research_evidence_bundle(
            str(bundle_path),
            audited_universe=audited_universe,
            artifact_root=str(bundle_root),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"qualified trades point-in-time contract lacks a verified evidence bundle: {exc}"
        ) from exc
    sessions = list(verified_evidence["trade_sessions"])
    if not sessions:
        raise ValueError("verified evidence has no trade sessions")
    if audited_universe is not None:
        composite = is_composite_universe(audited_universe)
        accepted_evidence_schemas = (
            {"research_pit_evidence_bundle/v7"}
            if composite
            else {
                "research_pit_evidence_bundle/v2",
                "research_pit_evidence_bundle/v5",
            }
        )
        if verified_evidence.get("schema_version") not in accepted_evidence_schemas:
            raise ValueError("strict production requires an audited-bound evidence bundle")
        eligibility = verified_evidence.get("eligibility")
        unresolved_reasons = (
            eligibility.get("reasons")
            if isinstance(eligibility, dict)
            else None
        )
        if not isinstance(unresolved_reasons, list) or unresolved_reasons:
            raise ValueError("evidence bundle has unresolved eligibility reasons")
        expected_authority = audited_authority_from_universe(audited_universe)
        bound_authority = verified_evidence.get("audited_authority")
        if not isinstance(bound_authority, dict) or set(bound_authority) != set(
            expected_authority
        ):
            raise ValueError("evidence bundle audited authority is missing")
        if any(
            not hmac.compare_digest(str(bound_authority[key]), str(expected_value))
            for key, expected_value in expected_authority.items()
        ):
            raise ValueError("evidence bundle is bound to a different audited artifact")
        _verify_contract_authority_binding(
            contract,
            audited_universe=audited_universe,
            verified_authority=bound_authority,
        )
    if declared_start_date is not None or declared_end_date is not None:
        if not declared_start_date or not declared_end_date:
            raise ValueError("declared validation coverage must have both boundaries")
        declared_start = _date(declared_start_date).isoformat()
        declared_end = _date(declared_end_date).isoformat()
        if declared_start > declared_end:
            raise ValueError("declared validation coverage is reversed")
        if sessions[0] > declared_start or sessions[-1] < declared_end:
            raise ValueError("verified evidence coverage does not contain declared range")
        if audited_universe is not None:
            audited_coverage = audited_coverage_from_universe(audited_universe)
            if (
                str(audited_coverage["start_date"]) > declared_start
                or str(audited_coverage["end_date"]) < declared_end
            ):
                raise ValueError("audited artifact coverage does not contain declared range")
        for trade in trades:
            if any(
                not declared_start <= str(trade.get(field) or "")[:10] <= declared_end
                for field in ("signal_date", "entry_date", "exit_date")
            ):
                raise ValueError("qualified trade falls outside declared validation range")
    session_indexes = {session: index for index, session in enumerate(sessions)}
    if audited_universe is not None:
        verify_trade_market_scope(trades)
    universe = verified_evidence["universe"]
    trade_dates_valid = True
    for trade in trades:
        signal_date = _canonical_trade_date(trade.get("signal_date"), "signal_date")
        entry_date = _canonical_trade_date(trade.get("entry_date"), "entry_date")
        exit_date = _canonical_trade_date(trade.get("exit_date"), "exit_date")
        if not all(value in session_indexes for value in (signal_date, entry_date, exit_date)):
            trade_dates_valid = False
            break
        if session_indexes[entry_date] != session_indexes[signal_date] + 1:
            trade_dates_valid = False
            break
        if session_indexes[exit_date] < session_indexes[entry_date]:
            trade_dates_valid = False
            break
        try:
            member = universe.item_as_of(str(trade.get("symbol") or ""), signal_date)
        except ValueError:
            member = None
        if member is None:
            trade_dates_valid = False
            break
        if audited_universe is not None:
            try:
                audited_member = audited_universe.item_as_of(
                    str(trade.get("symbol") or ""), signal_date
                )
            except ValueError:
                audited_member = None
            if audited_member is None:
                trade_dates_valid = False
                break
    artifact_lineage_sha256 = None
    if audited_universe is not None and trade_dates_valid:
        artifact_lineage_sha256 = verify_artifact_trade_lineage(audited_universe, trades)
    required_hashes = [
        "qualified_trades_sha256",
        "universe_sha256",
        "calendar_sha256",
        "source_manifest_sha256",
    ]
    strict_development = audited_universe is not None
    if strict_development:
        required_hashes.append("qualified_trade_lineage_sha256")
    eligibility_valid = (
        contract.get("eligible_for_development_validation") is True
        and contract.get("eligible_for_final_validation") is False
        and contract.get("final_oos_eligible") is False
        and verified_evidence.get("eligible_for_development_validation") is True
        and verified_evidence.get("eligible_for_final_validation") is False
        and verified_evidence.get("final_oos_eligible") is False
    ) if strict_development else (
        contract.get("eligible_for_final_validation") is True
        and verified_evidence.get("eligible_for_final_validation") is True
    )
    valid = (
        contract.get("schema_version")
        == ("research_data_contract/v4" if is_composite_universe(audited_universe) else "research_data_contract/v2")
        and contract.get("artifact_role") == "development_only"
        and contract.get("point_in_time") is True
        and eligibility_valid
        and contract.get("entry_decision_cutoff") == "next_open"
        and evidence_digest == verified_evidence["evidence_bundle_sha256"]
        and not (contract.get("known_biases") or [])
        and all(
            isinstance(contract.get(key), str)
            and len(contract[key]) == 64
            and all(character in "0123456789abcdef" for character in contract[key].lower())
            for key in required_hashes
        )
        and contract.get("qualified_trades_sha256") == qualified_trades_sha256(trades)
        and (
            not strict_development
            or verified_evidence.get("qualified_trades_sha256")
            == contract.get("qualified_trades_sha256")
        )
        and (
            not strict_development
            or contract.get("qualified_trade_lineage_sha256") == artifact_lineage_sha256
        )
        and (
            not strict_development
            or verified_evidence.get("qualified_trade_lineage_sha256")
            == artifact_lineage_sha256
        )
        and contract.get("universe_sha256") == verified_evidence["universe_sha256"]
        and contract.get("calendar_sha256") == verified_evidence["calendar_sha256"]
        and contract.get("source_manifest_sha256")
        == verified_evidence["source_manifest_sha256"]
        and (
            not strict_development
            or contract.get("market_scope")
            == verified_evidence["artifact_native_evidence"].get("market_scope")
        )
        and trade_dates_valid
    )
    if not valid:
        raise ValueError("qualified trades do not declare the required point-in-time contract")
    authority = None
    if audited_universe is not None:
        authority = {
            "coverage": audited_coverage_from_universe(audited_universe),
            **audited_authority_from_universe(audited_universe),
        }
    return {
        **dict(contract),
        "verified_authority": authority,
        **(
            {"verified_qualified_trade_lineage_sha256": artifact_lineage_sha256}
            if strict_development
            else {}
        ),
    }


def write_report_artifact(directory: str, report: Dict[str, Any]) -> Dict[str, Any]:
    artifact_dir = Path(directory)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode(
        "utf-8"
    )
    digest = hashlib.sha256(content).hexdigest()
    filename = f"{digest}.json"
    destination = artifact_dir / filename
    if destination.exists():
        if destination.read_bytes() != content:
            raise ValueError("content-addressed report artifact mismatch")
    else:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{digest}.", suffix=".tmp", dir=str(artifact_dir)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return {
        "filename": filename,
        "path": str(destination),
        "sha256": digest,
        "bytes": len(content),
        "media_type": "application/json",
    }


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Return a Wilson score interval for a binomial success rate."""

    if total <= 0:
        return (0.0, 1.0)
    if wins < 0 or wins > total:
        raise ValueError("wins must be between zero and total")
    probability = wins / total
    denominator = 1 + z * z / total
    center = (probability + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(probability * (1 - probability) / total + z * z / (4 * total * total))
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_purged_walk_forward_partitions(
    trades: List[Dict[str, Any]],
    *,
    train_days: int,
    validation_days: int,
    step_days: int,
    embargo_days: int,
    final_oos_start: str,
) -> Dict[str, Any]:
    """Build rolling train/validation folds while keeping final OOS sealed.

    Date boundaries are half-open.  Training outcomes that are not known before
    validation starts are purged, even when their signal belongs to the training
    window.  The embargo is a real calendar gap between train and validation.
    """

    for name, value in {
        "train_days": train_days,
        "validation_days": validation_days,
        "step_days": step_days,
    }.items():
        if int(value) <= 0:
            raise ValueError(f"{name} must be positive")
    if int(embargo_days) < 0:
        raise ValueError("embargo_days must be non-negative")

    final_start = _date(final_oos_start)
    all_normalized = [
        dict(item) for item in trades if item.get("signal_date") and item.get("exit_date")
    ]
    sealed = [
        item
        for item in all_normalized
        if item.get("temporal_role") in {"embargo", "final_oos"}
    ]
    if sealed:
        raise ValueError("validation input contains sealed embargo or final OOS trades")
    diagnostic = [
        item
        for item in all_normalized
        if item.get("temporal_role") == "contaminated_diagnostic"
    ]
    normalized = [
        item
        for item in all_normalized
        if item.get("temporal_role") != "contaminated_diagnostic"
    ]
    normalized.sort(key=lambda item: (str(item.get("signal_date")), str(item.get("symbol") or "")))
    outside_development = [
        item
        for item in normalized
        if not (
            date(2016, 1, 1) <= _date(item["signal_date"]) <= date(2023, 12, 31)
            and date(2016, 1, 1) <= _date(item["exit_date"]) <= date(2023, 12, 31)
        )
    ]
    if final_start == date(2026, 7, 13) and outside_development:
        raise ValueError("validation trades must stay inside the frozen development range")
    boundary_purged = [
        item
        for item in normalized
        if _date(item["signal_date"]) < final_start and _date(item["exit_date"]) >= final_start
    ]
    development = [
        item
        for item in normalized
        if _date(item["signal_date"]) < final_start and _date(item["exit_date"]) < final_start
    ]
    final_oos = [item for item in normalized if _date(item["signal_date"]) >= final_start]
    if final_oos:
        raise ValueError("development input contains final OOS rows")

    folds = []
    if development:
        train_start = min(_date(item["signal_date"]) for item in development)
        fold_index = 1
        while True:
            train_end = train_start + timedelta(days=int(train_days))
            validation_start = train_end + timedelta(days=int(embargo_days))
            if validation_start >= final_start:
                break
            validation_end = min(
                validation_start + timedelta(days=int(validation_days)),
                final_start,
            )
            train_rows = [
                item
                for item in development
                if train_start <= _date(item["signal_date"]) < train_end
                and _date(item["exit_date"]) < validation_start
            ]
            validation_rows = [
                item
                for item in development
                if validation_start <= _date(item["signal_date"]) < validation_end
                and _date(item["exit_date"]) < validation_end
            ]
            if train_rows and validation_rows:
                folds.append(
                    {
                        "metadata": {
                            "fold": fold_index,
                            "train_start": train_start.isoformat(),
                            "train_end": train_end.isoformat(),
                            "validation_start": validation_start.isoformat(),
                            "validation_end": validation_end.isoformat(),
                            "embargo_days": int(embargo_days),
                            "train_trade_count": len(train_rows),
                            "validation_trade_count": len(validation_rows),
                        },
                        "train_trades": train_rows,
                        "validation_trades": validation_rows,
                    }
                )
                fold_index += 1
            train_start += timedelta(days=int(step_days))

    return {
        "dataset_sha256": qualified_trades_sha256(all_normalized),
        "development_trade_count": len(development),
        "contaminated_diagnostic": {
            "trade_count": len(diagnostic),
            "promotion": False,
        },
        "boundary_purged_trade_count": len(boundary_purged),
        "folds": folds,
        "final_oos": {"status": "not_loaded", "start_date": final_start.isoformat()},
    }


def _fixed_sweep(trades: List[Dict[str, Any]], strategy: Dict[str, Any]) -> Dict[str, Any]:
    if not trades:
        return {}
    exposure = float(strategy.get("exposure_multiplier", 1.0))
    sweep = sweep_qualified_trades(
        trades,
        hold_days=int(strategy.get("hold_days", 5)),
        top_n=int(strategy.get("top_n", 10)),
        symbol_cooldown_days=int(strategy.get("symbol_cooldown_days", 0)),
        max_active_positions=int(strategy.get("max_active_positions", 0)),
        min_trades=1,
        max_filter_size=0,
        target_win_rate_pct=float(strategy.get("target_win_rate_pct", 52.0)),
        target_drawdown_pct=float(strategy.get("target_drawdown_pct", 15.0)),
        target_one_year_return_pct=float(strategy.get("target_one_year_return_pct", 50.0)),
        target_profit_factor=float(strategy.get("target_profit_factor", 1.3)),
        target_calmar=float(strategy.get("target_calmar", 1.5)),
        exposure_multipliers=[exposure],
        annual_financing_rate_pct=float(strategy.get("annual_financing_rate_pct", 8.0)),
        roundtrip_cost_bps=float(strategy.get("roundtrip_cost_bps", 25.0)),
        slippage_bps=float(strategy.get("slippage_bps", 10.0)),
        capital_model=str(strategy.get("capital_model", "slot-daily")),
        required_signal_tags=list(strategy.get("required_signal_tags") or []),
        excluded_signal_tags=list(strategy.get("excluded_signal_tags") or []),
        market_levels=list(strategy.get("market_levels") or []),
        force_exposure_multipliers=True,
        pre_exit_calendar_gap_days=int(strategy.get("pre_exit_calendar_gap_days", 0)),
        prior_high_trailing_stop_pct=strategy.get("prior_high_trailing_stop_pct"),
        prior_high_trailing_activation_pct=float(
            strategy.get("prior_high_trailing_activation_pct", 0.0)
        ),
        partial_profit_activation_pct=strategy.get("partial_profit_activation_pct"),
        partial_profit_fraction=float(strategy.get("partial_profit_fraction", 0.0)),
        correlation_threshold=strategy.get("correlation_threshold"),
        correlation_lookback_days=int(strategy.get("correlation_lookback_days", 60)),
        correlation_cache_dir=str(strategy.get("correlation_cache_dir", "data/research_cache")),
        correlation_min_periods=int(strategy.get("correlation_min_periods", 20)),
        correlation_history_lookback_days=int(
            strategy.get("correlation_history_lookback_days", 620)
        ),
        fixed_spec=True,
    )
    return dict((sweep.get("top") or [{}])[0])


def _callable_source_identity(target: Any) -> Dict[str, str]:
    try:
        source = inspect.getsource(target).encode("utf-8")
    except (OSError, TypeError) as exc:
        raise ValueError("strategy selection source is unavailable") from exc
    module = str(getattr(target, "__module__", ""))
    qualname = str(getattr(target, "__qualname__", ""))
    if not module or not qualname:
        raise ValueError("strategy selection source identity is incomplete")
    return {
        "module": module,
        "qualname": qualname,
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def _strategy_selection_replay_receipt(
    *,
    candidate_pool_sha256: str,
    candidate_pool_count: int,
    strategy_sha256: str,
    fold_reports: List[Dict[str, Any]],
    aggregate: Dict[str, Any],
) -> Dict[str, Any]:
    """Bind the deterministic filter/rank/top-N replay used by validation.

    This receipt proves selection relative to the supplied, hash-bound candidate
    pool. Candidate-generation completeness remains a development limitation
    until the full audited backtest is independently rerun from the artifact.
    """

    from app.research_portfolio import _select_with_portfolio_controls

    components = {
        "frozen_fixed_sweep": _callable_source_identity(_fixed_sweep),
        "fixed_sweep_engine": _callable_source_identity(sweep_qualified_trades),
        "portfolio_selector": _callable_source_identity(
            _select_with_portfolio_controls
        ),
    }
    receipt = {
        "schema_version": "strategy_selection_replay/v1",
        "candidate_pool_sha256": candidate_pool_sha256,
        "candidate_pool_count": int(candidate_pool_count),
        "strategy_sha256": strategy_sha256,
        "fold_selection_metrics_sha256": _sha256(fold_reports),
        "aggregate_selection_metrics_sha256": _sha256(aggregate),
        "components": components,
        "selection_receipt_bound": False,
        "bound": False,
        "reasons": ["independent_strategy_selection_replay_not_bound"],
        "limitations": [
            "candidate_generation_completeness_bound_by_producer_identity_not_independent_rebuild"
        ],
    }
    receipt["selection_replay_sha256"] = _sha256(receipt)
    return receipt


def _with_confidence(metrics: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(metrics)
    total = int(result.get("selected_trade_count") or 0)
    if result.get("trade_win_count") is None:
        rate = float(result.get("trade_win_rate_pct") or 0.0)
        wins = max(0, min(total, int(round(rate * total / 100))))
    else:
        wins = int(result["trade_win_count"])
    lower, upper = wilson_interval(wins, total)
    result.update(
        {
            "trade_win_count": wins,
            "trade_nonwin_count": total - wins,
            "win_rate_wilson_95_lower_pct": round(lower * 100, 2),
            "win_rate_wilson_95_upper_pct": round(upper * 100, 2),
        }
    )
    return result


def _unique_trades(folds: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for fold in folds:
        for item in fold[key]:
            identity = "|".join(
                str(item.get(field) or "")
                for field in ("symbol", "signal_date", "entry_date", "exit_date")
            )
            unique.setdefault(identity, item)
    return list(unique.values())


def run_frozen_strategy_validation(
    trades: List[Dict[str, Any]],
    *,
    strategy: Dict[str, Any],
    validation: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate one frozen strategy on development walk-forward folds only."""

    if strategy.get("correlation_adapter") is not None:
        raise ValueError("frozen validation forbids non-artifact correlation adapters")
    correlation_threshold = strategy.get("correlation_threshold")
    if correlation_threshold is not None and float(correlation_threshold) != 0.0:
        raise ValueError("frozen validation forbids legacy correlation cache reads")
    if str(validation.get("final_oos_start")) != "2026-07-13":
        raise ValueError("final_oos_start must equal the frozen boundary 2026-07-13")
    exposure = float(strategy.get("exposure_multiplier", 1.0))
    if exposure > 2.08:
        raise ValueError("exposure_multiplier must be <= 2.08")
    if exposure < 0:
        raise ValueError("exposure_multiplier must be non-negative")

    partitions = build_purged_walk_forward_partitions(
        trades,
        train_days=int(validation["train_days"]),
        validation_days=int(validation["validation_days"]),
        step_days=int(validation["step_days"]),
        embargo_days=int(validation["embargo_days"]),
        final_oos_start=str(validation["final_oos_start"]),
    )
    fold_reports = []
    for fold in partitions["folds"]:
        fold_reports.append(
            {
                **fold["metadata"],
                "train_metrics": _with_confidence(_fixed_sweep(fold["train_trades"], strategy)),
                "validation_metrics": _with_confidence(
                    _fixed_sweep(fold["validation_trades"], strategy)
                ),
            }
        )

    aggregate_rows = _unique_trades(partitions["folds"], "validation_trades")
    aggregate = _with_confidence(_fixed_sweep(aggregate_rows, strategy))
    selected_count = int(aggregate.get("selected_trade_count") or 0)
    minimum_trades = int(validation.get("minimum_oos_trades", 200))
    win_lower = float(aggregate.get("win_rate_wilson_95_lower_pct") or 0.0)
    rolling_return = aggregate.get("rolling_1y_latest_return_pct")
    full_rolling_window = bool(aggregate.get("rolling_1y_latest_full_window"))
    drawdown = aggregate.get("portfolio_max_drawdown_pct")
    target_win = float(strategy.get("target_win_rate_pct", 52.0))
    target_win_max = float(strategy.get("target_win_rate_max_pct", 60.0))
    target_return = float(strategy.get("target_one_year_return_pct", 50.0))
    target_drawdown = float(strategy.get("target_drawdown_pct", 15.0))
    target_profit_factor = float(strategy.get("target_profit_factor", 1.3))
    target_calmar = float(strategy.get("target_calmar", 1.5))
    if not 0.0 <= target_win <= target_win_max <= 100.0:
        raise ValueError("target win-rate band is invalid")
    observed_win = float(aggregate.get("trade_win_rate_pct") or 0.0)
    profit_factor = aggregate.get("trade_profit_factor")
    payoff_ratio = aggregate.get("trade_payoff_ratio")
    calmar = aggregate.get("calmar_latest_12m")
    if calmar is None:
        calmar = aggregate.get("portfolio_calmar_latest_1y")
    qualification = {
        "minimum_sample_pass": selected_count >= minimum_trades,
        "observed_win_rate_pct": observed_win,
        "observed_win_rate_pass": observed_win >= target_win,
        "observed_win_rate_ceiling_pass": observed_win <= target_win_max,
        "win_rate_confidence_pass": win_lower >= target_win,
        "signal_days_pass": int(aggregate.get("signal_days") or 0) >= 120,
        "payoff_ratio_pass": payoff_ratio is not None and float(payoff_ratio) >= 1.3,
        "full_rolling_12m_pass": full_rolling_window,
        "rolling_return_pass": full_rolling_window
        and rolling_return is not None
        and float(rolling_return) >= target_return,
        "drawdown_pass": drawdown is not None and abs(float(drawdown)) <= target_drawdown,
        "profit_factor_pass": (
            profit_factor is not None and float(profit_factor) >= target_profit_factor
        )
        or (
            selected_count > 0
            and int(aggregate.get("trade_nonwin_count") or 0) == 0
        ),
        "calmar_pass": (calmar is not None and float(calmar) >= target_calmar)
        or (
            rolling_return is not None
            and float(rolling_return) > 0
            and drawdown is not None
            and float(drawdown) == 0
        ),
        "target_profile": (
            f"primary_{target_return:.15g}_return_"
            f"{target_drawdown:.15g}_drawdown"
        ),
        "target_profile_values": {
            "one_year_return_pct": target_return,
            "max_drawdown_pct": target_drawdown,
            "win_rate_min_pct": target_win,
            "win_rate_max_pct": target_win_max,
            "profit_factor": target_profit_factor,
            "calmar": target_calmar,
        },
        "all_rolling_12m_stability_pass": bool(
            aggregate.get("target_rolling_12m_stability_pass")
        ),
    }
    qualification["profile_primary_gates_pass"] = all(
        qualification[key]
        for key in (
            "minimum_sample_pass",
            "observed_win_rate_pass",
            "observed_win_rate_ceiling_pass",
            "win_rate_confidence_pass",
            "signal_days_pass",
            "payoff_ratio_pass",
            "full_rolling_12m_pass",
            "all_rolling_12m_stability_pass",
            "rolling_return_pass",
            "drawdown_pass",
            "profit_factor_pass",
            "calmar_pass",
        )
    )
    qualification["profile_missing_gates"] = [
        key
        for key in (
            "minimum_sample_pass",
            "observed_win_rate_pass",
            "observed_win_rate_ceiling_pass",
            "win_rate_confidence_pass",
            "signal_days_pass",
            "payoff_ratio_pass",
            "full_rolling_12m_pass",
            "all_rolling_12m_stability_pass",
            "rolling_return_pass",
            "drawdown_pass",
            "profit_factor_pass",
            "calmar_pass",
        )
        if not qualification[key]
    ]
    qualification["development_primary_gates_pass"] = all(
        qualification[key]
        for key in (
            "minimum_sample_pass",
            "observed_win_rate_pass",
            "observed_win_rate_ceiling_pass",
            "full_rolling_12m_pass",
            "rolling_return_pass",
            "drawdown_pass",
            "profit_factor_pass",
            "calmar_pass",
        )
    )
    qualification["missing_completion_gates"] = [
        "sealed_final_oos",
        "baseline_and_double_cost_matrix",
        "regime_validation",
        "all_rolling_12m_windows",
        "multiple_testing_adjustment",
        "shadow_or_small_capital_validation",
    ]
    qualification["completion_pass"] = False
    qualification["all_pass"] = False

    strategy_payload = dict(strategy)
    validation_payload = dict(validation)
    strategy_sha256 = _sha256(strategy_payload)
    selection_replay = _strategy_selection_replay_receipt(
        candidate_pool_sha256=partitions["dataset_sha256"],
        candidate_pool_count=partitions["development_trade_count"],
        strategy_sha256=strategy_sha256,
        fold_reports=fold_reports,
        aggregate=aggregate,
    )
    return {
        "schema_version": 1,
        "dataset_sha256": partitions["dataset_sha256"],
        "strategy_sha256": strategy_sha256,
        "validation_sha256": _sha256(validation_payload),
        "strategy_selection_replay": selection_replay,
        "strategy": strategy_payload,
        "validation": validation_payload,
        "development_trade_count": partitions["development_trade_count"],
        "contaminated_diagnostic": partitions["contaminated_diagnostic"],
        "boundary_purged_trade_count": partitions["boundary_purged_trade_count"],
        "fold_count": len(fold_reports),
        "folds": fold_reports,
        "aggregate_validation": aggregate,
        "qualification": qualification,
        "final_oos": partitions["final_oos"],
    }


def _path_identity(path: Path, *, label: str) -> Tuple[int, int] | None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return None
    if path.is_symlink():
        raise ValueError(f"{label} path is an unsafe symlink")
    return (stat_result.st_dev, stat_result.st_ino)


def _assert_open_path_identity(path: Path, fd: int, *, label: str) -> Tuple[int, int]:
    path_identity = _path_identity(path, label=label)
    if path_identity is None:
        raise ValueError(f"{label} path was replaced or removed")
    fd_stat = os.fstat(fd)
    fd_identity = (fd_stat.st_dev, fd_stat.st_ino)
    if path_identity != fd_identity:
        raise ValueError(f"{label} path identity was replaced")
    return fd_identity


def _safe_open_fd(path: Path, flags: int, *, label: str, mode: int = 0o600) -> int:
    if _path_identity(path, label=label) is not None and path.is_symlink():
        raise ValueError(f"{label} path is an unsafe symlink")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags | nofollow, mode)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} path is unsafe or unavailable") from exc
    try:
        _assert_open_path_identity(path, fd, label=label)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _assert_ledger_guard(guard: Dict[str, Any]) -> None:
    lock_identity = _assert_open_path_identity(
        guard["lock_path"], guard["lock_fd"], label="ledger lock"
    )
    if lock_identity != guard.get("lock_identity"):
        raise ValueError("ledger lock path identity was replaced")
    lock_fd = guard["lock_fd"]
    os.lseek(lock_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(lock_fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(lock_fd, 0, os.SEEK_SET)
    if digest.hexdigest() != guard.get("lock_file_sha256"):
        raise ValueError("ledger lock contents changed")
    current_identity = _path_identity(guard["ledger_path"], label="ledger")
    if current_identity != guard["ledger_identity"]:
        raise ValueError("ledger path identity was replaced")


def _read_ledger_fd_snapshot(fd: int) -> tuple[bytes, Dict[str, Any]]:
    """Read the ledger through fd and return raw bytes plus a snapshot dict.

    The snapshot's purpose is TOCTOU detection. The caller already holds the
    cross-process ledger lock (see _assert_ledger_guard), so this function only
    needs to detect mid-read truncation/extension that would corrupt the sha256
    we return. We do NOT trust fstat.st_size on Windows: NTFS may report a size
    that does not match the bytes actually readable through the fd in some
    append+fsync states. lseek(SEEK_END) is more reliable but still not a
    ground truth. We therefore read until EOF and rely on the caller's sha256
    comparison against raw_before + encoded_payload for correctness; the size
    fields here are diagnostic only.
    """
    opened = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    raw = b"".join(chunks)
    return raw, {
        "identity": (opened.st_dev, opened.st_ino),
        "size": len(raw),
        "mtime_ns": opened.st_mtime_ns,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("ledger append made no progress")
        view = view[written:]


def _read_ledger(
    path: Path,
    guard: Dict[str, Any] | None = None,
    *,
    expected_file_sha256: str | None = None,
) -> List[Dict[str, Any]]:
    if expected_file_sha256 is not None:
        _require_control_sha256(expected_file_sha256, "ledger file hash")
    if guard is not None:
        _assert_ledger_guard(guard)
    identity = _path_identity(path, label="ledger")
    if identity is None:
        return []
    if guard is not None and identity != guard["ledger_identity"]:
        raise ValueError("ledger path identity was replaced")
    rows = []
    previous_hash = None
    fd = _safe_open_fd(path, os.O_RDONLY, label="ledger")
    if guard is not None and _assert_open_path_identity(path, fd, label="ledger") != identity:
        os.close(fd)
        raise ValueError("ledger path identity was replaced")
    try:
        raw, snapshot = _read_ledger_fd_snapshot(fd)
    finally:
        os.close(fd)
    if expected_file_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_file_sha256:
        raise ValueError("ledger file contents changed")
    if guard is not None:
        guard["ledger_read_snapshot"] = snapshot
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid experiment ledger at line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid experiment ledger row at line {line_number}")
            if row.get("schema_version") != "research-experiment-event/v1":
                raise ValueError(f"invalid experiment ledger schema at line {line_number}")
            if row.get("sequence") != line_number:
                raise ValueError(f"invalid experiment ledger sequence at line {line_number}")
            if row.get("previous_record_hash") != previous_hash:
                raise ValueError(f"record chain mismatch at line {line_number}")
            stored_hash = row.get("record_hash")
            unhashed = dict(row)
            unhashed.pop("record_hash", None)
            if not isinstance(stored_hash, str) or _sha256(unhashed) != stored_hash:
                raise ValueError(f"record hash mismatch at line {line_number}")
            previous_hash = stored_hash
            rows.append(row)
    return rows


@contextmanager
def _ledger_lock(
    ledger_path: Path,
    exclusive: bool,
    *,
    expected_ledger_identity: Tuple[int, int] | None = None,
    expected_lock_identity: Tuple[int, int] | None = None,
    expected_lock_file_sha256: str | None = None,
    require_existing_lock: bool = False,
):
    if expected_ledger_identity is not None and (
        not isinstance(expected_ledger_identity, tuple)
        or len(expected_ledger_identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in expected_ledger_identity
        )
    ):
        raise ValueError("expected ledger identity is invalid")
    if expected_lock_identity is not None and (
        not isinstance(expected_lock_identity, tuple)
        or len(expected_lock_identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in expected_lock_identity
        )
    ):
        raise ValueError("expected ledger lock identity is invalid")
    if expected_lock_file_sha256 is not None and (
        not isinstance(expected_lock_file_sha256, str)
        or len(expected_lock_file_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_lock_file_sha256)
    ):
        raise ValueError("expected ledger lock hash is invalid")
    if (expected_lock_identity is None) != (expected_lock_file_sha256 is None):
        raise ValueError("expected ledger lock binding is incomplete")
    if not isinstance(require_existing_lock, bool):
        raise ValueError("ledger lock existence requirement is invalid")
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    bootstrap_mutex_held = False
    _LEDGER_BOOTSTRAP_MUTEX.acquire()
    bootstrap_mutex_held = True
    try:
        ledger_identity_before = _path_identity(ledger_path, label="ledger")
        lock_identity_before = _path_identity(lock_path, label="ledger lock")
        if (ledger_identity_before is None) != (lock_identity_before is None):
            raise ValueError("ledger and lock bootstrap state is inconsistent")
        bootstrap = ledger_identity_before is None and lock_identity_before is None
        if bootstrap and not exclusive:
            raise ValueError("ledger bootstrap requires an exclusive lock")
        if lock_identity_before is None and (
            expected_lock_identity is not None or require_existing_lock
        ):
            raise ValueError("ledger lock path was replaced or removed")
        if bootstrap:
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            _LEDGER_BOOTSTRAP_MUTEX.release()
            bootstrap_mutex_held = False
        lock_fd = _safe_open_fd(
            lock_path,
            os.O_RDWR
            | (
                0
                if lock_identity_before is not None
                else os.O_CREAT | os.O_EXCL
            ),
            label="ledger lock",
        )
    except BaseException:
        if bootstrap_mutex_held:
            _LEDGER_BOOTSTRAP_MUTEX.release()
        raise
    try:
        if bootstrap:
            if os.write(lock_fd, _LEDGER_LOCK_MARKER) != len(_LEDGER_LOCK_MARKER):
                raise RuntimeError("ledger lock marker write was incomplete")
            os.fsync(lock_fd)
        if _fcntl is not None:
            _fcntl.flock(
                lock_fd,
                _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH,
            )
        elif _msvcrt is not None:
            if os.fstat(lock_fd).st_size == 0:
                raise ValueError("ledger lock is empty")
            os.lseek(lock_fd, 0, os.SEEK_SET)
            _msvcrt.locking(
                lock_fd,
                _msvcrt.LK_LOCK if exclusive else _msvcrt.LK_RLCK,
                1,
            )
        else:  # pragma: no cover - every supported platform has one backend.
            raise RuntimeError("no supported cross-process ledger lock backend")
        ledger_identity = _path_identity(ledger_path, label="ledger")
        if ledger_identity != ledger_identity_before:
            raise ValueError("ledger path identity was replaced")
        if (
            expected_ledger_identity is not None
            and ledger_identity != expected_ledger_identity
        ):
            raise ValueError("ledger path identity was replaced")
        lock_identity = _assert_open_path_identity(lock_path, lock_fd, label="ledger lock")
        if (
            lock_identity_before is not None
            and lock_identity != lock_identity_before
        ):
            raise ValueError("ledger lock path identity was replaced")
        if expected_lock_identity is not None and lock_identity != expected_lock_identity:
            raise ValueError("ledger lock path identity was replaced")
        lock_stat = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise ValueError("ledger lock path is unsafe")
        if lock_stat.st_size == 0:
            raise ValueError("ledger lock is empty")
        if lock_stat.st_size != len(_LEDGER_LOCK_MARKER):
            raise ValueError("ledger lock marker is invalid")
        os.lseek(lock_fd, 0, os.SEEK_SET)
        if os.read(lock_fd, len(_LEDGER_LOCK_MARKER)) != _LEDGER_LOCK_MARKER:
            raise ValueError("ledger lock marker is invalid")
        os.lseek(lock_fd, 0, os.SEEK_SET)
        lock_digest = hashlib.sha256()
        while True:
            chunk = os.read(lock_fd, 1024 * 1024)
            if not chunk:
                break
            lock_digest.update(chunk)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        lock_file_sha256 = lock_digest.hexdigest()
        if (
            expected_lock_file_sha256 is not None
            and lock_file_sha256 != expected_lock_file_sha256
        ):
            raise ValueError("ledger lock contents changed")
        guard = {
            "ledger_path": ledger_path,
            "ledger_identity": ledger_identity,
            "lock_path": lock_path,
            "lock_fd": lock_fd,
            "lock_identity": lock_identity,
            "lock_file_sha256": lock_file_sha256,
        }
        _assert_ledger_guard(guard)
        try:
            yield guard
        finally:
            _assert_ledger_guard(guard)
            if _fcntl is not None:
                _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                os.lseek(lock_fd, 0, os.SEEK_SET)
                _msvcrt.locking(lock_fd, _msvcrt.LK_UNLCK, 1)
    finally:
        os.close(lock_fd)
        if bootstrap_mutex_held:
            _LEDGER_BOOTSTRAP_MUTEX.release()


def read_experiment_ledger(
    path: str, *, require_existing_lock: bool = False
) -> List[Dict[str, Any]]:
    ledger_path = Path(path)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    ledger_identity = _path_identity(ledger_path, label="ledger")
    lock_identity = _path_identity(lock_path, label="ledger lock")
    if ledger_identity is None and lock_identity is None:
        if require_existing_lock:
            raise ValueError("ledger lock path was replaced or removed")
        return []
    if (ledger_identity is None) != (lock_identity is None):
        raise ValueError("ledger and lock bootstrap state is inconsistent")
    with _ledger_lock(
        ledger_path,
        exclusive=False,
        require_existing_lock=require_existing_lock,
    ) as guard:
        return _read_ledger(ledger_path, guard)


_PRECOMPUTE_LIFECYCLE_V1 = {
    "schema_version": "research-precompute-lifecycle/v1",
    "launch_started_event_schema": "research-precompute-launch-started/v1",
    "launcher_ready_schema": "research-launcher-ready/v4",
    "run_result_schema": "research-precompute-run-result/v3",
    "global_tip_cas": True,
    "sole_nonterminal_cas": True,
}


def _precompute_lifecycle_v2(legacy_quarantine_sha256: str) -> Dict[str, Any]:
    _require_control_sha256(
        legacy_quarantine_sha256, "legacy quarantine binding hash"
    )
    return {
        "schema_version": "research-precompute-lifecycle/v2",
        "launch_started_event_schema": "research-precompute-launch-started/v2",
        "launcher_ready_schema": "research-launcher-ready/v5",
        "run_result_schema": "research-precompute-run-result/v4",
        "global_tip_cas": True,
        "exact_quarantine_open_set_cas": True,
        "legacy_quarantine_sha256": legacy_quarantine_sha256,
    }


def _require_v3_launch_registration(registered: Dict[str, Any]) -> Dict[str, Any]:
    contract = registered.get("registration_contract")
    input_plan = contract.get("input_plan") if isinstance(contract, dict) else None
    plan = input_plan.get("payload") if isinstance(input_plan, dict) else None
    artifact = input_plan.get("artifact") if isinstance(input_plan, dict) else None
    execution = plan.get("precompute_execution") if isinstance(plan, dict) else None
    control = contract.get("precompute_control") if isinstance(contract, dict) else None
    ledger = contract.get("precompute_ledger") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or contract.get("schema_version") != "research-validation-registration/v3"
        or registered.get("registration_contract_sha256") != _sha256(contract)
        or not isinstance(input_plan, dict)
        or set(input_plan) != {"payload", "artifact"}
        or not isinstance(artifact, dict)
        or registered.get("input_plan_artifact") != artifact
        or not isinstance(plan, dict)
        or plan.get("schema_version") != "research-treatment-input-plan/v5"
        or not isinstance(execution, dict)
        or execution.get("schema_version")
        != "research-precompute-execution-plan/v2"
        or execution.get("control_required") is not True
        or not isinstance(control, dict)
        or control.get("schema_version") != "research-precompute-control-binding/v2"
        or not isinstance(ledger, dict)
        or ledger.get("schema_version") != "research-precompute-ledger-binding/v3"
        or contract.get("precompute_lifecycle") != _PRECOMPUTE_LIFECYCLE_V1
        or isinstance(registered.get("sequence"), bool)
        or not isinstance(registered.get("sequence"), int)
        or isinstance(control.get("minimum_registration_sequence_exclusive"), bool)
        or not isinstance(
            control.get("minimum_registration_sequence_exclusive"), int
        )
        or registered["sequence"]
        <= control["minimum_registration_sequence_exclusive"]
        or isinstance(ledger.get("expected_tip_sequence"), bool)
        or not isinstance(ledger.get("expected_tip_sequence"), int)
        or registered["sequence"] != ledger["expected_tip_sequence"] + 1
        or registered.get("previous_record_hash")
        != ledger.get("expected_tip_record_hash")
    ):
        raise ValueError("precompute launch requires the exact v3 launch protocol")
    for field in (
        "parent_proof_file_sha256",
        "parent_proof_canonical_sha256",
        "control_source_bundle_sha256",
    ):
        _require_control_sha256(control.get(field), field.replace("_", " "))
    for field in (
        "ledger_path_sha256",
        "lock_path_sha256",
        "lock_file_sha256",
    ):
        _require_control_sha256(ledger.get(field), field.replace("_", " "))
    return {
        "contract": contract,
        "control": control,
        "ledger": ledger,
    }


def _require_v4_launch_registration(registered: Dict[str, Any]) -> Dict[str, Any]:
    contract = registered.get("registration_contract")
    input_plan = contract.get("input_plan") if isinstance(contract, dict) else None
    plan = input_plan.get("payload") if isinstance(input_plan, dict) else None
    artifact = input_plan.get("artifact") if isinstance(input_plan, dict) else None
    execution = plan.get("precompute_execution") if isinstance(plan, dict) else None
    control = contract.get("precompute_control") if isinstance(contract, dict) else None
    ledger = contract.get("precompute_ledger") if isinstance(contract, dict) else None
    v7_plan = isinstance(plan, dict) and plan.get("schema_version") == (
        "research-treatment-input-plan/v7"
    )
    lifecycle = contract.get("precompute_lifecycle") if isinstance(contract, dict) else None
    legacy_quarantine = (
        contract.get("legacy_quarantine") if isinstance(contract, dict) else None
    )
    try:
        quarantine = validate_frozen_quarantine_binding_v1(legacy_quarantine)
        quarantine_sha256 = quarantine_binding_sha256_v1(quarantine)
    except ValueError as exc:
        raise ValueError("precompute launch requires the exact v4 launch protocol") from exc
    expected_contract_fields = {
        "schema_version",
        "intent",
        "strategy",
        "validation",
        "temporal_authority",
        "input_plan",
        "precompute_control",
        "precompute_ledger",
        "precompute_lifecycle",
        "legacy_quarantine",
        "legacy_quarantine_sha256",
    }
    if (
        not isinstance(contract, dict)
        or set(contract) != expected_contract_fields
        or contract.get("schema_version") != "research-validation-registration/v4"
        or registered.get("registration_contract_sha256") != _sha256(contract)
        or not isinstance(input_plan, dict)
        or set(input_plan) != {"payload", "artifact"}
        or not isinstance(artifact, dict)
        or registered.get("input_plan_artifact") != artifact
        or not isinstance(plan, dict)
        or plan.get("schema_version")
        not in {
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
        or not isinstance(execution, dict)
        or execution.get("schema_version")
        != (
            "research-precompute-execution-plan/v4"
            if v7_plan
            else "research-precompute-execution-plan/v3"
        )
        or execution.get("control_required") is not True
        or plan.get("legacy_quarantine") != quarantine
        or execution.get("legacy_quarantine_sha256") != quarantine_sha256
        or contract.get("legacy_quarantine") != quarantine
        or contract.get("legacy_quarantine_sha256") != quarantine_sha256
        or lifecycle != _precompute_lifecycle_v2(quarantine_sha256)
        or not isinstance(control, dict)
        or control.get("schema_version") != "research-precompute-control-binding/v2"
        or control.get("plan_sha256") != plan.get("plan_sha256")
        or control.get("plan_file_sha256") != artifact.get("sha256")
        or not isinstance(ledger, dict)
        or ledger.get("schema_version")
        != (
            "research-precompute-ledger-binding/v4"
            if v7_plan
            else "research-precompute-ledger-binding/v3"
        )
        or isinstance(registered.get("sequence"), bool)
        or not isinstance(registered.get("sequence"), int)
        or isinstance(control.get("minimum_registration_sequence_exclusive"), bool)
        or not isinstance(
            control.get("minimum_registration_sequence_exclusive"), int
        )
        or registered["sequence"]
        <= control["minimum_registration_sequence_exclusive"]
        or isinstance(ledger.get("expected_tip_sequence"), bool)
        or not isinstance(ledger.get("expected_tip_sequence"), int)
        or registered["sequence"] != ledger["expected_tip_sequence"] + 1
        or registered.get("previous_record_hash")
        != ledger.get("expected_tip_record_hash")
    ):
        raise ValueError("precompute launch requires the exact v4 launch protocol")
    for field in (
        "parent_proof_file_sha256",
        "parent_proof_canonical_sha256",
        "control_source_bundle_sha256",
    ):
        _require_control_sha256(control.get(field), field.replace("_", " "))
    for field in (
        "ledger_path_sha256",
        "lock_path_sha256",
        "lock_file_sha256",
    ):
        _require_control_sha256(ledger.get(field), field.replace("_", " "))
    if v7_plan:
        binding = plan.get("control_request_binding")
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "schema_version",
                "request_sha256",
                "control_source_bundle_sha256",
                "ledger",
            }
            or binding.get("schema_version") != "research-control-request-binding/v1"
            or binding.get("control_source_bundle_sha256")
            != control.get("control_source_bundle_sha256")
            or not isinstance(binding.get("ledger"), dict)
            or binding["ledger"].get("path") != ledger.get("ledger_path")
            or binding["ledger"].get("expected_tip_sequence")
            != ledger.get("expected_tip_sequence")
            or binding["ledger"].get("expected_tip_record_hash")
            != ledger.get("expected_tip_record_hash")
            or binding["ledger"].get("expected_lock_file_sha256")
            != ledger.get("lock_file_sha256")
            or binding["ledger"].get("expected_file_sha256")
            != ledger.get("pre_registration_ledger_file_sha256")
            or execution.get("control_request_binding_sha256") != _sha256(binding)
        ):
            raise ValueError("precompute launch requires the exact v4 request binding")
        _require_control_sha256(binding.get("request_sha256"), "request sha256")
        _require_control_sha256(
            ledger.get("pre_registration_ledger_file_sha256"),
            "pre-registration ledger file hash",
        )
    return {
        "contract": contract,
        "control": control,
        "ledger": ledger,
        "quarantine": quarantine,
        "legacy_quarantine_sha256": quarantine_sha256,
    }


def _require_controlled_launch_registration(registered: Dict[str, Any]) -> Dict[str, Any]:
    contract = registered.get("registration_contract")
    schema = contract.get("schema_version") if isinstance(contract, dict) else None
    if schema == "research-validation-registration/v4":
        return _require_v4_launch_registration(registered)
    return _require_v3_launch_registration(registered)


def _validate_launch_started_event(
    event: Dict[str, Any], registered: Dict[str, Any]
) -> None:
    binding = _require_controlled_launch_registration(registered)
    control = binding["control"]
    ledger = binding["ledger"]
    contract_schema = binding["contract"]["schema_version"]
    expected = {
        "event_id": f"{registered['experiment_id']}:precompute_launch_started",
        "experiment_id": registered["experiment_id"],
        "event_type": "precompute_launch_started",
        "precompute_launch_started_schema_version": (
            "research-precompute-launch-started/v2"
            if contract_schema == "research-validation-registration/v4"
            else "research-precompute-launch-started/v1"
        ),
        "registered_record_hash": registered["record_hash"],
        "registered_sequence": registered["sequence"],
        "registration_contract_sha256": registered[
            "registration_contract_sha256"
        ],
        "run_claim_file_sha256": event.get("run_claim_file_sha256"),
        "launch_lease_file_sha256": event.get("launch_lease_file_sha256"),
        "parent_proof_file_sha256": control["parent_proof_file_sha256"],
        "parent_proof_canonical_sha256": control[
            "parent_proof_canonical_sha256"
        ],
        "control_source_bundle_sha256": control[
            "control_source_bundle_sha256"
        ],
        "ledger_path_sha256": ledger["ledger_path_sha256"],
        "launch_attempt": 1,
        "single_launch": True,
    }
    if contract_schema == "research-validation-registration/v4":
        expected["legacy_quarantine_sha256"] = binding[
            "legacy_quarantine_sha256"
        ]
    for field in ("run_claim_file_sha256", "launch_lease_file_sha256"):
        _require_control_sha256(event.get(field), field.replace("_", " "))
    if event != expected:
        raise ValueError("precompute launch started event binding is invalid")


def _validate_event_transition(experiment_rows: List[Dict[str, Any]], event_type: str) -> None:
    if not experiment_rows:
        if event_type != "registered":
            raise ValueError("experiment must be registered first")
        return
    last_row = experiment_rows[-1]
    last_type = str(last_row.get("event_type") or "")
    allowed = {
        "registered": {
            "completed",
            "failed",
            "aborted",
        },
        "precompute_launch_started": {"precompute_completed", "failed", "aborted"},
        "precompute_completed": {"validation_started", "failed", "aborted"},
        "validation_started": {"completed", "failed", "aborted"},
        "completed": {"reviewed", "decision"},
        "reviewed": {"decision"},
        "decision": set(),
        "failed": set(),
        "aborted": set(),
    }
    if last_type == "registered" and "registration_contract" in last_row:
        contract = last_row.get("registration_contract")
        input_plan = contract.get("input_plan") if isinstance(contract, dict) else None
        plan = input_plan.get("payload") if isinstance(input_plan, dict) else None
        execution = plan.get("precompute_execution") if isinstance(plan, dict) else None
        controlled_plan_schema = plan.get("schema_version") if isinstance(plan, dict) else None
        controlled_v4 = (
            isinstance(contract, dict)
            and contract.get("schema_version")
            == "research-validation-registration/v2"
            and isinstance(plan, dict)
            and controlled_plan_schema == "research-treatment-input-plan/v4"
            and isinstance(execution, dict)
            and execution.get("schema_version")
            == "research-precompute-execution-plan/v1"
            and execution.get("control_required") is True
        )
        controlled_v5 = (
            isinstance(contract, dict)
            and contract.get("schema_version")
            == "research-validation-registration/v3"
            and controlled_plan_schema == "research-treatment-input-plan/v5"
            and isinstance(execution, dict)
            and execution.get("schema_version")
            == "research-precompute-execution-plan/v2"
            and execution.get("control_required") is True
        )
        controlled_v6 = (
            isinstance(contract, dict)
            and contract.get("schema_version")
            == "research-validation-registration/v4"
            and controlled_plan_schema
            in {
                "research-treatment-input-plan/v6",
                "research-treatment-input-plan/v7",
            }
            and isinstance(execution, dict)
            and execution.get("schema_version")
            in {
                "research-precompute-execution-plan/v3",
                "research-precompute-execution-plan/v4",
            }
            and execution.get("control_required") is True
        )
        if controlled_v6:
            if event_type == "precompute_launch_started":
                _require_v4_launch_registration(last_row)
            allowed["registered"] = {
                "precompute_launch_started",
                "failed",
                "aborted",
            }
        elif controlled_v5:
            if event_type == "precompute_launch_started":
                _require_v3_launch_registration(last_row)
            allowed["registered"] = {
                "precompute_launch_started",
                "failed",
                "aborted",
            }
        elif controlled_v4:
            allowed["registered"] = {"precompute_completed", "failed", "aborted"}
        else:
            allowed["registered"] = {"validation_started", "failed", "aborted"}
    if event_type not in allowed.get(last_type, set()):
        raise ValueError(f"invalid experiment transition: {last_type} -> {event_type}")


def _assert_controlled_ledger_identity_locked(
    registered: Dict[str, Any], guard: Dict[str, Any]
) -> None:
    contract = registered.get("registration_contract")
    binding = contract.get("precompute_ledger") if isinstance(contract, dict) else None
    if not isinstance(binding, dict) or binding.get("schema_version") not in {
        "research-precompute-ledger-binding/v2",
        "research-precompute-ledger-binding/v3",
        "research-precompute-ledger-binding/v4",
    }:
        return
    device = binding.get("ledger_device")
    inode = binding.get("ledger_inode")
    expected_path_raw = binding.get("ledger_path")
    expected_path_sha256 = binding.get("ledger_path_sha256")
    actual_path = guard.get("ledger_path")
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
        or not isinstance(expected_path_raw, str)
        or not expected_path_raw
        or not isinstance(actual_path, Path)
    ):
        raise ValueError("controlled ledger identity binding is invalid")
    expected_path = Path(expected_path_raw)
    if (
        not expected_path.is_absolute()
        or actual_path != expected_path
        or actual_path.resolve(strict=False) != expected_path
        or expected_path_sha256
        != hashlib.sha256(expected_path_raw.encode("utf-8")).hexdigest()
        or actual_path.stat().st_nlink != 1
    ):
        raise ValueError("controlled ledger path binding mismatch")
    if guard.get("ledger_identity") != (device, inode):
        raise ValueError("controlled ledger path identity was replaced")
    if binding.get("schema_version") == "research-precompute-ledger-binding/v2":
        return
    lock_device = binding.get("lock_device")
    lock_inode = binding.get("lock_inode")
    lock_path_raw = binding.get("lock_path")
    lock_path_sha256 = binding.get("lock_path_sha256")
    lock_file_sha256 = binding.get("lock_file_sha256")
    actual_lock_path = guard.get("lock_path")
    if (
        isinstance(lock_device, bool)
        or not isinstance(lock_device, int)
        or lock_device <= 0
        or isinstance(lock_inode, bool)
        or not isinstance(lock_inode, int)
        or lock_inode <= 0
        or not isinstance(lock_path_raw, str)
        or not lock_path_raw
        or not isinstance(actual_lock_path, Path)
        or not isinstance(lock_file_sha256, str)
        or len(lock_file_sha256) != 64
        or any(character not in "0123456789abcdef" for character in lock_file_sha256)
    ):
        raise ValueError("controlled ledger lock binding is invalid")
    expected_lock_path = Path(lock_path_raw)
    if (
        not expected_lock_path.is_absolute()
        or actual_lock_path != expected_lock_path
        or actual_lock_path.resolve(strict=False) != expected_lock_path
        or lock_path_sha256
        != hashlib.sha256(lock_path_raw.encode("utf-8")).hexdigest()
        or actual_lock_path.stat().st_nlink != 1
        or guard.get("lock_identity") != (lock_device, lock_inode)
        or guard.get("lock_file_sha256") != lock_file_sha256
    ):
        raise ValueError("controlled ledger lock binding mismatch")


def _append_experiment_event_locked(
    ledger_path: Path,
    rows: List[Dict[str, Any]],
    event: Dict[str, Any],
    guard: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    experiment_id = str(event.get("experiment_id") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id is required")
    if not event_id:
        raise ValueError("event_id is required")
    if event_type not in {
        "registered",
        "precompute_launch_started",
        "precompute_completed",
        "validation_started",
        "completed",
        "failed",
        "aborted",
        "reviewed",
        "decision",
    }:
        raise ValueError("unsupported event_type")
    if any(str(item.get("event_id")) == event_id for item in rows):
        raise ValueError(f"event_id already exists: {event_id}")
    experiment_rows = [
        item for item in rows if str(item.get("experiment_id")) == experiment_id
    ]
    _validate_event_transition(experiment_rows, event_type)
    if event_type == "precompute_launch_started":
        _validate_launch_started_event(dict(event), experiment_rows[-1])

    previous_hash = rows[-1].get("record_hash") if rows else None
    payload = dict(event)
    payload["schema_version"] = "research-experiment-event/v1"
    payload["sequence"] = len(rows) + 1
    payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    payload["previous_record_hash"] = previous_hash
    payload["record_hash"] = _sha256(payload)
    if guard is not None:
        _assert_ledger_guard(guard)
    expected_identity = guard["ledger_identity"] if guard is not None else None
    expected_snapshot = (
        guard.get("ledger_read_snapshot")
        if guard is not None and expected_identity is not None
        else None
    )
    if expected_snapshot is not None and (
        not isinstance(expected_snapshot, dict)
        or set(expected_snapshot) != {"identity", "size", "mtime_ns", "sha256"}
        or expected_snapshot.get("identity") != expected_identity
        or not isinstance(expected_snapshot.get("size"), int)
        or expected_snapshot["size"] < 0
        or not isinstance(expected_snapshot.get("mtime_ns"), int)
        or not isinstance(expected_snapshot.get("sha256"), str)
        or len(expected_snapshot["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in expected_snapshot["sha256"])
    ):
        raise ValueError("ledger read snapshot is invalid")
    if guard is not None and expected_identity is not None and expected_snapshot is None:
        raise ValueError("ledger read snapshot is missing")
    flags = os.O_RDWR | os.O_APPEND
    if expected_identity is None:
        flags |= os.O_CREAT | os.O_EXCL
    fd = _safe_open_fd(ledger_path, flags, label="ledger")
    opened_identity = _assert_open_path_identity(ledger_path, fd, label="ledger")
    if expected_identity is not None and opened_identity != expected_identity:
        os.close(fd)
        raise ValueError("ledger path identity was replaced")
    if guard is not None and expected_identity is None:
        guard["ledger_identity"] = opened_identity
    raw_before = b""
    try:
        if expected_snapshot is not None:
            raw_before, observed_snapshot = _read_ledger_fd_snapshot(fd)
            if observed_snapshot != expected_snapshot:
                raise ValueError("ledger file contents changed before append")
        encoded_payload = (_canonical_json(payload) + "\n").encode("utf-8")
        _write_all(fd, encoded_payload)
        os.fsync(fd)
        _assert_open_path_identity(ledger_path, fd, label="ledger")
        if guard is not None:
            raw_after, after_snapshot = _read_ledger_fd_snapshot(fd)
            if raw_after != raw_before + encoded_payload:
                raise ValueError("ledger file contents changed during append")
            guard["ledger_read_snapshot"] = after_snapshot
    finally:
        os.close(fd)
    if guard is not None:
        _assert_ledger_guard(guard)
    rows.append(payload)
    return payload


def append_experiment_event(path: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Append one lifecycle event to a hash-chained experiment ledger."""

    event = _freeze_json_mapping(event, label="ledger event")
    ledger_path = Path(path)
    experiment_id = str(event.get("experiment_id") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id is required")
    if not event_id:
        raise ValueError("event_id is required")
    if event_type not in {
        "registered",
        "precompute_launch_started",
        "precompute_completed",
        "validation_started",
        "completed",
        "failed",
        "aborted",
        "reviewed",
        "decision",
    }:
        raise ValueError("unsupported event_type")
    _canonical_json(event)

    contract = event.get("registration_contract")
    if (
        event_type == "registered"
        and isinstance(contract, dict)
        and contract.get("schema_version")
        in {"research-validation-registration/v3", "research-validation-registration/v4"}
    ):
        raise ValueError("controlled registration requires the ledger CAS API")
    if event_type in {"precompute_launch_started", "precompute_completed"}:
        raise ValueError("dedicated precompute CAS API is required")

    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    ledger_identity = _path_identity(ledger_path, label="ledger")
    lock_identity = _path_identity(lock_path, label="ledger lock")
    if ledger_identity is None and lock_identity is None and event_type != "registered":
        raise ValueError("experiment must be registered first")
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        latest_by_experiment: Dict[str, Dict[str, Any]] = {}
        registrations_by_experiment: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            row_experiment_id = str(row.get("experiment_id"))
            latest_by_experiment[row_experiment_id] = row
            if row.get("event_type") == "registered":
                registrations_by_experiment[row_experiment_id] = row
        if any(
            row.get("event_type") == "precompute_launch_started"
            for row in latest_by_experiment.values()
        ):
            raise ValueError("active precompute launch blocks generic ledger append")
        if any(
            isinstance(
                registrations_by_experiment.get(item_experiment_id, {}).get(
                    "registration_contract"
                ),
                dict,
            )
            and registrations_by_experiment[item_experiment_id][
                "registration_contract"
            ].get("schema_version")
            in {"research-validation-registration/v3", "research-validation-registration/v4"}
            and row.get("event_type") not in {"decision", "failed", "aborted"}
            for item_experiment_id, row in latest_by_experiment.items()
        ):
            raise ValueError("active controlled v3 experiment blocks generic ledger append")
        experiment_id = str(event.get("experiment_id") or "") if isinstance(event, dict) else ""
        experiment_rows = [
            row for row in rows if str(row.get("experiment_id")) == experiment_id
        ]
        registered_contract = None
        if experiment_rows:
            registered = next(
                (
                    row
                    for row in experiment_rows
                    if row.get("event_type") == "registered"
                ),
                None,
            )
            registered_contract = (
                registered.get("registration_contract")
                if isinstance(registered, dict)
                else None
            )
        if (
            isinstance(registered_contract, dict)
            and registered_contract.get("schema_version")
            in {"research-validation-registration/v3", "research-validation-registration/v4"}
        ):
            raise ValueError(
                "controlled v3 lifecycle requires the dedicated lifecycle CAS API"
            )
        return _append_experiment_event_locked(ledger_path, rows, event, guard)


def _require_v3_registration_cas_binding(
    event: Dict[str, Any],
    *,
    expected_sequence: int,
    expected_record_hash: str | None,
    expected_ledger_identity: Tuple[int, int] | None,
    expected_lock_identity: Tuple[int, int] | None,
    expected_lock_file_sha256: str | None,
) -> Dict[str, Any] | None:
    contract = event.get("registration_contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != (
        "research-validation-registration/v3"
    ):
        return None
    candidate = dict(event)
    candidate["sequence"] = expected_sequence + 1
    candidate["previous_record_hash"] = expected_record_hash
    binding = _require_v3_launch_registration(candidate)["ledger"]
    expected_ledger = (binding.get("ledger_device"), binding.get("ledger_inode"))
    expected_lock = (binding.get("lock_device"), binding.get("lock_inode"))
    if (
        expected_ledger_identity != expected_ledger
        or expected_lock_identity != expected_lock
        or expected_lock_file_sha256 != binding.get("lock_file_sha256")
    ):
        raise ValueError("precompute registration requires the exact v3 launch protocol")
    return binding


def _require_v4_registration_cas_binding(
    event: Dict[str, Any],
    *,
    expected_sequence: int,
    expected_record_hash: str | None,
    allowed_nonterminal_records: List[Dict[str, Any]],
    minimum_sequence_exclusive: int,
    expected_ledger_identity: Tuple[int, int] | None,
    expected_lock_identity: Tuple[int, int] | None,
    expected_lock_file_sha256: str | None,
    expected_ledger_file_sha256: str | None,
) -> Dict[str, Any] | None:
    contract = event.get("registration_contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != (
        "research-validation-registration/v4"
    ):
        return None
    candidate = dict(event)
    candidate["sequence"] = expected_sequence + 1
    candidate["previous_record_hash"] = expected_record_hash
    binding = _require_v4_launch_registration(candidate)
    ledger = binding["ledger"]
    expected_ledger = (ledger.get("ledger_device"), ledger.get("ledger_inode"))
    expected_lock = (ledger.get("lock_device"), ledger.get("lock_inode"))
    if (
        expected_ledger_identity != expected_ledger
        or expected_lock_identity != expected_lock
        or expected_lock_file_sha256 != ledger.get("lock_file_sha256")
        or minimum_sequence_exclusive
        != binding["control"].get("minimum_registration_sequence_exclusive")
        or allowed_nonterminal_records != binding["quarantine"]["records"]
    ):
        raise ValueError("precompute registration requires the exact v4 launch protocol")
    plan = contract.get("input_plan", {}).get("payload")
    if isinstance(plan, dict) and plan.get("schema_version") == (
        "research-treatment-input-plan/v7"
    ) and expected_ledger_file_sha256 != ledger.get(
        "pre_registration_ledger_file_sha256"
    ):
        raise ValueError("precompute registration requires the exact v4 launch protocol")
    return binding


def register_experiment_if_tip_matches(
    path: str,
    event: Dict[str, Any],
    *,
    expected_sequence: int,
    expected_record_hash: str | None,
    allowed_nonterminal_records: List[Dict[str, Any]],
    minimum_sequence_exclusive: int,
    expected_ledger_identity: Tuple[int, int] | None = None,
    expected_lock_identity: Tuple[int, int] | None = None,
    expected_lock_file_sha256: str | None = None,
    expected_ledger_file_sha256: str | None = None,
) -> Dict[str, Any]:
    """Atomically compare the ledger control state and append one registration."""

    event = _freeze_json_mapping(event, label="controlled ledger event")
    allowed_nonterminal_records = _freeze_json_list(
        allowed_nonterminal_records, label="allowed nonterminal records"
    )
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int):
        raise ValueError("expected ledger sequence is invalid")
    if expected_sequence < 0:
        raise ValueError("expected ledger sequence is invalid")
    if (
        isinstance(minimum_sequence_exclusive, bool)
        or not isinstance(minimum_sequence_exclusive, int)
        or minimum_sequence_exclusive < 0
    ):
        raise ValueError("minimum ledger sequence is invalid")
    if expected_sequence == 0:
        if expected_record_hash is not None:
            raise ValueError("expected ledger tip hash is invalid")
    elif (
        not isinstance(expected_record_hash, str)
        or len(expected_record_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_record_hash)
    ):
        raise ValueError("expected ledger tip hash is invalid")
    if event.get("event_type") != "registered":
        raise ValueError("controlled ledger event must be a registration")
    experiment_id = str(event.get("experiment_id") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id is required")
    if expected_ledger_file_sha256 is not None:
        _require_control_sha256(expected_ledger_file_sha256, "expected ledger file hash")

    v3_binding = _require_v3_registration_cas_binding(
        event,
        expected_sequence=expected_sequence,
        expected_record_hash=expected_record_hash,
        expected_ledger_identity=expected_ledger_identity,
        expected_lock_identity=expected_lock_identity,
        expected_lock_file_sha256=expected_lock_file_sha256,
    )
    v4_binding = _require_v4_registration_cas_binding(
        event,
        expected_sequence=expected_sequence,
        expected_record_hash=expected_record_hash,
        allowed_nonterminal_records=allowed_nonterminal_records,
        minimum_sequence_exclusive=minimum_sequence_exclusive,
        expected_ledger_identity=expected_ledger_identity,
        expected_lock_identity=expected_lock_identity,
        expected_lock_file_sha256=expected_lock_file_sha256,
        expected_ledger_file_sha256=expected_ledger_file_sha256,
    )

    required_fields = {"experiment_id", "sequence", "record_hash", "event_type"}
    expected_open: Dict[str, Dict[str, Any]] = {}
    for item in allowed_nonterminal_records:
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ValueError("allowed nonterminal records are invalid")
        item_experiment_id = item.get("experiment_id")
        sequence = item.get("sequence")
        record_hash = item.get("record_hash")
        event_type = item.get("event_type")
        if (
            not isinstance(item_experiment_id, str)
            or not item_experiment_id
            or item_experiment_id in expected_open
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= 0
            or not isinstance(record_hash, str)
            or len(record_hash) != 64
            or any(character not in "0123456789abcdef" for character in record_hash)
            or event_type not in {"registered", "validation_started", "completed", "reviewed"}
        ):
            raise ValueError("allowed nonterminal records are invalid")
        expected_open[item_experiment_id] = dict(item)

    ledger_path = Path(path)
    with _ledger_lock(
        ledger_path,
        exclusive=True,
        expected_ledger_identity=expected_ledger_identity,
        expected_lock_identity=expected_lock_identity,
        expected_lock_file_sha256=expected_lock_file_sha256,
        require_existing_lock=v3_binding is not None or v4_binding is not None,
    ) as guard:
        rows = _read_ledger(
            ledger_path,
            guard,
            expected_file_sha256=expected_ledger_file_sha256,
        )
        actual_sequence = len(rows)
        actual_record_hash = rows[-1]["record_hash"] if rows else None
        if (
            actual_sequence != expected_sequence
            or actual_record_hash != expected_record_hash
        ):
            raise ValueError("ledger tip does not match the frozen control state")
        if any(str(row.get("experiment_id")) == experiment_id for row in rows):
            raise ValueError("experiment_id already exists in the ledger")

        latest_by_experiment: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            latest_by_experiment[str(row["experiment_id"])] = row
        actual_open = {
            item_experiment_id: {
                "experiment_id": item_experiment_id,
                "sequence": row["sequence"],
                "record_hash": row["record_hash"],
                "event_type": row["event_type"],
            }
            for item_experiment_id, row in latest_by_experiment.items()
            if row["event_type"] not in {"decision", "failed", "aborted"}
        }
        if actual_open != expected_open:
            raise ValueError("ledger nonterminal records do not match the frozen allowlist")
        if len(rows) + 1 <= minimum_sequence_exclusive:
            raise ValueError("controlled registration sequence is below the required floor")
        return _append_experiment_event_locked(ledger_path, rows, event, guard)


def _require_control_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} is not a lowercase SHA-256 digest")
    return value


def authorize_registered_precompute_launch_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
    parent_proof_file_sha256: str,
    parent_proof_canonical_sha256: str,
) -> Dict[str, Any]:
    """Atomically acquire the sole ledger-bound precompute launch ownership."""

    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    _require_control_sha256(registered_record_hash, "registered record hash")
    _require_control_sha256(run_claim_file_sha256, "run claim file hash")
    _require_control_sha256(launch_lease_file_sha256, "launch lease file hash")
    _require_control_sha256(parent_proof_file_sha256, "parent proof file hash")
    _require_control_sha256(
        parent_proof_canonical_sha256, "parent proof canonical hash"
    )

    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        matches = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        if len(matches) != 1:
            raise ValueError("registered record was not found")
        registered = matches[0]
        binding = _require_controlled_launch_registration(registered)
        control = binding["control"]
        if (
            parent_proof_file_sha256 != control["parent_proof_file_sha256"]
            or parent_proof_canonical_sha256
            != control["parent_proof_canonical_sha256"]
        ):
            raise ValueError("precompute parent proof binding mismatch")
        _assert_controlled_ledger_identity_locked(registered, guard)
        if (
            not rows
            or rows[-1].get("event_type") != "registered"
            or rows[-1].get("record_hash") != registered_record_hash
        ):
            raise ValueError("experiment is no longer launchable")
        latest_by_experiment: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            latest_by_experiment[str(row["experiment_id"])] = row
        nonterminal = {
            item_experiment_id: row
            for item_experiment_id, row in latest_by_experiment.items()
            if row.get("event_type") not in {"decision", "failed", "aborted"}
        }
        if binding["contract"]["schema_version"] == (
            "research-validation-registration/v4"
        ):
            try:
                validate_exact_open_set_v1(
                    sorted(nonterminal.values(), key=lambda row: row["sequence"]),
                    binding=binding["quarantine"],
                    target=registered,
                )
            except ValueError as exc:
                raise ValueError(
                    "quarantined nonterminal records do not match the frozen control state"
                ) from exc
        elif set(nonterminal) != {experiment_id}:
            raise ValueError("another nonterminal experiment blocks precompute launch")
        launch_schema = (
            "research-precompute-launch-started/v2"
            if binding["contract"]["schema_version"]
            == "research-validation-registration/v4"
            else "research-precompute-launch-started/v1"
        )
        launch_event = {
            "event_id": f"{experiment_id}:precompute_launch_started",
            "experiment_id": experiment_id,
            "event_type": "precompute_launch_started",
            "precompute_launch_started_schema_version": launch_schema,
            "registered_record_hash": registered_record_hash,
            "registered_sequence": registered["sequence"],
            "registration_contract_sha256": registered[
                "registration_contract_sha256"
            ],
            "run_claim_file_sha256": run_claim_file_sha256,
            "launch_lease_file_sha256": launch_lease_file_sha256,
            "parent_proof_file_sha256": parent_proof_file_sha256,
            "parent_proof_canonical_sha256": parent_proof_canonical_sha256,
            "control_source_bundle_sha256": control[
                "control_source_bundle_sha256"
            ],
            "ledger_path_sha256": binding["ledger"]["ledger_path_sha256"],
            "launch_attempt": 1,
            "single_launch": True,
        }
        if binding["contract"]["schema_version"] == (
            "research-validation-registration/v4"
        ):
            launch_event["legacy_quarantine_sha256"] = binding[
                "legacy_quarantine_sha256"
            ]
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            launch_event,
            guard,
        )


def complete_authorized_precompute_launch_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
    parent_proof_file_sha256: str,
    parent_proof_canonical_sha256: str,
    run_result_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Atomically complete only the exact ledger-authorized launch."""

    try:
        run_result_artifact = _freeze_json_mapping(
            run_result_artifact, label="precompute run result artifact"
        )
    except ValueError as exc:
        raise ValueError("precompute run result artifact is invalid") from exc
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    for value, label in (
        (registered_record_hash, "registered record hash"),
        (launch_started_record_hash, "launch started record hash"),
        (run_claim_file_sha256, "run claim file hash"),
        (launch_lease_file_sha256, "launch lease file hash"),
        (parent_proof_file_sha256, "parent proof file hash"),
        (parent_proof_canonical_sha256, "parent proof canonical hash"),
    ):
        _require_control_sha256(value, label)
    if set(run_result_artifact) != {
        "path",
        "basename",
        "bytes",
        "sha256",
    }:
        raise ValueError("precompute run result artifact is invalid")
    artifact_path = run_result_artifact.get("path")
    basename = run_result_artifact.get("basename")
    byte_count = run_result_artifact.get("bytes")
    if (
        not isinstance(artifact_path, str)
        or not artifact_path
        or not isinstance(basename, str)
        or not basename
        or Path(artifact_path).name != basename
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
    ):
        raise ValueError("precompute run result artifact is invalid")
    _require_control_sha256(run_result_artifact.get("sha256"), "precompute run result hash")

    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered_matches = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        launch_matches = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == launch_started_record_hash
            and row.get("event_type") == "precompute_launch_started"
        ]
        if len(registered_matches) != 1 or len(launch_matches) != 1:
            raise ValueError("authorized precompute launch was not found")
        registered = registered_matches[0]
        launch = launch_matches[0]
        binding = _require_controlled_launch_registration(registered)
        _validate_launch_started_event(
            {
                key: value
                for key, value in launch.items()
                if key
                not in {
                    "schema_version",
                    "sequence",
                    "recorded_at",
                    "previous_record_hash",
                    "record_hash",
                }
            },
            registered,
        )
        _assert_controlled_ledger_identity_locked(registered, guard)
        expected_binding = {
            "registered_record_hash": registered_record_hash,
            "run_claim_file_sha256": run_claim_file_sha256,
            "launch_lease_file_sha256": launch_lease_file_sha256,
            "parent_proof_file_sha256": parent_proof_file_sha256,
            "parent_proof_canonical_sha256": parent_proof_canonical_sha256,
        }
        if binding["contract"]["schema_version"] == "research-validation-registration/v4":
            expected_binding["legacy_quarantine_sha256"] = binding[
                "legacy_quarantine_sha256"
            ]
        if (
            rows[-1].get("record_hash") != launch_started_record_hash
            or any(launch.get(key) != value for key, value in expected_binding.items())
        ):
            raise ValueError("authorized precompute launch is no longer current")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:precompute_completed",
                "experiment_id": experiment_id,
                "event_type": "precompute_completed",
                **expected_binding,
                "launch_started_record_hash": launch_started_record_hash,
                "run_result_artifact": dict(run_result_artifact),
            },
            guard,
        )


def fail_authorized_precompute_launch_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
    parent_proof_file_sha256: str,
    parent_proof_canonical_sha256: str,
    failure_code: str,
    failure_phase: str,
    error_type: str,
) -> Dict[str, Any]:
    for value, label in (
        (registered_record_hash, "registered record hash"),
        (launch_started_record_hash, "launch started record hash"),
        (run_claim_file_sha256, "run claim file hash"),
        (launch_lease_file_sha256, "launch lease file hash"),
        (parent_proof_file_sha256, "parent proof file hash"),
        (parent_proof_canonical_sha256, "parent proof canonical hash"),
    ):
        _require_control_sha256(value, label)
    for value, label in (
        (experiment_id, "experiment_id"),
        (failure_code, "failure_code"),
        (failure_phase, "failure_phase"),
        (error_type, "error_type"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered_matches = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        launch_matches = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == launch_started_record_hash
            and row.get("event_type") == "precompute_launch_started"
        ]
        if len(registered_matches) != 1 or len(launch_matches) != 1:
            raise ValueError("authorized precompute launch was not found")
        registered = registered_matches[0]
        launch = launch_matches[0]
        binding = _require_controlled_launch_registration(registered)
        _assert_controlled_ledger_identity_locked(registered, guard)
        expected = {
            "registered_record_hash": registered_record_hash,
            "run_claim_file_sha256": run_claim_file_sha256,
            "launch_lease_file_sha256": launch_lease_file_sha256,
            "parent_proof_file_sha256": parent_proof_file_sha256,
            "parent_proof_canonical_sha256": parent_proof_canonical_sha256,
        }
        if binding["contract"]["schema_version"] == "research-validation-registration/v4":
            expected["legacy_quarantine_sha256"] = binding[
                "legacy_quarantine_sha256"
            ]
        if (
            rows[-1].get("record_hash") != launch_started_record_hash
            or any(launch.get(key) != value for key, value in expected.items())
        ):
            raise ValueError("authorized precompute launch is no longer current")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:precompute_failed",
                "experiment_id": experiment_id,
                "event_type": "failed",
                **expected,
                "launch_started_record_hash": launch_started_record_hash,
                "error_code": failure_code,
                "error_type": error_type,
                "failure_phase": failure_phase,
            },
            guard,
        )


def fail_registered_experiment_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    failure_code: str,
    failure_phase: str,
    error_type: str,
    run_claim_file_sha256: str | None,
) -> Dict[str, Any]:
    """Atomically seal one exact registered precompute experiment as failed."""

    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    if (
        not isinstance(registered_record_hash, str)
        or len(registered_record_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in registered_record_hash
        )
    ):
        raise ValueError("registered record hash is not a lowercase SHA-256 digest")
    for label, value in (
        ("failure code", failure_code),
        ("failure phase", failure_phase),
        ("error type", error_type),
    ):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or any(
                not (character.isascii() and (character.isalnum() or character in "_-"))
                for character in value
            )
        ):
            raise ValueError(f"{label} is invalid")
    if run_claim_file_sha256 is not None and (
        not isinstance(run_claim_file_sha256, str)
        or len(run_claim_file_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in run_claim_file_sha256
        )
    ):
        raise ValueError("run claim file SHA is invalid")

    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        if len(registered) != 1:
            raise ValueError("registered record was not found")
        _assert_controlled_ledger_identity_locked(registered[0], guard)
        experiment_rows = [
            row for row in rows if row.get("experiment_id") == experiment_id
        ]
        if (
            experiment_rows[-1].get("event_type") != "registered"
            or experiment_rows[-1].get("record_hash") != registered_record_hash
        ):
            raise ValueError("experiment is no longer registered")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:precompute_failed",
                "experiment_id": experiment_id,
                "event_type": "failed",
                "error_code": failure_code,
                "error_type": error_type,
                "failure_phase": failure_phase,
                "registered_record_hash": registered_record_hash,
                "run_claim_file_sha256": run_claim_file_sha256,
            },
            guard,
        )


def complete_registered_precompute_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    run_claim_file_sha256: str,
    launch_lease_file_sha256: str,
    run_result_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Atomically bind one successful supervised precompute result to registration."""

    def require_sha(value: Any, label: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{label} is not a lowercase SHA-256 digest")
        return value

    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("experiment_id is required")
    require_sha(registered_record_hash, "registered record hash")
    require_sha(run_claim_file_sha256, "run claim file hash")
    require_sha(launch_lease_file_sha256, "launch lease file hash")
    if not isinstance(run_result_artifact, dict) or set(run_result_artifact) != {
        "path",
        "basename",
        "bytes",
        "sha256",
    }:
        raise ValueError("precompute run result artifact is invalid")
    artifact_path = run_result_artifact.get("path")
    basename = run_result_artifact.get("basename")
    byte_count = run_result_artifact.get("bytes")
    if (
        not isinstance(artifact_path, str)
        or not artifact_path
        or not isinstance(basename, str)
        or not basename
        or Path(artifact_path).name != basename
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count <= 0
    ):
        raise ValueError("precompute run result artifact is invalid")
    require_sha(run_result_artifact.get("sha256"), "precompute run result hash")

    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        if len(registered) != 1:
            raise ValueError("registered record was not found")
        _assert_controlled_ledger_identity_locked(registered[0], guard)
        experiment_rows = [
            row for row in rows if row.get("experiment_id") == experiment_id
        ]
        if (
            experiment_rows[-1].get("event_type") != "registered"
            or experiment_rows[-1].get("record_hash") != registered_record_hash
        ):
            raise ValueError("experiment is no longer registered")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:precompute_completed",
                "experiment_id": experiment_id,
                "event_type": "precompute_completed",
                "registered_record_hash": registered_record_hash,
                "run_claim_file_sha256": run_claim_file_sha256,
                "launch_lease_file_sha256": launch_lease_file_sha256,
                "run_result_artifact": dict(run_result_artifact),
            },
            guard,
        )


def fail_precomputed_experiment_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str | None = None,
    precompute_completed_record_hash: str,
    failure_code: str,
    failure_phase: str,
    error_type: str,
) -> Dict[str, Any]:
    """Atomically terminalize only the exact unclaimed precompute result."""

    for value, label in (
        (registered_record_hash, "registered record hash"),
        (precompute_completed_record_hash, "precompute completed record hash"),
    ):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{label} is not a lowercase SHA-256 digest")
    for value, label in (
        (experiment_id, "experiment_id"),
        (failure_code, "failure_code"),
        (failure_phase, "failure_phase"),
        (error_type, "error_type"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        completed = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == precompute_completed_record_hash
            and row.get("event_type") == "precompute_completed"
            and row.get("registered_record_hash") == registered_record_hash
        ]
        if len(registered) != 1 or len(completed) != 1:
            raise ValueError("precomputed experiment records were not found")
        registered_event = registered[0]
        completed_event = completed[0]
        contract = registered_event.get("registration_contract")
        controlled_launch = (
            isinstance(contract, dict)
            and contract.get("schema_version")
            in {
                "research-validation-registration/v3",
                "research-validation-registration/v4",
            }
        )
        binding = None
        if controlled_launch:
            binding = _require_controlled_launch_registration(registered_event)
            _require_control_sha256(
                launch_started_record_hash, "launch started record hash"
            )
            launch = [
                row
                for row in rows
                if row.get("experiment_id") == experiment_id
                and row.get("record_hash") == launch_started_record_hash
                and row.get("event_type") == "precompute_launch_started"
            ]
            if len(launch) != 1:
                raise ValueError("precompute launch started record was not found")
            launch_event = launch[0]
            _validate_launch_started_event(
                {
                    key: value
                    for key, value in launch_event.items()
                    if key
                    not in {
                        "schema_version",
                        "sequence",
                        "recorded_at",
                        "previous_record_hash",
                        "record_hash",
                    }
                },
                registered_event,
            )
            if (
                completed_event.get("launch_started_record_hash")
                != launch_started_record_hash
                or (
                    binding is not None
                    and binding["contract"]["schema_version"]
                    == "research-validation-registration/v4"
                    and completed_event.get("legacy_quarantine_sha256")
                    != binding["legacy_quarantine_sha256"]
                )
                or launch_event.get("sequence")
                != registered_event.get("sequence") + 1
                or completed_event.get("sequence") != launch_event.get("sequence") + 1
            ):
                raise ValueError("precomputed experiment launch binding mismatch")
        elif launch_started_record_hash is not None:
            raise ValueError("legacy precomputed state forbids launch binding")
        _assert_controlled_ledger_identity_locked(registered_event, guard)
        experiment_rows = [
            row for row in rows if row.get("experiment_id") == experiment_id
        ]
        if (
            experiment_rows[-1].get("event_type") != "precompute_completed"
            or experiment_rows[-1].get("record_hash")
            != precompute_completed_record_hash
        ):
            raise ValueError("experiment is no longer precompute_completed")
        terminal_event = {
            "event_id": f"{experiment_id}:precompute_validation_failed",
            "experiment_id": experiment_id,
            "event_type": "failed",
            "error_code": failure_code,
            "error_type": error_type,
            "failure_phase": failure_phase,
            "registered_record_hash": registered_record_hash,
            "precompute_completed_record_hash": precompute_completed_record_hash,
            **(
                {"launch_started_record_hash": launch_started_record_hash}
                if launch_started_record_hash is not None
                else {}
            ),
        }
        if (
            binding is not None
            and binding["contract"]["schema_version"]
            == "research-validation-registration/v4"
        ):
            terminal_event["legacy_quarantine_sha256"] = binding[
                "legacy_quarantine_sha256"
            ]
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            terminal_event,
            guard,
        )


def fail_validation_started_experiment_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    validation_started_record_hash: str,
    failure_code: str,
    failure_phase: str,
    error_type: str,
) -> Dict[str, Any]:
    """Atomically seal one exact controlled validation that has started."""

    for value, label in (
        (registered_record_hash, "registered record hash"),
        (validation_started_record_hash, "validation started record hash"),
    ):
        _require_control_sha256(value, label)
    for value, label in (
        (experiment_id, "experiment id"),
        (failure_code, "failure code"),
        (failure_phase, "failure phase"),
        (error_type, "error type"),
    ):
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"{label} is invalid")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        validation_started = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == validation_started_record_hash
            and row.get("event_type") == "validation_started"
            and row.get("registered_record_hash") == registered_record_hash
        ]
        if len(registered) != 1 or len(validation_started) != 1:
            raise ValueError("controlled validation records were not found")
        binding = _require_controlled_launch_registration(registered[0])
        if (
            binding["contract"]["schema_version"]
            == "research-validation-registration/v4"
            and validation_started[0].get("legacy_quarantine_sha256")
            != binding["legacy_quarantine_sha256"]
        ):
            raise ValueError("controlled validation quarantine binding is invalid")
        _assert_controlled_ledger_identity_locked(registered[0], guard)
        if (
            rows[-1].get("event_type") != "validation_started"
            or rows[-1].get("record_hash") != validation_started_record_hash
        ):
            raise ValueError("controlled validation is no longer current")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:validation_failed",
                "experiment_id": experiment_id,
                "event_type": "failed",
                "error_code": failure_code,
                "error_type": error_type,
                "failure_phase": failure_phase,
                "registered_record_hash": registered_record_hash,
                "validation_started_record_hash": validation_started_record_hash,
                **(
                    {"legacy_quarantine_sha256": binding["legacy_quarantine_sha256"]}
                    if binding["contract"]["schema_version"]
                    == "research-validation-registration/v4"
                    else {}
                ),
            },
            guard,
        )


def complete_validation_started_experiment_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    validation_started_record_hash: str,
    completion_event: Dict[str, Any],
) -> Dict[str, Any]:
    """Atomically append one bound controlled validation completion."""

    try:
        completion_event = _freeze_json_mapping(
            completion_event, label="controlled validation completion event"
        )
    except ValueError as exc:
        raise ValueError("controlled validation completion event is invalid") from exc
    for value, label in (
        (registered_record_hash, "registered record hash"),
        (validation_started_record_hash, "validation started record hash"),
    ):
        _require_control_sha256(value, label)
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("experiment id is invalid")
    if any(
        field in completion_event
        for field in (
            "schema_version",
            "sequence",
            "recorded_at",
            "previous_record_hash",
            "record_hash",
        )
    ):
        raise ValueError("controlled validation completion event is invalid")
    expected = {
        "event_id": f"{experiment_id}:completed",
        "experiment_id": experiment_id,
        "event_type": "completed",
        "registered_record_hash": registered_record_hash,
        "validation_started_record_hash": validation_started_record_hash,
    }
    if any(completion_event.get(field) != value for field, value in expected.items()):
        raise ValueError("controlled validation completion event is invalid")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        validation_started = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == validation_started_record_hash
            and row.get("event_type") == "validation_started"
            and row.get("registered_record_hash") == registered_record_hash
        ]
        if len(registered) != 1 or len(validation_started) != 1:
            raise ValueError("controlled validation records were not found")
        binding = _require_controlled_launch_registration(registered[0])
        if (
            binding["contract"]["schema_version"]
            == "research-validation-registration/v4"
        ):
            expected["legacy_quarantine_sha256"] = binding[
                "legacy_quarantine_sha256"
            ]
            if (
                validation_started[0].get("legacy_quarantine_sha256")
                != binding["legacy_quarantine_sha256"]
                or completion_event.get("legacy_quarantine_sha256")
                != binding["legacy_quarantine_sha256"]
            ):
                raise ValueError("controlled validation quarantine binding is invalid")
        _assert_controlled_ledger_identity_locked(registered[0], guard)
        if (
            rows[-1].get("event_type") != "validation_started"
            or rows[-1].get("record_hash") != validation_started_record_hash
        ):
            raise ValueError("controlled validation is no longer current")
        return _append_experiment_event_locked(
            ledger_path, rows, dict(completion_event), guard
        )


def decide_completed_validation_if_current(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    validation_started_record_hash: str,
    completed_record_hash: str,
    decision_code: str,
    validation_result_sha256: str,
    gate_result_sha256: str,
    all_gates_pass: bool,
) -> Dict[str, Any]:
    """Atomically bind the one pre-registered purged decision to completion."""

    for value, label in (
        (registered_record_hash, "registered record hash"),
        (validation_started_record_hash, "validation started record hash"),
        (completed_record_hash, "completed record hash"),
        (validation_result_sha256, "validation result hash"),
        (gate_result_sha256, "gate result hash"),
    ):
        _require_control_sha256(value, label)
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("experiment id is invalid")
    if decision_code not in {
        "PURGED_ACCEPTANCE_GATE_GREEN",
        "PURGED_ACCEPTANCE_GATE_RED",
        "PURGED_RESULT_INVALID",
    }:
        raise ValueError("controlled validation decision code is invalid")
    if not isinstance(all_gates_pass, bool):
        raise ValueError("controlled validation decision gate is invalid")
    if (
        decision_code == "PURGED_ACCEPTANCE_GATE_GREEN"
        and all_gates_pass is not True
    ) or (
        decision_code != "PURGED_ACCEPTANCE_GATE_GREEN"
        and all_gates_pass is not False
    ):
        raise ValueError("controlled validation decision gate is inconsistent")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        registered = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == registered_record_hash
            and row.get("event_type") == "registered"
        ]
        validation_started = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == validation_started_record_hash
            and row.get("event_type") == "validation_started"
            and row.get("registered_record_hash") == registered_record_hash
        ]
        completed = [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == completed_record_hash
            and row.get("event_type") == "completed"
            and row.get("registered_record_hash") == registered_record_hash
            and row.get("validation_started_record_hash")
            == validation_started_record_hash
        ]
        if len(registered) != 1 or len(validation_started) != 1 or len(completed) != 1:
            raise ValueError("controlled validation completion records were not found")
        binding = _require_controlled_launch_registration(registered[0])
        if (
            binding["contract"]["schema_version"]
            == "research-validation-registration/v4"
            and (
                validation_started[0].get("legacy_quarantine_sha256")
                != binding["legacy_quarantine_sha256"]
                or completed[0].get("legacy_quarantine_sha256")
                != binding["legacy_quarantine_sha256"]
            )
        ):
            raise ValueError("controlled validation quarantine binding is invalid")
        _assert_controlled_ledger_identity_locked(registered[0], guard)
        if (
            rows[-1].get("event_type") != "completed"
            or rows[-1].get("record_hash") != completed_record_hash
        ):
            raise ValueError("controlled validation completion is no longer current")
        return _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:decision",
                "experiment_id": experiment_id,
                "event_type": "decision",
                "decision_schema_version": "research-purged-validation-decision/v1",
                "decision_code": decision_code,
                "all_gates_pass": all_gates_pass,
                "registered_record_hash": registered_record_hash,
                "validation_started_record_hash": validation_started_record_hash,
                "completed_record_hash": completed_record_hash,
                "validation_result_sha256": validation_result_sha256,
                "gate_result_sha256": gate_result_sha256,
                **(
                    {"legacy_quarantine_sha256": binding["legacy_quarantine_sha256"]}
                    if binding["contract"]["schema_version"]
                    == "research-validation-registration/v4"
                    else {}
                ),
            },
            guard,
        )


def _validated_precomputed_state(
    rows: List[Dict[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str | None,
    precompute_completed_record_hash: str,
    expected_registration_contract: Dict[str, Any],
    expected_input_plan_artifact: Dict[str, Any],
    expected_run_result_artifact: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    registered_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    completed_matches = [
        row
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == precompute_completed_record_hash
        and row.get("event_type") == "precompute_completed"
    ]
    if len(registered_matches) != 1 or len(completed_matches) != 1:
        raise ValueError("precomputed experiment records were not found")
    registered = registered_matches[0]
    completed = completed_matches[0]
    contract = registered.get("registration_contract")
    controlled_launch = (
        isinstance(contract, dict)
        and contract.get("schema_version")
        in {
            "research-validation-registration/v3",
            "research-validation-registration/v4",
        }
    )
    launch_matches = (
        [
            row
            for row in rows
            if row.get("experiment_id") == experiment_id
            and row.get("record_hash") == launch_started_record_hash
            and row.get("event_type") == "precompute_launch_started"
        ]
        if controlled_launch
        else []
    )
    if controlled_launch:
        binding = _require_controlled_launch_registration(registered)
        _require_control_sha256(
            launch_started_record_hash, "launch started record hash"
        )
        if len(launch_matches) != 1:
            raise ValueError("precompute launch started record was not found")
    elif launch_started_record_hash is not None:
        raise ValueError("legacy precomputed state forbids launch binding")
    launch_started = launch_matches[0] if launch_matches else None
    experiment_rows = [
        row for row in rows if row.get("experiment_id") == experiment_id
    ]
    if experiment_rows[-1].get("record_hash") != precompute_completed_record_hash:
        raise ValueError("precomputed experiment was already claimed or completed")
    if (
        registered.get("registration_contract") != expected_registration_contract
        or registered.get("registration_contract_sha256")
        != _sha256(expected_registration_contract)
        or registered.get("input_plan_artifact") != expected_input_plan_artifact
        or completed.get("registered_record_hash") != registered_record_hash
        or completed.get("run_result_artifact") != expected_run_result_artifact
        or (
            controlled_launch
            and (
                completed.get("launch_started_record_hash")
                != launch_started_record_hash
                or completed.get("run_claim_file_sha256")
                != launch_started.get("run_claim_file_sha256")
                or completed.get("launch_lease_file_sha256")
                != launch_started.get("launch_lease_file_sha256")
                or completed.get("parent_proof_file_sha256")
                != contract["precompute_control"]["parent_proof_file_sha256"]
                or completed.get("parent_proof_canonical_sha256")
                != contract["precompute_control"][
                    "parent_proof_canonical_sha256"
                ]
                or (
                    binding["contract"]["schema_version"]
                    == "research-validation-registration/v4"
                    and completed.get("legacy_quarantine_sha256")
                    != binding["legacy_quarantine_sha256"]
                )
                or launch_started.get("sequence") != registered.get("sequence") + 1
                or completed.get("sequence") != launch_started.get("sequence") + 1
            )
        )
    ):
        raise ValueError("precomputed experiment binding mismatch")
    if launch_started is not None:
        _validate_launch_started_event(
            {
                key: value
                for key, value in launch_started.items()
                if key
                not in {
                    "schema_version",
                    "sequence",
                    "recorded_at",
                    "previous_record_hash",
                    "record_hash",
                }
            },
            registered,
        )
    state = {"registered": registered, "precompute_completed": completed}
    if launch_started is not None:
        state["precompute_launch_started"] = launch_started
    return state


def preflight_precomputed_experiment(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str | None = None,
    precompute_completed_record_hash: str,
    expected_registration_contract: Dict[str, Any],
    expected_input_plan_artifact: Dict[str, Any],
    expected_run_result_artifact: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        state = _validated_precomputed_state(
            rows,
            experiment_id=experiment_id,
            registered_record_hash=registered_record_hash,
            launch_started_record_hash=launch_started_record_hash,
            precompute_completed_record_hash=precompute_completed_record_hash,
            expected_registration_contract=expected_registration_contract,
            expected_input_plan_artifact=expected_input_plan_artifact,
            expected_run_result_artifact=expected_run_result_artifact,
        )
        _assert_controlled_ledger_identity_locked(state["registered"], guard)
        return state


def claim_precomputed_experiment(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    launch_started_record_hash: str | None = None,
    precompute_completed_record_hash: str,
    expected_registration_contract: Dict[str, Any],
    expected_input_plan_artifact: Dict[str, Any],
    expected_run_result_artifact: Dict[str, Any],
    claimed_input_artifacts: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(claimed_input_artifacts, dict) or not claimed_input_artifacts:
        raise ValueError("claimed input artifacts are invalid")
    if claimed_input_artifacts.get("precompute_run_result") != (
        expected_run_result_artifact
    ):
        raise ValueError("claimed precompute run result is invalid")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        state = _validated_precomputed_state(
            rows,
            experiment_id=experiment_id,
            registered_record_hash=registered_record_hash,
            launch_started_record_hash=launch_started_record_hash,
            precompute_completed_record_hash=precompute_completed_record_hash,
            expected_registration_contract=expected_registration_contract,
            expected_input_plan_artifact=expected_input_plan_artifact,
            expected_run_result_artifact=expected_run_result_artifact,
        )
        _assert_controlled_ledger_identity_locked(state["registered"], guard)
        validation_started = _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:validation_started",
                "experiment_id": experiment_id,
                "event_type": "validation_started",
                "registered_record_hash": registered_record_hash,
                "precompute_completed_record_hash": (
                    precompute_completed_record_hash
                ),
                **(
                    {"launch_started_record_hash": launch_started_record_hash}
                    if launch_started_record_hash is not None
                    else {}
                ),
                "registration_contract_sha256": _sha256(
                    expected_registration_contract
                ),
                "input_plan_artifact": dict(expected_input_plan_artifact),
                "run_result_artifact": dict(expected_run_result_artifact),
                "claimed_input_artifacts": dict(claimed_input_artifacts),
                "claimed_input_artifacts_sha256": _sha256(
                    claimed_input_artifacts
                ),
                **(
                    {"legacy_quarantine_sha256": _require_controlled_launch_registration(
                        state["registered"]
                    )["legacy_quarantine_sha256"]}
                    if isinstance(state["registered"].get("registration_contract"), dict)
                    and state["registered"]["registration_contract"].get(
                        "schema_version"
                    ) == "research-validation-registration/v4"
                    else {}
                ),
            },
            guard,
        )
        return {**state, "validation_started": validation_started}


def preflight_registered_experiment(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_registration_contract: Dict[str, Any],
    expected_input_plan_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify immutable registration inputs without consuming the claim.

    A mismatch is terminal because it proves the caller changed frozen intent.
    A match performs no ledger mutation; deterministic artifact integrity work
    may then run before the later atomic claim.
    """

    if not isinstance(registered_record_hash, str) or len(registered_record_hash) != 64:
        raise ValueError("registered record hash is not a SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in registered_record_hash):
        raise ValueError("registered record hash is not a lowercase SHA-256 digest")
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        matches = [
            row
            for row in rows
            if row.get("record_hash") == registered_record_hash
            and str(row.get("experiment_id")) == str(experiment_id)
        ]
        if len(matches) != 1 or matches[0].get("event_type") != "registered":
            raise ValueError("registered experiment record hash was not found")
        registered = matches[0]
        experiment_rows = [
            row for row in rows if str(row.get("experiment_id")) == str(experiment_id)
        ]
        if experiment_rows[-1].get("record_hash") != registered_record_hash:
            raise ValueError("registered experiment was already claimed or completed")
        expected_contract_sha256 = _sha256(expected_registration_contract)
        mismatch = (
            registered.get("registration_contract") != expected_registration_contract
            or registered.get("registration_contract_sha256")
            != expected_contract_sha256
            or registered.get("input_plan_artifact")
            != expected_input_plan_artifact
        )
        if mismatch:
            _append_experiment_event_locked(
                ledger_path,
                rows,
                {
                    "event_id": f"{experiment_id}:failed",
                    "experiment_id": experiment_id,
                    "event_type": "failed",
                    "error_code": "VALIDATION_INPUT_REJECTED",
                    "error_type": "ValueError",
                    "message": "registered validation inputs do not match",
                },
                guard,
            )
            raise ValueError("registered validation parameter fingerprint mismatch")
        return registered


def claim_registered_experiment(
    path: str,
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_registration_contract: Dict[str, Any],
    expected_input_plan_artifact: Dict[str, Any],
    claimed_input_artifacts: Dict[str, Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Atomically consume one exact pre-registration before strategy evaluation.

    The record hash identifies the immutable intent.  Rebuilding and comparing
    the complete registration contract prevents a caller from changing a
    strategy or validation parameter between registration and execution.  The
    caller may first perform deterministic integrity preflight, but a successful
    claim still appends ``validation_started`` under the same ledger lock so
    concurrent consumers cannot both evaluate the same registration.
    """

    if not isinstance(registered_record_hash, str) or len(registered_record_hash) != 64:
        raise ValueError("registered record hash is not a SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in registered_record_hash):
        raise ValueError("registered record hash is not a lowercase SHA-256 digest")
    if claimed_input_artifacts is not None:
        if not isinstance(claimed_input_artifacts, dict) or not claimed_input_artifacts:
            raise ValueError("claimed input artifacts are invalid")
        claimed_input_artifacts_sha256 = _sha256(claimed_input_artifacts)
    else:
        claimed_input_artifacts_sha256 = None
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True) as guard:
        rows = _read_ledger(ledger_path, guard)
        matches = [
            row
            for row in rows
            if row.get("record_hash") == registered_record_hash
            and str(row.get("experiment_id")) == str(experiment_id)
        ]
        if len(matches) != 1 or matches[0].get("event_type") != "registered":
            raise ValueError("registered experiment record hash was not found")
        registered = matches[0]
        experiment_rows = [
            row for row in rows if str(row.get("experiment_id")) == str(experiment_id)
        ]
        if experiment_rows[-1].get("record_hash") != registered_record_hash:
            raise ValueError("registered experiment was already claimed or completed")
        expected_contract_sha256 = _sha256(expected_registration_contract)
        mismatch = (
            registered.get("registration_contract") != expected_registration_contract
            or registered.get("registration_contract_sha256")
            != expected_contract_sha256
            or registered.get("input_plan_artifact")
            != expected_input_plan_artifact
        )
        if mismatch:
            _append_experiment_event_locked(
                ledger_path,
                rows,
                {
                    "event_id": f"{experiment_id}:failed",
                    "experiment_id": experiment_id,
                    "event_type": "failed",
                    "error_code": "VALIDATION_INPUT_REJECTED",
                    "error_type": "ValueError",
                    "message": "registered validation inputs do not match",
                },
                guard,
            )
            raise ValueError("registered validation parameter fingerprint mismatch")
        started = _append_experiment_event_locked(
            ledger_path,
            rows,
            {
                "event_id": f"{experiment_id}:validation_started",
                "experiment_id": experiment_id,
                "event_type": "validation_started",
                "registered_record_hash": registered_record_hash,
                "registration_contract_sha256": expected_contract_sha256,
                "input_plan_sha256": expected_input_plan_artifact["sha256"],
                **(
                    {
                        "claimed_input_artifacts": claimed_input_artifacts,
                        "claimed_input_artifacts_sha256": (
                            claimed_input_artifacts_sha256
                        ),
                    }
                    if claimed_input_artifacts is not None
                    else {}
                ),
            },
            guard,
        )
        return {"registered": registered, "validation_started": started}
