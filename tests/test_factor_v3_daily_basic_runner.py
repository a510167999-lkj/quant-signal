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
        "_verified_history_sources",
        lambda **_kwargs: (prewindow, development, "a" * 64, "b" * 64),
    )
    return prewindow, development


def _authority_inputs(tmp_path: Path) -> dict[str, object]:
    return {
        "audited_universe_sqlite_path": str((tmp_path / "universe.sqlite").resolve()),
        "expected_artifact_root_sha256": "c" * 64,
        "expected_coverage_audit_sha256": "d" * 64,
        "expected_security_code_transition_contract_sha256": "e" * 64,
        "expected_temporal_contract_sha256": "f" * 64,
        "expected_temporal_role": "development_4",
        "security_code_transition_evidence_root": str((tmp_path / "transitions").resolve()),
    }


def _spec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    _sources(monkeypatch)
    return runner.build_factor_v3_daily_basic_run_spec(
        feature_history_collection_plan={"frozen": "plan"},
        feature_history_authority_receipt={"verified": True},
        development_session_refs=[{"trade_date": "sentinel"}],
        exact_set_authority_inputs=_authority_inputs(tmp_path),
        timeout_seconds=30,
        max_attempts=3,
    )


def test_run_spec_derives_exact_ordered_733_session_union(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prewindow, development = _sources(monkeypatch)
    spec = runner.build_factor_v3_daily_basic_run_spec(
        feature_history_collection_plan={"frozen": "plan"},
        feature_history_authority_receipt={"verified": True},
        development_session_refs=[{"trade_date": "sentinel"}],
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
        "_verified_history_sources",
        lambda **_kwargs: (prewindow, [prewindow[-1], *development[1:]], "a" * 64, "b" * 64),
    )

    with pytest.raises(runner.FactorV3DailyBasicRunnerError, match="733"):
        runner.build_factor_v3_daily_basic_run_spec(
            feature_history_collection_plan={"frozen": "plan"},
            feature_history_authority_receipt={"verified": True},
            development_session_refs=[{"trade_date": "sentinel"}],
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
