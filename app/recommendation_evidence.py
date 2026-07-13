"""Content-addressed evidence receipts for the automatic advice profile."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.recommendation_profile import DEFAULT_PROFILE, RecommendationProfile, profile_to_dict
from app.storage import write_json


RECEIPT_SCHEMA = "profile-evidence-receipt/v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _rolling_windows(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        windows = value.get("windows")
        if isinstance(windows, list):
            return [item for item in windows if isinstance(item, dict)]
    return []


def _all_rolling_pass(windows: Iterable[Dict[str, Any]], profile: RecommendationProfile) -> bool:
    rows = list(windows)
    if not rows:
        return False
    for window in rows:
        try:
            if float(window["return_pct"]) < profile.target_annualized_return_pct:
                return False
            if abs(float(window["max_drawdown_pct"])) > profile.max_drawdown_pct:
                return False
            if float(window["payoff_ratio"]) < profile.min_payoff_ratio:
                return False
            if float(window["profit_factor"]) < profile.min_profit_factor:
                return False
            if float(window["calmar"]) < profile.min_calmar:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


def build_profile_evidence_receipt(
    *,
    profile: RecommendationProfile = DEFAULT_PROFILE,
    experiment_id: str,
    strategy: Dict[str, Any],
    validation: Dict[str, Any],
    validation_report: Dict[str, Any],
    rolling_12m: Any,
    evidence: Dict[str, Any],
    source_artifact: Dict[str, Any],
    report_artifact: Dict[str, Any] | None,
    ledger_anchor: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a receipt without upgrading incomplete research into proof."""
    if not isinstance(validation_report, dict):
        raise ValueError("validation_report must be an object")
    aggregate = validation_report.get("aggregate_validation") or {}
    if not isinstance(aggregate, dict):
        raise ValueError("aggregate_validation must be an object")
    profile_payload = profile_to_dict(profile)
    windows = _rolling_windows(rolling_12m)
    full_window = bool(aggregate.get("rolling_1y_latest_full_window"))
    signed_drawdown = aggregate.get("portfolio_max_drawdown_pct")
    metrics = {
        "annualized_return_pct": aggregate.get("rolling_1y_latest_return_pct") if full_window else None,
        "max_drawdown_pct": abs(float(signed_drawdown)) if signed_drawdown is not None else None,
        "max_drawdown_signed_pct": signed_drawdown,
        "selected_trade_count": aggregate.get("selected_trade_count"),
        "signal_days": aggregate.get("signal_days"),
        "win_count": aggregate.get("trade_win_count"),
        "nonwin_count": aggregate.get("trade_nonwin_count"),
        "win_rate_pct": aggregate.get("trade_win_rate_pct"),
        "wilson_95_lower_pct": aggregate.get("win_rate_wilson_95_lower_pct"),
        "wilson_95_upper_pct": aggregate.get("win_rate_wilson_95_upper_pct"),
        "win_rate_wilson_lower_pct": aggregate.get("win_rate_wilson_95_lower_pct"),
        "win_rate_wilson_upper_pct": aggregate.get("win_rate_wilson_95_upper_pct"),
        "payoff_ratio": aggregate.get("trade_payoff_ratio"),
        "profit_factor": aggregate.get("trade_profit_factor"),
        "calmar": aggregate.get("calmar_latest_12m"),
        "rolling_12m": windows,
    }
    gates = {
        "annualized_return": metrics["annualized_return_pct"] is not None
        and float(metrics["annualized_return_pct"]) >= profile.target_annualized_return_pct,
        "max_drawdown": metrics["max_drawdown_pct"] is not None
        and float(metrics["max_drawdown_pct"]) <= profile.max_drawdown_pct,
        "observed_win_rate": metrics["win_rate_pct"] is not None
        and profile.min_win_rate_pct <= float(metrics["win_rate_pct"]) <= profile.max_win_rate_pct,
        "wilson_lower": metrics["wilson_95_lower_pct"] is not None
        and float(metrics["wilson_95_lower_pct"]) >= profile.min_win_rate_wilson_lower_pct,
        "payoff_ratio": metrics["payoff_ratio"] is not None
        and float(metrics["payoff_ratio"]) >= profile.min_payoff_ratio,
        "profit_factor": metrics["profit_factor"] is not None
        and float(metrics["profit_factor"]) >= profile.min_profit_factor,
        "calmar": metrics["calmar"] is not None and float(metrics["calmar"]) >= profile.min_calmar,
        "minimum_sample": metrics["selected_trade_count"] is not None
        and int(metrics["selected_trade_count"]) >= 200,
        "signal_days_120": metrics["signal_days"] is not None
        and int(metrics["signal_days"]) >= profile.min_signal_days,
        "all_rolling_12m": _all_rolling_pass(windows, profile),
        "pit_contract": evidence.get("pit_contract") is True,
        "temporal_contract": evidence.get("temporal_contract") is True,
        "cost_slippage": evidence.get("cost_slippage") is True,
        "artifact_execution": evidence.get("artifact_execution") is True,
        "strategy_signal_replay": evidence.get("strategy_signal_replay") is True,
        "strategy_entry_decision": evidence.get("strategy_entry_decision") is True,
        "strategy_selection_replay": evidence.get("strategy_selection_replay")
        is True,
        "outcome_replay": evidence.get("outcome_replay") is True,
        "double_cost": evidence.get("double_cost") is True,
        "regime": evidence.get("regime") is True,
    }
    development_ready = all(gates.values())
    final_oos = evidence.get("final_oos") is True
    shadow = evidence.get("shadow") is True
    live_monitoring = evidence.get("live_monitoring") is True
    live_proof = development_ready and final_oos and shadow and live_monitoring and bool(
        (validation_report.get("qualification") or {}).get("completion_pass")
    )
    gates.update({"final_oos": final_oos, "shadow": shadow, "live_monitoring": live_monitoring})
    blocking_gates = [key for key, passed in gates.items() if not passed]
    status = "live_proven" if live_proof else "qualified" if development_ready else "incomplete"
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "profile_id": profile.profile_id,
        "version": profile.version,
        "profile_hash": profile_payload["profile_hash"],
        "experiment_id": str(experiment_id),
        "profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "profile_hash": profile_payload["profile_hash"],
            "strategy_sha256": validation_report.get("strategy_sha256") or _sha256(strategy),
            "validation_sha256": validation_report.get("validation_sha256") or _sha256(validation),
        },
        "metrics": metrics,
        "targets": {
            "annualized_return_pct": profile.target_annualized_return_pct,
            "max_drawdown_pct": profile.max_drawdown_pct,
            "win_rate_pct": [profile.min_win_rate_pct, profile.max_win_rate_pct],
            "wilson_lower_pct": profile.min_win_rate_wilson_lower_pct,
            "payoff_ratio": profile.min_payoff_ratio,
            "profit_factor": profile.min_profit_factor,
            "calmar": profile.min_calmar,
            "signal_days": profile.min_signal_days,
        },
        "gates": gates,
        "blocking_gates": blocking_gates,
        "status": status,
        "evidence_scope": "live_proof" if live_proof else "development_only",
        "live_proof": live_proof,
        "evidence": {
            **dict(evidence),
            "pit_verified": evidence.get("pit_verified") is True,
            "live_proof": live_proof,
            "auto_order": False,
        },
        "cost_model": {
            key: aggregate.get(key)
            for key in (
                "capital_model",
                "exposure_multiplier",
                "max_active_positions",
                "hold_days",
                "annual_financing_rate_pct",
                "roundtrip_cost_bps",
                "slippage_bps",
            )
        },
        "metric_basis": {
            "trade_metrics": "selected_trade_return_pct",
            "portfolio_return": "rolling_365d_equity",
            "drawdown": (
                "slot_daily_close_peak_daily_low"
                if aggregate.get("capital_model") == "slot-daily"
                else "equity_curve"
            ),
            "calmar": "rolling_365d_return_over_same_window_drawdown",
        },
        "provenance": {
            "dataset_sha256": validation_report.get("dataset_sha256"),
            "qualified_trades_sha256": validation_report.get("dataset_sha256"),
            "source_artifact": source_artifact,
            "report_artifact": report_artifact,
            "validation": validation,
            "data_contract": evidence.get("data_contract") or {},
        },
        "ledger": ledger_anchor,
        "auto_order": False,
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def write_profile_evidence_receipt(path: str, receipt: Dict[str, Any]) -> None:
    expected = _sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    if receipt.get("receipt_sha256") != expected:
        raise ValueError("receipt_sha256 does not match the receipt")
    write_json(path, receipt)


