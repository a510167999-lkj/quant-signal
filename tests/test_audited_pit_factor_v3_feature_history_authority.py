from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    canonical_sha256,
)
from app.research_partitions import load_temporal_partition_contract
from app.research_security_code_transition import (
    SECURITY_CODE_TRANSITION_CONTRACT_SHA256,
)


PARTITION_V1_PATH = Path("data/research_partitions/frozen-v1.json")
DEVELOPMENT_START = date(2024, 7, 5)
DEVELOPMENT_END = date(2026, 7, 3)
FROZEN_DEVELOPMENT_SESSIONS_SHA256 = (
    "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _business_days_before(value: date, count: int) -> list[str]:
    output: list[str] = []
    cursor = value - timedelta(days=1)
    while len(output) < count:
        if cursor.weekday() < 5:
            output.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return list(reversed(output))


def _development_sessions() -> list[str]:
    available: list[str] = []
    cursor = DEVELOPMENT_START
    while cursor <= DEVELOPMENT_END:
        if cursor.weekday() < 5:
            available.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert len(available) > 483
    return [*available[:482], available[-1]]


def _calendar_fixture() -> tuple[list[str], list[str], dict[str, object]]:
    development = _development_sessions()
    prewindow = _business_days_before(DEVELOPMENT_START, 250)
    open_sessions = [*prewindow, *development]
    open_wire = [value.replace("-", "") for value in open_sessions]
    descriptor = {
        "end_date": DEVELOPMENT_END.strftime("%Y%m%d"),
        "exchange": "SSE",
        "open_sessions": open_wire,
        "schema": "jiaoch-trade-cal-open-sessions/v1",
        "start_date": prewindow[0].replace("-", ""),
    }
    manifest = {
        "authority_scope": "SSE_TRADING_CALENDAR_WINDOW_ONLY",
        "calendar_authority_status": "VERIFIED_SINGLE_SEALED_CALL",
        "calendar_integrity_verified": True,
        "development_session_alignment_verified": False,
        "development_session_count_claimed": False,
        "embargo_consumed": False,
        "end_date": DEVELOPMENT_END.isoformat(),
        "exchange": "SSE",
        "experiment_launch_eligible": False,
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "natural_day_coverage_verified": True,
        "open_session_count": len(open_wire),
        "open_sessions": open_wire,
        "open_sessions_root_sha256": hashlib.sha256(_canonical_bytes(descriptor)).hexdigest(),
        "open_sessions_sorted": True,
        "pretrade_chain_verified": True,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "rows_published": False,
        "schema": "jiaoch-trade-cal-authority/v1",
        "start_date": prewindow[0],
    }
    return prewindow, development, manifest


def _development_refs(development: list[str]) -> list[dict[str, str]]:
    return [{"trade_date": value} for value in development]


def _refresh_open_sessions_root(manifest: dict[str, object]) -> None:
    descriptor = {
        "end_date": str(manifest["end_date"]).replace("-", ""),
        "exchange": "SSE",
        "open_sessions": manifest["open_sessions"],
        "schema": "jiaoch-trade-cal-open-sessions/v1",
        "start_date": str(manifest["start_date"]).replace("-", ""),
    }
    manifest["open_sessions_root_sha256"] = hashlib.sha256(_canonical_bytes(descriptor)).hexdigest()


def _install_synthetic_calendar(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, object],
    development: list[str],
) -> None:
    monkeypatch.setattr(
        history_authority,
        "_FROZEN_DEVELOPMENT_SESSIONS_SHA256",
        canonical_sha256(development),
    )
    monkeypatch.setattr(
        history_authority,
        "_load_verified_trade_cal_manifest",
        lambda **_kwargs: deepcopy(manifest),
    )


def _build_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], list[str], list[str], dict[str, object]]:
    prewindow, development, manifest = _calendar_fixture()
    _install_synthetic_calendar(monkeypatch, manifest, development)
    plan = history_authority.build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        trade_cal_authority_manifest_relative_path=(
            f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
        ),
        expected_trade_cal_authority_manifest_sha256=SHA_A,
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )
    return plan, prewindow, development, manifest


