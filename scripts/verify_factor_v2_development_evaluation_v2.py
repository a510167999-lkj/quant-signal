from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO_ROOT / "data" / "research_runs"
SOURCE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v2-eval-3e9bd1b"
)
EXPECTED_SOURCE_COMMIT = (
    "3e9bd1bcf12024f9bf52a0b0fbcdd86f7bc64109"
)
RUNNER_PATH = RUNS_ROOT / ".run_factor_v2_development_evaluation.py"
OUTPUT_DIR = (
    RUNS_ROOT
    / "audited_pit_factor_v2_development_evaluation_v1_development_4"
)
BUILD_STATUS_PATH = OUTPUT_DIR.with_name(
    f"{OUTPUT_DIR.name}.run.status.json"
)
VERIFY_STATUS_PATH = OUTPUT_DIR.with_name(
    f"{OUTPUT_DIR.name}.verify.status.json"
)
VERIFY_CLAIM_PATH = VERIFY_STATUS_PATH.with_name(
    f"{VERIFY_STATUS_PATH.name}.claim"
)
EVALUATION_LOCK_PATH = (
    RUNS_ROOT / ".factor_v2_development_evaluation.lock"
)
VERIFY_STATUS_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-independent-verification-status/v1"
)
BUILD_STATUS_SCHEMA_VERSION = (
    "factor-v2-development-evaluation-run-status/v1"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, field: str) -> dict[str, Any]:
    metadata = path.stat(follow_symlinks=False)
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{field} is not a direct regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{field} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{field} must contain a JSON object")
    return payload


