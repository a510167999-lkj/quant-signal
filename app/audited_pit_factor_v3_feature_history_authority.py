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
import sqlite3
from typing import Any
import uuid

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
    r"^trade_cal_manifest_candidates/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json$"
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
_TRADE_CAL_PUBLICATION_FIELDS = frozenset(
    {
        "authority_manifest_created",
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability",
        "publication_status",
        "schema",
    }
)
_SESSION_AUTHORITY_REF_FIELDS = frozenset(
    {
        "bak_basic_normalized_rows_sha256",
        "bak_basic_raw_sha256",
        "bak_basic_receipt_sha256",
        "bak_basic_row_count",
        "daily_rows_root_sha256",
        "market_generation_id",
        "market_generation_lineage_sha256",
        "market_generation_manifest_sha256",
        "suspend_d_rows_root_sha256",
        "trade_date",
    }
)
_PRODUCER_FILES = (
    "audited_pit_factor_v3_feature_history_authority.py",
    "research_pit_collector.py",
    "research_pit_store.py",
)
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
    "verification_recipe": {
        "caller_reported_collection_evidence_accepted": False,
        "pit_store_mode": "read_only_immutable_after_wal_shm_absence",
        "raw_and_normalized_receipts_replayed": True,
        "trade_calendar_publication_capability_required": True,
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
    "9b8acffc704285b93b603c7fafc4eb312b1259638a47db8359760dc0f48db045"
)
if (
    canonical_sha256(FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT)
    != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
):
    raise RuntimeError("frozen factor-v3 feature history authority contract drifted")



