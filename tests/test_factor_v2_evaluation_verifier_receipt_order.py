from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = (
    ROOT / "data" / "research_runs" / ".run_factor_v2_development_evaluation.py"
)
VERIFIER_V2_PATH = (
    ROOT
    / "scripts"
    / "verify_factor_v2_development_evaluation_v2.py"
)


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_receipt(runner: ModuleType) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "synthetic/v1",
        "artifact_sha256": "a" * 64,
        "manifest_file_sha256": "b" * 64,
        "factor_v2_spec_sha256": "c" * 64,
        "arm_order": list(runner.ARM_ORDER),
        "common_identity_root_sha256": "d" * 64,
        "arm_decisions": {arm: "RED" for arm in runner.ARM_ORDER},
        "automatic_winner_selected": False,
        "checks": dict(runner.EXPECTED_RECEIPT_CHECKS),
        "verified": True,
    }
    receipt["receipt_sha256"] = runner._canonical_sha256(receipt)
    return receipt


def test_sorted_json_receipt_is_normalized_without_changing_hash_or_input() -> None:
    runner_hash_before = hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest()
    runner = _load_module(RUNNER_PATH, "_synthetic_factor_v2_runner")
    verifier = _load_module(VERIFIER_V2_PATH, "_synthetic_factor_v2_verifier_v2")
    assert verifier.RUNS_ROOT == ROOT / "data" / "research_runs"
    assert verifier.RUNNER_PATH == RUNNER_PATH
    original = _synthetic_receipt(runner)
    reloaded = json.loads(json.dumps(original, sort_keys=True))

    assert tuple(reloaded["arm_decisions"]) != runner.ARM_ORDER
    with pytest.raises(RuntimeError, match="public evaluator receipt drifted"):
        runner._assert_public_receipt(reloaded)
    normalized = verifier._assert_public_receipt_order_insensitive(
        runner,
        reloaded,
    )

    assert tuple(normalized["arm_decisions"]) == runner.ARM_ORDER
    assert normalized["receipt_sha256"] == original["receipt_sha256"]
    assert tuple(reloaded["arm_decisions"]) != runner.ARM_ORDER
    assert hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest() == runner_hash_before


@pytest.mark.parametrize(
    "arm_decisions",
    [
        {"control": "RED", "overnight_20": "RED"},
        {
            "control": "RED",
            "overnight_20": "RED",
            "intraday_20": "RED",
            "unexpected": "RED",
        },
    ],
)
def test_receipt_normalizer_requires_exact_arm_key_set(
    arm_decisions: dict[str, str],
) -> None:
    runner = _load_module(RUNNER_PATH, "_synthetic_factor_v2_runner_bad_keys")
    verifier = _load_module(
        VERIFIER_V2_PATH,
        "_synthetic_factor_v2_verifier_v2_bad_keys",
    )
    receipt = _synthetic_receipt(runner)
    receipt["arm_decisions"] = arm_decisions

    with pytest.raises(RuntimeError, match="arm decision key set drifted"):
        verifier._assert_public_receipt_order_insensitive(runner, receipt)
