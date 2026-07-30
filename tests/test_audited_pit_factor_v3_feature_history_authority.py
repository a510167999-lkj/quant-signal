from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace
import uuid

import pytest

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app import jiaoch_trade_cal_authority
from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    canonical_sha256,
)
from app.research_partitions import load_temporal_partition_contract
from app.research_pit_collector import (
    ControlledTushareCollector,
    HttpEntityResponse,
    build_bak_basic_specs,
)
from app.research_pit_store import PITReceiptStore
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
PUBLICATION_CAPABILITY = "9a91dd99-1579-4dba-9b13-dd1c56b0760f"
FEATURE_HISTORY_APIS = (
    "bak_basic",
    "daily",
    "adj_factor",
    "stk_limit",
    "suspend_d",
)


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
    holidays = frozenset(
        """
        2024-09-16 2024-09-17
        2024-10-01 2024-10-02 2024-10-03 2024-10-04 2024-10-07
        2025-01-01
        2025-01-28 2025-01-29 2025-01-30 2025-01-31 2025-02-03 2025-02-04
        2025-04-04
        2025-05-01 2025-05-02 2025-05-05
        2025-06-02
        2025-10-01 2025-10-02 2025-10-03 2025-10-06 2025-10-07 2025-10-08
        2026-01-01 2026-01-02
        2026-02-16 2026-02-17 2026-02-18 2026-02-19 2026-02-20 2026-02-23
        2026-04-06
        2026-05-01 2026-05-04 2026-05-05
        2026-06-19
        """.split()
    )
    sessions: list[str] = []
    cursor = DEVELOPMENT_START
    while cursor <= DEVELOPMENT_END:
        if cursor.weekday() < 5 and cursor.isoformat() not in holidays:
            sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert len(sessions) == 483
    assert canonical_sha256(sessions) == FROZEN_DEVELOPMENT_SESSIONS_SHA256
    return sessions


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
        "calendar_authority_status": (
            "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
        ),
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


def _trade_cal_publication(digest: str = SHA_A) -> dict[str, object]:
    return {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": (
            f"trade_cal_manifest_candidates/sha256/{digest[:2]}/{digest}.json"
        ),
        "authority_manifest_sha256": digest,
        "publication_capability": PUBLICATION_CAPABILITY,
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "schema": "jiaoch-trade-cal-authority-publication/v1",
    }


def _trade_cal_verification(
    manifest: dict[str, object],
    digest: str = SHA_A,
) -> dict[str, object]:
    return {
        "authority_manifest_sha256": digest,
        "calendar_authority_status": "VERIFIED_SINGLE_SEALED_CALL",
        "development_session_alignment_verified": False,
        "embargo_consumed": False,
        "end_date": manifest["end_date"],
        "exchange": "SSE",
        "final_oos_consumed": False,
        "is_open_normalization_root_sha256": SHA_B,
        "open_session_count": manifest["open_session_count"],
        "open_sessions_root_sha256": manifest["open_sessions_root_sha256"],
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "start_date": manifest["start_date"],
        "verified": True,
    }


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
    *,
    allow_test_contract: bool = False,
) -> None:
    development_root = canonical_sha256(development)
    if allow_test_contract:
        monkeypatch.setattr(
            history_authority,
            "_FROZEN_DEVELOPMENT_SESSIONS_SHA256",
            development_root,
        )
    else:
        assert development_root == FROZEN_DEVELOPMENT_SESSIONS_SHA256
    monkeypatch.setattr(
        history_authority,
        "_load_verified_trade_cal_manifest",
        lambda **_kwargs: (
            deepcopy(manifest),
            _trade_cal_verification(manifest),
        ),
    )


def _feature_history_route_policy_descriptor() -> dict[str, object]:
    document = {
        "routes": [
            {
                "api_name": api_name,
                "credential_slot_id": "points-primary",
                "purpose": "factor-v3-feature-history",
                "route_id": f"feature-history:points-primary:{api_name}",
            }
            for api_name in FEATURE_HISTORY_APIS
        ],
        "schema": "jiaoch-credential-feature-history-routing-policy/v1",
    }
    return {
        "credential_proof_claimed": False,
        "document": document,
        "schema": "jiaoch-credential-feature-history-policy-descriptor/v1",
        "sha256": canonical_sha256(document),
    }


def _build_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], list[str], list[str], dict[str, object]]:
    prewindow, development, manifest = _calendar_fixture()
    _install_synthetic_calendar(monkeypatch, manifest, development)
    plan = history_authority.build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        trade_cal_publication=_trade_cal_publication(),
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )
    return plan, prewindow, development, manifest


