from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import run_shallow_gbdt_risk_on_breadth_development_1 as launcher


EXPECTED_ARGUMENTS = [
    "-m",
    "app.jobs",
    (
        "research-audited-pit-ranked-liquidity-"
        "shallow-gbdt-risk-on-breadth-rolling-oof"
    ),
    "--audited-pit-universe-path",
    (
        "data/research_artifacts/audited_pit_universe_v2/"
        "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4/"
        "metadata.sqlite3"
    ),
    "--expected-coverage-audit-sha256",
    "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed",
    "--expected-artifact-root-sha256",
    "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380",
    "--temporal-contract-path",
    "data/research_partitions/frozen-v2.json",
    "--expected-temporal-contract-sha256",
    "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934",
    "--security-code-transition-evidence-root",
    "data/research_artifacts/security_code_transition_evidence_v1",
    "--expected-security-code-transition-contract-sha256",
    "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c",
    "--start-date",
    "2024-07-05",
    "--end-date",
    "2026-07-03",
    "--output-dir",
    (
        "data/research_runs/"
        "audited_pit_ranked_liquidity_shallow_gbdt_risk_on_breadth_"
        "rolling126_oof_v1_development_1_unbounded_formal_local_research"
    ),
]


def test_formal_risk_on_breadth_run_spec_is_frozen_and_development_only() -> None:
    launcher._assert_frozen_run_spec()

    assert launcher.RUN_SPEC_SHA256 == (
        "5152b35fd399e15f036205c330c5a5765624faf784b2030c87669d7b04a71068"
    )
    assert launcher.EXPECTED_STRATEGY_SHA256 == (
        "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
    )
    assert launcher.EXPECTED_PRODUCER_ROOT_SHA256 == (
        "2c8530853a5d906dae2e6fa9895aa0a620d93a870d3bfd9dabada30444013f22"
    )
    assert launcher.RUN_SPEC["resource_contract"] == {
        "memory_policy": "unbounded",
        "enforcement": "none",
        "process_tree_completion": "windows_job_object_assigned_and_drained",
    }
    assert launcher.RUN_SPEC["attempt_contract"] == {
        "ledger_relative_path": (
            "data/research_attempts/"
            "risk_on_breadth_development_1.formal_attempt.json"
        ),
        "max_formal_attempts": 1,
        "ledger_outside_run_root": True,
    }
    assert launcher.RUN_SPEC["development_partition"] == {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }
    assert launcher.RUN_SPEC["scope"] == {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    }
    assert launcher.RUN_SPEC["inputs"]["current_pool_development_audit_path"] == (
        "data/research_artifacts/current_pool_audits/"
        "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55.json"
    )
    assert launcher.RUN_SPEC["inputs"][
        "expected_current_pool_development_audit_sha256"
    ] == "6de58a9b42ef6134219ea2b155afa43836cde24cc86bafeb2f71f3653830cf55"
    assert launcher.PROGRESS_FILE_NAME == (
        ".ranked_liquidity_shallow_gbdt_risk_on_breadth_v1_progress.json"
    )
    assert launcher.EXPECTED_PROGRESS_SCHEMA == (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-replay-progress/v1"
    )
    assert launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA == (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        "result-bundle-verification/v1"
    )


def test_formal_command_is_exactly_the_frozen_argument_vector() -> None:
    assert launcher._command_arguments() == EXPECTED_ARGUMENTS


def test_minimal_child_environment_does_not_inherit_secrets(tmp_path: Path) -> None:
    parent = {
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "PATH": r"C:\untrusted",
        "JIAOCH_TOKEN": "must-not-cross-process-boundary",
        "ANTHROPIC_AUTH_TOKEN": "must-not-cross-process-boundary",
        "UNRELATED_SETTING": "must-not-be-inherited",
    }

    environment = launcher._minimal_child_environment(
        parent,
        python_executable=Path(sys.executable),
        temp_dir=tmp_path,
    )

    assert environment["VPS_RUNTIME_ROLE"] == "local_research"
    assert environment["PYTHONHASHSEED"] == "0"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["TEMP"] == str(tmp_path.resolve())
    assert environment["TMP"] == str(tmp_path.resolve())
    assert "JIAOCH_TOKEN" not in environment
    assert "ANTHROPIC_AUTH_TOKEN" not in environment
    assert "UNRELATED_SETTING" not in environment
    assert r"C:\untrusted" not in environment["PATH"]


