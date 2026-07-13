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
import fcntl
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.research_sweep import sweep_qualified_trades
from app.research_pit import verify_research_evidence_bundle


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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

    manifest = getattr(audited_universe, "manifest", None)
    if not isinstance(manifest, dict):
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
    if set(authority) != _AUDITED_AUTHORITY_KEYS:
        raise ValueError("audited authority fields are incomplete")
    if authority["temporal_role"] != "development":
        raise ValueError("audited authority must use the development temporal role")
    if any(
        not (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value.lower())
        )
        for key, value in authority.items()
        if key != "temporal_role"
    ):
        raise ValueError("audited authority contains an invalid hash")
    return authority


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
    lineage = []
    seen_keys = set()
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("trade lineage row is invalid")
        symbol = str(trade.get("symbol") or "").strip()
        signal_date = str(trade.get("signal_date") or "")[:10]
        entry_date = str(trade.get("entry_date") or "")[:10]
        exit_date = str(trade.get("exit_date") or "")[:10]
        planned_exit_date = str(trade.get("planned_exit_date") or "")[:10]
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
        planned_position = 1 + holding_days
        if holding_days <= 0 or planned_position >= len(sessions) or sessions[planned_position] != planned_exit_date:
            raise ValueError(f"trade lineage planned exit does not match holding period: {trade_key}")
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
        if verified_evidence.get("schema_version") not in {
            "research_pit_evidence_bundle/v2",
            "research_pit_evidence_bundle/v3",
        }:
            raise ValueError("strict production requires an audited-bound evidence bundle")
        eligibility = verified_evidence.get("eligibility")
        unresolved_reasons = (
            eligibility.get("reasons")
            if isinstance(eligibility, dict)
            else None
        )
        if not isinstance(unresolved_reasons, list) or unresolved_reasons:
            raise ValueError("evidence bundle has unresolved eligibility reasons")
        expected_authority = {
            "artifact_root_sha256": audited_universe.artifact_root_sha256,
            "coverage_audit_sha256": audited_universe.coverage_audit_sha256,
            "temporal_contract_sha256": audited_universe.temporal_contract_sha256,
            "temporal_role": audited_universe.temporal_role,
            "artifact_manifest_sha256": audited_universe.manifest["manifest_sha256"],
            "market_generation_root_sha256": audited_universe.manifest[
                "market_generations"
            ]["root_sha256"],
            "stock_generation_lineage_sha256": audited_universe.manifest[
                "stock_generation"
            ]["lineage_sha256"],
        }
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
            audited_coverage = audited_universe.manifest["coverage"]
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
    universe = verified_evidence["universe"]
    trade_dates_valid = True
    for trade in trades:
        signal_date = str(trade.get("signal_date") or "")[:10]
        entry_date = str(trade.get("entry_date") or "")[:10]
        exit_date = str(trade.get("exit_date") or "")[:10]
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
        contract.get("schema_version") == "research_data_contract/v1"
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
        and trade_dates_valid
    )
    if not valid:
        raise ValueError("qualified trades do not declare the required point-in-time contract")
    authority = None
    if audited_universe is not None:
        authority = {
            "coverage": dict(audited_universe.manifest["coverage"]),
            "artifact_root_sha256": audited_universe.artifact_root_sha256,
            "coverage_audit_sha256": audited_universe.coverage_audit_sha256,
            "temporal_contract_sha256": audited_universe.temporal_contract_sha256,
            "temporal_role": audited_universe.temporal_role,
            "artifact_manifest_sha256": audited_universe.manifest["manifest_sha256"],
            "market_generation_root_sha256": audited_universe.manifest[
                "market_generations"
            ]["root_sha256"],
            "stock_generation_lineage_sha256": audited_universe.manifest[
                "stock_generation"
            ]["lineage_sha256"],
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
    content = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
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
    target_return = float(strategy.get("target_one_year_return_pct", 50.0))
    target_drawdown = float(strategy.get("target_drawdown_pct", 15.0))
    target_profit_factor = float(strategy.get("target_profit_factor", 1.3))
    target_calmar = float(strategy.get("target_calmar", 1.5))
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
        "target_profile": "primary_50_return_15_drawdown",
        "all_rolling_12m_stability_pass": bool(
            aggregate.get("target_rolling_12m_stability_pass")
        ),
    }
    qualification["profile_primary_gates_pass"] = all(
        qualification[key]
        for key in (
            "minimum_sample_pass",
            "observed_win_rate_pass",
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


def _read_ledger(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    previous_hash = None
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
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
def _ledger_lock(ledger_path: Path, exclusive: bool):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def read_experiment_ledger(path: str) -> List[Dict[str, Any]]:
    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=False):
        return _read_ledger(ledger_path)


def _validate_event_transition(experiment_rows: List[Dict[str, Any]], event_type: str) -> None:
    if not experiment_rows:
        if event_type != "registered":
            raise ValueError("experiment must be registered first")
        return
    last_type = str(experiment_rows[-1].get("event_type") or "")
    allowed = {
        "registered": {"completed", "failed", "aborted"},
        "completed": {"reviewed", "decision"},
        "reviewed": {"decision"},
        "decision": set(),
        "failed": set(),
        "aborted": set(),
    }
    if event_type not in allowed.get(last_type, set()):
        raise ValueError(f"invalid experiment transition: {last_type} -> {event_type}")


def append_experiment_event(path: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Append one lifecycle event to a hash-chained experiment ledger."""

    experiment_id = str(event.get("experiment_id") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "").strip()
    if not experiment_id:
        raise ValueError("experiment_id is required")
    if not event_id:
        raise ValueError("event_id is required")
    if event_type not in {
        "registered",
        "completed",
        "failed",
        "aborted",
        "reviewed",
        "decision",
    }:
        raise ValueError("unsupported event_type")

    ledger_path = Path(path)
    with _ledger_lock(ledger_path, exclusive=True):
        rows = _read_ledger(ledger_path)
        if any(str(item.get("event_id")) == event_id for item in rows):
            raise ValueError(f"event_id already exists: {event_id}")
        experiment_rows = [item for item in rows if str(item.get("experiment_id")) == experiment_id]
        _validate_event_transition(experiment_rows, event_type)

        previous_hash = rows[-1].get("record_hash") if rows else None
        payload = dict(event)
        payload["schema_version"] = "research-experiment-event/v1"
        payload["sequence"] = len(rows) + 1
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        payload["previous_record_hash"] = previous_hash
        payload["record_hash"] = _sha256(payload)
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload
