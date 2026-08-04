from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

import scripts.verify_shallow_gbdt_development_bundle as verifier


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _verification_result(*, verified: bool) -> dict[str, object]:
    checks = {key: True for key in verifier.REQUIRED_CHECKS}
    result: dict[str, object] = {
        "verified": verified,
        "schema_version": verifier.VERIFICATION_SCHEMA_VERSION,
        "strategy_sha256": "c" * 64,
        "producer_root_sha256": "b" * 64,
        "main_artifact_sha256": "a" * 64,
        "sidecar_artifact_sha256": {"selection": "d" * 64},
        "checks": checks,
    }
    result["receipt_sha256"] = _sha256_bytes(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return result


def _make_run_root(tmp_path: Path, *, verified: bool = True) -> tuple[Path, bytes]:
    run_root = tmp_path / (
        "audited_pit_ranked_liquidity_shallow_gbdt_rolling126_oof_v1_"
        "development_test"
    )
    run_root.mkdir()
    artifact_sha = "a" * 64
    artifact_path = run_root / f"{artifact_sha}.json"
    artifact_raw = b"{}\n"
    artifact_path.write_bytes(artifact_raw)
    replay_result = _verification_result(verified=verified)
    replay_path = run_root / verifier.REPLAY_NAME
    replay_path.write_text(
        "import json\n"
        "def main():\n"
        f"    print('INDEPENDENT_RESULT ' + json.dumps({replay_result!r}, sort_keys=True))\n"
        "    return 0\n",
        encoding="utf-8",
    )
    helper_path = run_root / verifier.HELPER_NAME
    helper_path.write_text(
        "import json\n"
        f"print('INDEPENDENT_RESULT ' + json.dumps({replay_result!r}, sort_keys=True))\n",
        encoding="utf-8",
    )
    completion = {
        "schema_version": "ranked-liquidity-shallow-gbdt-unbounded-completion/v1",
        "classification": "completed_result_pending_independent_verification",
        "result_available": True,
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "launcher_exception": None,
        "artifact_content_addressed": True,
        "immutable_inputs_unchanged": True,
        "post_run_tracked_worktree_clean": True,
        "producer_root_sha256": "b" * 64,
        "post_run_producer_root_sha256": "b" * 64,
        "strategy_sha256": "c" * 64,
        "post_run_strategy_sha256": "c" * 64,
        "git_commit": "e" * 40,
        "post_run_git_commit": "e" * 40,
        "formal_launcher_sha256": "f" * 64,
        "post_run_formal_launcher_sha256": "f" * 64,
        "formal_launcher_git_blob_sha256": "0" * 64,
        "post_run_formal_launcher_git_blob_sha256": "0" * 64,
        "progress": {
            "schema_version": "ranked-liquidity-shallow-gbdt-replay-progress/v1",
            "stage": "completed",
            "artifact_sha256": artifact_sha,
            "advancement_gate_passed": False,
        },
        "result_artifact": {
            "path": artifact_path.name,
            "canonical_artifact_sha256": artifact_sha,
            "file_sha256": _sha256_bytes(artifact_raw),
            "file_name_matches_content_sha256": True,
            "progress_artifact_sha256_matches": True,
        },
    }
    completion_path = run_root / verifier.COMPLETION_NAME
    completion_raw = json.dumps(completion, sort_keys=True).encode("utf-8")
    completion_path.write_bytes(completion_raw)
    preregistration = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-independent-verification-preregistration/v1"
        ),
        "run_root": run_root.name,
        "completion_file_sha256": _sha256_bytes(completion_raw),
        "replay_script_sha256": _sha256_bytes(replay_path.read_bytes()),
        "helper_script_sha256": _sha256_bytes(helper_path.read_bytes()),
        "verifier_entrypoint_sha256": verifier._sha256_file(verifier.SCRIPT_PATH),
        "strategy_sha256": "c" * 64,
        "producer_root_sha256": "b" * 64,
        "source_git_commit": "e" * 40,
        "verification_checkout_git_commit": "e" * 40,
        "python_executable_sha256": verifier._python_executable_sha256(),
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    (run_root / verifier.PREREGISTRATION_NAME).write_bytes(
        json.dumps(preregistration, sort_keys=True).encode("utf-8")
    )
    return run_root, completion_raw


