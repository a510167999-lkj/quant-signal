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


_FACTOR_V3_POINTS_PARENT_EXPECTATION = {
    "schema_version": "audited-pit-factor-v3-points-parent-expectation/v2",
    "sample_reference": "factor_v2_development_4_common_eligible_sample",
    "factor_v2_parent_artifact_sha256": (
        "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
    ),
    "factor_v2_parent_manifest_file_sha256": (
        "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
    ),
    "factor_v2_common_eligible_overlay_artifact_sha256": (
        "abd4b2166da4520952d7bfc5c8a988bc0a8027dd576a90ae0fdda2560b3a02f8"
    ),
    "factor_v2_common_eligible_overlay_manifest_file_sha256": (
        "9131f15e13f247a4663fae658af544b94bf2af01a3494b8eb0a3d0c095ee1312"
    ),
    "factor_v2_points_predecessor_spec_sha256": (
        "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
    ),
    "factor_v2_common_eligible_receipt_sha256": (
        "86199759116362c6317db7ca73b78dc56c9adaada57c2661a4b33028be44db4d"
    ),
    "original_parent_feature_row_count": 1_796_835,
    "original_parent_feature_rows_sha256": (
        "7cbd9bfe61052f87d736f6a7d14fdc1ad350ce66add98347ea36d9c2ed48b337"
    ),
    "all_candidate_keys_sha256": (
        "ffed3b95e3b31803c7e993af06cf155e42c379590a41c919989e31d5cf374acd"
    ),
    "common_eligible_candidate_count": 1_796_834,
    "common_eligible_candidate_keys_sha256": (
        "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
    ),
    "common_eligible_source_feature_rows_sha256": (
        "62f02c3d3b068f50b95d29a06a218e58dd72693113081570ded95ae73d7ec59f"
    ),
    "preregistered_suspension_excluded_candidate_count": 1,
    "preregistered_suspension_excluded_candidate_keys_sha256": (
        "a2149f2a5de78780459652aaf64bcb940ab2ad7631dd3260007f1ad1ec1ab18a"
    ),
    "sessions": {
        "count": 483,
        "start": "2024-07-05",
        "end": "2026-07-03",
        "sha256": ("d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"),
    },
}
FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256 = (
    "25f2802cfa11fa61a3e08f44886141bd82c01c89e7d9ee01a3e882b8a3561c99"
)
if (
    canonical_sha256(_FACTOR_V3_POINTS_PARENT_EXPECTATION)
    != FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256
):
    raise RuntimeError("frozen factor-v3 points parent expectation drifted")

