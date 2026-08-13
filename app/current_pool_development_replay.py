"""Development-only replay for the frozen current-pool v2 evidence.

This deliberately does *not* claim historical constituent membership.  The
universe is the audited current pool, so every result is explicitly
current-universe-biased and cannot be used to register or unlock a production
strategy profile.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from app.a_share_universe import _is_excluded_name
from app.config import Settings
from app.current_pool_gate import verify_current_pool_audit
from app.current_pool_history_source import verify_current_pool_history_descriptor
from app.current_pool_source import _strict_json_loads, verify_current_pool_universe_descriptor
from app.execution import assess_entry_executability
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_portfolio import _realized_trade_from_future
from app.research_pit_store import (
    PITReceiptError,
    PITReceiptStore,
)
from app.research_sweep import sweep_qualified_trades


class CurrentPoolDevelopmentReplayError(ValueError):
    """The development-only current-pool replay inputs are not trustworthy."""


SIMPLE_BREAKOUT_SPEC = {
    "schema_version": "development-simple-breakout/v4",
    "signal_tag": "breakout_20d",
    "signal_price_basis": "raw_ohlc_times_session_adj_factor",
    "adjustment_method": "causal_bar_factor_common_as_of_denominator_cancels",
    "membership_application": "signal_date_only",
    "lookback_sessions": 20,
    "hold_days": 5,
    "stop_loss_pct": 5.0,
    "exposure_multiplier": 1.0,
    "top_n": 3,
    "max_active_positions": 3,
    "capital_model": "slot-daily",
    "roundtrip_cost_bps": 25.0,
    "slippage_bps": 10.0,
    "annual_financing_rate_pct": 8.0,
    "entry_execution": {
        "decision_cutoff": "next_open",
        "max_gap_up_pct": 6.0,
        "max_gap_down_pct": 7.0,
        "locked_limit_gap_pct": 9.3,
        "max_intraday_range_pct": 8.0,
    },
    "forbidden_overlays": [
        "correlation_budget",
        "proxy_filter",
        "profit_lock",
        "prior_high_protection",
        "calendar_gap_exit",
    ],
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        payload = _strict_json_loads(Path(path).read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise CurrentPoolDevelopmentReplayError("development replay input is unreadable") from exc
    if not isinstance(payload, dict):
        raise CurrentPoolDevelopmentReplayError("development replay input is invalid")
    return payload


def _verify_market_refs(
    store: PITReceiptStore,
    connection: sqlite3.Connection,
    history_payload: Mapping[str, Any],
    contract_sha256: str,
) -> list[dict[str, str]]:
    refs = history_payload.get("market_generation_refs")
    if not isinstance(refs, list) or not refs:
        raise CurrentPoolDevelopmentReplayError("history summary has no market references")
    expected = [
        {
            "trade_date": str(ref["trade_date"]),
            "generation_id": str(ref["generation_id"]),
            "manifest_sha256": str(ref["manifest_sha256"]),
            "lineage_sha256": str(ref["lineage_sha256"]),
            "vintage": str(ref["vintage"]),
        }
        for ref in refs
    ]
    rows = connection.execute(
        """
        SELECT head.trade_date, head.generation_id, generation.manifest_sha256,
               generation.lineage_sha256, generation.vintage, generation.status
        FROM market_session_generation_head AS head
        JOIN market_session_generations AS generation
          ON generation.generation_id = head.generation_id
        ORDER BY head.trade_date
        """
    ).fetchall()
    observed = [
        {
            "trade_date": str(row["trade_date"]),
            "generation_id": str(row["generation_id"]),
            "manifest_sha256": str(row["manifest_sha256"]),
            "lineage_sha256": str(row["lineage_sha256"]),
            "vintage": str(row["vintage"]),
        }
        for row in rows
    ]
    if observed != expected or any(row["status"] != "published" for row in rows):
        raise CurrentPoolDevelopmentReplayError("market generations do not match history evidence")
    try:
        sessions = store._current_pool_calendar_authority_on_connection(
            connection,
            start_date=str(history_payload["history_start"]),
            end_date=str(history_payload["history_end"]),
            temporal_contract_sha256=contract_sha256,
            temporal_role="development",
            source_profile="jiaoch",
        )
        if sessions != [ref["trade_date"] for ref in expected]:
            raise CurrentPoolDevelopmentReplayError(
                "market references differ from verified calendar"
            )
        for ref in expected:
            generation, manifest, source, role, temporal_contract = (
                store._membership_market_authority_on_connection(
                    connection, ref["trade_date"]
                )
            )
            stored_lineage = generation.get("lineage_sha256")
            if not stored_lineage or not hmac.compare_digest(
                str(stored_lineage), str(manifest["lineage_sha256"])
            ):
                raise CurrentPoolDevelopmentReplayError(
                    "market generation lineage verification failed"
                )
            verified_ref = {
                "trade_date": str(generation["trade_date"]),
                "generation_id": str(generation["generation_id"]),
                "manifest_sha256": str(manifest["manifest_sha256"]),
                "lineage_sha256": str(stored_lineage),
                "vintage": str(generation["vintage"]),
            }
            if (
                verified_ref != ref
                or role != "development"
                or temporal_contract != contract_sha256
            ):
                raise CurrentPoolDevelopmentReplayError(
                    "market attempt authority differs from frozen evidence"
                )
            store._assert_membership_source_profile(source, "jiaoch")
    except PITReceiptError as exc:
        raise CurrentPoolDevelopmentReplayError(
            "market attempt authority verification failed"
        ) from exc
    binding = connection.execute(
        """
        SELECT temporal_contract_sha256, temporal_role, source_profile,
               market_generation_root_sha256, market_session_count, binding_sha256
        FROM current_pool_market_collection_bindings
        WHERE start_date = ? AND end_date = ?
        """,
        (history_payload["history_start"], history_payload["history_end"]),
    ).fetchone()
    if binding is None:
        raise CurrentPoolDevelopmentReplayError("market evidence lacks frozen temporal binding")
    unsigned_binding = {
        "schema_version": "current-pool-market-temporal-binding/v1",
        "start_date": str(history_payload["history_start"]),
        "end_date": str(history_payload["history_end"]),
        "temporal_contract_sha256": str(binding["temporal_contract_sha256"]),
        "temporal_role": str(binding["temporal_role"]),
        "source_profile": str(binding["source_profile"]),
        "market_generation_root_sha256": str(
            binding["market_generation_root_sha256"]
        ),
        "market_session_count": int(binding["market_session_count"]),
    }
    if (
        binding["temporal_contract_sha256"] != contract_sha256
        or binding["temporal_role"] != "development"
        or binding["source_profile"] != "jiaoch"
        or binding["market_generation_root_sha256"] != _sha256(expected)
        or int(binding["market_session_count"]) != len(expected)
        or not hmac.compare_digest(
            str(binding["binding_sha256"]), _sha256(unsigned_binding)
        )
    ):
        raise CurrentPoolDevelopmentReplayError("market evidence lacks frozen temporal binding")
    shard_counts = connection.execute(
        """
        SELECT generation_id, COUNT(*) AS count
        FROM market_session_generation_shards
        GROUP BY generation_id
        """
    ).fetchall()
    if {str(row["generation_id"]): int(row["count"]) for row in shard_counts} != {
        ref["generation_id"]: 4 for ref in expected
    }:
        raise CurrentPoolDevelopmentReplayError("market generation shards are incomplete")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise CurrentPoolDevelopmentReplayError("market store quick_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise CurrentPoolDevelopmentReplayError("market store foreign keys are invalid")
    return expected


def _load_bars(
    connection: sqlite3.Connection,
    eligible_symbols: set[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    rows = connection.execute(
        """
        SELECT daily.trade_date AS date, daily.ts_code, daily.open, daily.high, daily.low,
               daily.close, daily.pre_close, daily.amount, adjustment.adj_factor,
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
        WHERE daily.trade_date BETWEEN ? AND ?
        ORDER BY daily.ts_code, daily.trade_date
        """,
        (start_date, end_date),
    ).fetchall()
    frame = pd.DataFrame.from_records(rows, columns=[
        "date", "ts_code", "open", "high", "low", "close", "pre_close", "amount",
        "adj_factor", "suspended",
    ])
    if frame.empty:
        raise CurrentPoolDevelopmentReplayError("market evidence has no daily bars")
    frame = frame[frame["ts_code"].str[:6].isin(eligible_symbols)].copy()
    numeric = ["open", "high", "low", "close", "pre_close", "amount", "adj_factor"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "adj_factor"])
    frame = frame[(frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0) & (frame["adj_factor"] > 0)]
    if frame.empty:
        raise CurrentPoolDevelopmentReplayError("eligible current pool has no complete bars")
    return frame


def _candidate_trades_from_bars(
    bars: pd.DataFrame,
    names_by_symbol: Mapping[str, str],
    settings: Settings,
    *,
    membership_by_date: Mapping[str, Mapping[str, str]] | None = None,
    membership_name_column: str | None = None,
    required_signal_column: str | None = None,
    signal_tags: Sequence[str] = ("breakout_20d",),
    current_universe_bias: bool = True,
) -> list[dict[str, Any]]:
    """Build the one frozen simple-rule candidate set from verified raw bars."""

    normalized_signal_tags = tuple(str(tag).strip() for tag in signal_tags if str(tag).strip())
    if not normalized_signal_tags:
        raise CurrentPoolDevelopmentReplayError("signal tags cannot be empty")
    if required_signal_column is not None and required_signal_column not in bars:
        raise CurrentPoolDevelopmentReplayError("required signal column is missing")
    trades: list[dict[str, Any]] = []
    for ts_code, group in bars.groupby("ts_code", sort=True):
        frame = group.sort_values("date", kind="mergesort").reset_index(drop=True).copy()
        adjusted_high = frame["high"] * frame["adj_factor"]
        adjusted_close = frame["close"] * frame["adj_factor"]
        breakout = adjusted_close > adjusted_high.shift(1).rolling(
            SIMPLE_BREAKOUT_SPEC["lookback_sessions"], min_periods=SIMPLE_BREAKOUT_SPEC["lookback_sessions"]
        ).max()
        for index in breakout[breakout].index:
            if required_signal_column is not None:
                filter_value = frame.at[index, required_signal_column]
                if pd.isna(filter_value) or not bool(filter_value):
                    continue
            signal_date = str(frame.at[index, "date"])
            symbol = str(ts_code)[:6]
            if membership_by_date is not None:
                name = membership_by_date.get(signal_date, {}).get(symbol)
                if name is None or _is_excluded_name(str(name)):
                    continue
            elif membership_name_column is not None:
                raw_name = frame.at[index, membership_name_column]
                if pd.isna(raw_name):
                    continue
                name = str(raw_name or "").strip()
                if not name or _is_excluded_name(name):
                    continue
            else:
                name = names_by_symbol.get(symbol, "")
                if not name or _is_excluded_name(str(name)):
                    continue
            entry_index = index + 1
            exit_index = entry_index + SIMPLE_BREAKOUT_SPEC["hold_days"]
            if exit_index >= len(frame) or bool(frame.at[entry_index, "suspended"]):
                continue
            execution = SIMPLE_BREAKOUT_SPEC["entry_execution"]
            executable = assess_entry_executability(
                frame.iloc[index].to_dict(),
                frame.iloc[entry_index].to_dict(),
                max_gap_up_pct=execution["max_gap_up_pct"],
                max_gap_down_pct=execution["max_gap_down_pct"],
                locked_limit_gap_pct=execution["locked_limit_gap_pct"],
                max_intraday_range_pct=execution["max_intraday_range_pct"],
                decision_cutoff=execution["decision_cutoff"],
            )
            if not executable["executable"]:
                continue
            realized = _realized_trade_from_future(
                frame,
                entry_index,
                exit_index,
                stop_loss_pct=SIMPLE_BREAKOUT_SPEC["stop_loss_pct"],
            )
            trades.append(
                {
                    **realized,
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "name": name,
                    "action": "BUY",
                    "score": 1.0,
                    "rank_score": float(frame.at[index, "amount"] or 0.0),
                    "candidate_amount": float(frame.at[index, "amount"] or 0.0),
                    "market_level": (
                        "current_pool_development_only"
                        if current_universe_bias
                        else "audited_pit_development"
                    ),
                    "signal_tags": list(normalized_signal_tags),
                    "entry_executability": executable,
                    "current_universe_bias": current_universe_bias,
                }
            )
    return sorted(trades, key=lambda item: (item["signal_date"], item["symbol"]))


def _write_content_addressed(output_dir: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(payload)
    digest = _sha256(unsigned)
    body = {**unsigned, "artifact_sha256": digest}
    destination = Path(output_dir) / f"{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if destination.exists() and destination.read_text(encoding="utf-8") != content:
        raise CurrentPoolDevelopmentReplayError("content-addressed result conflicts")
    if not destination.exists():
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            try:
                os.link(temp_name, destination)
            except FileExistsError:
                if destination.read_text(encoding="utf-8") != content:
                    raise CurrentPoolDevelopmentReplayError(
                        "content-addressed result conflicts"
                    )
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return {"path": str(destination), "artifact_sha256": digest}


def run_current_pool_development_replay(
    *,
    settings: Settings,
    store_dir: str | Path,
    universe_path: str | Path,
    history_summary_path: str | Path,
    temporal_contract_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run the immutable simple rule and write a non-promotable result artifact."""

    contract = load_temporal_partition_contract(temporal_contract_path)
    assert_range_allowed(contract, "development", "2024-07-05", "2026-07-03", "backtest")
    evidence = contract["development_evidence"]["current_pool_coverage_audit"]
    verified_audit = verify_current_pool_audit(evidence["path"])
    if (
        verified_audit["canonical_sha256"] != evidence["canonical_sha256"]
        or verified_audit["source_as_of"] != evidence["source_as_of"]
    ):
        raise CurrentPoolDevelopmentReplayError("frozen current-pool audit does not match")

    universe_payload = _load_json(universe_path)
    verified_universe = verify_current_pool_universe_descriptor(universe_payload)
    history_payload = _load_json(history_summary_path)
    verified_history = verify_current_pool_history_descriptor(history_payload, universe_payload)
    if (
        history_payload.get("history_start") != contract["development_evidence"]["history_start"]
        or history_payload.get("history_end") != contract["development_evidence"]["history_end"]
    ):
        raise CurrentPoolDevelopmentReplayError("history summary range differs from frozen development range")

    names_by_symbol = {
        str(item["ts_code"])[:6]: str(item["name"])
        for item in universe_payload["items"]
        if str(item["ts_code"])[:6] in verified_audit["allowed_symbols"]
    }
    if set(names_by_symbol) != set(verified_audit["allowed_symbols"]):
        raise CurrentPoolDevelopmentReplayError("audit and universe symbols differ")

    try:
        with PITReceiptStore.readonly_snapshot(store_dir) as (store, connection):
            refs = _verify_market_refs(
                store, connection, history_payload, contract["contract_sha256"]
            )
            bars = _load_bars(
                connection,
                set(names_by_symbol),
                history_payload["history_start"],
                history_payload["history_end"],
            )
    except (PITReceiptError, sqlite3.Error) as exc:
        raise CurrentPoolDevelopmentReplayError(
            "current-pool market store verification failed"
        ) from exc
    trades = _candidate_trades_from_bars(bars, names_by_symbol, settings)
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
        "schema_version": "current-pool-development-replay-result/v1",
        "strategy": {**SIMPLE_BREAKOUT_SPEC, "strategy_sha256": _sha256(SIMPLE_BREAKOUT_SPEC)},
        "source": {
            "current_pool_coverage_audit_sha256": verified_audit["canonical_sha256"],
            "universe_descriptor_sha256": verified_universe["descriptor_sha256"],
            "history_summary_sha256": verified_history["descriptor_sha256"],
            "market_generation_root_sha256": history_payload["market_generation_root_sha256"],
            "market_session_count": len(refs),
            "temporal_contract_sha256": contract["contract_sha256"],
            "temporal_role": "development",
        },
        "scope": {
            "current_universe_bias": True,
            "development_only": True,
            "replay_eligible": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "qualified_trade_count": len(trades),
        "qualified_trades_sha256": _sha256(trades),
        "sweep": sweep,
    }
    return {**payload, "artifact": _write_content_addressed(output_dir, payload)}
