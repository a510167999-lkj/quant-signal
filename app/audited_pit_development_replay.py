"""Frozen simple-rule replay over a bound audited PIT universe artifact."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.a_share_universe import _is_excluded_name
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _candidate_trades_from_bars,
    _sha256,
    _write_content_addressed,
)
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptError
from app.research_scope import is_mainboard_chinext_symbol, market_scope_contract
from app.research_sweep import sweep_qualified_trades


class AuditedPITDevelopmentReplayError(ValueError):
    """The audited PIT development replay inputs are incomplete or inconsistent."""


BREADTH_MA20_BREAKOUT_SPEC = {
    **SIMPLE_BREAKOUT_SPEC,
    "schema_version": "development-breakout-breadth-ma20/v1",
    "required_signal_tags": ["breakout_20d", "breadth_ma20_gte_50"],
    "market_filter": {
        "tag": "breadth_ma20_gte_50",
        "threshold_pct": 50.0,
        "comparison": "gte",
        "price_basis": "causal_adjusted_close",
        "moving_average_sessions": 20,
        "eligible_denominator": "exact_signal_date_mainboard_chinext_non_risk_members",
        "missing_market_rows": "included_in_denominator_as_not_above_ma20",
    },
}


MODERATE_AMOUNT_BREAKOUT_SPEC = {
    **SIMPLE_BREAKOUT_SPEC,
    "schema_version": "development-breakout-moderate-amount/v1",
    "required_signal_tags": ["breakout_20d", "amount_ratio_gte_1_lt_2"],
    "signal_filter": {
        "tag": "amount_ratio_gte_1_lt_2",
        "numerator": "signal_date_amount",
        "denominator": "prior_20_observed_bars_amount_median",
        "minimum_inclusive": 1.0,
        "maximum_exclusive": 2.0,
        "missing_values": "reject",
    },
}


def _producer_code_binding() -> dict[str, Any]:
    module_names = (
        "a_share_universe.py",
        "audited_pit_development_replay.py",
        "current_pool_development_replay.py",
        "execution.py",
        "research_equity.py",
        "research_partitions.py",
        "research_pit_store.py",
        "research_portfolio.py",
        "research_scope.py",
        "research_sweep.py",
    )
    root = Path(__file__).resolve().parent
    refs = [
        {
            "module": name,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
        for name in module_names
    ]
    return {
        "schema_version": "audited-pit-development-producer-code/v1",
        "modules": refs,
        "root_sha256": _sha256(refs),
    }


def _exact_membership_sessions(
    universe: AuditedPointInTimeUniverse,
    *,
    start_date: str,
    end_date: str,
) -> list[str]:
    connection = universe._require_open()
    sessions = universe.open_sessions(start_date, end_date)
    exact = {
        str(row["partition_key"])
        for row in connection.execute(
            """
            SELECT partition_key FROM receipts
            WHERE dataset = 'bak_basic' AND partition_key BETWEEN ? AND ?
            """,
            (start_date, end_date),
        )
    }
    if exact != set(sessions):
        raise AuditedPITDevelopmentReplayError(
            "every development session requires an exact bak_basic receipt"
        )
    derived = connection.execute(
        """
        SELECT 1 FROM membership_session_head
        WHERE trade_date BETWEEN ? AND ? LIMIT 1
        """,
        (start_date, end_date),
    ).fetchone()
    if derived is not None:
        raise AuditedPITDevelopmentReplayError(
            "derived or quarantined membership is forbidden for this replay"
        )

    counts = {
        str(row["trade_date"]): int(row["row_count"])
        for row in connection.execute(
            """
            SELECT trade_date, COUNT(*) AS row_count
            FROM daily_universe
            WHERE trade_date BETWEEN ? AND ?
            GROUP BY trade_date
            ORDER BY trade_date
            """,
            (start_date, end_date),
        )
    }
    if set(counts) != set(sessions) or any(counts[session] <= 0 for session in sessions):
        raise AuditedPITDevelopmentReplayError(
            "exact membership rows are incomplete for an open session"
        )
    return sessions


def _load_exact_membership_bars(
    connection: Any,
    *,
    start_date: str,
    end_date: str,
) -> Any:
    import pandas as pd

    frame = pd.read_sql_query(
        """
        SELECT daily.trade_date AS date, daily.ts_code, daily.open, daily.high,
               daily.low, daily.close, daily.pre_close, daily.amount,
               adjustment.adj_factor, membership.name AS membership_name,
               EXISTS(
                   SELECT 1
                   FROM market_session_generation_rows_suspend_d AS suspension
                   WHERE suspension.generation_id = daily.generation_id
                     AND suspension.ts_code = daily.ts_code
                     AND suspension.trade_date = daily.trade_date
                     AND suspension.suspend_type = 'S'
               ) AS suspended
        FROM market_session_generation_rows_daily AS daily
        JOIN market_session_generation_head AS head
          ON head.trade_date = daily.trade_date
         AND head.generation_id = daily.generation_id
        JOIN market_session_generation_rows_adj_factor AS adjustment
          ON adjustment.generation_id = daily.generation_id
         AND adjustment.trade_date = daily.trade_date
         AND adjustment.ts_code = daily.ts_code
        LEFT JOIN daily_universe AS membership
          ON membership.trade_date = daily.trade_date
         AND membership.ts_code = daily.ts_code
        WHERE daily.trade_date BETWEEN ? AND ?
        ORDER BY daily.ts_code, daily.trade_date
        """,
        connection,
        params=(start_date, end_date),
    )
    if frame.empty:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact has no exact-membership market bars"
        )
    frame = frame[frame["ts_code"].map(is_mainboard_chinext_symbol)].copy()
    numeric = ["open", "high", "low", "close", "pre_close", "amount", "adj_factor"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=["open", "high", "low", "close", "adj_factor"]
    )
    frame = frame[
        (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["adj_factor"] > 0)
    ]
    if frame.empty:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact has no eligible exact-membership bars"
        )
    return frame


def _apply_breadth_ma20_filter(
    connection: Any,
    bars: Any,
    *,
    start_date: str,
    end_date: str,
) -> tuple[Any, dict[str, Any]]:
    import pandas as pd

    def eligible_member(ts_code: Any, name: Any) -> int:
        return int(
            is_mainboard_chinext_symbol(ts_code)
            and bool(str(name or "").strip())
            and not _is_excluded_name(str(name or ""))
        )

    connection.create_function(
        "pit_is_eligible_member",
        2,
        eligible_member,
        deterministic=True,
    )
    eligible_counts = {
        str(row["trade_date"]): int(row["eligible_count"])
        for row in connection.execute(
            """
            SELECT trade_date, COUNT(*) AS eligible_count
            FROM daily_universe
            WHERE trade_date BETWEEN ? AND ?
              AND pit_is_eligible_member(ts_code, name) = 1
            GROUP BY trade_date
            ORDER BY trade_date
            """,
            (start_date, end_date),
        )
    }
    market_sessions = {
        str(row["trade_date"])
        for row in connection.execute(
            """
            SELECT trade_date FROM market_session_generation_head
            WHERE trade_date BETWEEN ? AND ?
            """,
            (start_date, end_date),
        )
    }
    if (
        not eligible_counts
        or set(eligible_counts) != market_sessions
        or any(count <= 0 for count in eligible_counts.values())
    ):
        raise AuditedPITDevelopmentReplayError("breadth eligible membership is incomplete")

    frame = bars.copy()
    signal_close = frame["close"] * frame["adj_factor"]
    ma20 = signal_close.groupby(frame["ts_code"], sort=False).transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    observed_eligible = frame["membership_name"].map(
        lambda value: (
            False
            if pd.isna(value)
            else bool(str(value or "").strip())
            and not _is_excluded_name(str(value or ""))
        )
    )
    above_ma20 = observed_eligible & ma20.notna() & (signal_close >= ma20)
    numerators = (
        pd.DataFrame({"date": frame["date"], "above_ma20": above_ma20.astype(int)})
        .groupby("date", sort=True)["above_ma20"]
        .sum()
        .to_dict()
    )
    breadth_by_date = {
        session: round(float(numerators.get(session, 0)) / count * 100.0, 2)
        for session, count in eligible_counts.items()
    }
    threshold = float(BREADTH_MA20_BREAKOUT_SPEC["market_filter"]["threshold_pct"])
    frame["breadth_ma20_gte_50"] = frame["date"].map(
        lambda value: breadth_by_date.get(str(value), -1.0) >= threshold
    )
    values = pd.Series(list(breadth_by_date.values()), dtype=float)
    return frame, {
        "schema_version": "audited-pit-breadth-ma20-context/v1",
        "tag": "breadth_ma20_gte_50",
        "threshold_pct": threshold,
        "session_count": len(breadth_by_date),
        "pass_session_count": sum(value >= threshold for value in breadth_by_date.values()),
        "minimum_pct": round(float(values.min()), 2),
        "median_pct": round(float(values.median()), 2),
        "maximum_pct": round(float(values.max()), 2),
        "breadth_by_date_sha256": _sha256(breadth_by_date),
        "eligible_denominator": (
            "exact_signal_date_mainboard_chinext_non_risk_members"
        ),
        "missing_market_rows": "included_in_denominator_as_not_above_ma20",
        "price_basis": "raw_close_times_session_adj_factor",
        "moving_average_sessions": 20,
    }


def _apply_moderate_amount_filter(
    _connection: Any,
    bars: Any,
    *,
    start_date: str,
    end_date: str,
) -> tuple[Any, dict[str, Any]]:
    import pandas as pd

    frame = bars.copy()
    prior20_median = frame.groupby("ts_code", sort=False)["amount"].transform(
        lambda values: values.shift(1).rolling(20, min_periods=20).median()
    )
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    ratio = amount / prior20_median
    minimum = float(
        MODERATE_AMOUNT_BREAKOUT_SPEC["signal_filter"]["minimum_inclusive"]
    )
    maximum = float(
        MODERATE_AMOUNT_BREAKOUT_SPEC["signal_filter"]["maximum_exclusive"]
    )
    frame["amount_ratio_gte_1_lt_2"] = (
        ratio.notna()
        & (prior20_median > 0)
        & (ratio >= minimum)
        & (ratio < maximum)
    )
    valid = ratio[ratio.notna() & (prior20_median > 0)]
    passing = frame["amount_ratio_gte_1_lt_2"]
    values_digest = hashlib.sha256()
    for date, ts_code, pass_value in zip(
        frame["date"],
        frame["ts_code"],
        passing,
    ):
        values_digest.update(
            f"{date}\0{ts_code}\0{int(bool(pass_value))}\n".encode("utf-8")
        )
    return frame, {
        "schema_version": "audited-pit-moderate-amount-context/v1",
        "tag": "amount_ratio_gte_1_lt_2",
        "start_date": start_date,
        "end_date": end_date,
        "lookback_observed_bars": 20,
        "denominator_statistic": "median",
        "minimum_inclusive": minimum,
        "maximum_exclusive": maximum,
        "valid_bar_count": int(valid.size),
        "pass_bar_count": int(passing.sum()),
        "minimum_ratio": round(float(valid.min()), 4) if not valid.empty else None,
        "median_ratio": round(float(valid.median()), 4) if not valid.empty else None,
        "maximum_ratio": round(float(valid.max()), 4) if not valid.empty else None,
        "filter_values_sha256": values_digest.hexdigest(),
    }


def run_audited_pit_development_replay(
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
    _strategy_spec: Mapping[str, Any] | None = None,
    _prepare_bars: Callable[..., tuple[Any, dict[str, Any]]] | None = None,
    _required_signal_column: str | None = None,
    _result_schema_version: str = "audited-pit-development-replay-result/v1",
) -> dict[str, Any]:
    strategy_spec = dict(_strategy_spec or SIMPLE_BREAKOUT_SPEC)
    required_signal_tags = list(
        strategy_spec.get("required_signal_tags") or [strategy_spec["signal_tag"]]
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
        raise AuditedPITDevelopmentReplayError("audited PIT artifact verification failed") from exc
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
        preparation_source = {}
        if _prepare_bars is not None:
            bars, preparation_source = _prepare_bars(
                universe._require_open(),
                bars,
                start_date=start_date,
                end_date=end_date,
            )
        trades = _candidate_trades_from_bars(
            bars,
            {},
            settings,
            membership_name_column="membership_name",
            required_signal_column=_required_signal_column,
            signal_tags=required_signal_tags,
            current_universe_bias=False,
        )
        source = {
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "artifact_root_sha256": universe.artifact_root_sha256,
            "temporal_contract_sha256": universe.temporal_contract_sha256,
            "temporal_role": universe.temporal_role,
            "market_session_count": len(sessions),
            "exact_membership_session_count": len(sessions),
            "market_scope": market_scope_contract(),
            "producer_code": _producer_code_binding(),
        }
        if preparation_source:
            source["signal_context"] = preparation_source
    finally:
        universe.close()

    sweep = sweep_qualified_trades(
        trades,
        hold_days=strategy_spec["hold_days"],
        top_n=strategy_spec["top_n"],
        max_active_positions=strategy_spec["max_active_positions"],
        min_trades=20,
        target_win_rate_pct=52.0,
        target_drawdown_pct=15.0,
        target_one_year_return_pct=50.0,
        target_profit_factor=1.3,
        target_calmar=1.5,
        exposure_multipliers=[strategy_spec["exposure_multiplier"]],
        annual_financing_rate_pct=strategy_spec["annual_financing_rate_pct"],
        roundtrip_cost_bps=strategy_spec["roundtrip_cost_bps"],
        slippage_bps=strategy_spec["slippage_bps"],
        capital_model=strategy_spec["capital_model"],
        required_signal_tags=required_signal_tags,
        fixed_spec=True,
    )
    payload = {
        "schema_version": _result_schema_version,
        "strategy": {
            **strategy_spec,
            "strategy_sha256": _sha256(strategy_spec),
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "current_universe_bias": False,
            "exact_membership_required": True,
            "development_only": True,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "sweep": sweep,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}


def run_audited_pit_breadth_development_replay(**kwargs: Any) -> dict[str, Any]:
    return run_audited_pit_development_replay(
        **kwargs,
        _strategy_spec=BREADTH_MA20_BREAKOUT_SPEC,
        _prepare_bars=_apply_breadth_ma20_filter,
        _required_signal_column="breadth_ma20_gte_50",
        _result_schema_version="audited-pit-breadth-development-replay-result/v1",
    )


def run_audited_pit_moderate_amount_development_replay(
    **kwargs: Any,
) -> dict[str, Any]:
    return run_audited_pit_development_replay(
        **kwargs,
        _strategy_spec=MODERATE_AMOUNT_BREAKOUT_SPEC,
        _prepare_bars=_apply_moderate_amount_filter,
        _required_signal_column="amount_ratio_gte_1_lt_2",
        _result_schema_version=(
            "audited-pit-moderate-amount-development-replay-result/v1"
        ),
    )
