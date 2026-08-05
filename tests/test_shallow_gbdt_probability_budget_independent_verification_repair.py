from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from scripts import repair_shallow_gbdt_probability_budget_independent_verification as repair
from scripts import verify_shallow_gbdt_probability_budget_development_1 as verifier


def _write(path: Path, value: dict[str, object]) -> bytes:
    raw = verifier._canonical_bytes(value) + b"\n"
    path.write_bytes(raw)
    return raw


def _inputs(tmp_path: Path) -> dict[str, object]:
    return {
        "run_root": tmp_path,
        "completion_sha256": "a" * 64,
        "main_artifact_sha256": "b" * 64,
        "runtime_verification_artifact_sha256": "c" * 64,
        "source": {
            "git_commit": "d" * 40,
            "python_executable_sha256": "e" * 64,
            "strategy_sha256": "f" * 64,
            "producer_root_sha256": "1" * 64,
        },
    }


def _write_prior_failure(inputs: dict[str, object]) -> tuple[bytes, bytes]:
    run_root = inputs["run_root"]
    status = {
        "schema_version": verifier.STATUS_SCHEMA,
        "status": "failed",
        "stage": "failed",
        "pid": 1,
        "finished_at_utc": "2026-08-05T00:00:00Z",
        "run_root": verifier.RUN_ROOT_NAME,
        "claim_sha256": "2" * 64,
        "verified": False,
        "receipt": None,
        "error_type": "IndependentVerificationError",
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    preregistration = {
        "schema_version": verifier.PREREGISTRATION_SCHEMA,
        "run_root": verifier.RUN_ROOT_NAME,
        "completion_sha256": inputs["completion_sha256"],
        "main_artifact_sha256": inputs["main_artifact_sha256"],
        "runtime_verification_artifact_sha256": inputs["runtime_verification_artifact_sha256"],
        "source_git_commit": inputs["source"]["git_commit"],
        "source_python_sha256": inputs["source"]["python_executable_sha256"],
        "strategy_sha256": inputs["source"]["strategy_sha256"],
        "producer_root_sha256": inputs["source"]["producer_root_sha256"],
        "replay_plan_sha256": verifier.EXPECTED_REPLAY_PLAN_SHA256,
        "verifier_git_commit": repair.LEGACY_ENVELOPE_MISMATCH_VERIFIER_COMMIT,
        "verifier_script_sha256": "4" * 64,
        "verifier_script_git_blob_sha256": repair.LEGACY_ENVELOPE_MISMATCH_VERIFIER_BLOB_SHA256,
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
            "automatic_trading_authority": False,
        },
        "resource_contract": {"memory_policy": "unbounded", "enforcement": "none"},
    }
    return (
        _write(run_root / verifier.STATUS_NAME, status),
        _write(run_root / verifier.PREREGISTRATION_NAME, preregistration),
    )


def _prepared(tmp_path: Path) -> tuple[dict[str, object], bytes, bytes]:
    inputs = _inputs(tmp_path)
    status_raw, preregistration_raw = _write_prior_failure(inputs)
    prior_failure = repair._load_prior_failure(inputs)
    replay = {
        "result_path": tmp_path / verifier.REPLAY_RESULT_NAME,
        "result_sha256": "6" * 64,
        "stdout": {"path": verifier.REPLAY_STDOUT_NAME, "bytes": 0, "sha256": "7" * 64},
        "stderr": {"path": verifier.REPLAY_STDERR_NAME, "bytes": 0, "sha256": "8" * 64},
    }
    prepared = {
        "identity": {
            "git_commit": "9" * 40,
            "repair_script_sha256": "a" * 64,
            "repair_script_git_blob_sha256": "b" * 64,
            "core_script_sha256": "c" * 64,
            "core_script_git_blob_sha256": "d" * 64,
        },
        "inputs": inputs,
        "prior_failure": prior_failure,
        "replay": replay,
    }
    return prepared, status_raw, preregistration_raw


