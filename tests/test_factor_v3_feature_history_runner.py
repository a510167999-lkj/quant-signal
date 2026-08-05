from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app import factor_v3_feature_history_runner as runner
from app import jiaoch_credential_slots
from app.research_partitions import load_temporal_partition_contract


PARTITION_PATH = Path("data/research_partitions/frozen-v1.json")
PUBLICATION = {
    "authority_manifest_created": True,
    "authority_manifest_relative_path": "collection_candidates/sha256/aa/" + "a" * 64 + ".json",
    "authority_manifest_sha256": "a" * 64,
    "publication_capability": "6c21fe93-a24b-436d-8ac9-fcecd3b31042",
    "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
    "schema": "audited-pit-factor-v3-feature-history-publication/v1",
}
SOURCE_GENERATION_ID = "6c21fe93-a24b-436d-8ac9-fcecd3b31042"


def _sessions() -> tuple[list[str], list[str]]:
    development: list[str] = []
    cursor = date(2023, 1, 2)
    while len(development) < 249:
        if cursor.weekday() < 5:
            development.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert development[-1] < "2024-01-01"
    return development, ["2024-01-02"]


def _segment(role: str, sessions: list[str]) -> dict[str, object]:
    return {
        "temporal_role": role,
        "sessions": sessions,
        "count": len(sessions),
        "start": sessions[0],
        "end": sessions[-1],
        "sessions_sha256": runner._canonical_sha256(sessions),
    }


def _run_spec(tmp_path: Path) -> dict[str, object]:
    development, diagnostic = _sessions()
    all_sessions = [*development, *diagnostic]
    plan = {
        "prewindow": {"sessions": all_sessions},
        "segments": [
            _segment("development", development),
            _segment("contaminated_diagnostic", diagnostic),
        ],
    }
    return runner.build_factor_v3_feature_history_run_spec(
        collection_plan=plan,
        trade_cal_output_root=tmp_path,
        trade_cal_publication={"publication": "synthetic"},
        development_session_refs=[],
        temporal_partition_contract=load_temporal_partition_contract(PARTITION_PATH),
        timeout_seconds=12.5,
        max_attempts=2,
        workers=1,
    )


def _write_spec(tmp_path: Path, spec: dict[str, object]) -> Path:
    path = tmp_path / "run-spec.json"
    path.write_bytes(runner._canonical_bytes(spec))
    return path


def _v1_state(
    *,
    run_spec_sha256: str,
    status: str,
    completed_session_count: int,
    collection_publication: dict[str, object] | None = None,
    receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    unsigned = {
        "schema": runner._RUN_STATE_V1_SCHEMA,
        "run_spec_sha256": run_spec_sha256,
        "status": status,
        "completed_session_count": completed_session_count,
        "collection_publication": collection_publication,
        "receipt": receipt,
    }
    return {**unsigned, "state_sha256": runner._canonical_sha256(unsigned)}


def _run_with_synthetic_route(*, run_spec_path: Path, run_root: Path) -> dict[str, object]:
    return runner._run_factor_v3_feature_history_collection_with_route_credential(
        run_spec_path=run_spec_path,
        run_root=run_root,
        credential="synthetic-token",
        source_generation_id=SOURCE_GENERATION_ID,
        feature_history_policy_descriptor=(
            runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
        ),
    )


def test_run_spec_content_addresses_all_collector_controls(tmp_path: Path) -> None:
    spec = _run_spec(tmp_path)

    assert spec["collector"] == {
        "max_attempts": 2,
        "timeout_seconds": 12.5,
        "workers": 1,
    }
    assert spec["run_spec_sha256"] == runner._canonical_sha256(
        {key: value for key, value in spec.items() if key != "run_spec_sha256"}
    )

    altered = {**spec, "collector": {**spec["collector"], "workers": 2}}
    altered["run_spec_sha256"] = runner._canonical_sha256(
        {key: value for key, value in altered.items() if key != "run_spec_sha256"}
    )
    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="collector policy"):
        runner._validated_run_spec(altered)

    integer_timeout = runner.build_factor_v3_feature_history_run_spec(
        collection_plan=spec["collection_plan"],
        trade_cal_output_root=tmp_path,
        trade_cal_publication=spec["trade_cal_publication"],
        development_session_refs=[],
        temporal_partition_contract=spec["temporal_partition_contract"],
        timeout_seconds=12,
        max_attempts=2,
        workers=1,
    )
    assert runner._validated_run_spec(integer_timeout) == integer_timeout