FACTOR_V3_POINTS_CONTRACT = {
    "schema_version": "audited-pit-factor-v3-points-contract/v1",
    "temporal_role": "development_4",
    "development_only": True,
    "market_scope": market_scope_contract(),
    "preregistered_parent_expectation": deepcopy(_FACTOR_V3_POINTS_PARENT_EXPECTATION),
    "preregistered_parent_expectation_sha256": (FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256),
    "formal_parent_sample_policy": {
        "identity_fields": ["candidate_key", "signal_date"],
        "source_sample_reference": "factor_v2_development_4_common_eligible_sample",
        "source_candidate_count": 1_796_834,
        "source_candidate_keys_sha256": (
            "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
        ),
        "source_feature_rows_sha256": (
            "62f02c3d3b068f50b95d29a06a218e58dd72693113081570ded95ae73d7ec59f"
        ),
        "coverage": "deterministic_history_eligible_subset_of_common_eligible_parent",
        "same_history_eligible_subset_for_all_arms": True,
        "arbitrary_row_drops_permitted": False,
        "silent_row_drops_permitted": False,
        "zero_fill_permitted": False,
        "missing_points_history_policy": (
            "exclude_only_with_preregistered_reason_and_content_addressed_authority_evidence"
        ),
        "output_identity_must_equal_history_eligible_subset_identity": True,
        "history_eligible_subset_policy": {
            "schema_version": ("audited-pit-factor-v3-points-history-eligible-subset-policy/v1"),
            "selection": ("deterministic_fail_closed_from_verified_source_bound_authorities"),
            "window_market_sessions": {
                "short": 20,
                "long": 250,
            },
            "minimum_observed_trading_records": {
                "short_window": 15,
                "long_window": 120,
            },
            "minimum_ipo_age_calendar_months": 6,
            "latest_usable_source_session": "T-1",
            "allowed_exclusion_reasons": [
                "ipo_age_less_than_6_calendar_months",
                ("observed_trading_records_less_than_15_in_20_market_session_window"),
                ("observed_trading_records_less_than_120_in_250_market_session_window"),
                ("authoritative_daily_cross_section_row_missing_in_required_history_window"),
                "unresolved_authoritative_security_code_transition",
            ],
            "observed_trading_record_definition": (
                "normalized daily_basic row matched to the authoritative daily "
                "traded cross-section after security-code transition resolution"
            ),
            "exact_250_observed_rows_required": False,
            "missing_observation_fill": "none",
            "authoritative_suspension_rows_count_as_observed": False,
            "required_authority_roles": [
                "extended_trading_calendar",
                "pit_listing_and_membership",
                "pit_suspension",
                "daily_traded_cross_section",
                "security_code_transition",
                "normalized_daily_basic",
            ],
            "exclusion_record_identity_fields": [
                "candidate_key",
                "reason",
                "authority_evidence_root_sha256",
            ],
            "excluded_candidate_count_required": True,
            "excluded_candidate_keys_sha256_required": True,
            "exclusion_reason_rows_sha256_required": True,
            "eligible_candidate_count_required": True,
            "eligible_candidate_keys_sha256_required": True,
            "eligible_identity_rows_sha256_required": True,
            "unrecognized_or_unproven_missingness_policy": "fail_closed",
        },
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
            "scope": "same_signal_date_exact_history_eligible_common_subset",
        },
        "abnormal_turnover_rate_f_20_to_250_rank": {
            "raw_formula": "log(mean_20/mean_250)",
            "history_field": "turnover_rate_f",
            "history_order": "strict_market_session_order",
            "window_market_sessions": {
                "short": 20,
                "long": 250,
            },
            "mean_observation_policy": "observed_trading_records_only",
            "minimum_observed_trading_records": {
                "short_window": 15,
                "long_window": 120,
            },
            "minimum_ipo_age_calendar_months": 6,
            "missing_observation_fill": "none",
            "latest_source_session": "T-1",
            "cross_section_transform": "deterministic_midrank",
            "mapping": "2*midrank/(N+1)-1",
            "tie_policy": "equal_values_share_average_rank",
            "scope": "same_signal_date_exact_history_eligible_common_subset",
            "variant": "free_float_turnover_rate_f",
            "paper_original_turnover_denominator": "total_shares",
            "project_turnover_denominator": "free_float_shares",
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
    "formal_materialization_prerequisites": {
        "verified_factor_v2_parent_descriptor": {
            "required": True,
            "present": False,
            "descriptor_sha256": None,
        },
        "authoritative_extended_trading_calendar_descriptor": {
            "required": True,
            "present": False,
            "descriptor_root_sha256": None,
        },
        "verified_jiaoch_points_collection_and_normalized_row_authority": {
            "required": True,
            "present": False,
            "collection_set_root_sha256": None,
            "normalized_row_authority_root_sha256": None,
            "source_bound_context_required": True,
            "source_bound_context_root_sha256": None,
        },
        "all_present": False,
        "formal_materializer_implemented": False,
    },
    "preview_policy": {
        "authority_status": "UNBOUND_PREVIEW_ONLY",
        "parent_authority_verified": False,
        "session_calendar_authority_verified": False,
        "daily_basic_row_authority_verified": False,
        "formal_materialization_performed": False,
        "ordered_date_labels_are_market_sessions": False,
        "pit_claimed": False,
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
    "699bfb91aeed1523168ac1604f4fc920e21723fcbefbcdb868b390394f2e17b2"
)
if canonical_sha256(FACTOR_V3_POINTS_CONTRACT) != FACTOR_V3_POINTS_CONTRACT_SHA256:
    raise RuntimeError("frozen factor-v3 points contract drifted")

FACTOR_V3_POINTS_ARM_STRATEGY_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "control": ("36b47deda4ca4b300b090586a3f69147939c34148ac764fea04463bae3e7adfc"),
        "turnover_level": ("c4291b2d0e52cad77e7338032d6fde2a7b9b5ce7f813e6594516752f23df2b98"),
        "abnormal_turnover": ("1e64701d826492af3043b0846b445597d2678c91070de2a1559f1c3003b3fb8a"),
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
        "sample_policy_sha256": canonical_sha256(
            FACTOR_V3_POINTS_CONTRACT["formal_parent_sample_policy"]
        ),
        "combination_search_permitted": False,
    }


def assert_frozen_factor_v3_points_contract() -> None:
    if (
        canonical_sha256(_FACTOR_V3_POINTS_PARENT_EXPECTATION)
        != FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256
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


def preview_factor_v3_points_rows_unbound(
    *,
    sessions: Sequence[Mapping[str, Any]],
    parent_rows: Sequence[Mapping[str, Any]],
    daily_basic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute strict factor math without claiming parent, PIT, or row authority."""

    assert_frozen_factor_v3_points_contract()
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

    dates = [trade_date for _, trade_date in normalized_sessions]
    position_by_date = {trade_date: ordinal for ordinal, trade_date in enumerate(dates)}
    required_source_keys: set[tuple[str, str]] = set()
    windows_by_candidate: dict[str, tuple[str, ...]] = {}
    for parent in normalized_parents:
        signal_position = position_by_date.get(parent["signal_date"])
        if signal_position is None:
            raise ValueError("factor-v3 parent signal_date is outside sessions")
        if signal_position < 250:
            raise ValueError("factor-v3 preview parent lacks 250 prior ordered input labels")
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
                    "source_input_date_label": row["source_session"],
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
        "schema_version": ("audited-pit-factor-v3-points-unbound-preview/v1"),
        "authority_status": "UNBOUND_PREVIEW_ONLY",
        "row_authority_status": "NOT_GRANTED",
        "receipt_sha256_semantics": ("preview_self_integrity_only_not_authority"),
        "factor_v3_points_contract_sha256": (FACTOR_V3_POINTS_CONTRACT_SHA256),
        "parent_input_identity_sha256": observed_identity,
        "parent_input_row_count": len(normalized_parents),
        "daily_basic_input_row_count": len(source_payload),
        "daily_basic_input_rows_sha256": canonical_sha256(source_payload),
        "preview_output_row_count": len(output_rows),
        "preview_dropped_input_row_count": 0,
        "preview_output_identity_sha256": output_identity_root,
        "preview_output_rows_sha256": canonical_sha256(output_rows),
        "latest_source_lag_ordered_input_labels": 1,
        "parent_authority_verified": False,
        "session_calendar_authority_verified": False,
        "daily_basic_row_authority_verified": False,
        "formal_materialization_performed": False,
        "formal_receipt_eligible": False,
        "ordered_date_labels_are_market_sessions": False,
        "pit_claimed": False,
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
    return {
        "preview_rows": output_rows,
        "unbound_preview_receipt": receipt,
    }