class _FixtureClock:
    def __init__(self) -> None:
        self._monotonic = 0

    def assert_synchronized(self) -> dict[str, object]:
        return {"source": "feature-history-fixture", "synchronized": True}

    def now_utc(self) -> datetime:
        return datetime(2024, 1, 4, tzinfo=timezone.utc)

    def monotonic_ns(self) -> int:
        self._monotonic += 1_000_000
        return self._monotonic


class _FeatureHistoryTransport:
    def __init__(
        self,
        *,
        include_beijing: bool = True,
        include_star: bool = True,
        semantic_empty_sessions: frozenset[str] = frozenset(),
    ) -> None:
        self.include_beijing = include_beijing
        self.include_star = include_star
        self.semantic_empty_sessions = semantic_empty_sessions
        self.calls: list[dict[str, object]] = []

    def post(self, *, body: bytes, **_kwargs) -> HttpEntityResponse:
        request = json.loads(body)
        self.calls.append(request)
        dataset = request["api_name"]
        compact = request["params"]["trade_date"]
        trade_date = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
        if dataset == "bak_basic":
            rows = [] if trade_date in self.semantic_empty_sessions else [
                [compact, "600001.SH", "Main", "Industry", "20000101"],
                [compact, "300001.SZ", "ChiNext", "Industry", "20100101"],
            ]
            if self.include_star and rows:
                rows.append([compact, "688001.SH", "STAR", "Industry", "20200101"])
            if self.include_beijing and rows:
                rows.append([compact, "430001.BJ", "BSE", "Industry", "20100101"])
        elif dataset == "daily":
            rows = [
                [
                    "600001.SH",
                    compact,
                    10.0,
                    10.5,
                    9.8,
                    10.2,
                    9.9,
                    0.3,
                    3.03,
                    1000,
                    10100,
                ]
            ]
        elif dataset == "adj_factor":
            rows = [["600001.SH", compact, 1.5]]
        elif dataset == "stk_limit":
            rows = [[compact, "600001.SH", 9.9, 10.89, 8.91]]
        elif dataset == "suspend_d":
            rows = []
        else:
            raise AssertionError(f"unexpected feature-history API: {dataset}")
        return HttpEntityResponse(
            status=200,
            headers={},
            body=_canonical_bytes(
                {
                    "code": 0,
                    "data": {
                        "fields": request["fields"].split(","),
                        "items": rows,
                    },
                    "msg": "",
                }
            ),
            body_complete=True,
        )


def _small_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    prewindow = ["2023-12-29", "2024-01-02"]
    development = ["2024-01-03"]
    open_wire = [value.replace("-", "") for value in [*prewindow, *development]]
    manifest: dict[str, object] = {
        "authority_scope": "SSE_TRADING_CALENDAR_WINDOW_ONLY",
        "calendar_authority_status": (
            "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
        ),
        "calendar_integrity_verified": True,
        "development_session_alignment_verified": False,
        "development_session_count_claimed": False,
        "embargo_consumed": False,
        "end_date": development[-1],
        "exchange": "SSE",
        "experiment_launch_eligible": False,
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "natural_day_coverage_verified": True,
        "open_session_count": len(open_wire),
        "open_sessions": open_wire,
        "open_sessions_root_sha256": hashlib.sha256(
            _canonical_bytes(
                {
                    "end_date": development[-1].replace("-", ""),
                    "exchange": "SSE",
                    "open_sessions": open_wire,
                    "schema": "jiaoch-trade-cal-open-sessions/v1",
                    "start_date": prewindow[0].replace("-", ""),
                }
            )
        ).hexdigest(),
        "open_sessions_sorted": True,
        "pretrade_chain_verified": True,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "rows_published": False,
        "schema": "jiaoch-trade-cal-authority/v1",
        "start_date": prewindow[0],
    }
    monkeypatch.setattr(history_authority, "_REQUIRED_PRIOR_OPEN_SESSIONS", 2)
    monkeypatch.setattr(history_authority, "_FROZEN_DEVELOPMENT_SESSION_COUNT", 1)
    monkeypatch.setattr(history_authority, "_FROZEN_DEVELOPMENT_START", development[0])
    monkeypatch.setattr(history_authority, "_FROZEN_DEVELOPMENT_END", development[-1])
    _install_synthetic_calendar(
        monkeypatch,
        manifest,
        development,
        allow_test_contract=True,
    )
    publication = _trade_cal_publication()
    plan = history_authority.build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        trade_cal_publication=publication,
        development_session_refs=_development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
    )
    return plan, publication, prewindow


