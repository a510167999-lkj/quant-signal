from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import run_shallow_gbdt_probability_budget_development_1 as launcher


def test_formal_probability_budget_run_spec_is_frozen_and_development_only() -> None:
    launcher._assert_frozen_run_spec()

    assert launcher.RUN_SPEC_SHA256 == (
        "bb676ebfc55a31334ac8be2f80f955fe837d8cd6b903c1d52ef0588272255daf"
    )
    assert launcher.EXPECTED_PRODUCER_ROOT_SHA256 == (
        "5e04d64e6719e25f49ac342556f529877e87e39bc7abe6efda7549c193fd9bca"
    )
    assert launcher.RUN_SPEC["command"] == (
        "research-audited-pit-ranked-liquidity-"
        "shallow-gbdt-probability-budget-rolling-oof"
    )
    assert launcher.RUN_SPEC["resource_contract"] == {
        "memory_policy": "unbounded",
        "enforcement": "none",
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
    assert launcher.PROGRESS_FILE_NAME == (
        ".ranked_liquidity_shallow_gbdt_probability_budget_v1_progress.json"
    )
    assert launcher.EXPECTED_PROGRESS_SCHEMA == (
        "ranked-liquidity-shallow-gbdt-probability-budget-"
        "replay-progress/v1"
    )
    assert launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA == (
        "ranked-liquidity-shallow-gbdt-probability-budget-"
        "result-bundle-verification/v1"
    )


def test_formal_probability_budget_command_has_only_frozen_inputs() -> None:
    arguments = launcher._command_arguments()

    assert arguments[:3] == ["-m", "app.jobs", launcher.RUN_SPEC["command"]]
    assert "--output-dir" in arguments
    assert arguments[-1] == str(launcher.RELATIVE_OUTPUT_DIR)
    assert "--start-date" in arguments
    assert arguments[arguments.index("--start-date") + 1] == "2024-07-05"
    assert "--end-date" in arguments
    assert arguments[arguments.index("--end-date") + 1] == "2026-07-03"


def test_unbounded_command_receipt_records_no_memory_enforcement(
    tmp_path: Path,
) -> None:
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"

    receipt = launcher.run_unbounded_command(
        command=[sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        environment=dict(os.environ),
    )

    assert receipt["exit_code"] == 0
    assert receipt["memory_limit_enforced"] is False
    assert receipt["child_reaped"] is True
    assert receipt["process_tree_drained"] is None
    assert receipt["process_tree_drain_verification"] == "not_performed"
    assert receipt["stderr"]["bytes"] == 0


def test_content_addressed_document_rejects_mismatched_file_name(tmp_path: Path) -> None:
    payload = {"schema_version": "synthetic/v1", "verified": True}
    digest = launcher.hashlib.sha256(launcher._canonical_bytes(payload)).hexdigest()
    good_path = tmp_path / f"{digest}.json"
    good_path.write_text(
        json.dumps({**payload, "artifact_sha256": digest}),
        encoding="utf-8",
    )

    assert launcher._content_addressed_document(good_path) is not None

    bad_path = tmp_path / ("0" * 64 + ".json")
    good_path.rename(bad_path)
    assert launcher._content_addressed_document(bad_path) is None


def test_runtime_verification_must_bind_the_main_artifact(tmp_path: Path) -> None:
    verification_dir = tmp_path / "verifications"
    verification_dir.mkdir()
    payload = {
        "schema_version": launcher.EXPECTED_RESULT_VERIFICATION_SCHEMA,
        "verified": True,
        "main_artifact_sha256": "a" * 64,
    }
    digest = launcher.hashlib.sha256(launcher._canonical_bytes(payload)).hexdigest()
    (verification_dir / f"{digest}.json").write_text(
        json.dumps({**payload, "artifact_sha256": digest}),
        encoding="utf-8",
    )

    verification, verified = launcher._runtime_verification(tmp_path)

    assert verified is True
    assert verification is not None
    assert verification["main_artifact_sha256"] == "a" * 64


def test_probability_budget_producer_binding_rejects_valid_format_drift(
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
        launcher.probability_budget_producer_binding(tmp_path / "python.exe")


def test_formal_probability_budget_run_spec_rejects_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(launcher.RUN_SPEC["scope"], "development_only", False)

    with pytest.raises(RuntimeError, match="run spec drifted"):
        launcher._assert_frozen_run_spec()