def _patch_registered_run(
    monkeypatch: pytest.MonkeyPatch, run_root: Path, completion_raw: bytes
) -> None:
    monkeypatch.setattr(verifier, "RUNS_ROOT", run_root.parent)
    monkeypatch.setattr(verifier, "REGISTERED_RUN_ROOT_NAME", run_root.name)
    monkeypatch.setattr(
        verifier,
        "REGISTERED_COMPLETION_SHA256",
        _sha256_bytes(completion_raw),
    )
    monkeypatch.setattr(
        verifier,
        "REGISTERED_REPLAY_SHA256",
        verifier._sha256_file(run_root / verifier.REPLAY_NAME),
    )
    monkeypatch.setattr(
        verifier,
        "REGISTERED_HELPER_SHA256",
        verifier._sha256_file(run_root / verifier.HELPER_NAME),
    )
    monkeypatch.setattr(verifier, "REGISTERED_ARTIFACT_SHA256", "a" * 64)
    monkeypatch.setattr(verifier, "REGISTERED_STRATEGY_SHA256", "c" * 64)
    monkeypatch.setattr(verifier, "REGISTERED_PRODUCER_ROOT_SHA256", "b" * 64)
    monkeypatch.setattr(verifier, "REGISTERED_SOURCE_COMMIT", "e" * 40)
    monkeypatch.setattr(
        verifier,
        "REGISTERED_PYTHON_SHA256",
        verifier._python_executable_sha256(),
    )


def test_preflight_is_development_only(monkeypatch, tmp_path: Path) -> None:
    run_root, completion_raw = _make_run_root(tmp_path)
    _patch_registered_run(monkeypatch, run_root, completion_raw)
    monkeypatch.setattr(verifier, "_git_clean", lambda *_: True)
    monkeypatch.setattr(verifier, "_git_head", lambda *_: "e" * 40)
    monkeypatch.setattr(
        verifier, "_current_producer_root_sha256", lambda *_: "b" * 64
    )
    result = verifier.preflight(run_root)

    assert result["status"] == "preflight_ok"
    assert result["embargo_consumed"] is False
    assert result["final_oos_consumed"] is False
    assert result["production_authority"] is False


def test_preflight_rejects_dirty_worktree(monkeypatch, tmp_path: Path) -> None:
    run_root, completion_raw = _make_run_root(tmp_path)
    _patch_registered_run(monkeypatch, run_root, completion_raw)
    monkeypatch.setattr(verifier, "_git_clean", lambda *_: False)
    monkeypatch.setattr(verifier, "_git_head", lambda *_: "e" * 40)

    with pytest.raises(verifier.IndependentVerificationError):
        verifier.preflight(run_root)


def test_cli_run_root_rejects_non_development_partition() -> None:
    production_root = verifier.RUNS_ROOT / f"{verifier.RUN_NAME_PREFIX}production"

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._run_root(str(production_root))


def test_direct_run_rejects_unregistered_root(tmp_path: Path) -> None:
    run_root, _ = _make_run_root(tmp_path)

    with pytest.raises(verifier.IndependentVerificationError):
        verifier.run(run_root)

    assert not (run_root / verifier.STATUS_NAME).exists()
    assert not (run_root / verifier.CLAIM_NAME).exists()


def test_replay_input_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("pass\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(verifier.IndependentVerificationError):
        verifier._assert_no_reparse(link, "replay")


def test_replay_runs_from_frozen_source_root(monkeypatch, tmp_path: Path) -> None:
    helper_path = tmp_path / "helper.py"
    source_root = tmp_path / "frozen-source"
    source_root.mkdir()
    captured: dict[str, object] = {}

    class Process:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"INDEPENDENT_FULL_RESULT {}\n")

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

        def kill(self) -> None:
            raise AssertionError("successful replay must not be killed")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return Process()

    monkeypatch.setattr(verifier.subprocess, "Popen", fake_popen)

    assert verifier._replay_output(
        {
            "python_executable_sha256": verifier._python_executable_sha256(),
            "helper_path": helper_path,
            "source_root": source_root,
        }
    ) == ["INDEPENDENT_FULL_RESULT {}"]
    assert captured["cwd"] == source_root
    assert captured["command"][-2:] == [str(helper_path), str(source_root)]


