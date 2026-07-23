"""Frozen simple-rule replay over a bound audited PIT universe artifact."""

from __future__ import annotations

import hashlib
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
        JOIN daily_universe AS membership
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
    frame = frame[
        frame["ts_code"].map(is_mainboard_chinext_symbol)
        & ~frame["membership_name"].map(lambda value: _is_excluded_name(str(value or "")))
    ].copy()
    numeric = ["open", "high", "low", "close", "pre_close", "amount", "adj_factor"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=["open", "high", "low", "close", "adj_factor", "membership_name"]
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
) -> dict[str, Any]:
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
        trades = _candidate_trades_from_bars(
            bars,
            {},
            settings,
            membership_name_column="membership_name",
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
    finally:
        universe.close()

    sweep = sweep_qualified_trades(
        trades,
        hold_days=SIMPLE_BREAKOUT_SPEC["hold_days"],
        top_n=SIMPLE_BREAKOUT_SPEC["top_n"],
        max_active_positions=SIMPLE_BREAKOUT_SPEC["max_active_positions"],
        min_trades=20,
        target_win_rate_pct=52.0,
        target_drawdown_pct=15.0,
        target_one_year_return_pct=50.0,
        target_profit_factor=1.3,
        target_calmar=1.5,
        exposure_multipliers=[SIMPLE_BREAKOUT_SPEC["exposure_multiplier"]],
        annual_financing_rate_pct=SIMPLE_BREAKOUT_SPEC["annual_financing_rate_pct"],
        roundtrip_cost_bps=SIMPLE_BREAKOUT_SPEC["roundtrip_cost_bps"],
        slippage_bps=SIMPLE_BREAKOUT_SPEC["slippage_bps"],
        capital_model=SIMPLE_BREAKOUT_SPEC["capital_model"],
        required_signal_tags=[SIMPLE_BREAKOUT_SPEC["signal_tag"]],
        fixed_spec=True,
    )
    payload = {
        "schema_version": "audited-pit-development-replay-result/v1",
        "strategy": {
            **SIMPLE_BREAKOUT_SPEC,
            "strategy_sha256": _sha256(SIMPLE_BREAKOUT_SPEC),
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