def _collector_for_session(
    store: PITReceiptStore,
    transport: _FeatureHistoryTransport,
    trade_date: str,
    *,
    api_url: str = "https://jiaoch.site",
    source_profile: str = "jiaoch",
    request_protocol: str = "tushare-path-per-interface/v1",
) -> ControlledTushareCollector:
    contract = load_temporal_partition_contract(PARTITION_V1_PATH)
    role = "development" if trade_date < "2024-01-01" else "contaminated_diagnostic"
    host = api_url.split("://", 1)[-1].split("/", 1)[0]
    return ControlledTushareCollector(
        store=store,
        token="synthetic-token-never-persisted",
        api_url=api_url,
        allowed_hosts=(host,),
        transport=transport,
        clock=_FixtureClock(),
        max_attempts=3,
        sleeper=lambda _seconds: None,
        source_profile=source_profile,
        request_protocol=request_protocol,
        row_cap_overrides=(
            {"stk_limit": 10_000} if source_profile == "jiaoch" else None
        ),
        credential_slot_id="points-primary",
        credential_route_purpose="factor-v3-feature-history",
        credential_route_id_prefix="feature-history",
        credential_generation_id=PUBLICATION_CAPABILITY,
        temporal_contract=contract,
        temporal_role=role,
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date=trade_date,
        temporal_end_date=trade_date,
        workers=1,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_ref_sha256(receipt: sqlite3.Row) -> str:
    params = json.loads(receipt["params_json"])
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


def _independent_session_refs(
    store: PITReceiptStore,
    reports: dict[str, dict[str, object]],
    sessions: list[str],
) -> list[dict[str, object]]:
    output = []
    with sqlite3.connect(
        f"{Path(store.database_path).resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    ) as connection:
        connection.row_factory = sqlite3.Row
        for session in sessions:
            receipt = connection.execute(
                "SELECT * FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (session,),
            ).fetchone()
            if receipt is None or session not in reports:
                output.append(
                    {
                        "trade_date": session,
                        "bak_basic_receipt_sha256": SHA_A,
                        "bak_basic_raw_sha256": SHA_A,
                        "bak_basic_normalized_rows_sha256": SHA_A,
                        "bak_basic_row_count": 1,
                        "market_generation_id": f"missing:{session}",
                        "market_generation_manifest_sha256": SHA_A,
                        "market_generation_lineage_sha256": SHA_A,
                        "daily_rows_root_sha256": SHA_A,
                        "suspend_d_rows_root_sha256": SHA_A,
                    }
                )
                continue
            market = reports[session]
            manifest = market["manifest"]
            output.append(
                {
                    "trade_date": session,
                    "bak_basic_receipt_sha256": _receipt_ref_sha256(receipt),
                    "bak_basic_raw_sha256": receipt["raw_sha256"],
                    "bak_basic_normalized_rows_sha256": receipt["normalized_sha256"],
                    "bak_basic_row_count": int(receipt["row_count"]),
                    "market_generation_id": market["generation_id"],
                    "market_generation_manifest_sha256": market["manifest_sha256"],
                    "market_generation_lineage_sha256": market["lineage_sha256"],
                    "daily_rows_root_sha256": manifest["dataset_roots"]["daily"],
                    "suspend_d_rows_root_sha256": manifest["dataset_roots"]["suspend_d"],
                }
            )
    return output


def _finalize_store(store: PITReceiptStore) -> tuple[PITReceiptStore, str]:
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    sealed_root = Path(store.root).with_name(f"{Path(store.root).name}-sealed")
    shutil.copytree(
        store.root,
        sealed_root,
        ignore=shutil.ignore_patterns("*-wal", "*-shm"),
    )
    sealed = PITReceiptStore.__new__(PITReceiptStore)
    sealed.root = sealed_root
    sealed.raw_root = sealed_root / "raw"
    sealed.database_path = sealed_root / "metadata.sqlite3"
    return sealed, _file_sha256(Path(sealed.database_path))


def _real_store_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_beijing: bool = True,
    include_star: bool = True,
    semantic_empty_sessions: frozenset[str] = frozenset(),
    omit_market_session: str | None = None,
    api_url: str = "https://jiaoch.site",
    source_profile: str = "jiaoch",
    request_protocol: str = "tushare-path-per-interface/v1",
) -> tuple[
    dict[str, object],
    dict[str, object],
    list[str],
    PITReceiptStore,
    list[dict[str, object]],
    str,
]:
    plan, publication, sessions = _small_plan(monkeypatch)
    store = PITReceiptStore(str(tmp_path / "pit-store"))
    transport = _FeatureHistoryTransport(
        include_beijing=include_beijing,
        include_star=include_star,
        semantic_empty_sessions=semantic_empty_sessions,
    )
    reports: dict[str, dict[str, object]] = {}
    for session in sessions:
        collector = _collector_for_session(
            store,
            transport,
            session,
            api_url=api_url,
            source_profile=source_profile,
            request_protocol=request_protocol,
        )
        collector.fetch_membership_snapshot(build_bak_basic_specs([session])[0])
        if session != omit_market_session:
            reports[session] = collector.collect_market_session_generation(
                session,
                vintage="historical_backfill",
            )
    refs = _independent_session_refs(store, reports, sessions)
    sealed, database_sha256 = _finalize_store(store)
    return plan, publication, sessions, sealed, refs, database_sha256


def _publish_source_bound(
    *,
    plan: dict[str, object],
    publication: dict[str, object],
    store: PITReceiptStore,
) -> tuple[Path, dict[str, object]]:
    publication_output_root = Path(store.root).parent / "collection-publication"
    publication_output_root.mkdir()
    collection_publication = (
        history_authority._publish_factor_v3_feature_history_collection_candidate(
            collection_plan=plan,
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            pit_store_root=Path(store.root),
            publication_output_root=publication_output_root,
            feature_history_route_policy_descriptor=(
                _feature_history_route_policy_descriptor()
            ),
        )
    )
    return publication_output_root, collection_publication


def _verify_source_bound(
    *,
    plan: dict[str, object],
    publication: dict[str, object],
    store: PITReceiptStore,
    refs: list[dict[str, object]],
    database_sha256: str,
) -> dict[str, object]:
    del refs, database_sha256
    publication_output_root, collection_publication = _publish_source_bound(
        plan=plan,
        publication=publication,
        store=store,
    )
    return history_authority.verify_factor_v3_feature_history_collection_authority(
        collection_plan=plan,
        trade_cal_output_root=Path("synthetic-trade-cal-root"),
        trade_cal_publication=publication,
        development_session_refs=_development_refs(
            plan["development_sessions"]["sessions"]
        ),
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        collection_publication_output_root=publication_output_root,
        collection_publication=collection_publication,
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
        "trade_cal_publication",
        "development_session_refs",
        "temporal_partition_contract",
    }
    assert set(
        inspect.signature(
            history_authority.verify_factor_v3_feature_history_collection_authority
        ).parameters
    ) == {
        "collection_publication",
        "collection_publication_output_root",
        "collection_plan",
        "development_session_refs",
        "temporal_partition_contract",
        "trade_cal_output_root",
        "trade_cal_publication",
    }
    assert set(
        inspect.signature(
            history_authority._publish_factor_v3_feature_history_collection_candidate
        ).parameters
    ) == {
        "collection_plan",
        "development_session_refs",
        "feature_history_route_policy_descriptor",
        "pit_store_root",
        "publication_output_root",
        "temporal_partition_contract",
        "trade_cal_output_root",
        "trade_cal_publication",
    }
    source = inspect.getsource(history_authority)
    for forbidden in (
        "os.environ",
        "requests.",
        "urllib.",
        "ControlledTushareCollector(",
    ):
        assert forbidden not in source


def test_feature_history_route_policy_is_exact_and_does_not_claim_token_capability() -> None:
    descriptor = history_authority._validated_feature_history_route_policy_descriptor(
        _feature_history_route_policy_descriptor()
    )

    assert descriptor["credential_proof_claimed"] is False
    assert descriptor["document"]["routes"] == [
        {
            "api_name": api_name,
            "credential_slot_id": "points-primary",
            "purpose": "factor-v3-feature-history",
            "route_id": f"feature-history:points-primary:{api_name}",
        }
        for api_name in FEATURE_HISTORY_APIS
    ]
    assert descriptor["sha256"] == canonical_sha256(descriptor["document"])


def test_producer_binding_covers_direct_semantic_dependencies_and_loaded_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "audited_pit_factor_v3_feature_history_authority.py",
        "audited_pit_factor_v3_points_contract.py",
        "jiaoch_credential_slots.py",
        "jiaoch_trade_cal_authority.py",
        "research_partitions.py",
        "research_pit_collector.py",
        "research_pit_sources.py",
        "research_pit_store.py",
        "research_security_code_transition.py",
    }
    assert required <= set(history_authority._PRODUCER_FILES)
    before = history_authority._producer_binding()
    monkeypatch.setattr(history_authority, "classify_date", lambda *_args: "development")
    after = history_authority._producer_binding()

    assert before["source_manifest_root_sha256"] == after[
        "source_manifest_root_sha256"
    ]
    assert before["loaded_execution_root_sha256"] != after[
        "loaded_execution_root_sha256"
    ]
    assert before["root_sha256"] != after["root_sha256"]

    def replacement(
        _cls: object,
        _attempt: object,
        *,
        dataset: str,
        partition_key: str,
        marker: str = "replacement-default",
    ) -> tuple[dict[str, object], str, str]:
        return ({"marker": marker}, dataset, partition_key)

    monkeypatch.setattr(
        PITReceiptStore,
        "_membership_attempt_authority",
        classmethod(replacement),
    )
    class_method_after = history_authority._producer_binding()
    assert class_method_after["loaded_execution_root_sha256"] != after[
        "loaded_execution_root_sha256"
    ]


