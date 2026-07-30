from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app import factor_v3_daily_basic_733_exact_set_authority as authority
from app import factor_v3_daily_basic_runner as runner
from app import factor_v3_feature_history_runner as history_runner
from app import factor_v3_feature_history_frozen_source_attestation as frozen
from app import jiaoch_daily_basic_collection_set as daily_collection
from app import research_security_code_transition as code_transition
from app.research_partitions import load_temporal_partition_contract
from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    NORMALIZED_FIELDS,
    PITReceiptStore,
)
from scripts import build_factor_v3_daily_basic_formal_run_spec as formal_spec
from tests import test_audited_pit_factor_v3_feature_history_authority as history_fixture
from tests import test_research_pit_store as pit_fixture
from tests import test_research_security_code_transition as transition_fixture


PARTITION_PATH = Path("data/research_partitions/frozen-v1.json")


def _daily_authority_code(session: str) -> str:
    if session == "2025-02-14":
        return "300114.SZ"
    if session == "2025-02-17":
        return "302132.SZ"
    return "600001.SH"


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
        rows = pit_fixture._market_default_rows(
            dataset,
            session,
        )
        code = _daily_authority_code(session)
        if code != "600001.SH":
            for row in rows:
                row[1 if dataset == "stk_limit" else 0] = code
        raw = json.dumps(
            {
                "request_id": f"factor-v3-daily-basic-{dataset}-{session}",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": fields,
                    "items": rows,
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
                [[_daily_authority_code(session), "Fixture", "Industry"]],
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
        session = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        response = {
            "code": 0,
            "msg": "success",
            "data": {
                "fields": request["fields"].split(","),
                "items": [
                    [
                        _daily_authority_code(session),
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
    evidence_root, transition_sha256 = _real_transition_evidence(
        tmp_path,
        monkeypatch,
    )
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
        "feature_history_frozen_source_attestation_path": str(
            (tmp_path / "synthetic-frozen-attestation.json").resolve()
        ),
        "expected_feature_history_frozen_source_attestation_sha256": "a" * 64,
        "feature_history_frozen_source_root": str(frozen.FROZEN_SOURCE_ROOT),
        "expected_feature_history_frozen_source_commit": (
            frozen.FROZEN_SOURCE_COMMIT
        ),
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
    authority_binding = {"binding_sha256": "9" * 64}
    monkeypatch.setattr(
        frozen,
        "verify_factor_v3_feature_history_frozen_source_attestation",
        lambda **kwargs: {
            "authority_binding": authority_binding,
            "receipt_sha256": history_runner.verify_factor_v3_feature_history_run(
                run_spec_path=kwargs["feature_history_run_spec_path"],
                run_root=kwargs["feature_history_run_root"],
            )["receipt"]["receipt_sha256"],
            "session_count": 250,
            "sessions_sha256": "f" * 64,
            "verified": True,
        },
    )
    monkeypatch.setattr(
        frozen,
        "_validated_attested_replay_context",
        lambda **_kwargs: {"authority_binding": authority_binding},
    )
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


def test_real_chain_fixture_inherits_the_verified_receipt_identity_triple() -> None:
    source = inspect.getsource(
        test_real_250_plus_483_authority_chain_runs_and_cli_reverifies
    )

    assert '"sessions_sha256": "f" * 64' not in source
    assert 'feature_receipt["receipt_sha256"]' in source
    assert 'feature_receipt["session_count"]' in source
    assert 'feature_receipt["sessions_sha256"]' in source


def test_real_chain_fixture_uses_trusted_dispatch_and_buffers_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_binding = {"binding_sha256": "9" * 64}
    spec_output_root = (tmp_path / "formal-specs" / "sha256").resolve()
    planned_run_root = (tmp_path / "daily-basic-run").resolve()
    monkeypatch.setattr(
        formal_spec,
        "SPEC_OUTPUT_ROOT",
        spec_output_root,
    )
    monkeypatch.setattr(
        formal_spec,
        "PLANNED_RUN_ROOT",
        planned_run_root,
    )

    class FrozenConfig(Mapping[str, object]):
        def __init__(self) -> None:
            self.values = {
                "action": "verify",
                "formal_input_root": formal_spec.FORMAL_INPUT_ROOT_SHA256,
                "formal_output_root": str(formal_spec.SPEC_OUTPUT_ROOT),
                "run_spec_path": str(
                    (tmp_path / "daily-basic-run-spec.json").resolve()
                ),
                "run_root": str(planned_run_root),
            }

        def __getitem__(self, key: str) -> object:
            return self.values[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.values)

        def __len__(self) -> int:
            return len(self.values)

    config = FrozenConfig()
    buffered: list[object] = []
    ledger_entries: list[Mapping[str, object]] = []

    class Context:
        def validate_action_config(self, candidate: object) -> None:
            if candidate is not config:
                raise RuntimeError("untrusted config")

        def verified_ledger_entry(
            self,
            module_name: str,
        ) -> Mapping[str, object]:
            relative_path = (
                "app/__init__.py"
                if module_name == "app"
                else f"{module_name.replace('.', '/')}.py"
            )
            entry = {
                "absolute_path": str(
                    formal_spec.FORMAL_WORKTREE_ROOT
                    / Path(*relative_path.split("/"))
                ),
                "byte_count": 1,
                "is_package": module_name == "app",
                "loader_identity": (
                    "external-verified-source-loader/v1"
                ),
                "module_name": module_name,
                "relative_path": relative_path,
                "source_sha256": hashlib.sha256(
                    module_name.encode()
                ).hexdigest(),
            }
            ledger_entries.append(entry)
            return entry

        def assert_verified_module(
            self,
            _module_name: str,
            _relative_path: str,
            _expected_sha256: str,
        ) -> None:
            return None

        def emit_json(self, value: object) -> None:
            buffered.append(value)

        def postverify(self) -> None:
            raise AssertionError("external bootstrap owns terminal postverify")

    calls: list[tuple[str, str]] = []
    fake_runner = SimpleNamespace(
        verify_factor_v3_daily_basic_run=lambda **kwargs: (
            calls.append(
                (
                    str(kwargs["run_spec_path"]),
                    str(kwargs["run_root"]),
                )
            )
            or {
                "authority_binding": authority_binding,
                "status": "verified",
            }
        )
    )
    monkeypatch.setattr(
        formal_spec,
        "_load_runner",
        lambda _context: fake_runner,
    )
    monkeypatch.setattr(
        formal_spec,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    assert formal_spec.trusted_dispatch(Context(), config) == 0
    assert calls == [
        (
            str(config["run_spec_path"]),
            str(config["run_root"]),
        )
    ]
    assert buffered == [
        {
            "authority_binding": authority_binding,
            "status": "verified",
        }
    ]
    assert ledger_entries
    assert all(
        set(entry)
        == {
            "absolute_path",
            "byte_count",
            "is_package",
            "loader_identity",
            "module_name",
            "relative_path",
            "source_sha256",
        }
        for entry in ledger_entries
    )


def test_real_b805_attestation_builds_capability_free_250_plus_483_spec(
    tmp_path: Path,
) -> None:
    output_root = (tmp_path / "frozen-attestation").resolve()
    output_root.mkdir()
    publication = (
        frozen.publish_factor_v3_feature_history_frozen_source_attestation(
            frozen_source_root=frozen.FROZEN_SOURCE_ROOT,
            expected_frozen_source_commit=frozen.FROZEN_SOURCE_COMMIT,
            feature_history_run_spec_path=frozen.FROZEN_FEATURE_RUN_SPEC_PATH,
            feature_history_run_root=frozen.FROZEN_FEATURE_RUN_ROOT,
            output_root=output_root,
        )
    )
    attestation_path = output_root / Path(
        *publication["attestation_relative_path"].split("/")
    )
    exact_inputs = dict(formal_spec.EXACT_SET_AUTHORITY_INPUTS)
    exact_inputs.update(
        feature_history_frozen_source_attestation_path=str(attestation_path),
        expected_feature_history_frozen_source_attestation_sha256=publication[
            "attestation_sha256"
        ],
    )

    spec = runner.build_factor_v3_daily_basic_run_spec(
        exact_set_authority_inputs=exact_inputs,
        timeout_seconds=30,
        max_attempts=3,
    )

    raw = attestation_path.read_bytes().lower()
    assert spec["session_count"] == 733
    assert spec["sessions"][0] == "2023-06-26"
    assert spec["sessions"][-1] == "2026-07-03"
    assert b'"publication_capability":' not in raw
    assert b'"token":' not in raw
    assert b'"secret":' not in raw


def authority_tests_sessions() -> tuple[list[str], list[str]]:
    from tests.test_factor_v3_daily_basic_733_exact_set_authority import (
        _sessions,
    )

    return _sessions()