def _strict_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_uuid4(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.version != 4 or str(parsed) != value:
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


def _validated_trade_cal_publication(value: Any) -> dict[str, Any]:
    publication = _strict_mapping(
        value,
        fields=_TRADE_CAL_PUBLICATION_FIELDS,
        label="trade calendar publication",
    )
    if (
        publication.get("schema") != "jiaoch-trade-cal-authority-publication/v1"
        or publication.get("publication_status")
        != "DURABLE_POSTVERIFIED_AND_RETURNED"
        or publication.get("authority_manifest_created") is not True
    ):
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    digest = _strict_sha256(
        publication.get("authority_manifest_sha256"),
        label="trade calendar publication manifest sha256",
    )
    relative_path = publication.get("authority_manifest_relative_path")
    if type(relative_path) is not str:
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    match = _TRADE_CAL_MANIFEST_PATH_RE.fullmatch(relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    capability = _strict_uuid4(
        publication.get("publication_capability"),
        label="trade calendar publication capability",
    )
    return {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": relative_path,
        "authority_manifest_sha256": digest,
        "publication_capability": capability,
        "publication_status": publication["publication_status"],
        "schema": publication["schema"],
    }


def _validated_trade_cal_verification(
    value: Any,
    *,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or value.get("verified") is not True
        or value.get("calendar_authority_status") != "VERIFIED_SINGLE_SEALED_CALL"
        or value.get("authority_manifest_sha256")
        != publication["authority_manifest_sha256"]
        or value.get("exchange") != "SSE"
        or value.get("development_session_alignment_verified") is not False
        or value.get("embargo_consumed") is not False
        or value.get("final_oos_consumed") is not False
        or value.get("production_profile_registered") is not False
        or value.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("factor-v3 feature history trade calendar verification rejected")
    for field in (
        "authority_manifest_sha256",
        "is_open_normalization_root_sha256",
        "open_sessions_root_sha256",
    ):
        _strict_sha256(value.get(field), label=f"trade calendar verification {field}")
    _strict_positive_int(
        value.get("open_session_count"),
        label="trade calendar verification open session count",
    )
    _strict_iso_date(
        value.get("start_date"),
        label="trade calendar verification start",
    )
    _strict_iso_date(
        value.get("end_date"),
        label="trade calendar verification end",
    )
    return deepcopy(value)


def _load_verified_trade_cal_manifest(
    *,
    output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a candidate only while its returned publication capability verifies."""

    from app.jiaoch_trade_cal_authority import verify_jiaoch_trade_cal_authority

    publication = _validated_trade_cal_publication(trade_cal_publication)
    verifier_args = {
        "output_root": output_root,
        "authority_manifest_relative_path": publication[
            "authority_manifest_relative_path"
        ],
        "expected_authority_manifest_sha256": publication[
            "authority_manifest_sha256"
        ],
        "publication_capability": publication["publication_capability"],
    }
    first = _validated_trade_cal_verification(
        verify_jiaoch_trade_cal_authority(**verifier_args),
        publication=publication,
    )
    path = Path(output_root).joinpath(
        *publication["authority_manifest_relative_path"].split("/")
    )
    try:
        stat = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.st_size > _MAX_TRADE_CAL_MANIFEST_BYTES
        ):
            raise OSError
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected") from None
    if len(raw) != stat.st_size or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        publication["authority_manifest_sha256"],
    ):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    payload = _strict_json_loads(raw, label="trade calendar manifest")
    if not hmac.compare_digest(raw, _canonical_bytes(payload)):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    second = _validated_trade_cal_verification(
        verify_jiaoch_trade_cal_authority(**verifier_args),
        publication=publication,
    )
    if second != first:
        raise ValueError("factor-v3 feature history trade calendar verification drifted")
    return payload, first


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
    verification: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    if (
        type(manifest) is not dict
        or manifest.get("schema") != "jiaoch-trade-cal-authority/v1"
        or manifest.get("authority_scope") != "SSE_TRADING_CALENDAR_WINDOW_ONLY"
        or manifest.get("calendar_authority_status")
        != "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
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
    if (
        verification.get("calendar_authority_status")
        != "VERIFIED_SINGLE_SEALED_CALL"
        or verification.get("verified") is not True
        or verification.get("exchange") != "SSE"
        or verification.get("start_date") != start
        or verification.get("end_date") != end
        or verification.get("open_session_count") != count
        or verification.get("open_sessions_root_sha256") != root
    ):
        raise ValueError("factor-v3 feature history trade calendar grant rejected")
    return sessions, {
        "calendar_authority_status": verification["calendar_authority_status"],
        "candidate_calendar_authority_status": manifest[
            "calendar_authority_status"
        ],
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
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only permitted 250-session feature-history collection plan."""

    development_sessions = _validated_development_sessions(development_session_refs)
    contract = _validated_partition_contract(temporal_partition_contract)
    publication = _validated_trade_cal_publication(trade_cal_publication)
    manifest, calendar_verification = _load_verified_trade_cal_manifest(
        output_root=trade_cal_output_root,
        trade_cal_publication=publication,
    )
    calendar_sessions, calendar_descriptor = _validated_trade_calendar_manifest(
        manifest,
        calendar_verification,
    )
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
    manifest_sha256 = publication["authority_manifest_sha256"]
    unsigned = {
        "schema_version": "audited-pit-factor-v3-feature-history-plan/v1",
        "purpose": "feature_history_only",
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "trade_calendar_authority": {
            **calendar_descriptor,
            "authority_manifest_created": publication["authority_manifest_created"],
            "authority_manifest_relative_path": publication[
                "authority_manifest_relative_path"
            ],
            "authority_manifest_sha256": manifest_sha256,
            "publication_capability_sha256": hashlib.sha256(
                publication["publication_capability"].encode("utf-8")
            ).hexdigest(),
            "publication_schema": publication["schema"],
            "publication_status": publication["publication_status"],
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
    for field in (
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability_sha256",
        "publication_schema",
        "publication_status",
    ):
        if field not in descriptor:
            raise ValueError("factor-v3 feature history plan rejected")
    return descriptor


def verify_factor_v3_feature_history_collection_plan(
    *,
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and compare the plan using only verified offline authorities."""

    descriptor = _plan_source_descriptor(collection_plan)
    publication = _validated_trade_cal_publication(trade_cal_publication)
    if (
        descriptor["authority_manifest_relative_path"]
        != publication["authority_manifest_relative_path"]
        or descriptor["authority_manifest_sha256"]
        != publication["authority_manifest_sha256"]
        or descriptor["publication_capability_sha256"]
        != hashlib.sha256(
            publication["publication_capability"].encode("utf-8")
        ).hexdigest()
        or descriptor["publication_schema"] != publication["schema"]
        or descriptor["publication_status"] != publication["publication_status"]
    ):
        raise ValueError("factor-v3 feature history trade calendar publication drifted")
    rebuilt = build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=publication,
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
        type(collection_plan) is dict
        and collection_plan.get("security_code_transition_contract_sha256")
        != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    ):
        raise ValueError("factor-v3 feature history transition binding rejected")
    if (
        type(collection_plan) is not dict
        or collection_plan.get("schema_version") != "audited-pit-factor-v3-feature-history-plan/v1"
        or collection_plan.get("purpose") != "feature_history_only"
        or collection_plan.get("factor_v3_points_contract_sha256")
        != FACTOR_V3_POINTS_CONTRACT_SHA256
        or collection_plan.get("factor_v3_feature_history_authority_contract_sha256")
        != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ValueError("factor-v3 feature history PIT database rejected") from None
    return digest.hexdigest()


def _producer_code_root_sha256() -> str:
    source_root = Path(__file__).resolve().parent
    descriptors = []
    for filename in _PRODUCER_FILES:
        path = source_root / filename
        try:
            stat = path.stat()
        except OSError:
            raise ValueError("factor-v3 feature history producer code rejected") from None
        if path.is_symlink() or not path.is_file() or stat.st_size <= 0:
            raise ValueError("factor-v3 feature history producer code rejected")
        descriptors.append(
            {
                "path": f"app/{filename}",
                "sha256": _file_sha256(path),
                "size_bytes": stat.st_size,
            }
        )
    return canonical_sha256(
        {
            "files": descriptors,
            "schema_version": "factor-v3-feature-history-producer-binding/v1",
        }
    )


def _validated_session_authority_refs(
    value: Any,
    *,
    sessions: Sequence[str],
) -> list[dict[str, Any]]:
    refs = _strict_sequence(value, label="session authority refs")
    if len(refs) != len(sessions):
        raise ValueError("factor-v3 feature history session authority coverage rejected")
    output = []
    for expected_session, raw in zip(sessions, refs, strict=True):
        ref = _strict_mapping(
            raw,
            fields=_SESSION_AUTHORITY_REF_FIELDS,
            label="session authority ref",
        )
        trade_date = _strict_iso_date(
            ref.get("trade_date"),
            label="session authority trade_date",
        )
        if trade_date != expected_session:
            raise ValueError("factor-v3 feature history session authority order rejected")
        for field in (
            "bak_basic_normalized_rows_sha256",
            "bak_basic_raw_sha256",
            "bak_basic_receipt_sha256",
            "daily_rows_root_sha256",
            "market_generation_lineage_sha256",
            "market_generation_manifest_sha256",
            "suspend_d_rows_root_sha256",
        ):
            _strict_sha256(ref.get(field), label=f"session authority {field}")
        _strict_positive_int(
            ref.get("bak_basic_row_count"),
            label="session authority bak_basic row count",
        )
        generation_id = ref.get("market_generation_id")
        if type(generation_id) is not str or not generation_id:
            raise ValueError("factor-v3 feature history session authority generation rejected")
        output.append(deepcopy(ref))
    return output


def _board_for_membership_row(row: Mapping[str, Any]) -> str:
    code = str(row.get("ts_code") or "")
    exchange = str(row.get("exchange") or "")
    symbol, separator, suffix = code.partition(".")
    if len(symbol) != 6 or not symbol.isdigit() or separator != ".":
        raise ValueError("factor-v3 feature history upstream scope rejected")
    if exchange == "BSE" and suffix == "BJ":
        return "beijing"
    if exchange == "SSE" and suffix == "SH":
        if symbol.startswith(("688", "689")):
            return "science_technology"
        return "mainboard"
    if exchange == "SZSE" and suffix == "SZ":
        if symbol.startswith(("300", "301")):
            return "chinext"
        return "mainboard"
    raise ValueError("factor-v3 feature history upstream scope rejected")


def _receipt_semantics_sha256(receipt: Mapping[str, Any]) -> str:
    try:
        params = json.loads(receipt["params_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("factor-v3 feature history PIT receipt rejected") from None
    return canonical_sha256(
        {
            "dataset": receipt["dataset"],
            "partition_key": receipt["partition_key"],
            "endpoint": receipt["endpoint"],
            "params": params,
            "retrieved_at": receipt["retrieved_at"],
            "http_status": receipt["http_status"],
            "raw_path": receipt["raw_path"],
            "raw_sha256": receipt["raw_sha256"],
            "raw_bytes": receipt["raw_bytes"],
            "response_code": receipt["response_code"],
            "response_message": receipt["response_message"],
            "row_cap": receipt["row_cap"],
            "row_count": receipt["row_count"],
            "normalized_sha256": receipt["normalized_sha256"],
            "parser_version": receipt["parser_version"],
        }
    )


def _exact_membership_authority_on_connection(
    *,
    store: Any,
    connection: sqlite3.Connection,
    trade_date: str,
    expected_temporal_role: str,
    expected_temporal_contract_sha256: str,
    receipt_ref_sha256: str,
) -> dict[str, Any]:
    receipt_rows = list(
        connection.execute(
            """
            SELECT * FROM receipts
            WHERE dataset='bak_basic' AND partition_key=?
            """,
            (trade_date,),
        )
    )
    if len(receipt_rows) != 1:
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic receipt rejected"
        )
    receipt = dict(receipt_rows[0])
    if (
        receipt.get("dataset") != "bak_basic"
        or receipt.get("partition_key") != trade_date
        or receipt.get("endpoint") != "bak_basic"
        or int(receipt.get("response_code", -1)) != 0
        or int(receipt.get("row_count", 0)) <= 0
        or not hmac.compare_digest(
            _receipt_semantics_sha256(receipt),
            receipt_ref_sha256,
        )
    ):
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic receipt rejected"
        )
    derived_membership = connection.execute(
        """
        SELECT 1 FROM membership_session_generations
        WHERE trade_date=? LIMIT 1
        """,
        (trade_date,),
    ).fetchone()
    membership_head = connection.execute(
        "SELECT 1 FROM membership_session_head WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    if derived_membership is not None or membership_head is not None:
        raise ValueError(
            "factor-v3 feature history carry or quarantine membership rejected"
        )
    normalized_rows = store._normalized_rows_from_db(
        connection,
        "bak_basic",
        trade_date,
    )
    if (
        len(normalized_rows) != int(receipt["row_count"])
        or not hmac.compare_digest(
            canonical_sha256(normalized_rows),
            str(receipt["normalized_sha256"]),
        )
        or any(row.get("trade_date") != trade_date for row in normalized_rows)
    ):
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic rows rejected"
        )
    board_counts = {field: 0 for field in sorted(_BOARD_COUNT_FIELDS)}
    for row in normalized_rows:
        board_counts[_board_for_membership_row(row)] += 1
    if (
        sum(board_counts.values()) != len(normalized_rows)
        or board_counts["science_technology"] <= 0
        or board_counts["beijing"] <= 0
    ):
        raise ValueError("factor-v3 feature history upstream scope rejected")

    attempt_rows = list(
        connection.execute(
            """
            SELECT attempt.*, event.status AS terminal_status,
                   event.details_json AS terminal_details_json,
                   event.recorded_at AS terminal_recorded_at
            FROM fetch_attempts AS attempt
            LEFT JOIN fetch_promotion_events AS event
              ON event.attempt_id=attempt.attempt_id
            WHERE attempt.dataset='bak_basic' AND attempt.partition_key=?
            ORDER BY attempt.attempt_sequence
            """,
            (trade_date,),
        )
    )
    if not attempt_rows:
        raise ValueError("factor-v3 feature history PIT membership lineage rejected")
    linked_attempts = []
    source_authority = None
    for stored in attempt_rows:
        attempt = dict(stored)
        source, role, contract = store._membership_attempt_authority(
            attempt,
            dataset="bak_basic",
            partition_key=trade_date,
        )
        if (
            role != expected_temporal_role
            or contract != expected_temporal_contract_sha256
        ):
            raise ValueError(
                "factor-v3 feature history PIT membership temporal authority rejected"
            )
        if source_authority is None:
            source_authority = source
        elif source != source_authority:
            raise ValueError(
                "factor-v3 feature history PIT membership source authority rejected"
            )
        if attempt.get("terminal_status") == "invalid_json":
            raise ValueError(
                "factor-v3 feature history exact nonempty bak_basic semantic empty rejected"
            )
        details_raw = attempt.get("terminal_details_json")
        try:
            details = json.loads(details_raw) if details_raw is not None else None
        except (TypeError, json.JSONDecodeError):
            raise ValueError(
                "factor-v3 feature history PIT membership event rejected"
            ) from None
        if details is not None and _canonical_bytes(details).decode("utf-8") != details_raw:
            raise ValueError("factor-v3 feature history PIT membership event rejected")
        raw_link = attempt.get("raw_sha256") == receipt["raw_sha256"]
        event_link = (
            type(details) is dict
            and details.get("receipt_raw_sha256") == receipt["raw_sha256"]
        )
        if raw_link or event_link:
            if (
                attempt.get("terminal_status") not in {"stored", "reused"}
                or not raw_link
                or not event_link
                or attempt.get("error_kind") is not None
                or int(attempt.get("body_complete", 0)) != 1
                or attempt.get("http_status") is None
                or not 200 <= int(attempt["http_status"]) < 300
                or int(attempt.get("raw_bytes", -1)) != int(receipt["raw_bytes"])
                or store._verify_raw_file(attempt)
                != store._verify_raw_file(receipt)
            ):
                raise ValueError(
                    "factor-v3 feature history PIT membership lineage rejected"
                )
            linked_attempts.append(
                {
                    "attempt_id": str(attempt["attempt_id"]),
                    "raw_sha256": str(attempt["raw_sha256"]),
                    "request_semantics_sha256": str(
                        attempt["request_semantics_sha256"]
                    ),
                    "terminal_status": str(attempt["terminal_status"]),
                }
            )
    if not linked_attempts or source_authority is None:
        raise ValueError("factor-v3 feature history PIT membership lineage rejected")
    return {
        "attempt_refs": linked_attempts,
        "board_counts": board_counts,
        "normalized_rows_root_sha256": canonical_sha256(normalized_rows),
        "receipt": receipt,
        "receipt_ref_sha256": receipt_ref_sha256,
        "source_authority": source_authority,
    }


def _market_authority_on_connection(
    *,
    store: Any,
    connection: sqlite3.Connection,
    trade_date: str,
    expected_temporal_role: str,
    expected_temporal_contract_sha256: str,
) -> dict[str, Any]:
    if (
        connection.execute(
            """
            SELECT 1 FROM market_session_generation_head
            WHERE trade_date=?
            """,
            (trade_date,),
        ).fetchone()
        is None
    ):
        raise ValueError(
            "factor-v3 feature history market generation coverage rejected"
        )
    market = store._membership_market_authority_on_connection(
        connection,
        trade_date,
    )
    generation, verified, source, role, contract = market
    manifest = verified["manifest"]
    shards = manifest.get("shards")
    if (
        generation.get("status") != "published"
        or generation.get("vintage") != "historical_backfill"
        or generation.get("final_oos_eligible") is not False
        or role != expected_temporal_role
        or contract != expected_temporal_contract_sha256
        or not hmac.compare_digest(
            str(generation.get("lineage_sha256") or ""),
            str(verified["lineage_sha256"]),
        )
        or manifest.get("final_oos_eligible") is not False
        or not isinstance(shards, list)
        or [shard.get("dataset") for shard in shards]
        != sorted(_REQUIRED_MARKET_SHARDS)
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    shard_counts = {
        str(shard["dataset"]): int(shard["row_count"]) for shard in shards
    }
    if (
        any(shard_counts[dataset] <= 0 for dataset in _NONEMPTY_MARKET_SHARDS)
        or shard_counts["suspend_d"] < 0
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    dataset_roots = manifest.get("dataset_roots")
    if type(dataset_roots) is not dict or set(dataset_roots) != set(
        _REQUIRED_MARKET_SHARDS
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    for dataset in _REQUIRED_MARKET_SHARDS:
        _strict_sha256(
            dataset_roots.get(dataset),
            label=f"{dataset} market generation rows root",
        )
    return {
        "dataset_roots": deepcopy(dataset_roots),
        "generation": generation,
        "manifest_sha256": verified["manifest_sha256"],
        "lineage_sha256": verified["lineage_sha256"],
        "shard_counts": shard_counts,
        "source_authority": source,
    }


def _replay_pit_store(
    *,
    pit_store_root: str | Path,
    expected_database_sha256: str,
    sessions: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    from app.research_pit_store import (
        AuditedPointInTimeUniverse,
        PITReceiptError,
        PITReceiptStore,
    )

    root = Path(pit_store_root)
    database_path = root / "metadata.sqlite3"
    if (
        root.is_symlink()
        or not root.is_dir()
        or database_path.is_symlink()
        or not database_path.is_file()
        or database_path.resolve().parent != root.resolve()
    ):
        raise ValueError("factor-v3 feature history PIT store rejected")
    sidecars = (Path(f"{database_path}-wal"), Path(f"{database_path}-shm"))
    if any(path.exists() for path in sidecars):
        raise ValueError(
            "factor-v3 feature history PIT store must be finalized without WAL or SHM"
        )
    expected = _strict_sha256(
        expected_database_sha256,
        label="PIT store database sha256",
    )
    stat_before = database_path.stat()
    if stat_before.st_size <= 0 or not hmac.compare_digest(
        _file_sha256(database_path),
        expected,
    ):
        raise ValueError("factor-v3 feature history PIT database anchor rejected")
    store = PITReceiptStore.__new__(PITReceiptStore)
    store.root = root.resolve()
    store.raw_root = store.root / "raw"
    store.database_path = database_path.resolve()
    connection = None
    try:
        connection = AuditedPointInTimeUniverse._readonly_connection(
            store.database_path
        )
        receipt_verification = store._verify_receipts_on_connection(connection)
        receipt_ref_map = {
            (row["dataset"], row["partition_key"]): row["receipt_sha256"]
            for row in receipt_verification["receipt_refs"]
        }
        session_refs = []
        source_descriptors = []
        board_descriptors = []
        for trade_date in sessions:
            role = classify_date(temporal_partition_contract, trade_date)
            if role not in {"development", "contaminated_diagnostic"}:
                raise ValueError(
                    "factor-v3 feature history PIT temporal authority rejected"
                )
            membership_receipt_ref = receipt_ref_map.get(("bak_basic", trade_date))
            if membership_receipt_ref is None:
                raise ValueError(
                    "factor-v3 feature history exact nonempty bak_basic receipt rejected"
                )
            membership = _exact_membership_authority_on_connection(
                store=store,
                connection=connection,
                trade_date=trade_date,
                expected_temporal_role=role,
                expected_temporal_contract_sha256=temporal_partition_contract[
                    "contract_sha256"
                ],
                receipt_ref_sha256=membership_receipt_ref,
            )
            market = _market_authority_on_connection(
                store=store,
                connection=connection,
                trade_date=trade_date,
                expected_temporal_role=role,
                expected_temporal_contract_sha256=temporal_partition_contract[
                    "contract_sha256"
                ],
            )
            if membership["source_authority"] != market["source_authority"]:
                raise ValueError(
                    "factor-v3 feature history PIT source authority rejected"
                )
            receipt = membership["receipt"]
            session_ref = {
                "trade_date": trade_date,
                "bak_basic_receipt_sha256": membership["receipt_ref_sha256"],
                "bak_basic_raw_sha256": receipt["raw_sha256"],
                "bak_basic_normalized_rows_sha256": receipt["normalized_sha256"],
                "bak_basic_row_count": int(receipt["row_count"]),
                "market_generation_id": str(
                    market["generation"]["generation_id"]
                ),
                "market_generation_manifest_sha256": market["manifest_sha256"],
                "market_generation_lineage_sha256": market["lineage_sha256"],
                "daily_rows_root_sha256": market["dataset_roots"]["daily"],
                "suspend_d_rows_root_sha256": market["dataset_roots"]["suspend_d"],
            }
            session_refs.append(session_ref)
            source_descriptors.append(
                {
                    "trade_date": trade_date,
                    "source_authority": membership["source_authority"],
                    "temporal_role": role,
                    "temporal_contract_sha256": temporal_partition_contract[
                        "contract_sha256"
                    ],
                    "membership_attempt_refs": membership["attempt_refs"],
                    "session_authority_ref": session_ref,
                }
            )
            board_descriptors.append(
                {
                    "trade_date": trade_date,
                    "board_counts": membership["board_counts"],
                    "source_rows_sha256": membership[
                        "normalized_rows_root_sha256"
                    ],
                }
            )
    except (PITReceiptError, sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "factor-v3 feature history"
        ):
            raise
        raise ValueError(
            "factor-v3 feature history PIT store replay rejected"
        ) from None
    finally:
        if connection is not None:
            connection.close()
    if any(path.exists() for path in sidecars):
        raise ValueError(
            "factor-v3 feature history PIT store must be finalized without WAL or SHM"
        )
    stat_after = database_path.stat()
    if (
        stat_after.st_size != stat_before.st_size
        or stat_after.st_mtime_ns != stat_before.st_mtime_ns
        or not hmac.compare_digest(_file_sha256(database_path), expected)
    ):
        raise ValueError("factor-v3 feature history PIT database drifted")
    return {
        "database_bytes": stat_after.st_size,
        "database_sha256": expected,
        "receipt_manifest_sha256": receipt_verification[
            "receipt_manifest_sha256"
        ],
        "session_refs": session_refs,
        "source_authority_root_sha256": canonical_sha256(source_descriptors),
        "upstream_scope_root_sha256": canonical_sha256(board_descriptors),
    }


def verify_factor_v3_feature_history_collection_authority(
    *,
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    pit_store_root: str | Path,
    expected_pit_store_database_sha256: str,
    session_authority_refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay finalized PIT artifacts and grant feature-history-only authority."""

    sessions = _verify_plan_self_integrity(collection_plan)
    verify_factor_v3_feature_history_collection_plan(
        collection_plan=collection_plan,
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=trade_cal_publication,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    expected_refs = _validated_session_authority_refs(
        session_authority_refs,
        sessions=sessions,
    )
    contract = _validated_partition_contract(temporal_partition_contract)
    replay = _replay_pit_store(
        pit_store_root=pit_store_root,
        expected_database_sha256=expected_pit_store_database_sha256,
        sessions=sessions,
        temporal_partition_contract=contract,
    )
    if replay["session_refs"] != expected_refs:
        raise ValueError("factor-v3 feature history session authority refs rejected")
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
        "session_authority_refs_sha256": canonical_sha256(
            replay["session_refs"]
        ),
        "pit_store_database_sha256": replay["database_sha256"],
        "pit_store_database_bytes": replay["database_bytes"],
        "pit_store_receipt_manifest_sha256": replay[
            "receipt_manifest_sha256"
        ],
        "source_authority_root_sha256": replay[
            "source_authority_root_sha256"
        ],
        "upstream_scope_root_sha256": replay["upstream_scope_root_sha256"],
        "producer_code_root_sha256": _producer_code_root_sha256(),
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