def test_trade_calendar_loader_requires_offline_verifier_and_content_address(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prewindow, _development, manifest = _calendar_fixture()
    raw = _canonical_bytes(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    publication = _trade_cal_publication(digest)
    relative = publication["authority_manifest_relative_path"]
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    calls: list[dict[str, object]] = []

    def verifier(**kwargs):
        calls.append(kwargs)
        return _trade_cal_verification(manifest, digest)

    monkeypatch.setitem(
        sys.modules,
        "app.jiaoch_trade_cal_authority",
        SimpleNamespace(verify_jiaoch_trade_cal_authority=verifier),
    )
    loaded, verification = history_authority._load_verified_trade_cal_manifest(
        output_root=tmp_path,
        trade_cal_publication=publication,
    )
    assert loaded == manifest
    assert verification["calendar_authority_status"] == "VERIFIED_SINGLE_SEALED_CALL"
    assert len(calls) == 2
    assert calls[0]["publication_capability"] == PUBLICATION_CAPABILITY

    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="manifest"):
        history_authority._load_verified_trade_cal_manifest(
            output_root=tmp_path,
            trade_cal_publication=publication,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "wrong"),
        ("publication_status", "DURABLE"),
        ("authority_manifest_created", False),
        ("publication_capability", "not-a-uuid4"),
        (
            "authority_manifest_relative_path",
            f"trade_cal_manifests/sha256/{SHA_A[:2]}/{SHA_A}.json",
        ),
    ],
)
def test_trade_calendar_publication_descriptor_is_complete_and_strict(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _prewindow, development, manifest = _calendar_fixture()
    _install_synthetic_calendar(monkeypatch, manifest, development)
    publication = _trade_cal_publication()
    publication[field] = value
    with pytest.raises(ValueError, match="publication"):
        history_authority.build_factor_v3_feature_history_collection_plan(
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
        )


def test_trade_calendar_loader_accepts_real_candidate_shape_without_monkeypatching_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        ["SSE", "20240101", 0, "20231229"],
        ["SSE", "20240102", 1, "20231229"],
        ["SSE", "20240103", 1, "20240102"],
    ]
    body = _canonical_bytes(
        {
            "code": 0,
            "data": {
                "fields": ["exchange", "cal_date", "is_open", "pretrade_date"],
                "items": list(reversed(rows)),
            },
            "msg": "success",
        }
    )

    class Transport:
        def post(self, **_kwargs):
            return HttpEntityResponse(
                status=200,
                headers={},
                body=body,
                body_complete=True,
            )

    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_transport_factory",
        lambda: Transport(),
    )
    monkeypatch.setattr(
        jiaoch_trade_cal_authority,
        "_utc_now",
        lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    publication = (
        jiaoch_trade_cal_authority._collect_jiaoch_trade_cal_with_route_credential(
            credential="synthetic-secret-never-persisted",
            generation_id=str(uuid.uuid4()),
            auxiliary_policy_descriptor=(
                jiaoch_trade_cal_authority._current_auxiliary_policy_descriptor()
            ),
            output_root=tmp_path,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 3),
            timeout_seconds=5,
        )
    )

    manifest, verification = history_authority._load_verified_trade_cal_manifest(
        output_root=tmp_path,
        trade_cal_publication=publication,
    )

    assert manifest["calendar_authority_status"] == (
        "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
    )
    assert verification["calendar_authority_status"] == "VERIFIED_SINGLE_SEALED_CALL"
    assert verification["verified"] is True


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
        trade_cal_publication=_trade_cal_publication(),
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
        trade_cal_publication=_trade_cal_publication(),
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
            trade_cal_publication=_trade_cal_publication(),
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
    with pytest.raises(ValueError, match="plan"):
        history_authority.verify_factor_v3_feature_history_collection_plan(
            collection_plan=rehashed,
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=_trade_cal_publication(),
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
        )


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
            trade_cal_publication=_trade_cal_publication(),
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
            trade_cal_publication=_trade_cal_publication(),
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
            trade_cal_publication=_trade_cal_publication(),
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
            trade_cal_publication=_trade_cal_publication(),
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
            trade_cal_publication=_trade_cal_publication(),
            development_session_refs=_development_refs(development),
            temporal_partition_contract=load_temporal_partition_contract(PARTITION_V1_PATH),
        )


