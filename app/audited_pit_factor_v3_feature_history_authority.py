"""Frozen, offline factor-v3 feature-history collection authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any

from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    canonical_sha256,
)
from app.research_partitions import (
    PartitionContractError,
    assert_range_allowed,
    classify_date,
)
from app.research_security_code_transition import (
    SECURITY_CODE_TRANSITION_CONTRACT_SHA256,
)


__all__ = (
    "build_factor_v3_feature_history_collection_plan",
    "verify_factor_v3_feature_history_collection_authority",
    "verify_factor_v3_feature_history_collection_plan",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TRADE_CAL_MANIFEST_PATH_RE = re.compile(
    r"^trade_cal_manifests/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json$"
)
_WIRE_DATE_RE = re.compile(r"^[0-9]{8}$")
_MAX_TRADE_CAL_MANIFEST_BYTES = 256 * 1024
_FROZEN_PARTITION_V1_SHA256 = "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227"
_FROZEN_DEVELOPMENT_SESSIONS_SHA256 = (
    "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
)
_FROZEN_DEVELOPMENT_START = "2024-07-05"
_FROZEN_DEVELOPMENT_END = "2026-07-03"
_FROZEN_DEVELOPMENT_SESSION_COUNT = 483
_REQUIRED_PRIOR_OPEN_SESSIONS = 250
_REQUIRED_MARKET_SHARDS = ("daily", "adj_factor", "stk_limit", "suspend_d")
_NONEMPTY_MARKET_SHARDS = frozenset({"daily", "adj_factor", "stk_limit"})
_BOARD_COUNT_FIELDS = frozenset({"beijing", "chinext", "mainboard", "science_technology"})
_SAFETY = {
    "embargo_consumed": False,
    "experiment_launch_eligible": False,
    "final_oos_consumed": False,
    "formal_factor_materialization_eligible": False,
    "production_profile_registered": False,
    "production_recommendation_eligible": False,
}

FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT = {
    "schema_version": "audited-pit-factor-v3-feature-history-authority-contract/v1",
    "purpose": "feature_history_only",
    "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
    "development_sessions": {
        "count": _FROZEN_DEVELOPMENT_SESSION_COUNT,
        "end": _FROZEN_DEVELOPMENT_END,
        "sha256": _FROZEN_DEVELOPMENT_SESSIONS_SHA256,
        "start": _FROZEN_DEVELOPMENT_START,
    },
    "required_prior_open_sessions": _REQUIRED_PRIOR_OPEN_SESSIONS,
    "temporal_partition_contract": {
        "policy_version": "contamination-aware-forward-oos/v1",
        "sha256": _FROZEN_PARTITION_V1_SHA256,
    },
    "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
    "collector_recipe": {
        "bak_basic_spec_builder": "app.research_pit_collector.build_bak_basic_specs",
        "collector_class": "app.research_pit_collector.ControlledTushareCollector",
        "market_generation_method": "collect_market_session_generation",
        "market_generation_vintage": "historical_backfill",
        "market_shards": list(_REQUIRED_MARKET_SHARDS),
        "membership_method": "fetch_membership_snapshot",
        "network_execution_implemented": False,
    },
    "upstream_scope_policy": {
        "beijing_rows": "preserve_before_downstream_candidate_scope",
        "filter_applied_during_collection": False,
        "science_technology_rows": "preserve_before_downstream_candidate_scope",
    },
    "forbidden_outputs_and_operations": [
        "candidate_generation",
        "label_generation",
        "target_generation",
        "train",
        "backtest",
        "validate",
        "experiment_launch",
        "production_profile_registration",
        "production_recommendation",
    ],
    "safety": deepcopy(_SAFETY),
}
FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256 = (
    "5ff095b51e8a8d55de96ef99971efcb69e36a81e2115d2718a8a54b4a51d122a"
)
if (
    canonical_sha256(FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT)
    != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
):
    raise RuntimeError("frozen factor-v3 feature history authority contract drifted")

_COLLECTION_EVIDENCE_FIELDS = frozenset(
    {
        "candidate_rows_generated",
        "collector_class",
        "embargo_consumed",
        "experiment_launched",
        "feature_history_only",
        "final_oos_consumed",
        "label_rows_generated",
        "plan_sha256",
        "production_profile_registered",
        "production_recommendation_eligible",
        "schema_version",
        "security_code_transition_contract_sha256",
        "session_authorities",
        "target_rows_generated",
        "train_backtest_validate_performed",
    }
)
_SESSION_AUTHORITY_FIELDS = frozenset({"bak_basic", "market_generation", "trade_date"})
_BAK_BASIC_FIELDS = frozenset(
    {
        "dataset",
        "normalized_rows_sha256",
        "partition_key",
        "raw_sha256",
        "receipt_status",
        "request_semantics_sha256",
        "row_count",
        "semantic_empty",
        "source_kind",
        "upstream_scope",
    }
)
_UPSTREAM_SCOPE_FIELDS = frozenset(
    {
        "filter_applied",
        "preserved_board_counts",
        "preserved_row_count",
        "preserved_rows_sha256",
        "source_board_counts",
        "source_row_count",
        "source_rows_sha256",
    }
)
_MARKET_GENERATION_FIELDS = frozenset(
    {
        "final_oos_eligible",
        "generation_id",
        "lineage_sha256",
        "manifest_sha256",
        "shards",
        "status",
        "trade_date",
        "verification_status",
        "vintage",
    }
)
_MARKET_SHARD_FIELDS = frozenset({"authority_status", "dataset", "row_count"})


def _strict_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_iso_date(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.isoformat() != value:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_wire_date(value: Any, *, label: str) -> str:
    if type(value) is not str or _WIRE_DATE_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return parsed.isoformat()


def _strict_positive_int(value: Any, *, label: str, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_mapping(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"factor-v3 feature history {label} fields rejected")
    return value


def _strict_sequence(value: Any, *, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_json_loads(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"factor-v3 feature history {label} rejected")
            output[key] = value
        return output

    def reject_constant(_value: str) -> None:
        raise ValueError(f"factor-v3 feature history {label} rejected")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if type(value) is not dict:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("factor-v3 feature history canonical JSON rejected") from None


def _load_verified_trade_cal_manifest(
    *,
    output_root: str | Path,
    authority_manifest_relative_path: str,
    expected_authority_manifest_sha256: str,
) -> dict[str, Any]:
    """Load only after the sealed trade-calendar verifier proves raw lineage."""

    from app.jiaoch_trade_cal_authority import verify_jiaoch_trade_cal_authority

    digest = _strict_sha256(
        expected_authority_manifest_sha256,
        label="trade calendar manifest sha256",
    )
    if type(authority_manifest_relative_path) is not str:
        raise ValueError("factor-v3 feature history trade calendar path rejected")
    match = _TRADE_CAL_MANIFEST_PATH_RE.fullmatch(authority_manifest_relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("factor-v3 feature history trade calendar path rejected")
    first = verify_jiaoch_trade_cal_authority(
        output_root=output_root,
        authority_manifest_relative_path=authority_manifest_relative_path,
        expected_authority_manifest_sha256=digest,
    )
    if (
        type(first) is not dict
        or first.get("verified") is not True
        or first.get("authority_manifest_sha256") != digest
    ):
        raise ValueError("factor-v3 feature history trade calendar verification rejected")
    path = Path(output_root).joinpath(*authority_manifest_relative_path.split("/"))
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size > _MAX_TRADE_CAL_MANIFEST_BYTES:
            raise OSError
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected") from None
    if len(raw) != stat.st_size or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        digest,
    ):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    payload = _strict_json_loads(raw, label="trade calendar manifest")
    if not hmac.compare_digest(raw, _canonical_bytes(payload)):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    second = verify_jiaoch_trade_cal_authority(
        output_root=output_root,
        authority_manifest_relative_path=authority_manifest_relative_path,
        expected_authority_manifest_sha256=digest,
    )
    if second != first:
        raise ValueError("factor-v3 feature history trade calendar verification drifted")
    return payload


def _validated_development_sessions(
    development_session_refs: Sequence[Mapping[str, Any]],
) -> list[str]:
    refs = _strict_sequence(development_session_refs, label="development session refs")
    sessions: list[str] = []
    for raw in refs:
        row = _strict_mapping(
            raw,
            fields=frozenset({"trade_date"}),
            label="development session ref",
        )
        sessions.append(_strict_iso_date(row["trade_date"], label="development trade_date"))
    if (
        len(sessions) != _FROZEN_DEVELOPMENT_SESSION_COUNT
        or sessions[0] != _FROZEN_DEVELOPMENT_START
        or sessions[-1] != _FROZEN_DEVELOPMENT_END
        or sessions != sorted(sessions)
        or len(set(sessions)) != len(sessions)
        or not hmac.compare_digest(
            canonical_sha256(sessions),
            _FROZEN_DEVELOPMENT_SESSIONS_SHA256,
        )
    ):
        raise ValueError("factor-v3 feature history frozen development sessions rejected")
    return sessions


def _validated_partition_contract(
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    if type(temporal_partition_contract) is not dict:
        raise ValueError("factor-v3 feature history partition contract rejected")
    contract = deepcopy(temporal_partition_contract)
    try:
        if (
            contract.get("policy_version") != "contamination-aware-forward-oos/v1"
            or contract.get("contract_sha256") != _FROZEN_PARTITION_V1_SHA256
        ):
            raise PartitionContractError("wrong frozen contract")
        classify_date(contract, _FROZEN_DEVELOPMENT_START)
    except PartitionContractError:
        raise ValueError("factor-v3 feature history partition contract rejected") from None
    return contract


def _validated_trade_calendar_manifest(
    manifest: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    if (
        type(manifest) is not dict
        or manifest.get("schema") != "jiaoch-trade-cal-authority/v1"
        or manifest.get("authority_scope") != "SSE_TRADING_CALENDAR_WINDOW_ONLY"
        or manifest.get("calendar_authority_status") != "VERIFIED_SINGLE_SEALED_CALL"
        or manifest.get("calendar_integrity_verified") is not True
        or manifest.get("natural_day_coverage_verified") is not True
        or manifest.get("pretrade_chain_verified") is not True
        or manifest.get("open_sessions_sorted") is not True
        or manifest.get("development_session_alignment_verified") is not False
        or manifest.get("development_session_count_claimed") is not False
        or manifest.get("embargo_consumed") is not False
        or manifest.get("experiment_launch_eligible") is not False
        or manifest.get("final_oos_consumed") is not False
        or manifest.get("formal_materialization_eligible") is not False
        or manifest.get("production_profile_registered") is not False
        or manifest.get("production_recommendation_eligible") is not False
        or manifest.get("rows_published") is not False
        or manifest.get("exchange") != "SSE"
    ):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    start = _strict_iso_date(manifest.get("start_date"), label="trade calendar start")
    end = _strict_iso_date(manifest.get("end_date"), label="trade calendar end")
    if end != _FROZEN_DEVELOPMENT_END or start > _FROZEN_DEVELOPMENT_START:
        raise ValueError("factor-v3 feature history trade calendar window rejected")
    raw_sessions = _strict_sequence(
        manifest.get("open_sessions"),
        label="trade calendar open sessions",
    )
    sessions = [
        _strict_wire_date(value, label="trade calendar open session") for value in raw_sessions
    ]
    count = _strict_positive_int(
        manifest.get("open_session_count"),
        label="trade calendar open session count",
    )
    if count != len(sessions) or sessions != sorted(sessions) or len(set(sessions)) != count:
        raise ValueError("factor-v3 feature history trade calendar sessions rejected")
    root = _strict_sha256(
        manifest.get("open_sessions_root_sha256"),
        label="trade calendar open sessions root",
    )
    expected_root = hashlib.sha256(
        _canonical_bytes(
            {
                "end_date": end.replace("-", ""),
                "exchange": "SSE",
                "open_sessions": [value.replace("-", "") for value in sessions],
                "schema": "jiaoch-trade-cal-open-sessions/v1",
                "start_date": start.replace("-", ""),
            }
        )
    ).hexdigest()
    if not hmac.compare_digest(root, expected_root):
        raise ValueError("factor-v3 feature history trade calendar root rejected")
    return sessions, {
        "calendar_authority_status": manifest["calendar_authority_status"],
        "end_date": end,
        "exchange": "SSE",
        "open_session_count": count,
        "open_sessions_root_sha256": root,
        "schema": manifest["schema"],
        "start_date": start,
    }


def _segment_prewindow(
    *,
    prewindow: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    grouped: list[tuple[str, list[str]]] = []
    try:
        for session in prewindow:
            role = classify_date(temporal_partition_contract, session)
            if role not in {"development", "contaminated_diagnostic"}:
                raise PartitionContractError("forbidden feature history role")
            if not grouped or grouped[-1][0] != role:
                grouped.append((role, []))
            grouped[-1][1].append(session)
    except PartitionContractError:
        raise ValueError("factor-v3 feature history partition segmentation rejected") from None
    if [role for role, _sessions in grouped] != [
        "development",
        "contaminated_diagnostic",
    ]:
        raise ValueError("factor-v3 feature history partition segmentation rejected")
    output = []
    for role, sessions in grouped:
        try:
            assert_range_allowed(
                temporal_partition_contract,
                role,
                sessions[0],
                sessions[-1],
                "collect",
            )
            if role == "contaminated_diagnostic":
                assert_range_allowed(
                    temporal_partition_contract,
                    role,
                    sessions[0],
                    sessions[-1],
                    "diagnose",
                )
        except PartitionContractError:
            raise ValueError("factor-v3 feature history partition segmentation rejected") from None
        operations = ["collect_feature_history_only"]
        bindings = {"collect_feature_history_only": "collect"}
        if role == "contaminated_diagnostic":
            operations.append("diagnose_feature_history_quality_only")
            bindings["diagnose_feature_history_quality_only"] = "diagnose"
        output.append(
            {
                "authorized_operations": operations,
                "candidate_rows_generated": False,
                "count": len(sessions),
                "end": sessions[-1],
                "experiment_launch_permitted": False,
                "label_rows_generated": False,
                "partition_operation_bindings": bindings,
                "sessions": list(sessions),
                "sessions_sha256": canonical_sha256(list(sessions)),
                "start": sessions[0],
                "target_rows_generated": False,
                "temporal_role": role,
                "train_backtest_validate_permitted": False,
            }
        )
    return output


def build_factor_v3_feature_history_collection_plan(
    *,
    trade_cal_output_root: str | Path,
    trade_cal_authority_manifest_relative_path: str,
    expected_trade_cal_authority_manifest_sha256: str,
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only permitted 250-session feature-history collection plan."""

    development_sessions = _validated_development_sessions(development_session_refs)
    contract = _validated_partition_contract(temporal_partition_contract)
    manifest = _load_verified_trade_cal_manifest(
        output_root=trade_cal_output_root,
        authority_manifest_relative_path=trade_cal_authority_manifest_relative_path,
        expected_authority_manifest_sha256=expected_trade_cal_authority_manifest_sha256,
    )
    calendar_sessions, calendar_descriptor = _validated_trade_calendar_manifest(manifest)
    try:
        development_start_index = calendar_sessions.index(development_sessions[0])
        development_end_index = calendar_sessions.index(development_sessions[-1])
    except ValueError:
        raise ValueError(
            "factor-v3 feature history development session alignment rejected"
        ) from None
    if (
        calendar_sessions[development_start_index : development_end_index + 1]
        != development_sessions
    ):
        raise ValueError("factor-v3 feature history development session alignment rejected")
    if development_start_index < _REQUIRED_PRIOR_OPEN_SESSIONS:
        raise ValueError("factor-v3 feature history requires exact 250-session prewindow")
    prewindow = calendar_sessions[
        development_start_index - _REQUIRED_PRIOR_OPEN_SESSIONS : development_start_index
    ]
    if (
        len(prewindow) != _REQUIRED_PRIOR_OPEN_SESSIONS
        or not prewindow
        or prewindow[-1] >= development_sessions[0]
    ):
        raise ValueError("factor-v3 feature history requires exact 250-session prewindow")
    segments = _segment_prewindow(
        prewindow=prewindow,
        temporal_partition_contract=contract,
    )
    manifest_sha256 = _strict_sha256(
        expected_trade_cal_authority_manifest_sha256,
        label="trade calendar manifest sha256",
    )
    path_match = _TRADE_CAL_MANIFEST_PATH_RE.fullmatch(trade_cal_authority_manifest_relative_path)
    if (
        path_match is None
        or path_match.group(1) != manifest_sha256[:2]
        or path_match.group(2) != manifest_sha256
    ):
        raise ValueError("factor-v3 feature history trade calendar path rejected")
    unsigned = {
        "schema_version": "audited-pit-factor-v3-feature-history-plan/v1",
        "purpose": "feature_history_only",
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "trade_calendar_authority": {
            **calendar_descriptor,
            "authority_manifest_relative_path": (trade_cal_authority_manifest_relative_path),
            "authority_manifest_sha256": manifest_sha256,
        },
        "development_sessions": {
            "count": len(development_sessions),
            "end": development_sessions[-1],
            "sessions": development_sessions,
            "sha256": canonical_sha256(development_sessions),
            "start": development_sessions[0],
        },
        "prewindow": {
            "count": len(prewindow),
            "end": prewindow[-1],
            "sessions": prewindow,
            "sha256": canonical_sha256(prewindow),
            "start": prewindow[0],
        },
        "segments": segments,
        "temporal_partition_contract_sha256": contract["contract_sha256"],
        "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
        "collector_recipe": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["collector_recipe"]
        ),
        "upstream_scope_policy": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["upstream_scope_policy"]
        ),
        "forbidden_outputs_and_operations": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["forbidden_outputs_and_operations"]
        ),
        "safety": deepcopy(_SAFETY),
    }
    return {**unsigned, "plan_sha256": canonical_sha256(unsigned)}


