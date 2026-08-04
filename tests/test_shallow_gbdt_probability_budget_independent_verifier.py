from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import scripts.verify_shallow_gbdt_probability_budget_development_1 as verifier


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_content_addressed(directory: Path, body: dict[str, object]) -> tuple[Path, str, str]:
    artifact_sha256 = _sha256(_canonical_bytes(body))
    document = {**body, "artifact_sha256": artifact_sha256}
    raw = _canonical_bytes(document) + b"\n"
    path = directory / f"{artifact_sha256}.json"
    path.write_bytes(raw)
    return path, artifact_sha256, _sha256(raw)


def _runtime_document(main_artifact_sha256: str) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": verifier.EXPECTED_VERIFICATION_SCHEMA,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
        "main_artifact_sha256": main_artifact_sha256,
        "sidecar_artifact_sha256": {"selection": "b" * 64},
        "checks": {name: True for name in verifier.REQUIRED_VERIFICATION_CHECKS},
        "verified": True,
    }
    body["receipt_sha256"] = _sha256(_canonical_bytes(body))
    return body


def _make_completed_inputs(tmp_path: Path) -> dict[str, object]:
    run_root = tmp_path / verifier.RUN_ROOT_RELATIVE
    run_root.mkdir(parents=True)
    main_path, main_sha, main_file_sha = _write_content_addressed(
        run_root,
        {
            "schema_version": "test-main/v1",
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "producer_code": {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256},
            "scope": {
                "point_in_time": True,
                "development_only": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "eligible_for_profile_registration": False,
                "production_recommendation_eligible": False,
            },
        },
    )
    runtime_dir = run_root / "verifications"
    runtime_dir.mkdir()
    runtime_path, runtime_sha, runtime_file_sha = _write_content_addressed(
        runtime_dir,
        _runtime_document(main_sha),
    )
    resource = {
        "schema_version": "research-unbounded-command-receipt/v1",
        "exit_code": 0,
        "child_reaped": True,
        "memory_limit_enforced": False,
        "process_tree_drained": None,
        "process_tree_drain_verification": "not_performed",
    }
    resource_raw = _canonical_bytes(resource) + b"\n"
    (run_root / verifier.RESOURCE_RECEIPT_NAME).write_bytes(resource_raw)
    preflight = {
        "git_commit": verifier.EXPECTED_SOURCE_COMMIT,
        "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
        "run_spec_sha256": verifier.EXPECTED_RUN_SPEC_SHA256,
        "producer_binding": {"root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256},
    }
    completion = {
        "schema_version": verifier.EXPECTED_COMPLETION_SCHEMA,
        "classification": "completed_result_pending_independent_verification",
        "result_available": True,
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "immutable_inputs_unchanged": True,
        "artifact_content_addressed": True,
        "runtime_verification_content_addressed": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "preflight": preflight,
        "post_run_preflight": preflight,
        "resource_receipt": {
            "path": verifier.RESOURCE_RECEIPT_NAME,
            "sha256": _sha256(resource_raw),
            "exit_code": 0,
            "memory_limit_enforced": False,
            "process_tree_drained": None,
        },
        "progress": {
            "schema_version": verifier.EXPECTED_PROGRESS_SCHEMA,
            "stage": "completed",
            "artifact_sha256": main_sha,
        },
        "result_artifact": {
            "path": main_path.name,
            "canonical_artifact_sha256": main_sha,
            "file_sha256": main_file_sha,
            "file_name_matches_content_sha256": True,
        },
        "runtime_verification": {
            "path": runtime_path.name,
            "canonical_artifact_sha256": runtime_sha,
            "file_sha256": runtime_file_sha,
        },
    }
    completion_raw = _canonical_bytes(completion) + b"\n"
    (run_root / verifier.COMPLETION_NAME).write_bytes(completion_raw)
    return {
        "run_root": run_root,
        "completion_sha256": _sha256(completion_raw),
        "main_artifact_sha256": main_sha,
        "runtime_verification_artifact_sha256": runtime_sha,
        "runtime_verification": _runtime_document(main_sha)
        | {"artifact_sha256": runtime_sha},
        "source": {
            "root": tmp_path,
            "git_commit": verifier.EXPECTED_SOURCE_COMMIT,
            "python_executable": tmp_path / "python.exe",
            "python_executable_sha256": "c" * 64,
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
        },
    }


def _replay_result(inputs: dict[str, object]) -> dict[str, object]:
    result = {
        "schema_version": verifier.REPLAY_RESULT_SCHEMA,
        "main_artifact_sha256": inputs["main_artifact_sha256"],
        "runtime_verification_artifact_sha256": inputs[
            "runtime_verification_artifact_sha256"
        ],
        "verification": inputs["runtime_verification"],
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    }
    return result


def test_replay_plan_is_frozen_and_development_only() -> None:
    verifier._assert_replay_plan()

    mutated = dict(verifier.REPLAY_PLAN)
    mutated["development_partition"] = {
        **verifier.REPLAY_PLAN["development_partition"],
        "temporal_role": "embargo",
    }
    original = verifier.REPLAY_PLAN
    try:
        verifier.REPLAY_PLAN = mutated
        with pytest.raises(verifier.IndependentVerificationError):
            verifier._assert_replay_plan()
    finally:
        verifier.REPLAY_PLAN = original


def test_verifier_identity_requires_clean_exact_committed_entrypoint(monkeypatch) -> None:
    expected_commit = "a" * 40

    def fake_git_output(root: Path, *arguments: str) -> str:
        assert root == verifier.PROJECT_ROOT
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(verifier.PROJECT_ROOT)
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return expected_commit
        raise AssertionError(arguments)

    class Completed:
        returncode = 0
        stdout = verifier._normalized_source_bytes(verifier.SCRIPT_PATH)

    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(verifier.subprocess, "run", lambda *_args, **_kwargs: Completed())

    identity = verifier._verifier_identity(expected_commit)

    assert identity["git_commit"] == expected_commit
    assert len(identity["script_sha256"]) == 64
    assert identity["script_git_blob_sha256"] == _sha256(Completed.stdout)


def test_source_identity_requires_expected_clean_source(monkeypatch, tmp_path: Path) -> None:
    python_executable = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_bytes(b"python")

    def fake_git_output(root: Path, *arguments: str) -> str:
        assert root == tmp_path
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return verifier.EXPECTED_SOURCE_COMMIT
        raise AssertionError(arguments)

    monkeypatch.setattr(verifier, "_git_output", fake_git_output)
    monkeypatch.setattr(
        verifier,
        "_source_producer_identity",
        lambda *_: {
            "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
            "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
        },
    )

    identity = verifier._source_identity(str(tmp_path))

    assert identity["root"] == tmp_path
    assert identity["git_commit"] == verifier.EXPECTED_SOURCE_COMMIT
    assert identity["python_executable_sha256"] == _sha256(b"python")


def test_source_producer_probe_isolated_to_frozen_source(monkeypatch, tmp_path: Path) -> None:
    python_executable = tmp_path / "python.exe"
    python_executable.write_bytes(b"python")
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = _canonical_bytes(
            {
                "strategy_sha256": verifier.EXPECTED_STRATEGY_SHA256,
                "producer_root_sha256": verifier.EXPECTED_PRODUCER_ROOT_SHA256,
            }
        )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return Completed()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    identity = verifier._source_producer_identity(tmp_path, python_executable)

    assert identity["strategy_sha256"] == verifier.EXPECTED_STRATEGY_SHA256
    assert captured["command"][:2] == [str(python_executable), "-I"]
    assert captured["cwd"] == tmp_path


def test_safe_child_rejects_reparse_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._safe_child(link, "result.json", "result")


def test_load_completed_run_accepts_only_pending_development_result(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)

    loaded = verifier._load_completed_run(inputs["source"])

    assert loaded["completion_sha256"] == inputs["completion_sha256"]
    assert loaded["main_artifact_sha256"] == inputs["main_artifact_sha256"]
    assert loaded["runtime_verification"] == inputs["runtime_verification"]


def test_load_completed_run_rejects_open_statistical_interpretation(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["statistical_interpretation_allowed"] = True
    completion_path.write_bytes(_canonical_bytes(completion) + b"\n")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._load_completed_run(inputs["source"])


def test_validate_replay_requires_exact_runtime_verification_match(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    replay = {"result": _replay_result(inputs)}

    verifier._validate_replay(inputs, replay)

    replay["result"] = {
        **replay["result"],
        "runtime_verification_artifact_sha256": "f" * 64,
    }
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._validate_replay(inputs, replay)


def test_preregistration_is_idempotent_but_rejects_drift(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    verifier_identity = {
        "git_commit": "d" * 40,
        "script_sha256": "e" * 64,
        "script_git_blob_sha256": "f" * 64,
    }

    first = verifier._preregistration(inputs, verifier_identity)
    second = verifier._preregistration(inputs, verifier_identity)

    assert first["sha256"] == second["sha256"]
    with pytest.raises(verifier.IndependentVerificationError):
        verifier._preregistration(
            inputs,
            {**verifier_identity, "script_sha256": "a" * 64},
        )


def test_publish_receipt_does_not_change_formal_completion(tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    completion_path = inputs["run_root"] / verifier.COMPLETION_NAME
    before = completion_path.read_bytes()
    replay_result_path = inputs["run_root"] / verifier.REPLAY_RESULT_NAME
    replay_result_path.write_bytes(_canonical_bytes(_replay_result(inputs)) + b"\n")
    replay = {
        "result_path": replay_result_path,
        "exit_code": 0,
        "stdout": {"path": "stdout", "bytes": 0, "sha256": "1" * 64},
        "stderr": {"path": "stderr", "bytes": 0, "sha256": "2" * 64},
    }
    preregistration = {"sha256": "3" * 64}
    verifier_identity = {
        "git_commit": "4" * 40,
        "script_sha256": "5" * 64,
        "script_git_blob_sha256": "6" * 64,
    }

    receipt = verifier._publish_receipt(
        inputs,
        verifier_identity,
        preregistration,
        replay,
    )

    assert completion_path.read_bytes() == before
    assert receipt["path"].is_file()
    payload = json.loads(receipt["path"].read_text(encoding="utf-8"))
    assert payload["scope"]["production_authority"] is False
    assert payload["resource_contract"]["memory_policy"] == "unbounded"


def test_run_records_failure_without_receipt(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    prepared = {
        "verifier": {
            "git_commit": "4" * 40,
            "script_sha256": "5" * 64,
            "script_git_blob_sha256": "6" * 64,
        },
        "inputs": inputs,
    }
    monkeypatch.setattr(verifier, "preflight", lambda *_: prepared)
    monkeypatch.setattr(
        verifier,
        "_run_isolated_replay",
        lambda *_: (_ for _ in ()).throw(verifier.IndependentVerificationError()),
    )

    with pytest.raises(verifier.IndependentVerificationError):
        verifier.run("ignored", "4" * 40)

    status_path = inputs["run_root"] / verifier.STATUS_NAME
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert not (inputs["run_root"] / verifier.CLAIM_NAME).exists()
    assert not list(
        (inputs["run_root"] / verifier.RECEIPT_ROOT_NAME).rglob("*.json")
    )


def test_run_publishes_terminal_receipt_after_exact_replay(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    prepared = {
        "verifier": {
            "git_commit": "4" * 40,
            "script_sha256": "5" * 64,
            "script_git_blob_sha256": "6" * 64,
        },
        "inputs": inputs,
    }
    replay_result_path = inputs["run_root"] / verifier.REPLAY_RESULT_NAME
    replay_result_path.write_bytes(_canonical_bytes(_replay_result(inputs)) + b"\n")
    replay = {
        "result": _replay_result(inputs),
        "result_path": replay_result_path,
        "exit_code": 0,
        "stdout": {"path": "stdout", "bytes": 0, "sha256": "1" * 64},
        "stderr": {"path": "stderr", "bytes": 0, "sha256": "2" * 64},
    }
    monkeypatch.setattr(verifier, "preflight", lambda *_: prepared)
    monkeypatch.setattr(verifier, "_run_isolated_replay", lambda *_: replay)

    result = verifier.run("ignored", "4" * 40)

    assert result["status"] == "completed"
    status = json.loads(
        (inputs["run_root"] / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["verified"] is True
    assert status["receipt"]["sha256"] == result["receipt_sha256"]
    assert not (inputs["run_root"] / verifier.CLAIM_NAME).exists()


def test_isolated_replay_uses_hidden_unbounded_child(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)
    result_path = inputs["run_root"] / verifier.REPLAY_RESULT_NAME
    captured: dict[str, object] = {}

    class Process:
        def wait(self) -> int:
            result_path.write_bytes(b"{}\n")
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["creationflags"] = kwargs["creationflags"]
        return Process()

    monkeypatch.setattr(verifier.subprocess, "Popen", fake_popen)

    replay = verifier._run_isolated_replay(inputs)

    assert replay["result"] == {}
    assert "-I" in captured["command"]
    assert captured["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_isolated_replay_fails_closed_on_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    inputs = _make_completed_inputs(tmp_path)

    class Process:
        def wait(self) -> int:
            return 1

    monkeypatch.setattr(verifier.subprocess, "Popen", lambda *_args, **_kwargs: Process())

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._run_isolated_replay(inputs)

    assert (inputs["run_root"] / verifier.REPLAY_STDOUT_NAME).is_file()
    assert (inputs["run_root"] / verifier.REPLAY_STDERR_NAME).is_file()
    assert not (inputs["run_root"] / verifier.REPLAY_RESULT_NAME).exists()


def test_main_never_prints_verification_details(monkeypatch, capsys) -> None:
    monkeypatch.setattr(verifier, "preflight", lambda *_: {"ignored": True})

    assert verifier.main(["--expected-verifier-commit", "a" * 40, "--preflight"]) == 0
    assert capsys.readouterr().out == "status=preflight_verified\n"

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_: {"status": "completed", "receipt_sha256": "b" * 64},
    )
    assert verifier.main(["--expected-verifier-commit", "a" * 40]) == 0
    assert capsys.readouterr().out == "status=independently_verified\n"

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_: (_ for _ in ()).throw(verifier.IndependentVerificationError()),
    )
    assert verifier.main(["--expected-verifier-commit", "a" * 40]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "status=failed\n"