def test_frozen_policy_has_only_the_five_points_primary_routes() -> None:
    descriptor = runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
    document = descriptor["document"]

    assert set(descriptor) == {
        "credential_proof_claimed",
        "document",
        "schema",
        "sha256",
    }
    assert descriptor["credential_proof_claimed"] is False
    assert descriptor["sha256"] == runner.canonical_sha256(document)
    assert [route["api_name"] for route in document["routes"]] == list(
        runner._ALLOWED_DATASETS
    )
    assert all(
        route["credential_slot_id"] == "points-primary"
        and route["purpose"] == "factor-v3-feature-history"
        for route in document["routes"]
    )


def test_public_run_delegates_credential_resolution_to_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path, _run_spec(tmp_path))
    calls: list[dict[str, object]] = []

    def collect_from_environment(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"status": "verified", "credential_exposed": False}

    monkeypatch.setattr(
        jiaoch_credential_slots,
        "_collect_jiaoch_feature_history_from_environment_for_run",
        collect_from_environment,
    )
    monkeypatch.setattr(
        runner,
        "_run_credential_generation_id",
        lambda **_kwargs: SOURCE_GENERATION_ID,
    )
    result = runner.run_factor_v3_feature_history_collection(
        run_spec_path=spec_path,
        run_root=tmp_path / "run",
    )

    assert result == {"status": "verified", "credential_exposed": False}
    assert calls == [
        {
            "run_spec_path": spec_path,
            "run_root": tmp_path / "run",
            "source_generation_id": SOURCE_GENERATION_ID,
        }
    ]


def test_result_and_cli_redact_publication_capability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = {
        "collection_publication": PUBLICATION,
        "completed_session_count": 250,
        "receipt": {"receipt_sha256": "b" * 64, "verified": True},
        "run_spec_sha256": "a" * 64,
        "status": "verified",
    }
    result = runner._result(state)

    assert PUBLICATION["publication_capability"] not in json.dumps(result)
    assert result["collection_publication"] == {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": PUBLICATION[
            "authority_manifest_relative_path"
        ],
        "authority_manifest_sha256": PUBLICATION["authority_manifest_sha256"],
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "schema": "audited-pit-factor-v3-feature-history-publication/v1",
    }

    monkeypatch.setattr(
        runner,
        "run_factor_v3_feature_history_collection",
        lambda **_kwargs: {**result, "collection_publication": PUBLICATION},
    )
    assert runner.main(["run", "--run-spec", "spec", "--run-root", "root"]) == 0
    stdout = capsys.readouterr().out
    assert PUBLICATION["publication_capability"] not in stdout
    assert json.loads(stdout) == result

    monkeypatch.setattr(
        runner,
        "verify_factor_v3_feature_history_run",
        lambda **_kwargs: {**result, "collection_publication": PUBLICATION},
    )
    assert runner.main(["verify", "--run-spec", "spec", "--run-root", "root"]) == 0
    stdout = capsys.readouterr().out
    assert PUBLICATION["publication_capability"] not in stdout
    assert json.loads(stdout) == result

    unsafe_result = {
        **result,
        "receipt": {
            **result["receipt"],
            "publication_capability": PUBLICATION["publication_capability"],
        },
    }
    monkeypatch.setattr(
        runner,
        "run_factor_v3_feature_history_collection",
        lambda **_kwargs: unsafe_result,
    )
    assert runner.main(["run", "--run-spec", "spec", "--run-root", "root"]) == 2
    captured = capsys.readouterr()
    assert PUBLICATION["publication_capability"] not in captured.out
    assert PUBLICATION["publication_capability"] not in captured.err

    tuple_result = {
        **result,
        "receipt": (
            result["receipt"],
            {"publication_capability": PUBLICATION["publication_capability"]},
        ),
    }
    monkeypatch.setattr(
        runner,
        "run_factor_v3_feature_history_collection",
        lambda **_kwargs: tuple_result,
    )
    assert runner.main(["run", "--run-spec", "spec", "--run-root", "root"]) == 2
    captured = capsys.readouterr()
    assert PUBLICATION["publication_capability"] not in captured.out
    assert PUBLICATION["publication_capability"] not in captured.err


