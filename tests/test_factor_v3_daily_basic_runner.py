from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import uuid

import pytest

from app import factor_v3_daily_basic_runner as runner


def _sessions(start: date, count: int) -> list[str]:
    output: list[str] = []
    current = start
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current.isoformat())
        current += timedelta(days=1)
    return output


def _sources(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    prewindow = _sessions(date(2022, 1, 3), 250)
    development = _sessions(date.fromisoformat(prewindow[-1]) + timedelta(days=1), 483)
    monkeypatch.setattr(
        runner,
        "_verified_733_sources",
        lambda _inputs: ([*prewindow, *development], "a" * 64),
    )
    return prewindow, development


def _authority_inputs(tmp_path: Path) -> dict[str, object]:
    return {
        "audited_development_universe_sqlite_path": str(
            (tmp_path / "universe.sqlite").resolve()
        ),
        "expected_development_artifact_root_sha256": "c" * 64,
        "expected_development_coverage_audit_sha256": "d" * 64,
        "expected_development_temporal_contract_sha256": "f" * 64,
        "expected_development_temporal_role": "development_4",
        "expected_security_code_transition_contract_sha256": "e" * 64,
        "feature_history_run_root": str((tmp_path / "feature-history-run").resolve()),
        "feature_history_run_spec_path": str(
            (tmp_path / "feature-history-spec.json").resolve()
        ),
        "security_code_transition_evidence_root": str((tmp_path / "transitions").resolve()),
    }


def _spec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    _sources(monkeypatch)
    return runner.build_factor_v3_daily_basic_run_spec(
        exact_set_authority_inputs=_authority_inputs(tmp_path),
        timeout_seconds=30,
        max_attempts=3,
    )


def test_run_spec_derives_exact_ordered_733_session_union(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prewindow, development = _sources(monkeypatch)
    spec = runner.build_factor_v3_daily_basic_run_spec(
        exact_set_authority_inputs=_authority_inputs(tmp_path),
        timeout_seconds=30,
        max_attempts=3,
    )

    assert spec["session_count"] == 733
    assert spec["sessions"] == [*prewindow, *development]
    assert spec["sessions"] == sorted(spec["sessions"])
    assert spec["collection_policy_descriptor"] == (
        runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR
    )


def test_run_spec_rejects_overlapping_or_handwritten_source_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prewindow, development = _sources(monkeypatch)
    monkeypatch.setattr(
        runner,
        "_verified_733_sources",
        lambda _inputs: ([*prewindow, prewindow[-1], *development[1:]], "a" * 64),
    )

    with pytest.raises(runner.FactorV3DailyBasicRunnerError, match="733"):
        runner.build_factor_v3_daily_basic_run_spec(
            exact_set_authority_inputs=_authority_inputs(tmp_path),
            timeout_seconds=30,
            max_attempts=3,
        )


def test_policy_has_only_jiaoch_points_primary_daily_basic() -> None:
    descriptor = runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR
    assert descriptor["document"] == {
        "schema": "jiaoch-credential-factor-v3-daily-basic-routing-policy/v1",
        "routes": [
            {
                "api_name": "daily_basic",
                "credential_slot_id": "points-primary",
                "purpose": "factor-v3-daily-basic",
                "route_id": "factor-v3-daily-basic:points-primary:daily_basic",
            }
        ],
    }


def test_failed_collection_never_calls_exact_set_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _spec(monkeypatch, tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(
        runner,
        "_collect_one_daily_basic",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    monkeypatch.setattr(
        runner,
        "_publish_exact_set_coverage",
        lambda **_kwargs: calls.append("published"),
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        runner._run_factor_v3_daily_basic_collection_with_route_credential(
            run_spec_path=spec_path,
            run_root=tmp_path / "run",
            credential="test-only-credential",
            source_generation_id=str(uuid.uuid4()),
            daily_basic_policy_descriptor=runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR,
        )

    assert calls == []
    state = json.loads((tmp_path / "run" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["receipt"] is None


def test_resume_only_collects_the_missing_session_and_keeps_one_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _spec(monkeypatch, tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    paths = runner._paths(tmp_path / "run", create=True)
    refs = [
        {
            "trade_date": session,
            "collection_set_relative_path": f"sets/{index}",
            "collection_set_sha256": f"{index:064x}",
        }
        for index, session in enumerate(spec["sessions"][:-1])
    ]
    generation = str(uuid.uuid4())
    runner._load_or_initialize(paths, spec, allow_initialize=True)
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="collecting",
            completed_session_count=len(refs),
            credential_generation_id=generation,
            collection_set_refs=refs,
        ),
    )
    calls: list[str] = []
    monkeypatch.setattr(runner, "_verify_one_daily_basic", lambda **kwargs: dict(kwargs["ref"]))
    monkeypatch.setattr(runner, "_assert_complete_points_output", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "_collect_one_daily_basic",
        lambda **kwargs: calls.append(kwargs["trade_date"])
        or {
            "trade_date": kwargs["trade_date"],
            "collection_set_relative_path": "sets/final",
            "collection_set_sha256": "f" * 64,
        },
    )
    monkeypatch.setattr(
        runner,
        "_publish_exact_set_coverage",
        lambda **_kwargs: {
            "attestation_relative_path": "attestation.json",
            "attestation_sha256": "c" * 64,
            "publication_relative_path": "publication.json",
            "publication_sha256": "d" * 64,
            "receipt_relative_path": "receipt.json",
            "receipt_sha256": "b" * 64,
            "schema": "factor-v3-daily-basic-733-exact-set-publication/v2",
        },
    )
    monkeypatch.setattr(
        runner,
        "_verify_exact_set_coverage",
        lambda **_kwargs: {
            "authority_root_sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "trade_date_count": 733,
            "trade_dates": spec["sessions"],
            "verified": True,
        },
    )

    result = runner._run_factor_v3_daily_basic_collection_with_route_credential(
        run_spec_path=spec_path,
        run_root=tmp_path / "run",
        credential="test-only-credential",
        source_generation_id=generation,
        daily_basic_policy_descriptor=runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR,
    )

    assert calls == [spec["sessions"][-1]]
    assert result["status"] == "verified"
    assert result["completed_session_count"] == 733


def test_partial_run_rejects_different_sealed_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _spec(monkeypatch, tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    paths = runner._paths(tmp_path / "run", create=True)
    runner._load_or_initialize(paths, spec, allow_initialize=True)
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="collecting",
            completed_session_count=0,
            credential_generation_id=str(uuid.uuid4()),
            collection_set_refs=[],
        ),
    )

    with pytest.raises(runner.FactorV3DailyBasicRunnerError, match="generation drifted"):
        runner._run_factor_v3_daily_basic_collection_with_route_credential(
            run_spec_path=spec_path,
            run_root=tmp_path / "run",
            credential="test-only-credential",
            source_generation_id=str(uuid.uuid4()),
            daily_basic_policy_descriptor=runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR,
        )


def test_verify_replays_terminal_exact_set_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _spec(monkeypatch, tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(spec, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    paths = runner._paths(tmp_path / "run", create=True)
    runner._load_or_initialize(paths, spec, allow_initialize=True)
    paths["points"].mkdir()
    paths["authority"].mkdir()
    refs = [
        {
            "trade_date": session,
            "collection_set_relative_path": f"daily_basic_collection_sets/{index}",
            "collection_set_sha256": f"{index:064x}",
        }
        for index, session in enumerate(spec["sessions"])
    ]
    publication = {
        "attestation_relative_path": "attestation.json",
        "attestation_sha256": "c" * 64,
        "publication_relative_path": "publication.json",
        "publication_sha256": "d" * 64,
        "receipt_relative_path": "receipt.json",
        "receipt_sha256": "b" * 64,
        "schema": "factor-v3-daily-basic-733-exact-set-publication/v2",
    }
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="verified",
            completed_session_count=733,
            credential_generation_id=str(uuid.uuid4()),
            collection_set_refs=refs,
            exact_set_publication=publication,
            receipt={
                "authority_root_sha256": "a" * 64,
                "receipt_relative_path": "receipt.json",
                "receipt_sha256": "b" * 64,
                "verified": True,
            },
        ),
    )
    monkeypatch.setattr(
        runner,
        "_assert_complete_points_output",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_verify_exact_set_coverage",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner.FactorV3DailyBasicRunnerError("terminal tamper")
        ),
    )

    with pytest.raises(runner.FactorV3DailyBasicRunnerError, match="terminal tamper"):
        runner.verify_factor_v3_daily_basic_run(
            run_spec_path=spec_path,
            run_root=tmp_path / "run",
        )


def test_already_verified_run_rejects_rehashed_state_receipt_path_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _spec(monkeypatch, tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(spec, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    paths = runner._paths(tmp_path / "run", create=True)
    runner._load_or_initialize(paths, spec, allow_initialize=True)
    paths["points"].mkdir()
    paths["authority"].mkdir()
    generation = str(uuid.uuid4())
    refs = [
        {
            "trade_date": session,
            "collection_set_relative_path": f"daily_basic_collection_sets/{index}",
            "collection_set_sha256": f"{index:064x}",
        }
        for index, session in enumerate(spec["sessions"])
    ]
    publication = {
        "attestation_relative_path": "attestation.json",
        "attestation_sha256": "c" * 64,
        "publication_relative_path": "publication.json",
        "publication_sha256": "d" * 64,
        "receipt_relative_path": "receipt.json",
        "receipt_sha256": "b" * 64,
        "schema": "factor-v3-daily-basic-733-exact-set-publication/v2",
    }
    runner._atomic_json(
        paths["state"],
        runner._state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="verified",
            completed_session_count=733,
            credential_generation_id=generation,
            collection_set_refs=refs,
            exact_set_publication=publication,
            receipt={
                "authority_root_sha256": "a" * 64,
                "receipt_relative_path": "tampered-receipt.json",
                "receipt_sha256": "b" * 64,
                "verified": True,
            },
        ),
    )
    monkeypatch.setattr(
        runner,
        "_assert_complete_points_output",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_verify_exact_set_coverage",
        lambda **_kwargs: {
            "authority_root_sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "trade_date_count": 733,
            "trade_dates": spec["sessions"],
            "verified": True,
        },
    )

    with pytest.raises(runner.FactorV3DailyBasicRunnerError, match="terminal receipt"):
        runner._run_factor_v3_daily_basic_collection_with_route_credential(
            run_spec_path=spec_path,
            run_root=tmp_path / "run",
            credential="test-only-credential",
            source_generation_id=generation,
            daily_basic_policy_descriptor=(
                runner.FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR
            ),
        )