def test_formal_collection_authority_replays_real_store_and_grants_only_feature_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    before = Path(store.database_path).stat()

    receipt = _verify_source_bound(
        plan=plan,
        publication=publication,
        store=store,
        refs=refs,
        database_sha256=database_sha256,
    )

    after = Path(store.database_path).stat()
    assert receipt["verified"] is True
    assert receipt["authority_status"] == "VERIFIED_FEATURE_HISTORY_ONLY"
    assert receipt["session_count"] == 2
    assert receipt["sessions_sha256"] == canonical_sha256(sessions)
    assert receipt["pit_store_database_sha256"] == database_sha256
    assert receipt["exact_nonempty_bak_basic_session_count"] == 2
    assert receipt["daily_generation_session_count"] == 2
    assert receipt["suspend_d_authority_session_count"] == 2
    assert receipt["upstream_star_preserved_session_count"] == 2
    assert receipt["upstream_beijing_preserved_session_count"] == 2
    assert receipt["security_code_transition_contract_sha256"] == (
        SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    )
    assert receipt["source_authority_root_sha256"] not in {SHA_A, SHA_B, SHA_C}
    assert receipt["producer_code_root_sha256"] not in {SHA_A, SHA_B, SHA_C}
    assert receipt["factor_materialization_eligible"] is False
    assert receipt["experiment_launch_eligible"] is False
    assert receipt["embargo_consumed"] is False
    assert receipt["final_oos_consumed"] is False
    assert receipt["production_profile_registered"] is False
    assert receipt["production_recommendation_eligible"] is False
    assert receipt["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


def test_formal_authority_rejects_semantic_empty_instead_of_exact_bak_basic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
        semantic_empty_sessions=frozenset({"2023-12-29"}),
    )

    with pytest.raises(ValueError, match="exact nonempty"):
        _verify_source_bound(
            plan=plan,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


def test_formal_authority_does_not_accept_caller_session_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    assert "session_authority_refs" not in inspect.signature(
        history_authority.verify_factor_v3_feature_history_collection_authority
    ).parameters
    receipt = _verify_source_bound(
        plan=plan,
        publication=publication,
        store=store,
        refs=refs,
        database_sha256=database_sha256,
    )
    assert receipt["session_authority_refs_sha256"] == canonical_sha256(refs)


def test_collection_publication_is_strictly_six_fields_and_capability_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, _refs, _database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    output_root, collection_publication = _publish_source_bound(
        plan=plan,
        publication=publication,
        store=store,
    )
    assert set(collection_publication) == {
        "authority_manifest_created",
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability",
        "publication_status",
        "schema",
    }
    tampered = deepcopy(collection_publication)
    tampered["publication_capability"] = "9a91dd99-1579-4dba-9b13-dd1c56b0760f"
    with pytest.raises(ValueError, match="capability"):
        history_authority.verify_factor_v3_feature_history_collection_authority(
            collection_publication=tampered,
            collection_publication_output_root=output_root,
            collection_plan=plan,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            pit_store_root=Path(store.root),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
        )

    issuance = history_authority._collection_publication_issuance(
        collection_publication
    )
    issuance_path = output_root / history_authority._collection_issuance_relative_path(
        issuance
    )
    issuance_path.unlink()
    with pytest.raises(ValueError, match="issuance"):
        history_authority.verify_factor_v3_feature_history_collection_authority(
            collection_publication=collection_publication,
            collection_publication_output_root=output_root,
            collection_plan=plan,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            pit_store_root=Path(store.root),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
        )


def test_formal_authority_rejects_missing_four_shard_market_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
        omit_market_session="2024-01-02",
    )

    with pytest.raises(ValueError, match="market generation"):
        _verify_source_bound(
            plan=plan,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


@pytest.mark.parametrize(
    ("include_star", "include_beijing"),
    [(False, True), (True, False)],
)
def test_formal_authority_derives_and_requires_upstream_star_and_beijing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_star: bool,
    include_beijing: bool,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
        include_star=include_star,
        include_beijing=include_beijing,
    )

    with pytest.raises(ValueError, match="upstream"):
        _verify_source_bound(
            plan=plan,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


def test_formal_authority_rejects_database_drift_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, _refs, _database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    publication_output_root, collection_publication = _publish_source_bound(
        plan=plan,
        publication=publication,
        store=store,
    )
    database = Path(store.database_path)
    database.write_bytes(database.read_bytes() + b"drift")

    with pytest.raises(ValueError, match="database"):
        history_authority.verify_factor_v3_feature_history_collection_authority(
            collection_publication=collection_publication,
            collection_publication_output_root=publication_output_root,
            collection_plan=plan,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            pit_store_root=Path(store.root),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
        )


def test_public_verifier_replays_snapshot_after_live_store_is_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, _refs, _database_sha256 = (
        _real_store_fixture(tmp_path, monkeypatch)
    )
    publication_output_root, collection_publication = _publish_source_bound(
        plan=plan,
        publication=publication,
        store=store,
    )
    manifest = history_authority._read_collection_manifest(
        output_root=publication_output_root,
        publication=collection_publication,
    )
    assert manifest["schema"] == (
        "audited-pit-factor-v3-feature-history-collection-manifest/v2"
    )
    assert manifest["snapshot_index_relative_path"].startswith(
        "feature_history_collection_snapshots/sha256/"
    )
    assert len(manifest["snapshot_index_sha256"]) == 64

    live_root = Path(store.root)
    live_root.rename(tmp_path / "detached-live-pit-store")
    receipt = (
        history_authority.verify_factor_v3_feature_history_collection_authority(
            collection_publication=collection_publication,
            collection_publication_output_root=publication_output_root,
            collection_plan=plan,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
        )
    )

    assert receipt["verified"] is True
    assert receipt["snapshot_index_sha256"] == (
        manifest["snapshot_index_sha256"]
    )


def test_formal_authority_rejects_raw_receipt_tamper_even_with_refreshed_database_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        raw_path = connection.execute(
            "SELECT raw_path FROM receipts WHERE dataset='bak_basic' ORDER BY partition_key LIMIT 1"
        ).fetchone()[0]
    Path(store.root, raw_path).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="PIT"):
        _verify_source_bound(
            plan=plan,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


@pytest.mark.parametrize(
    ("api_url", "source_profile", "request_protocol"),
    [
        (
            "https://feature-history.example.test",
            "jiaoch",
            "tushare-path-per-interface/v1",
        ),
        (
            "https://jiaoch.site",
            "feature-history-fixture",
            "tushare-path-per-interface/v1",
        ),
        (
            "https://jiaoch.site",
            "jiaoch",
            "tushare-root-post/v1",
        ),
    ],
)
def test_replay_rejects_self_consistent_nonfrozen_jiaoch_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_url: str,
    source_profile: str,
    request_protocol: str,
) -> None:
    plan, _publication, sessions, store, _refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
        api_url=api_url,
        source_profile=source_profile,
        request_protocol=request_protocol,
    )

    with pytest.raises(ValueError, match="Jiaoch source"):
        history_authority._replay_pit_store(
            pit_store_root=store.root,
            expected_database_sha256=database_sha256,
            sessions=sessions,
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
        )