def test_run_uses_only_five_frozen_interfaces_and_two_sequential_collectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _run_spec(tmp_path)
    spec_path = _write_spec(tmp_path, spec)
    events: list[tuple[str, str, str]] = []
    finalized: list[list[str]] = []
    publisher_calls: list[dict[str, object]] = []
    verifier_calls: list[dict[str, object]] = []

    class FakeStore:
        def __init__(self, root: str) -> None:
            self.root = Path(root)
            self.root.mkdir(exist_ok=True)

    class FakeCollector:
        def __init__(self, **kwargs: object) -> None:
            self.role = str(kwargs["temporal_role"])
            self.workers = kwargs["workers"]
            self.timeout_s = kwargs["timeout_s"]
            self.max_attempts = kwargs["max_attempts"]
            assert kwargs["credential_slot_id"] == "points-primary"
            assert kwargs["credential_route_purpose"] == "factor-v3-feature-history"
            assert kwargs["credential_route_id_prefix"] == "feature-history"
            assert kwargs["credential_generation_id"] == SOURCE_GENERATION_ID

        def collect(self, **_kwargs: object) -> None:
            pytest.fail("runner must not call ControlledTushareCollector.collect")

        def fetch_membership_snapshot(self, fetch_spec: object, *, resume: bool) -> None:
            assert resume is True
            assert fetch_spec.dataset == "bak_basic"
            events.append((self.role, "bak_basic", fetch_spec.partition_key))

        def collect_market_session_generation(
            self, session: str, *, resume: bool, vintage: str
        ) -> None:
            assert resume is True
            assert vintage == "historical_backfill"
            events.append((self.role, "market", session))

    def publish(
        *,
        collection_plan: object,
        development_session_refs: object,
        feature_history_route_policy_descriptor: object,
        pit_store_root: object,
        publication_output_root: object,
        temporal_partition_contract: object,
        trade_cal_output_root: object,
        trade_cal_publication: object,
    ) -> dict[str, object]:
        publisher_calls.append(
            {
                "collection_plan": collection_plan,
                "development_session_refs": development_session_refs,
                "feature_history_route_policy_descriptor": (
                    feature_history_route_policy_descriptor
                ),
                "pit_store_root": pit_store_root,
                "publication_output_root": publication_output_root,
                "temporal_partition_contract": temporal_partition_contract,
                "trade_cal_output_root": trade_cal_output_root,
                "trade_cal_publication": trade_cal_publication,
            }
        )
        return PUBLICATION

    def verify(
        *,
        collection_publication: object,
        collection_publication_output_root: object,
        collection_plan: object,
        development_session_refs: object,
        temporal_partition_contract: object,
        trade_cal_output_root: object,
        trade_cal_publication: object,
    ) -> dict[str, object]:
        verifier_calls.append(
            {
                "collection_publication": collection_publication,
                "collection_publication_output_root": collection_publication_output_root,
                "collection_plan": collection_plan,
                "development_session_refs": development_session_refs,
                "temporal_partition_contract": temporal_partition_contract,
                "trade_cal_output_root": trade_cal_output_root,
                "trade_cal_publication": trade_cal_publication,
            }
        )
        return {"verified": True, "receipt_sha256": "b" * 64}

    monkeypatch.setattr(runner, "PITReceiptStore", FakeStore)
    monkeypatch.setattr(runner, "ControlledTushareCollector", FakeCollector)
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)
    monkeypatch.setattr(
        runner,
        "_finalize_store",
        lambda _root, sessions: finalized.append(list(sessions)),
    )
    monkeypatch.setattr(runner, "_assert_no_partial_store_artifacts", lambda *_args: None)
    monkeypatch.setattr(
        runner.history_authority,
        "_publish_factor_v3_feature_history_collection_candidate",
        publish,
        raising=False,
    )
    monkeypatch.setattr(
        runner.history_authority,
        "verify_factor_v3_feature_history_collection_authority",
        verify,
    )

    result = _run_with_synthetic_route(
        run_spec_path=spec_path,
        run_root=tmp_path / "run",
    )

    development, diagnostic = _sessions()
    assert result["status"] == "verified"
    assert result["completed_session_count"] == 250
    assert finalized == [[*development, *diagnostic]]
    assert len(events) == 500
    assert events[:2] == [
        ("development", "bak_basic", development[0]),
        ("development", "market", development[0]),
    ]
    assert events[-2:] == [
        ("contaminated_diagnostic", "bak_basic", diagnostic[0]),
        ("contaminated_diagnostic", "market", diagnostic[0]),
    ]
    assert all(dataset in {"bak_basic", "market"} for _role, dataset, _session in events)
    assert {role for role, _dataset, _session in events} == {
        "development",
        "contaminated_diagnostic",
    }
    assert len(publisher_calls) == 1
    assert publisher_calls[0]["feature_history_route_policy_descriptor"] == (
        runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
    )
    assert "expected_pit_store_database_sha256" not in publisher_calls[0]
    assert len(verifier_calls) == 1
    assert "collection_publication_output_root" in verifier_calls[0]
    assert "collection_publication" in verifier_calls[0]
    assert "pit_store_root" not in verifier_calls[0]
    assert "expected_pit_store_database_sha256" not in verifier_calls[0]
    assert "session_authority_refs" not in verifier_calls[0]
    for path in (tmp_path / "run" / "run-spec.json", tmp_path / "run" / "state.json"):
        assert b"synthetic-token" not in path.read_bytes()
    assert "synthetic-token" not in repr(result)

    resumed = _run_with_synthetic_route(
        run_spec_path=spec_path,
        run_root=tmp_path / "run",
    )
    assert resumed["status"] == "verified"
    assert len(events) == 500
    assert len(publisher_calls) == 1
    assert len(verifier_calls) == 2


