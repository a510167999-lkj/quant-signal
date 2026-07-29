from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app import factor_v2_decision_verification_receipt_publisher as publisher
from app.factor_v2_decision_verification_receipt_publisher import (
    EXPECTED_RECEIPT_CHECKS,
    PublisherConfig,
    canonical_sha256,
    publish_decision_verification_receipt,
)


ARM_ORDER = ["v2_control", "overnight_20", "intraday_20"]
FACTOR_V2_SPEC_SHA256 = "1" * 64
FROZEN_COMMIT = "2" * 40
PREREG_COMMIT = "a" * 40
BINDING_COMMIT = "b" * 40


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _claim_sha256(
    *,
    pid: int,
    started_at: str,
    token: str,
) -> str:
    payload = {
        "operation": "build_factor_v2_development_evaluation",
        "pid": pid,
        "started_at": started_at,
        "token": token,
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _verification_receipt(
    artifact_sha256: str,
    manifest_file_sha256: str,
    decisions: dict[str, str],
) -> dict[str, Any]:
    unsigned = {
        "schema_version": (
            "audited-pit-factor-v2-development-evaluation-verification/v1"
        ),
        "artifact_sha256": artifact_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
        "arm_order": ARM_ORDER,
        "common_identity_root_sha256": "3" * 64,
        "arm_decisions": decisions,
        "automatic_winner_selected": False,
        "checks": EXPECTED_RECEIPT_CHECKS,
        "verified": True,
    }
    return {**unsigned, "receipt_sha256": canonical_sha256(unsigned)}


def _valid_case(
    tmp_path: Path,
    *,
    decisions: dict[str, str] | None = None,
) -> tuple[PublisherConfig, dict[str, Any]]:
    decisions = decisions or {
        "v2_control": "RED",
        "overnight_20": "RED",
        "intraday_20": "RED",
    }
    runs_root = tmp_path / "runs"
    output_dir = runs_root / "evaluation"
    build_status_path = runs_root / "evaluation.run.status.json"
    verify_status_path = runs_root / "evaluation.verify.status.json"
    evaluation_lock_path = runs_root / ".evaluation.lock"
    receipt_output_dir = runs_root / "decision_receipt"
    publish_status_path = runs_root / "decision_receipt.publish.status.json"
    publish_claim_path = runs_root / "decision_receipt.publish.claim"
    prereg_path = tmp_path / "prereg.json"
    prereg_sidecar_path = tmp_path / "prereg.sha256"
    producer_binding_path = tmp_path / "producer_binding.json"
    producer_binding_sidecar_path = tmp_path / "producer_binding.sha256"
    runner_path = tmp_path / "runner.py"
    verifier_path = tmp_path / "verifier.py"
    frozen_source_root = tmp_path / "frozen"
    frozen_source_root.mkdir(parents=True)
    runner_path.write_bytes(b"runner\n")
    verifier_path.write_bytes(b"verifier\n")

    producer_identity = {
        "schema_version": (
            "audited-pit-factor-v2-development-evaluation-producer/v1"
        ),
        "module_sha256": {"module.py": "4" * 64},
        "python_version": "test",
        "pandas_version": "test",
        "sqlite_version": "test",
        "loaded_entrypoints": {},
    }
    producer_binding = {
        **producer_identity,
        "root_sha256": canonical_sha256(producer_identity),
    }
    manifest_unsigned = {
        "schema_version": (
            "audited-pit-factor-v2-development-evaluation/v1"
        ),
        "evaluation_producer_binding": producer_binding,
        "temporal_role": "development",
        "factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
        "arm_order": ARM_ORDER,
        "evaluation_contract": {},
        "source_binding": {
            "arms": {
                arm: {"arm": arm, "artifact_sha256": str(index) * 64}
                for index, arm in enumerate(ARM_ORDER, start=5)
            }
        },
        "evaluation_sessions": [],
        "evaluation_sessions_sha256": canonical_sha256([]),
        "common_identity": {
            "common_identity_root_sha256": "3" * 64,
            "expected_score_row_count": 1_511_000,
            "observed_arm_score_row_counts": {
                arm: 1_511_000 for arm in ARM_ORDER
            },
            "all_three_arms_exact_identity": True,
        },
        "arms": {
            arm: {
                "arm": arm,
                "preregistered_decision": decisions[arm],
            }
            for arm in ARM_ORDER
        },
        "comparison": {
            "baseline_arm": "v2_control",
            "automatic_winner_selected": False,
        },
        "scope": {
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "automatic_winner_selected": False,
        "production_profile_registered": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    artifact_sha256 = canonical_sha256(manifest_unsigned)
    manifest = {
        **manifest_unsigned,
        "artifact_sha256": artifact_sha256,
    }
    manifest_path = output_dir / f"{artifact_sha256}.json"
    _write_json(manifest_path, manifest)
    manifest_file_sha256 = _file_sha256(manifest_path)
    verification_receipt = _verification_receipt(
        artifact_sha256,
        manifest_file_sha256,
        decisions,
    )
    manifest_summary = {
        "artifact_sha256": artifact_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "manifest_path": str(manifest_path),
        "arm_order": ARM_ORDER,
        "arm_decisions": decisions,
        "expected_common_score_row_count": 1_511_000,
        "observed_arm_score_row_counts": {
            arm: 1_511_000 for arm in ARM_ORDER
        },
        "all_three_arms_exact_identity": True,
        "automatic_winner_selected": False,
        "production_profile_registered": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    build_pid = 111
    verify_pid = 222
    started_at = "2026-01-02T03:04:05+00:00"
    token = "build-token"
    runner_sha256 = _file_sha256(runner_path)
    verifier_sha256 = _file_sha256(verifier_path)
    claim_sha256 = _claim_sha256(
        pid=build_pid,
        started_at=started_at,
        token=token,
    )
    request = {
        "source_root": str(frozen_source_root),
        "expected_source_commit": FROZEN_COMMIT,
        "arm_order": ARM_ORDER,
        "expected_common_score_row_count": 1_511_000,
        "output_dir": str(output_dir),
        "status_path": str(build_status_path),
        "evaluation_lock_path": str(evaluation_lock_path),
    }
    build_result = {
        **verification_receipt,
        "manifest_path": str(manifest_path),
    }
    build_status = {
        "schema_version": "factor-v2-development-evaluation-run-status/v1",
        "status": "completed",
        "stage": "completed",
        "pid": build_pid,
        "started_at": started_at,
        "finished_at": "2026-01-02T04:04:05+00:00",
        "run_token": token,
        "runner_file_sha256": runner_sha256,
        "request": request,
        "preflight": {},
        "result": build_result,
        "manifest_summary": manifest_summary,
        "independent_verification_started": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    _write_json(build_status_path, build_status)
    verify_status = {
        "schema_version": (
            "factor-v2-development-evaluation-independent-"
            "verification-status/v1"
        ),
        "status": "completed",
        "stage": "completed",
        "pid": verify_pid,
        "started_at": "2026-01-02T04:05:00+00:00",
        "finished_at": "2026-01-02T05:05:00+00:00",
        "run_token": "verify-token",
        "runner_file_sha256": runner_sha256,
        "verifier_file_sha256": verifier_sha256,
        "build_status_path": str(build_status_path),
        "build_status_file_sha256": _file_sha256(build_status_path),
        "request": request,
        "receipt": verification_receipt,
        "checks": EXPECTED_RECEIPT_CHECKS,
        "manifest_summary": manifest_summary,
        "verified": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    _write_json(verify_status_path, verify_status)

    receipt_fields = [
        "schema_version",
        "temporal_role",
        "factor_v2_spec_sha256",
        "evaluation_artifact_sha256",
        "evaluation_manifest_file_sha256",
        "evaluation_producer_root_sha256",
        "verification_producer_root_sha256",
        "arm_order",
        "arm_decisions",
        "source_run_identity",
        "verified",
        "embargo_consumed",
        "final_oos_consumed",
        "production_recommendation_eligible",
        "receipt_sha256",
    ]
    source_run_identity = {
        "status_path": build_status_path.as_posix(),
        "pid": build_pid,
        "started_at": started_at,
        "runner_file_sha256": runner_sha256,
        "claim_file_sha256": claim_sha256,
        "lock_file_sha256": claim_sha256,
    }
    prereg = {
        "schema_version": (
            "factor-v2-low-rvol20-rank-overlay-dormant-"
            "preregistration/v2"
        ),
        "activation_policy": {
            "required_verified_arm_order": ARM_ORDER,
            "decision_verification_receipt_schema_version": (
                "factor-v2-development-evaluation-decision-"
                "verification-receipt/v1"
            ),
            "decision_verification_receipt_exact_fields": receipt_fields,
            "decision_verification_receipt_extra_fields_allowed": False,
            "decision_verification_receipt_required_values": {
                "temporal_role": "development",
                "factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
                "arm_order": ARM_ORDER,
                "verified": True,
                "embargo_consumed": False,
                "final_oos_consumed": False,
                "production_recommendation_eligible": False,
            },
            "decision_verification_receipt_source_run_identity_exact_fields": (
                list(source_run_identity)
            ),
            "decision_verification_receipt_source_run_identity_required_values": (
                source_run_identity
            ),
        },
        "source_binding": {
            "factor_v2_spec_sha256": FACTOR_V2_SPEC_SHA256,
            "development_evaluator_source_commit": FROZEN_COMMIT,
        },
    }
    _write_json(prereg_path, prereg)
    prereg_sidecar_path.write_bytes(
        (
            f"{_file_sha256(prereg_path)}  {prereg_path.name}\n"
        ).encode("ascii")
    )
    verification_producer_unsigned = {
        "schema_version": (
            "factor-v2-development-evaluation-decision-verification-"
            "receipt-publisher-binding/v1"
        ),
        "preregistration_introducing_commit": PREREG_COMMIT,
        "preregistration_raw_sha256": _file_sha256(prereg_path),
        "publisher_file_sha256": _file_sha256(Path(publisher.__file__)),
        "runner_file_sha256": runner_sha256,
        "verifier_file_sha256": verifier_sha256,
        "verifier_introducing_commit": (
            publisher.EXPECTED_VERIFIER_INTRODUCING_COMMIT
        ),
        "superseded_verifier_file_sha256": (
            publisher.SUPERSEDED_VERIFIER_FILE_SHA256
        ),
        "superseded_verifier_use_allowed": False,
        "superseded_verifier_reason_code": (
            "nested_arm_decisions_order_changed_by_sorted_status_json"
        ),
        "noncanonical_verifier_copy_file_sha256": (
            publisher.NONCANONICAL_VERIFIER_COPY_FILE_SHA256
        ),
        "noncanonical_verifier_copy_use_allowed": False,
        "frozen_source_commit": FROZEN_COMMIT,
        "evaluation_producer_root_sha256": producer_binding[
            "root_sha256"
        ],
        "decision_receipt_schema_version": (
            "factor-v2-development-evaluation-decision-"
            "verification-receipt/v1"
        ),
        "evaluation_manifest_schema_version": (
            "audited-pit-factor-v2-development-evaluation/v1"
        ),
        "evaluation_verification_receipt_schema_version": (
            "audited-pit-factor-v2-development-evaluation-verification/v1"
        ),
        "build_status_schema_version": (
            "factor-v2-development-evaluation-run-status/v1"
        ),
        "verify_status_schema_version": (
            "factor-v2-development-evaluation-independent-"
            "verification-status/v1"
        ),
        "canonicalization": (
            "receipt-json-utf8-sort-keys-compact-no-nan-no-newline;"
            "binding-same-plus-one-lf/v1"
        ),
    }
    verification_producer = {
        **verification_producer_unsigned,
        "verification_producer_root_sha256": canonical_sha256(
            verification_producer_unsigned
        ),
    }
    producer_binding_path.write_bytes(
        json.dumps(
            verification_producer,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    producer_binding_raw_sha256 = _file_sha256(producer_binding_path)
    producer_binding_sidecar_path.write_bytes(
        (
            f"{producer_binding_raw_sha256}  "
            f"{producer_binding_path.name}\n"
        ).encode("ascii")
    )
    config = PublisherConfig(
        project_root=tmp_path,
        preregistration_path=prereg_path,
        preregistration_sidecar_path=prereg_sidecar_path,
        expected_preregistration_raw_sha256=_file_sha256(prereg_path),
        expected_preregistration_introducing_commit=PREREG_COMMIT,
        producer_binding_path=producer_binding_path,
        producer_binding_sidecar_path=producer_binding_sidecar_path,
        expected_producer_binding_raw_sha256=(
            producer_binding_raw_sha256
        ),
        expected_producer_binding_introducing_commit=BINDING_COMMIT,
        runner_path=runner_path,
        expected_runner_file_sha256=runner_sha256,
        verifier_path=verifier_path,
        expected_verifier_file_sha256=verifier_sha256,
        frozen_source_root=frozen_source_root,
        expected_frozen_source_commit=FROZEN_COMMIT,
        expected_evaluation_producer_root_sha256=producer_binding[
            "root_sha256"
        ],
        build_status_path=build_status_path,
        verify_status_path=verify_status_path,
        evaluation_lock_path=evaluation_lock_path,
        receipt_output_dir=receipt_output_dir,
        publish_status_path=publish_status_path,
        publish_claim_path=publish_claim_path,
    )
    return config, {
        "artifact_sha256": artifact_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "producer_root_sha256": producer_binding["root_sha256"],
        "decisions": decisions,
        "source_run_identity": source_run_identity,
    }


def _clean_git_output(_root: Path, *args: str) -> str:
    if args == ("rev-parse", "HEAD"):
        return FROZEN_COMMIT
    if args == ("rev-parse", f"{BINDING_COMMIT}^{{commit}}"):
        return BINDING_COMMIT
    if args == ("rev-parse", f"{PREREG_COMMIT}^{{commit}}"):
        return PREREG_COMMIT
    if args[:3] == ("log", "--diff-filter=A", "--format=%H"):
        path = args[-1]
        return PREREG_COMMIT if path.startswith("prereg.") else BINDING_COMMIT
    if args == (
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ):
        return ""
    raise AssertionError(args)


def _anchored_git_file_bytes(
    root: Path,
    commit: str,
    relative_path: str,
) -> bytes:
    assert commit in {BINDING_COMMIT, PREREG_COMMIT}
    return (root / relative_path).read_bytes()


def test_publish_exact_content_addressed_receipt(tmp_path: Path) -> None:
    decisions = {
        "v2_control": "GREEN",
        "overnight_20": "RED",
        "intraday_20": "GREEN",
    }
    config, expected = _valid_case(tmp_path, decisions=decisions)

    descriptor = publish_decision_verification_receipt(
        config,
        process_is_running=lambda _pid: False,
        git_output=_clean_git_output,
        git_file_bytes=_anchored_git_file_bytes,
    )

    receipt_path = Path(descriptor["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    prereg = json.loads(
        config.preregistration_path.read_text(encoding="utf-8")
    )
    exact_fields = prereg["activation_policy"][
        "decision_verification_receipt_exact_fields"
    ]
    assert list(receipt) == sorted(exact_fields)
    assert set(receipt) == set(exact_fields)
    assert receipt["arm_decisions"] == decisions
    assert (
        receipt["evaluation_artifact_sha256"]
        == expected["artifact_sha256"]
    )
    assert (
        receipt["evaluation_manifest_file_sha256"]
        == expected["manifest_file_sha256"]
    )
    assert (
        receipt["evaluation_producer_root_sha256"]
        == expected["producer_root_sha256"]
    )
    assert receipt["source_run_identity"] == expected["source_run_identity"]
    unsigned = dict(receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    assert canonical_sha256(unsigned) == receipt_sha256
    raw_sha256 = _file_sha256(receipt_path)
    assert receipt_path.name == f"{raw_sha256}.json"
    assert descriptor["receipt"]["raw_file_sha256"] == raw_sha256
    assert descriptor["receipt"]["receipt_sha256"] == receipt_sha256
    terminal = json.loads(
        config.publish_status_path.read_text(encoding="utf-8")
    )
    assert terminal["status"] == "completed"
    assert terminal["stage"] == "completed"
    assert terminal["receipt"] == descriptor["receipt"]
    assert terminal["verification_producer"][
        "verification_producer_root_sha256"
    ] == receipt["verification_producer_root_sha256"]
    assert not config.publish_claim_path.exists()


@pytest.mark.parametrize(
    ("target", "value", "error"),
    [
        ("build_status.stage", "evaluating", "build completion"),
        ("verify_status.verified", False, "verification completion"),
        (
            "verify_status.checks.content_addressing_verified",
            False,
            "verification completion",
        ),
        (
            "build_status.production_profile_registered",
            True,
            "build completion",
        ),
        (
            "manifest.final_oos_consumed",
            True,
            "manifest",
        ),
    ],
)
def test_publish_fails_closed_on_drift(
    tmp_path: Path,
    target: str,
    value: Any,
    error: str,
) -> None:
    config, _ = _valid_case(tmp_path)
    parts = target.split(".")
    if parts[0] == "manifest":
        build = json.loads(
            config.build_status_path.read_text(encoding="utf-8")
        )
        manifest_path = Path(build["result"]["manifest_path"])
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload[parts[1]] = value
        _write_json(manifest_path, payload)
    else:
        status_path = (
            config.build_status_path
            if parts[0] == "build_status"
            else config.verify_status_path
        )
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        cursor: dict[str, Any] = payload
        for key in parts[1:-1]:
            cursor = cursor[key]
        cursor[parts[-1]] = value
        _write_json(status_path, payload)

    with pytest.raises(RuntimeError, match=error):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=_clean_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )
    assert not config.receipt_output_dir.exists()
    assert not config.publish_status_path.exists()
    assert not config.publish_claim_path.exists()


def test_publish_rejects_live_pid_and_existing_lock(tmp_path: Path) -> None:
    config, _ = _valid_case(tmp_path)
    with pytest.raises(RuntimeError, match="process has not exited"):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda pid: pid == 111,
            git_output=_clean_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )
    config.evaluation_lock_path.write_text("held", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lock or claim"):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=_clean_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )


def test_publish_rejects_dirty_or_wrong_frozen_source(tmp_path: Path) -> None:
    config, _ = _valid_case(tmp_path)

    def dirty_git_output(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return FROZEN_COMMIT
        return "?? unexpected.py"

    with pytest.raises(RuntimeError, match="dirty"):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=dirty_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )

    def wrong_git_output(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "0" * 40
        return ""

    with pytest.raises(RuntimeError, match="commit"):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=wrong_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )


def test_publish_rejects_preregistration_or_receipt_mismatch(
    tmp_path: Path,
) -> None:
    config, _ = _valid_case(tmp_path)
    prereg = json.loads(
        config.preregistration_path.read_text(encoding="utf-8")
    )
    prereg["activation_policy"][
        "decision_verification_receipt_source_run_identity_required_values"
    ]["claim_file_sha256"] = "9" * 64
    _write_json(config.preregistration_path, prereg)
    with pytest.raises(
        RuntimeError,
        match="preregistration (identity|Git anchor)",
    ):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=_clean_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )

    config, _ = _valid_case(tmp_path / "second")
    verify = json.loads(
        config.verify_status_path.read_text(encoding="utf-8")
    )
    verify["receipt"]["arm_decisions"]["intraday_20"] = "GREEN"
    unsigned_verify_receipt = dict(verify["receipt"])
    unsigned_verify_receipt.pop("receipt_sha256")
    verify["receipt"]["receipt_sha256"] = canonical_sha256(
        unsigned_verify_receipt
    )
    _write_json(config.verify_status_path, verify)
    with pytest.raises(RuntimeError, match="build and verification receipts"):
        publish_decision_verification_receipt(
            config,
            process_is_running=lambda _pid: False,
            git_output=_clean_git_output,
            git_file_bytes=_anchored_git_file_bytes,
        )
