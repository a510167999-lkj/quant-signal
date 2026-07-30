from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app import factor_v3_daily_basic_733_exact_set_authority as authority
from app import factor_v3_daily_basic_runner as runner
from app import factor_v3_feature_history_runner as history_runner
from app import jiaoch_daily_basic_collection_set as daily_collection
from app import research_security_code_transition as code_transition
from app.research_partitions import load_temporal_partition_contract
from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    NORMALIZED_FIELDS,
    PITReceiptStore,
)
from tests import test_audited_pit_factor_v3_feature_history_authority as history_fixture
from tests import test_research_pit_store as pit_fixture
from tests import test_research_security_code_transition as transition_fixture


PARTITION_PATH = Path("data/research_partitions/frozen-v1.json")


def _real_feature_history_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, list[str]]:
    plan, _prewindow, development, _manifest = history_fixture._build_plan(
        monkeypatch
    )
    sessions = list(plan["prewindow"]["sessions"])
    run_root = (tmp_path / "feature-history-run").resolve()
    run_root.mkdir()
    store_root = run_root / "pit-store"
    publication_root = run_root / "collection-publication"
    trade_cal_root = (tmp_path / "trade-cal").resolve()
    trade_cal_root.mkdir()
    spec = history_runner.build_factor_v3_feature_history_run_spec(
        collection_plan=plan,
        trade_cal_output_root=trade_cal_root,
        trade_cal_publication=history_fixture._trade_cal_publication(),
        development_session_refs=history_fixture._development_refs(development),
        temporal_partition_contract=load_temporal_partition_contract(
            PARTITION_PATH
        ),
        timeout_seconds=30,
        max_attempts=3,
        workers=1,
    )
    paths = history_runner._run_paths(run_root, create=False)
    history_runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    store = PITReceiptStore(str(store_root))
    transport = history_fixture._FeatureHistoryTransport()
    for session in sessions:
        collector = history_fixture._collector_for_session(
            store,
            transport,
            session,
        )
        collector.fetch_membership_snapshot(
            history_fixture.build_bak_basic_specs([session])[0]
        )
        collector.collect_market_session_generation(
            session,
            vintage="historical_backfill",
        )
    history_runner._finalize_store(store_root, sessions)
    spec_path = (tmp_path / "feature-history-run-spec.json").resolve()
    spec_path.write_bytes(history_runner._canonical_bytes(spec))
    publication_root.mkdir()
    publication = history_runner._publish_collection_candidate(
        publication_root=publication_root,
        store_root=store_root,
        spec=spec,
    )
    receipt = history_runner._verify_collection_authority(
        publication_root=publication_root,
        publication=publication,
        spec=spec,
    )
    history_runner._atomic_json(
        paths["state"],
        history_runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="verified",
            completed_session_count=250,
            credential_generation_id=str(uuid.uuid4()),
            collection_publication=publication,
            receipt=receipt,
        ),
    )
    assert history_runner.verify_factor_v3_feature_history_run(
        run_spec_path=spec_path,
        run_root=run_root,
    )["status"] == "verified"
    return spec_path, run_root, sessions


def _seed_stock_generation(
    store: PITReceiptStore,
    started_at: str,
    *,
    temporal_contract_sha256: str,
) -> None:
    generation = store.begin_or_resume_stock_basic_generation(started_at)
    for exchange in ("SSE", "SZSE"):
        for status in ("L", "D", "P", "G"):
            rows = (
                [
                    [
                        "600001.SH",
                        "600001",
                        "Fixture",
                        "SSE",
                        "主板",
                        "L",
                        "20000101",
                        None,
                    ]
                ]
                if (exchange, status) == ("SSE", "L")
                else []
            )
            partition = f"{exchange}:{status}"
            params = {"exchange": exchange, "list_status": status}
            semantics = {
                "schema_version": "tushare-wire-request/v1",
                "dataset": "stock_basic",
                "partition_key": partition,
                "api_name": "stock_basic",
                "method": "POST",
                "url": "https://api.tushare.pro",
                "wire_params": params,
                "receipt_params": params,
                "fields": list(NORMALIZED_FIELDS["stock_basic"]),
                "row_cap": 6000,
                "temporal_contract_sha256": temporal_contract_sha256,
                "temporal_role": "development",
            }
            attempt = store.record_fetch_attempt(
                dataset="stock_basic",
                partition_key=partition,
                endpoint="stock_basic",
                params=params,
                fields=NORMALIZED_FIELDS["stock_basic"],
                wire_request_sha256=hashlib.sha256(
                    f"stock:{partition}".encode()
                ).hexdigest(),
                raw_bytes=pit_fixture._stock_response(rows),
                http_status=200,
                started_at=started_at,
                retrieved_at=started_at,
                elapsed_ns=1,
                row_cap=6000,
                body_complete=True,
                request_semantics=semantics,
            )
            store.stage_stock_basic_attempt(
                generation["generation_id"],
                partition,
                attempt["attempt_id"],
            )
    store.publish_stock_basic_generation(generation["generation_id"])


