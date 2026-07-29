"""Frozen development-only factor-v3 points contract and pure transforms."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any

from app.research_scope import (
    is_mainboard_chinext_symbol,
    market_scope_contract,
)


FACTOR_V3_POINTS_FEATURE_NAMES = (
    "turnover_rate_f_rank",
    "abnormal_turnover_rate_f_20_to_250_rank",
)
FACTOR_V3_POINTS_ARMS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "control": (),
        "turnover_level": (FACTOR_V3_POINTS_FEATURE_NAMES[0],),
        "abnormal_turnover": (FACTOR_V3_POINTS_FEATURE_NAMES[1],),
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TS_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_SESSION_FIELDS = frozenset({"session_position", "trade_date"})
_PARENT_FIELDS = frozenset({"candidate_key", "signal_date", "ts_code"})
_DAILY_BASIC_FIELDS = frozenset({"ts_code", "trade_date", "turnover_rate_f"})


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("factor-v3 value is not strict canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


_FACTOR_V3_POINTS_PARENT_BINDING = {
    "schema_version": "audited-pit-factor-v3-points-parent-binding/v1",
    "sample_reference": "factor_v2_development_4_common_eligible_sample",
    "factor_v2_parent_artifact_sha256": (
        "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
    ),
    "factor_v2_parent_manifest_file_sha256": (
        "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
    ),
    "factor_v2_points_predecessor_spec_sha256": (
        "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
    ),
    "parent_feature_row_count": 1_796_835,
    "parent_feature_rows_sha256": (
        "7cbd9bfe61052f87d736f6a7d14fdc1ad350ce66add98347ea36d9c2ed48b337"
    ),
    "common_eligible_candidate_count": 1_796_834,
    "common_eligible_candidate_keys_sha256": (
        "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
    ),
    "preregistered_suspension_excluded_candidate_count": 1,
    "sessions": {
        "count": 483,
        "start": "2024-07-05",
        "end": "2026-07-03",
        "sha256": ("d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"),
    },
}
FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256 = (
    "de7c9a3715631d186e730d775673df1ed753717be44029b6c12dba34e56a5ef5"
)
if (
    canonical_sha256(_FACTOR_V3_POINTS_PARENT_BINDING)
    != FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256
):
    raise RuntimeError("frozen factor-v3 points parent binding drifted")

FACTOR_V3_POINTS_CONTRACT = {
    "schema_version": "audited-pit-factor-v3-points-contract/v1",
    "temporal_role": "development_4",
    "development_only": True,
    "market_scope": market_scope_contract(),
    "parent_binding": deepcopy(_FACTOR_V3_POINTS_PARENT_BINDING),
    "parent_binding_root_sha256": (FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256),
    "parent_sample_policy": {
        "identity_fields": ["candidate_key", "signal_date"],
        "coverage": "exact_full_parent",
        "arbitrary_row_drops_permitted": False,
        "missing_points_history_policy": "fail_closed",
        "output_identity_must_equal_parent_identity": True,
    },
    "sources": {
        "daily_basic": {
            "provider_semantics": "Tushare-compatible Jiaoch",
            "credential_slot": "points-primary",
            "field": "turnover_rate_f",
            "lag_market_sessions": 1,
            "latest_usable_source_session": "T-1",
            "recommendation_1502_uses": "T-1",
            "published_at_assumption_used": False,
            "factor_source": True,
        },
        "moneyflow": {
            "provider_semantics": "Tushare-compatible Jiaoch",
            "credential_slot": "points-primary",
            "lag_market_sessions": 1,
            "latest_usable_source_session": "T-1",
            "recommendation_1502_uses": "T-1",
            "published_at_assumption_used": False,
            "diagnostic_only": True,
            "hard_filter_permitted": False,
            "first_round_arm_permitted": False,
        },
    },
    "features": {
        "turnover_rate_f_rank": {
            "raw_value": "turnover_rate_f[T-1]",
            "cross_section_transform": "deterministic_midrank",
            "mapping": "2*midrank/(N+1)-1",
            "tie_policy": "equal_values_share_average_rank",
            "scope": "same_signal_date_exact_parent_sample",
        },
        "abnormal_turnover_rate_f_20_to_250_rank": {
            "raw_formula": "log(mean_20/mean_250)",
            "history_field": "turnover_rate_f",
            "history_order": "strict_market_session_order",
            "minimum_history_market_sessions": 250,
            "latest_source_session": "T-1",
            "cross_section_transform": "deterministic_midrank",
            "mapping": "2*midrank/(N+1)-1",
            "tie_policy": "equal_values_share_average_rank",
            "scope": "same_signal_date_exact_parent_sample",
            "variant": "free_float_turnover_rate_f",
            "literature_replication_claimed": False,
            "literature_boundary": (
                "literature-inspired free-float variant; PMO is not "
                "replicated verbatim and monthly long-short returns do not "
                "transfer to this long-only project"
            ),
        },
    },
    "arms": {arm: list(feature_names) for arm, feature_names in FACTOR_V3_POINTS_ARMS.items()},
    "arm_order": list(FACTOR_V3_POINTS_ARMS),
    "combination_search_permitted": False,
    "hyperparameter_search": False,
    "source_anchors": {
        "abnormal_turnover_literature": {
            "title": "Size and Value in China",
            "doi": "10.1016/j.jfineco.2019.03.008",
        },
        "china_anomaly_replication": {
            "title": ("Replicating and Digesting Anomalies in the Chinese A-share Market"),
            "doi": "10.1287/mnsc.2023.4904",
        },
        "daily_basic_documentation": ("https://tushare.pro/document/2?doc_id=32"),
    },
    "research_warnings": {
        "pmo_reported_annualized_long_short_pct": 12.0,
        "pmo_direction": "long_low_abnormal_turnover_short_high",
        "china_anomaly_replication_insignificant_pct": 83.37,
        "paper_return_is_expected_to_transfer": False,
        "expand_search_from_paper_result_permitted": False,
        "project_frozen_oof_is_only_return_authority": True,
        "mainboard_value_weighting_and_capacity_must_be_reported": True,
    },
    "experiment_gate": {
        "factor_v2_verified_terminal_decision_required": True,
        "factor_v2_verified_terminal_decision_present": False,
        "experiment_launch_eligible": False,
    },
    "embargo_consumed": False,
    "final_oos_consumed": False,
    "production_profile_registered": False,
    "production_recommendation_eligible": False,
}
FACTOR_V3_POINTS_CONTRACT_SHA256 = (
    "791ca95296969424f0255ef7aeb011e98b4051e0e2b62dbdac12213716b3448b"
)
if canonical_sha256(FACTOR_V3_POINTS_CONTRACT) != FACTOR_V3_POINTS_CONTRACT_SHA256:
    raise RuntimeError("frozen factor-v3 points contract drifted")

FACTOR_V3_POINTS_ARM_STRATEGY_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "control": ("cd6e0e40954c5ad7f956b10b9426d3e40ce07e7e3cd6292baa7a4a32620259f9"),
        "turnover_level": ("0d9bd4d0801e0f6a71e8c9d509cb3cb417efa8ad895c152d9363dff43a93459a"),
        "abnormal_turnover": ("a0754536544368a9b3743f5b6f7a0ced62529a2bbbae85f0f6aa0d4f828028b9"),
    }
)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("factor-v3 JSON contains a duplicate key")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"factor-v3 JSON contains non-finite value: {value}")


def parse_strict_factor_v3_json(raw: str | bytes) -> dict[str, Any]:
    if not isinstance(raw, (str, bytes)):
        raise ValueError("factor-v3 JSON must be text or bytes")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("factor-v3 JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("factor-v3 JSON root must be an object")
    _canonical_json(value)
    return value


def _strict_fields(
    value: Any,
    *,
    expected: frozenset[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"factor-v3 {field} fields are invalid")
    return value


def _strict_iso_date(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"factor-v3 {field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"factor-v3 {field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"factor-v3 {field} must be an ISO date")
    return value


def _strict_ts_code(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _TS_CODE_RE.fullmatch(value) is None
        or not is_mainboard_chinext_symbol(value)
    ):
        raise ValueError("factor-v3 ts_code is outside the frozen market scope")
    return value


def _strict_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"factor-v3 {field} must not be bool")
    if not isinstance(value, (int, float)):
        raise ValueError(f"factor-v3 {field} must be numeric")
    output = float(value)
    if not math.isfinite(output):
        raise ValueError(f"factor-v3 {field} must be finite")
    return output


def _strict_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 {field} must be a SHA-256")
    return value


def _validated_sessions(
    sessions: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, str], ...]:
    if isinstance(sessions, (str, bytes)) or not isinstance(sessions, Sequence):
        raise ValueError("factor-v3 sessions must be a sequence")
    normalized: list[tuple[int, str]] = []
    for raw in sessions:
        row = _strict_fields(
            raw,
            expected=_SESSION_FIELDS,
            field="session",
        )
        position = row["session_position"]
        if isinstance(position, bool):
            raise ValueError("factor-v3 session_position must not be bool")
        if not isinstance(position, int):
            raise ValueError("factor-v3 session_position must be an integer")
        normalized.append(
            (
                position,
                _strict_iso_date(
                    row["trade_date"],
                    field="session trade_date",
                ),
            )
        )
    if not normalized:
        raise ValueError("factor-v3 sessions must not be empty")
    first_position = normalized[0][0]
    if [position for position, _ in normalized] != list(
        range(first_position, first_position + len(normalized))
    ):
        raise ValueError("factor-v3 session positions must be consecutive")
    dates = [trade_date for _, trade_date in normalized]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("factor-v3 session dates must be ordered and unique")
    return tuple(normalized)


def _validated_parent_rows(
    parent_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], ...]:
    if isinstance(parent_rows, (str, bytes)) or not isinstance(parent_rows, Sequence):
        raise ValueError("factor-v3 parent rows must be a sequence")
    normalized: list[dict[str, str]] = []
    for raw in parent_rows:
        row = _strict_fields(
            raw,
            expected=_PARENT_FIELDS,
            field="parent",
        )
        candidate_key = row["candidate_key"]
        if not isinstance(candidate_key, str) or not candidate_key:
            raise ValueError("factor-v3 candidate_key is invalid")
        signal_date = _strict_iso_date(
            row["signal_date"],
            field="parent signal_date",
        )
        ts_code = _strict_ts_code(row["ts_code"])
        expected_key = f"cn-a-share:{ts_code}|{signal_date}"
        if candidate_key != expected_key:
            raise ValueError("factor-v3 candidate_key is not bound to ts_code and signal_date")
        normalized.append(
            {
                "candidate_key": candidate_key,
                "signal_date": signal_date,
                "ts_code": ts_code,
            }
        )
    if not normalized:
        raise ValueError("factor-v3 parent rows must not be empty")
    keys = [row["candidate_key"] for row in normalized]
    identities = [(row["candidate_key"], row["signal_date"]) for row in normalized]
    if len(keys) != len(set(keys)) or len(identities) != len(set(identities)):
        raise ValueError("factor-v3 duplicate parent identity")
    normalized.sort(key=lambda row: (row["signal_date"], row["candidate_key"]))
    return tuple(normalized)


def _parent_identity_payload(
    parent_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
        }
        for row in _validated_parent_rows(parent_rows)
    ]


def factor_v3_parent_identity_root(
    parent_rows: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_sha256(_parent_identity_payload(parent_rows))


def compute_abnormal_turnover_rate_f_20_to_250(
    history: Sequence[Any],
) -> float:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("factor-v3 turnover history must be a sequence")
    if len(history) != 250:
        raise ValueError("factor-v3 abnormal turnover requires exactly 250 sessions")
    normalized = [_strict_number(value, field="turnover_rate_f") for value in history]
    if any(value < 0.0 for value in normalized):
        raise ValueError("factor-v3 turnover_rate_f must be non-negative")
    mean_20 = math.fsum(normalized[-20:]) / 20.0
    mean_250 = math.fsum(normalized) / 250.0
    if mean_20 <= 0.0 or mean_250 <= 0.0:
        raise ValueError("factor-v3 abnormal turnover means must be positive")
    output = math.log(mean_20 / mean_250)
    if not math.isfinite(output):
        raise ValueError("factor-v3 abnormal turnover result must be finite")
    return output


def _deterministic_midrank(values: Sequence[float]) -> list[float]:
    if not values:
        raise ValueError("factor-v3 cross-section must not be empty")
    ordered = sorted(
        ((value, ordinal) for ordinal, value in enumerate(values)),
        key=lambda item: (item[0], item[1]),
    )
    output = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][0] == ordered[start][0]:
            end += 1
        midrank = ((start + 1) + end) / 2.0
        normalized_rank = 2.0 * midrank / (len(values) + 1.0) - 1.0
        for _, ordinal in ordered[start:end]:
            output[ordinal] = normalized_rank
        start = end
    return output


def _validated_daily_basic_rows(
    daily_basic_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    if isinstance(daily_basic_rows, (str, bytes)) or not isinstance(daily_basic_rows, Sequence):
        raise ValueError("factor-v3 daily_basic rows must be a sequence")
    normalized: dict[tuple[str, str], float] = {}
    for raw in daily_basic_rows:
        row = _strict_fields(
            raw,
            expected=_DAILY_BASIC_FIELDS,
            field="daily_basic",
        )
        ts_code = _strict_ts_code(row["ts_code"])
        trade_date = _strict_iso_date(
            row["trade_date"],
            field="daily_basic trade_date",
        )
        key = (ts_code, trade_date)
        if key in normalized:
            raise ValueError("factor-v3 duplicate daily_basic identity")
        turnover = _strict_number(
            row["turnover_rate_f"],
            field="turnover_rate_f",
        )
        if turnover < 0.0:
            raise ValueError("factor-v3 turnover_rate_f must be non-negative")
        normalized[key] = turnover
    return normalized


def _arm_strategy_payload(arm: str) -> dict[str, Any]:
    if not isinstance(arm, str) or arm not in FACTOR_V3_POINTS_ARMS:
        raise ValueError("factor-v3 points arm is not frozen")
    return {
        "schema_version": "audited-pit-factor-v3-points-arm/v1",
        "factor_v3_points_contract_sha256": (FACTOR_V3_POINTS_CONTRACT_SHA256),
        "arm": arm,
        "points_feature_names": list(FACTOR_V3_POINTS_ARMS[arm]),
        "base_model_and_evaluation": ("inherit_exact_factor_v2_verified_terminal_decision"),
        "sample_policy_sha256": canonical_sha256(FACTOR_V3_POINTS_CONTRACT["parent_sample_policy"]),
        "combination_search_permitted": False,
    }


def assert_frozen_factor_v3_points_contract() -> None:
    if (
        canonical_sha256(_FACTOR_V3_POINTS_PARENT_BINDING)
        != FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256
        or canonical_sha256(FACTOR_V3_POINTS_CONTRACT) != FACTOR_V3_POINTS_CONTRACT_SHA256
        or set(FACTOR_V3_POINTS_ARM_STRATEGY_SHA256) != set(FACTOR_V3_POINTS_ARMS)
        or any(
            canonical_sha256(_arm_strategy_payload(arm))
            != FACTOR_V3_POINTS_ARM_STRATEGY_SHA256[arm]
            for arm in FACTOR_V3_POINTS_ARMS
        )
    ):
        raise RuntimeError("frozen factor-v3 points contract drifted")


def factor_v3_points_arm_contract(arm: str) -> dict[str, Any]:
    assert_frozen_factor_v3_points_contract()
    payload = _arm_strategy_payload(arm)
    strategy_sha256 = canonical_sha256(payload)
    return {
        **deepcopy(payload),
        "strategy_sha256": strategy_sha256,
    }


def materialize_factor_v3_points_rows(
    *,
    sessions: Sequence[Mapping[str, Any]],
    parent_rows: Sequence[Mapping[str, Any]],
    daily_basic_rows: Sequence[Mapping[str, Any]],
    parent_binding_root_sha256: str,
    expected_parent_identity_root_sha256: str,
) -> dict[str, Any]:
    assert_frozen_factor_v3_points_contract()
    supplied_parent_binding = _strict_sha256(
        parent_binding_root_sha256,
        field="parent binding root",
    )
    if supplied_parent_binding != FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256:
        raise ValueError("factor-v3 parent binding root drifted")
    expected_identity = _strict_sha256(
        expected_parent_identity_root_sha256,
        field="parent identity root",
    )
    normalized_sessions = _validated_sessions(sessions)
    normalized_parents = _validated_parent_rows(parent_rows)
    parent_identity_payload = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
        }
        for row in normalized_parents
    ]
    observed_identity = canonical_sha256(parent_identity_payload)
    if observed_identity != expected_identity:
        raise ValueError("factor-v3 parent identity root drifted")

    dates = [trade_date for _, trade_date in normalized_sessions]
    position_by_date = {trade_date: ordinal for ordinal, trade_date in enumerate(dates)}
    required_source_keys: set[tuple[str, str]] = set()
    windows_by_candidate: dict[str, tuple[str, ...]] = {}
    for parent in normalized_parents:
        signal_position = position_by_date.get(parent["signal_date"])
        if signal_position is None:
            raise ValueError("factor-v3 parent signal_date is outside sessions")
        if signal_position < 250:
            raise ValueError("factor-v3 parent lacks 250 prior market sessions")
        window = tuple(dates[signal_position - 250 : signal_position])
        windows_by_candidate[parent["candidate_key"]] = window
        required_source_keys.update((parent["ts_code"], trade_date) for trade_date in window)

    source = _validated_daily_basic_rows(daily_basic_rows)
    observed_source_keys = set(source)
    missing = required_source_keys - observed_source_keys
    if missing:
        raise ValueError("factor-v3 daily_basic source rows are missing")
    unused = observed_source_keys - required_source_keys
    if unused:
        raise ValueError("factor-v3 daily_basic source rows are unused")

    raw_by_date: dict[str, list[dict[str, Any]]] = {}
    for parent in normalized_parents:
        window = windows_by_candidate[parent["candidate_key"]]
        history = [source[(parent["ts_code"], trade_date)] for trade_date in window]
        raw_by_date.setdefault(parent["signal_date"], []).append(
            {
                **parent,
                "source_session": window[-1],
                "turnover_rate_f": history[-1],
                "abnormal_turnover_rate_f_20_to_250": (
                    compute_abnormal_turnover_rate_f_20_to_250(history)
                ),
            }
        )

    output_rows: list[dict[str, Any]] = []
    for signal_date in sorted(raw_by_date):
        cross_section = sorted(
            raw_by_date[signal_date],
            key=lambda row: row["candidate_key"],
        )
        level_ranks = _deterministic_midrank([row["turnover_rate_f"] for row in cross_section])
        abnormal_ranks = _deterministic_midrank(
            [row["abnormal_turnover_rate_f_20_to_250"] for row in cross_section]
        )
        for row, level_rank, abnormal_rank in zip(
            cross_section,
            level_ranks,
            abnormal_ranks,
            strict=True,
        ):
            output_rows.append(
                {
                    "candidate_key": row["candidate_key"],
                    "signal_date": signal_date,
                    "source_session": row["source_session"],
                    "turnover_rate_f_rank": level_rank,
                    "abnormal_turnover_rate_f_20_to_250_rank": (abnormal_rank),
                }
            )

    output_identity_payload = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
        }
        for row in output_rows
    ]
    output_identity_root = canonical_sha256(output_identity_payload)
    if len(output_rows) != len(normalized_parents) or output_identity_root != observed_identity:
        raise ValueError("factor-v3 output parent coverage drifted")
    source_payload = [
        {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "turnover_rate_f": source[(ts_code, trade_date)],
        }
        for ts_code, trade_date in sorted(source)
    ]
    receipt_unsigned = {
        "schema_version": "audited-pit-factor-v3-points-pure-receipt/v1",
        "factor_v3_points_contract_sha256": (FACTOR_V3_POINTS_CONTRACT_SHA256),
        "parent_binding_root_sha256": supplied_parent_binding,
        "parent_identity_root_sha256": observed_identity,
        "parent_row_count": len(normalized_parents),
        "daily_basic_source_row_count": len(source_payload),
        "daily_basic_source_rows_sha256": canonical_sha256(source_payload),
        "output_row_count": len(output_rows),
        "dropped_parent_row_count": 0,
        "output_identity_root_sha256": output_identity_root,
        "output_rows_sha256": canonical_sha256(output_rows),
        "latest_source_lag_market_sessions": 1,
        "moneyflow_consumed": False,
        "experiment_launch_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    receipt = {
        **receipt_unsigned,
        "receipt_sha256": canonical_sha256(receipt_unsigned),
    }
    return {"rows": output_rows, "receipt": receipt}
