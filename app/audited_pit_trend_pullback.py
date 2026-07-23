"""Strict audited-PIT replay for the pre-registered MA20 reclaim strategy."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from app.a_share_universe import _is_excluded_name
from app.artifact_outcome_evidence import build_artifact_outcome_claim
from app.audited_pit_development_replay import (
    AuditedPITDevelopmentReplayError,
    _exact_membership_sessions,
    _load_exact_membership_bars,
    _producer_code_binding,
)
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _sha256,
    _write_content_addressed,
)
from app.execution import assess_entry_executability
from app.research_artifact_replay import ArtifactNativeReplayAdapter
from app.research_backtest import _artifact_open_verdict
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptError
from app.research_portfolio import (
    _select_with_portfolio_controls_receipt,
    _selection_trade_key,
)
from app.research_scope import market_scope_contract
from app.research_sweep import _trade_metrics


TREND_PULLBACK_SPEC = {
    "schema_version": "development-trend-pullback-ma20-reclaim-strict/v1",
    "signal_tag": "trend_pullback_ma20_reclaim",
    "required_signal_tags": ["trend_pullback_ma20_reclaim"],
    "signal_price_basis": "raw_ohlc_times_session_adj_factor",
    "adjustment_method": "causal_bar_factor_common_as_of_denominator_cancels",
    "membership_application": "signal_date_only",
    "minimum_signal_history_sessions": 60,
    "signal": {
        "trend_condition": "ma20_gt_ma60",
        "prior_condition": "prior_close_lt_prior_ma20",
        "reclaim_condition": "close_gte_ma20",
        "breakout_exclusion": "close_lte_prior_20_session_high",
        "moving_average_short_sessions": 20,
        "moving_average_long_sessions": 60,
        "prior_high_sessions": 20,
    },
    "hold_days": 5,
    "close_stop_loss_pct": 5.0,
    "close_stop_execution": "first_strictly_fillable_open_after_trigger_close",
    "blocked_sell_policy": "retry_each_following_open",
    "coverage_end_policy": (
        "uniform_signal_cutoff_requires_covered_planned_exit"
    ),
    "intraday_stop_fill_assumed": False,
    "exposure_multiplier": 1.0,
    "top_n": 3,
    "max_active_positions": 3,
    "capital_model": "slot-daily",
    "roundtrip_cost_bps": 25.0,
    "slippage_bps": 10.0,
    "annual_financing_rate_pct": 8.0,
    "entry_execution": dict(SIMPLE_BREAKOUT_SPEC["entry_execution"]),
    "rank": {
        "field": "signal_date_amount",
        "direction": "descending",
        "tie_breaker": "source_order_signal_date_then_symbol",
    },
    "strict_baseline": {
        "signal_tag": "breakout_20d",
        "minimum_signal_history_sessions": 60,
        "same_execution_and_cost_contract": True,
    },
    "forbidden_overlays": [
        "rsi_filter",
        "amount_ratio_filter",
        "breadth_filter",
        "industry_filter",
        "proxy_filter",
        "correlation_budget",
        "profit_lock",
        "prior_high_protection",
        "calendar_gap_exit",
    ],
}

_SIGNAL_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "amount",
    "adj_factor",
)
_EXIT_PATH_FIELDS = (
    "signal_date",
    "symbol",
    "entry_date",
    "exit_date",
    "planned_exit_date",
    "exit_reason",
    "stop_trigger_date",
    "holding_days",
    "return_pct",
    "max_adverse_pct",
    "max_favorable_pct",
    "mark_to_market_path",
    "exit_execution_attempts",
)


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    root = Path(__file__).resolve().parent
    module_names = (
        "artifact_outcome_evidence.py",
        "execution.py",
        "research_artifact_replay.py",
        "research_backtest.py",
        "research_portfolio.py",
    )
    refs = [
        {
            "module": name,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
        for name in module_names
    ]
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "strict_dependency_modules": refs,
        "trend_pullback_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pandas_version": pd.__version__,
    }
    return {
        "schema_version": "audited-pit-trend-pullback-producer/v1",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _stable_sidecar_reference(
    sidecar: Mapping[str, Any],
) -> dict[str, str]:
    digest = str(sidecar.get("artifact_sha256") or "")
    if (
        len(digest) != 64
        or digest != digest.lower()
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise AuditedPITDevelopmentReplayError(
            "sidecar artifact hash is invalid"
        )
    return {
        "artifact_sha256": digest,
        "relative_path": f"sidecars/{digest}.json",
    }


def _fixed_signal_masks(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in _SIGNAL_COLUMNS if column not in frame]
    if missing:
        raise AuditedPITDevelopmentReplayError(
            "trend pullback signal frame is missing required columns"
        )
    values = frame.copy()
    for column in _SIGNAL_COLUMNS:
        values[column] = pd.to_numeric(values[column], errors="coerce")
    adjusted_close = values["close"] * values["adj_factor"]
    adjusted_high = values["high"] * values["adj_factor"]
    ma20 = adjusted_close.rolling(20, min_periods=20).mean()
    ma60 = adjusted_close.rolling(60, min_periods=60).mean()
    prior_high20 = adjusted_high.shift(1).rolling(20, min_periods=20).max()
    history_complete = ma60.notna()
    pullback = (
        history_complete
        & (ma20 > ma60)
        & (adjusted_close.shift(1) < ma20.shift(1))
        & (adjusted_close >= ma20)
        & (adjusted_close <= prior_high20)
    ).fillna(False)
    breakout = (
        history_complete
        & (adjusted_close > prior_high20)
    ).fillna(False)
    if bool((pullback & breakout).any()):
        raise AuditedPITDevelopmentReplayError(
            "trend pullback and strict breakout signals overlap"
        )
    return pd.DataFrame(
        {
            "adjusted_close": adjusted_close,
            "ma20": ma20,
            "ma60": ma60,
            "prior_high20": prior_high20,
            "pullback": pullback.astype(bool),
            "breakout": breakout.astype(bool),
        },
        index=frame.index,
    )


def _eligible_signal_name(value: Any) -> str | None:
    if pd.isna(value):
        return None
    name = str(value or "").strip()
    if not name or _is_excluded_name(name):
        return None
    return name


def _load_suspension_evidence(
    connection: Any,
    *,
    start_date: str,
    end_date: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    rows = connection.execute(
        """
        SELECT suspension.ts_code, suspension.trade_date,
               suspension.suspend_type, suspension.suspend_timing,
               head.generation_id, head.manifest_sha256, head.published_at
        FROM market_session_generation_rows_suspend_d AS suspension
        JOIN market_session_generation_head AS head
          ON head.trade_date = suspension.trade_date
         AND head.generation_id = suspension.generation_id
        WHERE suspension.trade_date BETWEEN ? AND ?
          AND upper(trim(suspension.suspend_type)) = 'S'
        ORDER BY suspension.ts_code, suspension.trade_date,
                 suspension.suspend_timing
        """,
        (start_date, end_date),
    )
    for row in rows:
        symbol = str(row["ts_code"])[:6]
        trade_date = str(row["trade_date"])
        evidence[(symbol, trade_date)].append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "suspend_type": str(row["suspend_type"]),
                "suspend_timing": row["suspend_timing"],
                "generation_id": str(row["generation_id"]),
                "manifest_sha256": str(row["manifest_sha256"]),
                "published_at": str(row["published_at"]),
            }
        )
    return dict(evidence)


def _strategy_entry_filter(
    *,
    signal_adjusted_close: float,
    entry_adjusted_open: float,
) -> dict[str, Any]:
    control = TREND_PULLBACK_SPEC["entry_execution"]
    return assess_entry_executability(
        {"close": signal_adjusted_close},
        {"open": entry_adjusted_open},
        max_gap_up_pct=float(control["max_gap_up_pct"]),
        max_gap_down_pct=float(control["max_gap_down_pct"]),
        locked_limit_gap_pct=float(control["locked_limit_gap_pct"]),
        max_intraday_range_pct=float(control["max_intraday_range_pct"]),
        decision_cutoff=str(control["decision_cutoff"]),
    )


def _strict_close_stop_trade(
    *,
    adapter: ArtifactNativeReplayAdapter,
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]],
    frame: pd.DataFrame,
    symbol: str,
    signal_index: int,
    sessions: Sequence[str],
    session_positions: Mapping[str, int],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ] | None,
    hold_days: int,
    stop_loss_pct: float,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any],
]:
    signal_date = str(frame.at[signal_index, "date"])
    signal_position = session_positions.get(signal_date)
    if signal_position is None:
        raise AuditedPITDevelopmentReplayError(
            "signal date is absent from audited open sessions"
        )
    entry_position = signal_position + 1
    planned_exit_position = entry_position + int(hold_days)
    event: dict[str, Any] = {
        "symbol": str(symbol),
        "signal_date": signal_date,
        "status": None,
    }
    if (
        entry_position >= len(sessions)
        or planned_exit_position >= len(sessions)
    ):
        event["status"] = "administrative_signal_cutoff"
        return None, None, event

    entry_date = str(sessions[entry_position])
    planned_exit_date = str(sessions[planned_exit_position])
    buy = _artifact_open_verdict(
        adapter, verdict_cache, str(symbol), entry_date, "buy"
    )
    event["entry_date"] = entry_date
    event["entry_verdict_sha256"] = _sha256(buy)
    if not buy["fillable"]:
        event["status"] = "entry_unfillable"
        event["entry_reason"] = buy.get("reason")
        return None, None, event

    if frame["date"].astype(str).duplicated().any():
        raise AuditedPITDevelopmentReplayError(
            "symbol frame contains duplicate market dates"
        )
    date_positions = {
        str(value): int(position)
        for position, value in enumerate(frame["date"].tolist())
    }
    entry_index = date_positions.get(entry_date)
    if entry_index is None:
        raise AuditedPITDevelopmentReplayError(
            "strict fill verdict has no matching entry bar"
        )
    entry_row = frame.iloc[entry_index]
    entry_raw_price = float(buy["raw_price"])
    if not math.isclose(
        float(entry_row["open"]),
        entry_raw_price,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict entry price differs from frozen market bar"
        )
    signal_row = frame.iloc[signal_index]
    signal_adjusted_close = float(signal_row["close"]) * float(
        signal_row["adj_factor"]
    )
    entry_adjusted_open = entry_raw_price * float(entry_row["adj_factor"])
    strategy_filter = _strategy_entry_filter(
        signal_adjusted_close=signal_adjusted_close,
        entry_adjusted_open=entry_adjusted_open,
    )
    event["strategy_entry_filter_sha256"] = _sha256(strategy_filter)
    if not strategy_filter["executable"]:
        event["status"] = "entry_strategy_filter_rejected"
        return None, None, event

    entry_total_return_price = entry_adjusted_open
    trigger_date: str | None = None
    trigger_return_pct: float | None = None
    trigger_position: int | None = None
    for candidate_position in range(
        entry_position, min(planned_exit_position, len(sessions))
    ):
        candidate_date = str(sessions[candidate_position])
        row_index = date_positions.get(candidate_date)
        if row_index is None:
            continue
        row = frame.iloc[row_index]
        close_total_return_price = float(row["close"]) * float(row["adj_factor"])
        close_return_pct = (
            close_total_return_price / entry_total_return_price - 1.0
        ) * 100.0
        if close_return_pct <= -abs(float(stop_loss_pct)):
            trigger_date = candidate_date
            trigger_return_pct = round(close_return_pct, 4)
            trigger_position = candidate_position
            break

    desired_exit_position = (
        trigger_position + 1
        if trigger_position is not None
        else planned_exit_position
    )
    exit_attempts: list[dict[str, Any]] = []
    exit_position: int | None = None
    exit_verdict: dict[str, Any] | None = None
    for candidate_position in range(
        min(desired_exit_position, len(sessions)), len(sessions)
    ):
        candidate_date = str(sessions[candidate_position])
        verdict = _artifact_open_verdict(
            adapter, verdict_cache, str(symbol), candidate_date, "sell"
        )
        exit_attempts.append(
            {
                "trade_date": candidate_date,
                "fillable": verdict["fillable"],
                "reason": verdict.get("reason"),
                "raw_price": verdict.get("raw_price"),
                "generation_proof": verdict["generation_proof"],
            }
        )
        if verdict["fillable"]:
            exit_position = candidate_position
            exit_verdict = verdict
            break
    event["exit_attempts_sha256"] = _sha256(exit_attempts)
    if exit_position is None or exit_verdict is None:
        censored = _build_censored_position(
            frame=frame,
            sessions=sessions,
            entry_position=entry_position,
            entry_date=entry_date,
            entry_raw_price=entry_raw_price,
            symbol=str(symbol),
            signal_date=signal_date,
            planned_exit_date=planned_exit_date,
            trigger_date=trigger_date,
            trigger_return_pct=trigger_return_pct,
            strategy_filter=strategy_filter,
            buy=buy,
            exit_attempts=exit_attempts,
            suspension_evidence=suspension_evidence or {},
        )
        censored["source_execution_requery_verification"] = (
            _verify_execution_evidence_against_adapter(
                adapter=adapter,
                symbol=str(symbol),
                trade=censored,
            )
        )
        event["status"] = "entered_unresolved_exit"
        event["planned_exit_date"] = planned_exit_date
        event["censored_position_sha256"] = _sha256(censored)
        return None, censored, event

    exit_date = str(sessions[exit_position])
    exit_index = date_positions.get(exit_date)
    if exit_index is None:
        raise AuditedPITDevelopmentReplayError(
            "strict fill verdict has no matching exit bar"
        )
    exit_raw_price = float(exit_verdict["raw_price"])
    if not math.isclose(
        float(frame.at[exit_index, "open"]),
        exit_raw_price,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict exit price differs from frozen market bar"
        )

    held_market_sessions = [
        str(value) for value in sessions[entry_position : exit_position + 1]
    ]
    observed_held_dates = [
        value for value in held_market_sessions if value in date_positions
    ]
    if (
        not observed_held_dates
        or observed_held_dates[0] != entry_date
        or observed_held_dates[-1] != exit_date
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict outcome has an incomplete entry or exit mark"
        )
    claim_bars = {}
    for trade_date in observed_held_dates:
        row = frame.iloc[date_positions[trade_date]]
        claim_bars[trade_date] = {
            "trade_date": trade_date,
            "raw_open": row["open"],
            "raw_high": row["high"],
            "raw_low": row["low"],
            "raw_close": row["close"],
            "bar_adj_factor": row["adj_factor"],
        }
    outcome_claim = build_artifact_outcome_claim(
        bars_by_date=claim_bars,
        held_dates=observed_held_dates,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_raw_price=entry_raw_price,
        exit_raw_price=exit_raw_price,
    )
    missing_marks = [
        value for value in held_market_sessions if value not in date_positions
    ]
    suspension_carry_forward_evidence: list[dict[str, Any]] = []
    for trade_date in missing_marks:
        proofs = list((suspension_evidence or {}).get((str(symbol), trade_date), []))
        if not proofs:
            raise AuditedPITDevelopmentReplayError(
                "held market session is missing without official suspension evidence"
            )
        suspension_carry_forward_evidence.append(
            {
                "trade_date": trade_date,
                "proofs": [dict(proof) for proof in proofs],
            }
        )
    observed_marks = {
        str(mark["date"]): dict(mark)
        for mark in outcome_claim["mark_to_market_path"]
    }
    portfolio_mark_to_market_path: list[dict[str, Any]] = []
    last_close_return_pct: float | None = None
    for trade_date in held_market_sessions:
        observed = observed_marks.get(trade_date)
        if observed is not None:
            marked = {**observed, "valuation_source": "audited_raw_ohlc"}
            last_close_return_pct = float(marked["close_return_pct"])
        else:
            if last_close_return_pct is None:
                raise AuditedPITDevelopmentReplayError(
                    "suspension carry-forward has no prior observed close"
                )
            marked = {
                "date": trade_date,
                "open_return_pct": last_close_return_pct,
                "high_return_pct": last_close_return_pct,
                "close_return_pct": last_close_return_pct,
                "low_return_pct": last_close_return_pct,
                "valuation_source": "official_suspension_carry_forward",
            }
        portfolio_mark_to_market_path.append(marked)
    realized = {
        "entry_date": entry_date,
        "exit_date": exit_date,
        "planned_exit_date": planned_exit_date,
        "exit_reason": (
            "close_stop_next_open"
            if trigger_date is not None
            else "time_exit_next_open"
        ),
        "stop_trigger_date": trigger_date,
        "stop_trigger_close_return_pct": trigger_return_pct,
        "holding_days": max(1, exit_position - entry_position),
        "planned_holding_sessions": int(hold_days),
        "actual_holding_sessions": max(1, exit_position - entry_position),
        "held_market_session_count": len(held_market_sessions),
        "unmarked_market_sessions": missing_marks,
        **{
            key: value
            for key, value in outcome_claim.items()
            if key != "mark_to_market_path"
        },
        "artifact_observed_mark_to_market_path": outcome_claim[
            "mark_to_market_path"
        ],
        "mark_to_market_path": portfolio_mark_to_market_path,
        "portfolio_valuation_schema_version": (
            "artifact-suspension-carry-forward/v1"
        ),
        "suspension_carry_forward_evidence": (
            suspension_carry_forward_evidence
        ),
        "entry_executability": {
            **strategy_filter,
            "evidence_source": "audited_artifact_next_open",
            "strict_fill_gate": buy,
        },
        "exit_execution_evidence": exit_verdict,
        "exit_execution_attempts": exit_attempts,
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
    }
    verification = _verify_completed_close_stop_trade(
        frame=frame,
        sessions=sessions,
        session_positions=session_positions,
        signal_index=signal_index,
        symbol=str(symbol),
        trade=realized,
        suspension_evidence=suspension_evidence or {},
        hold_days=hold_days,
        stop_loss_pct=stop_loss_pct,
    )
    realized["close_stop_verification"] = verification
    realized["source_execution_requery_verification"] = (
        _verify_execution_evidence_against_adapter(
            adapter=adapter,
            symbol=str(symbol),
            trade=realized,
        )
    )
    event.update(
        {
            "status": "candidate_built",
            "exit_date": exit_date,
            "exit_reason": realized["exit_reason"],
            "stop_trigger_date": trigger_date,
            "outcome_claim_sha256": _sha256(outcome_claim),
        }
    )
    return realized, None, event


def _build_censored_position(
    *,
    frame: pd.DataFrame,
    sessions: Sequence[str],
    entry_position: int,
    entry_date: str,
    entry_raw_price: float,
    symbol: str,
    signal_date: str,
    planned_exit_date: str,
    trigger_date: str | None,
    trigger_return_pct: float | None,
    strategy_filter: Mapping[str, Any],
    buy: Mapping[str, Any],
    exit_attempts: Sequence[Mapping[str, Any]],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
) -> dict[str, Any]:
    if any(attempt.get("fillable") is not False for attempt in exit_attempts):
        raise AuditedPITDevelopmentReplayError(
            "right-censored position contains a fillable sell attempt"
        )
    date_positions = {
        str(value): int(position)
        for position, value in enumerate(frame["date"].tolist())
    }
    entry_index = date_positions.get(entry_date)
    if entry_index is None:
        raise AuditedPITDevelopmentReplayError(
            "right-censored position has no entry bar"
        )
    entry_total_return_price = (
        entry_raw_price * float(frame.at[entry_index, "adj_factor"])
    )
    held_market_sessions = [
        str(value) for value in sessions[entry_position:]
    ]
    path: list[dict[str, Any]] = []
    suspension_proofs: list[dict[str, Any]] = []
    last_close_return_pct: float | None = None
    for trade_date in held_market_sessions:
        row_index = date_positions.get(trade_date)
        if row_index is not None:
            row = frame.iloc[row_index]

            def marked_return(field: str) -> float:
                return round(
                    (
                        float(row[field])
                        * float(row["adj_factor"])
                        / entry_total_return_price
                        - 1.0
                    )
                    * 100.0,
                    4,
                )

            mark = {
                "date": trade_date,
                "open_return_pct": marked_return("open"),
                "high_return_pct": marked_return("high"),
                "close_return_pct": marked_return("close"),
                "low_return_pct": marked_return("low"),
                "valuation_source": "audited_raw_ohlc",
            }
            last_close_return_pct = float(mark["close_return_pct"])
        else:
            proofs = [
                dict(proof)
                for proof in suspension_evidence.get(
                    (str(symbol), trade_date), []
                )
            ]
            if not proofs or last_close_return_pct is None:
                raise AuditedPITDevelopmentReplayError(
                    "right-censored position has an unexplained missing held bar"
                )
            suspension_proofs.append(
                {"trade_date": trade_date, "proofs": proofs}
            )
            mark = {
                "date": trade_date,
                "open_return_pct": last_close_return_pct,
                "high_return_pct": last_close_return_pct,
                "close_return_pct": last_close_return_pct,
                "low_return_pct": last_close_return_pct,
                "valuation_source": "official_suspension_carry_forward",
            }
        path.append(mark)
    if not path:
        raise AuditedPITDevelopmentReplayError(
            "right-censored position has no valuation path"
        )
    verification = {
        "schema_version": "artifact-close-stop-censor-verification/v1",
        "symbol": str(symbol),
        "signal_date": signal_date,
        "entry_date": entry_date,
        "coverage_end_date": str(sessions[-1]),
        "planned_exit_date": planned_exit_date,
        "stop_trigger_date": trigger_date,
        "entry_proof_sha256": _sha256(buy),
        "exit_attempts_sha256": _sha256(list(exit_attempts)),
        "mark_to_market_path_sha256": _sha256(path),
        "suspension_carry_forward_sha256": _sha256(suspension_proofs),
    }
    verification["receipt_sha256"] = _sha256(verification)
    return {
        "entry_date": entry_date,
        "exit_date": str(sessions[-1]),
        "planned_exit_date": planned_exit_date,
        "selection_exit_date_semantics": "coverage_end_position_still_open",
        "exit_reason": "right_censored_open_position",
        "stop_trigger_date": trigger_date,
        "stop_trigger_close_return_pct": trigger_return_pct,
        "holding_days": max(1, len(sessions) - 1 - entry_position),
        "actual_holding_sessions": max(
            1, len(sessions) - 1 - entry_position
        ),
        "right_censored": True,
        "outcome_complete": False,
        "censor_reason": "no_strict_sell_fill_through_coverage_end",
        "entry_executability": {
            **dict(strategy_filter),
            "evidence_source": "audited_artifact_next_open",
            "strict_fill_gate": dict(buy),
        },
        "exit_execution_attempts": [dict(item) for item in exit_attempts],
        "mark_to_market_path": path,
        "portfolio_valuation_schema_version": (
            "artifact-suspension-carry-forward/v1"
        ),
        "suspension_carry_forward_evidence": suspension_proofs,
        "last_marked_return_pct": path[-1]["close_return_pct"],
        "max_adverse_pct": min(
            float(mark["low_return_pct"]) for mark in path
        ),
        "max_favorable_pct": max(
            float(mark["high_return_pct"]) for mark in path
        ),
        "censor_verification": verification,
        "price_basis": "raw_unadjusted_entry_open",
        "return_price_basis": "causal_total_return_mark_only",
    }


def _verify_execution_evidence_against_adapter(
    *,
    adapter: ArtifactNativeReplayAdapter,
    symbol: str,
    trade: Mapping[str, Any],
) -> dict[str, Any]:
    entry_date = str(trade.get("entry_date") or "")
    claimed_entry = trade.get("entry_executability", {}).get(
        "strict_fill_gate"
    )
    expected_entry = _artifact_open_verdict(
        adapter, {}, str(symbol), entry_date, "buy"
    )
    if claimed_entry != expected_entry:
        raise AuditedPITDevelopmentReplayError(
            "source requery entry execution evidence mismatch"
        )
    claimed_attempts = trade.get("exit_execution_attempts")
    if not isinstance(claimed_attempts, list):
        raise AuditedPITDevelopmentReplayError(
            "source requery sell execution attempts are missing"
        )
    expected_attempts: list[dict[str, Any]] = []
    requery_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    for claimed in claimed_attempts:
        trade_date = str(claimed.get("trade_date") or "")
        verdict = _artifact_open_verdict(
            adapter,
            requery_cache,
            str(symbol),
            trade_date,
            "sell",
        )
        expected_attempts.append(
            {
                "trade_date": trade_date,
                "fillable": verdict["fillable"],
                "reason": verdict.get("reason"),
                "raw_price": verdict.get("raw_price"),
                "generation_proof": verdict["generation_proof"],
            }
        )
    if claimed_attempts != expected_attempts:
        raise AuditedPITDevelopmentReplayError(
            "source requery sell execution evidence mismatch"
        )
    receipt = {
        "schema_version": "artifact-execution-source-requery/v1",
        "artifact_root_sha256": getattr(
            adapter, "artifact_root_sha256", None
        ),
        "symbol": str(symbol),
        "entry_date": entry_date,
        "entry_evidence_sha256": _sha256(expected_entry),
        "sell_attempt_count": len(expected_attempts),
        "sell_attempts_sha256": _sha256(expected_attempts),
    }
    return {**receipt, "receipt_sha256": _sha256(receipt)}


def _verify_completed_close_stop_trade(
    *,
    frame: pd.DataFrame,
    sessions: Sequence[str],
    session_positions: Mapping[str, int],
    signal_index: int,
    symbol: str,
    trade: Mapping[str, Any],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
    hold_days: int,
    stop_loss_pct: float,
) -> dict[str, Any]:
    signal_date = str(frame.at[signal_index, "date"])
    signal_position = session_positions.get(signal_date)
    if signal_position is None:
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification signal session is missing"
        )
    entry_position = signal_position + 1
    planned_position = entry_position + int(hold_days)
    if planned_position >= len(sessions):
        raise AuditedPITDevelopmentReplayError(
            "completed trade violates the uniform signal cutoff"
        )
    entry_date = str(sessions[entry_position])
    planned_exit_date = str(sessions[planned_position])
    exit_date = str(trade.get("exit_date") or "")
    if (
        trade.get("entry_date") != entry_date
        or trade.get("planned_exit_date") != planned_exit_date
        or exit_date not in session_positions
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification dates are inconsistent"
        )
    date_positions = {
        str(value): int(position)
        for position, value in enumerate(frame["date"].tolist())
    }
    entry_index = date_positions.get(entry_date)
    exit_index = date_positions.get(exit_date)
    if entry_index is None or exit_index is None:
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification entry or exit bar is missing"
        )
    entry_gate = trade.get("entry_executability", {}).get("strict_fill_gate")
    if (
        not isinstance(entry_gate, Mapping)
        or entry_gate.get("fillable") is not True
        or str(entry_gate.get("generation_proof", {}).get("trade_date"))
        != entry_date
        or not math.isclose(
            float(entry_gate.get("raw_price")),
            float(frame.at[entry_index, "open"]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification entry proof is invalid"
        )
    expected_entry_filter = _strategy_entry_filter(
        signal_adjusted_close=(
            float(frame.at[signal_index, "close"])
            * float(frame.at[signal_index, "adj_factor"])
        ),
        entry_adjusted_open=(
            float(frame.at[entry_index, "open"])
            * float(frame.at[entry_index, "adj_factor"])
        ),
    )
    claimed_entry_filter = {
        key: trade["entry_executability"].get(key)
        for key in expected_entry_filter
    }
    if claimed_entry_filter != expected_entry_filter:
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification entry filter mismatch"
        )

    entry_total_return_price = (
        float(frame.at[entry_index, "open"])
        * float(frame.at[entry_index, "adj_factor"])
    )
    trigger_date: str | None = None
    trigger_position: int | None = None
    trigger_return_pct: float | None = None
    for candidate_position in range(
        entry_position, min(planned_position, len(sessions))
    ):
        candidate_date = str(sessions[candidate_position])
        row_index = date_positions.get(candidate_date)
        if row_index is None:
            continue
        close_total_return_price = (
            float(frame.at[row_index, "close"])
            * float(frame.at[row_index, "adj_factor"])
        )
        close_return_pct = (
            close_total_return_price / entry_total_return_price - 1.0
        ) * 100.0
        if close_return_pct <= -abs(float(stop_loss_pct)):
            trigger_date = candidate_date
            trigger_position = candidate_position
            trigger_return_pct = round(close_return_pct, 4)
            break
    if (
        trade.get("stop_trigger_date") != trigger_date
        or trade.get("stop_trigger_close_return_pct")
        != trigger_return_pct
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification trigger mismatch"
        )
    expected_exit_reason = (
        "close_stop_next_open"
        if trigger_date is not None
        else "time_exit_next_open"
    )
    if trade.get("exit_reason") != expected_exit_reason:
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification exit reason mismatch"
        )
    desired_exit_position = (
        trigger_position + 1
        if trigger_position is not None
        else planned_position
    )
    exit_position = session_positions[exit_date]
    actual_holding_sessions = max(1, exit_position - entry_position)
    if (
        trade.get("holding_days") != actual_holding_sessions
        or trade.get("actual_holding_sessions")
        != actual_holding_sessions
        or trade.get("planned_holding_sessions") != int(hold_days)
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification holding period mismatch"
        )
    expected_attempt_dates = [
        str(value) for value in sessions[desired_exit_position : exit_position + 1]
    ]
    attempts = trade.get("exit_execution_attempts")
    if (
        not isinstance(attempts, list)
        or [str(item.get("trade_date")) for item in attempts]
        != expected_attempt_dates
        or not attempts
        or any(item.get("fillable") is not False for item in attempts[:-1])
        or any(item.get("raw_price") is not None for item in attempts[:-1])
        or any(
            str(item.get("generation_proof", {}).get("trade_date"))
            != expected_date
            for item, expected_date in zip(
                attempts, expected_attempt_dates
            )
        )
        or attempts[-1].get("fillable") is not True
        or str(attempts[-1].get("generation_proof", {}).get("trade_date"))
        != exit_date
        or not math.isclose(
            float(attempts[-1].get("raw_price")),
            float(frame.at[exit_index, "open"]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification sell retry path mismatch"
        )
    held_market_sessions = [
        str(value) for value in sessions[entry_position : exit_position + 1]
    ]
    observed_dates = [
        value for value in held_market_sessions if value in date_positions
    ]
    claim_bars = {
        trade_date: {
            "trade_date": trade_date,
            "raw_open": frame.at[date_positions[trade_date], "open"],
            "raw_high": frame.at[date_positions[trade_date], "high"],
            "raw_low": frame.at[date_positions[trade_date], "low"],
            "raw_close": frame.at[date_positions[trade_date], "close"],
            "bar_adj_factor": frame.at[date_positions[trade_date], "adj_factor"],
        }
        for trade_date in observed_dates
    }
    expected_claim = build_artifact_outcome_claim(
        bars_by_date=claim_bars,
        held_dates=observed_dates,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_raw_price=entry_gate["raw_price"],
        exit_raw_price=attempts[-1]["raw_price"],
    )
    claim_fields = (
        "outcome_claim_schema_version",
        "outcome_rounding_mode",
        "entry_raw_price",
        "exit_raw_price",
        "return_pct",
        "max_adverse_pct",
        "max_favorable_pct",
    )
    if any(trade.get(field) != expected_claim[field] for field in claim_fields):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification outcome claim mismatch"
        )
    if trade.get("artifact_observed_mark_to_market_path") != expected_claim[
        "mark_to_market_path"
    ]:
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification observed marks mismatch"
        )
    missing_dates = [
        value for value in held_market_sessions if value not in date_positions
    ]
    expected_suspensions = [
        {
            "trade_date": trade_date,
            "proofs": [
                dict(proof)
                for proof in suspension_evidence.get((str(symbol), trade_date), [])
            ],
        }
        for trade_date in missing_dates
    ]
    if (
        any(not item["proofs"] for item in expected_suspensions)
        or trade.get("suspension_carry_forward_evidence")
        != expected_suspensions
        or len(trade.get("mark_to_market_path") or [])
        != len(held_market_sessions)
    ):
        raise AuditedPITDevelopmentReplayError(
            "close-stop verification suspension carry-forward mismatch"
        )
    expected_marks_by_date = {
        str(mark["date"]): {
            **mark,
            "valuation_source": "audited_raw_ohlc",
        }
        for mark in expected_claim["mark_to_market_path"]
    }
    previous_close: float | None = None
    for trade_date, mark in zip(
        held_market_sessions, trade["mark_to_market_path"]
    ):
        if str(mark.get("date")) != trade_date:
            raise AuditedPITDevelopmentReplayError(
                "close-stop verification portfolio marks are misaligned"
            )
        expected_observed_mark = expected_marks_by_date.get(trade_date)
        if expected_observed_mark is not None:
            if mark != expected_observed_mark:
                raise AuditedPITDevelopmentReplayError(
                    "close-stop verification observed portfolio mark mismatch"
                )
        elif mark.get("valuation_source") == "official_suspension_carry_forward":
            if previous_close is None or any(
                float(mark[field]) != previous_close
                for field in (
                    "open_return_pct",
                    "high_return_pct",
                    "close_return_pct",
                    "low_return_pct",
                )
            ):
                raise AuditedPITDevelopmentReplayError(
                    "close-stop verification carry-forward value mismatch"
                )
        else:
            raise AuditedPITDevelopmentReplayError(
                "close-stop verification unknown valuation source"
            )
        previous_close = float(mark["close_return_pct"])
    receipt = {
        "schema_version": "artifact-close-stop-verification/v1",
        "signal_date": signal_date,
        "entry_date": entry_date,
        "planned_exit_date": planned_exit_date,
        "exit_date": exit_date,
        "exit_reason": trade.get("exit_reason"),
        "stop_trigger_date": trigger_date,
        "entry_proof_sha256": _sha256(entry_gate),
        "exit_attempts_sha256": _sha256(attempts),
        "outcome_claim_sha256": _sha256(expected_claim),
        "suspension_carry_forward_sha256": _sha256(expected_suspensions),
    }
    return {**receipt, "receipt_sha256": _sha256(receipt)}


def _exit_paths(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {field: trade.get(field) for field in _EXIT_PATH_FIELDS}
        for trade in trades
    ]


def _build_strict_candidates(
    *,
    bars: pd.DataFrame,
    adapter: ArtifactNativeReplayAdapter,
    sessions: Sequence[str],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    session_positions = {
        str(value): position for position, value in enumerate(sessions)
    }
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    pullback: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    pullback_censored: list[dict[str, Any]] = []
    baseline_censored: list[dict[str, Any]] = []
    signal_keys: dict[str, list[str]] = {
        "trend_pullback_ma20_reclaim": [],
        "breakout_20d": [],
    }
    execution_events: dict[str, list[dict[str, Any]]] = {
        "trend_pullback_ma20_reclaim": [],
        "breakout_20d": [],
    }
    status_counts: dict[str, Counter[str]] = {
        "trend_pullback_ma20_reclaim": Counter(),
        "breakout_20d": Counter(),
    }
    overlap_count = 0

    for ts_code, raw_group in bars.groupby("ts_code", sort=True):
        frame = raw_group.sort_values("date", kind="mergesort").reset_index(
            drop=True
        ).copy()
        masks = _fixed_signal_masks(frame)
        overlap_count += int((masks["pullback"] & masks["breakout"]).sum())
        symbol = str(ts_code)[:6]
        for family, column, tags, target, censored_target in (
            (
                "trend_pullback_ma20_reclaim",
                "pullback",
                ["trend_pullback_ma20_reclaim"],
                pullback,
                pullback_censored,
            ),
            (
                "breakout_20d",
                "breakout",
                ["breakout_20d"],
                baseline,
                baseline_censored,
            ),
        ):
            for index in masks.index[masks[column]]:
                signal_date = str(frame.at[index, "date"])
                name = _eligible_signal_name(frame.at[index, "membership_name"])
                if name is None:
                    status_counts[family]["signal_date_membership_rejected"] += 1
                    continue
                signal_key = f"{signal_date}|{symbol}"
                signal_keys[family].append(signal_key)
                realized, censored, event = _strict_close_stop_trade(
                    adapter=adapter,
                    verdict_cache=verdict_cache,
                    frame=frame,
                    symbol=symbol,
                    signal_index=int(index),
                    sessions=sessions,
                    session_positions=session_positions,
                    suspension_evidence=suspension_evidence,
                    hold_days=int(TREND_PULLBACK_SPEC["hold_days"]),
                    stop_loss_pct=float(
                        TREND_PULLBACK_SPEC["close_stop_loss_pct"]
                    ),
                )
                status_counts[family][str(event["status"])] += 1
                execution_events[family].append(event)
                amount = float(frame.at[index, "amount"])
                if not math.isfinite(amount):
                    amount = 0.0
                metadata = {
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "name": name,
                    "action": "BUY",
                    "score": 1.0,
                    "rank_score": amount,
                    "candidate_amount": amount,
                    "market_level": "audited_pit_development",
                    "signal_tags": tags,
                    "current_universe_bias": False,
                }
                if realized is not None:
                    target.append({**realized, **metadata})
                if censored is not None:
                    censored_target.append({**censored, **metadata})

    if overlap_count != 0:
        raise AuditedPITDevelopmentReplayError(
            "trend pullback and strict breakout signals are not mutually exclusive"
        )
    pullback.sort(key=lambda item: (item["signal_date"], item["symbol"]))
    baseline.sort(key=lambda item: (item["signal_date"], item["symbol"]))
    pullback_censored.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    baseline_censored.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    receipt = {
        "schema_version": "trend-pullback-strict-candidate-receipt/v1",
        "parameters": {
            "minimum_signal_history_sessions": TREND_PULLBACK_SPEC[
                "minimum_signal_history_sessions"
            ],
            "hold_days": TREND_PULLBACK_SPEC["hold_days"],
            "close_stop_loss_pct": TREND_PULLBACK_SPEC[
                "close_stop_loss_pct"
            ],
            "entry_execution": TREND_PULLBACK_SPEC["entry_execution"],
            "blocked_sell_policy": TREND_PULLBACK_SPEC["blocked_sell_policy"],
            "coverage_end_policy": TREND_PULLBACK_SPEC["coverage_end_policy"],
        },
        "signal_overlap_count": overlap_count,
        "strict_verdict_cache_count": len(verdict_cache),
        "families": {},
    }
    for family, candidates, censored_positions in (
        (
            "trend_pullback_ma20_reclaim",
            pullback,
            pullback_censored,
        ),
        ("breakout_20d", baseline, baseline_censored),
    ):
        selection_candidates = [*candidates, *censored_positions]
        selection_candidates.sort(
            key=lambda item: (item["signal_date"], item["symbol"])
        )
        receipt["families"][family] = {
            "eligible_signal_count": len(signal_keys[family]),
            "eligible_signal_keys_sha256": _sha256(signal_keys[family]),
            "status_counts": dict(sorted(status_counts[family].items())),
            "execution_event_count": len(execution_events[family]),
            "execution_events_sha256": _sha256(execution_events[family]),
            "candidate_count": len(candidates),
            "candidate_trade_keys_sha256": _sha256(
                [_selection_trade_key(trade) for trade in candidates]
            ),
            "candidate_outcomes_sha256": _sha256(_exit_paths(candidates)),
            "candidates_sha256": _sha256(candidates),
            "right_censored_position_count": len(censored_positions),
            "right_censored_positions_sha256": _sha256(
                censored_positions
            ),
            "selection_candidate_count": len(selection_candidates),
            "selection_candidates_sha256": _sha256(
                selection_candidates
            ),
        }
    receipt["receipt_sha256"] = _sha256(receipt)
    return (
        pullback,
        baseline,
        pullback_censored,
        baseline_censored,
        receipt,
    )


def _selection_receipt(
    trades: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_signal_date[str(trade["signal_date"])].append(trade)
    return _select_with_portfolio_controls_receipt(
        by_signal_date,
        top_n=int(TREND_PULLBACK_SPEC["top_n"]),
        max_active_positions=int(TREND_PULLBACK_SPEC["max_active_positions"]),
    )


def _evaluate(
    trades: list[dict[str, Any]],
    censored_positions: list[dict[str, Any]],
    required_tags: list[str],
    *,
    evaluation_session_dates: Sequence[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        evaluation_session_dates is None
        or isinstance(evaluation_session_dates, (str, bytes))
        or len(evaluation_session_dates) == 0
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict evaluation requires an audited evaluation session grid"
        )
    evaluation_start_date = str(evaluation_session_dates[0])
    evaluation_end_date = str(evaluation_session_dates[-1])
    if any(
        trade.get("censor_reason")
        != "no_strict_sell_fill_through_coverage_end"
        for trade in censored_positions
    ):
        raise AuditedPITDevelopmentReplayError(
            "strict evaluation received an unknown censor reason"
        )
    selection_candidates = [*trades, *censored_positions]
    selection_candidates.sort(
        key=lambda item: (item["signal_date"], item["symbol"])
    )
    selected, selection_receipt = _selection_receipt(selection_candidates)
    selected_censored = [
        trade for trade in selected if trade.get("right_censored") is True
    ]
    selected_complete = [
        trade for trade in selected if trade.get("right_censored") is not True
    ]
    metrics = _trade_metrics(
        selected_complete,
        hold_days=int(TREND_PULLBACK_SPEC["hold_days"]),
        max_active_positions=int(TREND_PULLBACK_SPEC["max_active_positions"]),
        exposure_multiplier=float(
            TREND_PULLBACK_SPEC["exposure_multiplier"]
        ),
        annual_financing_rate_pct=float(
            TREND_PULLBACK_SPEC["annual_financing_rate_pct"]
        ),
        roundtrip_cost_bps=float(TREND_PULLBACK_SPEC["roundtrip_cost_bps"]),
        slippage_bps=float(TREND_PULLBACK_SPEC["slippage_bps"]),
        capital_model=str(TREND_PULLBACK_SPEC["capital_model"]),
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        evaluation_session_dates=evaluation_session_dates,
    )
    max_drawdown = abs(float(metrics.get("portfolio_max_drawdown_pct") or 0))
    win_rate = float(metrics.get("trade_win_rate_pct") or 0)
    latest_return = metrics.get("rolling_1y_latest_return_pct")
    profit_factor = metrics.get("trade_profit_factor")
    calmar = metrics.get("calmar_latest_12m")
    sample_pass = len(selected_complete) >= 20
    evidence_complete = not selected_censored
    win_drawdown_pass = bool(
        sample_pass and win_rate >= 52.0 and max_drawdown <= 15.0
    )
    one_year_return_pass = bool(
        metrics.get("rolling_1y_latest_full_window")
        and latest_return is not None
        and float(latest_return) >= 50.0
    )
    profit_factor_pass = bool(
        profit_factor is not None and float(profit_factor) >= 1.3
    )
    calmar_pass = bool(calmar is not None and float(calmar) >= 1.5)
    quality_pass = profit_factor_pass and calmar_pass
    windows = metrics.get("rolling_1y_windows") or []
    rolling_stability_pass = bool(
        windows
        and all(
            window.get("return_pct") is not None
            and float(window["return_pct"]) >= 50.0
            and window.get("max_drawdown_pct") is not None
            and abs(float(window["max_drawdown_pct"])) <= 15.0
            and window.get("payoff_ratio") is not None
            and float(window["payoff_ratio"]) >= 1.3
            and window.get("profit_factor") is not None
            and float(window["profit_factor"]) >= 1.3
            and window.get("calmar") is not None
            and float(window["calmar"]) >= 1.5
            for window in windows
        )
    )
    target_all_pass = bool(
        evidence_complete
        and win_drawdown_pass
        and one_year_return_pass
        and quality_pass
    )
    row = {
        "label": "+".join(sorted(required_tags)) + "|all_market_levels",
        "required_signal_tags": sorted(required_tags),
        "market_levels": [],
        **metrics,
        "selection_candidate_count": len(selection_candidates),
        "selected_position_count": len(selected),
        "selected_complete_trade_count": len(selected_complete),
        "selected_right_censored_position_count": len(selected_censored),
        "selected_right_censored_trade_keys": [
            _selection_trade_key(trade) for trade in selected_censored
        ],
        "evidence_complete": evidence_complete,
        "target_minimum_sample_pass": sample_pass,
        "target_win_drawdown_pass": win_drawdown_pass,
        "target_one_year_return_pass": one_year_return_pass,
        "target_profit_factor_pass": profit_factor_pass,
        "target_calmar_pass": calmar_pass,
        "target_quality_pass": quality_pass,
        "target_rolling_12m_stability_pass": rolling_stability_pass,
        "target_all_pass": target_all_pass,
        "target_gap_1y_return_pct": (
            round(50.0 - float(latest_return), 2)
            if latest_return is not None
            else None
        ),
    }
    sweep = {
        "schema_version": "strict-fixed-strategy-evaluation/v1",
        "qualified_trade_count": len(trades),
        "right_censored_position_count": len(censored_positions),
        "blocking_right_censored_position_count": len(censored_positions),
        "selection_candidate_count": len(selection_candidates),
        "spec_count": 1,
        "top": [row],
        "target_all_pass_count": int(target_all_pass),
        "target_rolling_12m_stability_pass_count": int(
            rolling_stability_pass
        ),
        "evidence_complete": evidence_complete,
    }
    return sweep, selection_receipt


def _single_fixed_spec_row(sweep: Mapping[str, Any]) -> Mapping[str, Any]:
    top = sweep.get("top")
    if (
        not isinstance(top, list)
        or len(top) != 1
        or not isinstance(top[0], Mapping)
    ):
        raise AuditedPITDevelopmentReplayError(
            "fixed trend pullback sweep must return exactly one result row"
        )
    return top[0]


def run_audited_pit_trend_pullback(
    *,
    settings: Settings,
    audited_pit_universe_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    temporal_contract_path: str | Path,
    expected_temporal_contract_sha256: str,
    start_date: str,
    end_date: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    if not isinstance(settings, Settings):
        raise AuditedPITDevelopmentReplayError(
            "trend pullback replay requires frozen settings"
        )
    contract = load_temporal_partition_contract(temporal_contract_path)
    if contract["contract_sha256"] != expected_temporal_contract_sha256:
        raise AuditedPITDevelopmentReplayError("temporal contract hash mismatch")
    assert_range_allowed(contract, "development", start_date, end_date, "backtest")

    try:
        universe = AuditedPointInTimeUniverse.from_file(
            str(audited_pit_universe_path),
            expected_coverage_audit_sha256=expected_coverage_audit_sha256,
            expected_artifact_root_sha256=expected_artifact_root_sha256,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            expected_temporal_role="development",
        )
    except (OSError, TypeError, ValueError, PITReceiptError) as exc:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact verification failed"
        ) from exc
    try:
        if universe.start_date != start_date or universe.end_date != end_date:
            raise AuditedPITDevelopmentReplayError(
                "audited PIT artifact range differs from frozen development range"
            )
        sessions = _exact_membership_sessions(
            universe,
            start_date=start_date,
            end_date=end_date,
        )
        bars = _load_exact_membership_bars(
            universe._require_open(),
            start_date=start_date,
            end_date=end_date,
        )
        suspension_evidence = _load_suspension_evidence(
            universe._require_open(),
            start_date=start_date,
            end_date=end_date,
        )
        adapter = ArtifactNativeReplayAdapter(
            universe,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            expected_temporal_role="development",
        )
        (
            pullback,
            baseline,
            pullback_censored,
            baseline_censored,
            candidate_receipt,
        ) = _build_strict_candidates(
            bars=bars,
            adapter=adapter,
            sessions=sessions,
            suspension_evidence=suspension_evidence,
        )
        source = {
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "artifact_root_sha256": universe.artifact_root_sha256,
            "temporal_contract_sha256": universe.temporal_contract_sha256,
            "temporal_role": universe.temporal_role,
            "market_session_count": len(sessions),
            "exact_membership_session_count": len(sessions),
            "market_scope": market_scope_contract(),
            "artifact_native_replay_contract_sha256": adapter.contract_sha256,
            "full_session_suspension_evidence_count": len(
                suspension_evidence
            ),
            "full_session_suspension_evidence_sha256": _sha256(
                [
                    {
                        "symbol": key[0],
                        "trade_date": key[1],
                        "proofs": value,
                    }
                    for key, value in sorted(suspension_evidence.items())
                ]
            ),
            "producer_code": _producer_binding(),
        }
    finally:
        universe.close()

    pullback_sweep, pullback_selection = _evaluate(
        pullback,
        pullback_censored,
        list(TREND_PULLBACK_SPEC["required_signal_tags"]),
        evaluation_session_dates=sessions,
    )
    baseline_sweep, baseline_selection = _evaluate(
        baseline,
        baseline_censored,
        ["breakout_20d"],
        evaluation_session_dates=sessions,
    )
    pullback_row = _single_fixed_spec_row(pullback_sweep)
    baseline_row = _single_fixed_spec_row(baseline_sweep)
    advancement_gate = bool(
        pullback_row.get("target_all_pass")
        and pullback_row.get("target_rolling_12m_stability_pass")
    )
    pullback_sidecar_payload = {
        "schema_version": "audited-pit-strict-candidate-sidecar/v1",
        "family": "trend_pullback_ma20_reclaim",
        "strategy_sha256": _sha256(TREND_PULLBACK_SPEC),
        "source": source,
        "completed_candidates": pullback,
        "right_censored_positions": pullback_censored,
        "selection_receipt": pullback_selection,
    }
    pullback_sidecar = _write_content_addressed(
        Path(output_dir) / "sidecars",
        pullback_sidecar_payload,
    )
    baseline_sidecar_payload = {
        "schema_version": "audited-pit-strict-candidate-sidecar/v1",
        "family": "breakout_20d",
        "strategy_sha256": _sha256(TREND_PULLBACK_SPEC),
        "source": source,
        "completed_candidates": baseline,
        "right_censored_positions": baseline_censored,
        "selection_receipt": baseline_selection,
    }
    baseline_sidecar = _write_content_addressed(
        Path(output_dir) / "sidecars",
        baseline_sidecar_payload,
    )
    payload = {
        "schema_version": "audited-pit-trend-pullback-result/v1",
        "strategy": {
            **TREND_PULLBACK_SPEC,
            "strategy_sha256": _sha256(TREND_PULLBACK_SPEC),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "final_oos_consumed": False,
            "advancement_gate_passed": advancement_gate,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "candidate_receipt": candidate_receipt,
        "pullback_candidate_count": len(pullback),
        "pullback_candidates_sha256": _sha256(pullback),
        "pullback_right_censored_position_count": len(
            pullback_censored
        ),
        "pullback_right_censored_positions_sha256": _sha256(
            pullback_censored
        ),
        "pullback_exit_paths_sha256": _sha256(_exit_paths(pullback)),
        "pullback_selection_receipt": pullback_selection,
        "pullback_candidate_sidecar": _stable_sidecar_reference(
            pullback_sidecar
        ),
        "pullback_sweep": pullback_sweep,
        "strict_breakout_baseline_candidate_count": len(baseline),
        "strict_breakout_baseline_candidates_sha256": _sha256(baseline),
        "strict_breakout_baseline_right_censored_position_count": len(
            baseline_censored
        ),
        "strict_breakout_baseline_right_censored_positions_sha256": _sha256(
            baseline_censored
        ),
        "strict_breakout_baseline_exit_paths_sha256": _sha256(
            _exit_paths(baseline)
        ),
        "strict_breakout_baseline_selection_receipt": baseline_selection,
        "strict_breakout_baseline_candidate_sidecar": (
            _stable_sidecar_reference(baseline_sidecar)
        ),
        "strict_breakout_baseline_sweep": baseline_sweep,
        "advancement_gate": {
            "latest_and_quality_targets_passed": bool(
                pullback_row.get("target_all_pass")
            ),
            "rolling_12m_stability_passed": bool(
                pullback_row.get("target_rolling_12m_stability_pass")
            ),
            "all_required_gates_passed": advancement_gate,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
        "comparison": {
            "pullback_selected_trade_count": pullback_row.get(
                "selected_trade_count"
            ),
            "baseline_selected_trade_count": baseline_row.get(
                "selected_trade_count"
            ),
            "same_execution_contract": True,
        },
    }
    artifact = _write_content_addressed(output_dir, payload)
    return {
        **payload,
        "artifact": artifact,
        "runtime_sidecars": {
            "pullback": pullback_sidecar,
            "strict_breakout_baseline": baseline_sidecar,
        },
    }