def _board_counts() -> dict[str, int]:
    return {
        "beijing": 1,
        "chinext": 1,
        "mainboard": 1,
        "science_technology": 1,
    }


def _session_authority(trade_date: str) -> dict[str, object]:
    counts = _board_counts()
    return {
        "bak_basic": {
            "dataset": "bak_basic",
            "normalized_rows_sha256": SHA_B,
            "partition_key": trade_date,
            "raw_sha256": SHA_A,
            "receipt_status": "stored",
            "request_semantics_sha256": SHA_C,
            "row_count": 4,
            "semantic_empty": False,
            "source_kind": "exact_nonempty_snapshot",
            "upstream_scope": {
                "filter_applied": False,
                "preserved_board_counts": deepcopy(counts),
                "preserved_row_count": 4,
                "preserved_rows_sha256": SHA_B,
                "source_board_counts": deepcopy(counts),
                "source_row_count": 4,
                "source_rows_sha256": SHA_B,
            },
        },
        "market_generation": {
            "final_oos_eligible": False,
            "generation_id": f"generation:{trade_date}",
            "lineage_sha256": SHA_B,
            "manifest_sha256": SHA_A,
            "shards": [
                {
                    "authority_status": "VERIFIED",
                    "dataset": "daily",
                    "row_count": 4,
                },
                {
                    "authority_status": "VERIFIED",
                    "dataset": "adj_factor",
                    "row_count": 4,
                },
                {
                    "authority_status": "VERIFIED",
                    "dataset": "stk_limit",
                    "row_count": 4,
                },
                {
                    "authority_status": "VERIFIED",
                    "dataset": "suspend_d",
                    "row_count": 0,
                },
            ],
            "status": "published",
            "trade_date": trade_date,
            "verification_status": "passed",
            "vintage": "historical_backfill",
        },
        "trade_date": trade_date,
    }


def _collection_evidence(plan: dict[str, object]) -> dict[str, object]:
    sessions = plan["prewindow"]["sessions"]
    return {
        "candidate_rows_generated": False,
        "collector_class": "app.research_pit_collector.ControlledTushareCollector",
        "embargo_consumed": False,
        "experiment_launched": False,
        "feature_history_only": True,
        "final_oos_consumed": False,
        "label_rows_generated": False,
        "plan_sha256": plan["plan_sha256"],
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "schema_version": "audited-pit-factor-v3-feature-history-collection-evidence/v1",
        "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
        "session_authorities": [_session_authority(value) for value in sessions],
        "target_rows_generated": False,
        "train_backtest_validate_performed": False,
    }


def _verify_authority(
    *,
    evidence: dict[str, object],
    plan: dict[str, object],
) -> dict[str, object]:
    development = plan["development_sessions"]["sessions"]
    return history_authority.verify_factor_v3_feature_history_collection_authority(
        collection_evidence=evidence,
        collection_plan=plan,
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )


def test_public_surface_is_offline_and_caller_cannot_select_history_window() -> None:
    assert history_authority.__all__ == (
        "build_factor_v3_feature_history_collection_plan",
        "verify_factor_v3_feature_history_collection_authority",
        "verify_factor_v3_feature_history_collection_plan",
    )
    assert set(
        inspect.signature(
            history_authority.build_factor_v3_feature_history_collection_plan
        ).parameters
    ) == {
        "trade_cal_output_root",
        "trade_cal_authority_manifest_relative_path",
        "expected_trade_cal_authority_manifest_sha256",
        "development_session_refs",
        "temporal_partition_contract",
    }
    assert set(
        inspect.signature(
            history_authority.verify_factor_v3_feature_history_collection_authority
        ).parameters
    ) == {
        "collection_evidence",
        "collection_plan",
        "development_session_refs",
        "temporal_partition_contract",
        "trade_cal_output_root",
    }
    source = inspect.getsource(history_authority)
    for forbidden in (
        "os.environ",
        "jiaoch_credential_slots",
        "requests.",
        "urllib.",
        "ControlledTushareCollector(",
    ):
        assert forbidden not in source


