from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path



SCRIPT_PATH = Path(__file__).resolve()
WORKSPACE = Path(r"E:\AI workspace\quant-signal-lkj")
RELATIVE_OUTPUT_DIR = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_rolling126_oof_v1_"
    "development_9_unbounded_local_research"
)
PARENT_FAILURE = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_rolling126_oof_v1_"
    "development_8_inplace_feature_input_local_research_9gib/formal_run.failure.json"
)
EXPECTED_PARENT_FAILURE_SHA256 = (
    "26818f79f02ec47507b5e0817ef1193d092db18d8952cba389d4c6b522899685"
)
EXPECTED_STRATEGY_SHA256 = (
    "53d00badc8683670ef3d6c02307697e2c3ef8ec769b9d8da072d36420cea70ac"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "8e335c35b01bf8662256f50effd732b58ce176018fe504c5a36b5868a5c71a18"
)
HEX_ARTIFACT = re.compile(r"^[0-9a-f]{64}\.json$")
LAUNCHER_GIT_PATH = "scripts/run_shallow_gbdt_development_3_resource_capped.py"
OUTPUT_DIR_OWNED_BY_THIS_PROCESS = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_unbounded_command(
    *,
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    environment: dict[str, str],
) -> dict:
    if not command or any(not isinstance(token, str) or not token for token in command):
        raise ValueError("unbounded research command is invalid")
    if stdout_path.exists() or stderr_path.exists() or stdout_path == stderr_path:
        raise ValueError("unbounded research output paths are invalid")
    started_at = utc_now()
    with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(environment),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        exit_code = process.wait()
    return {
        "schema_version": "research-unbounded-command-receipt/v1",
        "pid": process.pid,
        "started_at_utc": started_at,
        "finished_at_utc": utc_now(),
        "exit_code": exit_code,
        "command_sha256": hashlib.sha256(
            json.dumps(
                {"tokens": command},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "argument_count": len(command),
        "cwd": str(cwd),
        "shell": False,
        "stdin_closed": True,
        "memory_limit_enforced": False,
        "child_reaped": process.poll() is not None,
        "process_tree_drained": True,
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


def producer_binding(python_executable: Path) -> dict:
    code = (
        "import json\n"
        "from app import audited_pit_shallow_gbdt as gbdt\n"
        "from app.audited_pit_continuous_ridge_oof "
        "import _shallow_gbdt_producer_binding\n"
        "print(json.dumps({"
        "'strategy_sha256': gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256, "
        "'producer_binding': _shallow_gbdt_producer_binding()"
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
    return json.loads(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    global OUTPUT_DIR_OWNED_BY_THIS_PROCESS

    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    expected_commit = args.expected_commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("expected commit must be a full lowercase SHA-1")
    if __package__ != "scripts" or SCRIPT_PATH.parents[1] != WORKSPACE.resolve():
        raise RuntimeError("formal launcher must run as the committed scripts module")

    os.chdir(WORKSPACE)
    output_dir = WORKSPACE / RELATIVE_OUTPUT_DIR
    if output_dir.exists():
        raise FileExistsError(f"formal output directory exists: {output_dir}")
    current_commit = git_output("rev-parse", "HEAD").lower()
    if current_commit != expected_commit:
        raise RuntimeError("formal commit drifted")
    if git_output("status", "--porcelain"):
        raise RuntimeError("formal tracked worktree is not clean")
    launcher_blob = git_bytes(
        "show",
        f"{expected_commit}:{LAUNCHER_GIT_PATH}",
    )
    if launcher_blob != normalized_source_bytes(SCRIPT_PATH):
        raise RuntimeError("formal launcher does not match the expected commit")
    launcher_git_blob_sha256 = hashlib.sha256(launcher_blob).hexdigest()

    parent_path = WORKSPACE / PARENT_FAILURE
    if sha256_file(parent_path) != EXPECTED_PARENT_FAILURE_SHA256:
        raise RuntimeError("parent failure receipt drifted")
    python_executable = (WORKSPACE / ".venv/Scripts/python.exe").resolve()
    binding = producer_binding(python_executable)
    if (
        binding["strategy_sha256"] != EXPECTED_STRATEGY_SHA256
        or binding["producer_binding"]["root_sha256"]
        != EXPECTED_PRODUCER_ROOT_SHA256
    ):
        raise RuntimeError("strategy or producer binding drifted")

    output_dir.mkdir(parents=False)
    OUTPUT_DIR_OWNED_BY_THIS_PROCESS = True
    script_path = SCRIPT_PATH
    launcher_sha256 = sha256_file(script_path)
    progress_path = output_dir / ".ranked_liquidity_shallow_gbdt_v1_progress.json"
    stdout_path = output_dir / "formal_run.stdout.log"
    stderr_path = output_dir / "formal_run.stderr.log"
    resource_receipt_path = output_dir / "formal_run.resource_receipt.json"
    completion_path = output_dir / "formal_run.completion.json"
    failure_path = output_dir / "formal_run.failure.json"
    started_at = utc_now()

    replay_args = [
        "-m",
        "app.jobs",
        "research-audited-pit-ranked-liquidity-shallow-gbdt-rolling-oof",
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
        str(RELATIVE_OUTPUT_DIR),
    ]
    command = [str(python_executable), *replay_args]
    command_sha256 = hashlib.sha256(
        json.dumps(
            {"tokens": command},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    preflight = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-unbounded-preflight/v1"
        ),
        "observed_at_utc": started_at,
        "git_commit": current_commit,
        "tracked_worktree_clean": True,
        "strategy_sha256": binding["strategy_sha256"],
        "producer_binding": binding["producer_binding"],
        "formal_launcher_sha256": launcher_sha256,
        "formal_launcher_git_blob_sha256": launcher_git_blob_sha256,
        "formal_launcher_invocation": [
            "-m",
            "scripts.run_shallow_gbdt_development_3_resource_capped",
        ],
        "retry_kind": "same_frozen_hypothesis_unbounded_memory_replay",
        "runtime_role": "local_research",
        "parent_failure_receipt": {
            "path": str(PARENT_FAILURE).replace("\\", "/"),
            "sha256": EXPECTED_PARENT_FAILURE_SHA256,
            "classification": "resource_capped_run_without_valid_completed_result",
            "statistical_result_available": False,
        },
        "resource_contract": {
            "memory_policy": "unbounded",
            "enforcement": "none",
        },
        "development_partition": {
            "start_date": "2024-07-05",
            "end_date": "2026-07-03",
            "temporal_role": "development",
            "embargo_consumed": False,
            "final_oos_consumed": False,
        },
    }
    write_json(output_dir / "formal_run.preflight.json", preflight)
    launch = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-unbounded-launch/v1"
        ),
        "started_at_utc": started_at,
        "supervisor_process_id": os.getpid(),
        "git_commit": current_commit,
        "strategy_sha256": EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": EXPECTED_PRODUCER_ROOT_SHA256,
        "executable": str(python_executable),
        "arguments": replay_args,
        "formal_launcher_sha256": launcher_sha256,
        "formal_launcher_git_blob_sha256": launcher_git_blob_sha256,
        "formal_launcher_invocation": [
            "-m",
            "scripts.run_shallow_gbdt_development_3_resource_capped",
        ],
        "parent_failure_receipt_sha256": EXPECTED_PARENT_FAILURE_SHA256,
        "runtime_role": "local_research",
        "memory_policy": "unbounded",
        "embargo_consumed": False,
        "final_oos_consumed": False,
    }
    launch_path = output_dir / "formal_run.launch.json"
    write_json(launch_path, launch)

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

    finished_at = utc_now()
    post_commit = git_output("rev-parse", "HEAD").lower()
    post_clean = not bool(
        git_output("status", "--porcelain")
    )
    post_binding = producer_binding(python_executable)
    post_launcher_sha256 = sha256_file(script_path)
    post_launcher_blob = git_bytes(
        "show",
        f"{expected_commit}:{LAUNCHER_GIT_PATH}",
    )
    post_launcher_blob_sha256 = hashlib.sha256(post_launcher_blob).hexdigest()
    immutable_inputs_unchanged = (
        post_commit == expected_commit
        and post_clean
        and post_launcher_sha256 == launcher_sha256
        and post_launcher_blob_sha256 == launcher_git_blob_sha256
        and post_launcher_blob == normalized_source_bytes(script_path)
        and post_binding["strategy_sha256"] == EXPECTED_STRATEGY_SHA256
        and post_binding["producer_binding"]["root_sha256"]
        == EXPECTED_PRODUCER_ROOT_SHA256
    )
    progress = (
        json.loads(progress_path.read_text(encoding="utf-8-sig"))
        if progress_path.exists()
        else None
    )
    artifacts = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and HEX_ARTIFACT.fullmatch(path.name)
    )
    artifact_file_sha256 = (
        sha256_file(artifacts[0]) if len(artifacts) == 1 else None
    )
    artifact_payload = (
        json.loads(artifacts[0].read_text(encoding="utf-8-sig"))
        if len(artifacts) == 1
        else None
    )
    embedded_artifact_sha256 = (
        artifact_payload.pop("artifact_sha256", None)
        if isinstance(artifact_payload, dict)
        else None
    )
    canonical_artifact_sha256 = (
        hashlib.sha256(
            json.dumps(
                artifact_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if isinstance(artifact_payload, dict)
        else None
    )
    artifact_content_addressed = bool(
        len(artifacts) == 1
        and canonical_artifact_sha256 == artifacts[0].stem
        and embedded_artifact_sha256 == canonical_artifact_sha256
        and progress
        and progress.get("artifact_sha256") == canonical_artifact_sha256
    )
    resource_ok = bool(
        resource_receipt
        and resource_receipt["schema_version"]
        == "research-unbounded-command-receipt/v1"
        and resource_receipt["exit_code"] == 0
        and resource_receipt["child_reaped"] is True
        and resource_receipt["process_tree_drained"] is True
        and resource_receipt["memory_limit_enforced"] is False
        and resource_receipt["command_sha256"] == command_sha256
    )
    result_available = bool(
        resource_ok
        and progress
        and progress.get("schema_version")
        == "ranked-liquidity-shallow-gbdt-replay-progress/v1"
        and progress.get("stage") == "completed"
        and artifact_content_addressed
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
    launcher_exception_path = output_dir / "formal_run.launcher_exception.log"
    launcher_exception_receipt = (
        {
            "path": launcher_exception_path.name,
            "bytes": launcher_exception_path.stat().st_size,
            "sha256": sha256_file(launcher_exception_path),
        }
        if launcher_exception_path.exists()
        else None
    )
    completion = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-unbounded-completion/v1"
        ),
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "classification": classification,
        "git_commit": current_commit,
        "post_run_git_commit": post_commit,
        "post_run_tracked_worktree_clean": post_clean,
        "strategy_sha256": EXPECTED_STRATEGY_SHA256,
        "producer_root_sha256": EXPECTED_PRODUCER_ROOT_SHA256,
        "post_run_strategy_sha256": post_binding["strategy_sha256"],
        "post_run_producer_root_sha256": (
            post_binding["producer_binding"]["root_sha256"]
        ),
        "formal_launcher_sha256": launcher_sha256,
        "post_run_formal_launcher_sha256": post_launcher_sha256,
        "formal_launcher_git_blob_sha256": launcher_git_blob_sha256,
        "post_run_formal_launcher_git_blob_sha256": (
            post_launcher_blob_sha256
        ),
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
                "process_tree_drained": resource_receipt[
                    "process_tree_drained"
                ],
            }
            if resource_receipt
            else None
        ),
        "launcher_exception": launcher_exception_receipt,
        "progress": progress,
        "result_artifact": (
            {
                "path": artifacts[0].name,
                "file_sha256": artifact_file_sha256,
                "canonical_artifact_sha256": canonical_artifact_sha256,
                "embedded_artifact_sha256": embedded_artifact_sha256,
                "file_name_matches_content_sha256": (
                    canonical_artifact_sha256 == artifacts[0].stem
                ),
                "progress_artifact_sha256_matches": (
                    progress.get("artifact_sha256")
                    == canonical_artifact_sha256
                ),
            }
            if len(artifacts) == 1
            else None
        ),
        "result_available": result_available,
        "artifact_content_addressed": artifact_content_addressed,
        "independent_verification_complete": False,
        "statistical_interpretation_allowed": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
    }
    write_json(completion_path, completion)
    if not result_available:
        write_json(
            failure_path,
            {
                "schema_version": (
                    "ranked-liquidity-shallow-gbdt-unbounded-failure/v1"
                ),
                "observed_at_utc": finished_at,
                "classification": classification,
                "last_progress": progress,
                "resource_receipt_available": resource_receipt is not None,
                "launcher_exception_available": launcher_exception is not None,
                "launcher_exception": launcher_exception_receipt,
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
        exception_path.write_text(
            exception_text,
            encoding="utf-8",
        )
        failure_path = output_dir / "formal_run.failure.json"
        if not failure_path.exists():
            write_json(
                failure_path,
                {
                    "schema_version": (
                        "ranked-liquidity-shallow-gbdt-unbounded-failure/v1"
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
