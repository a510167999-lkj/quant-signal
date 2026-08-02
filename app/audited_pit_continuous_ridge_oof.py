"""Strict audited-PIT continuous ridge out-of-fold research."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import gc
import hashlib
from importlib.metadata import version as package_version
import json
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
from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
    candidate_passes_gate,
    candidate_score,
    frozen_score_contract,
    score_evidence_payload,
    selection_rank_key as _contract_selection_rank_key,
    validate_selection_rank_mode,
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
    _canonical_json,
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

ROLLING_CONTINUOUS_RIDGE_OOF_SPEC = deepcopy(CONTINUOUS_RIDGE_OOF_SPEC)
ROLLING_CONTINUOUS_RIDGE_OOF_SPEC.update(
    {
        "schema_version": (
            "development-pit-cross-sectional-ranked-liquidity-"
            "ridge-rolling-oof/v3"
        ),
        "signal_tag": (
            "cross_sectional_ranked_liquidity_ridge_rolling_126_oof"
        ),
        "walk_forward": {
            "minimum_training_sessions": 126,
            "training_window_type": "trailing_frozen_signal_sessions",
            "training_window_sessions": 126,
            "validation_sessions": 63,
            "purge": (
                "complete_exit_date_strictly_before_validation_start"
            ),
            "folds": "continuous_non_overlapping_validation_windows",
        },
    }
)
_ROLLING_CONTINUOUS_RIDGE_OOF_SPEC_SHA256 = (
    "c5730311c3660cf4afa8fae5c442b80622c16cb4633bd26c5d5a889525fb3be4"
)


@dataclass(frozen=True, slots=True)
class ModelOOFAdapter:
    model_id: str
    score_contract: Mapping[str, Any]
    score_field: str
    build_scores: Any
    verify_receipt: Any


def resolve_model_oof_adapter(
    strategy_spec: Mapping[str, Any],
) -> ModelOOFAdapter:
    if (
        dict(strategy_spec) == ROLLING_CONTINUOUS_RIDGE_OOF_SPEC
        and _sha256(strategy_spec)
        == _ROLLING_CONTINUOUS_RIDGE_OOF_SPEC_SHA256
    ):
        return ModelOOFAdapter(
            model_id="continuous_ridge",
            score_contract=RIDGE_SCORE_CONTRACT,
            score_field="predicted_net_return_pct",
            build_scores=_build_purged_oof_scores,
            verify_receipt=verify_rolling_oof_receipt,
        )
    from app import audited_pit_shallow_gbdt as shallow_gbdt

    shallow_spec_sha256 = shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
    if (
        _sha256(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
        == shallow_spec_sha256
        and _sha256(strategy_spec) == shallow_spec_sha256
        and dict(strategy_spec) == shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
        and frozen_score_contract(
            strategy_spec["selection"]["score_contract"]
        )
        is SHALLOW_GBDT_SCORE_CONTRACT
    ):
        return ModelOOFAdapter(
            model_id="shallow_gbdt_utility_logit",
            score_contract=SHALLOW_GBDT_SCORE_CONTRACT,
            score_field="predicted_positive_utility_probability",
            build_scores=(
                shallow_gbdt.build_shallow_gbdt_rolling_oof_scores
            ),
            verify_receipt=(
                shallow_gbdt.verify_shallow_gbdt_rolling_oof_receipt
            ),
        )
    raise ValueError("ranked-liquidity model OOF strategy is not frozen")


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


def _first_nonfinite_json_path(value: Any, path: str) -> str | None:
    if isinstance(value, (float, np.floating)):
        return path if not math.isfinite(float(value)) else None
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _first_nonfinite_json_path(item, f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, item in enumerate(value):
            found = _first_nonfinite_json_path(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _audited_payload_sha256(value: Any, *, root_path: str) -> str:
    try:
        return _sha256(value)
    except ValueError as exc:
        nonfinite_path = _first_nonfinite_json_path(value, root_path)
        if nonfinite_path is None:
            raise
        raise AuditedPITDevelopmentReplayError(
            "audited payload contains a nonfinite float at "
            f"{nonfinite_path}"
        ) from exc


def _normalize_security_transition_feature_metadata(
    features: pd.DataFrame,
) -> None:
    fields = (
        "security_code_transition_id",
        "security_code_transition_contract_sha256",
    )
    if not set(fields).issubset(features.columns):
        raise AuditedPITDevelopmentReplayError(
            "continuous ridge transition metadata is missing"
        )
    for field in fields:
        values = features[field].astype(object)
        missing = pd.isna(values)
        present = values.loc[~missing].tolist()
        if field == "security_code_transition_id":
            valid = all(
                isinstance(value, str) and bool(value)
                for value in present
            )
        else:
            valid = all(
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
                for value in present
            )
        if not valid:
            raise AuditedPITDevelopmentReplayError(
                f"continuous ridge {field} is invalid"
            )
        values.loc[missing] = None
        features[field] = values
    transition_rows = features["security_code_transition_id"].notna()
    if bool(
        features.loc[
            transition_rows,
            "security_code_transition_contract_sha256",
        ].isna().any()
    ):
        raise AuditedPITDevelopmentReplayError(
            "continuous ridge transition id has no contract hash"
        )


def _write_replay_progress(
    output_dir: str | Path,
    stage: str,
    *,
    artifact_version: int = 2,
    **details: Any,
) -> None:
    if artifact_version not in {2, 3}:
        raise ValueError("ranked-liquidity artifact version is invalid")
    write_json(
        str(
            Path(output_dir)
            / f".ranked_liquidity_v{artifact_version}_progress.json"
        ),
        {
            "schema_version": (
                f"ranked-liquidity-replay-progress/v{artifact_version}"
            ),
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


def _sha256_canonical_sequence(values: Any) -> str:
    """Hash a JSON sequence without retaining the complete sequence in memory."""

    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for value in values:
        if not first:
            digest.update(b",")
        digest.update(_canonical_json(value))
        first = False
    digest.update(b"]")
    return digest.hexdigest()


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
    ordered_source_rows = frame.sort_values(
        ["date", "security_id", "source_ts_code"],
        kind="mergesort",
    )

    def source_row_payload(row: Any) -> dict[str, Any]:
        amount = float(row.amount)
        return {
            "date": str(row.date),
            "ts_code": str(row.ts_code),
            "source_ts_code": str(row.source_ts_code),
            "security_id": str(row.security_id),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "amount": (
                amount if math.isfinite(amount) else f"nonfinite:{amount}"
            ),
            "adj_factor": float(row.adj_factor),
            "membership_name": (
                "" if pd.isna(row.membership_name) else str(row.membership_name)
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

    source_rows_sha256 = _sha256_canonical_sequence(
        source_row_payload(row)
        for row in ordered_source_rows.itertuples(index=False)
    )
    del ordered_source_rows
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
        "source_rows_sha256": source_rows_sha256,
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
    _normalize_security_transition_feature_metadata(features)
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


def _continuous_net_label(
    gross_return_pct: float,
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
) -> float:
    value = float(gross_return_pct)
    if not math.isfinite(value):
        raise ValueError("continuous ridge gross return is nonfinite")
    return value - float(
        strategy_spec["label"]["friction_percentage_points"]
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
    training_window_sessions: int | None = None,
    validation_sessions: int = 63,
    require_nonempty_validation_folds: bool = False,
    receipt_schema_version: str = (
        "ranked-liquidity-ridge-purged-oof-receipt/v2"
    ),
    ridge_lambda: float | None = None,
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    session_dates = _ordered_sessions(sessions)
    if training_window_sessions is not None and (
        training_window_sessions <= 0
        or training_window_sessions > minimum_training_sessions
    ):
        raise ValueError(
            "continuous ridge rolling training window is invalid"
        )
    expected_receipt_schema = (
        "ranked-liquidity-ridge-purged-oof-receipt/v3"
        if training_window_sessions is not None
        else "ranked-liquidity-ridge-purged-oof-receipt/v2"
    )
    if receipt_schema_version != expected_receipt_schema:
        raise ValueError("continuous ridge OOF receipt schema is invalid")
    frozen_ridge_lambda = float(strategy_spec["model"]["ridge_lambda"])
    resolved_ridge_lambda = (
        frozen_ridge_lambda
        if ridge_lambda is None
        else float(ridge_lambda)
    )
    if not math.isclose(
        resolved_ridge_lambda,
        frozen_ridge_lambda,
        rel_tol=0.0,
        abs_tol=0.0,
    ):
        raise ValueError("continuous ridge lambda differs from frozen spec")
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
            "net_label": _continuous_net_label(
                gross_return,
                strategy_spec,
            ),
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
        validation_start_position = session_dates.index(validation_start)
        if training_window_sessions is None:
            training_window = session_dates[:validation_start_position]
        else:
            training_window = session_dates[
                validation_start_position - training_window_sessions :
                validation_start_position
            ]
            if len(training_window) != training_window_sessions:
                raise ValueError(
                    "continuous ridge rolling training window is incomplete"
                )
        training_window_set = set(training_window)
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
        window_candidate_keys: list[str] = []
        eligible_window_training_candidate_keys: list[str] = []
        purged_immature_candidate_keys: list[str] = []
        noncomplete_window_candidate_keys: list[str] = []
        for row in rows.itertuples(index=False):
            key = str(row.candidate_key)
            if str(row.signal_date) not in training_window_set:
                continue
            window_candidate_keys.append(key)
            outcome = completed.get(key)
            if outcome is None:
                noncomplete_window_candidate_keys.append(key)
                continue
            eligible_window_training_candidate_keys.append(key)
            if str(outcome["exit_date"]) >= validation_start:
                purged_immature_candidate_keys.append(key)
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
        window_candidate_keys.sort()
        eligible_window_training_candidate_keys.sort()
        purged_immature_candidate_keys.sort()
        noncomplete_window_candidate_keys.sort()
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
            ridge_lambda=resolved_ridge_lambda,
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
        if training_window_sessions is not None:
            fold_receipt.update(
                {
                    "training_window_type": (
                        "trailing_frozen_signal_sessions"
                    ),
                    "training_window_session_count": len(training_window),
                    "training_window_start": training_window[0],
                    "training_window_end": training_window[-1],
                    "training_window_sessions_sha256": _sha256(
                        training_window
                    ),
                    "window_candidate_count": len(
                        window_candidate_keys
                    ),
                    "window_candidate_keys_sha256": _sha256(
                        window_candidate_keys
                    ),
                    "eligible_window_training_candidate_count": len(
                        eligible_window_training_candidate_keys
                    ),
                    "eligible_window_training_candidate_keys_sha256": (
                        _sha256(eligible_window_training_candidate_keys)
                    ),
                    "purged_immature_candidate_count": len(
                        purged_immature_candidate_keys
                    ),
                    "purged_immature_candidate_keys_sha256": _sha256(
                        purged_immature_candidate_keys
                    ),
                    "noncomplete_window_candidate_count": len(
                        noncomplete_window_candidate_keys
                    ),
                    "noncomplete_window_candidate_keys_sha256": _sha256(
                        noncomplete_window_candidate_keys
                    ),
                }
            )
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
        "schema_version": receipt_schema_version,
        "minimum_training_sessions": int(minimum_training_sessions),
        "validation_sessions": int(validation_sessions),
        "purge": "complete_exit_date_strictly_before_validation_start",
        "fold_count": len(fold_receipts),
        "folds": fold_receipts,
        "folds_sha256": _sha256(fold_receipts),
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": _sha256(score_payload),
    }
    if training_window_sessions is not None:
        receipt.update(
            {
                "training_window_type": (
                    "trailing_frozen_signal_sessions"
                ),
                "training_window_sessions": int(training_window_sessions),
                "ridge_lambda": resolved_ridge_lambda,
                "frozen_signal_sessions": session_dates,
                "frozen_signal_sessions_sha256": _sha256(
                    session_dates
                ),
            }
        )
    receipt["receipt_sha256"] = _sha256(receipt)
    return scored_oof, receipt


def verify_rolling_oof_receipt(
    features: pd.DataFrame,
    outcomes: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    scored_oof: pd.DataFrame,
    receipt: Mapping[str, Any],
    *,
    minimum_training_sessions: int,
    training_window_sessions: int,
    validation_sessions: int,
) -> dict[str, Any]:
    try:
        session_dates = _ordered_sessions(sessions)
        required = {"candidate_key", "signal_date", *FEATURE_NAMES}
        if (
            training_window_sessions <= 0
            or training_window_sessions > minimum_training_sessions
            or not required.issubset(features.columns)
        ):
            raise ValueError
        rows = features.copy()
        rows["candidate_key"] = rows["candidate_key"].astype(str)
        rows["signal_date"] = rows["signal_date"].astype(str)
        session_set = set(session_dates)
        if (
            rows["candidate_key"].duplicated().any()
            or rows["candidate_key"].eq("").any()
            or not rows["signal_date"].isin(session_set).all()
            or not np.isfinite(
                rows[list(FEATURE_NAMES)].to_numpy(dtype=float)
            ).all()
        ):
            raise ValueError
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
                raise ValueError
            outcome_lookup[key] = outcome
        friction = float(
            ROLLING_CONTINUOUS_RIDGE_OOF_SPEC["label"][
                "friction_percentage_points"
            ]
        )
        ridge_lambda = float(
            ROLLING_CONTINUOUS_RIDGE_OOF_SPEC["model"]["ridge_lambda"]
        )
        completed: dict[str, dict[str, Any]] = {}
        for key, outcome in outcome_lookup.items():
            if outcome.get("right_censored") is True:
                continue
            exit_date = outcome.get("exit_date")
            gross_return = float(outcome.get("return_pct"))
            if (
                _strict_iso_date(exit_date) is None
                or exit_date not in session_set
                or exit_date <= feature_signal_dates[key]
                or not math.isfinite(gross_return)
            ):
                raise ValueError
            completed[key] = {
                "exit_date": str(exit_date),
                "net_label": gross_return - friction,
            }

        fold_receipts: list[dict[str, Any]] = []
        expected_score_payload: list[dict[str, Any]] = []
        for fold_index, start_position in enumerate(
            range(
                minimum_training_sessions,
                len(session_dates),
                validation_sessions,
            ),
            start=1,
        ):
            validation_start = session_dates[start_position]
            validation_end = session_dates[
                min(
                    start_position + validation_sessions - 1,
                    len(session_dates) - 1,
                )
            ]
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
                raise ValueError
            training_window = session_dates[
                start_position - training_window_sessions : start_position
            ]
            if len(training_window) != training_window_sessions:
                raise ValueError
            training_window_set = set(training_window)
            window_candidate_keys: list[str] = []
            eligible_candidate_keys: list[str] = []
            immature_candidate_keys: list[str] = []
            noncomplete_candidate_keys: list[str] = []
            training_records: list[dict[str, Any]] = []
            for row in rows.itertuples(index=False):
                key = str(row.candidate_key)
                signal_date = str(row.signal_date)
                if signal_date not in training_window_set:
                    continue
                window_candidate_keys.append(key)
                outcome = completed.get(key)
                if outcome is None:
                    noncomplete_candidate_keys.append(key)
                    continue
                eligible_candidate_keys.append(key)
                if outcome["exit_date"] >= validation_start:
                    immature_candidate_keys.append(key)
                    continue
                training_records.append(
                    {
                        "candidate_key": key,
                        "signal_date": signal_date,
                        "exit_date": outcome["exit_date"],
                        "net_label": float(outcome["net_label"]),
                        "features": [
                            float(getattr(row, name))
                            for name in FEATURE_NAMES
                        ],
                    }
                )
            training_records.sort(
                key=lambda item: (
                    item["signal_date"],
                    item["candidate_key"],
                )
            )
            window_candidate_keys.sort()
            eligible_candidate_keys.sort()
            immature_candidate_keys.sort()
            noncomplete_candidate_keys.sort()
            if not training_records:
                raise ValueError

            x_train = np.asarray(
                [item["features"] for item in training_records],
                dtype=float,
            )
            y_train = np.asarray(
                [item["net_label"] for item in training_records],
                dtype=float,
            )
            training_dates = [
                item["signal_date"] for item in training_records
            ]
            date_counts = Counter(training_dates)
            weights = np.asarray(
                [1.0 / date_counts[value] for value in training_dates],
                dtype=float,
            )
            total_weight = float(weights.sum())
            mean = (
                x_train * weights[:, None]
            ).sum(axis=0) / total_weight
            centered = x_train - mean
            variance = (
                (centered**2 * weights[:, None]).sum(axis=0)
                / total_weight
            )
            scale = np.sqrt(variance)
            scale = np.where(scale > 1e-12, scale, 1.0)
            standardized = centered / scale
            design = np.column_stack(
                [
                    np.ones(len(standardized), dtype=float),
                    standardized,
                ]
            )
            sqrt_weights = np.sqrt(weights)
            weighted_design = design * sqrt_weights[:, None]
            weighted_target = y_train * sqrt_weights
            penalty = (
                np.eye(design.shape[1], dtype=float) * ridge_lambda
            )
            penalty[0, 0] = 0.0
            coefficients = np.linalg.solve(
                weighted_design.T @ weighted_design + penalty,
                weighted_design.T @ weighted_target,
            )
            validation_values = validation[
                list(FEATURE_NAMES)
            ].to_numpy(dtype=float)
            validation_design = np.column_stack(
                [
                    np.ones(len(validation_values), dtype=float),
                    (validation_values - mean) / scale,
                ]
            )
            scores = validation_design @ coefficients
            if not np.isfinite(scores).all():
                raise ValueError
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
            expected_score_payload.extend(score_rows)
            model_payload = {
                "mean": [float(value) for value in mean],
                "scale": [float(value) for value in scale],
                "coefficients": [
                    float(value) for value in coefficients
                ],
            }
            fold_receipt = {
                "fold": fold_index,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "training_candidate_count": len(training_records),
                "training_signal_date_count": len(set(training_dates)),
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
                "training_window_type": (
                    "trailing_frozen_signal_sessions"
                ),
                "training_window_session_count": len(training_window),
                "training_window_start": training_window[0],
                "training_window_end": training_window[-1],
                "training_window_sessions_sha256": _sha256(
                    training_window
                ),
                "window_candidate_count": len(window_candidate_keys),
                "window_candidate_keys_sha256": _sha256(
                    window_candidate_keys
                ),
                "eligible_window_training_candidate_count": len(
                    eligible_candidate_keys
                ),
                "eligible_window_training_candidate_keys_sha256": (
                    _sha256(eligible_candidate_keys)
                ),
                "purged_immature_candidate_count": len(
                    immature_candidate_keys
                ),
                "purged_immature_candidate_keys_sha256": _sha256(
                    immature_candidate_keys
                ),
                "noncomplete_window_candidate_count": len(
                    noncomplete_candidate_keys
                ),
                "noncomplete_window_candidate_keys_sha256": _sha256(
                    noncomplete_candidate_keys
                ),
            }
            fold_receipt["receipt_sha256"] = _sha256(fold_receipt)
            fold_receipts.append(fold_receipt)

        expected_score_payload.sort(
            key=lambda item: (
                item["signal_date"],
                item["candidate_key"],
            )
        )
        expected_receipt = {
            "schema_version": (
                "ranked-liquidity-ridge-purged-oof-receipt/v3"
            ),
            "minimum_training_sessions": int(
                minimum_training_sessions
            ),
            "validation_sessions": int(validation_sessions),
            "purge": (
                "complete_exit_date_strictly_before_validation_start"
            ),
            "fold_count": len(fold_receipts),
            "folds": fold_receipts,
            "folds_sha256": _sha256(fold_receipts),
            "oof_candidate_count": len(expected_score_payload),
            "oof_scores_sha256": _sha256(expected_score_payload),
            "training_window_type": (
                "trailing_frozen_signal_sessions"
            ),
            "training_window_sessions": int(training_window_sessions),
            "ridge_lambda": ridge_lambda,
            "frozen_signal_sessions": session_dates,
            "frozen_signal_sessions_sha256": _sha256(session_dates),
        }
        expected_receipt["receipt_sha256"] = _sha256(expected_receipt)
        observed_scores = scored_oof.sort_values(
            ["signal_date", "candidate_key"],
            kind="mergesort",
        ).reset_index(drop=True)
        observed_payload = [
            {
                "candidate_key": str(row.candidate_key),
                "signal_date": str(row.signal_date),
                "predicted_net_return_pct": float(
                    row.predicted_net_return_pct
                ),
            }
            for row in observed_scores.itertuples(index=False)
        ]
        if (
            dict(receipt) != expected_receipt
            or observed_payload != expected_score_payload
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "rolling OOF receipt verification failed"
        ) from exc
    return {
        "verified": True,
        "receipt_sha256": expected_receipt["receipt_sha256"],
        "fold_count": expected_receipt["fold_count"],
        "oof_candidate_count": expected_receipt["oof_candidate_count"],
    }


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


SCORED_EXECUTION_EVIDENCE_COLUMNS = (
    "candidate_key",
    "signal_date",
    "trade_key",
    "security_id",
    "signal_industry",
    "predicted_net_return_pct",
    "candidate_amount",
    "right_censored",
    "outcome_payload_sha256",
    "candidate_payload_sha256",
)


def _score_contract_metadata(
    score_contract: Mapping[str, Any],
) -> dict[str, Any]:
    frozen = frozen_score_contract(score_contract)
    if frozen is RIDGE_SCORE_CONTRACT:
        return {
            "field": "predicted_net_return_pct",
            "evidence_schema": "ranked-liquidity-score-evidence/v3",
            "pool_schema": "continuous-ridge-positive-score-pool/v1",
            "comparison": (
                "predicted_net_return_pct_strictly_greater_than_zero"
            ),
            "selection_schema": (
                "continuous-ridge-industry-selection-receipt/v1"
            ),
        }
    if frozen is SHALLOW_GBDT_SCORE_CONTRACT:
        return {
            "field": "predicted_positive_utility_probability",
            "evidence_schema": (
                "ranked-liquidity-shallow-gbdt-score-evidence/v1"
            ),
            "pool_schema": "shallow-gbdt-positive-utility-pool/v1",
            "comparison": (
                "predicted_positive_utility_probability_"
                "strictly_greater_than_0.5"
            ),
            "selection_schema": (
                "shallow-gbdt-industry-selection-receipt/v1"
            ),
        }
    raise ValueError("score contract must match a frozen contract")


def _outcome_payload_from_scored_candidate(
    candidate: Mapping[str, Any],
    *,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> dict[str, Any]:
    metadata = _score_contract_metadata(score_contract)
    candidate_score(candidate, contract=score_contract)
    payload = dict(candidate)
    for field in (
        metadata["field"],
        "score",
        "rank_score",
    ):
        payload.pop(field, None)
    return payload


def _compact_outcome_membership_evidence(
    completed_candidates: Sequence[Mapping[str, Any]],
    right_censored_positions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    completed = sorted(
        completed_candidates,
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("candidate_key") or ""),
        ),
    )
    censored = sorted(
        right_censored_positions,
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("candidate_key") or ""),
        ),
    )
    completed_hashes = [_sha256(candidate) for candidate in completed]
    censored_hashes = [_sha256(candidate) for candidate in censored]
    rows = sorted(
        [
            [
                str(candidate.get("candidate_key") or ""),
                False,
                payload_sha256,
            ]
            for candidate, payload_sha256 in zip(
                completed,
                completed_hashes,
            )
        ]
        + [
            [
                str(candidate.get("candidate_key") or ""),
                True,
                payload_sha256,
            ]
            for candidate, payload_sha256 in zip(
                censored,
                censored_hashes,
            )
        ],
        key=lambda row: row[0],
    )
    candidate_keys = [str(row[0]) for row in rows]
    completed_row_hashes = [row[2] for row in rows if row[1] is False]
    censored_row_hashes = [row[2] for row in rows if row[1] is True]
    ordered_union = sorted(
        [*completed, *censored],
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
        ),
    )
    if (
        not all(candidate_keys)
        or len(candidate_keys) != len(set(candidate_keys))
    ):
        raise ValueError("outcome membership keys are invalid")
    evidence = {
        "schema_version": "ranked-liquidity-outcome-membership/v3",
        "columns": [
            "candidate_key",
            "right_censored",
            "outcome_payload_sha256",
        ],
        "row_count": len(rows),
        "rows": rows,
        "rows_sha256": _sha256(rows),
        "completed_candidate_count": len(completed),
        "completed_candidates_sha256": _sha256(completed),
        "completed_candidate_payload_hashes_sha256": _sha256(
            completed_row_hashes
        ),
        "right_censored_position_count": len(censored),
        "right_censored_positions_sha256": _sha256(censored),
        "right_censored_position_payload_hashes_sha256": _sha256(
            censored_row_hashes
        ),
        "strict_outcome_candidate_payload_hashes_sha256": _sha256(
            [*completed_row_hashes, *censored_row_hashes]
        ),
        "strict_outcome_candidates_sha256": _sha256(ordered_union),
    }
    evidence["receipt_sha256"] = _sha256(evidence)
    return evidence


def _compact_scored_execution_evidence(
    candidates: Sequence[Mapping[str, Any]],
    *,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> dict[str, Any]:
    metadata = _score_contract_metadata(score_contract)
    score_field = str(metadata["field"])
    columns = (
        *SCORED_EXECUTION_EVIDENCE_COLUMNS[:5],
        score_field,
        *SCORED_EXECUTION_EVIDENCE_COLUMNS[6:],
    )
    ordered = sorted(
        candidates,
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("security_id") or ""),
            str(item.get("candidate_key") or ""),
        ),
    )
    rows: list[list[Any]] = []
    candidate_keys: list[str] = []
    trade_keys: list[str] = []
    payload_hashes: list[str] = []
    positive_payload_hashes: list[str] = []
    for candidate in ordered:
        candidate_key = str(candidate.get("candidate_key") or "")
        signal_date = str(candidate.get("signal_date") or "")
        trade_key = _selection_trade_key(candidate)
        security_id = str(candidate.get("security_id") or "")
        industry = str(candidate.get("signal_industry") or "").strip()
        score = candidate_score(candidate, contract=score_contract)
        amount = float(candidate.get("candidate_amount"))
        if (
            not candidate_key
            or not signal_date
            or not security_id
            or not industry
            or not math.isfinite(score)
            or not math.isfinite(amount)
        ):
            raise ValueError("scored execution evidence row is invalid")
        payload_sha256 = _sha256(candidate)
        outcome_payload_sha256 = _sha256(
            _outcome_payload_from_scored_candidate(
                candidate,
                score_contract=score_contract,
            )
        )
        candidate_keys.append(candidate_key)
        trade_keys.append(trade_key)
        payload_hashes.append(payload_sha256)
        if candidate_passes_gate(candidate, contract=score_contract):
            positive_payload_hashes.append(payload_sha256)
        rows.append(
            [
                candidate_key,
                signal_date,
                trade_key,
                security_id,
                industry,
                score,
                amount,
                candidate.get("right_censored") is True,
                outcome_payload_sha256,
                payload_sha256,
            ]
        )
    if (
        len(candidate_keys) != len(set(candidate_keys))
        or len(trade_keys) != len(set(trade_keys))
    ):
        raise ValueError("scored execution evidence keys are duplicated")
    evidence = {
        "schema_version": metadata["evidence_schema"],
        "columns": list(columns),
        "row_count": len(rows),
        "rows": rows,
        "rows_sha256": _sha256(rows),
        "candidate_payload_hashes_sha256": _sha256(payload_hashes),
        "positive_candidate_payload_hashes_sha256": _sha256(
            positive_payload_hashes
        ),
    }
    evidence["receipt_sha256"] = _sha256(evidence)
    return evidence


def _positive_score_pool(
    candidates: Sequence[Mapping[str, Any]],
    *,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = _score_contract_metadata(score_contract)
    ordered = sorted(
        candidates,
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
        try:
            candidate_score(candidate, contract=score_contract)
        except ValueError as exc:
            if metadata["field"] == "predicted_net_return_pct":
                raise ValueError(
                    "continuous ridge prediction is nonfinite"
                ) from exc
            raise
        input_keys.append(key)
        if candidate_passes_gate(candidate, contract=score_contract):
            positive.append(dict(candidate))
            positive_keys.append(key)
    if len(input_keys) != len(set(input_keys)):
        raise ValueError("continuous ridge score pool keys are duplicated")
    receipt = {
        "schema_version": metadata["pool_schema"],
        "comparison": metadata["comparison"],
        "input_candidate_count": len(ordered),
        "input_candidate_keys_sha256": _sha256(input_keys),
        "positive_candidate_count": len(positive),
        "positive_candidate_keys_sha256": _sha256(positive_keys),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return positive, receipt


def _selection_candidate_table(
    candidates: Sequence[Mapping[str, Any]],
    *,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> list[dict[str, Any]]:
    metadata = _score_contract_metadata(score_contract)
    score_field = str(metadata["field"])
    rows = []
    for candidate in candidates:
        trade = dict(candidate)
        score = candidate_score(trade, contract=score_contract)
        amount = float(trade.get("candidate_amount"))
        industry = str(trade.get("signal_industry") or "").strip()
        security_id = str(trade.get("security_id") or "")
        if (
            not math.isfinite(score)
            or not candidate_passes_gate(
                trade,
                contract=score_contract,
            )
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
                score_field: score,
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
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> tuple[Any, ...]:
    _score_contract_metadata(score_contract)
    return _contract_selection_rank_key(
        trade,
        rank_mode=rank_mode,
        contract=score_contract,
    )


def _select_with_industry_cap_receipt(
    candidates: Sequence[Mapping[str, Any]],
    *,
    rank_mode: str,
    top_n: int,
    max_active_positions: int,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metadata = _score_contract_metadata(score_contract)
    validate_selection_rank_mode(
        rank_mode,
        contract=score_contract,
    )
    if top_n <= 0 or max_active_positions <= 0:
        raise ValueError("continuous ridge selection capacities are invalid")
    candidate_table = _selection_candidate_table(
        candidates,
        score_contract=score_contract,
    )
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
                score_contract=score_contract,
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
        "schema_version": metadata["selection_schema"],
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


def _assert_shared_strict_execution_contract(
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
) -> None:
    comparisons = (
        (
            strategy_spec[
                "planned_exit_signal_offset_sessions"
            ],
            INDUSTRY_RESIDUAL_SPEC[
                "planned_exit_signal_offset_sessions"
            ],
            "planned exit offset",
        ),
        (
            strategy_spec["hold_days"],
            INDUSTRY_RESIDUAL_SPEC["hold_days"],
            "hold days",
        ),
        (
            strategy_spec["close_stop_loss_pct"],
            INDUSTRY_RESIDUAL_SPEC["close_stop_loss_pct"],
            "close stop",
        ),
        (
            strategy_spec["entry_execution"],
            INDUSTRY_RESIDUAL_SPEC["entry_execution"],
            "entry execution",
        ),
        (
            strategy_spec["capital_model"],
            INDUSTRY_RESIDUAL_SPEC["capital_model"],
            "capital model",
        ),
        (
            strategy_spec["exposure_multiplier"],
            INDUSTRY_RESIDUAL_SPEC["exposure_multiplier"],
            "exposure multiplier",
        ),
        (
            strategy_spec["roundtrip_cost_bps"],
            INDUSTRY_RESIDUAL_SPEC["roundtrip_cost_bps"],
            "roundtrip cost",
        ),
        (
            strategy_spec["slippage_bps"],
            INDUSTRY_RESIDUAL_SPEC["slippage_bps"],
            "slippage",
        ),
        (
            strategy_spec["annual_financing_rate_pct"],
            INDUSTRY_RESIDUAL_SPEC["annual_financing_rate_pct"],
            "financing",
        ),
        (
            strategy_spec["blocked_sell_policy"],
            INDUSTRY_RESIDUAL_SPEC["blocked_sell_policy"],
            "blocked sell",
        ),
        (
            strategy_spec["terminal_listing_policy"],
            INDUSTRY_RESIDUAL_SPEC["terminal_listing_policy"],
            "terminal listing",
        ),
        (
            strategy_spec["entry_execution"],
            TREND_PULLBACK_SPEC["entry_execution"],
            "strict trade core entry execution",
        ),
        (
            strategy_spec["hold_days"],
            TREND_PULLBACK_SPEC["hold_days"],
            "strict trade core hold days",
        ),
        (
            strategy_spec["close_stop_loss_pct"],
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
        float(strategy_spec["roundtrip_cost_bps"])
        + 2.0 * float(strategy_spec["slippage_bps"])
    ) / 100.0
    actual_label_friction = float(
        strategy_spec["label"][
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
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _assert_shared_strict_execution_contract(strategy_spec)
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
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[tuple[str, str, str], dict[str, Any]],
]:
    _assert_shared_strict_execution_contract(strategy_spec)
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
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
    receipt_schema_version: str = (
        "ranked-liquidity-ridge-strict-outcome/v2"
    ),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _assert_shared_strict_execution_contract(strategy_spec)
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
        executable_candidates,
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
            hold_days=int(strategy_spec["hold_days"]),
            stop_loss_pct=float(
                strategy_spec["close_stop_loss_pct"]
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
            "signal_tags": [strategy_spec["signal_tag"]],
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
        "schema_version": receipt_schema_version,
        "input_executable_candidate_count": len(ordered),
        "input_candidate_keys_sha256": _sha256(candidate_keys),
        "status_counts": dict(sorted(status_counts.items())),
        "execution_event_count": len(events),
        "execution_events": events,
        "execution_events_sha256": _sha256(events),
        "completed_candidate_count": len(completed),
        "completed_candidates_sha256": _audited_payload_sha256(
            completed,
            root_path="$.completed_candidates",
        ),
        "right_censored_position_count": len(censored),
        "right_censored_positions_sha256": _audited_payload_sha256(
            censored,
            root_path="$.right_censored_positions",
        ),
        "verdict_cache_count": len(verdict_cache),
    }
    if receipt_schema_version.endswith("/v3"):
        completed_payload_hashes = [
            _sha256(candidate)
            for candidate in sorted(
                completed,
                key=lambda item: str(
                    item.get("candidate_key") or ""
                ),
            )
        ]
        censored_payload_hashes = [
            _sha256(candidate)
            for candidate in sorted(
                censored,
                key=lambda item: str(
                    item.get("candidate_key") or ""
                ),
            )
        ]
        receipt.update(
            {
                "completed_candidate_payload_hashes_sha256": _sha256(
                    completed_payload_hashes
                ),
                "right_censored_position_payload_hashes_sha256": (
                    _sha256(censored_payload_hashes)
                ),
                "strict_outcome_candidate_payload_hashes_sha256": (
                    _sha256(
                        [
                            *completed_payload_hashes,
                            *censored_payload_hashes,
                        ]
                    )
                ),
            }
        )
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
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
    outcome_receipt_schema_version: str = (
        "ranked-liquidity-ridge-strict-outcome/v2"
    ),
) -> dict[str, Any]:
    tail_kwargs: dict[str, Any] = {
        "sessions": sessions,
        "family": str(strategy_spec["signal_tag"]),
    }
    if strategy_spec is not CONTINUOUS_RIDGE_OOF_SPEC:
        tail_kwargs["strategy_spec"] = strategy_spec
    tail_candidates, tail_receipt = _apply_uniform_tail_cutoff(
        candidates,
        **tail_kwargs,
    )
    executable, entry_receipt, verdict_cache = (
        _preflight_strict_entries(
            tail_candidates,
            frames_by_symbol=frames_by_symbol,
            sessions=sessions,
            adapter=adapter,
            strategy_spec=strategy_spec,
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
        strategy_spec=strategy_spec,
        receipt_schema_version=outcome_receipt_schema_version,
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
    strategy_spec: Mapping[str, Any] = CONTINUOUS_RIDGE_OOF_SPEC,
    sweep_schema_version: str = "strict-ranked-liquidity-ridge-fixed-oof/v2",
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    frozen_contract = frozen_score_contract(score_contract)
    validate_selection_rank_mode(
        rank_mode,
        contract=score_contract,
    )
    allowed_sweep_schemas = (
        {
            "strict-ranked-liquidity-ridge-fixed-oof/v2",
            "strict-ranked-liquidity-ridge-fixed-oof/v3",
        }
        if frozen_contract is RIDGE_SCORE_CONTRACT
        else {"strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"}
    )
    if sweep_schema_version not in allowed_sweep_schemas:
        raise ValueError(
            "fixed OOF sweep schema differs from score contract"
        )
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
    selection = strategy_spec["selection"]
    selected, selection_receipt = _select_with_industry_cap_receipt(
        selection_candidates,
        rank_mode=rank_mode,
        top_n=int(selection["top_n"]),
        max_active_positions=int(selection["max_active_positions"]),
        score_contract=score_contract,
    )
    selected_censored = [
        trade for trade in selected if trade.get("right_censored") is True
    ]
    selected_complete = [
        trade for trade in selected if trade.get("right_censored") is not True
    ]
    metrics = _trade_metrics(
        selected_complete,
        hold_days=int(strategy_spec["hold_days"]),
        max_active_positions=int(selection["max_active_positions"]),
        exposure_multiplier=float(
            strategy_spec["exposure_multiplier"]
        ),
        annual_financing_rate_pct=float(
            strategy_spec["annual_financing_rate_pct"]
        ),
        roundtrip_cost_bps=float(
            strategy_spec["roundtrip_cost_bps"]
        ),
        slippage_bps=float(strategy_spec["slippage_bps"]),
        capital_model=str(strategy_spec["capital_model"]),
        evaluation_start_date=session_dates[0],
        evaluation_end_date=session_dates[-1],
        evaluation_session_dates=session_dates,
    )
    thresholds = strategy_spec["advancement_thresholds"]
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
            f"{strategy_spec['signal_tag']}|"
            f"{rank_mode}|all_market_levels"
        ),
        "required_signal_tags": [
            strategy_spec["signal_tag"]
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
        "schema_version": sweep_schema_version,
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


def _recompute_fixed_oof_gate(
    sweep: Mapping[str, Any],
    selection_receipt: Mapping[str, Any],
    candidate_table: Sequence[Mapping[str, Any]],
    *,
    strategy_spec: Mapping[str, Any],
    rank_mode: str,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> dict[str, bool]:
    frozen_contract = frozen_score_contract(score_contract)
    metadata = _score_contract_metadata(score_contract)
    validate_selection_rank_mode(
        rank_mode,
        contract=score_contract,
    )
    for candidate in candidate_table:
        candidate_score(candidate, contract=score_contract)
    top = sweep.get("top")
    if not isinstance(top, list) or len(top) != 1:
        raise ValueError("fixed OOF sweep must contain one result row")
    row = dict(top[0])
    selected_keys = list(selection_receipt["selected_trade_keys"])
    candidate_by_trade_key = {
        str(candidate["trade_key"]): dict(candidate)
        for candidate in candidate_table
    }
    if (
        len(candidate_by_trade_key) != len(candidate_table)
        or any(key not in candidate_by_trade_key for key in selected_keys)
    ):
        raise ValueError("fixed OOF selection keys are invalid")
    selected_censored_keys = [
        key
        for key in selected_keys
        if candidate_by_trade_key[key]["right_censored"] is True
    ]
    selected_complete_count = len(selected_keys) - len(
        selected_censored_keys
    )
    all_censored_count = sum(
        candidate["right_censored"] is True
        for candidate in candidate_table
    )
    evidence_complete = not selected_censored_keys
    thresholds = strategy_spec["advancement_thresholds"]
    raw_gate_metrics = (
        row.get("gate_metric_basis") == "unrounded_float64"
    )
    win_rate = row.get("trade_win_rate_pct_raw")
    full_drawdown = row.get("portfolio_max_drawdown_pct_raw")
    full_profit_factor = row.get("trade_profit_factor_raw")
    sample_pass = selected_complete_count >= int(
        thresholds["minimum_complete_trades"]
    )
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
    latest_return = row.get("rolling_1y_latest_return_pct_raw")
    latest_calmar = row.get("calmar_latest_12m_raw")
    latest_window_pass = bool(
        raw_gate_metrics
        and row.get("rolling_1y_latest_full_window") is True
        and latest_return is not None
        and float(latest_return)
        >= float(thresholds["minimum_latest_365d_return_pct"])
        and latest_calmar is not None
        and float(latest_calmar)
        >= float(thresholds["minimum_latest_365d_calmar"])
    )
    windows = row.get("rolling_1y_windows")
    if not isinstance(windows, list):
        raise ValueError("fixed OOF rolling windows are invalid")
    rolling_stability_pass = bool(
        windows
        and raw_gate_metrics
        and all(
            window.get("return_pct_raw") is not None
            and float(window["return_pct_raw"])
            >= float(
                thresholds[
                    "all_complete_365d_minimum_return_pct"
                ]
            )
            and window.get("max_drawdown_pct_raw") is not None
            and abs(float(window["max_drawdown_pct_raw"]))
            <= float(
                thresholds[
                    "all_complete_365d_maximum_drawdown_pct"
                ]
            )
            and window.get("payoff_ratio_raw") is not None
            and float(window["payoff_ratio_raw"])
            >= float(
                thresholds[
                    "all_complete_365d_minimum_payoff_ratio"
                ]
            )
            and window.get("profit_factor_raw") is not None
            and float(window["profit_factor_raw"])
            >= float(
                thresholds[
                    "all_complete_365d_minimum_profit_factor"
                ]
            )
            and window.get("calmar_raw") is not None
            and float(window["calmar_raw"])
            >= float(
                thresholds["all_complete_365d_minimum_calmar"]
            )
            for window in windows
        )
    )
    target_all_pass = bool(
        evidence_complete
        and full_quality_pass
        and latest_window_pass
    )
    target_gap = (
        round(
            float(thresholds["minimum_latest_365d_return_pct"])
            - float(latest_return),
            2,
        )
        if latest_return is not None
        else None
    )
    expected_label = (
        f"{strategy_spec['signal_tag']}|"
        f"{rank_mode}|all_market_levels"
    )
    expected_sweep_schema = (
        "strict-ranked-liquidity-ridge-fixed-oof/v3"
        if frozen_contract is RIDGE_SCORE_CONTRACT
        else "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
    )
    if (
        selection_receipt.get("schema_version")
        != metadata["selection_schema"]
    ):
        raise ValueError(
            "fixed OOF selection receipt schema differs from score contract"
        )
    if (
        sweep.get("schema_version") != expected_sweep_schema
        or sweep.get("selection_candidate_count")
        != len(candidate_table)
        or sweep.get("qualified_trade_count")
        != len(candidate_table) - all_censored_count
        or sweep.get("right_censored_position_count")
        != all_censored_count
        or sweep.get("spec_count") != 1
        or sweep.get("evidence_complete") is not evidence_complete
        or sweep.get("target_all_pass_count") != int(target_all_pass)
        or sweep.get("target_rolling_12m_stability_pass_count")
        != int(rolling_stability_pass)
        or selection_receipt["selected_count"] != len(selected_keys)
        or selection_receipt["parameters"]["rank_mode"] != rank_mode
        or row.get("label") != expected_label
        or row.get("required_signal_tags")
        != [strategy_spec["signal_tag"]]
        or row.get("market_levels") != []
        or row.get("rank_mode") != rank_mode
        or row.get("selection_candidate_count")
        != len(candidate_table)
        or row.get("selected_position_count") != len(selected_keys)
        or row.get("selected_complete_trade_count")
        != selected_complete_count
        or row.get("selected_right_censored_position_count")
        != len(selected_censored_keys)
        or row.get("selected_right_censored_trade_keys")
        != selected_censored_keys
        or row.get("evidence_complete") is not evidence_complete
        or row.get("target_minimum_sample_pass") is not sample_pass
        or row.get("target_full_development_quality_pass")
        is not full_quality_pass
        or row.get("target_latest_12m_pass")
        is not latest_window_pass
        or row.get("target_rolling_12m_stability_pass")
        is not rolling_stability_pass
        or row.get("target_all_pass") is not target_all_pass
        or row.get("target_gap_1y_return_pct") != target_gap
    ):
        raise ValueError("fixed OOF sweep gate verification failed")
    return {
        "target_all_pass": target_all_pass,
        "rolling_stability_pass": rolling_stability_pass,
        "evidence_complete": evidence_complete,
    }


def _verify_recomputed_trade_metrics(
    sweep: Mapping[str, Any],
    selected_trade_keys: Sequence[str],
    selected_evidence_by_trade_key: Mapping[str, Mapping[str, Any]],
    frozen_signal_sessions: Sequence[str],
    *,
    strategy_spec: Mapping[str, Any],
) -> None:
    selected_complete = [
        dict(selected_evidence_by_trade_key[trade_key])
        for trade_key in selected_trade_keys
        if selected_evidence_by_trade_key[trade_key].get(
            "right_censored"
        )
        is not True
    ]
    minimum_training_sessions = int(
        strategy_spec["walk_forward"]["minimum_training_sessions"]
    )
    evaluation_sessions = list(
        frozen_signal_sessions[minimum_training_sessions:]
    )
    if not evaluation_sessions:
        raise ValueError("fixed OOF metric session grid is empty")
    recomputed = _trade_metrics(
        selected_complete,
        hold_days=int(strategy_spec["hold_days"]),
        max_active_positions=int(
            strategy_spec["selection"]["max_active_positions"]
        ),
        exposure_multiplier=float(
            strategy_spec["exposure_multiplier"]
        ),
        annual_financing_rate_pct=float(
            strategy_spec["annual_financing_rate_pct"]
        ),
        roundtrip_cost_bps=float(
            strategy_spec["roundtrip_cost_bps"]
        ),
        slippage_bps=float(strategy_spec["slippage_bps"]),
        capital_model=str(strategy_spec["capital_model"]),
        evaluation_start_date=evaluation_sessions[0],
        evaluation_end_date=evaluation_sessions[-1],
        evaluation_session_dates=evaluation_sessions,
    )
    row = sweep["top"][0]
    if any(row.get(key) != value for key, value in recomputed.items()):
        raise ValueError("fixed OOF trade metrics replay failed")


def _replay_selection_summary(
    receipt: Mapping[str, Any],
    candidate_table: Sequence[Mapping[str, Any]],
    *,
    strategy_spec: Mapping[str, Any],
    rank_mode: str,
    score_contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> list[str]:
    metadata = _score_contract_metadata(score_contract)
    validate_selection_rank_mode(
        rank_mode,
        contract=score_contract,
    )
    summary = dict(receipt)
    _verify_compact_receipt_summary(summary)
    selection_spec = strategy_spec["selection"]
    top_n = int(selection_spec["top_n"])
    max_active_positions = int(
        selection_spec["max_active_positions"]
    )
    expected_parameters = {
        "rank_mode": rank_mode,
        "top_n": top_n,
        "max_active_positions": max_active_positions,
        "max_active_positions_per_industry": 1,
        "same_day_exit_before_signal_selection": True,
        "industry_source": "signal_date_frozen",
        "stable_identity": "security_id",
    }
    by_signal_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for compact_candidate in candidate_table:
        candidate = dict(compact_candidate)
        candidate_score(candidate, contract=score_contract)
        trade_key = str(candidate.get("trade_key") or "")
        trade_key_parts = trade_key.split("|")
        if (
            len(trade_key_parts) != 4
            or trade_key_parts[0] != candidate.get("security_id")
        ):
            raise ValueError("selection evidence trade key is invalid")
        candidate["signal_date"] = trade_key_parts[1]
        candidate["entry_date"] = trade_key_parts[2]
        candidate["exit_date"] = trade_key_parts[3]
        by_signal_date[trade_key_parts[1]].append(candidate)

    selected_trade_keys: list[str] = []
    active_positions: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    all_ordered_keys: list[str] = []
    for signal_date in sorted(by_signal_date):
        signal_day = date.fromisoformat(signal_date)
        active_positions = [
            trade
            for trade in active_positions
            if date.fromisoformat(str(trade["exit_date"])) > signal_day
        ]
        trades = sorted(
            by_signal_date[signal_date],
            key=lambda trade: _selection_rank_key(
                trade,
                rank_mode=rank_mode,
                score_contract=score_contract,
            ),
        )
        ordered_keys = [str(trade["trade_key"]) for trade in trades]
        if len(ordered_keys) != len(set(ordered_keys)):
            raise ValueError("daily selection keys are duplicated")
        all_ordered_keys.extend(ordered_keys)
        active_securities = {
            str(trade["security_id"]) for trade in active_positions
        }
        active_industries = {
            str(trade["signal_industry"]) for trade in active_positions
        }
        selected_today = 0
        selected_today_keys: list[str] = []
        decisions: list[dict[str, str]] = []
        for index, trade in enumerate(trades):
            trade_key = ordered_keys[index]
            security_id = str(trade["security_id"])
            industry = str(trade["signal_industry"])
            if security_id in active_securities:
                decisions.append(
                    {
                        "trade_key": trade_key,
                        "decision": "active_security",
                    }
                )
                continue
            if industry in active_industries:
                decisions.append(
                    {
                        "trade_key": trade_key,
                        "decision": "active_industry",
                    }
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
            active_positions.append(trade)
            active_securities.add(security_id)
            active_industries.add(industry)
            selected_trade_keys.append(trade_key)
            selected_today_keys.append(trade_key)
            selected_today += 1
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
                "ordered_candidate_root_sha256": _sha256(
                    ordered_keys
                ),
                "decisions": decisions,
                "selected_trade_keys": selected_today_keys,
            }
        )

    full_receipt_without_self_hash = {
        key: value
        for key, value in summary.items()
        if key
        not in {
            "compact_summary_schema_version",
            "full_receipt_sha256",
            "omitted_fields",
            "summary_receipt_sha256",
        }
    }
    full_receipt_without_self_hash["days"] = days
    if (
        summary.get("schema_version") != metadata["selection_schema"]
        or summary.get("parameters") != expected_parameters
        or summary.get("candidate_count") != len(candidate_table)
        or summary.get("candidate_table_sha256")
        != _sha256(candidate_table)
        or summary.get("ordered_candidate_trade_keys_sha256")
        != _sha256(all_ordered_keys)
        or summary.get("selected_count") != len(selected_trade_keys)
        or summary.get("selected_trade_keys") != selected_trade_keys
        or summary.get("selected_trade_keys_sha256")
        != _sha256(selected_trade_keys)
        or summary.get("signal_day_count") != len(days)
        or summary["omitted_fields"].get("days")
        != {"count": len(days), "sha256": _sha256(days)}
        or summary.get("full_receipt_sha256")
        != _sha256(full_receipt_without_self_hash)
    ):
        raise ValueError("selection summary replay failed")
    return selected_trade_keys


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


def _producer_binding(*, artifact_version: int = 2) -> dict[str, Any]:
    if artifact_version not in {2, 3}:
        raise ValueError("ranked-liquidity producer version is invalid")
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
    if artifact_version == 3:
        identity["artifact_version"] = artifact_version
    return {
        "schema_version": (
            f"audited-pit-ranked-liquidity-producer/v{artifact_version}"
        ),
        **identity,
        "root_sha256": _sha256(identity),
    }


def _assert_shallow_gbdt_entrypoints_frozen() -> None:
    from app import audited_pit_shallow_gbdt as shallow_gbdt

    if (
        shallow_gbdt.build_shallow_gbdt_rolling_oof_scores
        is not shallow_gbdt._FROZEN_BUILD_SHALLOW_GBDT_ROLLING_OOF_SCORES
        or shallow_gbdt.verify_shallow_gbdt_rolling_oof_receipt
        is not (
            shallow_gbdt
            ._FROZEN_VERIFY_SHALLOW_GBDT_ROLLING_OOF_RECEIPT
        )
    ):
        raise ValueError("shallow GBDT model entrypoints are not frozen")


def _shallow_gbdt_producer_binding() -> dict[str, Any]:
    from app import audited_pit_shallow_gbdt as shallow_gbdt

    _assert_shallow_gbdt_entrypoints_frozen()
    _, xgboost_runtime = shallow_gbdt._xgboost_runtime()
    ridge_binding = _producer_binding(artifact_version=3)
    root = Path(__file__).resolve().parent
    identity = {
        "schema_version": (
            "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
        ),
        "base_ranked_liquidity_producer_root_sha256": (
            ridge_binding["root_sha256"]
        ),
        "strict_dependency_modules": (
            ridge_binding["strict_dependency_modules"]
        ),
        "shared_ranked_liquidity_module_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "shallow_gbdt_module_sha256": hashlib.sha256(
            Path(shallow_gbdt.__file__).read_bytes()
        ).hexdigest(),
        "score_contract_module_sha256": hashlib.sha256(
            (root / "audited_pit_score_contract.py").read_bytes()
        ).hexdigest(),
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "numpy_build_config": np.show_config(mode="dicts"),
        "pandas_version": pd.__version__,
        "pypdf_version": package_version("pypdf"),
        "xgboost_version": xgboost_runtime["xgboost_version"],
        "xgboost_build_info": xgboost_runtime["xgboost_build_info"],
        "xgboost_parameters": dict(
            shallow_gbdt.FROZEN_XGBOOST_PARAMS
        ),
        "num_boost_round": shallow_gbdt.NUM_BOOST_ROUND,
        "score_contract_sha256": _sha256(
            dict(SHALLOW_GBDT_SCORE_CONTRACT)
        ),
        "strategy_sha256": (
            shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
        ),
    }
    return {
        **identity,
        "root_sha256": _sha256(identity),
    }


def resolve_ranked_liquidity_run_variant(
    strategy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        model_adapter = resolve_model_oof_adapter(strategy_spec)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "ranked-liquidity run variant must match a frozen strategy"
        ) from exc
    if model_adapter.model_id == "continuous_ridge":
        return {
            "strategy_schema_version": (
                "development-pit-cross-sectional-ranked-liquidity-"
                "ridge-rolling-oof/v3"
            ),
            "progress_file_name": (
                ".ranked_liquidity_v3_progress.json"
            ),
            "progress_schema_version": (
                "ranked-liquidity-replay-progress/v3"
            ),
            "producer_schema_version": (
                "audited-pit-ranked-liquidity-producer/v3"
            ),
            "result_schema_version": (
                "ranked-liquidity-ridge-result/v3"
            ),
            "sidecar_schema_versions": {
                name: f"ranked-liquidity-{name}-sidecar/v3"
                for name in (
                    "features",
                    "models",
                    "execution",
                    "selection",
                )
            },
            "strict_outcome_schema_version": (
                "ranked-liquidity-ridge-strict-outcome/v3"
            ),
            "sweep_schema_version": (
                "strict-ranked-liquidity-ridge-fixed-oof/v3"
            ),
            "model_adapter": model_adapter,
            "main_rank_mode": "predicted_net_return",
            "baseline_rank_mode": "signal_date_amount",
            "score_contract": dict(RIDGE_SCORE_CONTRACT),
            "producer_binding": (
                lambda: _producer_binding(artifact_version=3)
            ),
            "artifact_semantics_version": 3,
        }
    if model_adapter.model_id == "shallow_gbdt_utility_logit":
        _assert_shallow_gbdt_entrypoints_frozen()
        return {
            "strategy_schema_version": (
                "development-pit-cross-sectional-shallow-gbdt-"
                "utility-logit-rolling-126-oof/v1"
            ),
            "progress_file_name": (
                ".ranked_liquidity_shallow_gbdt_v1_progress.json"
            ),
            "progress_schema_version": (
                "ranked-liquidity-shallow-gbdt-replay-progress/v1"
            ),
            "producer_schema_version": (
                "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
            ),
            "result_schema_version": (
                "ranked-liquidity-shallow-gbdt-result/v1"
            ),
            "sidecar_schema_versions": {
                name: (
                    f"ranked-liquidity-shallow-gbdt-{name}-sidecar/v1"
                )
                for name in (
                    "features",
                    "models",
                    "execution",
                    "selection",
                )
            },
            "strict_outcome_schema_version": (
                "ranked-liquidity-shallow-gbdt-strict-outcome/v1"
            ),
            "sweep_schema_version": (
                "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
            ),
            "model_adapter": model_adapter,
            "main_rank_mode": "positive_utility_probability",
            "baseline_rank_mode": "signal_date_amount",
            "score_contract": dict(SHALLOW_GBDT_SCORE_CONTRACT),
            "producer_binding": _shallow_gbdt_producer_binding,
            "artifact_semantics_version": 3,
        }
    raise ValueError(
        "ranked-liquidity run variant must match a frozen strategy"
    )


def _assert_producer_binding_unchanged(
    expected: Mapping[str, Any],
) -> None:
    schema_version = str(expected.get("schema_version") or "")
    if schema_version == "audited-pit-ranked-liquidity-producer/v3":
        actual = _producer_binding(artifact_version=3)
    elif schema_version == (
        "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
    ):
        actual = _shallow_gbdt_producer_binding()
    else:
        actual = _producer_binding()
    if actual != dict(expected):
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


def build_ranked_liquidity_result_payloads(
    *,
    strategy_spec: Mapping[str, Any],
    shared_receipts: Mapping[str, Any],
) -> dict[str, Any]:
    variant = resolve_ranked_liquidity_run_variant(strategy_spec)
    if variant["model_adapter"].model_id == "continuous_ridge":
        frozen_strategy = deepcopy(
            ROLLING_CONTINUOUS_RIDGE_OOF_SPEC
        )
    else:
        from app import audited_pit_shallow_gbdt as shallow_gbdt

        frozen_strategy = deepcopy(
            shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
        )
    if _sha256(strategy_spec) != _sha256(frozen_strategy):
        raise ValueError(
            "ranked-liquidity result strategy is not frozen"
        )
    score_contract = frozen_score_contract(
        variant["score_contract"]
    )
    shared = dict(shared_receipts)
    source = dict(shared["source"])
    feature_values = dict(shared["features"])
    model_values = dict(shared["model"])
    execution_values = dict(shared["execution"])
    selection_values = dict(shared["selection"])
    scope = dict(shared["scope"])
    producer_code = variant["producer_binding"]()
    if (
        producer_code.get("schema_version")
        != variant["producer_schema_version"]
    ):
        raise ValueError(
            "ranked-liquidity producer schema differs from variant"
        )
    strategy_sha256 = _sha256(frozen_strategy)
    sidecar_common = {
        "strategy_sha256": strategy_sha256,
        "source": source,
        "producer_code": producer_code,
    }
    completed_candidates = list(
        execution_values.pop(
            "completed_candidates",
            [],
        )
    )
    censored_positions = list(
        execution_values.pop(
            "right_censored_positions",
            [],
        )
    )
    outcome_membership = _compact_outcome_membership_evidence(
        completed_candidates,
        censored_positions,
    )
    scored_candidates = list(
        selection_values.pop(
            "scored_execution_candidates"
        )
    )
    positive_candidates = list(
        selection_values.pop("positive_candidates")
    )
    for candidate in scored_candidates:
        candidate_score(candidate, contract=score_contract)
    positive_pool_receipt = dict(
        selection_values.pop("positive_pool_receipt")
    )
    expected_positive_candidates, expected_positive_pool_receipt = (
        _positive_score_pool(
            scored_candidates,
            score_contract=score_contract,
        )
    )
    if (
        positive_candidates != expected_positive_candidates
        or positive_pool_receipt != expected_positive_pool_receipt
    ):
        raise ValueError(
            "ranked-liquidity positive candidates differ from strict score gate"
        )
    main_selected = list(selection_values.pop("main_selected"))
    baseline_selected = list(
        selection_values.pop("baseline_selected")
    )
    selected_by_key = {
        _selection_trade_key(candidate): candidate
        for candidate in [*main_selected, *baseline_selected]
    }
    selected_evidence = [
        selected_by_key[key] for key in sorted(selected_by_key)
    ]
    for candidate in selected_evidence:
        candidate_score(candidate, contract=score_contract)
    scored_evidence = _compact_scored_execution_evidence(
        scored_candidates,
        score_contract=score_contract,
    )
    feature_sidecar = {
        "schema_version": variant["sidecar_schema_versions"][
            "features"
        ],
        **sidecar_common,
        **feature_values,
    }
    model_sidecar = {
        "schema_version": variant["sidecar_schema_versions"]["models"],
        **sidecar_common,
        **model_values,
        "oof_candidate_count": int(
            model_values["oof_receipt"]["oof_candidate_count"]
        ),
        "oof_scores_sha256": model_values["oof_receipt"][
            "oof_scores_sha256"
        ],
    }
    execution_sidecar = {
        "schema_version": variant["sidecar_schema_versions"][
            "execution"
        ],
        **sidecar_common,
        **execution_values,
        "outcome_membership_evidence": outcome_membership,
        "completed_candidate_count": len(completed_candidates),
        "right_censored_position_count": len(censored_positions),
        "completed_candidates_sha256": outcome_membership[
            "completed_candidates_sha256"
        ],
        "right_censored_positions_sha256": outcome_membership[
            "right_censored_positions_sha256"
        ],
        "strict_outcome_candidate_payload_hashes_sha256": (
            outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
        ),
    }
    main_sweep = dict(selection_values["main_sweep"])
    baseline_sweep = dict(
        selection_values["amount_baseline_sweep"]
    )
    advancement_gate_passed = bool(
        selection_values["advancement_gate_passed"]
    )
    selection_sidecar = {
        "schema_version": variant["sidecar_schema_versions"][
            "selection"
        ],
        **sidecar_common,
        **selection_values,
        "positive_pool_receipt": positive_pool_receipt,
        "scored_execution_candidate_evidence": scored_evidence,
        "selected_evidence": selected_evidence,
        "selected_evidence_sha256": _sha256(selected_evidence),
        "positive_candidate_count": len(positive_candidates),
        "positive_candidate_payload_hashes_sha256": scored_evidence[
            "positive_candidate_payload_hashes_sha256"
        ],
        "positive_candidate_keys_sha256": positive_pool_receipt[
            "positive_candidate_keys_sha256"
        ],
    }
    main_row = (
        dict(main_sweep["top"][0])
        if isinstance(main_sweep.get("top"), list)
        and main_sweep["top"]
        else {}
    )
    baseline_row = (
        dict(baseline_sweep["top"][0])
        if isinstance(baseline_sweep.get("top"), list)
        and baseline_sweep["top"]
        else {}
    )
    main_payload = {
        "schema_version": variant["result_schema_version"],
        "strategy_sha256": strategy_sha256,
        "strategy": {
            **frozen_strategy,
            "strategy_sha256": strategy_sha256,
        },
        "source": source,
        "scope": scope,
        "feature_rows_sha256": feature_values["feature_receipt"][
            "feature_rows_sha256"
        ],
        "strict_outcome_candidate_count": (
            len(completed_candidates) + len(censored_positions)
        ),
        "strict_outcome_candidates_sha256": outcome_membership[
            "strict_outcome_candidates_sha256"
        ],
        "strict_outcome_candidate_payload_hashes_sha256": (
            outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
        ),
        "oof_candidate_count": model_sidecar[
            "oof_candidate_count"
        ],
        "oof_scores_sha256": model_sidecar["oof_scores_sha256"],
        "scored_execution_candidate_count": len(scored_candidates),
        "scored_execution_candidate_evidence_rows_sha256": (
            scored_evidence["rows_sha256"]
        ),
        "scored_execution_candidate_payload_hashes_sha256": (
            scored_evidence["candidate_payload_hashes_sha256"]
        ),
        "positive_candidate_count": len(positive_candidates),
        "positive_candidate_payload_hashes_sha256": scored_evidence[
            "positive_candidate_payload_hashes_sha256"
        ],
        "positive_candidate_keys_sha256": positive_pool_receipt[
            "positive_candidate_keys_sha256"
        ],
        "positive_pool_receipt_sha256": positive_pool_receipt[
            "receipt_sha256"
        ],
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
            "all_required_gates_passed": advancement_gate_passed,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
        "comparison": {
            "shared_positive_candidate_table": True,
            "shared_candidate_table_sha256": selection_values[
                "main_selection_receipt"
            ].get("candidate_table_sha256"),
            "independent_portfolio_replays": True,
            "same_execution_contract": True,
            "baseline_performance_is_advancement_gate": False,
            "baseline_evidence_completeness_is_advancement_gate": True,
        },
    }
    return {
        "main_payload": main_payload,
        "sidecar_payloads": {
            "features": feature_sidecar,
            "models": model_sidecar,
            "execution": execution_sidecar,
            "selection": selection_sidecar,
        },
        "producer_code": producer_code,
    }


def _verify_shallow_gbdt_result_bundle_oof_replay(
    tail_features: pd.DataFrame,
    outcome_candidates: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    scored_oof: pd.DataFrame,
    receipt: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    from app.audited_pit_shallow_gbdt import (
        verify_shallow_gbdt_rolling_oof_receipt,
    )

    return verify_shallow_gbdt_rolling_oof_receipt(
        tail_features,
        outcome_candidates,
        sessions,
        scored_oof,
        receipt,
        **kwargs,
    )


def verify_shallow_gbdt_result_bundle(
    result: Mapping[str, Any],
    *,
    tail_features: pd.DataFrame,
    outcome_candidates: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    scored_oof: pd.DataFrame,
    expected_source: Mapping[str, Any],
    expected_outcome_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        from app import audited_pit_shallow_gbdt as shallow_gbdt

        expected_strategy = shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
        variant = resolve_ranked_liquidity_run_variant(
            expected_strategy
        )
        runtime_result = dict(result)
        artifact = dict(runtime_result["artifact"])
        main_path = Path(str(artifact["path"]))
        main_document = json.loads(
            main_path.read_text(encoding="utf-8")
        )
        main_digest = str(main_document.pop("artifact_sha256"))
        if (
            main_digest != artifact["artifact_sha256"]
            or _sha256(main_document) != main_digest
            or main_path.name != f"{main_digest}.json"
            or main_document.get("schema_version")
            != variant["result_schema_version"]
            or main_document.get("producer_code")
            != variant["producer_binding"]()
        ):
            raise ValueError
        if main_document.get("source") != dict(expected_source):
            raise ValueError
        embedded_strategy = dict(main_document["strategy"])
        embedded_strategy_sha256 = str(
            embedded_strategy.pop("strategy_sha256")
        )
        if (
            embedded_strategy != expected_strategy
            or embedded_strategy_sha256
            != shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
            or main_document.get("strategy_sha256")
            != embedded_strategy_sha256
        ):
            raise ValueError
        scope = main_document["scope"]
        required_scope = {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        }
        if any(
            scope.get(key) is not expected
            for key, expected in required_scope.items()
        ):
            raise ValueError
        runtime_sidecars = dict(
            runtime_result["runtime_sidecars"]
        )
        expected_sidecar_schemas = variant[
            "sidecar_schema_versions"
        ]
        if (
            set(runtime_sidecars) != set(expected_sidecar_schemas)
            or set(main_document["sidecars"])
            != set(expected_sidecar_schemas)
        ):
            raise ValueError
        sidecars: dict[str, dict[str, Any]] = {}
        sidecar_digests: dict[str, str] = {}
        for name in sorted(expected_sidecar_schemas):
            runtime = dict(runtime_sidecars[name])
            sidecar_path = Path(str(runtime["path"]))
            sidecar = json.loads(
                sidecar_path.read_text(encoding="utf-8")
            )
            sidecar_digest = str(sidecar.pop("artifact_sha256"))
            if (
                sidecar_digest != runtime["artifact_sha256"]
                or _sha256(sidecar) != sidecar_digest
                or sidecar_path.name != f"{sidecar_digest}.json"
                or main_document["sidecars"][name]
                != _stable_sidecar_reference(runtime)
                or sidecar.get("schema_version")
                != expected_sidecar_schemas[name]
                or sidecar.get("strategy_sha256")
                != embedded_strategy_sha256
                or sidecar.get("source")
                != main_document["source"]
                or sidecar.get("producer_code")
                != main_document["producer_code"]
            ):
                raise ValueError
            sidecars[name] = sidecar
            sidecar_digests[name] = sidecar_digest
        model_sidecar = sidecars["models"]
        oof_receipt = dict(model_sidecar["oof_receipt"])
        stored_oof_verification = dict(
            model_sidecar["oof_replay_verification"]
        )
        walk_forward = expected_strategy["walk_forward"]
        frozen_sessions = _ordered_sessions(sessions)
        receipt_sessions = oof_receipt.get(
            "frozen_signal_sessions"
        )
        folds = oof_receipt.get("folds")
        receipt_unsigned = dict(oof_receipt)
        receipt_sha256 = str(
            receipt_unsigned.pop("receipt_sha256", "") or ""
        )
        expected_fold_ranges = _fold_ranges(
            frozen_sessions,
            minimum_training_sessions=int(
                walk_forward["minimum_training_sessions"]
            ),
            validation_sessions=int(
                walk_forward["validation_sessions"]
            ),
        )
        observed_fold_ranges = (
            [
                (
                    str(fold.get("validation_start") or ""),
                    str(fold.get("validation_end") or ""),
                )
                for fold in folds
            ]
            if isinstance(folds, list)
            and all(isinstance(fold, Mapping) for fold in folds)
            else []
        )
        feature_receipt = sidecars["features"]["feature_receipt"]
        if (
            list(sessions) != frozen_sessions
            or len(frozen_sessions)
            != int(expected_strategy["required_market_session_count"])
            or receipt_sessions != frozen_sessions
            or oof_receipt.get("frozen_signal_sessions_sha256")
            != _sha256(frozen_sessions)
            or receipt_sha256 != _sha256(receipt_unsigned)
            or not isinstance(folds, list)
            or oof_receipt.get("fold_count") != len(folds)
            or len(folds)
            != int(expected_strategy["required_oof_fold_count"])
            or observed_fold_ranges != expected_fold_ranges
            or stored_oof_verification.get("receipt_sha256")
            != receipt_sha256
            or stored_oof_verification.get("fold_count")
            != len(folds)
            or feature_receipt.get("session_count")
            != len(frozen_sessions)
            or feature_receipt.get("sessions_sha256")
            != _sha256(frozen_sessions)
            or main_document["source"].get("market_session_count")
            != len(frozen_sessions)
            or main_document["source"].get(
                "exact_membership_session_count"
            )
            != len(frozen_sessions)
        ):
            raise ValueError
        replay_verification = (
            _verify_shallow_gbdt_result_bundle_oof_replay(
                tail_features,
                outcome_candidates,
                sessions,
                scored_oof,
                oof_receipt,
                minimum_training_sessions=int(
                    walk_forward["minimum_training_sessions"]
                ),
                training_window_sessions=int(
                    walk_forward["training_window_sessions"]
                ),
                validation_sessions=int(
                    walk_forward["validation_sessions"]
                ),
            )
        )
        if (
            replay_verification != stored_oof_verification
            or replay_verification.get("verified") is not True
            or model_sidecar.get("oof_candidate_count")
            != oof_receipt.get("oof_candidate_count")
            or model_sidecar.get("oof_scores_sha256")
            != oof_receipt.get("oof_scores_sha256")
            or main_document.get("oof_candidate_count")
            != oof_receipt.get("oof_candidate_count")
            or main_document.get("oof_scores_sha256")
            != oof_receipt.get("oof_scores_sha256")
        ):
            raise ValueError
        score_contract = frozen_score_contract(
            variant["score_contract"]
        )
        replayed_scored_candidates = (
            _scored_execution_candidates_from_oof(
                outcome_candidates,
                scored_oof,
                score_contract=score_contract,
            )
        )
        replayed_selection = _evaluate_scored_oof_variant(
            scored_execution_candidates=replayed_scored_candidates,
            sessions=sessions,
            strategy_spec=expected_strategy,
        )
        replayed_scored_evidence = (
            _compact_scored_execution_evidence(
                replayed_scored_candidates,
                score_contract=score_contract,
            )
        )
        replayed_selected_by_key = {
            _selection_trade_key(candidate): candidate
            for candidate in [
                *replayed_selection["main_selected"],
                *replayed_selection["baseline_selected"],
            ]
        }
        replayed_selected_evidence = [
            replayed_selected_by_key[key]
            for key in sorted(replayed_selected_by_key)
        ]
        replayed_completed = [
            candidate
            for candidate in outcome_candidates
            if candidate.get("right_censored") is not True
        ]
        replayed_censored = [
            candidate
            for candidate in outcome_candidates
            if candidate.get("right_censored") is True
        ]
        replayed_outcome_membership = (
            _compact_outcome_membership_evidence(
                replayed_completed,
                replayed_censored,
            )
        )
        execution = sidecars["execution"]
        stored_outcome_receipt = dict(execution["outcome_receipt"])
        if stored_outcome_receipt != dict(expected_outcome_receipt):
            raise ValueError
        if "summary_receipt_sha256" in stored_outcome_receipt:
            _verify_compact_receipt_summary(stored_outcome_receipt)
        else:
            unsigned_outcome_receipt = dict(stored_outcome_receipt)
            outcome_receipt_sha256 = str(
                unsigned_outcome_receipt.pop("receipt_sha256", "") or ""
            )
            if (
                not outcome_receipt_sha256
                or outcome_receipt_sha256
                != _sha256(unsigned_outcome_receipt)
            ):
                raise ValueError
        completed_sha256 = _audited_payload_sha256(
            replayed_completed,
            root_path="$.completed_candidates",
        )
        censored_sha256 = _audited_payload_sha256(
            replayed_censored,
            root_path="$.right_censored_positions",
        )
        if (
            stored_outcome_receipt.get("schema_version")
            != variant["strict_outcome_schema_version"]
            or stored_outcome_receipt.get(
                "completed_candidate_count"
            )
            != len(replayed_completed)
            or stored_outcome_receipt.get(
                "completed_candidates_sha256"
            )
            != completed_sha256
            or stored_outcome_receipt.get(
                "right_censored_position_count"
            )
            != len(replayed_censored)
            or stored_outcome_receipt.get(
                "right_censored_positions_sha256"
            )
            != censored_sha256
            or (
                "strict_outcome_candidate_payload_hashes_sha256"
                in stored_outcome_receipt
                and stored_outcome_receipt[
                    "strict_outcome_candidate_payload_hashes_sha256"
                ]
                != replayed_outcome_membership[
                    "strict_outcome_candidate_payload_hashes_sha256"
                ]
            )
            or (
                "input_executable_candidate_count"
                in stored_outcome_receipt
                and stored_outcome_receipt[
                    "input_executable_candidate_count"
                ]
                != len(outcome_candidates)
            )
            or execution.get("outcome_membership_evidence")
            != replayed_outcome_membership
            or execution.get("completed_candidate_count")
            != len(replayed_completed)
            or execution.get("right_censored_position_count")
            != len(replayed_censored)
            or execution.get("completed_candidates_sha256")
            != replayed_outcome_membership[
                "completed_candidates_sha256"
            ]
            or execution.get("right_censored_positions_sha256")
            != replayed_outcome_membership[
                "right_censored_positions_sha256"
            ]
            or execution.get(
                "strict_outcome_candidate_payload_hashes_sha256"
            )
            != replayed_outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            or main_document.get("strict_outcome_candidate_count")
            != len(outcome_candidates)
            or main_document.get("strict_outcome_candidates_sha256")
            != replayed_outcome_membership[
                "strict_outcome_candidates_sha256"
            ]
            or main_document.get(
                "strict_outcome_candidate_payload_hashes_sha256"
            )
            != replayed_outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
        ):
            raise ValueError
        selection = sidecars["selection"]
        scored_evidence = dict(
            selection["scored_execution_candidate_evidence"]
        )
        evidence_receipt_sha256 = str(
            scored_evidence.pop("receipt_sha256")
        )
        expected_columns = list(
            SCORED_EXECUTION_EVIDENCE_COLUMNS
        )
        expected_columns[5] = (
            "predicted_positive_utility_probability"
        )
        positive_pool_receipt = dict(
            selection["positive_pool_receipt"]
        )
        positive_pool_sha256 = str(
            positive_pool_receipt.pop("receipt_sha256")
        )
        if (
            scored_evidence.get("schema_version")
            != "ranked-liquidity-shallow-gbdt-score-evidence/v1"
            or scored_evidence.get("columns") != expected_columns
            or evidence_receipt_sha256 != _sha256(scored_evidence)
            or "predicted_net_return_pct"
            in json.dumps(
                selection,
                ensure_ascii=False,
                sort_keys=True,
            )
            or positive_pool_receipt.get("schema_version")
            != "shallow-gbdt-positive-utility-pool/v1"
            or positive_pool_receipt.get("comparison")
            != (
                "predicted_positive_utility_probability_"
                "strictly_greater_than_0.5"
            )
            or positive_pool_sha256
            != _sha256(positive_pool_receipt)
            or main_document.get("positive_pool_receipt_sha256")
            != positive_pool_sha256
            or selection["main_sweep"].get("schema_version")
            != variant["sweep_schema_version"]
            or selection["amount_baseline_sweep"].get(
                "schema_version"
            )
            != variant["sweep_schema_version"]
            or selection["main_selection_receipt"].get(
                "schema_version"
            )
            != "shallow-gbdt-industry-selection-receipt/v1"
            or selection[
                "amount_baseline_selection_receipt"
            ].get("schema_version")
            != "shallow-gbdt-industry-selection-receipt/v1"
            or main_document.get("main_sweep")
            != selection["main_sweep"]
            or main_document.get("amount_baseline_sweep")
            != selection["amount_baseline_sweep"]
        ):
            raise ValueError
        replayed_positive_candidates = replayed_selection[
            "positive_candidates"
        ]
        replayed_positive_pool_receipt = replayed_selection[
            "positive_pool_receipt"
        ]
        replayed_main_sweep = replayed_selection["main_sweep"]
        replayed_baseline_sweep = replayed_selection[
            "baseline_sweep"
        ]
        replayed_main_receipt = replayed_selection[
            "main_selection_receipt"
        ]
        replayed_baseline_receipt = replayed_selection[
            "baseline_selection_receipt"
        ]
        replayed_advancement = replayed_selection[
            "advancement_gate_passed"
        ]
        replayed_main_row = replayed_main_sweep["top"][0]
        replayed_baseline_row = replayed_baseline_sweep["top"][0]
        replayed_advancement_gate = {
            "main_latest_and_full_quality_passed": bool(
                replayed_main_row.get("target_all_pass")
            ),
            "main_rolling_12m_stability_passed": bool(
                replayed_main_row.get(
                    "target_rolling_12m_stability_pass"
                )
            ),
            "main_evidence_complete": bool(
                replayed_main_row.get("evidence_complete")
            ),
            "amount_baseline_evidence_complete": bool(
                replayed_baseline_row.get("evidence_complete")
            ),
            "all_required_gates_passed": replayed_advancement,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        }
        if (
            selection["scored_execution_candidate_evidence"]
            != replayed_scored_evidence
            or selection["positive_pool_receipt"]
            != replayed_positive_pool_receipt
            or selection["positive_candidate_count"]
            != len(replayed_positive_candidates)
            or selection["positive_candidate_payload_hashes_sha256"]
            != replayed_scored_evidence[
                "positive_candidate_payload_hashes_sha256"
            ]
            or selection["positive_candidate_keys_sha256"]
            != replayed_positive_pool_receipt[
                "positive_candidate_keys_sha256"
            ]
            or selection["main_sweep"] != replayed_main_sweep
            or selection["amount_baseline_sweep"]
            != replayed_baseline_sweep
            or selection["main_selection_receipt"]
            != replayed_main_receipt
            or selection["amount_baseline_selection_receipt"]
            != replayed_baseline_receipt
            or selection["selected_evidence"]
            != replayed_selected_evidence
            or selection["selected_evidence_sha256"]
            != _sha256(replayed_selected_evidence)
            or selection["advancement_gate_passed"]
            is not replayed_advancement
            or main_document.get("scored_execution_candidate_count")
            != len(replayed_scored_candidates)
            or main_document.get(
                "scored_execution_candidate_evidence_rows_sha256"
            )
            != replayed_scored_evidence["rows_sha256"]
            or main_document.get(
                "scored_execution_candidate_payload_hashes_sha256"
            )
            != replayed_scored_evidence[
                "candidate_payload_hashes_sha256"
            ]
            or main_document.get("positive_candidate_count")
            != len(replayed_positive_candidates)
            or main_document.get(
                "positive_candidate_payload_hashes_sha256"
            )
            != replayed_scored_evidence[
                "positive_candidate_payload_hashes_sha256"
            ]
            or main_document.get("positive_candidate_keys_sha256")
            != replayed_positive_pool_receipt[
                "positive_candidate_keys_sha256"
            ]
            or main_document.get("positive_pool_receipt_sha256")
            != replayed_positive_pool_receipt["receipt_sha256"]
            or main_document.get("main_sweep")
            != replayed_main_sweep
            or main_document.get("amount_baseline_sweep")
            != replayed_baseline_sweep
            or main_document.get("advancement_gate")
            != replayed_advancement_gate
            or main_document["scope"].get("advancement_gate_passed")
            is not replayed_advancement
            or main_document.get("comparison", {}).get(
                "shared_candidate_table_sha256"
            )
            != replayed_main_receipt.get("candidate_table_sha256")
        ):
            raise ValueError
        verification = {
            "schema_version": (
                "ranked-liquidity-shallow-gbdt-"
                "result-bundle-verification/v1"
            ),
            "strategy_sha256": embedded_strategy_sha256,
            "producer_root_sha256": main_document["producer_code"][
                "root_sha256"
            ],
            "main_artifact_sha256": main_digest,
            "sidecar_artifact_sha256": sidecar_digests,
            "checks": {
                "independent_rolling_oof_replay": True,
                "content_addressing_verified": True,
                "probability_score_contract_verified": True,
                "shared_positive_candidate_pool_verified": True,
                "strict_outcome_membership_verified": True,
                "independent_selection_replay": True,
                "independent_sweep_and_gate_replay": True,
            },
            "verified": True,
        }
        verification["receipt_sha256"] = _sha256(verification)
        return verification
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "shallow GBDT result bundle verification failed"
        ) from exc


def verify_rolling_result_bundle(
    result: Mapping[str, Any],
    *,
    expected_strategy_spec: Mapping[str, Any] = (
        ROLLING_CONTINUOUS_RIDGE_OOF_SPEC
    ),
) -> dict[str, Any]:
    try:
        runtime_result = dict(result)
        artifact = dict(runtime_result["artifact"])
        runtime_sidecars = dict(runtime_result["runtime_sidecars"])
        main_path = Path(str(artifact["path"]))
        main_file = json.loads(main_path.read_text(encoding="utf-8"))
        main_digest = str(main_file.pop("artifact_sha256"))
        if (
            main_digest != artifact["artifact_sha256"]
            or _sha256(main_file) != main_digest
            or main_path.name != f"{main_digest}.json"
        ):
            raise ValueError
        strategy = dict(main_file["strategy"])
        strategy_digest = str(strategy.pop("strategy_sha256"))
        expected_strategy = dict(expected_strategy_spec)
        expected_strategy_sha256 = _sha256(expected_strategy)
        if (
            strategy != expected_strategy
            or strategy_digest != expected_strategy_sha256
            or main_file["strategy_sha256"] != strategy_digest
        ):
            raise ValueError
        if (
            main_file["schema_version"]
            != "ranked-liquidity-ridge-result/v3"
            or main_file["producer_code"]
            != _producer_binding(artifact_version=3)
            or main_file["scope"]["point_in_time"] is not True
            or main_file["scope"]["development_only"] is not True
            or main_file["scope"]["strict_artifact_native_execution"]
            is not True
            or main_file["scope"]["intraday_fill_claimed"] is not False
            or main_file["scope"]["embargo_consumed"] is not False
            or main_file["scope"]["final_oos_consumed"] is not False
            or main_file["scope"]["eligible_for_profile_registration"]
            is not False
            or main_file["scope"]["production_recommendation_eligible"]
            is not False
        ):
            raise ValueError

        expected_sidecar_schemas = {
            "features": "ranked-liquidity-feature-sidecar/v3",
            "models": "ranked-liquidity-model-sidecar/v3",
            "execution": "ranked-liquidity-execution-sidecar/v3",
            "selection": "ranked-liquidity-selection-sidecar/v3",
        }
        expected_names = set(expected_sidecar_schemas)
        if (
            set(runtime_sidecars) != expected_names
            or set(main_file["sidecars"]) != expected_names
        ):
            raise ValueError
        sidecars: dict[str, dict[str, Any]] = {}
        for name in sorted(expected_names):
            runtime = dict(runtime_sidecars[name])
            sidecar_path = Path(str(runtime["path"]))
            sidecar_file = json.loads(
                sidecar_path.read_text(encoding="utf-8")
            )
            sidecar_digest = str(sidecar_file.pop("artifact_sha256"))
            if (
                sidecar_digest != runtime["artifact_sha256"]
                or _sha256(sidecar_file) != sidecar_digest
                or sidecar_path.name != f"{sidecar_digest}.json"
                or main_file["sidecars"][name]
                != _stable_sidecar_reference(runtime)
                or sidecar_file["schema_version"]
                != expected_sidecar_schemas[name]
                or sidecar_file["strategy_sha256"] != strategy_digest
                or sidecar_file["source"] != main_file["source"]
                or sidecar_file["producer_code"]
                != main_file["producer_code"]
            ):
                raise ValueError
            sidecars[name] = sidecar_file

        oof_verification = sidecars["models"].get(
            "oof_replay_verification"
        )
        oof_receipt_value = sidecars["models"].get("oof_receipt")
        if (
            not isinstance(oof_verification, Mapping)
            or oof_verification.get("verified") is not True
            or not isinstance(oof_receipt_value, Mapping)
        ):
            raise ValueError
        oof_receipt = dict(oof_receipt_value)
        oof_receipt_sha256 = str(
            oof_receipt.pop("receipt_sha256")
        )
        folds = oof_receipt.get("folds")
        frozen_signal_sessions = oof_receipt.get(
            "frozen_signal_sessions"
        )
        if (
            oof_receipt.get("schema_version")
            != "ranked-liquidity-ridge-purged-oof-receipt/v3"
            or not isinstance(folds, list)
            or not isinstance(frozen_signal_sessions, list)
            or frozen_signal_sessions
            != sorted(set(frozen_signal_sessions))
            or any(
                date.fromisoformat(str(session)).isoformat()
                != str(session)
                for session in frozen_signal_sessions
            )
            or len(frozen_signal_sessions)
            != int(expected_strategy["required_market_session_count"])
            or oof_receipt.get("frozen_signal_sessions_sha256")
            != _sha256(frozen_signal_sessions)
            or sidecars["features"]["feature_receipt"].get(
                "session_count"
            )
            != len(frozen_signal_sessions)
            or sidecars["features"]["feature_receipt"].get(
                "sessions_sha256"
            )
            != _sha256(frozen_signal_sessions)
            or main_file["source"].get("market_session_count")
            != len(frozen_signal_sessions)
            or oof_receipt.get("fold_count") != len(folds)
            or oof_receipt.get("folds_sha256") != _sha256(folds)
            or oof_receipt_sha256 != _sha256(oof_receipt)
            or len(folds)
            != int(expected_strategy["required_oof_fold_count"])
            or oof_verification.get("receipt_sha256")
            != oof_receipt_sha256
            or oof_verification.get("fold_count") != len(folds)
            or oof_verification.get("oof_candidate_count")
            != oof_receipt.get("oof_candidate_count")
            or oof_receipt.get("minimum_training_sessions")
            != expected_strategy["walk_forward"][
                "minimum_training_sessions"
            ]
            or oof_receipt.get("validation_sessions")
            != expected_strategy["walk_forward"]["validation_sessions"]
            or oof_receipt.get("training_window_type")
            != expected_strategy["walk_forward"][
                "training_window_type"
            ]
            or oof_receipt.get("training_window_sessions")
            != expected_strategy["walk_forward"][
                "training_window_sessions"
            ]
            or oof_receipt.get("purge")
            != expected_strategy["walk_forward"]["purge"]
            or float(oof_receipt.get("ridge_lambda"))
            != float(expected_strategy["model"]["ridge_lambda"])
            or oof_receipt.get("oof_candidate_count")
            != sum(
                int(fold.get("validation_candidate_count", -1))
                for fold in folds
            )
            or sidecars["models"].get("oof_candidate_count")
            != oof_receipt.get("oof_candidate_count")
            or main_file.get("oof_candidate_count")
            != oof_receipt.get("oof_candidate_count")
            or sidecars["models"].get("oof_scores_sha256")
            != oof_receipt.get("oof_scores_sha256")
            or main_file.get("oof_scores_sha256")
            != oof_receipt.get("oof_scores_sha256")
        ):
            raise ValueError
        prior_validation_end: str | None = None
        for expected_fold_number, fold_value in enumerate(
            folds,
            start=1,
        ):
            if not isinstance(fold_value, Mapping):
                raise ValueError
            fold = dict(fold_value)
            fold_receipt_sha256 = str(
                fold.pop("receipt_sha256")
            )
            validation_start = str(
                fold.get("validation_start") or ""
            )
            validation_end = str(fold.get("validation_end") or "")
            start_position = int(
                expected_strategy["walk_forward"][
                    "minimum_training_sessions"
                ]
            ) + (
                (expected_fold_number - 1)
                * int(
                    expected_strategy["walk_forward"][
                        "validation_sessions"
                    ]
                )
            )
            expected_validation_start = frozen_signal_sessions[
                start_position
            ]
            expected_validation_end = frozen_signal_sessions[
                min(
                    start_position
                    + int(
                        expected_strategy["walk_forward"][
                            "validation_sessions"
                        ]
                    )
                    - 1,
                    len(frozen_signal_sessions) - 1,
                )
            ]
            training_window_sessions = int(
                expected_strategy["walk_forward"][
                    "training_window_sessions"
                ]
            )
            expected_training_window = frozen_signal_sessions[
                start_position - training_window_sessions : start_position
            ]
            if (
                fold_receipt_sha256 != _sha256(fold)
                or fold.get("fold") != expected_fold_number
                or fold.get("training_window_type")
                != "trailing_frozen_signal_sessions"
                or fold.get("training_window_session_count")
                != training_window_sessions
                or validation_start != expected_validation_start
                or validation_end != expected_validation_end
                or fold.get("training_window_start")
                != expected_training_window[0]
                or fold.get("training_window_end")
                != expected_training_window[-1]
                or fold.get("training_window_sessions_sha256")
                != _sha256(expected_training_window)
                or not validation_start
                or validation_end < validation_start
                or (
                    prior_validation_end is not None
                    and validation_start <= prior_validation_end
                )
                or str(fold.get("training_window_start") or "")
                > str(fold.get("training_window_end") or "")
                or str(fold.get("training_window_end") or "")
                >= validation_start
                or str(fold.get("training_last_exit_date") or "")
                >= validation_start
                or fold.get("model_sha256")
                != _sha256(fold.get("model"))
            ):
                raise ValueError
            prior_validation_end = validation_end
        selection = sidecars["selection"]
        main_selection = selection["main_selection_receipt"]
        baseline_selection = selection[
            "amount_baseline_selection_receipt"
        ]
        _verify_compact_receipt_summary(main_selection)
        _verify_compact_receipt_summary(baseline_selection)
        positive_pool_receipt = dict(selection["positive_pool_receipt"])
        positive_pool_receipt_sha256 = str(
            positive_pool_receipt.pop("receipt_sha256")
        )
        main_sweep = selection["main_sweep"]
        baseline_sweep = selection["amount_baseline_sweep"]
        outcome_membership = dict(
            sidecars["execution"]["outcome_membership_evidence"]
        )
        outcome_membership_receipt_sha256 = str(
            outcome_membership.pop("receipt_sha256")
        )
        outcome_membership_rows = outcome_membership["rows"]
        if (
            outcome_membership.get("schema_version")
            != "ranked-liquidity-outcome-membership/v3"
            or outcome_membership.get("columns")
            != [
                "candidate_key",
                "right_censored",
                "outcome_payload_sha256",
            ]
            or not isinstance(outcome_membership_rows, list)
            or outcome_membership.get("row_count")
            != len(outcome_membership_rows)
            or outcome_membership_rows
            != sorted(outcome_membership_rows, key=lambda row: row[0])
            or outcome_membership.get("rows_sha256")
            != _sha256(outcome_membership_rows)
            or outcome_membership_receipt_sha256
            != _sha256(outcome_membership)
        ):
            raise ValueError
        outcome_membership_by_candidate_key: dict[
            str, tuple[bool, str]
        ] = {}
        completed_outcome_payload_hashes: list[str] = []
        censored_outcome_payload_hashes: list[str] = []
        for row in outcome_membership_rows:
            if (
                not isinstance(row, list)
                or len(row) != 3
                or not str(row[0] or "")
                or not isinstance(row[1], bool)
                or len(str(row[2] or "")) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in str(row[2])
                )
                or str(row[0])
                in outcome_membership_by_candidate_key
            ):
                raise ValueError
            candidate_key = str(row[0])
            right_censored = bool(row[1])
            payload_sha256 = str(row[2])
            outcome_membership_by_candidate_key[candidate_key] = (
                right_censored,
                payload_sha256,
            )
            if right_censored:
                censored_outcome_payload_hashes.append(payload_sha256)
            else:
                completed_outcome_payload_hashes.append(payload_sha256)
        if (
            outcome_membership["completed_candidate_count"]
            != len(completed_outcome_payload_hashes)
            or outcome_membership[
                "right_censored_position_count"
            ]
            != len(censored_outcome_payload_hashes)
            or outcome_membership[
                "completed_candidate_payload_hashes_sha256"
            ]
            != _sha256(completed_outcome_payload_hashes)
            or outcome_membership[
                "right_censored_position_payload_hashes_sha256"
            ]
            != _sha256(censored_outcome_payload_hashes)
            or sidecars["execution"]["completed_candidate_count"]
            != len(completed_outcome_payload_hashes)
            or main_file["strict_outcome_candidate_count"]
            != len(completed_outcome_payload_hashes)
            + len(censored_outcome_payload_hashes)
            or outcome_membership["row_count"]
            != main_file["strict_outcome_candidate_count"]
            or outcome_membership["completed_candidates_sha256"]
            != sidecars["execution"]["outcome_receipt"][
                "completed_candidates_sha256"
            ]
            or outcome_membership["completed_candidates_sha256"]
            != sidecars["execution"]["completed_candidates_sha256"]
            or sidecars["execution"][
                "right_censored_position_count"
            ]
            != len(censored_outcome_payload_hashes)
            or outcome_membership[
                "right_censored_positions_sha256"
            ]
            != sidecars["execution"]["outcome_receipt"][
                "right_censored_positions_sha256"
            ]
            or outcome_membership[
                "right_censored_positions_sha256"
            ]
            != sidecars["execution"][
                "right_censored_positions_sha256"
            ]
            or sidecars["execution"][
                "completed_candidate_payload_hashes_sha256"
            ]
            != _sha256(completed_outcome_payload_hashes)
            or sidecars["execution"]["outcome_receipt"][
                "completed_candidate_payload_hashes_sha256"
            ]
            != _sha256(completed_outcome_payload_hashes)
            or sidecars["execution"][
                "right_censored_position_payload_hashes_sha256"
            ]
            != _sha256(censored_outcome_payload_hashes)
            or sidecars["execution"]["outcome_receipt"][
                "right_censored_position_payload_hashes_sha256"
            ]
            != _sha256(censored_outcome_payload_hashes)
            or outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            != _sha256(
                [
                    *completed_outcome_payload_hashes,
                    *censored_outcome_payload_hashes,
                ]
            )
            or sidecars["execution"][
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            != outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            or sidecars["execution"]["outcome_receipt"][
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            != outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            or main_file[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            != outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
            or outcome_membership[
                "strict_outcome_candidates_sha256"
            ]
            != main_file["strict_outcome_candidates_sha256"]
        ):
            raise ValueError
        scored_execution_evidence = dict(
            selection["scored_execution_candidate_evidence"]
        )
        evidence_receipt_sha256 = str(
            scored_execution_evidence.pop("receipt_sha256")
        )
        evidence_rows = scored_execution_evidence["rows"]
        if (
            scored_execution_evidence.get("schema_version")
            != "ranked-liquidity-score-evidence/v3"
            or scored_execution_evidence.get("columns")
            != list(SCORED_EXECUTION_EVIDENCE_COLUMNS)
            or not isinstance(evidence_rows, list)
            or scored_execution_evidence.get("row_count")
            != len(evidence_rows)
            or scored_execution_evidence.get("rows_sha256")
            != _sha256(evidence_rows)
            or evidence_receipt_sha256
            != _sha256(scored_execution_evidence)
        ):
            raise ValueError
        input_candidate_keys: list[str] = []
        positive_candidate_keys: list[str] = []
        candidate_payload_hashes: list[str] = []
        positive_candidate_payload_hashes: list[str] = []
        reconstructed_candidate_table: list[dict[str, Any]] = []
        positive_payload_by_trade_key: dict[str, str] = {}
        positive_outcome_payload_by_trade_key: dict[str, str] = {}
        prior_sort_key: tuple[str, str, str] | None = None
        all_trade_keys: list[str] = []
        for row in evidence_rows:
            if not isinstance(row, list) or len(row) != len(
                SCORED_EXECUTION_EVIDENCE_COLUMNS
            ):
                raise ValueError
            (
                candidate_key_value,
                signal_date_value,
                trade_key_value,
                security_id_value,
                industry_value,
                score_value,
                amount_value,
                right_censored_value,
                outcome_payload_sha256_value,
                payload_sha256_value,
            ) = row
            candidate_key = str(candidate_key_value or "")
            signal_date = str(signal_date_value or "")
            trade_key = str(trade_key_value or "")
            security_id = str(security_id_value or "")
            industry = str(industry_value or "").strip()
            score = float(score_value)
            amount = float(amount_value)
            outcome_payload_sha256 = str(
                outcome_payload_sha256_value or ""
            )
            payload_sha256 = str(payload_sha256_value or "")
            sort_key = (signal_date, security_id, candidate_key)
            trade_key_parts = trade_key.split("|")
            if (
                not candidate_key
                or not signal_date
                or not trade_key
                or not security_id
                or not industry
                or len(trade_key_parts) != 4
                or trade_key_parts[0] != security_id
                or trade_key_parts[1] != signal_date
                or not is_mainboard_chinext_symbol(
                    security_id.rsplit(":", 1)[-1]
                )
                or not math.isfinite(score)
                or not math.isfinite(amount)
                or not isinstance(right_censored_value, bool)
                or len(outcome_payload_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in outcome_payload_sha256
                )
                or len(payload_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in payload_sha256
                )
                or (
                    prior_sort_key is not None
                    and sort_key < prior_sort_key
                )
                or outcome_membership_by_candidate_key.get(
                    candidate_key
                )
                != (
                    right_censored_value,
                    outcome_payload_sha256,
                )
            ):
                raise ValueError
            prior_sort_key = sort_key
            input_candidate_keys.append(candidate_key)
            all_trade_keys.append(trade_key)
            candidate_payload_hashes.append(payload_sha256)
            if score > 0.0:
                positive_candidate_keys.append(candidate_key)
                positive_candidate_payload_hashes.append(payload_sha256)
                if trade_key in positive_payload_by_trade_key:
                    raise ValueError
                positive_payload_by_trade_key[trade_key] = payload_sha256
                positive_outcome_payload_by_trade_key[trade_key] = (
                    outcome_payload_sha256
                )
                reconstructed_candidate_table.append(
                    {
                        "trade_key": trade_key,
                        "candidate_key": candidate_key,
                        "security_id": security_id,
                        "signal_industry": industry,
                        "predicted_net_return_pct": score,
                        "candidate_amount": amount,
                        "right_censored": right_censored_value,
                    }
                )
        if (
            len(input_candidate_keys) != len(set(input_candidate_keys))
            or len(all_trade_keys) != len(set(all_trade_keys))
            or scored_execution_evidence[
                "candidate_payload_hashes_sha256"
            ]
            != _sha256(candidate_payload_hashes)
            or scored_execution_evidence[
                "positive_candidate_payload_hashes_sha256"
            ]
            != _sha256(positive_candidate_payload_hashes)
        ):
            raise ValueError
        reconstructed_candidate_table.sort(
            key=lambda item: item["trade_key"]
        )
        reconstructed_trade_keys = [
            item["trade_key"]
            for item in reconstructed_candidate_table
        ]
        if len(reconstructed_trade_keys) != len(
            set(reconstructed_trade_keys)
        ):
            raise ValueError
        positive_candidate_count = int(
            positive_pool_receipt["positive_candidate_count"]
        )
        _replay_selection_summary(
            main_selection,
            reconstructed_candidate_table,
            strategy_spec=expected_strategy,
            rank_mode="predicted_net_return",
        )
        _replay_selection_summary(
            baseline_selection,
            reconstructed_candidate_table,
            strategy_spec=expected_strategy,
            rank_mode="signal_date_amount",
        )
        if (
            positive_pool_receipt.get("schema_version")
            != "continuous-ridge-positive-score-pool/v1"
            or positive_pool_receipt.get("comparison")
            != (
                "predicted_net_return_pct_"
                "strictly_greater_than_zero"
            )
            or _sha256(positive_pool_receipt)
            != positive_pool_receipt_sha256
            or main_file["positive_pool_receipt_sha256"]
            != positive_pool_receipt_sha256
            or scored_execution_evidence["rows_sha256"]
            != main_file[
                "scored_execution_candidate_evidence_rows_sha256"
            ]
            or scored_execution_evidence[
                "candidate_payload_hashes_sha256"
            ]
            != main_file[
                "scored_execution_candidate_payload_hashes_sha256"
            ]
            or len(evidence_rows)
            != main_file["scored_execution_candidate_count"]
            or positive_pool_receipt["input_candidate_count"]
            != len(evidence_rows)
            or positive_pool_receipt["input_candidate_keys_sha256"]
            != _sha256(input_candidate_keys)
            or positive_pool_receipt["positive_candidate_count"]
            != len(positive_candidate_keys)
            or positive_pool_receipt[
                "positive_candidate_keys_sha256"
            ]
            != _sha256(positive_candidate_keys)
            or main_sweep["schema_version"]
            != "strict-ranked-liquidity-ridge-fixed-oof/v3"
            or baseline_sweep["schema_version"]
            != "strict-ranked-liquidity-ridge-fixed-oof/v3"
            or main_file["main_sweep"] != main_sweep
            or main_file["amount_baseline_sweep"] != baseline_sweep
            or main_selection["schema_version"]
            != "continuous-ridge-industry-selection-receipt/v1"
            or baseline_selection["schema_version"]
            != "continuous-ridge-industry-selection-receipt/v1"
            or main_selection["parameters"]["rank_mode"]
            != "predicted_net_return"
            or baseline_selection["parameters"]["rank_mode"]
            != "signal_date_amount"
            or sidecars["execution"]["outcome_receipt"]["schema_version"]
            != "ranked-liquidity-ridge-strict-outcome/v3"
            or main_selection["candidate_table_sha256"]
            != baseline_selection["candidate_table_sha256"]
            or main_selection["candidate_table_sha256"]
            != _sha256(reconstructed_candidate_table)
            or main_selection["candidate_table_sha256"]
            != main_file["comparison"]["shared_candidate_table_sha256"]
            or selection["positive_candidate_count"]
            != positive_candidate_count
            or main_file["positive_candidate_count"]
            != positive_candidate_count
            or main_selection["candidate_count"]
            != positive_candidate_count
            or baseline_selection["candidate_count"]
            != positive_candidate_count
            or main_sweep["selection_candidate_count"]
            != positive_candidate_count
            or baseline_sweep["selection_candidate_count"]
            != positive_candidate_count
            or main_sweep["top"][0]["selection_candidate_count"]
            != positive_candidate_count
            or baseline_sweep["top"][0]["selection_candidate_count"]
            != positive_candidate_count
            or positive_pool_receipt["input_candidate_count"]
            != main_file["scored_execution_candidate_count"]
            or selection[
                "positive_candidate_payload_hashes_sha256"
            ]
            != scored_execution_evidence[
                "positive_candidate_payload_hashes_sha256"
            ]
            or main_file[
                "positive_candidate_payload_hashes_sha256"
            ]
            != scored_execution_evidence[
                "positive_candidate_payload_hashes_sha256"
            ]
            or selection["positive_candidate_keys_sha256"]
            != positive_pool_receipt[
                "positive_candidate_keys_sha256"
            ]
            or main_file["positive_candidate_keys_sha256"]
            != positive_pool_receipt[
                "positive_candidate_keys_sha256"
            ]
        ):
            raise ValueError
        _verify_compact_receipt_summary(
            sidecars["execution"]["entry_preflight_receipt"]
        )
        _verify_compact_receipt_summary(
            sidecars["execution"]["outcome_receipt"]
        )
        selected_evidence = selection["selected_evidence"]
        main_selected_keys = main_selection["selected_trade_keys"]
        baseline_selected_keys = baseline_selection["selected_trade_keys"]
        expected_selected_keys = sorted(
            set(main_selected_keys) | set(baseline_selected_keys)
        )
        selected_evidence_keys = [
            _selection_trade_key(item) for item in selected_evidence
        ]
        candidate_table_by_trade_key = {
            str(item["trade_key"]): item
            for item in reconstructed_candidate_table
        }
        selected_projection_mismatch = False
        for item, trade_key in zip(
            selected_evidence,
            selected_evidence_keys,
        ):
            selected_projection = {
                "trade_key": trade_key,
                "candidate_key": str(
                    item.get("candidate_key") or ""
                ),
                "security_id": str(
                    item.get("security_id") or ""
                ),
                "signal_industry": str(
                    item.get("signal_industry") or ""
                ).strip(),
                "predicted_net_return_pct": float(
                    item.get("predicted_net_return_pct")
                ),
                "candidate_amount": float(
                    item.get("candidate_amount")
                ),
                "right_censored": item.get("right_censored") is True,
            }
            if (
                candidate_table_by_trade_key.get(trade_key)
                != selected_projection
            ):
                selected_projection_mismatch = True
                break
        if (
            len(main_selected_keys) != main_selection["selected_count"]
            or len(main_selected_keys) != len(set(main_selected_keys))
            or _sha256(main_selected_keys)
            != main_selection["selected_trade_keys_sha256"]
            or len(baseline_selected_keys)
            != baseline_selection["selected_count"]
            or len(baseline_selected_keys)
            != len(set(baseline_selected_keys))
            or _sha256(baseline_selected_keys)
            != baseline_selection["selected_trade_keys_sha256"]
            or selected_evidence_keys != expected_selected_keys
            or selected_projection_mismatch
            or _sha256(selected_evidence)
            != selection["selected_evidence_sha256"]
            or any(
                positive_payload_by_trade_key.get(
                    _selection_trade_key(item)
                )
                != _sha256(dict(item))
                for item in selected_evidence
            )
            or any(
                positive_outcome_payload_by_trade_key.get(
                    _selection_trade_key(item)
                )
                != _sha256(
                    _outcome_payload_from_scored_candidate(item)
                )
                for item in selected_evidence
            )
            or any(
                float(item.get("score"))
                != float(item.get("predicted_net_return_pct"))
                or float(item.get("rank_score"))
                != float(item.get("predicted_net_return_pct"))
                for item in selected_evidence
            )
            or any(
                not is_mainboard_chinext_symbol(
                    str(item.get("symbol") or "")
                )
                for item in selected_evidence
            )
        ):
            raise ValueError
        selected_evidence_by_trade_key = {
            _selection_trade_key(item): dict(item)
            for item in selected_evidence
        }
        _verify_recomputed_trade_metrics(
            main_sweep,
            main_selected_keys,
            selected_evidence_by_trade_key,
            frozen_signal_sessions,
            strategy_spec=expected_strategy,
        )
        _verify_recomputed_trade_metrics(
            baseline_sweep,
            baseline_selected_keys,
            selected_evidence_by_trade_key,
            frozen_signal_sessions,
            strategy_spec=expected_strategy,
        )
        main_gate = _recompute_fixed_oof_gate(
            main_sweep,
            main_selection,
            reconstructed_candidate_table,
            strategy_spec=expected_strategy,
            rank_mode="predicted_net_return",
        )
        baseline_gate = _recompute_fixed_oof_gate(
            baseline_sweep,
            baseline_selection,
            reconstructed_candidate_table,
            strategy_spec=expected_strategy,
            rank_mode="signal_date_amount",
        )
        expected_advancement_gate = bool(
            main_gate["target_all_pass"]
            and main_gate["rolling_stability_pass"]
            and main_gate["evidence_complete"]
            and baseline_gate["evidence_complete"]
        )
        if (
            selection["advancement_gate_passed"]
            is not expected_advancement_gate
            or main_file["scope"]["advancement_gate_passed"]
            is not expected_advancement_gate
            or main_file["advancement_gate"][
                "all_required_gates_passed"
            ]
            is not expected_advancement_gate
            or main_file["advancement_gate"]["embargo_consumed"]
            is not False
            or main_file["advancement_gate"]["final_oos_consumed"]
            is not False
        ):
            raise ValueError
    except (
        KeyError,
        IndexError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "rolling result bundle verification failed"
        ) from exc
    verification = {
        "schema_version": (
            "ranked-liquidity-rolling-result-bundle-verification/v3"
        ),
        "strategy_sha256": strategy_digest,
        "producer_root_sha256": main_file["producer_code"][
            "root_sha256"
        ],
        "main_artifact_sha256": main_digest,
        "sidecar_artifact_sha256": {
            name: runtime_sidecars[name]["artifact_sha256"]
            for name in sorted(runtime_sidecars)
        },
        "checks": {
            "independent_rolling_oof_replay": True,
            "exact_trailing_126_session_windows": True,
            "window_external_labels_excluded": True,
            "window_internal_mature_labels_bound": True,
            "immature_labels_purged": True,
            "six_fold_geometry_matches_v2": True,
            "content_addressing_verified": True,
            "shared_positive_candidate_pool_verified": True,
            "selected_board_scope_verified": True,
        },
        "verified": True,
    }
    verification["receipt_sha256"] = _sha256(verification)
    return verification


def _frozen_variant_strategy_spec(
    variant: Mapping[str, Any],
) -> dict[str, Any]:
    adapter = variant["model_adapter"]
    if not isinstance(adapter, ModelOOFAdapter):
        raise ValueError("ranked-liquidity model adapter is invalid")
    if adapter.model_id == "continuous_ridge":
        return deepcopy(ROLLING_CONTINUOUS_RIDGE_OOF_SPEC)

    from app import audited_pit_shallow_gbdt as shallow_gbdt

    frozen_strategy = deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
    if (
        _sha256(frozen_strategy)
        != shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
    ):
        raise ValueError("ranked-liquidity frozen strategy copy drifted")
    return frozen_strategy


def _scored_execution_candidates_from_oof(
    outcome_candidates: Sequence[Mapping[str, Any]],
    scored_oof: pd.DataFrame,
    *,
    score_contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metadata = _score_contract_metadata(score_contract)
    score_field = str(metadata["field"])
    required_columns = {"candidate_key", score_field}
    if (
        not isinstance(scored_oof, pd.DataFrame)
        or not required_columns.issubset(scored_oof.columns)
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF score table is invalid"
        )

    outcome_by_key: dict[str, Mapping[str, Any]] = {}
    for raw_candidate in outcome_candidates:
        candidate_key = str(raw_candidate.get("candidate_key") or "")
        if not candidate_key or candidate_key in outcome_by_key:
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity outcome candidate keys are invalid"
            )
        outcome_by_key[candidate_key] = raw_candidate

    score_lookup: dict[str, float] = {}
    signal_date_lookup: dict[str, str] = {}
    for row in scored_oof.itertuples(index=False):
        candidate_key = str(row.candidate_key)
        if (
            not candidate_key
            or candidate_key in score_lookup
        ):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity OOF score keys are invalid"
            )
        score_lookup[candidate_key] = float(getattr(row, score_field))
        if "signal_date" in scored_oof.columns:
            signal_date_lookup[candidate_key] = str(row.signal_date)

    scored_candidates: list[dict[str, Any]] = []
    for candidate_key, candidate in outcome_by_key.items():
        if candidate_key not in score_lookup:
            continue
        score = score_lookup[candidate_key]
        if (
            candidate_key in signal_date_lookup
            and signal_date_lookup[candidate_key]
            != str(candidate.get("signal_date") or "")
        ):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity OOF signal dates differ from outcomes"
            )
        scored_candidates.append(
            score_evidence_payload(
                {**candidate, score_field: score},
                contract=score_contract,
            )
        )
    scored_candidates.sort(
        key=lambda item: (
            str(item["signal_date"]),
            str(item["security_id"]),
            str(item["candidate_key"]),
        )
    )
    return scored_candidates


def _evaluate_scored_oof_variant(
    *,
    scored_execution_candidates: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    strategy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    variant = resolve_ranked_liquidity_run_variant(strategy_spec)
    frozen_strategy = _frozen_variant_strategy_spec(variant)
    if _sha256(strategy_spec) != _sha256(frozen_strategy):
        raise ValueError("ranked-liquidity evaluation strategy is not frozen")
    score_contract = frozen_score_contract(variant["score_contract"])
    scored_candidates = list(scored_execution_candidates)
    for candidate in scored_candidates:
        candidate_score(candidate, contract=score_contract)
    positive_candidates, positive_pool_receipt = _positive_score_pool(
        scored_candidates,
        score_contract=score_contract,
    )

    ordered_sessions = _ordered_sessions(sessions)
    minimum_training_sessions = int(
        frozen_strategy["walk_forward"]["minimum_training_sessions"]
    )
    evaluation_sessions = list(
        ordered_sessions[minimum_training_sessions:]
    )
    if not evaluation_sessions and len(ordered_sessions) <= minimum_training_sessions:
        evaluation_sessions = ordered_sessions
    if not evaluation_sessions:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity evaluation session grid is empty"
        )

    main_sweep, main_selection_receipt = _evaluate_fixed_oof(
        positive_candidates,
        rank_mode=variant["main_rank_mode"],
        evaluation_session_dates=evaluation_sessions,
        strategy_spec=frozen_strategy,
        sweep_schema_version=variant["sweep_schema_version"],
        score_contract=score_contract,
    )
    baseline_sweep, baseline_selection_receipt = _evaluate_fixed_oof(
        positive_candidates,
        rank_mode=variant["baseline_rank_mode"],
        evaluation_session_dates=evaluation_sessions,
        strategy_spec=frozen_strategy,
        sweep_schema_version=variant["sweep_schema_version"],
        score_contract=score_contract,
    )
    selection_spec = frozen_strategy["selection"]
    main_selected, replayed_main_receipt = (
        _select_with_industry_cap_receipt(
            positive_candidates,
            rank_mode=variant["main_rank_mode"],
            top_n=int(selection_spec["top_n"]),
            max_active_positions=int(
                selection_spec["max_active_positions"]
            ),
            score_contract=score_contract,
        )
    )
    baseline_selected, replayed_baseline_receipt = (
        _select_with_industry_cap_receipt(
            positive_candidates,
            rank_mode=variant["baseline_rank_mode"],
            top_n=int(selection_spec["top_n"]),
            max_active_positions=int(
                selection_spec["max_active_positions"]
            ),
            score_contract=score_contract,
        )
    )
    if (
        replayed_main_receipt != main_selection_receipt
        or replayed_baseline_receipt != baseline_selection_receipt
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity selection replay differs from evaluation"
        )
    return {
        "scored_execution_candidates": scored_candidates,
        "positive_candidates": positive_candidates,
        "positive_pool_receipt": positive_pool_receipt,
        "main_sweep": main_sweep,
        "baseline_sweep": baseline_sweep,
        "main_selection_receipt": main_selection_receipt,
        "baseline_selection_receipt": baseline_selection_receipt,
        "main_selected": main_selected,
        "baseline_selected": baseline_selected,
        "advancement_gate_passed": _advancement_gate_passes(
            main_sweep["top"][0],
            baseline_sweep["top"][0],
        ),
    }


def _score_and_evaluate_oof_variant(
    *,
    tail_features: pd.DataFrame,
    outcome_candidates: Sequence[Mapping[str, Any]],
    sessions: Sequence[str],
    strategy_spec: Mapping[str, Any],
    include_scored_oof: bool = False,
) -> dict[str, Any]:
    variant = resolve_ranked_liquidity_run_variant(strategy_spec)
    adapter = variant["model_adapter"]
    frozen_strategy_spec = _frozen_variant_strategy_spec(variant)
    strategy_spec = frozen_strategy_spec
    score_contract = frozen_score_contract(adapter.score_contract)
    if dict(score_contract) != variant["score_contract"]:
        raise ValueError(
            "ranked-liquidity variant score contract drifted"
        )
    walk_forward = strategy_spec["walk_forward"]
    minimum_training_sessions = int(
        walk_forward["minimum_training_sessions"]
    )
    training_window_sessions = int(
        walk_forward["training_window_sessions"]
    )
    validation_sessions = int(
        walk_forward["validation_sessions"]
    )
    common_oof_kwargs = {
        "minimum_training_sessions": minimum_training_sessions,
        "training_window_sessions": training_window_sessions,
        "validation_sessions": validation_sessions,
    }
    build_kwargs = dict(common_oof_kwargs)
    if adapter.model_id == "continuous_ridge":
        build_kwargs.update(
            {
                "require_nonempty_validation_folds": True,
                "receipt_schema_version": (
                    "ranked-liquidity-ridge-purged-oof-receipt/v3"
                ),
                "ridge_lambda": float(
                    strategy_spec["model"]["ridge_lambda"]
                ),
                "strategy_spec": strategy_spec,
            }
        )
    scored_oof, oof_receipt = adapter.build_scores(
        tail_features,
        outcome_candidates,
        sessions,
        **build_kwargs,
    )
    expected_fold_ranges = _fold_ranges(
        sessions,
        minimum_training_sessions=minimum_training_sessions,
        validation_sessions=validation_sessions,
    )
    observed_fold_ranges = [
        (str(fold["validation_start"]), str(fold["validation_end"]))
        for fold in oof_receipt["folds"]
    ]
    if (
        len(expected_fold_ranges)
        != int(strategy_spec["required_oof_fold_count"])
        or observed_fold_ranges != expected_fold_ranges
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF folds differ from frozen six-fold plan"
        )
    oof_replay_verification = adapter.verify_receipt(
        tail_features,
        outcome_candidates,
        sessions,
        scored_oof,
        oof_receipt,
        **common_oof_kwargs,
    )
    if oof_replay_verification.get("verified") is not True:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF replay verification failed"
        )
    scored_execution_candidates = _scored_execution_candidates_from_oof(
        outcome_candidates,
        scored_oof,
        score_contract=score_contract,
    )
    evaluated = _evaluate_scored_oof_variant(
        scored_execution_candidates=scored_execution_candidates,
        sessions=sessions,
        strategy_spec=strategy_spec,
    )
    result = {
        "oof_receipt": oof_receipt,
        "oof_replay_verification": oof_replay_verification,
        **evaluated,
    }
    if include_scored_oof:
        result["scored_oof"] = scored_oof
    return result


def _run_audited_pit_ranked_liquidity_ridge_oof(
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
    strategy_spec: Mapping[str, Any],
    artifact_version: int,
    training_window_sessions: int | None,
) -> dict[str, Any]:
    if not isinstance(settings, Settings):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity replay requires frozen settings"
        )
    if artifact_version not in {2, 3}:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity replay version is invalid"
        )
    shallow_gbdt_strategy_schema = (
        "development-pit-cross-sectional-shallow-gbdt-"
        "utility-logit-rolling-126-oof/v1"
    )
    run_variant = (
        resolve_ranked_liquidity_run_variant(strategy_spec)
        if (
            artifact_version == 3
            and strategy_spec.get("schema_version")
            == shallow_gbdt_strategy_schema
        )
        else None
    )
    model_id = (
        run_variant["model_adapter"].model_id
        if run_variant is not None
        else "continuous_ridge"
    )
    is_shallow_gbdt = model_id == "shallow_gbdt_utility_logit"
    expected_strategy_schema = (
        "development-pit-cross-sectional-ranked-liquidity-ridge-oof/v2"
        if artifact_version == 2
        else (
            run_variant["strategy_schema_version"]
            if run_variant is not None
            else (
                "development-pit-cross-sectional-ranked-liquidity-"
                "ridge-rolling-oof/v3"
            )
        )
    )
    if strategy_spec.get("schema_version") != expected_strategy_schema:
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity strategy identity is invalid"
        )
    frozen_window = strategy_spec["walk_forward"].get(
        "training_window_sessions"
    )
    if (
        (artifact_version == 2 and training_window_sessions is not None)
        or (
            artifact_version == 3
            and (
                training_window_sessions is None
                or int(frozen_window) != int(training_window_sessions)
            )
        )
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity training window differs from frozen strategy"
        )
    if artifact_version == 3 and (
        int(
            strategy_spec["walk_forward"]["minimum_training_sessions"]
        )
        != 126
        or int(training_window_sessions) != 126
        or int(strategy_spec["walk_forward"]["validation_sessions"]) != 63
        or (
            not is_shallow_gbdt
            and float(strategy_spec["model"]["ridge_lambda"]) != 1.0
        )
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity rolling strategy differs from preregistration"
        )

    def write_progress(stage: str, **details: Any) -> None:
        if is_shallow_gbdt:
            write_json(
                str(
                    Path(output_dir)
                    / run_variant["progress_file_name"]
                ),
                {
                    "schema_version": run_variant[
                        "progress_schema_version"
                    ],
                    "stage": str(stage),
                    **details,
                },
            )
            return
        _write_replay_progress(
            output_dir,
            stage,
            artifact_version=artifact_version,
            **details,
        )

    write_progress("starting")
    _assert_shared_strict_execution_contract(strategy_spec)
    producer_code = (
        run_variant["producer_binding"]()
        if is_shallow_gbdt
        else _producer_binding(artifact_version=artifact_version)
    )
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
            strategy_spec["required_market_session_count"]
        ):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity market session count differs from freeze"
            )
        write_progress(
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
        write_progress(
            "bars_loaded",
            bar_row_count=len(bars),
        )
        features, feature_receipt = (
            _build_exact_cross_section_features(
                bars,
                sessions,
                minimum_cross_section_members=int(
                    strategy_spec["minimum_cross_section_members"]
                ),
            )
        )
        verify_feature_receipt(features, feature_receipt)
        write_progress(
            "features_built",
            feature_candidate_count=len(features),
        )
        feature_candidate_count = len(features)
        feature_candidates = features.to_dict("records")
        tail_candidates, tail_receipt = _apply_uniform_tail_cutoff(
            feature_candidates,
            sessions=sessions,
            family=str(strategy_spec["signal_tag"]),
            strategy_spec=strategy_spec,
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
        tail_features = features.loc[
            features["signal_date"].astype(str).isin(tail_session_set),
            ["candidate_key", "signal_date", *FEATURE_NAMES],
        ].copy()
        if len(tail_features) != int(tail_receipt["kept_count"]):
            raise AuditedPITDevelopmentReplayError(
                "ranked-liquidity tail feature pool differs from cutoff"
            )
        del feature_candidates, features
        frames_by_symbol = _residual_frames_by_symbol(bars)
        del bars
        gc.collect()
        write_progress(
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
        write_progress(
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
                strategy_spec=strategy_spec,
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
                strategy_spec=strategy_spec,
                receipt_schema_version=(
                    run_variant["strict_outcome_schema_version"]
                    if is_shallow_gbdt
                    else (
                        "ranked-liquidity-ridge-strict-outcome/"
                        f"v{artifact_version}"
                    )
                ),
            )
        )
        compact_entry_receipt = _compact_receipt_summary(
            entry_receipt,
            omitted_fields={
                "events": {
                    "count_field": "event_count",
                    "sha256_field": "events_sha256",
                }
            },
        )
        compact_outcome_receipt = _compact_receipt_summary(
            outcome_receipt,
            omitted_fields={
                "execution_events": {
                    "count_field": "execution_event_count",
                    "sha256_field": "execution_events_sha256",
                }
            },
        )
        execution = {
            "completed_candidates": completed_candidates,
            "right_censored_positions": censored_positions,
            "tail_cutoff_receipt": tail_receipt,
            "entry_preflight_receipt": compact_entry_receipt,
            "outcome_receipt": compact_outcome_receipt,
        }
        write_progress(
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

    del (
        adapter,
        base_adapter,
        bulk_adapter,
        completed_candidates,
        connection,
        censored_positions,
        entry_receipt,
        executable_candidates,
        frames_by_symbol,
        outcome_receipt,
        raw_suspension_evidence,
        raw_terminal_listing_evidence,
        suspension_evidence,
        tail_candidates,
        terminal_listing_evidence,
        universe,
        verdict_cache,
    )
    gc.collect()

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
    if is_shallow_gbdt:
        evaluated = _score_and_evaluate_oof_variant(
            tail_features=tail_features,
            outcome_candidates=outcome_candidates,
            sessions=sessions,
            strategy_spec=strategy_spec,
            include_scored_oof=True,
        )
        scored_oof = evaluated.pop("scored_oof")
        oof_receipt = evaluated["oof_receipt"]
        oof_replay_verification = evaluated[
            "oof_replay_verification"
        ]
        scored_execution_candidates = evaluated[
            "scored_execution_candidates"
        ]
        positive_candidates = evaluated["positive_candidates"]
        positive_pool_receipt = evaluated["positive_pool_receipt"]
        main_sweep = evaluated["main_sweep"]
        baseline_sweep = evaluated["baseline_sweep"]
        main_selection_receipt = evaluated[
            "main_selection_receipt"
        ]
        baseline_selection_receipt = evaluated[
            "baseline_selection_receipt"
        ]
        main_selected = evaluated["main_selected"]
        baseline_selected = evaluated["baseline_selected"]
        advancement_gate = evaluated["advancement_gate_passed"]
        write_progress(
            "oof_scored",
            oof_candidate_count=len(scored_oof),
            fold_count=len(oof_receipt["folds"]),
        )
        write_progress(
            "selection_evaluated",
            positive_candidate_count=len(positive_candidates),
            advancement_gate_passed=advancement_gate,
        )
        compact_entry_receipt = execution["entry_preflight_receipt"]
        compact_outcome_receipt = execution["outcome_receipt"]
        payloads = build_ranked_liquidity_result_payloads(
            strategy_spec=strategy_spec,
            shared_receipts={
                "source": source,
                "features": {
                    "bar_loader_receipt": bar_loader_receipt,
                    "feature_receipt": feature_receipt,
                },
                "model": {
                    "oof_receipt": oof_receipt,
                    "oof_replay_verification": (
                        oof_replay_verification
                    ),
                },
                "execution": {
                    "security_code_transition_evidence": (
                        transition_evidence
                    ),
                    "bulk_next_open_evidence_receipt": (
                        bulk_next_open_receipt
                    ),
                    "tail_cutoff_receipt": execution[
                        "tail_cutoff_receipt"
                    ],
                    "entry_preflight_receipt": compact_entry_receipt,
                    "outcome_receipt": compact_outcome_receipt,
                    "completed_candidates": execution[
                        "completed_candidates"
                    ],
                    "right_censored_positions": execution[
                        "right_censored_positions"
                    ],
                },
                "selection": {
                    "scored_execution_candidates": (
                        scored_execution_candidates
                    ),
                    "positive_candidates": positive_candidates,
                    "positive_pool_receipt": positive_pool_receipt,
                    "main_sweep": main_sweep,
                    "amount_baseline_sweep": baseline_sweep,
                    "main_selection_receipt": (
                        main_selection_receipt
                    ),
                    "amount_baseline_selection_receipt": (
                        baseline_selection_receipt
                    ),
                    "main_selected": main_selected,
                    "baseline_selected": baseline_selected,
                    "advancement_gate_passed": advancement_gate,
                },
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
            },
        )
        result = _write_result_bundle(
            output_dir,
            main_payload=payloads["main_payload"],
            sidecar_payloads=payloads["sidecar_payloads"],
            expected_producer_code=payloads["producer_code"],
        )
        del (
            baseline_selected,
            baseline_selection_receipt,
            baseline_sweep,
            evaluated,
            execution,
            main_selected,
            main_selection_receipt,
            main_sweep,
            oof_receipt,
            oof_replay_verification,
            payloads,
            positive_candidates,
            positive_pool_receipt,
            scored_execution_candidates,
        )
        gc.collect()
        verification = verify_shallow_gbdt_result_bundle(
            result,
            tail_features=tail_features,
            outcome_candidates=outcome_candidates,
            sessions=sessions,
            scored_oof=scored_oof,
            expected_source=source,
            expected_outcome_receipt=compact_outcome_receipt,
        )
        runtime_verification = _write_content_addressed(
            Path(output_dir) / "verifications",
            verification,
        )
        result = {
            **result,
            "verification": verification,
            "runtime_verification": runtime_verification,
        }
        write_progress(
            "completed",
            artifact_sha256=result["artifact"]["artifact_sha256"],
            advancement_gate_passed=advancement_gate,
        )
        return result
    scored_oof, oof_receipt = _build_purged_oof_scores(
        tail_features,
        outcome_candidates,
        sessions,
        minimum_training_sessions=int(
            strategy_spec["walk_forward"][
                "minimum_training_sessions"
            ]
        ),
        training_window_sessions=training_window_sessions,
        validation_sessions=int(
            strategy_spec["walk_forward"][
                "validation_sessions"
            ]
        ),
        require_nonempty_validation_folds=True,
        receipt_schema_version=(
            "ranked-liquidity-ridge-purged-oof-receipt/"
            f"v{artifact_version}"
        ),
        ridge_lambda=float(strategy_spec["model"]["ridge_lambda"]),
        strategy_spec=strategy_spec,
    )
    expected_fold_ranges = _fold_ranges(
        sessions,
        minimum_training_sessions=int(
            strategy_spec["walk_forward"][
                "minimum_training_sessions"
            ]
        ),
        validation_sessions=int(
            strategy_spec["walk_forward"][
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
        != int(strategy_spec["required_oof_fold_count"])
        or observed_fold_ranges != expected_fold_ranges
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity OOF folds differ from frozen six-fold plan"
        )
    oof_replay_verification = None
    if training_window_sessions is not None:
        oof_replay_verification = verify_rolling_oof_receipt(
            tail_features,
            outcome_candidates,
            sessions,
            scored_oof,
            oof_receipt,
            minimum_training_sessions=int(
                strategy_spec["walk_forward"][
                    "minimum_training_sessions"
                ]
            ),
            training_window_sessions=training_window_sessions,
            validation_sessions=int(
                strategy_spec["walk_forward"]["validation_sessions"]
            ),
        )
    write_progress(
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
        strategy_spec["walk_forward"][
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
        strategy_spec=strategy_spec,
        sweep_schema_version=(
            f"strict-ranked-liquidity-ridge-fixed-oof/v{artifact_version}"
        ),
    )
    baseline_sweep, baseline_selection_receipt = _evaluate_fixed_oof(
        positive_candidates,
        rank_mode="signal_date_amount",
        evaluation_session_dates=evaluation_sessions,
        strategy_spec=strategy_spec,
        sweep_schema_version=(
            f"strict-ranked-liquidity-ridge-fixed-oof/v{artifact_version}"
        ),
    )
    selection_spec = strategy_spec["selection"]
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
    write_progress(
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
    scored_execution_evidence = (
        _compact_scored_execution_evidence(
            scored_execution_candidates
        )
        if artifact_version == 3
        else None
    )
    outcome_membership_evidence = (
        _compact_outcome_membership_evidence(
            execution["completed_candidates"],
            execution["right_censored_positions"],
        )
        if artifact_version == 3
        else None
    )
    strategy_sha256 = _sha256(strategy_spec)

    sidecar_common = {
        "strategy_sha256": strategy_sha256,
        "source": source,
        "producer_code": producer_code,
    }
    feature_sidecar = {
        "schema_version": (
            f"ranked-liquidity-feature-sidecar/v{artifact_version}"
        ),
        **sidecar_common,
        "bar_loader_receipt": bar_loader_receipt,
        "feature_receipt": feature_receipt,
    }
    model_sidecar = {
        "schema_version": (
            f"ranked-liquidity-model-sidecar/v{artifact_version}"
        ),
        **sidecar_common,
        "oof_receipt": oof_receipt,
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": oof_receipt["oof_scores_sha256"],
    }
    if oof_replay_verification is not None:
        model_sidecar["oof_replay_verification"] = (
            oof_replay_verification
        )
    compact_entry_receipt = execution["entry_preflight_receipt"]
    compact_outcome_receipt = execution["outcome_receipt"]
    execution_sidecar = {
        "schema_version": (
            f"ranked-liquidity-execution-sidecar/v{artifact_version}"
        ),
        **sidecar_common,
        "security_code_transition_evidence": transition_evidence,
        "bulk_next_open_evidence_receipt": bulk_next_open_receipt,
        "tail_cutoff_receipt": execution["tail_cutoff_receipt"],
        "entry_preflight_receipt": compact_entry_receipt,
        "outcome_receipt": compact_outcome_receipt,
        "completed_candidate_count": len(
            execution["completed_candidates"]
        ),
        "right_censored_position_count": len(
            execution["right_censored_positions"]
        ),
    }
    if artifact_version == 3:
        execution_sidecar.update(
            {
                "outcome_membership_evidence": (
                    outcome_membership_evidence
                ),
                "completed_candidates_sha256": (
                    outcome_membership_evidence[
                        "completed_candidates_sha256"
                    ]
                ),
                "completed_candidate_payload_hashes_sha256": (
                    outcome_membership_evidence[
                        "completed_candidate_payload_hashes_sha256"
                    ]
                ),
                "right_censored_positions_sha256": (
                    outcome_membership_evidence[
                        "right_censored_positions_sha256"
                    ]
                ),
                "right_censored_position_payload_hashes_sha256": (
                    outcome_membership_evidence[
                        "right_censored_position_payload_hashes_sha256"
                    ]
                ),
                "strict_outcome_candidate_payload_hashes_sha256": (
                    outcome_membership_evidence[
                        "strict_outcome_candidate_payload_hashes_sha256"
                    ]
                ),
            }
        )
    else:
        execution_sidecar.update(
            {
                "completed_candidates_sha256": _sha256(
                    execution["completed_candidates"]
                ),
                "right_censored_positions_sha256": _sha256(
                    execution["right_censored_positions"]
                ),
            }
        )
    selection_sidecar = {
        "schema_version": (
            f"ranked-liquidity-selection-sidecar/v{artifact_version}"
        ),
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
    if artifact_version == 3:
        selection_sidecar.update(
            {
                "scored_execution_candidate_evidence": (
                    scored_execution_evidence
                ),
                "positive_candidate_count": len(positive_candidates),
                "positive_candidate_payload_hashes_sha256": (
                    scored_execution_evidence[
                        "positive_candidate_payload_hashes_sha256"
                    ]
                ),
                "positive_candidate_keys_sha256": (
                    positive_pool_receipt[
                        "positive_candidate_keys_sha256"
                    ]
                ),
            }
        )
    main_payload = {
        "schema_version": (
            f"ranked-liquidity-ridge-result/v{artifact_version}"
        ),
        "strategy_sha256": strategy_sha256,
        "strategy": {
            **strategy_spec,
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
        "feature_candidate_count": feature_candidate_count,
        "feature_rows_sha256": feature_receipt["feature_rows_sha256"],
        "strict_outcome_candidate_count": len(outcome_candidates),
        "strict_outcome_candidates_sha256": _sha256(outcome_candidates),
        "oof_candidate_count": len(scored_oof),
        "oof_scores_sha256": oof_receipt["oof_scores_sha256"],
        "scored_execution_candidate_count": len(
            scored_execution_candidates
        ),
        "positive_candidate_count": len(positive_candidates),
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
    if artifact_version == 3:
        main_payload.update(
            {
                "scored_execution_candidate_evidence_rows_sha256": (
                    scored_execution_evidence["rows_sha256"]
                ),
                "strict_outcome_candidate_payload_hashes_sha256": (
                    outcome_membership_evidence[
                        "strict_outcome_candidate_payload_hashes_sha256"
                    ]
                ),
                "scored_execution_candidate_payload_hashes_sha256": (
                    scored_execution_evidence[
                        "candidate_payload_hashes_sha256"
                    ]
                ),
                "positive_candidate_payload_hashes_sha256": (
                    scored_execution_evidence[
                        "positive_candidate_payload_hashes_sha256"
                    ]
                ),
                "positive_candidate_keys_sha256": (
                    positive_pool_receipt[
                        "positive_candidate_keys_sha256"
                    ]
                ),
                "positive_pool_receipt_sha256": (
                    positive_pool_receipt["receipt_sha256"]
                ),
            }
        )
    else:
        main_payload.update(
            {
                "scored_execution_candidates_sha256": _sha256(
                    scored_execution_candidates
                ),
                "positive_candidates_sha256": _sha256(
                    positive_candidates
                ),
            }
        )
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
    if artifact_version == 3:
        verification = verify_rolling_result_bundle(
            result,
            expected_strategy_spec=strategy_spec,
        )
        runtime_verification = _write_content_addressed(
            Path(output_dir) / "verifications",
            verification,
        )
        result = {
            **result,
            "verification": verification,
            "runtime_verification": runtime_verification,
        }
    write_progress(
        "completed",
        artifact_sha256=result["artifact"]["artifact_sha256"],
        advancement_gate_passed=advancement_gate,
    )
    return result


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
    return _run_audited_pit_ranked_liquidity_ridge_oof(
        settings=settings,
        audited_pit_universe_path=audited_pit_universe_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        temporal_contract_path=temporal_contract_path,
        expected_temporal_contract_sha256=(
            expected_temporal_contract_sha256
        ),
        security_code_transition_evidence_root=(
            security_code_transition_evidence_root
        ),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        start_date=start_date,
        end_date=end_date,
        output_dir=output_dir,
        strategy_spec=CONTINUOUS_RIDGE_OOF_SPEC,
        artifact_version=2,
        training_window_sessions=None,
    )


def run_audited_pit_ranked_liquidity_ridge_rolling_oof(
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
    if (
        _sha256(ROLLING_CONTINUOUS_RIDGE_OOF_SPEC)
        != _ROLLING_CONTINUOUS_RIDGE_OOF_SPEC_SHA256
    ):
        raise AuditedPITDevelopmentReplayError(
            "ranked-liquidity rolling strategy differs from "
            "preregistered canonical hash"
        )
    training_window_sessions = int(
        ROLLING_CONTINUOUS_RIDGE_OOF_SPEC["walk_forward"][
            "training_window_sessions"
        ]
    )
    return _run_audited_pit_ranked_liquidity_ridge_oof(
        settings=settings,
        audited_pit_universe_path=audited_pit_universe_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        temporal_contract_path=temporal_contract_path,
        expected_temporal_contract_sha256=(
            expected_temporal_contract_sha256
        ),
        security_code_transition_evidence_root=(
            security_code_transition_evidence_root
        ),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        start_date=start_date,
        end_date=end_date,
        output_dir=output_dir,
        strategy_spec=ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
        artifact_version=3,
        training_window_sessions=training_window_sessions,
    )


def run_audited_pit_ranked_liquidity_shallow_gbdt_rolling_oof(
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
    from app import audited_pit_shallow_gbdt as shallow_gbdt

    if (
        _sha256(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
        != shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
    ):
        raise AuditedPITDevelopmentReplayError(
            "shallow GBDT rolling strategy differs from preregistered "
            "canonical hash"
        )
    training_window_sessions = int(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["walk_forward"][
            "training_window_sessions"
        ]
    )
    return _run_audited_pit_ranked_liquidity_ridge_oof(
        settings=settings,
        audited_pit_universe_path=audited_pit_universe_path,
        expected_coverage_audit_sha256=expected_coverage_audit_sha256,
        expected_artifact_root_sha256=expected_artifact_root_sha256,
        temporal_contract_path=temporal_contract_path,
        expected_temporal_contract_sha256=(
            expected_temporal_contract_sha256
        ),
        security_code_transition_evidence_root=(
            security_code_transition_evidence_root
        ),
        expected_security_code_transition_contract_sha256=(
            expected_security_code_transition_contract_sha256
        ),
        start_date=start_date,
        end_date=end_date,
        output_dir=output_dir,
        strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
        artifact_version=3,
        training_window_sessions=training_window_sessions,
    )