def test_load_prior_failure_requires_original_failure_to_remain_nonpromotable(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    status_raw, preregistration_raw = _write_prior_failure(inputs)

    prior = repair._load_prior_failure(inputs)

    assert prior["status_sha256"] == verifier._sha256_bytes(status_raw)
    assert prior["preregistration_sha256"] == verifier._sha256_bytes(preregistration_raw)
    status_path = tmp_path / verifier.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["development_statistical_interpretation_allowed"] = True
    _write(status_path, status)

    with pytest.raises(repair.RepairVerificationError):
        repair._load_prior_failure(inputs)


def test_load_prior_failure_rejects_an_unknown_legacy_failure(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    _write_prior_failure(inputs)
    status_path = tmp_path / verifier.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["error_type"] = "ValueError"
    _write(status_path, status)

    with pytest.raises(repair.RepairVerificationError):
        repair._load_prior_failure(inputs)


def test_run_publishes_repair_receipt_without_mutating_original_failure(monkeypatch, tmp_path: Path) -> None:
    prepared, status_raw, preregistration_raw = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    monkeypatch.setattr(repair, "_load_existing_replay", lambda *_: prepared["replay"])

    result = repair.run("ignored", "9" * 40)

    assert result["status"] == "completed"
    assert (tmp_path / verifier.STATUS_NAME).read_bytes() == status_raw
    assert (tmp_path / verifier.PREREGISTRATION_NAME).read_bytes() == preregistration_raw
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["verified"] is True
    assert status["development_statistical_interpretation_allowed"] is False
    assert status["profile_registration_authority"] is False
    assert status["production_recommendation_authority"] is False
    assert status["automatic_trading_authority"] is False
    assert status["repair_status_authority"] == {
        "current_input_revalidation_required": True,
        "status_alone_authoritative": False,
    }
    claim = json.loads((tmp_path / repair.CLAIM_NAME).read_text(encoding="utf-8"))
    assert claim["claim_authority"] == {
        "terminal_repair_status_required": True,
        "claim_alone_authoritative": False,
    }
    receipt_path = tmp_path / status["receipt"]["path"]
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["post_verification_authority"] == {
        "development_statistical_interpretation_allowed": False,
        "profile_registration_authority": False,
        "production_recommendation_authority": False,
        "automatic_trading_authority": False,
    }
    assert receipt["receipt_authority"] == {
        "terminal_completed_repair_status_required": True,
        "current_input_revalidation_required": True,
        "receipt_alone_authoritative": False,
    }


def test_run_records_fail_closed_terminal_status(monkeypatch, tmp_path: Path) -> None:
    prepared, status_raw, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    monkeypatch.setattr(
        repair,
        "_publish_receipt",
        lambda *_: (_ for _ in ()).throw(repair.RepairVerificationError()),
    )

    with pytest.raises(repair.RepairVerificationError):
        repair.run("ignored", "9" * 40)

    assert (tmp_path / verifier.STATUS_NAME).read_bytes() == status_raw
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert status["receipt"] is None
    assert status["profile_registration_authority"] is False
    assert (tmp_path / repair.CLAIM_NAME).is_file()


def test_run_fails_closed_when_evidence_changes_after_claim(monkeypatch, tmp_path: Path) -> None:
    prepared, status_raw, _ = _prepared(tmp_path)
    changed = deepcopy(prepared)
    changed["replay"]["result_sha256"] = "0" * 64
    calls = iter((prepared, changed))
    monkeypatch.setattr(repair, "preflight", lambda *_: next(calls))

    with pytest.raises(repair.RepairVerificationError):
        repair.run("ignored", "9" * 40)

    assert (tmp_path / verifier.STATUS_NAME).read_bytes() == status_raw
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert status["receipt"] is None
    assert not list((tmp_path / repair.RECEIPT_ROOT_NAME).rglob("*.json"))
    assert (tmp_path / repair.CLAIM_NAME).is_file()


def test_orphan_receipt_cannot_authorize_when_final_preflight_changes(monkeypatch, tmp_path: Path) -> None:
    prepared, status_raw, _ = _prepared(tmp_path)
    changed = deepcopy(prepared)
    changed["replay"]["result_sha256"] = "0" * 64
    calls = iter((prepared, prepared, changed))
    monkeypatch.setattr(repair, "preflight", lambda *_: next(calls))

    with pytest.raises(repair.RepairVerificationError):
        repair.run("ignored", "9" * 40)

    assert (tmp_path / verifier.STATUS_NAME).read_bytes() == status_raw
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert status["receipt"] is None
    receipts = list((tmp_path / repair.RECEIPT_ROOT_NAME).rglob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["post_verification_authority"]["development_statistical_interpretation_allowed"] is False
    assert receipt["receipt_authority"]["terminal_completed_repair_status_required"] is True
    assert receipt["receipt_authority"]["current_input_revalidation_required"] is True
    assert receipt["receipt_authority"]["receipt_alone_authoritative"] is False
    assert (tmp_path / repair.CLAIM_NAME).is_file()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handles")
def test_receipt_publication_uses_a_handle_bound_directory_chain(tmp_path: Path) -> None:
    body = {"schema_version": "test-repair-receipt/v1"}
    digest = verifier._sha256_bytes(verifier._canonical_bytes(body))
    raw = verifier._canonical_bytes({**body, "receipt_sha256": digest}) + b"\n"

    with repair._HeldWindowsRunRoot(tmp_path, include_receipt_tree=True) as publisher:
        path = publisher.write_receipt_once(digest=digest, raw=raw)
        assert publisher.write_receipt_once(digest=digest, raw=raw) == path

    assert path.parent == tmp_path / repair.RECEIPT_ROOT_NAME / "sha256"
    assert path.read_bytes() == raw


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handles")
def test_completed_status_survives_a_best_effort_handle_close_failure(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    original_close = repair._HeldWindowsRunRoot._close
    leaked: list[int] = []

    def close_once(handle: int) -> bool:
        if (tmp_path / repair.STATUS_NAME).exists() and not leaked:
            leaked.append(handle)
            raise repair.RepairVerificationError("simulated close failure")
        return original_close(handle)

    monkeypatch.setattr(repair._HeldWindowsRunRoot, "_close", staticmethod(close_once))
    try:
        result = repair.run("ignored", "9" * 40)
    finally:
        for handle in leaked:
            original_close(handle)

    assert result["status"] == "completed"
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["verified"] is True
    assert status["development_statistical_interpretation_allowed"] is False


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handles")
def test_completed_status_survives_a_false_handle_close_result(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    original_close = repair._HeldWindowsRunRoot._close
    leaked: list[int] = []

    def close_once(handle: int) -> bool:
        if (tmp_path / repair.STATUS_NAME).exists() and not leaked:
            leaked.append(handle)
            return False
        return original_close(handle)

    monkeypatch.setattr(repair._HeldWindowsRunRoot, "_close", staticmethod(close_once))
    try:
        result = repair.run("ignored", "9" * 40)
    finally:
        for handle in leaked:
            original_close(handle)

    assert result["status"] == "completed"
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["verified"] is True
    assert status["development_statistical_interpretation_allowed"] is False


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handles")
def test_run_writes_all_new_artifacts_through_the_held_root(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    monkeypatch.setattr(
        verifier,
        "_write_once",
        lambda *_: (_ for _ in ()).throw(AssertionError("pathname writer must remain unused")),
    )

    result = repair.run("ignored", "9" * 40)

    assert result["status"] == "completed"
    assert (tmp_path / repair.PREREGISTRATION_NAME).is_file()
    assert (tmp_path / repair.CLAIM_NAME).is_file()
    assert (tmp_path / repair.STATUS_NAME).is_file()


def test_completed_snapshot_remains_non_authoritative_after_a_late_input_mutation(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    original_record_status = repair._record_status

    def mutate_then_record(*args, **kwargs):
        original_failure = json.loads((tmp_path / verifier.STATUS_NAME).read_text(encoding="utf-8"))
        original_failure["error_type"] = "changed-after-final-preflight"
        _write(tmp_path / verifier.STATUS_NAME, original_failure)
        return original_record_status(*args, **kwargs)

    monkeypatch.setattr(repair, "_record_status", mutate_then_record)

    result = repair.run("ignored", "9" * 40)

    assert result["status"] == "completed"
    status = json.loads((tmp_path / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["verified"] is True
    assert status["development_statistical_interpretation_allowed"] is False
    assert status["repair_status_authority"]["current_input_revalidation_required"] is True
    assert status["repair_status_authority"]["status_alone_authoritative"] is False


def test_verify_current_repair_snapshot_revalidates_the_completed_artifacts(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)

    repair.run("ignored", "9" * 40)

    assert repair.verify_current_repair_snapshot("ignored", "9" * 40) == {
        "status": "current_snapshot_verified"
    }


def test_verify_current_repair_snapshot_rejects_authority_flag_tampering(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    repair.run("ignored", "9" * 40)
    status_path = tmp_path / repair.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["development_statistical_interpretation_allowed"] = True
    _write(status_path, status)

    with pytest.raises(repair.RepairVerificationError):
        repair.verify_current_repair_snapshot("ignored", "9" * 40)


@pytest.mark.parametrize("name", [repair.CLAIM_NAME, repair.STATUS_NAME])
def test_verify_current_repair_snapshot_rejects_unknown_schema_fields(monkeypatch, tmp_path: Path, name: str) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    repair.run("ignored", "9" * 40)
    path = tmp_path / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected_authority"] = True
    _write(path, payload)

    with pytest.raises(repair.RepairVerificationError):
        repair.verify_current_repair_snapshot("ignored", "9" * 40)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file-share enforcement")
def test_verify_current_snapshot_holds_artifacts_through_the_final_preflight(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    repair.run("ignored", "9" * 40)
    calls = 0

    def preflight_with_late_write(*_):
        nonlocal calls
        calls += 1
        if calls == 2:
            status_path = tmp_path / repair.STATUS_NAME
            with pytest.raises(OSError):
                status_path.write_bytes(status_path.read_bytes())
        return prepared

    monkeypatch.setattr(repair, "preflight", preflight_with_late_write)

    assert repair.verify_current_repair_snapshot("ignored", "9" * 40) == {
        "status": "current_snapshot_verified"
    }


def test_verify_current_repair_snapshot_rejects_current_input_binding_drift(monkeypatch, tmp_path: Path) -> None:
    prepared, _, _ = _prepared(tmp_path)
    monkeypatch.setattr(repair, "preflight", lambda *_: prepared)
    repair.run("ignored", "9" * 40)
    changed = deepcopy(prepared)
    changed["replay"]["result_sha256"] = "0" * 64
    calls = iter((prepared, changed))
    monkeypatch.setattr(repair, "preflight", lambda *_: next(calls))

    with pytest.raises(repair.RepairVerificationError):
        repair.verify_current_repair_snapshot("ignored", "9" * 40)


def test_held_root_rejects_dot_dot_relative_names() -> None:
    with pytest.raises(repair.RepairVerificationError):
        repair._HeldWindowsRunRoot._validate_leaf_name("..")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory handles")
def test_second_transaction_rejects_a_different_directory_identity_chain(tmp_path: Path) -> None:
    with repair._HeldWindowsRunRoot(tmp_path) as first:
        expected = first.chain_identity
    changed = (*expected[:-1], (expected[-1][0], expected[-1][1] + 1))

    with pytest.raises(repair.RepairVerificationError):
        with repair._HeldWindowsRunRoot(tmp_path, expected_chain=changed):
            pass


def test_main_only_reports_fixed_status_words(monkeypatch, capsys) -> None:
    monkeypatch.setattr(repair, "preflight", lambda *_: {"ignored": True})

    assert repair.main(["--expected-repair-commit", "a" * 40, "--preflight"]) == 0
    assert capsys.readouterr().out == "status=preflight_verified\n"

    monkeypatch.setattr(repair, "run", lambda *_: {"status": "completed"})
    assert repair.main(["--expected-repair-commit", "a" * 40, "--run"]) == 0
    assert capsys.readouterr().out == "status=repair_verified\n"

    monkeypatch.setattr(repair, "verify_current_repair_snapshot", lambda *_: {"status": "current_snapshot_verified"})
    assert repair.main(["--expected-repair-commit", "a" * 40, "--verify-current"]) == 0
    assert capsys.readouterr().out == "status=current_snapshot_verified\n"

    monkeypatch.setattr(
        repair,
        "preflight",
        lambda *_: (_ for _ in ()).throw(RuntimeError()),
    )
    assert repair.main(["--expected-repair-commit", "a" * 40, "--preflight"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "status=failed\n"