def test_final_store_requires_exact_five_interfaces_and_no_partial_generation(
    tmp_path: Path,
) -> None:
    development, diagnostic = _sessions()
    sessions = [*development, *diagnostic]
    root = tmp_path / "store"
    root.mkdir()
    database = root / "metadata.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE fetch_attempts (dataset TEXT NOT NULL, partition_key TEXT NOT NULL);
            CREATE TABLE market_session_generations (status TEXT NOT NULL);
            CREATE TABLE membership_session_generations (status TEXT NOT NULL);
            CREATE TABLE stock_basic_generations (generation_id TEXT NOT NULL);
            """
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executemany(
            "INSERT INTO fetch_attempts VALUES (?, ?)",
            [(dataset, session) for dataset in runner._ALLOWED_DATASETS for session in sessions],
        )
        connection.executemany(
            "INSERT INTO market_session_generations VALUES (?)",
            [("published",) for _session in sessions],
        )
        connection.commit()
    finally:
        connection.close()

    runner._finalize_store(root, sessions)
    assert not (root / "metadata.sqlite3-wal").exists()
    assert not (root / "metadata.sqlite3-shm").exists()

    connection = sqlite3.connect(database)
    try:
        connection.execute("INSERT INTO market_session_generations VALUES ('collecting')")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="partial generations"):
        runner._assert_no_partial_store_artifacts(root, sessions)


def test_resume_keeps_monotonic_completed_session_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _run_spec(tmp_path)
    spec_path = _write_spec(tmp_path, spec)
    market_sessions: list[str] = []
    fail_once = {"enabled": True}

    class FakeStore:
        def __init__(self, root: str) -> None:
            Path(root).mkdir(exist_ok=True)

    class FakeCollector:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def fetch_membership_snapshot(self, _spec: object, *, resume: bool) -> None:
            assert resume is True

        def collect_market_session_generation(
            self, session: str, *, resume: bool, vintage: str
        ) -> None:
            assert resume is True
            assert vintage == "historical_backfill"
            market_sessions.append(session)
            if fail_once["enabled"] and len(market_sessions) == 3:
                raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(runner, "PITReceiptStore", FakeStore)
    monkeypatch.setattr(runner, "ControlledTushareCollector", FakeCollector)
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)
    monkeypatch.setattr(runner, "_finalize_store", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "_publish_collection_candidate",
        lambda **_kwargs: PUBLICATION,
    )
    monkeypatch.setattr(
        runner,
        "_verify_collection_authority",
        lambda **_kwargs: {"verified": True, "receipt_sha256": "c" * 64},
    )

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        _run_with_synthetic_route(
            run_spec_path=spec_path,
            run_root=tmp_path / "resume-run",
        )
    failed_state = runner._read_json_file(
        tmp_path / "resume-run" / "state.json",
        label="run state",
        max_bytes=runner._MAX_STATE_BYTES,
    )
    assert failed_state["status"] == "failed"
    assert failed_state["completed_session_count"] == 2
    assert failed_state["credential_generation_id"] == SOURCE_GENERATION_ID

    fail_once["enabled"] = False
    result = _run_with_synthetic_route(
        run_spec_path=spec_path,
        run_root=tmp_path / "resume-run",
    )

    development, diagnostic = _sessions()
    assert result["status"] == "verified"
    assert result["completed_session_count"] == 250
    assert market_sessions.count(development[0]) == 1
    assert market_sessions.count(development[1]) == 1
    assert market_sessions.count(development[2]) == 2
    assert market_sessions[-1] == diagnostic[0]


def test_run_scoped_credential_generation_id_is_durable_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path, _run_spec(tmp_path))
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)

    first = runner._run_credential_generation_id(
        run_spec_path=spec_path,
        run_root=tmp_path / "generation-run",
    )
    second = runner._run_credential_generation_id(
        run_spec_path=spec_path,
        run_root=tmp_path / "generation-run",
    )

    assert first == second
    state = runner._read_json_file(
        tmp_path / "generation-run" / "state.json",
        label="run state",
        max_bytes=runner._MAX_STATE_BYTES,
    )
    assert state["credential_generation_id"] == first


def test_v1_zero_progress_state_migrates_to_v2_before_generation_is_issued(
    tmp_path: Path,
) -> None:
    spec = _run_spec(tmp_path)
    paths = runner._run_paths(tmp_path / "v1-zero-run", create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    runner._atomic_json(
        paths["state"],
        _v1_state(
            run_spec_sha256=spec["run_spec_sha256"],
            status="initialized",
            completed_session_count=0,
        ),
    )

    migrated = runner._load_or_initialize_run(paths, spec, allow_initialize=True)

    assert migrated["schema"] == runner._RUN_STATE_SCHEMA
    assert migrated["credential_generation_id"] is None


def test_v1_partial_state_migrates_only_a_single_persisted_generation(
    tmp_path: Path,
) -> None:
    spec = _run_spec(tmp_path)
    paths = runner._run_paths(tmp_path / "v1-partial-run", create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    store = runner._safe_directory(paths["store"], label="PIT store", create=True)
    with sqlite3.connect(store / "metadata.sqlite3") as connection:
        connection.execute("CREATE TABLE fetch_attempts (request_semantics_json TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO fetch_attempts VALUES (?)",
            (runner._canonical_bytes({"credential_generation_id": SOURCE_GENERATION_ID}).decode(),),
        )
        connection.commit()
    runner._atomic_json(
        paths["state"],
        _v1_state(
            run_spec_sha256=spec["run_spec_sha256"],
            status="collecting",
            completed_session_count=1,
        ),
    )

    migrated = runner._load_or_initialize_run(paths, spec, allow_initialize=True)

    assert migrated["schema"] == runner._RUN_STATE_SCHEMA
    assert migrated["credential_generation_id"] == SOURCE_GENERATION_ID


def test_v1_collecting_zero_progress_state_becomes_restartable_v2_failure(
    tmp_path: Path,
) -> None:
    spec = _run_spec(tmp_path)
    paths = runner._run_paths(tmp_path / "v1-collecting-zero-run", create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    runner._atomic_json(
        paths["state"],
        _v1_state(
            run_spec_sha256=spec["run_spec_sha256"],
            status="collecting",
            completed_session_count=0,
        ),
    )

    migrated = runner._load_or_initialize_run(paths, spec, allow_initialize=True)

    assert migrated["status"] == "failed"
    assert migrated["credential_generation_id"] is None


def test_partial_resume_rejects_a_different_credential_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _run_spec(tmp_path)
    spec_path = _write_spec(tmp_path, spec)
    paths = runner._run_paths(tmp_path / "generation-drift-run", create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="collecting",
            completed_session_count=1,
            credential_generation_id=SOURCE_GENERATION_ID,
        ),
    )
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="generation drifted"):
        runner._run_factor_v3_feature_history_collection_with_route_credential(
            run_spec_path=spec_path,
            run_root=tmp_path / "generation-drift-run",
            credential="synthetic-token",
            source_generation_id="8a879340-f696-472d-8f44-bf840e71e0fc",
            feature_history_policy_descriptor=(
                runner.FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
            ),
        )


def test_resume_reverifies_published_candidate_without_recollecting_or_republishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _run_spec(tmp_path)
    spec_path = _write_spec(tmp_path, spec)
    run_root = tmp_path / "published-run"
    paths = runner._run_paths(run_root, create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    runner._safe_directory(paths["store"], label="PIT store", create=True)
    runner._safe_directory(paths["publication_root"], label="publication root", create=True)
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="published",
            completed_session_count=250,
            credential_generation_id=SOURCE_GENERATION_ID,
            collection_publication=PUBLICATION,
        ),
    )
    verification_attempts = {"count": 0}

    def verify(**_kwargs: object) -> dict[str, object]:
        verification_attempts["count"] += 1
        if verification_attempts["count"] == 1:
            raise RuntimeError("synthetic verification fault")
        return {"verified": True, "receipt_sha256": "d" * 64}

    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)
    monkeypatch.setattr(runner, "_assert_no_partial_store_artifacts", lambda *_args: None)
    monkeypatch.setattr(runner, "_verify_collection_authority", verify)
    monkeypatch.setattr(
        runner,
        "_publish_collection_candidate",
        lambda **_kwargs: pytest.fail("published candidate must not be published again"),
    )
    monkeypatch.setattr(
        runner,
        "_fixed_jiaoch_source",
        lambda **_kwargs: pytest.fail("published candidate must not recollect"),
    )

    with pytest.raises(RuntimeError, match="synthetic verification fault"):
        _run_with_synthetic_route(
            run_spec_path=spec_path,
            run_root=run_root,
        )
    failed = runner._read_json_file(
        paths["state"], label="run state", max_bytes=runner._MAX_STATE_BYTES
    )
    assert failed["status"] == "failed"
    assert failed["collection_publication"] == PUBLICATION

    resumed = runner.verify_factor_v3_feature_history_run(
        run_spec_path=spec_path,
        run_root=run_root,
    )
    assert resumed["status"] == "verified"
    assert verification_attempts["count"] == 2


def test_public_verify_demotes_stale_verified_state_on_authority_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _run_spec(tmp_path)
    spec_path = _write_spec(tmp_path, spec)
    run_root = tmp_path / "stale-verified-run"
    paths = runner._run_paths(run_root, create=True)
    runner._load_or_initialize_run(paths, spec, allow_initialize=True)
    runner._safe_directory(paths["store"], label="PIT store", create=True)
    runner._safe_directory(paths["publication_root"], label="publication root", create=True)
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="verified",
            completed_session_count=250,
            credential_generation_id=SOURCE_GENERATION_ID,
            collection_publication=PUBLICATION,
            receipt={"verified": True, "receipt_sha256": "d" * 64},
        ),
    )

    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)

    def reject(**_kwargs: object) -> dict[str, object]:
        raise runner.FactorV3FeatureHistoryRunnerError(
            "synthetic producer binding rejection"
        )

    monkeypatch.setattr(runner, "_verify_collection_authority", reject)

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="producer binding"):
        runner.verify_factor_v3_feature_history_run(
            run_spec_path=spec_path,
            run_root=run_root,
        )

    failed = runner._read_json_file(
        paths["state"], label="run state", max_bytes=runner._MAX_STATE_BYTES
    )
    assert failed["status"] == "failed"
    assert failed["completed_session_count"] == 250
    assert failed["collection_publication"] == PUBLICATION


def test_run_rejects_nonfrozen_jiaoch_source_before_creating_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path, _run_spec(tmp_path))
    collector_created: list[bool] = []

    class FakeStore:
        def __init__(self, root: str) -> None:
            Path(root).mkdir(exist_ok=True)

    class FakeCollector:
        def __init__(self, **_kwargs: object) -> None:
            collector_created.append(True)

    monkeypatch.setattr(runner, "PITReceiptStore", FakeStore)
    monkeypatch.setattr(runner, "ControlledTushareCollector", FakeCollector)
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)
    monkeypatch.setattr(
        runner,
        "_fixed_jiaoch_source",
        lambda **_kwargs: SimpleNamespace(
            api_url="https://evil.example.test",
            allowed_hosts=("evil.example.test",),
            proxy_url=None,
            name="jiaoch",
            request_protocol="tushare-path-per-interface/v1",
            row_cap_overrides=(),
            network_route="direct",
        ),
    )

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="Jiaoch source"):
        _run_with_synthetic_route(
            run_spec_path=spec_path,
            run_root=tmp_path / "invalid-source-run",
        )
    assert collector_created == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token", ""),
        ("generation_id", "not-a-uuid"),
        ("row_cap_overrides", ()),
        ("api_url", "https://other.example.test"),
        ("allowed_hosts", ("other.example.test",)),
        ("request_protocol", "tushare-root-post/v1"),
        ("network_route", "loopback_http_proxy"),
        ("proxy_url", "http://127.0.0.1:8080"),
    ],
)
def test_frozen_jiaoch_source_guard_rejects_each_authority_field(
    field: str,
    value: object,
) -> None:
    source = replace(
        runner._fixed_jiaoch_source(
            credential="synthetic-token",
            source_generation_id=SOURCE_GENERATION_ID,
        ),
        **{field: value},
    )

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="Jiaoch source"):
        runner._validated_frozen_jiaoch_source(source)


def test_frozen_jiaoch_source_guard_requires_exact_source_type() -> None:
    source = SimpleNamespace(
        token="synthetic-token",
        generation_id=SOURCE_GENERATION_ID,
        name="jiaoch",
        api_url="https://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        request_protocol="tushare-path-per-interface/v1",
        row_cap_overrides=(("stk_limit", 10_000),),
        network_route="direct",
        proxy_url=None,
    )

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="Jiaoch source"):
        runner._validated_frozen_jiaoch_source(source)


def test_verify_does_not_initialize_an_absent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path, _run_spec(tmp_path))
    run_root = tmp_path / "absent-run"
    run_root.mkdir()
    monkeypatch.setattr(runner, "_verify_plan", lambda _spec: None)

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="unavailable"):
        runner.verify_factor_v3_feature_history_run(
            run_spec_path=spec_path,
            run_root=run_root,
        )

    assert {path.name for path in run_root.iterdir()} == {
        ".factor-v3-feature-history-runner.lock"
    }


def test_run_lock_rejects_a_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "other.lock"
    target.write_bytes(b"\0")
    link = tmp_path / "runner.lock"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="lock unavailable"):
        with runner._run_lock(link):
            pytest.fail("symlink lock must not be acquired")


def test_run_lock_rejects_opened_file_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = tmp_path / "runner.lock"
    expected.write_bytes(b"\0")
    substitute = tmp_path / "substitute.lock"
    substitute.write_bytes(b"\0")
    real_open = runner.os.open

    def open_substitute(path: str, flags: int, mode: int) -> int:
        assert Path(path) == expected
        return real_open(str(substitute), flags, mode)

    monkeypatch.setattr(runner.os, "open", open_substitute)
    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="lock unavailable"):
        with runner._run_lock(expected):
            pytest.fail("drifted lock identity must not be acquired")


def test_run_lock_rejects_a_hardlink_target(tmp_path: Path) -> None:
    target = tmp_path / "other.lock"
    target.write_bytes(b"\0")
    link = tmp_path / "runner.lock"
    try:
        runner.os.link(target, link)
    except OSError:
        pytest.skip("hardlink creation is unavailable")

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="lock unavailable"):
        with runner._run_lock(link):
            pytest.fail("hardlink lock must not be acquired")


def test_run_lock_rejects_a_reparse_parent(tmp_path: Path) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(runner.FactorV3FeatureHistoryRunnerError, match="lock unavailable"):
        with runner._run_lock(link / "runner.lock"):
            pytest.fail("lock beneath a reparse parent must not be acquired")


def test_cli_has_only_run_and_verify_commands() -> None:
    parser = runner._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect"])
    parsed = parser.parse_args(["verify", "--run-spec", "a", "--run-root", "b"])
    assert parsed.command == "verify"
