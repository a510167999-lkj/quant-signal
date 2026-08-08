from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import py_compile
import subprocess
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


def _write_content_addressed_json(directory: Path, payload: dict) -> tuple[Path, str]:
    digest = launcher._sha256(payload)
    path = directory / f"{digest}.json"
    path.write_text(
        json.dumps({**payload, "artifact_sha256": digest}, sort_keys=True),
        encoding="utf-8",
    )
    return path, digest


def test_formal_risk_on_breadth_run_spec_is_frozen_and_development_only() -> None:
    launcher._assert_frozen_run_spec()

    assert launcher.RUN_SPEC_SHA256 == (
        "6bc468419bacc391db171cd79a5b5e5c1f4e13973a0d708cd8efcd12ac1cc6e7"
    )
    assert launcher.EXPECTED_STRATEGY_SHA256 == (
        "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
    )
    assert launcher.EXPECTED_PRODUCER_ROOT_SHA256 == (
        "bb833d0bc91720b3bf46b204ffb4d8615a9ec4530b7734d48ee3663bcd1d753f"
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
        "terminal_relative_path": (
            "data/research_attempts/"
            "risk_on_breadth_development_1.formal_attempt.terminal.json"
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
    assert launcher.RUN_SPEC["runtime_contract"]["critical_distributions"] == [
        "numpy",
        "pandas",
        "pypdf",
        "xgboost",
    ]
    assert launcher.RUN_SPEC["runtime_contract"]["pycache_policy"] == {
        "environment_key": "PYTHONPYCACHEPREFIX",
        "relative_to_runtime_temp": "pycache",
        "must_not_preexist": True,
    }
    assert launcher.RUN_SPEC["runtime_contract"]["bootstrap_contract"] == {
        "interpreter_flags": ["-I", "-S", "-B"],
        "site_import_before_job_assignment": False,
    }
    assert launcher.RUN_SPEC["runtime_contract"]["top_level_interpreter_flags"] == [
        "-I",
        "-S",
        "-B",
    ]
    assert launcher.RUN_SPEC["runtime_contract"]["isolated_probe_contract"] == {
        "interpreter_flags": ["-I", "-S", "-B"],
        "sys_path": ["workspace", "venv-site-packages"],
        "pycache_prefix_from_command_line": True,
    }
    assert launcher.RUN_SPEC["runtime_contract"]["formal_child_contract"] == {
        "interpreter_flags": ["-I", "-S", "-B"],
        "entrypoint": "runpy.run_module-app.jobs",
        "jobs_argument_prefix": ["-m", "app.jobs"],
        "sys_path": ["workspace", "venv-site-packages"],
        "pycache_prefix_from_command_line": True,
    }
    assert launcher.RUN_SPEC["runtime_contract"]["launcher_entrypoint"] == (
        "direct-source-file"
    )
    assert launcher.RUN_SPEC["inputs"]["transitive_binding"] == {
        "schema_version": "formal-transitive-input-binding/v1",
        "current_pool_audit": {
            "authority_path_field": "temporal_contract_path",
            "path_field": "current_pool_development_audit_path",
            "sha256_field": "expected_current_pool_development_audit_sha256",
        },
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


def test_formal_research_child_uses_isolated_runpy_app_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "jobs.py").write_text(
        "import json\n"
        "import sys\n"
        "print(json.dumps({\n"
        "    'argv': sys.argv[1:],\n"
        "    'hash_probe': hash('formal-hash-seed-probe'),\n"
        "    'site_loaded': 'site' in sys.modules,\n"
        "    'isolated': sys.flags.isolated,\n"
        "    'ignore_environment': sys.flags.ignore_environment,\n"
        "    'no_site': sys.flags.no_site,\n"
        "    'safe_path': sys.flags.safe_path,\n"
        "    'dont_write_bytecode': sys.flags.dont_write_bytecode,\n"
        "}, sort_keys=True))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "WORKSPACE", tmp_path)
    runtime_temp = tmp_path / "runtime-temp"
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=runtime_temp,
    )
    command = launcher._formal_research_command(
        Path(sys.executable),
        environment=environment,
    )

    assert command[:7] == [
        sys.executable,
        "-S",
        "-B",
        "-P",
        "-X",
        f"pycache_prefix={environment['PYTHONPYCACHEPREFIX']}",
        "-c",
    ]
    assert "runpy.run_module" in command[7]
    assert command[-len(EXPECTED_ARGUMENTS) :] == EXPECTED_ARGUMENTS

    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(completed.stdout)
    repeated = json.loads(
        subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert observed["argv"] == EXPECTED_ARGUMENTS[2:]
    assert observed["site_loaded"] is False
    assert observed["isolated"] == 0
    assert observed["ignore_environment"] == 0
    assert observed["no_site"] == 1
    assert observed["safe_path"] is True
    assert observed["dont_write_bytecode"] == 1
    assert observed["hash_probe"] == repeated["hash_probe"]


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
    assert environment["DISABLE_ENV_FILE"] == "1"
    assert environment["PYTHONPYCACHEPREFIX"] == str(
        (tmp_path / "pycache").resolve()
    )
    assert environment["TEMP"] == str(tmp_path.resolve())
    assert environment["TMP"] == str(tmp_path.resolve())
    assert "JIAOCH_TOKEN" not in environment
    assert "ANTHROPIC_AUTH_TOKEN" not in environment
    assert "UNRELATED_SETTING" not in environment
    assert r"C:\untrusted" not in environment["PATH"]


def test_temporal_contract_explicitly_binds_the_attested_current_pool_audit(
    tmp_path: Path,
) -> None:
    temporal = tmp_path / "frozen.json"
    audit = tmp_path / "audit.json"
    audit.write_bytes(b"audit")
    audit_sha256 = "a" * 64
    temporal.write_text(
        json.dumps(
            {
                "development_evidence": {
                    "current_pool_coverage_audit": {
                        "path": audit.name,
                        "canonical_sha256": audit_sha256,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    inputs = {
        "temporal_contract_path": temporal.name,
        "current_pool_development_audit_path": audit.name,
        "expected_current_pool_development_audit_sha256": audit_sha256,
        "transitive_binding": launcher.RUN_SPEC["inputs"]["transitive_binding"],
    }

    launcher._assert_transitive_input_binding(tmp_path, inputs)
    temporal.write_text(
        json.dumps(
            {
                "development_evidence": {
                    "current_pool_coverage_audit": {
                        "path": "different.json",
                        "canonical_sha256": audit_sha256,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="transitive input binding differs"):
        launcher._assert_transitive_input_binding(tmp_path, inputs)


def test_invalid_current_pool_audit_fails_before_attempt_claim(
    tmp_path: Path,
) -> None:
    audit = tmp_path / "invalid-audit.json"
    audit.write_text('{"not":"an authority audit"}', encoding="utf-8")
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=tmp_path / "runtime-temp",
    )

    with pytest.raises(RuntimeError, match="current-pool audit"):
        launcher.current_pool_audit_binding(
            Path(sys.executable),
            environment=environment,
            audit_path=audit,
            expected_canonical_sha256="a" * 64,
        )


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


@pytest.mark.skipif(os.name != "nt", reason="正式 launcher 只允许 Windows")
def test_held_frozen_inputs_deny_midflight_write(tmp_path: Path) -> None:
    universe = tmp_path / "universe.sqlite3"
    temporal = tmp_path / "frozen.json"
    current_pool_audit = tmp_path / "current-pool-audit.json"
    transition = tmp_path / "transition"
    transition.mkdir()
    universe.write_bytes(b"universe")
    temporal.write_bytes(b"temporal")
    current_pool_audit.write_bytes(b"current-pool")
    (transition / "receipt.json").write_bytes(b"transition")
    inputs = {
        "audited_pit_universe_path": universe.name,
        "temporal_contract_path": temporal.name,
        "current_pool_development_audit_path": current_pool_audit.name,
        "security_code_transition_evidence_root": transition.name,
        "observed_attestation": launcher.RUN_SPEC["inputs"]["observed_attestation"],
    }

    with launcher._held_frozen_inputs(tmp_path, inputs):
        assert universe.read_bytes() == b"universe"
        with pytest.raises(PermissionError):
            universe.write_bytes(b"tampered")

    universe.write_bytes(b"after-release")
    assert universe.read_bytes() == b"after-release"


def test_result_artifact_accepts_the_real_nested_strategy_binding(tmp_path: Path) -> None:
    payload = {
        "schema_version": launcher.EXPECTED_RESULT_SCHEMA,
        "strategy": {
            "schema_version": "synthetic-frozen-strategy/v1",
            "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        },
        "producer_code": {
            "schema_version": "synthetic-producer/v1",
            "root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256,
        },
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
    }
    _path, digest = _write_content_addressed_json(tmp_path, payload)

    artifact, verified = launcher._result_artifact(tmp_path)

    assert verified is True
    assert artifact is not None
    assert artifact["canonical_artifact_sha256"] == digest


def test_runtime_verification_requires_the_risk_breadth_replay_receipts(
    tmp_path: Path,
) -> None:
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    payload = {
        "schema_version": launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256,
        "main_artifact_sha256": "a" * 64,
        "sidecar_artifact_sha256": {
            "features": "b" * 64,
            "models": "c" * 64,
            "execution": "d" * 64,
            "selection": "e" * 64,
        },
        "checks": {
            "independent_rolling_oof_replay": True,
            "content_addressing_verified": True,
            "probability_score_contract_verified": True,
            "shared_positive_candidate_pool_verified": True,
            "strict_outcome_membership_verified": True,
            "independent_selection_replay": True,
            "independent_sweep_and_gate_replay": True,
            "market_breadth_filter_replayed": True,
        },
        "market_breadth_feature_binding_receipt_sha256": "f" * 64,
        "verified": True,
    }
    payload["receipt_sha256"] = launcher._sha256(payload)
    _write_content_addressed_json(verification_dir, payload)

    verification, verified = launcher._runtime_verification(tmp_path)

    assert verified is True
    assert verification is not None
    assert verification["main_artifact_sha256"] == "a" * 64


def test_completed_result_bundle_rejects_missing_sidecar_files(tmp_path: Path) -> None:
    sidecar_sha256 = {
        "features": "b" * 64,
        "models": "c" * 64,
        "execution": "d" * 64,
        "selection": "e" * 64,
    }
    main_payload = {
        "schema_version": launcher.EXPECTED_RESULT_SCHEMA,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "strategy": {"strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256},
        "source": {"schema_version": "synthetic-source/v1"},
        "producer_code": {"root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256},
        "sidecars": {
            name: {
                "artifact_sha256": digest,
                "relative_path": f"sidecars/{digest}.json",
            }
            for name, digest in sidecar_sha256.items()
        },
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
    }
    _main_path, main_sha256 = _write_content_addressed_json(tmp_path, main_payload)
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    verification_payload = {
        "schema_version": launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256,
        "main_artifact_sha256": main_sha256,
        "sidecar_artifact_sha256": sidecar_sha256,
        "checks": {
            "independent_rolling_oof_replay": True,
            "content_addressing_verified": True,
            "probability_score_contract_verified": True,
            "shared_positive_candidate_pool_verified": True,
            "strict_outcome_membership_verified": True,
            "independent_selection_replay": True,
            "independent_sweep_and_gate_replay": True,
            "market_breadth_filter_replayed": True,
        },
        "market_breadth_feature_binding_receipt_sha256": "f" * 64,
        "verified": True,
    }
    verification_payload["receipt_sha256"] = launcher._sha256(
        verification_payload
    )
    _write_content_addressed_json(verification_dir, verification_payload)

    main, verification, verified = launcher._result_bundle(tmp_path)

    assert main is None
    assert verification is None
    assert verified is False


def test_completed_result_bundle_accepts_exact_real_sidecar_set(tmp_path: Path) -> None:
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir()
    source = {"schema_version": "synthetic-source/v1"}
    producer_code = {"root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256}
    sidecar_sha256: dict[str, str] = {}
    for name in ("features", "models", "execution", "selection"):
        _path, digest = _write_content_addressed_json(
            sidecar_dir,
            {
                "schema_version": (
                    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
                    f"{name}-sidecar/v1"
                ),
                "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
                "source": source,
                "producer_code": producer_code,
            },
        )
        sidecar_sha256[name] = digest
    main_payload = {
        "schema_version": launcher.EXPECTED_RESULT_SCHEMA,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "strategy": {"strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256},
        "source": source,
        "producer_code": producer_code,
        "sidecars": {
            name: {
                "artifact_sha256": digest,
                "relative_path": f"sidecars/{digest}.json",
            }
            for name, digest in sidecar_sha256.items()
        },
        "scope": {
            "development_only": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
    }
    _main_path, main_sha256 = _write_content_addressed_json(tmp_path, main_payload)
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    verification_payload = {
        "schema_version": launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256,
        "main_artifact_sha256": main_sha256,
        "sidecar_artifact_sha256": sidecar_sha256,
        "checks": {
            "independent_rolling_oof_replay": True,
            "content_addressing_verified": True,
            "probability_score_contract_verified": True,
            "shared_positive_candidate_pool_verified": True,
            "strict_outcome_membership_verified": True,
            "independent_selection_replay": True,
            "independent_sweep_and_gate_replay": True,
            "market_breadth_filter_replayed": True,
        },
        "market_breadth_feature_binding_receipt_sha256": "f" * 64,
        "verified": True,
    }
    verification_payload["receipt_sha256"] = launcher._sha256(
        verification_payload
    )
    _write_content_addressed_json(verification_dir, verification_payload)

    main, verification, verified = launcher._result_bundle(tmp_path)

    assert verified is True
    assert main is not None
    assert main["sidecar_artifact_sha256"] == sidecar_sha256
    assert verification is not None
    assert verification["main_artifact_sha256"] == main_sha256


def test_runtime_attestation_binds_minimal_environment_and_distributions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = {
        "schema_version": "formal-python-runtime-attestation/v1",
        "implementation": "CPython",
        "python_version": "3.11.5",
        "cache_tag": "cpython-311",
        "site_loaded": False,
        "platform": "synthetic-win32",
        "executable": str(tmp_path / "python.exe"),
        "executable_sha256": "a" * 64,
        "base_executable": str(tmp_path / "base-python.exe"),
        "base_executable_sha256": "b" * 64,
        "distributions": [
            {
                "name": name,
                "version": "1.0",
                "file_count": 1,
                "files_sha256": str(index) * 64,
            }
            for index, name in enumerate(
                launcher.RUN_SPEC["runtime_contract"]["critical_distributions"],
                start=1,
            )
        ],
    }
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )
    environment = {
        "PYTHONHASHSEED": "0",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "fresh-pycache"),
        "VPS_RUNTIME_ROLE": "local_research",
    }

    attestation = launcher._runtime_attestation(
        tmp_path / "python.exe",
        environment=environment,
    )

    assert attestation["environment"] == {
        "schema_version": "minimal-research-environment/v1",
        "keys": ["PYTHONHASHSEED", "PYTHONPYCACHEPREFIX", "VPS_RUNTIME_ROLE"],
        "environment_sha256": launcher._sha256(environment),
        "inherit_parent_environment": False,
    }
    unsigned = dict(attestation)
    embedded = unsigned.pop("root_sha256")
    assert embedded == launcher._sha256(unsigned)


def test_preflight_binds_source_input_runtime_and_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    input_attestation = {
        "schema_version": "formal-frozen-input-attestation/v1",
        "root_sha256": "2" * 64,
    }
    runtime_attestation = {
        "schema_version": "formal-python-runtime-attestation/v1",
        "root_sha256": "3" * 64,
    }

    def fake_git_output(*args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return expected_commit
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(launcher, "git_output", fake_git_output)
    monkeypatch.setattr(
        launcher,
        "git_bytes",
        lambda *_args: launcher.normalized_source_bytes(launcher.SCRIPT_PATH),
    )
    monkeypatch.setattr(
        launcher,
        "risk_on_breadth_producer_binding",
        lambda *_args, **_kwargs: {
            "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
            "producer_binding": {
                "root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256
            },
        },
    )
    monkeypatch.setattr(
        launcher,
        "frozen_input_attestation",
        lambda *_args, **_kwargs: input_attestation,
    )
    monkeypatch.setattr(
        launcher,
        "_runtime_attestation",
        lambda *_args, **_kwargs: runtime_attestation,
    )

    preflight = launcher._preflight(
        expected_commit=expected_commit,
        python_executable=Path(sys.executable),
        environment={"PYTHONHASHSEED": "0"},
    )

    assert preflight["git_commit"] == expected_commit
    assert preflight["frozen_input_attestation"] == input_attestation
    assert preflight["runtime_attestation"] == runtime_attestation
    assert preflight["producer_binding"]["root_sha256"] == (
        launcher.EXPECTED_PRODUCER_ROOT_SHA256
    )


def _sandbox_launcher_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, dict]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script_path = scripts_dir / launcher.LAUNCHER_GIT_PATH.rsplit("/", 1)[-1]
    script_path.write_text("synthetic launcher", encoding="utf-8")
    (tmp_path / "data" / "research_runs").mkdir(parents=True)
    relative_output = Path("data/research_runs/formal-risk-run")
    preflight = {
        "git_commit": "1" * 40,
        "strategy_sha256": launcher.EXPECTED_STRATEGY_SHA256,
        "producer_binding": {
            "root_sha256": launcher.EXPECTED_PRODUCER_ROOT_SHA256
        },
        "run_spec_sha256": launcher.RUN_SPEC_SHA256,
        "frozen_input_attestation": {"root_sha256": "2" * 64},
        "runtime_attestation": {"root_sha256": "3" * 64},
    }
    monkeypatch.setattr(launcher, "WORKSPACE", tmp_path)
    monkeypatch.setattr(launcher, "SCRIPT_PATH", script_path)
    monkeypatch.setattr(launcher, "RELATIVE_OUTPUT_DIR", relative_output)
    monkeypatch.setattr(launcher, "__package__", "")
    monkeypatch.setattr(
        launcher,
        "_assert_top_level_interpreter",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(launcher, "_preflight", lambda **_kwargs: dict(preflight))
    monkeypatch.setattr(
        launcher,
        "_held_frozen_inputs",
        lambda *_args, **_kwargs: nullcontext(),
    )
    return relative_output, preflight


def test_main_dry_run_does_not_claim_the_formal_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _relative_output, _preflight = _sandbox_launcher_main(monkeypatch, tmp_path)
    original_cwd = Path.cwd()
    try:
        result = launcher.main(["--expected-commit", "1" * 40, "--dry-run"])
    finally:
        os.chdir(original_cwd)

    assert result == 0
    assert not (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["ledger_relative_path"]
    ).exists()


def test_main_success_stays_pending_independent_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative_output, _preflight = _sandbox_launcher_main(monkeypatch, tmp_path)
    artifact_sha256 = "a" * 64
    monkeypatch.setattr(
        launcher,
        "_safe_progress",
        lambda _path: {
            "schema_version": launcher.EXPECTED_PROGRESS_SCHEMA,
            "stage": "completed",
            "artifact_sha256": artifact_sha256,
        },
    )
    monkeypatch.setattr(
        launcher,
        "_result_artifact",
        lambda _path: (
            {"canonical_artifact_sha256": artifact_sha256},
            True,
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_runtime_verification",
        lambda _path: (
            {"main_artifact_sha256": artifact_sha256},
            True,
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_result_bundle",
        lambda _path: (
            {"canonical_artifact_sha256": artifact_sha256},
            {"main_artifact_sha256": artifact_sha256},
            True,
        ),
    )

    def fake_run_unbounded_command(**kwargs: object) -> dict:
        command = kwargs["command"]
        Path(kwargs["stdout_path"]).write_bytes(b"")
        Path(kwargs["stderr_path"]).write_bytes(b"")
        return {
            "schema_version": "research-unbounded-job-object-command-receipt/v1",
            "exit_code": 0,
            "child_reaped": True,
            "job_object_assigned": True,
            "process_tree_drained": True,
            "memory_limit_enforced": False,
            "command_sha256": launcher._sha256({"tokens": command}),
        }

    monkeypatch.setattr(
        launcher,
        "run_unbounded_command",
        fake_run_unbounded_command,
    )
    original_cwd = Path.cwd()
    try:
        result = launcher.main(["--expected-commit", "1" * 40])
    finally:
        os.chdir(original_cwd)

    completion_path = tmp_path / relative_output / "formal_run.completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    assert result == 0
    assert completion["result_available"] is True
    assert completion["independent_verification_complete"] is False
    assert completion["statistical_interpretation_allowed"] is False
    assert completion["profile_registration_authority"] is False
    assert completion["production_recommendation_authority"] is False
    assert completion["automatic_trading_authority"] is False
    attempt_terminal = json.loads(
        (
            tmp_path
            / launcher.RUN_SPEC["attempt_contract"]["terminal_relative_path"]
        ).read_text(encoding="utf-8")
    )
    assert attempt_terminal["status"] == "completed"
    assert attempt_terminal["completion_sha256"] == launcher.sha256_file(
        completion_path
    )


def test_main_failure_records_only_stable_error_type_and_no_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative_output, _preflight = _sandbox_launcher_main(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_safe_progress", lambda _path: None)
    monkeypatch.setattr(launcher, "_result_artifact", lambda _path: (None, False))
    monkeypatch.setattr(
        launcher,
        "_runtime_verification",
        lambda _path: (None, False),
    )
    monkeypatch.setattr(
        launcher,
        "_result_bundle",
        lambda _path: (None, None, False),
    )

    def fail_run(**_kwargs: object) -> dict:
        raise RuntimeError("synthetic detail must not enter authority records")

    monkeypatch.setattr(launcher, "run_unbounded_command", fail_run)
    original_cwd = Path.cwd()
    try:
        result = launcher.main(["--expected-commit", "1" * 40])
    finally:
        os.chdir(original_cwd)

    run_root = tmp_path / relative_output
    completion = json.loads(
        (run_root / "formal_run.completion.json").read_text(encoding="utf-8")
    )
    failure = json.loads(
        (run_root / "formal_run.failure.json").read_text(encoding="utf-8")
    )
    assert result == 1
    assert completion["result_available"] is False
    assert completion["launcher_error_type"] == "RuntimeError"
    assert failure["launcher_error_type"] == "RuntimeError"
    assert "synthetic detail" not in json.dumps(completion)
    assert failure["profile_registration_authority"] is False
    assert failure["production_recommendation_authority"] is False
    assert failure["automatic_trading_authority"] is False
    attempt_terminal = json.loads(
        (
            tmp_path
            / launcher.RUN_SPEC["attempt_contract"]["terminal_relative_path"]
        ).read_text(encoding="utf-8")
    )
    assert attempt_terminal["status"] == "failed"
    assert attempt_terminal["error_type"] == "RuntimeError"


@pytest.mark.parametrize("failure_target", ["output", "runtime_tmp"])
def test_main_directory_creation_failure_writes_external_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_target: str,
) -> None:
    relative_output, _preflight = _sandbox_launcher_main(monkeypatch, tmp_path)
    output_dir = tmp_path / relative_output
    target = (
        output_dir if failure_target == "output" else output_dir / "runtime_tmp"
    ).resolve(strict=False)
    original_mkdir = Path.mkdir

    def fail_selected_mkdir(
        self: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if self.resolve(strict=False) == target:
            raise PermissionError("synthetic sensitive mkdir detail")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_selected_mkdir)

    original_cwd = Path.cwd()
    try:
        result = launcher.main(["--expected-commit", "1" * 40])
    finally:
        os.chdir(original_cwd)

    attempt_path = (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["ledger_relative_path"]
    )
    terminal_path = (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["terminal_relative_path"]
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert result == 1
    assert attempt_path.is_file()
    assert terminal["status"] == "failed"
    assert terminal["attempt_claim_sha256"] == launcher.sha256_file(attempt_path)
    assert terminal["completion_sha256"] is None
    assert terminal["failure_sha256"] is None
    assert terminal["error_type"] == "PermissionError"
    assert "sensitive mkdir detail" not in json.dumps(terminal)
    assert terminal["production_authority"] is False


@pytest.mark.parametrize(
    ("failing_name", "completion_exists"),
    [
        ("formal_run.resource_receipt.json", True),
        ("formal_run.completion.json", False),
        ("formal_run.failure.json", True),
    ],
)
def test_claimed_attempt_write_failure_still_writes_external_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_name: str,
    completion_exists: bool,
) -> None:
    relative_output, _preflight = _sandbox_launcher_main(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_safe_progress", lambda _path: None)
    monkeypatch.setattr(
        launcher,
        "_result_bundle",
        lambda _path: (None, None, False),
    )

    def fake_run_unbounded_command(**kwargs: object) -> dict:
        command = kwargs["command"]
        Path(kwargs["stdout_path"]).write_bytes(b"")
        Path(kwargs["stderr_path"]).write_bytes(b"")
        return {
            "schema_version": "research-unbounded-job-object-command-receipt/v1",
            "exit_code": 0,
            "child_reaped": True,
            "job_object_assigned": True,
            "process_tree_drained": True,
            "memory_limit_enforced": False,
            "command_sha256": launcher._sha256({"tokens": command}),
        }

    monkeypatch.setattr(
        launcher,
        "run_unbounded_command",
        fake_run_unbounded_command,
    )
    original_write_json_once = launcher._write_json_once

    def fail_selected_write(path: Path, payload: dict) -> None:
        if path.name == failing_name:
            raise PermissionError("synthetic sensitive write detail")
        original_write_json_once(path, payload)

    monkeypatch.setattr(launcher, "_write_json_once", fail_selected_write)
    original_cwd = Path.cwd()
    try:
        result = launcher.main(["--expected-commit", "1" * 40])
    finally:
        os.chdir(original_cwd)

    run_root = tmp_path / relative_output
    terminal_path = (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["terminal_relative_path"]
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert result == 1
    assert terminal["status"] == "failed"
    assert terminal["error_type"] == "PermissionError"
    if completion_exists:
        assert terminal["completion_sha256"] is not None
    else:
        assert terminal["completion_sha256"] is None
    assert "sensitive write detail" not in json.dumps(terminal)
    assert (run_root / "formal_run.completion.json").exists() is completion_exists
    assert terminal["production_authority"] is False


def test_cli_masks_preflight_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_main(_argv: object = None) -> int:
        raise RuntimeError("synthetic sensitive detail")

    monkeypatch.setattr(launcher, "main", fail_main)

    assert launcher.cli(["--expected-commit", "1" * 40, "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "status=failed\n"
    assert "sensitive" not in captured.err


def test_single_attempt_ledger_is_external_and_write_once(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs" / "formal-run"
    ledger_path = tmp_path / "attempts" / "attempt.json"
    claim = {
        "schema_version": "formal-single-attempt-claim/v1",
        "run_spec_sha256": launcher.RUN_SPEC_SHA256,
    }

    claim_sha256 = launcher._reserve_single_attempt(
        ledger_path=ledger_path,
        output_dir=output_dir,
        claim=claim,
    )

    assert ledger_path.is_file()
    assert claim_sha256 == launcher.sha256_file(ledger_path)
    assert not output_dir.exists()
    assert json.loads(ledger_path.read_text(encoding="utf-8")) == claim
    with pytest.raises(FileExistsError, match="already claimed"):
        launcher._reserve_single_attempt(
            ledger_path=ledger_path,
            output_dir=output_dir,
            claim=claim,
        )


def test_write_once_never_exposes_partial_target_when_fsync_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "claim.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync detail")

    monkeypatch.setattr(launcher.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync detail"):
        launcher._write_json_once(target, {"schema_version": "synthetic/v1"})

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_attempt_terminal_rejects_replaced_claim(tmp_path: Path) -> None:
    attempt_path = tmp_path / "attempt.json"
    terminal_path = tmp_path / "terminal.json"
    launcher._write_json_once(
        attempt_path,
        {"schema_version": "formal-single-attempt-claim/v1", "owner": "first"},
    )
    owned_sha256 = launcher.sha256_file(attempt_path)
    attempt_path.unlink()
    launcher._write_json_once(
        attempt_path,
        {"schema_version": "formal-single-attempt-claim/v1", "owner": "foreign"},
    )

    with pytest.raises(RuntimeError, match="claim ownership"):
        launcher._write_attempt_terminal(
            terminal_path,
            status="failed",
            attempt_path=attempt_path,
            expected_claim_sha256=owned_sha256,
            completion_path=None,
            failure_path=None,
            error_type="RuntimeError",
        )

    assert not terminal_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="正式 launcher 只允许 Windows")
def test_preexisting_attempt_is_never_closed_by_a_later_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _sandbox_launcher_main(monkeypatch, tmp_path)
    attempt_path = (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["ledger_relative_path"]
    )
    terminal_path = (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["terminal_relative_path"]
    )
    attempt_path.parent.mkdir(parents=True)
    launcher._write_json_once(
        attempt_path,
        {
            "schema_version": "formal-single-attempt-claim/v1",
            "expected_commit": "0" * 40,
        },
    )
    original_bytes = attempt_path.read_bytes()

    original_cwd = Path.cwd()
    try:
        with pytest.raises(FileExistsError, match="already claimed"):
            launcher.main(["--expected-commit", "1" * 40])
    finally:
        os.chdir(original_cwd)

    assert attempt_path.read_bytes() == original_bytes
    assert not terminal_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="formal launcher only supports Windows")
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


def test_gated_bootstrap_disables_site_and_legacy_pyc_before_job_assignment(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "formal-pycache-blocker"
    blocker.write_bytes(b"formal-pycache-prefix-blocker-v1\n")
    command = launcher._gated_bootstrap_command(
        [sys.executable, "-c", "print('actual')"],
        event_value=123,
        pycache_prefix=blocker,
    )

    assert command[:7] == [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-X",
        f"pycache_prefix={blocker}",
        "-c",
    ]
    assert "import site" not in command[7]


def test_top_level_launcher_requires_isolated_no_site_interpreter(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "scripts/formal_pycache_blocker_v1"
    blocker.parent.mkdir()
    blocker.write_bytes(b"formal-pycache-prefix-blocker-v1\n")
    good = SimpleNamespace(
        isolated=1,
        no_site=1,
        ignore_environment=1,
        dont_write_bytecode=1,
    )
    launcher._assert_top_level_interpreter(
        good,
        pycache_prefix=str(blocker),
        workspace=tmp_path,
    )

    for field in (
        "isolated",
        "no_site",
        "ignore_environment",
        "dont_write_bytecode",
    ):
        bad = SimpleNamespace(**vars(good))
        setattr(bad, field, 0)
        with pytest.raises(RuntimeError, match="isolated no-site interpreter"):
            launcher._assert_top_level_interpreter(
                bad,
                pycache_prefix=str(blocker),
                workspace=tmp_path,
            )
    with pytest.raises(RuntimeError, match="pycache blocker"):
        launcher._assert_top_level_interpreter(
            good,
            pycache_prefix=str(tmp_path / "wrong"),
            workspace=tmp_path,
        )


def test_preflight_probe_uses_isolated_no_site_source_paths(
    tmp_path: Path,
) -> None:
    pycache_prefix = tmp_path / "fresh-pycache"
    command = launcher._isolated_probe_command(
        Path(sys.executable),
        environment={"PYTHONPYCACHEPREFIX": str(pycache_prefix)},
        code="print('probe')",
        arguments=["synthetic"],
    )

    assert command[:7] == [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-X",
        f"pycache_prefix={pycache_prefix}",
        "-c",
    ]
    assert "sys.path[:0]" in command[7]
    assert "sys.argv.pop(1)" in command[7]
    assert command[-1] == "synthetic"


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


@pytest.mark.skipif(os.name != "nt", reason="正式 launcher 只允许 Windows")
def test_job_receipt_waits_for_delayed_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-finished.txt"
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=tmp_path,
    )
    descendant = (
        "import time; from pathlib import Path; time.sleep(0.35); "
        f"Path({str(marker)!r}).write_text('finished')"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])"
    )

    started = time.monotonic()
    receipt = launcher.run_unbounded_command(
        command=[sys.executable, "-c", parent],
        cwd=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        environment=environment,
    )

    assert time.monotonic() - started >= 0.30
    assert marker.read_text(encoding="utf-8") == "finished"
    assert receipt["process_tree_drained"] is True


def test_risk_on_breadth_producer_binding_rejects_valid_format_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = {
        "site_loaded": False,
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
            environment={
                "PYTHONHASHSEED": "0",
                "PYTHONPYCACHEPREFIX": str(tmp_path / "fresh-pycache"),
            },
        )


def test_formal_run_spec_rejects_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(launcher.RUN_SPEC["scope"], "development_only", False)

    with pytest.raises(RuntimeError, match="run spec drifted"):
        launcher._assert_frozen_run_spec()


def test_path_environment_attempt_and_commit_boundaries_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="path is invalid"):
        launcher._safe_workspace_path(tmp_path, "", "synthetic")
    with pytest.raises(RuntimeError, match="path is invalid"):
        launcher._safe_workspace_path(tmp_path, str(tmp_path), "synthetic")
    with pytest.raises(RuntimeError, match="path is invalid"):
        launcher._safe_workspace_path(tmp_path, "missing.json", "synthetic")
    with pytest.raises(RuntimeError, match="system root is unavailable"):
        launcher._minimal_child_environment(
            {},
            python_executable=Path(sys.executable),
            temp_dir=tmp_path,
        )
    output_dir = tmp_path / "formal-run"
    output_dir.mkdir()
    with pytest.raises(ValueError, match="outside output root"):
        launcher._reserve_single_attempt(
            ledger_path=output_dir / "attempt.json",
            output_dir=output_dir,
            claim={"schema_version": "synthetic/v1"},
        )
    with pytest.raises(ValueError, match="full lowercase SHA-1"):
        launcher._validated_expected_commit("ABC")


def test_workspace_path_rejects_internal_reparse_alias(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    alias = tmp_path / "alias.json"
    real.write_text("{}", encoding="utf-8")
    try:
        alias.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")

    with pytest.raises(RuntimeError, match="reparse"):
        launcher._safe_workspace_path(tmp_path, alias.name, "synthetic")


def test_formal_entrypoint_requires_direct_source_execution(tmp_path: Path) -> None:
    script_path = tmp_path / launcher.LAUNCHER_GIT_PATH
    script_path.parent.mkdir()
    script_path.write_text("synthetic", encoding="utf-8")
    rogue_path = script_path.with_name("rogue.py")
    rogue_path.write_text("synthetic", encoding="utf-8")

    launcher._assert_direct_entrypoint("", script_path, tmp_path)
    with pytest.raises(RuntimeError, match="direct committed source file"):
        launcher._assert_direct_entrypoint("scripts", script_path, tmp_path)
    with pytest.raises(RuntimeError, match="direct committed source file"):
        launcher._assert_direct_entrypoint("", rogue_path, tmp_path)


def test_module_entrypoint_is_rejected_before_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _sandbox_launcher_main(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "__package__", "scripts")

    with pytest.raises(RuntimeError, match="direct committed source file"):
        launcher.main(["--expected-commit", "1" * 40])

    assert not (
        tmp_path / launcher.RUN_SPEC["attempt_contract"]["ledger_relative_path"]
    ).exists()


def test_fresh_pycache_prefix_prevents_ignored_legacy_pyc_execution(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "synthetic_module.py"
    module_path.write_text("VALUE = 'evil'\n", encoding="utf-8")
    py_compile.compile(
        str(module_path),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    module_path.write_text("VALUE = 'good'\n", encoding="utf-8")

    inherited = dict(os.environ)
    inherited.pop("PYTHONPYCACHEPREFIX", None)
    legacy = subprocess.run(
        [sys.executable, "-c", "import synthetic_module; print(synthetic_module.VALUE)"],
        cwd=tmp_path,
        env=inherited,
        check=True,
        capture_output=True,
        text=True,
    )
    assert legacy.stdout.strip() == "evil"

    runtime_temp = tmp_path / "runtime-temp"
    environment = launcher._minimal_child_environment(
        os.environ,
        python_executable=Path(sys.executable),
        temp_dir=runtime_temp,
    )
    isolated = subprocess.run(
        [sys.executable, "-c", "import synthetic_module; print(synthetic_module.VALUE)"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert isolated.stdout.strip() == "good"


def test_input_attestation_rejects_empty_tree_and_duplicate_paths(
    tmp_path: Path,
) -> None:
    universe = tmp_path / "universe.sqlite3"
    temporal = tmp_path / "frozen.json"
    current_pool = tmp_path / "current-pool.json"
    transition = tmp_path / "transition"
    transition.mkdir()
    universe.write_bytes(b"universe")
    temporal.write_bytes(b"temporal")
    current_pool.write_bytes(b"current-pool")
    inputs = {
        "audited_pit_universe_path": universe.name,
        "temporal_contract_path": temporal.name,
        "current_pool_development_audit_path": current_pool.name,
        "security_code_transition_evidence_root": transition.name,
        "observed_attestation": launcher.RUN_SPEC["inputs"]["observed_attestation"],
    }

    with pytest.raises(RuntimeError, match="input tree is empty"):
        launcher.frozen_input_attestation(tmp_path, inputs)

    (transition / "receipt.json").write_bytes(b"transition")
    inputs["current_pool_development_audit_path"] = universe.name
    with pytest.raises(RuntimeError, match="path is duplicated"):
        launcher.frozen_input_attestation(tmp_path, inputs)


def test_progress_and_content_address_boundaries_fail_closed(tmp_path: Path) -> None:
    assert launcher._safe_progress(tmp_path / "missing.json") is None
    invalid_progress = tmp_path / "progress.json"
    invalid_progress.write_text("not-json", encoding="utf-8")
    assert launcher._safe_progress(invalid_progress) is None
    invalid_progress.write_text("[]", encoding="utf-8")
    assert launcher._safe_progress(invalid_progress) is None

    wrong_name = tmp_path / "wrong-name.json"
    wrong_name.write_text("{}", encoding="utf-8")
    assert launcher._content_addressed_document(wrong_name) is None
    bad_json = tmp_path / ("0" * 64 + ".json")
    bad_json.write_text("not-json", encoding="utf-8")
    assert launcher._content_addressed_document(bad_json) is None
    bad_json.write_text(
        json.dumps({"schema_version": "synthetic/v1", "artifact_sha256": "0" * 64}),
        encoding="utf-8",
    )
    assert launcher._content_addressed_document(bad_json) is None


@pytest.mark.parametrize("mode", ["wrong_commit", "dirty", "blob_drift"])
def test_preflight_source_identity_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    expected_commit = "1" * 40

    def fake_git_output(*args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "2" * 40 if mode == "wrong_commit" else expected_commit
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return " M tracked.py" if mode == "dirty" else ""
        raise AssertionError(args)

    monkeypatch.setattr(launcher, "git_output", fake_git_output)
    monkeypatch.setattr(
        launcher,
        "git_bytes",
        lambda *_args: (
            b"drifted"
            if mode == "blob_drift"
            else launcher.normalized_source_bytes(launcher.SCRIPT_PATH)
        ),
    )

    with pytest.raises(RuntimeError):
        launcher._preflight(
            expected_commit=expected_commit,
            python_executable=Path(sys.executable),
            environment={"PYTHONHASHSEED": "0"},
        )