def test_replay_reopens_every_raw_after_midflight_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _publication, sessions, store, _refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    with sqlite3.connect(
        f"{Path(store.database_path).resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    ) as connection:
        raw_path = connection.execute(
            """
            SELECT raw_path FROM receipts
            WHERE dataset='bak_basic' AND partition_key=?
            """,
            (sessions[0],),
        ).fetchone()[0]
    target = Path(store.root, raw_path)
    original = history_authority._market_authority_on_connection
    mutated = False

    def mutate_after_first_session(**kwargs):
        nonlocal mutated
        result = original(**kwargs)
        if not mutated:
            target.write_bytes(b"midflight-raw-replacement")
            mutated = True
        return result

    monkeypatch.setattr(
        history_authority,
        "_market_authority_on_connection",
        mutate_after_first_session,
    )

    with pytest.raises(ValueError, match="raw artifact drifted"):
        history_authority._replay_pit_store(
            pit_store_root=store.root,
            expected_database_sha256=database_sha256,
            sessions=sessions,
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
        )


def test_closed_store_scope_rejects_unrelated_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _publication, sessions, store, _refs, _database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO stock_basic_generations (
                generation_sequence, generation_id, scope_key, contract_sha256,
                status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (999, "unrelated-stock-basic", "unrelated", SHA_A, "abandoned", "2024-01-04T00:00:00+00:00"),
        )
    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(ValueError, match="scope"):
            history_authority._validate_closed_collection_store_scope(
                connection=connection,
                sessions=sessions,
            )


def test_market_attempt_scope_checks_every_unselected_attempt_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plan, _publication, sessions, store, _refs, _database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        original = dict(
            connection.execute(
                """
                SELECT * FROM fetch_attempts
                WHERE dataset='daily' AND partition_key=?
                """,
                (sessions[0],),
            ).fetchone()
        )
    semantics = json.loads(original["request_semantics_json"])
    semantics["url"] = "https://not-jiaoch.example.test/daily"
    store.record_fetch_attempt(
        dataset="daily",
        partition_key=sessions[0],
        endpoint="daily",
        params=json.loads(original["params_json"]),
        fields=json.loads(original["fields_json"]),
        wire_request_sha256=original["wire_request_sha256"],
        raw_bytes=Path(store.root, original["raw_path"]).read_bytes(),
        http_status=original["http_status"],
        started_at=original["started_at"],
        retrieved_at=original["retrieved_at"],
        elapsed_ns=original["elapsed_ns"],
        row_cap=original["row_cap"],
        body_complete=True,
        response_headers=json.loads(original["response_headers_json"]),
        clock_attestation=json.loads(original["clock_attestation_json"]),
        error_kind="invalid_json",
        request_body_sha256=original["wire_request_sha256"],
        request_semantics=semantics,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="Jiaoch source"):
            history_authority._validated_market_attempt_routes_on_connection(
                store=store,
                connection=connection,
                trade_date=sessions[0],
                expected_temporal_role="development",
                expected_temporal_contract_sha256=load_temporal_partition_contract(
                    PARTITION_V1_PATH
                )["contract_sha256"],
            )


def test_formal_authority_rejects_wal_or_shm_instead_of_ignoring_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    Path(f"{store.database_path}-wal").write_bytes(b"unexpected-live-wal")

    with pytest.raises(ValueError, match="WAL|SHM|finalized"):
        _verify_source_bound(
            plan=plan,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


def test_formal_authority_rejects_transition_session_or_plan_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, _sessions, store, refs, database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    tampered = deepcopy(plan)
    tampered["security_code_transition_contract_sha256"] = SHA_A
    tampered["plan_sha256"] = canonical_sha256(
        {key: value for key, value in tampered.items() if key != "plan_sha256"}
    )
    with pytest.raises(ValueError, match="transition"):
        _verify_source_bound(
            plan=tampered,
            publication=publication,
            store=store,
            refs=refs,
            database_sha256=database_sha256,
        )


def test_formal_authority_is_structurally_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, publication, sessions, store, _refs, _database_sha256 = _real_store_fixture(
        tmp_path,
        monkeypatch,
    )
    output_root, collection_publication = _publish_source_bound(
        plan=plan,
        publication=publication,
        store=store,
    )
    unexpected = deepcopy(collection_publication)
    unexpected["surprise"] = True
    with pytest.raises(ValueError, match="fields"):
        history_authority.verify_factor_v3_feature_history_collection_authority(
            collection_publication=unexpected,
            collection_publication_output_root=output_root,
            collection_plan=plan,
            development_session_refs=_development_refs(
                plan["development_sessions"]["sessions"]
            ),
            pit_store_root=Path(store.root),
            temporal_partition_contract=load_temporal_partition_contract(
                PARTITION_V1_PATH
            ),
            trade_cal_output_root=Path("synthetic-trade-cal-root"),
            trade_cal_publication=publication,
        )

    manifest = history_authority._read_collection_manifest(
        output_root=output_root,
        publication=collection_publication,
    )
    manifest["session_authority_refs"] = manifest["session_authority_refs"][:-1]
    with pytest.raises(ValueError, match="session authority"):
        history_authority._validated_collection_manifest(
            manifest,
            publication=collection_publication,
            sessions=sessions,
        )