def test_trade_calendar_loader_requires_offline_verifier_and_content_address(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prewindow, _development, manifest = _calendar_fixture()
    raw = _canonical_bytes(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"trade_cal_manifests/sha256/{digest[:2]}/{digest}.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    calls: list[dict[str, object]] = []

    def verifier(**kwargs):
        calls.append(kwargs)
        return {
            "authority_manifest_sha256": digest,
            "verified": True,
        }

    monkeypatch.setitem(
        sys.modules,
        "app.jiaoch_trade_cal_authority",
        SimpleNamespace(verify_jiaoch_trade_cal_authority=verifier),
    )
    assert (
        history_authority._load_verified_trade_cal_manifest(
            output_root=tmp_path,
            authority_manifest_relative_path=relative,
            expected_authority_manifest_sha256=digest,
        )
        == manifest
    )
    assert len(calls) == 2

    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="manifest"):
        history_authority._load_verified_trade_cal_manifest(
            output_root=tmp_path,
            authority_manifest_relative_path=relative,
            expected_authority_manifest_sha256=digest,
        )


def test_frozen_contract_binds_parent_calendar_transition_and_safety() -> None:
    assert (
        history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT[
            "factor_v3_points_contract_sha256"
        ]
        == FACTOR_V3_POINTS_CONTRACT_SHA256
    )
    assert history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT[
        "development_sessions"
    ] == {
        "count": 483,
        "end": "2026-07-03",
        "sha256": FROZEN_DEVELOPMENT_SESSIONS_SHA256,
        "start": "2024-07-05",
    }
    assert (
        history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT[
            "security_code_transition_contract_sha256"
        ]
        == SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    )
    assert (
        history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT[
            "required_prior_open_sessions"
        ]
        == 250
    )
    assert (
        history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["purpose"]
        == "feature_history_only"
    )
    safety = history_authority.FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["safety"]
    assert set(safety.values()) == {False}


def test_plan_derives_exact_250_prewindow_and_two_frozen_v1_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, prewindow, development, _manifest = _build_plan(monkeypatch)

    assert plan["schema_version"] == "audited-pit-factor-v3-feature-history-plan/v1"
    assert plan["purpose"] == "feature_history_only"
    assert plan["factor_v3_points_contract_sha256"] == FACTOR_V3_POINTS_CONTRACT_SHA256
    assert plan["development_sessions"] == {
        "count": 483,
        "end": development[-1],
        "sessions": development,
        "sha256": canonical_sha256(development),
        "start": development[0],
    }
    assert plan["prewindow"] == {
        "count": 250,
        "end": prewindow[-1],
        "sessions": prewindow,
        "sha256": canonical_sha256(prewindow),
        "start": prewindow[0],
    }
    assert [segment["temporal_role"] for segment in plan["segments"]] == [
        "development",
        "contaminated_diagnostic",
    ]
    assert plan["segments"][0]["sessions"] == [value for value in prewindow if value < "2024-01-01"]
    assert plan["segments"][0]["authorized_operations"] == ["collect_feature_history_only"]
    assert plan["segments"][1]["sessions"] == [
        value for value in prewindow if value >= "2024-01-01"
    ]
    assert plan["segments"][1]["authorized_operations"] == [
        "collect_feature_history_only",
        "diagnose_feature_history_quality_only",
    ]
    for segment in plan["segments"]:
        assert segment["candidate_rows_generated"] is False
        assert segment["label_rows_generated"] is False
        assert segment["target_rows_generated"] is False
        assert segment["train_backtest_validate_permitted"] is False
        assert segment["experiment_launch_permitted"] is False
    assert plan["collector_recipe"] == {
        "bak_basic_spec_builder": "app.research_pit_collector.build_bak_basic_specs",
        "collector_class": "app.research_pit_collector.ControlledTushareCollector",
        "market_generation_method": "collect_market_session_generation",
        "market_generation_vintage": "historical_backfill",
        "market_shards": ["daily", "adj_factor", "stk_limit", "suspend_d"],
        "membership_method": "fetch_membership_snapshot",
        "network_execution_implemented": False,
    }
    assert plan["safety"] == {
        "embargo_consumed": False,
        "experiment_launch_eligible": False,
        "final_oos_consumed": False,
        "formal_factor_materialization_eligible": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    assert plan["plan_sha256"] == canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )


def test_prewindow_start_is_derived_not_a_frozen_or_caller_selected_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, prewindow, development, manifest = _build_plan(monkeypatch)
    assert plan["prewindow"]["start"] == prewindow[0]

    earlier = (date.fromisoformat(prewindow[0]) - timedelta(days=3)).isoformat()
    changed_open = [earlier, *manifest["open_sessions"][1:]]
    changed_manifest = deepcopy(manifest)
    changed_manifest["open_sessions"] = [
        value.replace("-", "") if "-" in value else value for value in changed_open
    ]
    changed_manifest["start_date"] = earlier
    _refresh_open_sessions_root(changed_manifest)
    _install_synthetic_calendar(monkeypatch, changed_manifest, development)
    changed = history_authority.build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        trade_cal_authority_manifest_relative_path=(
            f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
        ),
        expected_trade_cal_authority_manifest_sha256=SHA_A,
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )
    assert changed["prewindow"]["start"] == earlier
    assert changed["prewindow"]["start"] != plan["prewindow"]["start"]