def _publish_market_session(
    store: PITReceiptStore,
    session: str,
    *,
    temporal_contract_sha256: str,
) -> None:
    started = datetime.combine(
        date.fromisoformat(session),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    generation = store.begin_or_resume_market_session_generation(
        started,
        session,
    )
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        retrieved_at = (started + timedelta(seconds=index)).isoformat()
        fields = pit_fixture._MARKET_DATASET_FIELDS[dataset]
        semantics = {
            "schema_version": "tushare-wire-request/v1",
            "dataset": dataset,
            "partition_key": session,
            "api_name": dataset,
            "method": "POST",
            "url": "https://api.tushare.pro",
            "wire_params": {"trade_date": session.replace("-", "")},
            "receipt_params": {"trade_date": session},
            "fields": fields,
            "row_cap": MARKET_SESSION_ROW_CAPS[dataset],
            "temporal_contract_sha256": temporal_contract_sha256,
            "temporal_role": "development",
        }
        raw = json.dumps(
            {
                "request_id": f"factor-v3-daily-basic-{dataset}-{session}",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": fields,
                    "items": pit_fixture._market_default_rows(
                        dataset,
                        session,
                    ),
                },
            },
            separators=(",", ":"),
        ).encode()
        wire_sha256 = hashlib.sha256(
            f"{dataset}:{session}:{retrieved_at}".encode()
        ).hexdigest()
        attempt = store.record_fetch_attempt(
            dataset=dataset,
            partition_key=session,
            endpoint=dataset,
            params={"trade_date": session},
            fields=fields,
            wire_request_sha256=wire_sha256,
            request_body_sha256=wire_sha256,
            request_semantics=semantics,
            raw_bytes=raw,
            http_status=200,
            started_at=(started + timedelta(seconds=index - 1)).isoformat(),
            retrieved_at=retrieved_at,
            elapsed_ns=1,
            row_cap=MARKET_SESSION_ROW_CAPS[dataset],
            body_complete=True,
        )
        store.stage_market_session_attempt(
            generation["generation_id"],
            dataset,
            attempt["attempt_id"],
        )
    store.publish_market_session_generation(generation["generation_id"])


def _calendar_rows(
    exchange: str,
    sessions: list[str],
) -> list[list[object]]:
    open_dates = set(sessions)
    cursor = date.fromisoformat(sessions[0])
    end = date.fromisoformat(sessions[-1])
    previous_open = cursor - timedelta(days=1)
    rows: list[list[object]] = []
    while cursor <= end:
        is_open = cursor.isoformat() in open_dates
        rows.append(
            [
                exchange,
                cursor.strftime("%Y%m%d"),
                int(is_open),
                previous_open.strftime("%Y%m%d"),
            ]
        )
        if is_open:
            previous_open = cursor
        cursor += timedelta(days=1)
    return rows


def _real_development_artifact(
    tmp_path: Path,
    sessions: list[str],
    *,
    temporal_contract_sha256: str,
) -> dict[str, object]:
    store = PITReceiptStore(str(tmp_path / "development-store"))
    started = f"{sessions[0]}T08:00:00+00:00"
    _seed_stock_generation(
        store,
        started,
        temporal_contract_sha256=temporal_contract_sha256,
    )
    for exchange in ("SSE", "SZSE"):
        store.ingest_tushare_response(
            dataset="trade_cal",
            partition_key=f"{exchange}:{sessions[0]}:{sessions[-1]}",
            endpoint="trade_cal",
            params={
                "exchange": exchange,
                "start_date": sessions[0].replace("-", ""),
                "end_date": sessions[-1].replace("-", ""),
            },
            raw_bytes=pit_fixture._calendar_response(
                _calendar_rows(exchange, sessions)
            ),
            http_status=200,
            retrieved_at=started,
            row_cap=10000,
        )
    for session in sessions:
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key=session,
            endpoint="bak_basic",
            params={"trade_date": session.replace("-", "")},
            raw_bytes=pit_fixture._daily_response(
                session.replace("-", ""),
                [["600001.SH", "Fixture", "Industry"]],
            ),
            http_status=200,
            retrieved_at=f"{session}T08:00:00+00:00",
            row_cap=7000,
        )
        _publish_market_session(
            store,
            session,
            temporal_contract_sha256=temporal_contract_sha256,
        )
    audit = store.audit_coverage(
        start_date=sessions[0],
        end_date=sessions[-1],
    )
    return store.publish_universe_artifact(
        str(tmp_path / "development-artifact"),
        start_date=sessions[0],
        end_date=sessions[-1],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        temporal_contract_sha256=temporal_contract_sha256,
        temporal_role="development",
    )