def verify_profile_evidence_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Verify receipt identity and any content-addressed artifacts it names."""
    errors: List[str] = []
    if not isinstance(receipt, dict):
        return {"ok": False, "status": None, "errors": ["receipt_not_object"]}
    stored_hash = receipt.get("receipt_sha256")
    expected_hash = _sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    if stored_hash != expected_hash:
        errors.append("receipt_sha256_mismatch")
    expected_profile_hash = profile_to_dict(DEFAULT_PROFILE)["profile_hash"]
    if receipt.get("profile_id") != DEFAULT_PROFILE.profile_id:
        errors.append("profile_id_mismatch")
    if receipt.get("profile_hash") != expected_profile_hash:
        errors.append("profile_hash_mismatch")
    status = receipt.get("status")
    if status not in {"incomplete", "qualified", "live_proven"}:
        errors.append("status_invalid")
    blocking = set(receipt.get("blocking_gates") or [])
    if status == "qualified" and blocking - {"final_oos", "shadow", "live_monitoring"}:
        errors.append("qualified_receipt_has_blocking_gates")
    if status == "live_proven" and blocking:
        errors.append("live_proven_receipt_has_blocking_gates")
    if status == "live_proven" and receipt.get("live_proof") is not True:
        errors.append("live_proof_mismatch")
    provenance = receipt.get("provenance") or {}
    for name in ("source_artifact", "report_artifact"):
        descriptor = provenance.get(name)
        if not descriptor:
            continue
        path = descriptor.get("path")
        expected = descriptor.get("sha256")
        if not path or not expected:
            errors.append("%s_descriptor_incomplete" % name)
            continue
        artifact_path = Path(str(path))
        if not artifact_path.exists():
            errors.append("%s_missing" % name)
            continue
        actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append("%s_sha256_mismatch" % name)
    return {"ok": not errors, "status": status, "errors": list(dict.fromkeys(errors))}