def test_plan_verifier_rebuilds_all_authorities_and_rejects_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _prewindow, development, manifest = _build_plan(monkeypatch)
    _install_synthetic_calendar(monkeypatch, manifest, development)
    receipt = history_authority.verify_factor_v3_feature_history_collection_plan(
        collection_plan=plan,
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )
    assert receipt["verified"] is True
    assert receipt["plan_sha256"] == plan["plan_sha256"]
    assert receipt["prewindow_session_count"] == 250
    assert receipt["development_session_count"] == 483
    assert receipt["embargo_consumed"] is False
    assert receipt["final_oos_consumed"] is False
    assert receipt["production_recommendation_eligible"] is False

    tampered = deepcopy(plan)
    tampered["prewindow"]["sessions"][0] = "1999-01-01"
    with pytest.raises(ValueError, match="plan"):
        history_authority.verify_factor_v3_feature_history_collection_plan(
            collection_plan=tampered,
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )

    rehashed = deepcopy(plan)
    rehashed["prewindow"]["sessions"][0] = "1999-01-01"
    rehashed["prewindow"]["start"] = "1999-01-01"
    rehashed["prewindow"]["sha256"] = canonical_sha256(rehashed["prewindow"]["sessions"])
    rehashed["plan_sha256"] = canonical_sha256(
        {key: value for key, value in rehashed.items() if key != "plan_sha256"}
    )
    rehashed_evidence = _collection_evidence(rehashed)
    with pytest.raises(ValueError, match="plan"):
        _verify_authority(evidence=rehashed_evidence, plan=rehashed)