def _plan_source_descriptor(collection_plan: Any) -> Mapping[str, Any]:
    if type(collection_plan) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    descriptor = collection_plan.get("trade_calendar_authority")
    if type(descriptor) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    for field in ("authority_manifest_relative_path", "authority_manifest_sha256"):
        if field not in descriptor:
            raise ValueError("factor-v3 feature history plan rejected")
    return descriptor


def verify_factor_v3_feature_history_collection_plan(
    *,
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and compare the plan using only verified offline authorities."""

    descriptor = _plan_source_descriptor(collection_plan)
    rebuilt = build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_authority_manifest_relative_path=descriptor["authority_manifest_relative_path"],
        expected_trade_cal_authority_manifest_sha256=descriptor["authority_manifest_sha256"],
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    if collection_plan != rebuilt:
        raise ValueError("factor-v3 feature history plan verification rejected")
    return {
        "schema_version": "audited-pit-factor-v3-feature-history-plan-verification/v1",
        "verified": True,
        "plan_sha256": rebuilt["plan_sha256"],
        "prewindow_session_count": rebuilt["prewindow"]["count"],
        "development_session_count": rebuilt["development_sessions"]["count"],
        "feature_history_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }


def _verify_plan_self_integrity(collection_plan: Mapping[str, Any]) -> list[str]:
    if (
        type(collection_plan) is not dict
        or collection_plan.get("schema_version") != "audited-pit-factor-v3-feature-history-plan/v1"
        or collection_plan.get("purpose") != "feature_history_only"
        or collection_plan.get("factor_v3_points_contract_sha256")
        != FACTOR_V3_POINTS_CONTRACT_SHA256
        or collection_plan.get("factor_v3_feature_history_authority_contract_sha256")
        != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        or collection_plan.get("security_code_transition_contract_sha256")
        != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        or collection_plan.get("safety") != _SAFETY
    ):
        raise ValueError("factor-v3 feature history plan rejected")
    plan_sha256 = _strict_sha256(
        collection_plan.get("plan_sha256"),
        label="plan sha256",
    )
    unsigned = {key: value for key, value in collection_plan.items() if key != "plan_sha256"}
    if not hmac.compare_digest(canonical_sha256(unsigned), plan_sha256):
        raise ValueError("factor-v3 feature history plan rejected")
    prewindow = collection_plan.get("prewindow")
    if type(prewindow) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    sessions = prewindow.get("sessions")
    if (
        not isinstance(sessions, list)
        or len(sessions) != _REQUIRED_PRIOR_OPEN_SESSIONS
        or sessions != sorted(sessions)
        or len(set(sessions)) != len(sessions)
        or prewindow.get("count") != len(sessions)
        or prewindow.get("start") != sessions[0]
        or prewindow.get("end") != sessions[-1]
        or prewindow.get("sha256") != canonical_sha256(sessions)
    ):
        raise ValueError("factor-v3 feature history plan sessions rejected")
    for session in sessions:
        _strict_iso_date(session, label="plan session")
    return sessions


def _verified_board_counts(value: Any, *, label: str) -> dict[str, int]:
    if type(value) is not dict or set(value) != _BOARD_COUNT_FIELDS:
        raise ValueError("factor-v3 feature history upstream scope rejected")
    output = {}
    for board, count in value.items():
        output[board] = _strict_positive_int(
            count,
            label=f"{label} {board} count",
            allow_zero=True,
        )
    return output


def _verify_bak_basic(
    value: Any,
    *,
    trade_date: str,
) -> dict[str, Any]:
    row = _strict_mapping(
        value,
        fields=_BAK_BASIC_FIELDS,
        label="bak_basic",
    )
    if (
        row.get("dataset") != "bak_basic"
        or row.get("partition_key") != trade_date
        or row.get("receipt_status") not in {"stored", "reused"}
        or row.get("semantic_empty") is not False
        or row.get("source_kind") != "exact_nonempty_snapshot"
        or type(row.get("row_count")) is not int
        or row["row_count"] <= 0
    ):
        raise ValueError("factor-v3 feature history exact nonempty bak_basic rejected")
    for field in (
        "normalized_rows_sha256",
        "raw_sha256",
        "request_semantics_sha256",
    ):
        _strict_sha256(row.get(field), label=f"bak_basic {field}")
    scope = _strict_mapping(
        row.get("upstream_scope"),
        fields=_UPSTREAM_SCOPE_FIELDS,
        label="upstream scope",
    )
    source_count = _strict_positive_int(
        scope.get("source_row_count"),
        label="upstream source row count",
    )
    preserved_count = _strict_positive_int(
        scope.get("preserved_row_count"),
        label="upstream preserved row count",
    )
    source_root = _strict_sha256(
        scope.get("source_rows_sha256"),
        label="upstream source rows sha256",
    )
    preserved_root = _strict_sha256(
        scope.get("preserved_rows_sha256"),
        label="upstream preserved rows sha256",
    )
    source_boards = _verified_board_counts(
        scope.get("source_board_counts"),
        label="source",
    )
    preserved_boards = _verified_board_counts(
        scope.get("preserved_board_counts"),
        label="preserved",
    )
    if (
        scope.get("filter_applied") is not False
        or source_count != preserved_count
        or source_count != row["row_count"]
        or not hmac.compare_digest(source_root, preserved_root)
        or source_boards != preserved_boards
        or sum(source_boards.values()) != source_count
        or source_boards["science_technology"] <= 0
        or source_boards["beijing"] <= 0
    ):
        raise ValueError("factor-v3 feature history upstream scope rejected")
    return {
        "normalized_rows_sha256": row["normalized_rows_sha256"],
        "row_count": row["row_count"],
        "upstream_scope_sha256": canonical_sha256(scope),
    }


def _verify_market_generation(
    value: Any,
    *,
    trade_date: str,
) -> dict[str, Any]:
    generation = _strict_mapping(
        value,
        fields=_MARKET_GENERATION_FIELDS,
        label="market generation",
    )
    if (
        generation.get("trade_date") != trade_date
        or generation.get("status") != "published"
        or generation.get("verification_status") != "passed"
        or generation.get("vintage") != "historical_backfill"
        or generation.get("final_oos_eligible") is not False
        or type(generation.get("generation_id")) is not str
        or not generation["generation_id"]
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    _strict_sha256(
        generation.get("manifest_sha256"),
        label="market generation manifest sha256",
    )
    _strict_sha256(
        generation.get("lineage_sha256"),
        label="market generation lineage sha256",
    )
    shards_value = _strict_sequence(
        generation.get("shards"),
        label="market generation shards",
    )
    shards: dict[str, Mapping[str, Any]] = {}
    for raw in shards_value:
        shard = _strict_mapping(
            raw,
            fields=_MARKET_SHARD_FIELDS,
            label="market shard",
        )
        dataset = shard.get("dataset")
        if dataset not in _REQUIRED_MARKET_SHARDS or dataset in shards:
            raise ValueError("factor-v3 feature history market generation shards rejected")
        shards[dataset] = shard
    missing = [dataset for dataset in _REQUIRED_MARKET_SHARDS if dataset not in shards]
    if missing:
        raise ValueError(f"factor-v3 feature history {missing[0]} market authority rejected")
    for dataset in _REQUIRED_MARKET_SHARDS:
        shard = shards[dataset]
        if shard.get("authority_status") != "VERIFIED":
            raise ValueError(f"factor-v3 feature history {dataset} authority rejected")
        row_count = _strict_positive_int(
            shard.get("row_count"),
            label=f"{dataset} row count",
            allow_zero=dataset == "suspend_d",
        )
        if dataset in _NONEMPTY_MARKET_SHARDS and row_count <= 0:
            raise ValueError(f"factor-v3 feature history {dataset} authority rejected")
    return {
        "generation_id": generation["generation_id"],
        "lineage_sha256": generation["lineage_sha256"],
        "manifest_sha256": generation["manifest_sha256"],
    }


def verify_factor_v3_feature_history_collection_authority(
    *,
    collection_evidence: Mapping[str, Any],
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify per-session feature-history evidence without reading outcomes."""

    verify_factor_v3_feature_history_collection_plan(
        collection_plan=collection_plan,
        trade_cal_output_root=trade_cal_output_root,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    sessions = _verify_plan_self_integrity(collection_plan)
    evidence = _strict_mapping(
        collection_evidence,
        fields=_COLLECTION_EVIDENCE_FIELDS,
        label="collection evidence",
    )
    if (
        evidence.get("schema_version")
        != "audited-pit-factor-v3-feature-history-collection-evidence/v1"
        or evidence.get("feature_history_only") is not True
        or evidence.get("collector_class")
        != "app.research_pit_collector.ControlledTushareCollector"
        or evidence.get("candidate_rows_generated") is not False
        or evidence.get("label_rows_generated") is not False
        or evidence.get("target_rows_generated") is not False
        or evidence.get("train_backtest_validate_performed") is not False
        or evidence.get("experiment_launched") is not False
        or evidence.get("embargo_consumed") is not False
        or evidence.get("final_oos_consumed") is not False
        or evidence.get("production_profile_registered") is not False
        or evidence.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("factor-v3 feature history evidence safety rejected")
    if evidence.get("plan_sha256") != collection_plan["plan_sha256"]:
        raise ValueError("factor-v3 feature history plan binding rejected")
    if (
        evidence.get("security_code_transition_contract_sha256")
        != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    ):
        raise ValueError("factor-v3 feature history transition binding rejected")
    authorities = _strict_sequence(
        evidence.get("session_authorities"),
        label="session authorities",
    )
    if len(authorities) != len(sessions):
        raise ValueError("factor-v3 feature history session authority coverage rejected")
    authority_refs = []
    for expected_session, raw in zip(sessions, authorities, strict=True):
        authority = _strict_mapping(
            raw,
            fields=_SESSION_AUTHORITY_FIELDS,
            label="session authority",
        )
        trade_date = _strict_iso_date(
            authority.get("trade_date"),
            label="session authority trade_date",
        )
        if trade_date != expected_session:
            raise ValueError("factor-v3 feature history session authority order rejected")
        membership = _verify_bak_basic(
            authority.get("bak_basic"),
            trade_date=trade_date,
        )
        market = _verify_market_generation(
            authority.get("market_generation"),
            trade_date=trade_date,
        )
        authority_refs.append(
            {
                "bak_basic_normalized_rows_sha256": membership["normalized_rows_sha256"],
                "bak_basic_row_count": membership["row_count"],
                "generation_id": market["generation_id"],
                "market_generation_lineage_sha256": market["lineage_sha256"],
                "market_generation_manifest_sha256": market["manifest_sha256"],
                "trade_date": trade_date,
                "upstream_scope_sha256": membership["upstream_scope_sha256"],
            }
        )
    unsigned = {
        "schema_version": "audited-pit-factor-v3-feature-history-authority-receipt/v1",
        "verified": True,
        "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
        "feature_history_only": True,
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "collection_plan_sha256": collection_plan["plan_sha256"],
        "session_count": len(sessions),
        "sessions_sha256": canonical_sha256(sessions),
        "session_authority_refs_sha256": canonical_sha256(authority_refs),
        "exact_nonempty_bak_basic_session_count": len(sessions),
        "daily_generation_session_count": len(sessions),
        "suspend_d_authority_session_count": len(sessions),
        "upstream_star_preserved_session_count": len(sessions),
        "upstream_beijing_preserved_session_count": len(sessions),
        "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
        "factor_materialization_eligible": False,
        "experiment_launch_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    return {**unsigned, "receipt_sha256": canonical_sha256(unsigned)}
