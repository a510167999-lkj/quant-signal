"""Strict audited-PIT continuous ridge out-of-fold research."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
from importlib.metadata import version as package_version
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

from app.a_share_universe import _is_excluded_name
from app.audited_pit_development_replay import (
    AuditedPITDevelopmentReplayError,
    _exact_membership_sessions,
    _producer_code_binding,
)
from app.audited_pit_industry_residual_reversal import (
    INDUSTRY_RESIDUAL_SPEC,
    _apply_uniform_tail_cutoff as _residual_apply_uniform_tail_cutoff,
    _frames_by_symbol as _residual_frames_by_symbol,
    _preflight_strict_entries as _residual_preflight_strict_entries,
)
from app.audited_pit_trend_pullback import (
    TREND_PULLBACK_SPEC,
    _load_suspension_evidence,
    _load_terminal_listing_evidence,
    _resolve_frame_date_positions,
    _stable_sidecar_reference,
    _strict_close_stop_trade,
)
from app.config import Settings
from app.current_pool_development_replay import (
    _sha256,
    _write_content_addressed,
)
from app.research_artifact_replay import ArtifactNativeReplayAdapter
from app.research_market_data import next_open_fill_gate
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_pit_store import (
    AuditedPointInTimeUniverse,
    PITReceiptError,
)
from app.research_scope import (
    is_mainboard_chinext_symbol,
    market_scope_contract,
)
from app.research_security_code_transition import (
    SecurityCodeTransitionEvidenceError,
    SecurityCodeTransitionReplayAdapter,
    apply_security_code_transition_contract,
    load_security_code_transition_evidence,
    remap_security_code_transition_suspension_evidence,
    remap_security_code_transition_terminal_evidence,
)
from app.research_sweep import _trade_metrics
from app.storage import write_json


RAW_STOCK_FEATURE_NAMES = (
    "amount_level_20",
    "amount_volatility_20",
    "amount_surge_5_to_60",
    "amihud_20",
    "realized_volatility_20_pct",
    "max_return_20_pct",
    "signal_return_1d_pct",
    "reversal_20_skip5_pct",
)
RANKED_STOCK_FEATURE_NAMES = tuple(
    f"{name}_rank" for name in RAW_STOCK_FEATURE_NAMES
)
MARKET_CONTEXT_FEATURE_NAMES = (
    "cross_section_above_ma20_fraction",
    "cross_section_median_return_5d_pct",
)
FEATURE_NAMES = (
    *RANKED_STOCK_FEATURE_NAMES,
    *MARKET_CONTEXT_FEATURE_NAMES,
)


CONTINUOUS_RIDGE_OOF_SPEC = {
    "schema_version": (
        "development-pit-cross-sectional-ranked-liquidity-ridge-oof/v2"
    ),
    "signal_tag": "cross_sectional_ranked_liquidity_ridge_oof",
    "market_scope": {
        **market_scope_contract(),
        "allowed_boards": [
            "shanghai_main",
            "shenzhen_main",
            "chinext",
        ],
    },
    "raw_stock_features": list(RAW_STOCK_FEATURE_NAMES),
    "features": list(FEATURE_NAMES),
    "cross_section_transform": {
        "method": "deterministic_midrank",
        "mapping": "2*midrank/(N+1)-1",
        "tie_policy": "equal_raw_values_share_average_rank",
        "missing_policy": "reject",
    },
    "feature_history_sessions": 61,
    "required_market_session_count": 483,
    "required_oof_fold_count": 6,
    "minimum_cross_section_members": 1_000,
    "label": {
        "target": "gross_return_pct_minus_0.45",
        "friction_percentage_points": 0.45,
        "clipping": False,
    },
    "model": {
        "type": "weighted_continuous_linear_ridge",
        "ridge_lambda": 1.0,
        "loss": "squared_error",
        "hyperparameter_search": False,
        "standardization": "training_fold_only",
        "intercept_penalized": False,
        "signal_date_total_weight": 1.0,
        "interactions": False,
    },
    "walk_forward": {
        "minimum_training_sessions": 126,
        "validation_sessions": 63,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "folds": "continuous_non_overlapping_validation_windows",
    },
    "selection": {
        "minimum_predicted_net_return_pct": 0.0,
        "comparison": "strictly_greater_unrounded_float64",
        "top_n": 3,
        "max_active_positions": 3,
        "max_active_positions_per_industry": 1,
        "main_rank": [
            "predicted_net_return_pct_desc",
            "signal_date_amount_desc",
            "security_id_asc",
        ],
        "amount_baseline_rank": [
            "signal_date_amount_desc",
            "security_id_asc",
        ],
    },
    "entry_signal_offset_sessions": 1,
    "planned_exit_signal_offset_sessions": 6,
    "hold_days": 5,
    "close_stop_loss_pct": 5.0,
    "close_stop_execution": "first_strictly_fillable_open_after_trigger_close",
    "blocked_sell_policy": "retry_each_following_open",
    "coverage_end_policy": "uniform_cutoff_before_entry_or_outcome_query",
    "terminal_listing_policy": (
        "right_censor_without_settlement_return_and_block_if_selected"
    ),
    "intraday_stop_fill_assumed": False,
    "exposure_multiplier": 1.0,
    "capital_model": "slot-daily",
    "roundtrip_cost_bps": 25.0,
    "slippage_bps": 10.0,
    "annual_financing_rate_pct": 8.0,
    "entry_numeric_abs_tolerance": 1e-9,
    "entry_execution": {
        "decision_cutoff": "next_open",
        "max_gap_up_pct": 6.0,
        "max_gap_down_pct": 7.0,
        "locked_limit_gap_pct": 9.3,
        "max_intraday_range_pct": 8.0,
    },
    "advancement_thresholds": {
        "minimum_complete_trades": 20,
        "minimum_full_win_rate_pct": 52.0,
        "maximum_full_drawdown_pct": 15.0,
        "minimum_full_profit_factor": 1.3,
        "minimum_latest_365d_return_pct": 50.0,
        "minimum_latest_365d_calmar": 1.5,
        "all_complete_365d_minimum_return_pct": 50.0,
        "all_complete_365d_maximum_drawdown_pct": 15.0,
        "all_complete_365d_minimum_payoff_ratio": 1.3,
        "all_complete_365d_minimum_profit_factor": 1.3,
        "all_complete_365d_minimum_calmar": 1.5,
    },
}


_BAR_COLUMNS = (
    "date",
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "adj_factor",
    "membership_name",
    "membership_industry",
    "membership_receipt_dataset",
    "membership_receipt_partition",
    "membership_list_date",
)


def _ordered_sessions(sessions: Sequence[str]) -> list[str]:
    values = [str(value) for value in sessions]
    if (
        not values
        or values != sorted(values)
        or len(values) != len(set(values))
        or any(_strict_iso_date(value) is None for value in values)
    ):
        raise ValueError("continuous ridge sessions must be ordered and unique")
    return values


def _strict_iso_date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _deterministic_median(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not all(math.isfinite(value) for value in ordered):
        raise ValueError("continuous ridge median inputs are invalid")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _deterministic_midrank(values: Sequence[float]) -> np.ndarray:
    raw = np.asarray(values, dtype=float)
    if raw.ndim != 1 or len(raw) == 0 or not np.isfinite(raw).all():
        raise ValueError("continuous ridge midrank inputs are invalid")
    ranks = pd.Series(raw).rank(method="average", ascending=True).to_numpy(
        dtype=float
    )
    return 2.0 * ranks / (len(raw) + 1.0) - 1.0


def _eligible_list_date(value: Any, signal_date: str) -> str | None:
    if pd.isna(value) or not isinstance(value, str):
        return None
    raw = value
    if _strict_iso_date(raw) is None or _strict_iso_date(signal_date) is None:
        return None
    parsed = date.fromisoformat(raw)
    signal = date.fromisoformat(signal_date)
    if parsed > signal:
        return None
    return raw


def _eligible_signal_name(value: Any) -> str | None:
    if pd.isna(value):
        return None
    name = str(value or "").strip()
    if not name or _is_excluded_name(name):
        return None
    return name


def _write_replay_progress(
    output_dir: str | Path,
    stage: str,
    **details: Any,
) -> None:
    write_json(
        str(Path(output_dir) / ".ranked_liquidity_v2_progress.json"),
        {
            "schema_version": "ranked-liquidity-replay-progress/v2",
            "stage": str(stage),
            **details,
        },
    )


class _BulkNextOpenReplayAdapter:
    def __init__(
        self,
        *,
        base_adapter: Any,
        sessions: Sequence[str],
        generation_refs: Mapping[str, Mapping[str, Any]],
        rows_by_symbol: Mapping[str, Sequence[Any]],
        evidence_receipt_sha256: str,
    ) -> None:
        base_contract = str(
            getattr(base_adapter, "contract_sha256", "") or ""
        )
        artifact_root = str(
            getattr(base_adapter, "artifact_root_sha256", "") or ""
        )
        if (
            len(base_contract) != 64
            or len(artifact_root) != 64
            or len(evidence_receipt_sha256) != 64
        ):
            raise PITReceiptError(
                "bulk next-open adapter audit hashes are invalid"
            )
        self._base_adapter = base_adapter
        self._sessions = _ordered_sessions(sessions)
        self._session_positions = {
            trade_date: position
            for position, trade_date in enumerate(self._sessions)
        }
        self._generation_refs = {
            trade_date: dict(generation_refs[trade_date])
            for trade_date in self._sessions
        }
        self._rows_by_symbol = {
            str(symbol): tuple(rows)
            for symbol, rows in rows_by_symbol.items()
        }
        self._artifact_root_sha256 = artifact_root
        identity = {
            "schema_version": "bulk-next-open-replay-adapter/v2",
            "base_replay_contract_sha256": base_contract,
            "artifact_root_sha256": artifact_root,
            "evidence_receipt_sha256": evidence_receipt_sha256,
        }
        self._contract_sha256 = _sha256(identity)

    @property
    def artifact_root_sha256(self) -> str:
        return self._artifact_root_sha256

    @property
    def contract_sha256(self) -> str:
        return self._contract_sha256

    def baseline_scenarios(self) -> Any:
        return self._base_adapter.baseline_scenarios()

    def next_open(
        self,
        symbol: Any,
        trade_date: Any,
        side: str = "buy",
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip()
        if (
            len(normalized_symbol) != 6
            or not normalized_symbol.isdigit()
            or normalized_symbol not in self._rows_by_symbol
        ):
            raise PITReceiptError(
                f"unknown symbol in bulk execution evidence: {symbol!r}"
            )
        session = _strict_iso_date(str(trade_date))
        session_position = self._session_positions.get(session or "")
        if session_position is None:
            raise ValueError(
                "trade_date is not a covered SSE open session: "
                f"{trade_date}"
            )
        row = self._rows_by_symbol[normalized_symbol][session_position]
        raw_bar: Mapping[str, Any] | None = None
        price_limit: Mapping[str, Any] | None = None
        suspension: Mapping[str, Any] | None = None
        if row is not None:
            (
                _ts_code,
                _generation_id,
                raw_open,
                limit_generation_id,
                pre_close,
                up_limit,
                down_limit,
                suspended,
            ) = row
            raw_bar = {"open": raw_open}
            if limit_generation_id is not None:
                price_limit = {
                    "pre_close": pre_close,
                    "up_limit": up_limit,
                    "down_limit": down_limit,
                }
            if suspended:
                suspension = {"suspend_type": "S"}
        verdict = next_open_fill_gate(
            side=side,
            raw_bar=raw_bar,
            price_limit=price_limit,
            suspension=suspension,
        )
        return {
            **verdict,
            "generation_proof": dict(self._generation_refs[session]),
        }


def _update_length_prefixed_digest(
    digest: Any,
    values: Sequence[Any],
) -> None:
    for value in values:
        encoded = (
            b"\xff"
            if value is None
            else str(value).encode("utf-8")
        )
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)


def _load_bulk_next_open_replay_adapter(
    connection: Any,
    *,
    base_adapter: Any,
    market_generation_refs: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
) -> tuple[_BulkNextOpenReplayAdapter, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    generation_refs: dict[str, dict[str, Any]] = {}
    for raw_ref in market_generation_refs:
        ref = dict(raw_ref)
        trade_date = _strict_iso_date(str(ref.get("trade_date") or ""))
        if trade_date is None or trade_date in generation_refs:
            raise PITReceiptError(
                "bulk next-open generation refs are invalid"
            )
        generation_refs[trade_date] = ref
    if list(sorted(generation_refs)) != session_dates:
        raise PITReceiptError(
            "bulk next-open generation refs differ from sessions"
        )

    rows_by_symbol: dict[str, list[Any]] = {}
    evidence_row_count = 0
    missing_price_limit_row_count = 0
    suspended_row_count = 0
    rows = connection.execute(
        """
        SELECT daily.generation_id, daily.trade_date, daily.ts_code,
               daily.open,
               price_limit.generation_id AS limit_generation_id,
               price_limit.pre_close, price_limit.up_limit,
               price_limit.down_limit,
               EXISTS(
                   SELECT 1
                   FROM market_session_generation_rows_suspend_d AS suspension
                   WHERE suspension.generation_id = daily.generation_id
                     AND suspension.ts_code = daily.ts_code
                     AND suspension.trade_date = daily.trade_date
                     AND upper(trim(suspension.suspend_type)) = 'S'
               ) AS suspended
        FROM market_session_generation_rows_daily AS daily
        JOIN market_session_generation_head AS head
          ON head.trade_date = daily.trade_date
         AND head.generation_id = daily.generation_id
        LEFT JOIN market_session_generation_rows_stk_limit AS price_limit
          ON price_limit.generation_id = daily.generation_id
         AND price_limit.trade_date = daily.trade_date
         AND price_limit.ts_code = daily.ts_code
        WHERE daily.trade_date BETWEEN ? AND ?
        """,
        (session_dates[0], session_dates[-1]),
    )
    for raw_row in rows:
        trade_date = _strict_iso_date(str(raw_row["trade_date"]))
        ts_code = str(raw_row["ts_code"] or "").strip().upper()
        if (
            trade_date not in generation_refs
            or not is_mainboard_chinext_symbol(ts_code)
        ):
            continue
        generation_id = str(raw_row["generation_id"] or "")
        if generation_id != str(
            generation_refs[trade_date]["generation_id"]
        ):
            raise PITReceiptError(
                "bulk next-open daily generation differs from manifest"
            )
        limit_generation_id = raw_row["limit_generation_id"]
        if (
            limit_generation_id is not None
            and str(limit_generation_id) != generation_id
        ):
            raise PITReceiptError(
                "bulk next-open limit generation differs from daily"
            )
        symbol = ts_code[:6]
        symbol_rows = rows_by_symbol.setdefault(
            symbol,
            [None] * len(session_dates),
        )
        session_position = session_positions[trade_date]
        if symbol_rows[session_position] is not None:
            raise PITReceiptError(
                "bulk next-open daily evidence is duplicated"
            )
        suspended = bool(int(raw_row["suspended"] or 0))
        row = (
            ts_code,
            generation_id,
            raw_row["open"],
            (
                str(limit_generation_id)
                if limit_generation_id is not None
                else None
            ),
            raw_row["pre_close"],
            raw_row["up_limit"],
            raw_row["down_limit"],
            suspended,
        )
        symbol_rows[session_position] = row
        evidence_row_count += 1
        missing_price_limit_row_count += int(limit_generation_id is None)
        suspended_row_count += int(suspended)

    digest = hashlib.sha256()
    digest.update(b"bulk-next-open-length-prefixed-rows/v2")
    for symbol in sorted(rows_by_symbol):
        for session_position, row in enumerate(rows_by_symbol[symbol]):
            if row is None:
                continue
            _update_length_prefixed_digest(
                digest,
                (
                    symbol,
                    session_dates[session_position],
                    *row,
                ),
            )
    receipt = {
        "schema_version": "bulk-next-open-evidence-receipt/v2",
        "range": {
            "start_date": session_dates[0],
            "end_date": session_dates[-1],
        },
        "session_count": len(session_dates),
        "covered_symbol_count": len(rows_by_symbol),
        "evidence_row_count": evidence_row_count,
        "missing_price_limit_row_count": (
            missing_price_limit_row_count
        ),
        "suspended_row_count": suspended_row_count,
        "evidence_rows_hash_method": (
            "length_prefixed_utf8_fields_ordered_by_symbol_session/v2"
        ),
        "evidence_rows_sha256": digest.hexdigest(),
        "market_generation_refs_sha256": _sha256(
            [generation_refs[value] for value in session_dates]
        ),
        "base_artifact_native_replay_contract_sha256": str(
            getattr(base_adapter, "contract_sha256", "") or ""
        ),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    adapter = _BulkNextOpenReplayAdapter(
        base_adapter=base_adapter,
        sessions=session_dates,
        generation_refs=generation_refs,
        rows_by_symbol=rows_by_symbol,
        evidence_receipt_sha256=receipt["receipt_sha256"],
    )
    return adapter, receipt


def _load_ranked_liquidity_bars(
    connection: Any,
    *,
    start_date: str,
    end_date: str,
    sessions: Sequence[str],
    security_code_transition_contract: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    frame = pd.read_sql_query(
        """
        SELECT daily.trade_date AS date, daily.ts_code, daily.open, daily.high,
               daily.low, daily.close, daily.pre_close, daily.amount,
               adjustment.adj_factor, membership.name AS membership_name,
               membership.industry AS membership_industry,
               membership.list_date AS membership_list_date,
               membership.receipt_dataset AS membership_receipt_dataset,
               membership.receipt_partition AS membership_receipt_partition
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
            "audited PIT artifact has no ranked-liquidity bars"
        )
    source_row_count = len(frame)
    frame = frame[
        frame["ts_code"].map(is_mainboard_chinext_symbol)
    ].copy()
    numeric_columns = (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "amount",
        "adj_factor",
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "adj_factor",
        ]
    )
    frame = frame[
        (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["adj_factor"] > 0)
    ].copy()
    if frame.empty:
        raise AuditedPITDevelopmentReplayError(
            "audited PIT artifact has no eligible ranked-liquidity bars"
        )
    frame["date"] = frame["date"].astype(str)
    frame["ts_code"] = frame["ts_code"].astype(str)
    if (
        not set(frame["date"]).issubset(set(session_dates))
        or frame.duplicated(["ts_code", "date"]).any()
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity source bars have invalid dates or duplicates"
        )
    try:
        frame, transition_receipt = apply_security_code_transition_contract(
            frame,
            sessions=session_dates,
            contract=security_code_transition_contract,
        )
    except SecurityCodeTransitionEvidenceError as exc:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity security-code transition application failed"
        ) from exc

    exact_membership = (
        frame["membership_receipt_dataset"].eq("bak_basic")
        & frame["membership_receipt_partition"].astype(str).eq(frame["date"])
    )
    membership_receipt_present = (
        frame["membership_receipt_dataset"].notna()
        | frame["membership_receipt_partition"].notna()
    )
    if bool((membership_receipt_present & ~exact_membership).any()):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity bar has a non-exact membership receipt"
        )
    membership_refs = sorted(
        {
            (
                str(row.membership_receipt_dataset),
                str(row.membership_receipt_partition),
            )
            for row in frame[exact_membership].itertuples(index=False)
        }
    )
    source_rows = [
        {
            "date": str(row.date),
            "ts_code": str(row.ts_code),
            "source_ts_code": str(row.source_ts_code),
            "security_id": str(row.security_id),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "amount": (
                float(row.amount)
                if math.isfinite(float(row.amount))
                else f"nonfinite:{float(row.amount)}"
            ),
            "adj_factor": float(row.adj_factor),
            "membership_name": (
                ""
                if pd.isna(row.membership_name)
                else str(row.membership_name)
            ),
            "membership_industry": (
                ""
                if pd.isna(row.membership_industry)
                else str(row.membership_industry)
            ),
            "membership_list_date": (
                ""
                if pd.isna(row.membership_list_date)
                else str(row.membership_list_date)
            ),
            "membership_receipt_dataset": (
                ""
                if pd.isna(row.membership_receipt_dataset)
                else str(row.membership_receipt_dataset)
            ),
            "membership_receipt_partition": (
                ""
                if pd.isna(row.membership_receipt_partition)
                else str(row.membership_receipt_partition)
            ),
        }
        for row in frame.sort_values(
            ["date", "security_id", "source_ts_code"],
            kind="mergesort",
        ).itertuples(index=False)
    ]
    receipt = {
        "schema_version": "ranked-liquidity-bar-loader-receipt/v2",
        "range": {"start_date": start_date, "end_date": end_date},
        "source_row_count": source_row_count,
        "eligible_market_row_count": len(frame),
        "matched_exact_membership_row_count": int(exact_membership.sum()),
        "missing_exact_membership_row_count": int((~exact_membership).sum()),
        "membership_receipt_refs": [
            {"dataset": dataset, "partition": partition}
            for dataset, partition in membership_refs
        ],
        "membership_receipt_refs_sha256": _sha256(membership_refs),
        "source_rows_sha256": _sha256(source_rows),
        "security_code_transition_application": transition_receipt,
        "security_code_transition_application_receipt_sha256": (
            transition_receipt["receipt_sha256"]
        ),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return frame.reset_index(drop=True), receipt


def _feature_row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_key": str(row["candidate_key"]),
        "signal_date": str(row["signal_date"]),
        "ts_code": str(row["ts_code"]),
        "source_ts_code": str(row["source_ts_code"]),
        "security_id": str(row["security_id"]),
        "signal_industry": str(row["signal_industry"]),
        "candidate_amount": float(row["candidate_amount"]),
        "membership_list_date": str(row["membership_list_date"]),
        "above_ma20": bool(row["above_ma20"]),
        "return_5d_pct": float(row["return_5d_pct"]),
        **{
            name: float(row[name])
            for name in RAW_STOCK_FEATURE_NAMES
        },
        **{name: float(row[name]) for name in FEATURE_NAMES},
    }