def _real_transition_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    raw = transition_fixture._transition_pdf()
    contract = transition_fixture._test_contract(raw)
    contract_sha256 = code_transition.canonical_sha256(contract)
    monkeypatch.setattr(
        code_transition,
        "SECURITY_CODE_TRANSITION_CONTRACT",
        contract,
    )
    monkeypatch.setattr(
        code_transition,
        "SECURITY_CODE_TRANSITION_CONTRACT_SHA256",
        contract_sha256,
    )
    evidence_root = tmp_path / "transition-evidence"
    evidence_root.mkdir()
    (evidence_root / f"{hashlib.sha256(raw).hexdigest()}.pdf").write_bytes(raw)
    return evidence_root.resolve(), contract_sha256


class _DailyBasicTransport:
    def post(self, *, body: bytes, **_kwargs: object) -> SimpleNamespace:
        request = json.loads(body)
        trade_date = request["params"]["trade_date"]
        response = {
            "code": 0,
            "msg": "success",
            "data": {
                "fields": request["fields"].split(","),
                "items": [
                    [
                        "600001.SH",
                        trade_date,
                        1.0,
                        2.0,
                        3.0,
                        4.0,
                        5.0,
                        6.0,
                    ]
                ],
            },
        }
        return SimpleNamespace(
            status=200,
            body_complete=True,
            body=json.dumps(response).encode(),
        )


def test_real_250_plus_483_authority_chain_runs_and_cli_reverifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_spec_path, feature_run_root, prewindow = _real_feature_history_run(
        tmp_path,
        monkeypatch,
    )
    _expected_prewindow, development = authority_tests_sessions()
    assert prewindow == _expected_prewindow
    contract = load_temporal_partition_contract(PARTITION_PATH)
    artifact = _real_development_artifact(
        tmp_path,
        development,
        temporal_contract_sha256=contract["contract_sha256"],
    )
    evidence_root, transition_sha256 = _real_transition_evidence(
        tmp_path,
        monkeypatch,
    )
    loaded = authority._load_development_authority(
        audited_development_universe_sqlite_path=artifact["path"],
        expected_development_coverage_audit_sha256=artifact[
            "coverage_audit_sha256"
        ],
        expected_development_artifact_root_sha256=artifact[
            "artifact_root_sha256"
        ],
        expected_development_temporal_contract_sha256=contract[
            "contract_sha256"
        ],
        expected_development_temporal_role="development_4",
    )
    assert len(loaded.partitions) == 483
    exact_inputs = {
        "feature_history_run_spec_path": str(feature_spec_path),
        "feature_history_run_root": str(feature_run_root),
        "audited_development_universe_sqlite_path": str(artifact["path"]),
        "expected_development_coverage_audit_sha256": artifact[
            "coverage_audit_sha256"
        ],
        "expected_development_artifact_root_sha256": artifact[
            "artifact_root_sha256"
        ],
        "expected_development_temporal_contract_sha256": contract[
            "contract_sha256"
        ],
        "expected_development_temporal_role": "development_4",
        "security_code_transition_evidence_root": str(evidence_root),
        "expected_security_code_transition_contract_sha256": transition_sha256,
    }
    spec = runner.build_factor_v3_daily_basic_run_spec(
        exact_set_authority_inputs=exact_inputs,
        timeout_seconds=30,
        max_attempts=1,
    )
    spec_path = (tmp_path / "daily-basic-run-spec.json").resolve()
    spec_path.write_bytes(runner._canonical_bytes(spec))
    run_root = (tmp_path / "daily-basic-run").resolve()
    monkeypatch.setattr(
        daily_collection.points_common,
        "_transport_factory",
        lambda: _DailyBasicTransport(),
    )
    result = runner._run_factor_v3_daily_basic_collection_with_route_credential(
        run_spec_path=spec_path,
        run_root=run_root,
        credential="fixture-only-credential",
        source_generation_id=str(uuid.uuid4()),
        daily_basic_policy_descriptor=(
            runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR
        ),
    )

    assert result["status"] == "verified"
    assert (
        runner.main(
            [
                "verify",
                "--run-spec",
                str(spec_path),
                "--run-root",
                str(run_root),
            ]
        )
        == 0
    )
    state = runner._read_json(
        run_root / "state.json",
        label="run state",
        max_bytes=runner._MAX_STATE_BYTES,
    )
    attestation_path = run_root / "exact-set-authority" / Path(
        *state["exact_set_publication"]["attestation_relative_path"].split("/")
    )
    attestation_path.unlink()
    assert (
        runner.main(
            [
                "verify",
                "--run-spec",
                str(spec_path),
                "--run-root",
                str(run_root),
            ]
        )
        == 2
    )


def authority_tests_sessions() -> tuple[list[str], list[str]]:
    from tests.test_factor_v3_daily_basic_733_exact_set_authority import (
        _sessions,
    )

    return _sessions()
