from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Mapping


SCRIPT_PATH = Path(__file__).resolve()
WORKSPACE = Path(r"E:\AI workspace\quant-signal-lkj")
LAUNCHER_GIT_PATH = "scripts/run_shallow_gbdt_probability_budget_development_1.py"
RELATIVE_OUTPUT_DIR = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_probability_budget_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
EXPECTED_STRATEGY_SHA256 = (
    "4bd7afa5a8694580f9eabc2c6aed1554ea2e199d189c8e5a808265700100aaf5"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "5e04d64e6719e25f49ac342556f529877e87e39bc7abe6efda7549c193fd9bca"
)
EXPECTED_RUN_SPEC_SHA256 = (
    "bb676ebfc55a31334ac8be2f80f955fe837d8cd6b903c1d52ef0588272255daf"
)
EXPECTED_PROGRESS_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-replay-progress/v1"
)
EXPECTED_RESULT_VERIFICATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-probability-budget-"
    "result-bundle-verification/v1"
)
PROGRESS_FILE_NAME = (
    ".ranked_liquidity_shallow_gbdt_probability_budget_v1_progress.json"
)
HEX_ARTIFACT = re.compile(r"^[0-9a-f]{64}\.json$")
OUTPUT_DIR_OWNED_BY_THIS_PROCESS = False