def test_plan_rejects_insufficient_history_and_development_alignment_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prewindow, development, manifest = _calendar_fixture()
    short = deepcopy(manifest)
    short["open_sessions"] = short["open_sessions"][1:]
    short["open_session_count"] -= 1
    _refresh_open_sessions_root(short)
    _install_synthetic_calendar(monkeypatch, short, development)
    with pytest.raises(ValueError, match="250"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_authority_manifest_relative_path=(
                f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
            ),
            expected_trade_cal_authority_manifest_sha256=SHA_A,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )

    misaligned = deepcopy(manifest)
    injected = (date.fromisoformat(development[10]) + timedelta(days=1)).strftime("%Y%m%d")
    misaligned["open_sessions"].insert(250 + 11, injected)
    misaligned["open_session_count"] += 1
    _refresh_open_sessions_root(misaligned)
    _install_synthetic_calendar(monkeypatch, misaligned, development)
    with pytest.raises(ValueError, match="development"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_authority_manifest_relative_path=(
                f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
            ),
            expected_trade_cal_authority_manifest_sha256=SHA_A,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )
    assert len(prewindow) == 250


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "wrong"),
        ("exchange", "SZSE"),
        ("calendar_integrity_verified", False),
        ("natural_day_coverage_verified", False),
        ("pretrade_chain_verified", False),
        ("open_sessions_sorted", False),
        ("development_session_alignment_verified", True),
        ("embargo_consumed", True),
        ("final_oos_consumed", True),
        ("production_profile_registered", True),
        ("production_recommendation_eligible", True),
        ("rows_published", True),
    ],
)
def test_plan_rejects_unverified_or_unsafe_trade_calendar_manifest(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _prewindow, development, manifest = _calendar_fixture()
    manifest[field] = value
    _install_synthetic_calendar(monkeypatch, manifest, development)
    with pytest.raises(ValueError, match="trade calendar"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_authority_manifest_relative_path=(
                f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
            ),
            expected_trade_cal_authority_manifest_sha256=SHA_A,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )


def test_plan_rejects_wrong_partition_contract_or_frozen_parent_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prewindow, development, manifest = _calendar_fixture()
    _install_synthetic_calendar(monkeypatch, manifest, development)
    wrong_contract = json.loads(PARTITION_V1_PATH.read_text(encoding="utf-8"))
    wrong_contract["contract_sha256"] = SHA_A
    with pytest.raises(ValueError, match="partition"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_authority_manifest_relative_path=(
                f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
            ),
            expected_trade_cal_authority_manifest_sha256=SHA_A,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=wrong_contract,
        )

    monkeypatch.setattr(
        history_authority,
        "_FROZEN_DEVELOPMENT_SESSIONS_SHA256",
        SHA_A,
    )
    with pytest.raises(ValueError, match="frozen development"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_authority_manifest_relative_path=(
                f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json"
            ),
            expected_trade_cal_authority_manifest_sha256=SHA_A,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )


def test_formal_collection_authority_accepts_exact_nonempty_daily_and_suspend_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)

    receipt = _verify_authority(evidence=evidence, plan=plan)

    assert receipt["verified"] is True
    assert receipt["authority_status"] == "VERIFIED_FEATURE_HISTORY_ONLY"
    assert receipt["session_count"] == 250
    assert receipt["sessions_sha256"] == canonical_sha256(prewindow)
    assert receipt["exact_nonempty_bak_basic_session_count"] == 250
    assert receipt["daily_generation_session_count"] == 250
    assert receipt["suspend_d_authority_session_count"] == 250
    assert receipt["upstream_star_preserved_session_count"] == 250
    assert receipt["upstream_beijing_preserved_session_count"] == 250
    assert receipt["security_code_transition_contract_sha256"] == (
        SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    )
    assert receipt["factor_materialization_eligible"] is False
    assert receipt["experiment_launch_eligible"] is False
    assert receipt["embargo_consumed"] is False
    assert receipt["final_oos_consumed"] is False
    assert receipt["production_profile_registered"] is False
    assert receipt["production_recommendation_eligible"] is False
    assert receipt["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("semantic_empty", True, "exact nonempty"),
        ("source_kind", "semantic_empty", "exact nonempty"),
        ("source_kind", "causal_carry_forward", "exact nonempty"),
        ("source_kind", "pre_anchor_quarantine", "exact nonempty"),
        ("row_count", 0, "exact nonempty"),
        ("receipt_status", "invalid_json", "exact nonempty"),
    ],
)
def test_formal_authority_rejects_nonexact_or_empty_bak_basic(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    evidence["session_authorities"][0]["bak_basic"][field] = value
    with pytest.raises(ValueError, match=message):
        _verify_authority(evidence=evidence, plan=plan)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_daily", "daily"),
        ("empty_daily", "daily"),
        ("missing_suspend", "suspend_d"),
        ("unverified_suspend", "suspend_d"),
        ("unpublished", "market generation"),
        ("final_oos", "market generation"),
    ],
)
def test_formal_authority_rejects_missing_or_unverified_market_shards(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    generation = evidence["session_authorities"][0]["market_generation"]
    if mutation == "missing_daily":
        generation["shards"] = [row for row in generation["shards"] if row["dataset"] != "daily"]
    elif mutation == "empty_daily":
        generation["shards"][0]["row_count"] = 0
    elif mutation == "missing_suspend":
        generation["shards"] = [
            row for row in generation["shards"] if row["dataset"] != "suspend_d"
        ]
    elif mutation == "unverified_suspend":
        generation["shards"][-1]["authority_status"] = "MISSING"
    elif mutation == "unpublished":
        generation["status"] = "collecting"
    else:
        generation["final_oos_eligible"] = True
    with pytest.raises(ValueError, match=message):
        _verify_authority(evidence=evidence, plan=plan)


@pytest.mark.parametrize(
    "mutation",
    [
        "filter",
        "counts",
        "root",
        "star_missing",
        "beijing_missing",
    ],
)
def test_formal_authority_rejects_upstream_star_or_beijing_filtering(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    scope = evidence["session_authorities"][0]["bak_basic"]["upstream_scope"]
    if mutation == "filter":
        scope["filter_applied"] = True
    elif mutation == "counts":
        scope["preserved_row_count"] -= 1
    elif mutation == "root":
        scope["preserved_rows_sha256"] = SHA_C
    elif mutation == "star_missing":
        scope["preserved_board_counts"]["science_technology"] = 0
        scope["source_board_counts"]["science_technology"] = 0
        scope["preserved_row_count"] -= 1
        scope["source_row_count"] -= 1
    else:
        scope["preserved_board_counts"]["beijing"] = 0
        scope["source_board_counts"]["beijing"] = 0
        scope["preserved_row_count"] -= 1
        scope["source_row_count"] -= 1
    with pytest.raises(ValueError, match="upstream"):
        _verify_authority(evidence=evidence, plan=plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_rows_generated", True),
        ("label_rows_generated", True),
        ("target_rows_generated", True),
        ("train_backtest_validate_performed", True),
        ("experiment_launched", True),
        ("embargo_consumed", True),
        ("final_oos_consumed", True),
        ("production_profile_registered", True),
        ("production_recommendation_eligible", True),
    ],
)
def test_formal_authority_rejects_forbidden_outputs_or_safety_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    evidence[field] = value
    with pytest.raises(ValueError, match="feature history"):
        _verify_authority(evidence=evidence, plan=plan)


def test_formal_authority_rejects_transition_session_or_plan_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    evidence["security_code_transition_contract_sha256"] = SHA_A
    with pytest.raises(ValueError, match="transition"):
        _verify_authority(evidence=evidence, plan=plan)

    evidence = _collection_evidence(plan)
    evidence["session_authorities"] = evidence["session_authorities"][:-1]
    with pytest.raises(ValueError, match="session"):
        _verify_authority(evidence=evidence, plan=plan)

    evidence = _collection_evidence(plan)
    evidence["plan_sha256"] = SHA_A
    with pytest.raises(ValueError, match="plan"):
        _verify_authority(evidence=evidence, plan=plan)


def test_formal_authority_is_structurally_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _prewindow, _development, _manifest = _build_plan(monkeypatch)
    evidence = _collection_evidence(plan)
    evidence["surprise"] = True
    with pytest.raises(ValueError, match="fields"):
        _verify_authority(evidence=evidence, plan=plan)

    evidence = _collection_evidence(plan)
    evidence["session_authorities"][0]["bak_basic"]["surprise"] = True
    with pytest.raises(ValueError, match="fields"):
        _verify_authority(evidence=evidence, plan=plan)