def test_replay_keeps_frozen_app_after_helper_adds_its_repo_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "frozen-source"
    source_app = source_root / "app"
    source_app.mkdir(parents=True)
    (source_app / "__init__.py").write_text("", encoding="utf-8")
    (source_app / "audited_pit_continuous_ridge_oof.py").write_text(
        "def verify_shallow_gbdt_result_bundle():\n"
        "    return {'origin': 'frozen-source'}\n",
        encoding="utf-8",
    )
    main_root = tmp_path / "mutable-main"
    main_app = main_root / "app"
    main_app.mkdir(parents=True)
    (main_app / "__init__.py").write_text("", encoding="utf-8")
    (main_app / "audited_pit_continuous_ridge_oof.py").write_text(
        "def verify_shallow_gbdt_result_bundle():\n"
        "    return {'origin': 'mutable-main'}\n",
        encoding="utf-8",
    )
    replay_root = main_root / "data" / "research_runs" / "probe"
    replay_root.mkdir(parents=True)
    replay_path = replay_root / "replay.py"
    replay_path.write_text(
        "from app import audited_pit_continuous_ridge_oof as ridge\n"
        "ridge.verify_shallow_gbdt_result_bundle()\n",
        encoding="utf-8",
    )
    helper_path = replay_root / "helper.py"
    helper_path.write_text(
        "import runpy\n"
        "import sys\n"
        "from pathlib import Path\n"
        "repo_root = Path(__file__).resolve().parents[3]\n"
        "if str(repo_root) not in sys.path:\n"
        "    sys.path.insert(0, str(repo_root))\n"
        "runpy.run_path(str(Path(__file__).with_name('replay.py')))\n",
        encoding="utf-8",
    )

    lines = verifier._replay_output(
        {
            "python_executable_sha256": verifier._python_executable_sha256(),
            "helper_path": helper_path,
            "source_root": source_root,
        }
    )

    result = json.loads(lines[0].removeprefix("INDEPENDENT_FULL_RESULT "))
    assert result == {"origin": "frozen-source"}


def test_run_publishes_content_addressed_receipt_without_mutating_completion(
    monkeypatch, tmp_path: Path
) -> None:
    run_root, completion_raw = _make_run_root(tmp_path)
    _patch_registered_run(monkeypatch, run_root, completion_raw)
    monkeypatch.setattr(verifier, "_git_clean", lambda *_: True)
    monkeypatch.setattr(verifier, "_git_head", lambda *_: "e" * 40)
    monkeypatch.setattr(
        verifier, "_current_producer_root_sha256", lambda *_: "b" * 64
    )
    monkeypatch.setattr(
        verifier,
        "_replay_output",
        lambda _: [
            "INDEPENDENT_FULL_RESULT "
            + json.dumps(_verification_result(verified=True), sort_keys=True)
        ],
    )

    result = verifier.run(run_root)

    assert result["status"] == "completed"
    assert len(result["receipt_sha256"]) == 64
    assert (run_root / verifier.COMPLETION_NAME).read_bytes() == completion_raw
    status = json.loads(
        (run_root / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["verified"] is True
    receipt_path = run_root / status["receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_sha256"] == result["receipt_sha256"]
    assert receipt["verification"]["sidecar_artifact_sha256"] == {
        "selection": "d" * 64
    }
    assert receipt["scope"]["production_authority"] is False
    assert len(status["claim_sha256"]) == 64
    assert not (run_root / verifier.CLAIM_NAME).exists()


def test_run_records_failure_without_publishing_receipt(monkeypatch, tmp_path: Path) -> None:
    run_root, completion_raw = _make_run_root(tmp_path, verified=False)
    _patch_registered_run(monkeypatch, run_root, completion_raw)
    monkeypatch.setattr(verifier, "_git_clean", lambda *_: True)
    monkeypatch.setattr(verifier, "_git_head", lambda *_: "e" * 40)
    monkeypatch.setattr(
        verifier, "_current_producer_root_sha256", lambda *_: "b" * 64
    )
    monkeypatch.setattr(
        verifier,
        "_replay_output",
        lambda _: [
            "INDEPENDENT_FULL_RESULT "
            + json.dumps(_verification_result(verified=False), sort_keys=True)
        ],
    )

    with pytest.raises(verifier.IndependentVerificationError):
        verifier.run(run_root)

    status = json.loads(
        (run_root / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["verified"] is False
    assert not list((run_root / verifier.RECEIPT_ROOT_NAME).rglob("*.json"))
    assert not (run_root / verifier.CLAIM_NAME).exists()


def test_run_records_base_exception(monkeypatch, tmp_path: Path) -> None:
    run_root, completion_raw = _make_run_root(tmp_path)
    _patch_registered_run(monkeypatch, run_root, completion_raw)
    monkeypatch.setattr(verifier, "_git_clean", lambda *_: True)
    monkeypatch.setattr(verifier, "_git_head", lambda *_: "e" * 40)
    monkeypatch.setattr(
        verifier, "_current_producer_root_sha256", lambda *_: "b" * 64
    )

    def interrupted(_: object) -> list[str]:
        raise KeyboardInterrupt

    monkeypatch.setattr(verifier, "_replay_output", interrupted)

    with pytest.raises(KeyboardInterrupt):
        verifier.run(run_root)

    status = json.loads(
        (run_root / verifier.STATUS_NAME).read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["error_type"] == "KeyboardInterrupt"
    assert not (run_root / verifier.CLAIM_NAME).exists()