RUN_SPEC: dict[str, Any] = {
    "schema_version": (
        "ranked-liquidity-shallow-gbdt-probability-budget-formal-run-spec/v1"
    ),
    "hypothesis_id": (
        "probability_budget_linear_excess_over_positive_utility_probability"
    ),
    "command": (
        "research-audited-pit-ranked-liquidity-"
        "shallow-gbdt-probability-budget-rolling-oof"
    ),
    "inputs": {
        "audited_pit_universe_path": (
            "data/research_artifacts/audited_pit_universe_v2/"
            "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4/"
            "metadata.sqlite3"
        ),
        "expected_coverage_audit_sha256": (
            "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
        ),
        "expected_artifact_root_sha256": (
            "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
        ),
        "temporal_contract_path": "data/research_partitions/frozen-v2.json",
        "expected_temporal_contract_sha256": (
            "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
        ),
        "security_code_transition_evidence_root": (
            "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
    },
    "development_partition": {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    },
    "resource_contract": {
        "memory_policy": "unbounded",
        "enforcement": "none",
    },
    "scope": {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    },
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


RUN_SPEC_SHA256 = EXPECTED_RUN_SPEC_SHA256


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def git_bytes(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def normalized_source_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _assert_frozen_run_spec() -> None:
    if (
        hashlib.sha256(_canonical_bytes(RUN_SPEC)).hexdigest()
        != EXPECTED_RUN_SPEC_SHA256
    ):
        raise RuntimeError("formal probability-budget run spec drifted")
    if RUN_SPEC["resource_contract"] != {
        "memory_policy": "unbounded",
        "enforcement": "none",
    }:
        raise RuntimeError("formal probability-budget resource contract drifted")
    if RUN_SPEC["development_partition"] != {
        "start_date": "2024-07-05",
        "end_date": "2026-07-03",
        "temporal_role": "development",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }:
        raise RuntimeError("formal probability-budget partition drifted")
    if RUN_SPEC["scope"] != {
        "point_in_time": True,
        "development_only": True,
        "production_authority": False,
        "automatic_trading_authority": False,
    }:
        raise RuntimeError("formal probability-budget scope drifted")


def _assert_input_layout() -> None:
    inputs = RUN_SPEC["inputs"]
    required_paths = (
        inputs["audited_pit_universe_path"],
        inputs["temporal_contract_path"],
        inputs["security_code_transition_evidence_root"],
    )
    if any(not (WORKSPACE / relative_path).exists() for relative_path in required_paths):
        raise RuntimeError("formal probability-budget frozen input is unavailable")


def probability_budget_producer_binding(python_executable: Path) -> dict[str, Any]:
    code = (
        "import json\n"
        "from app import audited_pit_continuous_ridge_oof as ridge\n"
        "from app import audited_pit_shallow_gbdt_probability_budget as budget\n"
        "print(json.dumps({\n"
        "'strategy_sha256': budget._SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256,\n"
        "'producer_binding': ridge._shallow_gbdt_probability_budget_producer_binding(),\n"
        "}, ensure_ascii=False, sort_keys=True))\n"
    )
    completed = subprocess.run(
        [str(python_executable), "-c", code],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    binding = json.loads(completed.stdout)
    if (
        not isinstance(binding, dict)
        or binding.get("strategy_sha256") != EXPECTED_STRATEGY_SHA256
        or not isinstance(binding.get("producer_binding"), dict)
        or binding["producer_binding"].get("root_sha256")
        != EXPECTED_PRODUCER_ROOT_SHA256
    ):
        raise RuntimeError("formal probability-budget producer binding drifted")
    return binding


def run_unbounded_command(
    *,
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    if not command or any(not isinstance(token, str) or not token for token in command):
        raise ValueError("unbounded research command is invalid")
    if stdout_path.exists() or stderr_path.exists() or stdout_path == stderr_path:
        raise ValueError("unbounded research output paths are invalid")
    started_at = utc_now()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(environment),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
        )
        exit_code = process.wait()
    return {
        "schema_version": "research-unbounded-command-receipt/v1",
        "pid": process.pid,
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "exit_code": exit_code,
        "command_sha256": hashlib.sha256(
            _canonical_bytes({"tokens": command})
        ).hexdigest(),
        "argument_count": len(command),
        "cwd": str(cwd),
        "shell": False,
        "stdin_closed": True,
        "memory_limit_enforced": False,
        "child_reaped": process.poll() is not None,
        "process_tree_drained": None,
        "process_tree_drain_verification": "not_performed",
        "stdout": {
            "path": str(stdout_path),
            "bytes": stdout_path.stat().st_size,
            "sha256": sha256_file(stdout_path),
        },
        "stderr": {
            "path": str(stderr_path),
            "bytes": stderr_path.stat().st_size,
            "sha256": sha256_file(stderr_path),
        },
    }


def _command_arguments() -> list[str]:
    inputs = RUN_SPEC["inputs"]
    partition = RUN_SPEC["development_partition"]
    return [
        "-m",
        "app.jobs",
        RUN_SPEC["command"],
        "--audited-pit-universe-path",
        inputs["audited_pit_universe_path"],
        "--expected-coverage-audit-sha256",
        inputs["expected_coverage_audit_sha256"],
        "--expected-artifact-root-sha256",
        inputs["expected_artifact_root_sha256"],
        "--temporal-contract-path",
        inputs["temporal_contract_path"],
        "--expected-temporal-contract-sha256",
        inputs["expected_temporal_contract_sha256"],
        "--security-code-transition-evidence-root",
        inputs["security_code_transition_evidence_root"],
        "--expected-security-code-transition-contract-sha256",
        inputs["expected_security_code_transition_contract_sha256"],
        "--start-date",
        partition["start_date"],
        "--end-date",
        partition["end_date"],
        "--output-dir",
        str(RELATIVE_OUTPUT_DIR),
    ]


def _safe_progress(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        "schema_version": value.get("schema_version"),
        "stage": value.get("stage"),
        "artifact_sha256": value.get("artifact_sha256"),
    }


def _content_addressed_document(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or not HEX_ARTIFACT.fullmatch(path.name):
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    embedded = document.pop("artifact_sha256", None)
    canonical = hashlib.sha256(_canonical_bytes(document)).hexdigest()
    if embedded != canonical or path.stem != canonical:
        return None
    return {
        "path": path.name,
        "file_sha256": sha256_file(path),
        "canonical_artifact_sha256": canonical,
        "embedded_artifact_sha256": embedded,
    }


def _result_artifact(output_dir: Path) -> tuple[dict[str, Any] | None, bool]:
    artifacts = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and HEX_ARTIFACT.fullmatch(path.name)
    )
    if len(artifacts) != 1:
        return None, False
    artifact = _content_addressed_document(artifacts[0])
    return artifact, artifact is not None


def _runtime_verification(output_dir: Path) -> tuple[dict[str, Any] | None, bool]:
    directory = output_dir / "verifications"
    artifacts = (
        sorted(path for path in directory.iterdir() if path.is_file())
        if directory.is_dir()
        else []
    )
    if len(artifacts) != 1:
        return None, False
    artifact = _content_addressed_document(artifacts[0])
    if artifact is None:
        return None, False
    try:
        document = json.loads(artifacts[0].read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, False
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != EXPECTED_RESULT_VERIFICATION_SCHEMA
        or document.get("verified") is not True
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(document.get("main_artifact_sha256") or ""),
        )
    ):
        return None, False
    return {
        **artifact,
        "schema_version": document["schema_version"],
        "main_artifact_sha256": document["main_artifact_sha256"],
    }, True


def _validated_expected_commit(value: str) -> str:
    expected = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("expected commit must be a full lowercase SHA-1")
    return expected


def _preflight(*, expected_commit: str, python_executable: Path) -> dict[str, Any]:
    _assert_frozen_run_spec()
    _assert_input_layout()
    current_commit = git_output("rev-parse", "HEAD").lower()
    if current_commit != expected_commit:
        raise RuntimeError("formal probability-budget commit drifted")
    if git_output("status", "--porcelain"):
        raise RuntimeError("formal probability-budget tracked worktree is not clean")
    launcher_blob = git_bytes("show", f"{expected_commit}:{LAUNCHER_GIT_PATH}")
    if launcher_blob != normalized_source_bytes(SCRIPT_PATH):
        raise RuntimeError("formal probability-budget launcher does not match commit")
    binding = probability_budget_producer_binding(python_executable)
    return {
        "git_commit": current_commit,
        "strategy_sha256": binding["strategy_sha256"],
        "producer_binding": binding["producer_binding"],
        "formal_launcher_sha256": sha256_file(SCRIPT_PATH),
        "formal_launcher_git_blob_sha256": hashlib.sha256(launcher_blob).hexdigest(),
        "run_spec_sha256": RUN_SPEC_SHA256,
    }


def main(argv: list[str] | None = None) -> int:
    global OUTPUT_DIR_OWNED_BY_THIS_PROCESS

    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    expected_commit = _validated_expected_commit(args.expected_commit)
    if __package__ != "scripts" or SCRIPT_PATH.parents[1] != WORKSPACE.resolve():
        raise RuntimeError("formal launcher must run as the committed scripts module")

    os.chdir(WORKSPACE)
    output_dir = WORKSPACE / RELATIVE_OUTPUT_DIR
    if output_dir.exists():
        raise FileExistsError(f"formal output directory exists: {output_dir}")
    python_executable = (WORKSPACE / ".venv/Scripts/python.exe").resolve()
    preflight = _preflight(
        expected_commit=expected_commit,
        python_executable=python_executable,
    )
    if args.dry_run:
        print("status=preflight_verified")
        return 0

    output_dir.mkdir(parents=False)
    OUTPUT_DIR_OWNED_BY_THIS_PROCESS = True
    started_at = utc_now()
    progress_path = output_dir / PROGRESS_FILE_NAME
    stdout_path = output_dir / "formal_run.stdout.log"
    stderr_path = output_dir / "formal_run.stderr.log"
    resource_receipt_path = output_dir / "formal_run.resource_receipt.json"
    launch_path = output_dir / "formal_run.launch.json"
    completion_path = output_dir / "formal_run.completion.json"
    failure_path = output_dir / "formal_run.failure.json"
    replay_args = _command_arguments()
    command = [str(python_executable), *replay_args]
    command_sha256 = hashlib.sha256(_canonical_bytes({"tokens": command})).hexdigest()
    write_json(
        output_dir / "formal_run.preflight.json",
        {
            "schema_version": (
                "ranked-liquidity-shallow-gbdt-probability-budget-preflight/v1"
            ),
            "observed_at_utc": started_at,
            **preflight,
            "run_spec": RUN_SPEC,
            "formal_launcher_invocation": [
                "-m",
                "scripts.run_shallow_gbdt_probability_budget_development_1",
            ],
            "runtime_role": "local_research",
            "statistical_result_available": False,
        },
    )
    write_json(
        launch_path,
        {
            "schema_version": (
                "ranked-liquidity-shallow-gbdt-probability-budget-launch/v1"
            ),
            "started_at_utc": started_at,
            **preflight,
            "executable": str(python_executable),
            "arguments": replay_args,
            "command_sha256": command_sha256,
            "runtime_role": "local_research",
            "memory_policy": "unbounded",
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    )

    resource_receipt = None
    launcher_exception = None
    try:
        child_environment = dict(os.environ)
        child_environment["VPS_RUNTIME_ROLE"] = "local_research"
        resource_receipt = run_unbounded_command(
            command=command,
            cwd=WORKSPACE,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            environment=child_environment,
        )
        write_json(resource_receipt_path, resource_receipt)
    except BaseException:
        launcher_exception = traceback.format_exc()
        (output_dir / "formal_run.launcher_exception.log").write_text(
            launcher_exception,
            encoding="utf-8",
        )

    post = _preflight(
        expected_commit=expected_commit,
        python_executable=python_executable,
    )
    immutable_inputs_unchanged = post == preflight
    progress = _safe_progress(progress_path)
    result_artifact, artifact_content_addressed = _result_artifact(output_dir)
    runtime_verification, runtime_verification_content_addressed = (
        _runtime_verification(output_dir)
    )
    resource_ok = bool(
        resource_receipt
        and resource_receipt["schema_version"]
        == "research-unbounded-command-receipt/v1"
        and resource_receipt["exit_code"] == 0
        and resource_receipt["child_reaped"] is True
        and resource_receipt["memory_limit_enforced"] is False
        and resource_receipt["command_sha256"] == command_sha256
    )
    result_available = bool(
        resource_ok
        and progress
        and progress.get("schema_version") == EXPECTED_PROGRESS_SCHEMA
        and progress.get("stage") == "completed"
        and artifact_content_addressed
        and runtime_verification_content_addressed
        and progress.get("artifact_sha256")
        == (result_artifact or {}).get("canonical_artifact_sha256")
        and (runtime_verification or {}).get("main_artifact_sha256")
        == (result_artifact or {}).get("canonical_artifact_sha256")
        and immutable_inputs_unchanged
    )
    classification = (
        "completed_result_pending_independent_verification"
        if result_available
        else (
            "unbounded_launcher_exception_before_valid_result"
            if launcher_exception
            else "unbounded_run_without_valid_completed_result"
        )
    )
    exception_path = output_dir / "formal_run.launcher_exception.log"
    exception_receipt = (
        {
            "path": exception_path.name,
            "bytes": exception_path.stat().st_size,
            "sha256": sha256_file(exception_path),
        }
        if exception_path.exists()
        else None
    )
    write_json(
        completion_path,
        {
            "schema_version": (
                "ranked-liquidity-shallow-gbdt-probability-budget-completion/v1"
            ),
            "started_at_utc": started_at,
            "finished_at_utc": utc_now(),
            "classification": classification,
            "preflight": preflight,
            "post_run_preflight": post,
            "immutable_inputs_unchanged": immutable_inputs_unchanged,
            "launch_receipt_sha256": sha256_file(launch_path),
            "resource_receipt": (
                {
                    "path": resource_receipt_path.name,
                    "sha256": sha256_file(resource_receipt_path),
                    "exit_code": resource_receipt["exit_code"],
                    "memory_limit_enforced": resource_receipt[
                        "memory_limit_enforced"
                    ],
                    "process_tree_drained": resource_receipt["process_tree_drained"],
                }
                if resource_receipt
                else None
            ),
            "launcher_exception": exception_receipt,
            "progress": progress,
            "result_artifact": result_artifact,
            "runtime_verification": runtime_verification,
            "result_available": result_available,
            "artifact_content_addressed": artifact_content_addressed,
            "runtime_verification_content_addressed": (
                runtime_verification_content_addressed
            ),
            "independent_verification_complete": False,
            "statistical_interpretation_allowed": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_authority": False,
        },
    )
    if not result_available:
        write_json(
            failure_path,
            {
                "schema_version": (
                    "ranked-liquidity-shallow-gbdt-probability-budget-failure/v1"
                ),
                "observed_at_utc": utc_now(),
                "classification": classification,
                "last_progress": progress,
                "resource_receipt_available": resource_receipt is not None,
                "launcher_exception_available": launcher_exception is not None,
                "launcher_exception": exception_receipt,
                "statistical_result_available": False,
                "usable_for_hyperparameter_selection": False,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "production_authority": False,
            },
        )
        return 1
    return 0


def write_uncaught_failure() -> None:
    if not OUTPUT_DIR_OWNED_BY_THIS_PROCESS:
        return
    output_dir = WORKSPACE / RELATIVE_OUTPUT_DIR
    exception_text = traceback.format_exc()
    try:
        exception_path = output_dir / "formal_run.launcher_exception.log"
        exception_path.write_text(exception_text, encoding="utf-8")
        failure_path = output_dir / "formal_run.failure.json"
        if not failure_path.exists():
            write_json(
                failure_path,
                {
                    "schema_version": (
                        "ranked-liquidity-shallow-gbdt-probability-budget-"
                        "failure/v1"
                    ),
                    "observed_at_utc": utc_now(),
                    "classification": (
                        "formal_launcher_uncaught_exception_before_valid_result"
                    ),
                    "launcher_exception": {
                        "path": exception_path.name,
                        "bytes": exception_path.stat().st_size,
                        "sha256": sha256_file(exception_path),
                    },
                    "statistical_result_available": False,
                    "usable_for_hyperparameter_selection": False,
                    "embargo_consumed": False,
                    "final_oos_consumed": False,
                    "production_authority": False,
                },
            )
    except BaseException:
        print(exception_text, file=sys.stderr, flush=True)
        print(traceback.format_exc(), file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        write_uncaught_failure()
        raise