def test_frozen_input_attestation_binds_observed_bytes(tmp_path: Path) -> None:
    universe = tmp_path / "universe.sqlite3"
    temporal = tmp_path / "frozen.json"
    current_pool_audit = tmp_path / "current-pool-audit.json"
    transition = tmp_path / "transition"
    transition.mkdir()
    universe.write_bytes(b"universe-v1")
    temporal.write_bytes(b"temporal-v1")
    current_pool_audit.write_bytes(b"current-pool-v1")
    (transition / "receipt.json").write_bytes(b"transition-v1")
    inputs = {
        "audited_pit_universe_path": universe.name,
        "temporal_contract_path": temporal.name,
        "current_pool_development_audit_path": current_pool_audit.name,
        "security_code_transition_evidence_root": transition.name,
        "observed_attestation": launcher.RUN_SPEC["inputs"]["observed_attestation"],
    }

    before = launcher.frozen_input_attestation(tmp_path, inputs)
    universe.write_bytes(b"universe-v2")
    after = launcher.frozen_input_attestation(tmp_path, inputs)

    assert before["schema_version"] == "formal-frozen-input-attestation/v1"
    assert before["root_sha256"] != after["root_sha256"]
    assert {item["path"] for item in before["files"]} == {
        "transition/receipt.json",
        "frozen.json",
        "current-pool-audit.json",
        "universe.sqlite3",
    }
    assert all(set(item) == {"path", "bytes", "sha256"} for item in before["files"])


def test_frozen_input_attestation_rejects_unexpected_file_type(tmp_path: Path) -> None:
    universe = tmp_path / "universe.sqlite3"
    universe.mkdir()
    temporal = tmp_path / "frozen.json"
    temporal.write_bytes(b"temporal")
    current_pool_audit = tmp_path / "current-pool-audit.json"
    current_pool_audit.write_bytes(b"current-pool")
    transition = tmp_path / "transition"
    transition.mkdir()
    (transition / "receipt.json").write_bytes(b"transition")
    inputs = {
        "audited_pit_universe_path": universe.name,
        "temporal_contract_path": temporal.name,
        "current_pool_development_audit_path": current_pool_audit.name,
        "security_code_transition_evidence_root": transition.name,
        "observed_attestation": launcher.RUN_SPEC["inputs"]["observed_attestation"],
    }

    with pytest.raises(RuntimeError, match="frozen input type differs"):
        launcher.frozen_input_attestation(tmp_path, inputs)


def test_single_attempt_ledger_is_external_and_write_once(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs" / "formal-run"
    ledger_path = tmp_path / "attempts" / "attempt.json"
    claim = {
        "schema_version": "formal-single-attempt-claim/v1",
        "run_spec_sha256": launcher.RUN_SPEC_SHA256,
    }

    launcher._reserve_single_attempt(
        ledger_path=ledger_path,
        output_dir=output_dir,
        claim=claim,
    )

    assert ledger_path.is_file()
    assert not output_dir.exists()
    assert json.loads(ledger_path.read_text(encoding="utf-8")) == claim
    with pytest.raises(FileExistsError, match="already claimed"):
        launcher._reserve_single_attempt(
            ledger_path=ledger_path,
            output_dir=output_dir,
            claim=claim,
        )


@pytest.mark.skipif(os.name != "nt", reason="正式 launcher 只允许 Windows")
def test_unbounded_command_requires_assigned_and_drained_job_object(
    tmp_path: Path,
) -> None:
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=tmp_path,
    )

    receipt = launcher.run_unbounded_command(
        command=[sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        environment=environment,
    )

    assert receipt["exit_code"] == 0
    assert receipt["memory_limit_enforced"] is False
    assert receipt["child_reaped"] is True
    assert receipt["job_object_assigned"] is True
    assert receipt["process_tree_drained"] is True
    assert receipt["process_tree_drain_verification"] == (
        "windows_job_object_active_process_count_zero"
    )
    assert receipt["stderr"]["bytes"] == 0


@pytest.mark.skipif(os.name != "nt", reason="正式 launcher 只允许 Windows")
def test_job_assignment_failure_never_releases_the_actual_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "actual-command-started.txt"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=tmp_path,
    )

    def fail_assignment(
        _self: launcher._WindowsJob,
        _process: object,
    ) -> None:
        time.sleep(0.2)
        raise OSError("synthetic assignment failure")

    monkeypatch.setattr(launcher._WindowsJob, "assign", fail_assignment)

    with pytest.raises(OSError, match="synthetic assignment failure"):
        launcher.run_unbounded_command(
            command=[
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
            ],
            cwd=tmp_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            environment=environment,
        )

    assert not marker.exists()


def test_risk_on_breadth_producer_binding_rejects_valid_format_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = {
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "producer_binding": {"root_sha256": "0" * 64},
    }
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    with pytest.raises(RuntimeError, match="producer binding drifted"):
        launcher.risk_on_breadth_producer_binding(
            tmp_path / "python.exe",
            environment={"PYTHONHASHSEED": "0"},
        )


def test_formal_run_spec_rejects_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(launcher.RUN_SPEC["scope"], "development_only", False)

    with pytest.raises(RuntimeError, match="run spec drifted"):
        launcher._assert_frozen_run_spec()