def _build_exact_cross_section_features(
    bars: pd.DataFrame,
    sessions: Sequence[str],
    *,
    minimum_cross_section_members: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    minimum = (
        int(minimum_cross_section_members)
        if minimum_cross_section_members is not None
        else int(
            CONTINUOUS_RIDGE_OOF_SPEC["minimum_cross_section_members"]
        )
    )
    if minimum <= 0:
        raise ValueError("minimum cross-section members must be positive")
    missing_columns = [column for column in _BAR_COLUMNS if column not in bars]
    if missing_columns:
        raise ValueError("continuous ridge bars are missing required columns")

    values = bars.copy()
    values["date"] = values["date"].astype(str)
    values["ts_code"] = values["ts_code"].astype(str)
    if "source_ts_code" not in values:
        values["source_ts_code"] = values["ts_code"]
    if "security_id" not in values:
        values["security_id"] = values["ts_code"].map(
            lambda ts_code: f"cn-a-share:{ts_code}"
        )
    if "security_code_transition_id" not in values:
        values["security_code_transition_id"] = None
    if "security_code_transition_contract_sha256" not in values:
        values["security_code_transition_contract_sha256"] = None

    status_counts: Counter[str] = Counter()
    source_row_count = len(values)
    in_scope = values["ts_code"].map(is_mainboard_chinext_symbol)
    status_counts["out_of_scope_market_row"] = int((~in_scope).sum())
    values = values[in_scope].copy()
    if values.empty:
        raise ValueError("continuous ridge bars have no in-scope rows")

    numeric_columns = (
        "open",
        "high",
        "low",
        "close",
        "amount",
        "adj_factor",
    )
    for column in numeric_columns:
        values[column] = pd.to_numeric(values[column], errors="coerce")
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    values["session_position"] = values["date"].map(session_positions)
    if values["session_position"].isna().any():
        raise ValueError("continuous ridge bars contain dates outside sessions")
    values["session_position"] = values["session_position"].astype(int)
    if values.duplicated(["security_id", "date"]).any():
        raise ValueError(
            "continuous ridge bars contain duplicate stable entity dates"
        )
    values = values.sort_values(
        ["security_id", "session_position", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)

    longitudinal_rows: list[dict[str, Any]] = []
    for security_id, group in values.groupby("security_id", sort=True):
        frame = group.sort_values("session_position", kind="mergesort")
        positions = frame["session_position"].to_numpy(dtype=int)
        for row_position in range(60, len(frame)):
            signal_session_position = int(positions[row_position])
            if signal_session_position < 60:
                continue
            if signal_session_position - int(positions[row_position - 60]) != 60:
                status_counts["missing_exact_61_session_history"] += 1
                continue
            window = frame.iloc[row_position - 60 : row_position + 1]
            if not np.array_equal(
                window["session_position"].to_numpy(dtype=int),
                np.arange(
                    signal_session_position - 60,
                    signal_session_position + 1,
                    dtype=int,
                ),
            ):
                status_counts["missing_exact_61_session_history"] += 1
                continue
            signal = window.iloc[-1]
            signal_date = str(signal["date"])
            exact_membership = (
                str(signal["membership_receipt_dataset"]) == "bak_basic"
                and str(signal["membership_receipt_partition"])
                == signal_date
            )
            name = _eligible_signal_name(signal["membership_name"])
            list_date = _eligible_list_date(
                signal["membership_list_date"],
                signal_date,
            )
            industry = (
                ""
                if pd.isna(signal["membership_industry"])
                else str(signal["membership_industry"]).strip()
            )
            raw_matrix = window[
                ["open", "high", "low", "close", "amount", "adj_factor"]
            ].to_numpy(dtype=float)
            price_factor_matrix = raw_matrix[:, [0, 1, 2, 3, 5]]
            used_amounts = raw_matrix[-60:, 4]
            if (
                not exact_membership
                or name is None
                or not industry
                or not np.isfinite(price_factor_matrix).all()
                or not np.isfinite(used_amounts).all()
                or (price_factor_matrix <= 0).any()
                or (used_amounts <= 0).any()
                or float(signal["high"]) <= float(signal["low"])
            ):
                status_counts["signal_ineligible"] += 1
                continue
            if list_date is None:
                status_counts["invalid_membership_list_date"] += 1
                continue

            adjusted_close = (
                window["close"].to_numpy(dtype=float)
                * window["adj_factor"].to_numpy(dtype=float)
            )
            amounts = window["amount"].to_numpy(dtype=float)
            amount_20 = amounts[-20:]
            amount_5 = amounts[-5:]
            amount_60 = amounts[-60:]
            ma20 = float(np.mean(adjusted_close[-20:]))
            if (
                float(np.mean(amount_5)) <= 0
                or float(np.mean(amount_60)) <= 0
                or ma20 <= 0
            ):
                status_counts["nonpositive_feature_denominator"] += 1
                continue
            one_step_returns = (
                adjusted_close[-21:][1:] / adjusted_close[-21:][:-1] - 1.0
            )
            signal_close = float(adjusted_close[-1])
            feature_values = {
                "amount_level_20": math.log1p(
                    float(np.mean(amount_20))
                ),
                "amount_volatility_20": float(
                    np.std(amount_20, ddof=0)
                ),
                "amount_surge_5_to_60": math.log(
                    float(np.mean(amount_5))
                    / float(np.mean(amount_60))
                ),
                "amihud_20": float(
                    np.mean(np.abs(one_step_returns) / amount_20)
                ),
                "realized_volatility_20_pct": (
                    float(np.std(one_step_returns, ddof=0)) * 100.0
                ),
                "max_return_20_pct": (
                    float(np.max(one_step_returns)) * 100.0
                ),
                "signal_return_1d_pct": float(one_step_returns[-1]) * 100.0,
                "reversal_20_skip5_pct": -(
                    float(adjusted_close[-6])
                    / float(adjusted_close[-26])
                    - 1.0
                )
                * 100.0,
            }
            if not all(
                math.isfinite(value) for value in feature_values.values()
            ):
                status_counts["nonfinite_longitudinal_feature"] += 1
                continue
            ts_code = str(signal["ts_code"])
            source_ts_code = str(signal["source_ts_code"])
            candidate_key = f"{security_id}|{signal_date}"
            longitudinal_rows.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "date": signal_date,
                    "ts_code": ts_code,
                    "source_ts_code": source_ts_code,
                    "security_id": str(security_id),
                    "security_code_transition_id": (
                        None
                        if pd.isna(signal["security_code_transition_id"])
                        else str(signal["security_code_transition_id"])
                    ),
                    "security_code_transition_contract_sha256": (
                        None
                        if pd.isna(
                            signal[
                                "security_code_transition_contract_sha256"
                            ]
                        )
                        else str(
                            signal[
                                "security_code_transition_contract_sha256"
                            ]
                        )
                    ),
                    "symbol": ts_code[:6],
                    "name": name,
                    "signal_industry": industry,
                    "membership_list_date": list_date,
                    "candidate_amount": float(signal["amount"]),
                    "signal_frame_index": int(row_position),
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": signal_date,
                    "above_ma20": bool(signal_close > ma20),
                    "return_5d_pct": (
                        signal_close / float(adjusted_close[-6]) - 1.0
                    )
                    * 100.0,
                    **feature_values,
                }
            )

    if not longitudinal_rows:
        raise ValueError("continuous ridge produced no longitudinal features")
    longitudinal = pd.DataFrame(longitudinal_rows)
    complete_groups: list[pd.DataFrame] = []
    group_receipts: list[dict[str, Any]] = []
    for signal_date, raw_group in longitudinal.groupby(
        "signal_date",
        sort=True,
    ):
        group = raw_group.sort_values(
            ["security_id", "source_ts_code"],
            kind="mergesort",
        ).copy()
        if len(group) < minimum:
            status_counts["cross_section_member_count_below_minimum"] += len(
                group
            )
            continue
        member_keys = group["security_id"].astype(str).tolist()
        if len(member_keys) != len(set(member_keys)):
            raise ValueError(
                "continuous ridge cross-section has duplicate entities"
            )
        return_inputs = [
            {
                "security_id": str(row.security_id),
                "value": float(row.return_5d_pct),
            }
            for row in group.itertuples(index=False)
        ]
        breadth_inputs = [
            {
                "security_id": str(row.security_id),
                "above_ma20": bool(row.above_ma20),
            }
            for row in group.itertuples(index=False)
        ]
        breadth = sum(
            item["above_ma20"] for item in breadth_inputs
        ) / len(breadth_inputs)
        median_return = _deterministic_median(
            [item["value"] for item in return_inputs]
        )
        raw_feature_inputs: dict[str, list[dict[str, Any]]] = {}
        ranked_feature_outputs: dict[str, list[dict[str, Any]]] = {}
        for raw_name, ranked_name in zip(
            RAW_STOCK_FEATURE_NAMES,
            RANKED_STOCK_FEATURE_NAMES,
        ):
            raw_values = group[raw_name].to_numpy(dtype=float)
            ranked_values = _deterministic_midrank(raw_values)
            group[ranked_name] = ranked_values
            raw_feature_inputs[raw_name] = [
                {
                    "security_id": str(security_id),
                    "value": float(value),
                }
                for security_id, value in zip(
                    group["security_id"].astype(str),
                    raw_values,
                )
            ]
            ranked_feature_outputs[ranked_name] = [
                {
                    "security_id": str(security_id),
                    "value": float(value),
                }
                for security_id, value in zip(
                    group["security_id"].astype(str),
                    ranked_values,
                )
            ]
        group["cross_section_above_ma20_fraction"] = float(breadth)
        group["cross_section_median_return_5d_pct"] = float(
            median_return
        )
        receipt = {
            "signal_date": str(signal_date),
            "member_count": len(group),
            "member_keys_sha256": _sha256(member_keys),
            "breadth_inputs_sha256": _sha256(breadth_inputs),
            "return_5d_inputs_sha256": _sha256(return_inputs),
            "raw_feature_inputs_sha256": {
                name: _sha256(raw_feature_inputs[name])
                for name in RAW_STOCK_FEATURE_NAMES
            },
            "ranked_feature_outputs_sha256": {
                name: _sha256(ranked_feature_outputs[name])
                for name in RANKED_STOCK_FEATURE_NAMES
            },
            "cross_section_above_ma20_fraction": float(breadth),
            "cross_section_median_return_5d_pct": float(median_return),
        }
        receipt["statistics_sha256"] = _sha256(receipt)
        group_receipts.append(receipt)
        complete_groups.append(group)

    if not complete_groups:
        raise ValueError("continuous ridge produced no complete cross-section")
    features = pd.concat(complete_groups, ignore_index=True).sort_values(
        ["signal_date", "security_id", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    if features["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge feature keys are duplicated")
    matrix = features[list(FEATURE_NAMES)].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("continuous ridge feature matrix is nonfinite")
    feature_payload = [
        _feature_row_payload(row)
        for row in features.to_dict("records")
    ]
    scope = market_scope_contract()
    receipt = {
        "schema_version": "ranked-liquidity-ridge-feature-receipt/v2",
        "scope_policy_id": scope["policy_id"],
        "scope_policy_sha256": scope["policy_sha256"],
        "cross_section_transform": dict(
            CONTINUOUS_RIDGE_OOF_SPEC["cross_section_transform"]
        ),
        "session_count": len(session_dates),
        "sessions_sha256": _sha256(session_dates),
        "source_row_count": source_row_count,
        "in_scope_row_count": len(values),
        "minimum_cross_section_members": minimum,
        "status_counts": dict(sorted(status_counts.items())),
        "cross_section_group_count": len(group_receipts),
        "cross_section_groups": group_receipts,
        "cross_section_groups_sha256": _sha256(group_receipts),
        "feature_row_count": len(features),
        "feature_rows_sha256": _sha256(feature_payload),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return features, receipt


def _replay_cross_section_group_receipts(
    features: pd.DataFrame,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for signal_date, raw_group in features.groupby("signal_date", sort=True):
        group = raw_group.sort_values(
            ["security_id", "source_ts_code"],
            kind="mergesort",
        )
        member_keys = group["security_id"].astype(str).tolist()
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("feature receipt contains duplicate entities")
        breadth_inputs = [
            {
                "security_id": str(row.security_id),
                "above_ma20": bool(row.above_ma20),
            }
            for row in group.itertuples(index=False)
        ]
        return_inputs = [
            {
                "security_id": str(row.security_id),
                "value": float(row.return_5d_pct),
            }
            for row in group.itertuples(index=False)
        ]
        breadth = sum(
            item["above_ma20"] for item in breadth_inputs
        ) / len(group)
        median_return = _deterministic_median(
            [item["value"] for item in return_inputs]
        )
        if not np.allclose(
            group["cross_section_above_ma20_fraction"].to_numpy(
                dtype=float
            ),
            breadth,
            rtol=0.0,
            atol=1e-15,
        ):
            raise ValueError("feature receipt breadth replay failed")
        if not np.allclose(
            group["cross_section_median_return_5d_pct"].to_numpy(
                dtype=float
            ),
            median_return,
            rtol=0.0,
            atol=1e-15,
        ):
            raise ValueError("feature receipt market return replay failed")

        raw_hashes: dict[str, str] = {}
        ranked_hashes: dict[str, str] = {}
        for raw_name, ranked_name in zip(
            RAW_STOCK_FEATURE_NAMES,
            RANKED_STOCK_FEATURE_NAMES,
        ):
            raw_values = group[raw_name].to_numpy(dtype=float)
            expected_ranked = _deterministic_midrank(raw_values)
            actual_ranked = group[ranked_name].to_numpy(dtype=float)
            if not np.allclose(
                actual_ranked,
                expected_ranked,
                rtol=0.0,
                atol=1e-15,
            ):
                raise ValueError("feature receipt midrank replay failed")
            raw_rows = [
                {
                    "security_id": security_id,
                    "value": float(value),
                }
                for security_id, value in zip(member_keys, raw_values)
            ]
            ranked_rows = [
                {
                    "security_id": security_id,
                    "value": float(value),
                }
                for security_id, value in zip(
                    member_keys,
                    expected_ranked,
                )
            ]
            raw_hashes[raw_name] = _sha256(raw_rows)
            ranked_hashes[ranked_name] = _sha256(ranked_rows)
        group_receipt = {
            "signal_date": str(signal_date),
            "member_count": len(group),
            "member_keys_sha256": _sha256(member_keys),
            "breadth_inputs_sha256": _sha256(breadth_inputs),
            "return_5d_inputs_sha256": _sha256(return_inputs),
            "raw_feature_inputs_sha256": raw_hashes,
            "ranked_feature_outputs_sha256": ranked_hashes,
            "cross_section_above_ma20_fraction": float(breadth),
            "cross_section_median_return_5d_pct": float(median_return),
        }
        group_receipt["statistics_sha256"] = _sha256(group_receipt)
        receipts.append(group_receipt)
    return receipts


def verify_feature_receipt(
    features: pd.DataFrame,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    stored = dict(receipt)
    expected_receipt_sha256 = str(
        stored.pop("receipt_sha256", "")
    )
    if (
        not expected_receipt_sha256
        or _sha256(stored) != expected_receipt_sha256
    ):
        raise ValueError("feature receipt self hash is invalid")
    required = {
        "candidate_key",
        "signal_date",
        "ts_code",
        "source_ts_code",
        "security_id",
        "signal_industry",
        "candidate_amount",
        "membership_list_date",
        "above_ma20",
        "return_5d_pct",
        *RAW_STOCK_FEATURE_NAMES,
        *FEATURE_NAMES,
    }
    if not required.issubset(features.columns):
        raise ValueError("feature receipt matrix is incomplete")
    ordered = features.sort_values(
        ["signal_date", "security_id", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    if ordered["candidate_key"].duplicated().any():
        raise ValueError("feature receipt candidate keys are duplicated")
    structural_rows = ordered[
        [
            "candidate_key",
            "signal_date",
            "ts_code",
            "source_ts_code",
            "security_id",
            "candidate_amount",
            "membership_list_date",
        ]
    ].to_dict("records")
    for row in structural_rows:
        signal_date = str(row["signal_date"])
        security_id = str(row["security_id"])
        ts_code = str(row["ts_code"])
        source_ts_code = str(row["source_ts_code"])
        try:
            amount = float(row["candidate_amount"])
        except (TypeError, ValueError) as exc:
            raise ValueError("feature receipt identity replay failed") from exc
        if (
            _strict_iso_date(signal_date) is None
            or str(row["candidate_key"])
            != f"{security_id}|{signal_date}"
            or not security_id
            or not is_mainboard_chinext_symbol(ts_code)
            or len(source_ts_code) != 9
            or _eligible_list_date(
                row["membership_list_date"],
                signal_date,
            )
            is None
            or not math.isfinite(amount)
            or amount <= 0.0
        ):
            raise ValueError("feature receipt identity replay failed")
    matrix = ordered[
        [*RAW_STOCK_FEATURE_NAMES, *FEATURE_NAMES]
    ].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("feature receipt matrix is nonfinite")
    feature_payload = [
        _feature_row_payload(row)
        for row in ordered.to_dict("records")
    ]
    group_receipts = _replay_cross_section_group_receipts(ordered)
    if (
        int(receipt.get("feature_row_count", -1)) != len(ordered)
        or receipt.get("feature_rows_sha256") != _sha256(feature_payload)
        or int(receipt.get("cross_section_group_count", -1))
        != len(group_receipts)
        or receipt.get("cross_section_groups") != group_receipts
        or receipt.get("cross_section_groups_sha256")
        != _sha256(group_receipts)
        or receipt.get("cross_section_transform")
        != CONTINUOUS_RIDGE_OOF_SPEC["cross_section_transform"]
        or any(
            int(group["member_count"])
            < int(receipt.get("minimum_cross_section_members", -1))
            for group in group_receipts
        )
    ):
        raise ValueError("feature receipt replay failed")
    return {
        "verified": True,
        "receipt_sha256": expected_receipt_sha256,
        "feature_row_count": len(ordered),
        "cross_section_group_count": len(group_receipts),
    }


def _signal_date_weights(signal_dates: Sequence[str]) -> np.ndarray:
    dates = [str(value) for value in signal_dates]
    if not dates:
        raise ValueError("continuous ridge training dates are empty")
    counts = Counter(dates)
    return np.asarray(
        [1.0 / counts[value] for value in dates],
        dtype=float,
    )


def _continuous_net_label(gross_return_pct: float) -> float:
    value = float(gross_return_pct)
    if not math.isfinite(value):
        raise ValueError("continuous ridge gross return is nonfinite")
    return value - float(
        CONTINUOUS_RIDGE_OOF_SPEC["label"][
            "friction_percentage_points"
        ]
    )


def _fit_weighted_continuous_ridge(
    x: np.ndarray,
    y: np.ndarray,
    signal_dates: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
) -> dict[str, np.ndarray]:
    x_values = np.asarray(x, dtype=float)
    y_values = np.asarray(y, dtype=float)
    if (
        x_values.ndim != 2
        or y_values.ndim != 1
        or len(x_values) != len(y_values)
        or len(y_values) != len(signal_dates)
        or len(x_values) == 0
        or not np.isfinite(x_values).all()
        or not np.isfinite(y_values).all()
        or not math.isfinite(float(ridge_lambda))
        or float(ridge_lambda) < 0
    ):
        raise ValueError("invalid continuous ridge training inputs")
    weights = _signal_date_weights(signal_dates)
    total_weight = float(weights.sum())
    mean = (x_values * weights[:, None]).sum(axis=0) / total_weight
    centered = x_values - mean
    variance = (
        (centered**2 * weights[:, None]).sum(axis=0) / total_weight
    )
    scale = np.sqrt(variance)
    scale = np.where(scale > 1e-12, scale, 1.0)
    standardized = centered / scale
    design = np.column_stack(
        [np.ones(len(standardized), dtype=float), standardized]
    )
    sqrt_weights = np.sqrt(weights)
    weighted_design = design * sqrt_weights[:, None]
    weighted_target = y_values * sqrt_weights
    penalty = np.eye(design.shape[1], dtype=float) * float(ridge_lambda)
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(
        weighted_design.T @ weighted_design + penalty,
        weighted_design.T @ weighted_target,
    )
    return {
        "mean": mean,
        "scale": scale,
        "coefficients": coefficients,
    }


def _predict_continuous_ridge(
    model: Mapping[str, np.ndarray],
    x: np.ndarray,
) -> np.ndarray:
    values = np.asarray(x, dtype=float)
    mean = np.asarray(model["mean"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    if (
        values.ndim != 2
        or values.shape[1] != len(mean)
        or len(scale) != len(mean)
        or len(coefficients) != len(mean) + 1
        or not np.isfinite(values).all()
    ):
        raise ValueError("invalid continuous ridge prediction inputs")
    standardized = (values - mean) / scale
    design = np.column_stack(
        [np.ones(len(standardized), dtype=float), standardized]
    )
    scores = design @ coefficients
    if not np.isfinite(scores).all():
        raise ValueError("continuous ridge predictions are nonfinite")
    return scores


def _fold_ranges(
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    validation_sessions: int = 63,
) -> list[tuple[str, str]]:
    values = _ordered_sessions(sessions)
    if minimum_training_sessions <= 0 or validation_sessions <= 0:
        raise ValueError("continuous ridge fold lengths must be positive")
    return [
        (
            values[start],
            values[min(start + validation_sessions - 1, len(values) - 1)],
        )
        for start in range(
            minimum_training_sessions,
            len(values),
            validation_sessions,
        )
    ]


def _model_payload(model: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {
        "mean": [float(value) for value in model["mean"]],
        "scale": [float(value) for value in model["scale"]],
        "coefficients": [
            float(value) for value in model["coefficients"]
        ],
    }


def _build_purged_oof_scores(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    *,
    minimum_training_sessions: int = 126,
    validation_sessions: int = 63,
    require_nonempty_validation_folds: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    required = {"candidate_key", "signal_date", *FEATURE_NAMES}
    if not required.issubset(features.columns):
        raise ValueError("continuous ridge OOF features are incomplete")
    rows = features.copy()
    rows["candidate_key"] = rows["candidate_key"].astype(str)
    rows["signal_date"] = rows["signal_date"].astype(str)
    if rows["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge OOF feature keys are duplicated")
    session_set = set(session_dates)
    if (
        rows["candidate_key"].eq("").any()
        or not rows["signal_date"].map(
            lambda value: (
                _strict_iso_date(value) is not None
                and value in session_set
            )
        ).all()
    ):
        raise ValueError("continuous ridge OOF feature dates are invalid")
    if not np.isfinite(rows[list(FEATURE_NAMES)].to_numpy(dtype=float)).all():
        raise ValueError("continuous ridge OOF feature matrix is nonfinite")
    feature_signal_dates = dict(
        zip(rows["candidate_key"], rows["signal_date"])
    )

    outcome_lookup: dict[str, dict[str, Any]] = {}
    for raw_outcome in outcomes:
        outcome = dict(raw_outcome)
        key = str(outcome.get("candidate_key") or "")
        if (
            not key
            or key in outcome_lookup
            or key not in feature_signal_dates
        ):
            raise ValueError("continuous ridge outcomes have invalid keys")
        outcome_lookup[key] = outcome

    completed: dict[str, dict[str, Any]] = {}
    for key, outcome in outcome_lookup.items():
        if outcome.get("right_censored") is True:
            continue
        exit_date = outcome.get("exit_date")
        return_pct = outcome.get("return_pct")
        try:
            gross_return = float(return_pct)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "continuous ridge complete outcome return is invalid"
            ) from exc
        if (
            _strict_iso_date(exit_date) is None
            or exit_date not in session_set
            or exit_date <= feature_signal_dates[key]
            or not math.isfinite(gross_return)
        ):
            raise ValueError("continuous ridge outcome boundary is invalid")
        completed[key] = {
            "exit_date": exit_date,
            "net_label": _continuous_net_label(gross_return),
        }

    folds = _fold_ranges(
        session_dates,
        minimum_training_sessions=minimum_training_sessions,
        validation_sessions=validation_sessions,
    )
    scored_frames: list[pd.DataFrame] = []
    fold_receipts: list[dict[str, Any]] = []
    for fold_index, (validation_start, validation_end) in enumerate(
        folds,
        start=1,
    ):
        validation = rows[
            rows["signal_date"].between(
                validation_start,
                validation_end,
                inclusive="both",
            )
        ].sort_values(
            ["signal_date", "candidate_key"],
            kind="mergesort",
        )
        if validation.empty:
            if require_nonempty_validation_folds:
                raise ValueError(
                    "continuous ridge fold has no validation feature rows"
                )
            continue
        training_records = []
        for row in rows.itertuples(index=False):
            key = str(row.candidate_key)
            outcome = completed.get(key)
            if (
                outcome is None
                or str(outcome["exit_date"]) >= validation_start
            ):
                continue
            training_records.append(
                {
                    "candidate_key": key,
                    "signal_date": str(row.signal_date),
                    "exit_date": str(outcome["exit_date"]),
                    "net_label": float(outcome["net_label"]),
                    "features": [
                        float(getattr(row, name)) for name in FEATURE_NAMES
                    ],
                }
            )
        training_records.sort(
            key=lambda item: (
                item["signal_date"],
                item["candidate_key"],
            )
        )
        if not training_records:
            raise ValueError(
                "continuous ridge fold has no mature complete training rows"
            )
        x_train = np.asarray(
            [item["features"] for item in training_records],
            dtype=float,
        )
        y_train = np.asarray(
            [item["net_label"] for item in training_records],
            dtype=float,
        )
        model = _fit_weighted_continuous_ridge(
            x_train,
            y_train,
            [item["signal_date"] for item in training_records],
            ridge_lambda=float(
                CONTINUOUS_RIDGE_OOF_SPEC["model"]["ridge_lambda"]
            ),
        )
        scores = _predict_continuous_ridge(
            model,
            validation[list(FEATURE_NAMES)].to_numpy(dtype=float),
        )
        scored = validation.copy()
        scored["predicted_net_return_pct"] = scores
        scored_frames.append(scored)
        score_rows = [
            {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                "predicted_net_return_pct": float(score),
            }
            for row, score in zip(
                validation.itertuples(index=False),
                scores,
            )
        ]
        model_payload = _model_payload(model)
        fold_receipt = {
            "fold": fold_index,
            "validation_start": validation_start,
            "validation_end": validation_end,
            "training_candidate_count": len(training_records),
            "training_signal_date_count": len(
                {item["signal_date"] for item in training_records}
            ),
            "training_last_exit_date": max(
                item["exit_date"] for item in training_records
            ),
            "training_candidate_keys_sha256": _sha256(
                [item["candidate_key"] for item in training_records]
            ),
            "training_rows_sha256": _sha256(training_records),
            "training_label": "gross_return_pct_minus_0.45",
            "model": model_payload,
            "model_sha256": _sha256(model_payload),
            "validation_candidate_count": len(validation),
            "validation_signal_date_count": int(
                validation["signal_date"].nunique()
            ),
            "score_rows_sha256": _sha256(score_rows),
        }
        fold_receipt["receipt_sha256"] = _sha256(fold_receipt)
        fold_receipts.append(fold_receipt)

    if not scored_frames:
        raise ValueError("continuous ridge produced no OOF scores")
    scored_oof = pd.concat(scored_frames, ignore_index=True).sort_values(
        ["signal_date", "candidate_key"],
        kind="mergesort",
    ).reset_index(drop=True)
    if scored_oof["candidate_key"].duplicated().any():
        raise ValueError("continuous ridge OOF keys overlap across folds")
    score_payload = [
        {
            "candidate_key": str(row.candidate_key),
            "signal_date": str(row.signal_date),
            "predicted_net_return_pct": float(
                row.predicted_net_return_pct
            ),
        }
        for row in scored_oof.itertuples(index=False)
    ]
    receipt = {
        "schema_version": "ranked-liquidity-ridge-purged-oof-receipt/v2",
        "minimum_training_sessions": int(minimum_training_sessions),
        "validation_sessions": int(validation_sessions),
        "purge": "complete_exit_date_strictly_before_validation_start",
        "fold_count": len(fold_receipts),
        "folds": fold_receipts,
        "folds_sha256": _sha256(fold_receipts),
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": _sha256(score_payload),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return scored_oof, receipt


def _selection_trade_key(trade: Mapping[str, Any]) -> str:
    values = [
        str(trade.get("security_id") or ""),
        str(trade.get("signal_date") or "")[:10],
        str(trade.get("entry_date") or "")[:10],
        str(trade.get("exit_date") or "")[:10],
    ]
    if not all(values):
        raise ValueError("continuous ridge selection key is incomplete")
    return "|".join(values)


def _positive_score_pool(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered = sorted(
        (dict(candidate) for candidate in candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("candidate_key") or ""),
        ),
    )
    input_keys: list[str] = []
    positive: list[dict[str, Any]] = []
    positive_keys: list[str] = []
    for candidate in ordered:
        key = str(candidate.get("candidate_key") or "")
        if not key:
            raise ValueError("continuous ridge score candidate key is missing")
        score = float(candidate.get("predicted_net_return_pct"))
        if not math.isfinite(score):
            raise ValueError("continuous ridge prediction is nonfinite")
        input_keys.append(key)
        if score > 0.0:
            positive.append(candidate)
            positive_keys.append(key)
    if len(input_keys) != len(set(input_keys)):
        raise ValueError("continuous ridge score pool keys are duplicated")
    receipt = {
        "schema_version": "continuous-ridge-positive-score-pool/v1",
        "comparison": "predicted_net_return_pct_strictly_greater_than_zero",
        "input_candidate_count": len(ordered),
        "input_candidate_keys_sha256": _sha256(input_keys),
        "positive_candidate_count": len(positive),
        "positive_candidate_keys_sha256": _sha256(positive_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return positive, receipt


def _selection_candidate_table(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        trade = dict(candidate)
        score = float(trade.get("predicted_net_return_pct"))
        amount = float(trade.get("candidate_amount"))
        industry = str(trade.get("signal_industry") or "").strip()
        security_id = str(trade.get("security_id") or "")
        if (
            not math.isfinite(score)
            or score <= 0.0
            or not math.isfinite(amount)
            or not industry
            or not security_id
        ):
            raise ValueError("continuous ridge selection candidate is invalid")
        rows.append(
            {
                "trade_key": _selection_trade_key(trade),
                "candidate_key": str(trade.get("candidate_key") or ""),
                "security_id": security_id,
                "signal_industry": industry,
                "predicted_net_return_pct": score,
                "candidate_amount": amount,
                "right_censored": trade.get("right_censored") is True,
            }
        )
    rows.sort(key=lambda item: item["trade_key"])
    if len({item["trade_key"] for item in rows}) != len(rows):
        raise ValueError("continuous ridge selection candidates are duplicated")
    return rows


def _selection_rank_key(
    trade: Mapping[str, Any],
    *,
    rank_mode: str,
) -> tuple[Any, ...]:
    security_id = str(trade.get("security_id") or "")
    amount = float(trade.get("candidate_amount"))
    if not security_id or not math.isfinite(amount):
        raise ValueError("continuous ridge selection rank inputs are invalid")
    if rank_mode == "predicted_net_return":
        score = float(trade.get("predicted_net_return_pct"))
        if not math.isfinite(score):
            raise ValueError("continuous ridge model score is invalid")
        return (-score, -amount, security_id)
    if rank_mode == "signal_date_amount":
        return (-amount, security_id)
    raise ValueError("continuous ridge selection rank mode is invalid")


def _select_with_industry_cap_receipt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    top_n: int,
    max_active_positions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if top_n <= 0 or max_active_positions <= 0:
        raise ValueError("continuous ridge selection capacities are invalid")
    candidate_table = _selection_candidate_table(candidates)
    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        signal_date = str(candidate.get("signal_date") or "")[:10]
        if not signal_date:
            raise ValueError("continuous ridge selection date is missing")
        by_signal_date[signal_date].append(candidate)

    selected: list[dict[str, Any]] = []
    active_positions: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    all_ordered_keys: list[str] = []
    for signal_date in sorted(by_signal_date):
        signal_day = date.fromisoformat(signal_date)
        active_positions = [
            trade
            for trade in active_positions
            if date.fromisoformat(str(trade["exit_date"])[:10]) > signal_day
        ]
        trades = sorted(
            by_signal_date[signal_date],
            key=lambda trade: _selection_rank_key(
                trade,
                rank_mode=rank_mode,
            ),
        )
        ordered_keys = [_selection_trade_key(trade) for trade in trades]
        if len(ordered_keys) != len(set(ordered_keys)):
            raise ValueError(
                "continuous ridge daily selection keys are duplicated"
            )
        all_ordered_keys.extend(ordered_keys)
        active_securities = {
            str(trade["security_id"]) for trade in active_positions
        }
        active_industries = {
            str(trade["signal_industry"]).strip()
            for trade in active_positions
        }
        selected_today = 0
        selected_keys: list[str] = []
        decisions: list[dict[str, str]] = []
        for index, trade in enumerate(trades):
            trade_key = ordered_keys[index]
            security_id = str(trade["security_id"])
            industry = str(trade["signal_industry"]).strip()
            if security_id in active_securities:
                decisions.append(
                    {"trade_key": trade_key, "decision": "active_security"}
                )
                continue
            if industry in active_industries:
                decisions.append(
                    {"trade_key": trade_key, "decision": "active_industry"}
                )
                continue
            if len(active_positions) >= max_active_positions:
                decisions.extend(
                    {
                        "trade_key": ordered_keys[remaining],
                        "decision": "max_active_positions_break",
                    }
                    for remaining in range(index, len(trades))
                )
                break
            selected.append(trade)
            active_positions.append(trade)
            active_securities.add(security_id)
            active_industries.add(industry)
            selected_today += 1
            selected_keys.append(trade_key)
            decisions.append(
                {"trade_key": trade_key, "decision": "selected"}
            )
            if selected_today >= top_n:
                decisions.extend(
                    {
                        "trade_key": ordered_keys[remaining],
                        "decision": "top_n_break",
                    }
                    for remaining in range(index + 1, len(trades))
                )
                break
        days.append(
            {
                "signal_date": signal_date,
                "ordered_candidate_trade_keys": ordered_keys,
                "ordered_candidate_root_sha256": _sha256(ordered_keys),
                "decisions": decisions,
                "selected_trade_keys": selected_keys,
            }
        )

    selected_keys = [_selection_trade_key(trade) for trade in selected]
    receipt = {
        "schema_version": "continuous-ridge-industry-selection-receipt/v1",
        "parameters": {
            "rank_mode": rank_mode,
            "top_n": int(top_n),
            "max_active_positions": int(max_active_positions),
            "max_active_positions_per_industry": 1,
            "same_day_exit_before_signal_selection": True,
            "industry_source": "signal_date_frozen",
            "stable_identity": "security_id",
        },
        "candidate_count": len(candidate_table),
        "candidate_table_sha256": _sha256(candidate_table),
        "ordered_candidate_trade_keys_sha256": _sha256(
            all_ordered_keys
        ),
        "selected_count": len(selected),
        "selected_trade_keys": selected_keys,
        "selected_trade_keys_sha256": _sha256(selected_keys),
        "signal_day_count": len(days),
        "days": days,
        "days_sha256": _sha256(days),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return selected, receipt


def _assert_shared_strict_execution_contract() -> None:
    comparisons = (
        (
            CONTINUOUS_RIDGE_OOF_SPEC[
                "planned_exit_signal_offset_sessions"
            ],
            INDUSTRY_RESIDUAL_SPEC[
                "planned_exit_signal_offset_sessions"
            ],
            "planned exit offset",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["hold_days"],
            INDUSTRY_RESIDUAL_SPEC["hold_days"],
            "hold days",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["close_stop_loss_pct"],
            INDUSTRY_RESIDUAL_SPEC["close_stop_loss_pct"],
            "close stop",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["entry_execution"],
            INDUSTRY_RESIDUAL_SPEC["entry_execution"],
            "entry execution",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["capital_model"],
            INDUSTRY_RESIDUAL_SPEC["capital_model"],
            "capital model",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["exposure_multiplier"],
            INDUSTRY_RESIDUAL_SPEC["exposure_multiplier"],
            "exposure multiplier",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["roundtrip_cost_bps"],
            INDUSTRY_RESIDUAL_SPEC["roundtrip_cost_bps"],
            "roundtrip cost",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["slippage_bps"],
            INDUSTRY_RESIDUAL_SPEC["slippage_bps"],
            "slippage",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["annual_financing_rate_pct"],
            INDUSTRY_RESIDUAL_SPEC["annual_financing_rate_pct"],
            "financing",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["blocked_sell_policy"],
            INDUSTRY_RESIDUAL_SPEC["blocked_sell_policy"],
            "blocked sell",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["terminal_listing_policy"],
            INDUSTRY_RESIDUAL_SPEC["terminal_listing_policy"],
            "terminal listing",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["entry_execution"],
            TREND_PULLBACK_SPEC["entry_execution"],
            "strict trade core entry execution",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["hold_days"],
            TREND_PULLBACK_SPEC["hold_days"],
            "strict trade core hold days",
        ),
        (
            CONTINUOUS_RIDGE_OOF_SPEC["close_stop_loss_pct"],
            TREND_PULLBACK_SPEC["close_stop_loss_pct"],
            "strict trade core close stop",
        ),
    )
    for actual, expected, label in comparisons:
        if actual != expected:
            raise ValueError(
                f"continuous ridge shared strict {label} contract drifted"
            )
    expected_label_friction = (
        float(CONTINUOUS_RIDGE_OOF_SPEC["roundtrip_cost_bps"])
        + 2.0 * float(CONTINUOUS_RIDGE_OOF_SPEC["slippage_bps"])
    ) / 100.0
    actual_label_friction = float(
        CONTINUOUS_RIDGE_OOF_SPEC["label"][
            "friction_percentage_points"
        ]
    )
    if not math.isclose(
        actual_label_friction,
        expected_label_friction,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "continuous ridge label friction differs from execution costs"
        )


def _apply_uniform_tail_cutoff(
    candidates: Sequence[Mapping[str, Any]],
    *,
    sessions: Sequence[str],
    family: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _assert_shared_strict_execution_contract()
    return _residual_apply_uniform_tail_cutoff(
        candidates,
        sessions=sessions,
        family=family,
    )


def _preflight_strict_entries(
    raw_candidates: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    sessions: Sequence[str],
    adapter: Any,
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[tuple[str, str, str], dict[str, Any]],
]:
    _assert_shared_strict_execution_contract()
    return _residual_preflight_strict_entries(
        raw_candidates,
        frames_by_symbol=frames_by_symbol,
        sessions=sessions,
        adapter=adapter,
        verdict_cache=verdict_cache,
    )


def _build_strict_outcomes(
    executable_candidates: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    sessions: Sequence[str],
    adapter: Any,
    verdict_cache: dict[tuple[str, str, str], dict[str, Any]],
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
    terminal_listing_evidence: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _assert_shared_strict_execution_contract()
    session_dates = _ordered_sessions(sessions)
    session_positions = {
        trade_date: position
        for position, trade_date in enumerate(session_dates)
    }
    completed: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    ordered = sorted(
        (dict(candidate) for candidate in executable_candidates),
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    candidate_keys: list[str] = []
    date_positions_by_symbol = {
        symbol: _resolve_frame_date_positions(
            frame=frame,
            prevalidated_date_positions=None,
        )
        for symbol, frame in frames_by_symbol.items()
    }
    for candidate in ordered:
        candidate_key = str(candidate.get("candidate_key") or "")
        symbol = str(candidate.get("symbol") or "")
        security_id = str(candidate.get("security_id") or "")
        signal_date = str(candidate.get("signal_date") or "")
        if (
            not candidate_key
            or not symbol
            or not security_id
            or candidate_key != f"{security_id}|{signal_date}"
        ):
            raise AuditedPITDevelopmentReplayError(
                "continuous ridge strict outcome identity is invalid"
            )
        candidate_keys.append(candidate_key)
        frame = frames_by_symbol.get(symbol)
        if frame is None:
            raise AuditedPITDevelopmentReplayError(
                "continuous ridge strict outcome symbol frame is missing"
            )
        realized, unresolved, event = _strict_close_stop_trade(
            adapter=adapter,
            verdict_cache=verdict_cache,
            frame=frame,
            symbol=symbol,
            signal_index=int(candidate["signal_frame_index"]),
            sessions=session_dates,
            session_positions=session_positions,
            suspension_evidence=suspension_evidence,
            terminal_listing_evidence=terminal_listing_evidence,
            hold_days=int(CONTINUOUS_RIDGE_OOF_SPEC["hold_days"]),
            stop_loss_pct=float(
                CONTINUOUS_RIDGE_OOF_SPEC["close_stop_loss_pct"]
            ),
            prevalidated_date_positions=date_positions_by_symbol[symbol],
        )
        status = str(event.get("status") or "")
        if status not in {"candidate_built", "entered_unresolved_exit"}:
            raise AuditedPITDevelopmentReplayError(
                "preflight-executable continuous ridge candidate failed "
                "during outcome replay"
            )
        if str(event.get("entry_date") or "") != str(
            candidate.get("entry_date") or ""
        ):
            raise AuditedPITDevelopmentReplayError(
                "continuous ridge outcome entry differs from preflight"
            )
        status_counts[status] += 1
        events.append(event)
        metadata = {
            **candidate,
            "candidate_key": candidate_key,
            "signal_date": signal_date,
            "symbol": symbol,
            "security_id": security_id,
            "name": str(candidate.get("name") or ""),
            "action": "BUY",
            "market_level": "audited_pit_development",
            "signal_tags": [CONTINUOUS_RIDGE_OOF_SPEC["signal_tag"]],
            "current_universe_bias": False,
        }
        prediction = candidate.get("predicted_net_return_pct")
        if prediction is not None:
            score = float(prediction)
            if not math.isfinite(score):
                raise ValueError(
                    "continuous ridge strict outcome score is nonfinite"
                )
            metadata.update(
                {
                    "score": score,
                    "rank_score": score,
                    "predicted_net_return_pct": score,
                }
            )
        if realized is not None:
            completed.append({**metadata, **realized})
        if unresolved is not None:
            censored.append({**metadata, **unresolved})
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("continuous ridge strict outcomes are duplicated")
    completed.sort(
        key=lambda item: (
            str(item["signal_date"]),
            str(item["security_id"]),
        )
    )
    censored.sort(
        key=lambda item: (
            str(item["signal_date"]),
            str(item["security_id"]),
        )
    )
    receipt = {
        "schema_version": "ranked-liquidity-ridge-strict-outcome/v2",
        "input_executable_candidate_count": len(ordered),
        "input_candidate_keys_sha256": _sha256(candidate_keys),
        "status_counts": dict(sorted(status_counts.items())),
        "execution_event_count": len(events),
        "execution_events": events,
        "execution_events_sha256": _sha256(events),
        "completed_candidate_count": len(completed),
        "completed_candidates_sha256": _sha256(completed),
        "right_censored_position_count": len(censored),
        "right_censored_positions_sha256": _sha256(censored),
        "verdict_cache_count": len(verdict_cache),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return completed, censored, receipt


def _strict_execution_dataset(
    candidates: Sequence[Mapping[str, Any]],
    *,
    frames_by_symbol: Mapping[str, pd.DataFrame],
    sessions: Sequence[str],
    adapter: Any,
    suspension_evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
    terminal_listing_evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    tail_candidates, tail_receipt = _apply_uniform_tail_cutoff(
        candidates,
        sessions=sessions,
        family=str(CONTINUOUS_RIDGE_OOF_SPEC["signal_tag"]),
    )
    executable, entry_receipt, verdict_cache = (
        _preflight_strict_entries(
            tail_candidates,
            frames_by_symbol=frames_by_symbol,
            sessions=sessions,
            adapter=adapter,
        )
    )
    completed, censored, outcome_receipt = _build_strict_outcomes(
        executable,
        frames_by_symbol=frames_by_symbol,
        sessions=sessions,
        adapter=adapter,
        verdict_cache=verdict_cache,
        suspension_evidence=suspension_evidence,
        terminal_listing_evidence=terminal_listing_evidence,
    )
    return {
        "tail_candidates": tail_candidates,
        "executable_candidates": executable,
        "completed_candidates": completed,
        "right_censored_positions": censored,
        "tail_cutoff_receipt": tail_receipt,
        "entry_preflight_receipt": entry_receipt,
        "outcome_receipt": outcome_receipt,
        "verdict_cache_count": len(verdict_cache),
    }


def _evaluate_fixed_oof(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    evaluation_session_dates: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_dates = _ordered_sessions(evaluation_session_dates)
    allowed_censor_reasons = {
        "no_strict_sell_fill_through_coverage_end",
        "terminal_listing_without_settlement_evidence",
    }
    selection_candidates = [dict(candidate) for candidate in candidates]
    if any(
        candidate.get("right_censored") is True
        and candidate.get("censor_reason") not in allowed_censor_reasons
        for candidate in selection_candidates
    ):
        raise AuditedPITDevelopmentReplayError(
            "continuous ridge evaluation received an unknown censor reason"
        )
    selection = CONTINUOUS_RIDGE_OOF_SPEC["selection"]
    selected, selection_receipt = _select_with_industry_cap_receipt(
        selection_candidates,
        rank_mode=rank_mode,
        top_n=int(selection["top_n"]),
        max_active_positions=int(selection["max_active_positions"]),
    )
    selected_censored = [
        trade for trade in selected if trade.get("right_censored") is True
    ]
    selected_complete = [
        trade for trade in selected if trade.get("right_censored") is not True
    ]
    metrics = _trade_metrics(
        selected_complete,
        hold_days=int(CONTINUOUS_RIDGE_OOF_SPEC["hold_days"]),
        max_active_positions=int(selection["max_active_positions"]),
        exposure_multiplier=float(
            CONTINUOUS_RIDGE_OOF_SPEC["exposure_multiplier"]
        ),
        annual_financing_rate_pct=float(
            CONTINUOUS_RIDGE_OOF_SPEC["annual_financing_rate_pct"]
        ),
        roundtrip_cost_bps=float(
            CONTINUOUS_RIDGE_OOF_SPEC["roundtrip_cost_bps"]
        ),
        slippage_bps=float(CONTINUOUS_RIDGE_OOF_SPEC["slippage_bps"]),
        capital_model=str(CONTINUOUS_RIDGE_OOF_SPEC["capital_model"]),
        evaluation_start_date=session_dates[0],
        evaluation_end_date=session_dates[-1],
        evaluation_session_dates=session_dates,
    )
    thresholds = CONTINUOUS_RIDGE_OOF_SPEC["advancement_thresholds"]
    sample_pass = len(selected_complete) >= int(
        thresholds["minimum_complete_trades"]
    )
    evidence_complete = not selected_censored
    raw_gate_metrics = (
        metrics.get("gate_metric_basis") == "unrounded_float64"
    )
    win_rate = metrics.get("trade_win_rate_pct_raw")
    full_drawdown = metrics.get("portfolio_max_drawdown_pct_raw")
    full_profit_factor = metrics.get("trade_profit_factor_raw")
    full_quality_pass = bool(
        raw_gate_metrics
        and sample_pass
        and win_rate is not None
        and float(win_rate)
        >= float(thresholds["minimum_full_win_rate_pct"])
        and full_drawdown is not None
        and abs(float(full_drawdown))
        <= float(thresholds["maximum_full_drawdown_pct"])
        and full_profit_factor is not None
        and float(full_profit_factor)
        >= float(thresholds["minimum_full_profit_factor"])
    )
    latest_return = metrics.get("rolling_1y_latest_return_pct_raw")
    latest_calmar = metrics.get("calmar_latest_12m_raw")
    latest_window_pass = bool(
        raw_gate_metrics
        and metrics.get("rolling_1y_latest_full_window")
        and latest_return is not None
        and float(latest_return)
        >= float(thresholds["minimum_latest_365d_return_pct"])
        and latest_calmar is not None
        and float(latest_calmar)
        >= float(thresholds["minimum_latest_365d_calmar"])
    )
    windows = metrics.get("rolling_1y_windows") or []
    rolling_stability_pass = bool(
        windows
        and raw_gate_metrics
        and all(
            window.get("return_pct_raw") is not None
            and float(window["return_pct_raw"])
            >= float(
                thresholds["all_complete_365d_minimum_return_pct"]
            )
            and window.get("max_drawdown_pct_raw") is not None
            and abs(float(window["max_drawdown_pct_raw"]))
            <= float(
                thresholds["all_complete_365d_maximum_drawdown_pct"]
            )
            and window.get("payoff_ratio_raw") is not None
            and float(window["payoff_ratio_raw"])
            >= float(
                thresholds["all_complete_365d_minimum_payoff_ratio"]
            )
            and window.get("profit_factor_raw") is not None
            and float(window["profit_factor_raw"])
            >= float(
                thresholds["all_complete_365d_minimum_profit_factor"]
            )
            and window.get("calmar_raw") is not None
            and float(window["calmar_raw"])
            >= float(thresholds["all_complete_365d_minimum_calmar"])
            for window in windows
        )
    )
    target_all_pass = bool(
        evidence_complete and full_quality_pass and latest_window_pass
    )
    row = {
        "label": (
            f"{CONTINUOUS_RIDGE_OOF_SPEC['signal_tag']}|"
            f"{rank_mode}|all_market_levels"
        ),
        "required_signal_tags": [
            CONTINUOUS_RIDGE_OOF_SPEC["signal_tag"]
        ],
        "market_levels": [],
        **metrics,
        "rank_mode": rank_mode,
        "selection_candidate_count": len(selection_candidates),
        "selected_position_count": len(selected),
        "selected_complete_trade_count": len(selected_complete),
        "selected_right_censored_position_count": len(selected_censored),
        "selected_right_censored_trade_keys": [
            _selection_trade_key(trade) for trade in selected_censored
        ],
        "evidence_complete": evidence_complete,
        "target_minimum_sample_pass": sample_pass,
        "target_full_development_quality_pass": full_quality_pass,
        "target_latest_12m_pass": latest_window_pass,
        "target_rolling_12m_stability_pass": rolling_stability_pass,
        "target_all_pass": target_all_pass,
        "target_gap_1y_return_pct": (
            round(
                float(thresholds["minimum_latest_365d_return_pct"])
                - float(latest_return),
                2,
            )
            if latest_return is not None
            else None
        ),
    }
    sweep = {
        "schema_version": "strict-ranked-liquidity-ridge-fixed-oof/v2",
        "qualified_trade_count": len(selection_candidates)
        - sum(
            candidate.get("right_censored") is True
            for candidate in selection_candidates
        ),
        "right_censored_position_count": sum(
            candidate.get("right_censored") is True
            for candidate in selection_candidates
        ),
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


def _advancement_gate_passes(
    main_row: Mapping[str, Any],
    amount_baseline_row: Mapping[str, Any],
) -> bool:
    return bool(
        main_row.get("target_all_pass")
        and main_row.get("target_rolling_12m_stability_pass")
        and main_row.get("evidence_complete")
        and amount_baseline_row.get("evidence_complete")
    )


def _compact_receipt_summary(
    receipt: Mapping[str, Any],
    *,
    omitted_fields: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    full = dict(receipt)
    full_receipt_sha256 = str(full.get("receipt_sha256") or "")
    if len(full_receipt_sha256) != 64:
        raise ValueError("compact receipt requires a full receipt hash")
    full_without_self_hash = {
        key: value
        for key, value in full.items()
        if key != "receipt_sha256"
    }
    if _sha256(full_without_self_hash) != full_receipt_sha256:
        raise ValueError("compact receipt full receipt hash is invalid")
    omitted_summary: dict[str, dict[str, Any]] = {}
    for field in sorted(omitted_fields):
        specification = omitted_fields[field]
        count_field = str(specification.get("count_field") or "")
        sha256_field = str(specification.get("sha256_field") or "")
        values = full.get(field)
        count = full.get(count_field)
        content_sha256 = str(full.get(sha256_field) or "")
        if (
            not isinstance(values, list)
            or not count_field
            or not sha256_field
            or int(count) != len(values)
            or len(content_sha256) != 64
        ):
            raise ValueError("compact receipt omitted evidence is invalid")
        if _sha256(values) != content_sha256:
            raise ValueError(
                "compact receipt omitted evidence hash is invalid"
            )
        omitted_summary[field] = {
            "count": len(values),
            "sha256": content_sha256,
        }
    summary = {
        key: value
        for key, value in full.items()
        if key not in {*omitted_fields, "receipt_sha256"}
    }
    summary.update(
        {
            "compact_summary_schema_version": (
                "ranked-liquidity-compact-receipt-summary/v2"
            ),
            "full_receipt_sha256": full_receipt_sha256,
            "omitted_fields": omitted_summary,
        }
    )
    summary["summary_receipt_sha256"] = _sha256(summary)
    return summary


def _verify_compact_receipt_summary(
    receipt: Mapping[str, Any],
) -> None:
    summary = dict(receipt)
    expected = str(summary.pop("summary_receipt_sha256", "") or "")
    if (
        summary.get("compact_summary_schema_version")
        != "ranked-liquidity-compact-receipt-summary/v2"
        or len(str(summary.get("full_receipt_sha256") or "")) != 64
        or not isinstance(summary.get("omitted_fields"), Mapping)
        or not expected
        or _sha256(summary) != expected
    ):
        raise ValueError("compact receipt summary verification failed")
    if "receipt_sha256" in summary:
        raise ValueError("compact receipt summary retained a full self hash")
    for field, evidence in summary["omitted_fields"].items():
        if (
            field in summary
            or not isinstance(evidence, Mapping)
            or int(evidence.get("count", -1)) < 0
            or len(str(evidence.get("sha256") or "")) != 64
        ):
            raise ValueError("compact receipt summary evidence is invalid")


def _producer_binding() -> dict[str, Any]:
    base = _producer_code_binding()
    root = Path(__file__).resolve().parent
    module_names = (
        "artifact_outcome_evidence.py",
        "audited_pit_industry_residual_reversal.py",
        "audited_pit_trend_pullback.py",
        "current_pool_development_replay.py",
        "execution.py",
        "research_artifact_replay.py",
        "research_backtest.py",
        "research_common.py",
        "research_equity.py",
        "research_market_data.py",
        "research_portfolio.py",
        "research_security_code_transition.py",
        "research_sweep.py",
        "storage.py",
    )
    dependencies = [
        {
            "module": name,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
        for name in module_names
    ]
    identity = {
        "base_replay_root_sha256": base["root_sha256"],
        "strict_dependency_modules": dependencies,
        "ranked_liquidity_module_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "numpy_build_config": np.show_config(mode="dicts"),
        "pandas_version": pd.__version__,
        "pypdf_version": package_version("pypdf"),
    }
    return {
        "schema_version": "audited-pit-ranked-liquidity-producer/v2",
        **identity,
        "root_sha256": _sha256(identity),
    }


def _assert_producer_binding_unchanged(
    expected: Mapping[str, Any],
) -> None:
    if _producer_binding() != dict(expected):
        raise ValueError("continuous ridge producer code changed during replay")


def _write_result_bundle(
    output_dir: str | Path,
    *,
    main_payload: Mapping[str, Any],
    sidecar_payloads: Mapping[str, Mapping[str, Any]],
    expected_producer_code: Mapping[str, Any],
) -> dict[str, Any]:
    expected = dict(expected_producer_code)
    _assert_producer_binding_unchanged(expected)
    required_names = {"features", "models", "execution", "selection"}
    if set(sidecar_payloads) != required_names:
        raise ValueError(
            "continuous ridge result requires four exact sidecars"
        )
    main = dict(main_payload)
    strategy_sha256 = main.get("strategy_sha256")
    source = main.get("source")
    if not strategy_sha256 or not isinstance(source, Mapping):
        raise ValueError("continuous ridge main payload anchors are invalid")

    runtime_sidecars: dict[str, dict[str, Any]] = {}
    stable_sidecars: dict[str, dict[str, str]] = {}
    for name in sorted(required_names):
        _assert_producer_binding_unchanged(expected)
        payload = dict(sidecar_payloads[name])
        if (
            payload.get("strategy_sha256") != strategy_sha256
            or payload.get("source") != source
            or payload.get("producer_code") != expected
        ):
            raise ValueError(
                "continuous ridge sidecar anchors do not match main payload"
            )
        runtime = _write_content_addressed(
            Path(output_dir) / "sidecars",
            payload,
        )
        runtime_sidecars[name] = runtime
        stable_sidecars[name] = _stable_sidecar_reference(runtime)
    _assert_producer_binding_unchanged(expected)
    signed_main = {
        **main,
        "producer_code": expected,
        "sidecars": stable_sidecars,
    }
    artifact = _write_content_addressed(output_dir, signed_main)
    _assert_producer_binding_unchanged(expected)
    return {
        **signed_main,
        "artifact": artifact,
        "runtime_sidecars": runtime_sidecars,
    }


def run_audited_pit_ranked_liquidity_ridge_oof(
    *,
    settings: Settings,
    audited_pit_universe_path: str | Path,
    expected_coverage_audit_sha256: str,
    expected_artifact_root_sha256: str,
    temporal_contract_path: str | Path,
    expected_temporal_contract_sha256: str,
    security_code_transition_evidence_root: str | Path,
    expected_security_code_transition_contract_sha256: str,
    start_date: str,
    end_date: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    if not isinstance(settings, Settings):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity replay requires frozen settings"
        )
    _write_replay_progress(output_dir, "starting")
    _assert_shared_strict_execution_contract()
    producer_code = _producer_binding()
    contract = load_temporal_partition_contract(temporal_contract_path)
    if contract["contract_sha256"] != expected_temporal_contract_sha256:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity temporal contract hash mismatch"
        )
    assert_range_allowed(
        contract,
        "development",
        start_date,
        end_date,
        "backtest",
    )
    try:
        transition_evidence = load_security_code_transition_evidence(
            security_code_transition_evidence_root,
            expected_contract_sha256=(
                expected_security_code_transition_contract_sha256
            ),
        )
    except (
        OSError,
        TypeError,
        ValueError,
        SecurityCodeTransitionEvidenceError,
    ) as exc:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity security-code transition evidence failed"
        ) from exc
    transition_contract = transition_evidence["contract"]

    try:
        universe = AuditedPointInTimeUniverse.from_file(
            str(audited_pit_universe_path),
            expected_coverage_audit_sha256=(
                expected_coverage_audit_sha256
            ),
            expected_artifact_root_sha256=expected_artifact_root_sha256,
            expected_temporal_contract_sha256=(
                expected_temporal_contract_sha256
            ),
            expected_temporal_role="development",
        )
    except (OSError, TypeError, ValueError, PITReceiptError) as exc:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity audited PIT artifact verification failed"
        ) from exc

    try:
        if universe.start_date != start_date or universe.end_date != end_date:
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity artifact range differs from development"
            )
        sessions = _exact_membership_sessions(
            universe,
            start_date=start_date,
            end_date=end_date,
        )
        if len(sessions) != int(
            CONTINUOUS_RIDGE_OOF_SPEC["required_market_session_count"]
        ):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity market session count differs from freeze"
            )
        _write_replay_progress(
            output_dir,
            "artifact_verified",
            market_session_count=len(sessions),
        )
        connection = universe._require_open()
        bars, bar_loader_receipt = _load_ranked_liquidity_bars(
            connection,
            start_date=start_date,
            end_date=end_date,
            sessions=sessions,
            security_code_transition_contract=transition_contract,
        )
        _write_replay_progress(
            output_dir,
            "bars_loaded",
            bar_row_count=len(bars),
        )
        frames_by_symbol = _residual_frames_by_symbol(bars)
        features, feature_receipt = (
            _build_exact_cross_section_features(
                bars,
                sessions,
            )
        )
        verify_feature_receipt(features, feature_receipt)
        _write_replay_progress(
            output_dir,
            "features_built",
            feature_candidate_count=len(features),
        )
        feature_candidates = features.to_dict("records")
        tail_candidates, tail_receipt = _apply_uniform_tail_cutoff(
            feature_candidates,
            sessions=sessions,
            family=str(CONTINUOUS_RIDGE_OOF_SPEC["signal_tag"]),
        )
        tail_offset = int(
            tail_receipt["required_signal_to_exit_offset_sessions"]
        )
        allowed_session_count = len(sessions) - tail_offset
        if allowed_session_count <= 0:
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity tail cutoff removed every session"
            )
        tail_session_set = set(sessions[:allowed_session_count])
        tail_features = features[
            features["signal_date"].astype(str).isin(tail_session_set)
        ].copy()
        if len(tail_features) != int(tail_receipt["kept_count"]):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity tail feature pool differs from cutoff"
            )
        _write_replay_progress(
            output_dir,
            "tail_cutoff_applied",
            kept_candidate_count=len(tail_candidates),
            cut_candidate_count=int(tail_receipt["cut_count"]),
        )

        raw_suspension_evidence = _load_suspension_evidence(
            connection,
            start_date=start_date,
            end_date=end_date,
        )
        raw_terminal_listing_evidence = _load_terminal_listing_evidence(
            connection,
            start_date=start_date,
            end_date=end_date,
        )
        try:
            suspension_evidence = (
                remap_security_code_transition_suspension_evidence(
                    raw_suspension_evidence,
                    contract=transition_contract,
                )
            )
            terminal_listing_evidence = (
                remap_security_code_transition_terminal_evidence(
                    raw_terminal_listing_evidence,
                    contract=transition_contract,
                )
            )
        except SecurityCodeTransitionEvidenceError as exc:
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity outcome evidence remap failed"
            ) from exc
        base_adapter = ArtifactNativeReplayAdapter(
            universe,
            expected_temporal_contract_sha256=(
                expected_temporal_contract_sha256
            ),
                expected_temporal_role="development",
            )
        bulk_adapter, bulk_next_open_receipt = (
            _load_bulk_next_open_replay_adapter(
                connection,
                base_adapter=base_adapter,
                market_generation_refs=universe.manifest[
                    "market_generations"
                ]["refs"],
                sessions=sessions,
            )
        )
        _write_replay_progress(
            output_dir,
            "execution_evidence_loaded",
            execution_evidence_row_count=bulk_next_open_receipt[
                "evidence_row_count"
            ],
        )
        try:
            adapter = SecurityCodeTransitionReplayAdapter(
                bulk_adapter,
                contract=transition_contract,
            )
        except SecurityCodeTransitionEvidenceError as exc:
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity transition replay adapter failed"
            ) from exc
        executable_candidates, entry_receipt, verdict_cache = (
            _preflight_strict_entries(
                tail_candidates,
                frames_by_symbol=frames_by_symbol,
                sessions=sessions,
                adapter=adapter,
            )
        )
        completed_candidates, censored_positions, outcome_receipt = (
            _build_strict_outcomes(
                executable_candidates,
                frames_by_symbol=frames_by_symbol,
                sessions=sessions,
                adapter=adapter,
                verdict_cache=verdict_cache,
                suspension_evidence=suspension_evidence,
                terminal_listing_evidence=terminal_listing_evidence,
            )
        )
        execution = {
            "tail_candidates": tail_candidates,
            "executable_candidates": executable_candidates,
            "completed_candidates": completed_candidates,
            "right_censored_positions": censored_positions,
            "tail_cutoff_receipt": tail_receipt,
            "entry_preflight_receipt": entry_receipt,
            "outcome_receipt": outcome_receipt,
            "verdict_cache_count": len(verdict_cache),
        }
        _write_replay_progress(
            output_dir,
            "outcomes_built",
            completed_candidate_count=len(completed_candidates),
            right_censored_position_count=len(censored_positions),
        )
        suspension_evidence_sha256 = _sha256(
            [
                {
                    "symbol": key[0],
                    "trade_date": key[1],
                    "proofs": value,
                }
                for key, value in sorted(suspension_evidence.items())
            ]
        )
        terminal_listing_evidence_sha256 = _sha256(
            [
                terminal_listing_evidence[symbol]
                for symbol in sorted(terminal_listing_evidence)
            ]
        )
        source = {
            "coverage_audit_sha256": universe.coverage_audit_sha256,
            "artifact_root_sha256": universe.artifact_root_sha256,
            "temporal_contract_sha256": universe.temporal_contract_sha256,
            "temporal_role": universe.temporal_role,
            "market_session_count": len(sessions),
            "exact_membership_session_count": len(sessions),
            "market_scope": market_scope_contract(),
            "artifact_native_replay_contract_sha256": (
                adapter.contract_sha256
            ),
            "base_artifact_native_replay_contract_sha256": (
                base_adapter.contract_sha256
            ),
            "bulk_next_open_replay_contract_sha256": (
                bulk_adapter.contract_sha256
            ),
            "bulk_next_open_evidence_receipt_sha256": (
                bulk_next_open_receipt["receipt_sha256"]
            ),
            "security_code_transition_contract_sha256": (
                transition_evidence["contract_sha256"]
            ),
            "security_code_transition_evidence_receipt_sha256": (
                transition_evidence["receipt_sha256"]
            ),
            "security_code_transition_application_receipt_sha256": (
                bar_loader_receipt[
                    "security_code_transition_application_receipt_sha256"
                ]
            ),
            "bar_loader_receipt_sha256": bar_loader_receipt[
                "receipt_sha256"
            ],
            "feature_receipt_sha256": feature_receipt["receipt_sha256"],
            "full_session_suspension_evidence_count": len(
                suspension_evidence
            ),
            "full_session_suspension_evidence_sha256": (
                suspension_evidence_sha256
            ),
            "terminal_listing_evidence_count": len(
                terminal_listing_evidence
            ),
            "terminal_listing_evidence_sha256": (
                terminal_listing_evidence_sha256
            ),
            "terminal_listing_evidence_usage": (
                "outcome_censor_only_not_signal_or_settlement_return"
            ),
            "producer_code": producer_code,
        }
    finally:
        universe.close()

    outcome_candidates = [
        *execution["completed_candidates"],
        *execution["right_censored_positions"],
    ]
    outcome_candidates.sort(
        key=lambda item: (
            str(item["signal_date"]),
            str(item["security_id"]),
        )
    )
    scored_oof, oof_receipt = _build_purged_oof_scores(
        tail_features,
        outcome_candidates,
        sessions,
        minimum_training_sessions=int(
            CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
                "minimum_training_sessions"
            ]
        ),
        validation_sessions=int(
            CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
                "validation_sessions"
            ]
        ),
        require_nonempty_validation_folds=True,
    )
    expected_fold_ranges = _fold_ranges(
        sessions,
        minimum_training_sessions=int(
            CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
                "minimum_training_sessions"
            ]
        ),
        validation_sessions=int(
            CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
                "validation_sessions"
            ]
        ),
    )
    observed_fold_ranges = [
        (str(fold["validation_start"]), str(fold["validation_end"]))
        for fold in oof_receipt["folds"]
    ]
    if (
        len(expected_fold_ranges)
        != int(CONTINUOUS_RIDGE_OOF_SPEC["required_oof_fold_count"])
        or observed_fold_ranges != expected_fold_ranges
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF folds differ from frozen six-fold plan"
        )
    _write_replay_progress(
        output_dir,
        "oof_scored",
        oof_candidate_count=len(scored_oof),
        fold_count=len(oof_receipt["folds"]),
    )
    score_lookup = {
        str(row.candidate_key): float(row.predicted_net_return_pct)
        for row in scored_oof.itertuples(index=False)
    }
    if len(score_lookup) != len(scored_oof):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF score keys are duplicated"
        )
    scored_execution_candidates: list[dict[str, Any]] = []
    for candidate in outcome_candidates:
        candidate_key = str(candidate["candidate_key"])
        score = score_lookup.get(candidate_key)
        if score is None:
            continue
        scored_execution_candidates.append(
            {
                **candidate,
                "predicted_net_return_pct": score,
                "score": score,
                "rank_score": score,
            }
        )
    scored_execution_candidates.sort(
        key=lambda item: (
            str(item["signal_date"]),
            str(item["security_id"]),
        )
    )
    positive_candidates, positive_pool_receipt = _positive_score_pool(
        scored_execution_candidates
    )
    minimum_training_sessions = int(
        CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
            "minimum_training_sessions"
        ]
    )
    evaluation_sessions = sessions[minimum_training_sessions:]
    if not evaluation_sessions:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity evaluation session grid is empty"
        )
    main_sweep, main_selection_receipt = _evaluate_fixed_oof(
        positive_candidates,
        rank_mode="predicted_net_return",
        evaluation_session_dates=evaluation_sessions,
    )
    baseline_sweep, baseline_selection_receipt = _evaluate_fixed_oof(
        positive_candidates,
        rank_mode="signal_date_amount",
        evaluation_session_dates=evaluation_sessions,
    )
    selection_spec = CONTINUOUS_RIDGE_OOF_SPEC["selection"]
    main_selected, replayed_main_receipt = (
        _select_with_industry_cap_receipt(
            positive_candidates,
            rank_mode="predicted_net_return",
            top_n=int(selection_spec["top_n"]),
            max_active_positions=int(
                selection_spec["max_active_positions"]
            ),
        )
    )
    baseline_selected, replayed_baseline_receipt = (
        _select_with_industry_cap_receipt(
            positive_candidates,
            rank_mode="signal_date_amount",
            top_n=int(selection_spec["top_n"]),
            max_active_positions=int(
                selection_spec["max_active_positions"]
            ),
        )
    )
    if (
        replayed_main_receipt != main_selection_receipt
        or replayed_baseline_receipt != baseline_selection_receipt
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity selection replay differs from evaluation"
        )
    if (
        not isinstance(main_sweep.get("top"), list)
        or len(main_sweep["top"]) != 1
        or not isinstance(baseline_sweep.get("top"), list)
        or len(baseline_sweep["top"]) != 1
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity fixed evaluation must return one row"
        )
    main_row = main_sweep["top"][0]
    baseline_row = baseline_sweep["top"][0]
    advancement_gate = _advancement_gate_passes(
        main_row,
        baseline_row,
    )
    _write_replay_progress(
        output_dir,
        "selection_evaluated",
        positive_candidate_count=len(positive_candidates),
        advancement_gate_passed=advancement_gate,
    )
    selected_union = {
        _selection_trade_key(candidate): candidate
        for candidate in [*main_selected, *baseline_selected]
    }
    selected_evidence = [
        selected_union[key] for key in sorted(selected_union)
    ]
    strategy_sha256 = _sha256(CONTINUOUS_RIDGE_OOF_SPEC)

    sidecar_common = {
        "strategy_sha256": strategy_sha256,
        "source": source,
        "producer_code": producer_code,
    }
    feature_sidecar = {
        "schema_version": "ranked-liquidity-feature-sidecar/v2",
        **sidecar_common,
        "bar_loader_receipt": bar_loader_receipt,
        "feature_receipt": feature_receipt,
    }
    model_sidecar = {
        "schema_version": "ranked-liquidity-model-sidecar/v2",
        **sidecar_common,
        "oof_receipt": oof_receipt,
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": oof_receipt["oof_scores_sha256"],
    }
    entry_receipt = execution["entry_preflight_receipt"]
    outcome_receipt = execution["outcome_receipt"]
    execution_sidecar = {
        "schema_version": "ranked-liquidity-execution-sidecar/v2",
        **sidecar_common,
        "security_code_transition_evidence": transition_evidence,
        "bulk_next_open_evidence_receipt": bulk_next_open_receipt,
        "tail_cutoff_receipt": execution["tail_cutoff_receipt"],
        "entry_preflight_receipt": _compact_receipt_summary(
            entry_receipt,
            omitted_fields={
                "events": {
                    "count_field": "event_count",
                    "sha256_field": "events_sha256",
                }
            },
        ),
        "outcome_receipt": _compact_receipt_summary(
            outcome_receipt,
            omitted_fields={
                "execution_events": {
                    "count_field": "execution_event_count",
                    "sha256_field": "execution_events_sha256",
                }
            },
        ),
        "completed_candidate_count": len(
            execution["completed_candidates"]
        ),
        "completed_candidates_sha256": _sha256(
            execution["completed_candidates"]
        ),
        "right_censored_position_count": len(
            execution["right_censored_positions"]
        ),
        "right_censored_positions_sha256": _sha256(
            execution["right_censored_positions"]
        ),
    }
    selection_sidecar = {
        "schema_version": "ranked-liquidity-selection-sidecar/v2",
        **sidecar_common,
        "positive_pool_receipt": positive_pool_receipt,
        "main_selection_receipt": _compact_receipt_summary(
            main_selection_receipt,
            omitted_fields={
                "days": {
                    "count_field": "signal_day_count",
                    "sha256_field": "days_sha256",
                }
            },
        ),
        "amount_baseline_selection_receipt": _compact_receipt_summary(
            baseline_selection_receipt,
            omitted_fields={
                "days": {
                    "count_field": "signal_day_count",
                    "sha256_field": "days_sha256",
                }
            },
        ),
        "selected_evidence": selected_evidence,
        "selected_evidence_sha256": _sha256(selected_evidence),
        "main_sweep": main_sweep,
        "amount_baseline_sweep": baseline_sweep,
        "advancement_gate_passed": advancement_gate,
    }
    main_payload = {
        "schema_version": "ranked-liquidity-ridge-result/v2",
        "strategy_sha256": strategy_sha256,
        "strategy": {
            **CONTINUOUS_RIDGE_OOF_SPEC,
            "strategy_sha256": strategy_sha256,
        },
        "source": source,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "advancement_gate_passed": advancement_gate,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "feature_candidate_count": len(features),
        "feature_rows_sha256": feature_receipt["feature_rows_sha256"],
        "strict_outcome_candidate_count": len(outcome_candidates),
        "strict_outcome_candidates_sha256": _sha256(outcome_candidates),
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": oof_receipt["oof_scores_sha256"],
        "scored_execution_candidate_count": len(
            scored_execution_candidates
        ),
        "scored_execution_candidates_sha256": _sha256(
            scored_execution_candidates
        ),
        "positive_candidate_count": len(positive_candidates),
        "positive_candidates_sha256": _sha256(positive_candidates),
        "main_sweep": main_sweep,
        "amount_baseline_sweep": baseline_sweep,
        "advancement_gate": {
            "main_latest_and_full_quality_passed": bool(
                main_row.get("target_all_pass")
            ),
            "main_rolling_12m_stability_passed": bool(
                main_row.get("target_rolling_12m_stability_pass")
            ),
            "main_evidence_complete": bool(
                main_row.get("evidence_complete")
            ),
            "amount_baseline_evidence_complete": bool(
                baseline_row.get("evidence_complete")
            ),
            "all_required_gates_passed": advancement_gate,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
        "comparison": {
            "shared_positive_candidate_table": True,
            "shared_candidate_table_sha256": main_selection_receipt[
                "candidate_table_sha256"
            ],
            "independent_portfolio_replays": True,
            "same_execution_contract": True,
            "baseline_performance_is_advancement_gate": False,
            "baseline_evidence_completeness_is_advancement_gate": True,
        },
    }
    result = _write_result_bundle(
        output_dir,
        main_payload=main_payload,
        sidecar_payloads={
            "features": feature_sidecar,
            "models": model_sidecar,
            "execution": execution_sidecar,
            "selection": selection_sidecar,
        },
        expected_producer_code=producer_code,
    )
    _write_replay_progress(
        output_dir,
        "completed",
        artifact_sha256=result["artifact"]["artifact_sha256"],
        advancement_gate_passed=advancement_gate,
    )
    return result