def _git_output(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(SOURCE_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _verify_source_before_local_import() -> None:
    if not SOURCE_ROOT.is_dir() or SOURCE_ROOT.is_symlink():
        raise RuntimeError("frozen evaluator worktree is unavailable")
    if _git_output("rev-parse", "HEAD") != EXPECTED_SOURCE_COMMIT:
        raise RuntimeError("frozen evaluator source commit drifted")
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("frozen evaluator source worktree is dirty")


def _verify_interpreter_before_local_import() -> None:
    if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
        raise RuntimeError("launch requires Python -I -B")
    if any(name == "app" or name.startswith("app.") for name in sys.modules):
        raise RuntimeError("app was imported before frozen-source validation")


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_factor_v2_development_evaluation_runner",
        RUNNER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the frozen evaluation runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_public_receipt_order_insensitive(
    runner: ModuleType,
    receipt: Any,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        return runner._assert_public_receipt(receipt)
    normalized = copy.deepcopy(receipt)
    arm_decisions = normalized.get("arm_decisions")
    if not isinstance(arm_decisions, dict):
        return runner._assert_public_receipt(normalized)
    arm_order = tuple(runner.ARM_ORDER)
    if (
        len(arm_decisions) != len(arm_order)
        or set(arm_decisions) != set(arm_order)
    ):
        raise RuntimeError("public evaluator receipt arm decision key set drifted")
    normalized["arm_decisions"] = {
        arm: arm_decisions[arm]
        for arm in arm_order
    }
    return runner._assert_public_receipt(normalized)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Independently replay and verify the completed frozen factor-v2 "
            "development evaluation. This command must run only after the "
            "build process has exited."
        )
    )
    return parser.parse_args()


def main() -> int:
    _arguments()
    _verify_interpreter_before_local_import()
    _verify_source_before_local_import()
    if RUNNER_PATH.is_symlink() or not RUNNER_PATH.is_file():
        raise RuntimeError("evaluation runner is not a direct regular file")

    runner_file_sha256 = _file_sha256(RUNNER_PATH)
    build_status = _read_json(BUILD_STATUS_PATH, "evaluation build status")
    build_status_file_sha256 = _file_sha256(BUILD_STATUS_PATH)
    if (
        build_status.get("schema_version")
        != BUILD_STATUS_SCHEMA_VERSION
        or build_status.get("status") != "completed"
        or build_status.get("stage") != "completed"
        or build_status.get("runner_file_sha256")
        != runner_file_sha256
        or build_status.get("independent_verification_started") is not False
        or build_status.get("embargo_consumed") is not False
        or build_status.get("final_oos_consumed") is not False
        or build_status.get("production_profile_registered") is not False
        or build_status.get("production_recommendation_eligible") is not False
    ):
        raise RuntimeError("evaluation build completion status drifted")
    runner = _load_runner()
    runner._verify_interpreter()
    runner._verify_source()
    if (
        runner.SOURCE_ROOT != SOURCE_ROOT
        or runner.EXPECTED_SOURCE_COMMIT != EXPECTED_SOURCE_COMMIT
        or runner.RUN_STATUS_SCHEMA_VERSION
        != BUILD_STATUS_SCHEMA_VERSION
    ):
        raise RuntimeError("evaluation runner frozen binding drifted")
    build_pid = build_status.get("pid")
    if runner._process_is_running(build_pid):
        raise RuntimeError("evaluation build process has not exited")
    if runner._entry_exists(EVALUATION_LOCK_PATH):
        raise RuntimeError("shared evaluation lock is still present")
    request = build_status.get("request")
    if not isinstance(request, dict):
        raise RuntimeError("evaluation build request is missing")
    runner._assert_request_shape(request)
    initial_preflight = runner._preflight_request(request)
    if build_status.get("preflight") != initial_preflight:
        raise RuntimeError("evaluation build preflight evidence drifted")

    build_result = build_status.get("result")
    if not isinstance(build_result, dict):
        raise RuntimeError("evaluation build result is missing")
    build_receipt = _assert_public_receipt_order_insensitive(
        runner,
        build_result,
    )
    build_summary = runner._assert_evaluation_manifest(
        Path(str(build_result.get("manifest_path", ""))),
        build_receipt,
        request,
    )
    if build_status.get("manifest_summary") != build_summary:
        raise RuntimeError("evaluation build manifest summary drifted")

    started_at = _utc_now()
    token = uuid.uuid4().hex
    verifier_file_sha256 = _file_sha256(Path(__file__).resolve())
    claim_payload = {
        "operation": "verify_factor_v2_development_evaluation",
        "pid": os.getpid(),
        "started_at": started_at,
        "token": token,
    }
    running_status = {
        "schema_version": VERIFY_STATUS_SCHEMA_VERSION,
        "status": "running",
        "stage": "claiming_shared_evaluation_lock",
        "pid": os.getpid(),
        "started_at": started_at,
        "run_token": token,
        "runner_file_sha256": runner_file_sha256,
        "verifier_file_sha256": verifier_file_sha256,
        "build_status_path": str(BUILD_STATUS_PATH),
        "build_status_file_sha256": build_status_file_sha256,
        "request": request,
    }
    runner._acquire_claim(VERIFY_CLAIM_PATH, claim_payload)
    lock_acquired = False
    status_owned = False
    stdout_payload: dict[str, Any] | None = None
    try:
        if runner._entry_exists(VERIFY_STATUS_PATH):
            raise FileExistsError(
                f"verification status already exists: {VERIFY_STATUS_PATH}"
            )
        runner._acquire_claim(EVALUATION_LOCK_PATH, claim_payload)
        lock_acquired = True
        if runner._entry_exists(VERIFY_STATUS_PATH):
            raise FileExistsError(
                "verification status appeared while acquiring shared lock"
            )
        running_status["stage"] = "preflight"
        runner._write_new_status(VERIFY_STATUS_PATH, running_status)
        status_owned = True

        runner._verify_source()
        if runner._process_is_running(build_pid):
            raise RuntimeError("evaluation build process restarted or was reused")
        if _file_sha256(BUILD_STATUS_PATH) != build_status_file_sha256:
            raise RuntimeError("evaluation build status changed")
        if _file_sha256(RUNNER_PATH) != runner_file_sha256:
            raise RuntimeError("evaluation runner changed")
        if _file_sha256(Path(__file__).resolve()) != verifier_file_sha256:
            raise RuntimeError("independent verifier changed")
        if runner._preflight_request(request) != initial_preflight:
            raise RuntimeError("evaluation source preflight evidence changed")

        sys.path.insert(0, str(runner.SOURCE_ROOT))
        from app import audited_pit_factor_v2_development_evaluation as evaluation

        if Path(evaluation.__file__).resolve().parent.parent != (
            runner.SOURCE_ROOT.resolve()
        ):
            raise RuntimeError(
                "evaluation module was not imported from frozen source"
            )
        if tuple(evaluation.FACTOR_V2_ARM_ORDER) != runner.ARM_ORDER:
            raise RuntimeError("frozen evaluator arm order drifted")
        arm_paths, arm_artifacts, arm_manifest_hashes = (
            runner._public_call_arguments(request)
        )
        running_status["stage"] = "independent_source_replay"
        runner._replace_status(
            VERIFY_STATUS_PATH,
            running_status,
            token,
        )
        receipt = evaluation.verify_factor_v2_development_evaluation(
            build_result["manifest_path"],
            parent_materialization_manifest_path=Path(
                request["parent"]["manifest_path"]
            ),
            overlay_manifest_path=Path(
                request["overlay"]["manifest_path"]
            ),
            suspension_metadata_path=Path(
                request["suspension_metadata_path"]
            ),
            control_gate_manifest_path=Path(
                request["gate"]["manifest_path"]
            ),
            control_oracle_database_path=Path(
                request["control_oracle_database_path"]
            ),
            arm_manifest_paths=arm_paths,
            expected_artifact_sha256=build_result["artifact_sha256"],
            expected_manifest_file_sha256=build_result[
                "manifest_file_sha256"
            ],
            expected_overlay_artifact_sha256=request["overlay"][
                "expected_artifact_sha256"
            ],
            expected_overlay_manifest_file_sha256=request["overlay"][
                "expected_manifest_file_sha256"
            ],
            expected_gate_artifact_sha256=request["gate"][
                "expected_artifact_sha256"
            ],
            expected_gate_manifest_file_sha256=request["gate"][
                "expected_manifest_file_sha256"
            ],
            expected_arm_artifact_sha256=arm_artifacts,
            expected_arm_manifest_file_sha256=arm_manifest_hashes,
        )
        receipt = _assert_public_receipt_order_insensitive(runner, receipt)
        if receipt != build_receipt:
            raise RuntimeError(
                "independent verification receipt differs from build receipt"
            )
        manifest_summary = runner._assert_evaluation_manifest(
            Path(build_result["manifest_path"]),
            receipt,
            request,
        )
        if manifest_summary != build_summary:
            raise RuntimeError(
                "independent evaluation manifest summary drifted"
            )
        runner._verify_source()
        if runner._preflight_request(request) != initial_preflight:
            raise RuntimeError("evaluation source changed during verification")
        if (
            _file_sha256(BUILD_STATUS_PATH) != build_status_file_sha256
            or _file_sha256(RUNNER_PATH) != runner_file_sha256
            or _file_sha256(Path(__file__).resolve())
            != verifier_file_sha256
        ):
            raise RuntimeError(
                "verification inputs changed during independent replay"
            )
        completed_status = {
            "schema_version": VERIFY_STATUS_SCHEMA_VERSION,
            "status": "completed",
            "stage": "completed",
            "pid": os.getpid(),
            "started_at": started_at,
            "finished_at": _utc_now(),
            "run_token": token,
            "runner_file_sha256": runner_file_sha256,
            "verifier_file_sha256": verifier_file_sha256,
            "build_status_path": str(BUILD_STATUS_PATH),
            "build_status_file_sha256": build_status_file_sha256,
            "request": request,
            "receipt": receipt,
            "checks": receipt["checks"],
            "manifest_summary": manifest_summary,
            "verified": True,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_profile_registered": False,
            "production_recommendation_eligible": False,
        }
        stdout_payload = {
            "artifact_sha256": receipt["artifact_sha256"],
            "checks": receipt["checks"],
            "receipt_sha256": receipt["receipt_sha256"],
            "status": "completed",
            "verified": True,
        }
        runner._replace_status(
            VERIFY_STATUS_PATH,
            completed_status,
            token,
        )
    except BaseException as exc:
        runner._print_traceback_safely()
        if status_owned:
            failure_status = {
                **running_status,
                "status": "failed",
                "stage": "failed",
                "error_type": type(exc).__name__,
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": _utc_now(),
            }
            runner._record_failed_terminal_status(
                VERIFY_STATUS_PATH,
                failure_status,
                token,
            )
        raise
    finally:
        if lock_acquired:
            runner._release_claim(EVALUATION_LOCK_PATH, token)
        runner._release_claim(VERIFY_CLAIM_PATH, token)
    if stdout_payload is not None:
        try:
            print(
                json.dumps(
                    stdout_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        except BaseException:
            runner._print_traceback_safely()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
