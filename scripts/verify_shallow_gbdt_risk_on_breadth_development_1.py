from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[1]
RUN_ROOT_RELATIVE = Path(
    "data/research_runs/"
    "audited_pit_ranked_liquidity_shallow_gbdt_risk_on_breadth_"
    "rolling126_oof_v1_development_1_unbounded_formal_local_research"
)
EXPECTED_STRATEGY_SHA256 = (
    "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
)
EXPECTED_PRODUCER_ROOT_SHA256 = (
    "bb833d0bc91720b3bf46b204ffb4d8615a9ec4530b7734d48ee3663bcd1d753f"
)
EXPECTED_RESULT_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-result/v1"
)
EXPECTED_VERIFICATION_SCHEMA = (
    "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
    "result-bundle-verification/v1"
)
REQUIRED_VERIFICATION_CHECKS = frozenset(
    {
        "independent_rolling_oof_replay",
        "content_addressing_verified",
        "probability_score_contract_verified",
        "shared_positive_candidate_pool_verified",
        "strict_outcome_membership_verified",
        "independent_selection_replay",
        "independent_sweep_and_gate_replay",
        "market_breadth_filter_replayed",
    }
)
RESULT_BUNDLE_SIDECAR_SCHEMAS = {
    name: (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        f"{name}-sidecar/v1"
    )
    for name in ("features", "models", "execution", "selection")
}
REPLAY_PLAN: dict[str, Any] = {
    "schema_version": (
        "ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        "independent-replay-plan/v1"
    ),
    "command": (
        "research-audited-pit-ranked-liquidity-"
        "shallow-gbdt-risk-on-breadth-rolling-oof"
    ),
    "execution": {
        "entrypoint": "app.jobs",
        "direct_callable_allowed": False,
    },
    "scope": {
        "point_in_time": True,
        "development_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_authority": False,
        "automatic_trading_authority": False,
    },
}
ISOLATED_REPLAY_DRIVER = ""


def _jobs_argv(output_dir: str) -> list[str]:
    raise NotImplementedError


def _verify_runtime_verification(
    run_root: Path,
    *,
    expected_main_sha256: str,
    expected_sidecar_sha256: Mapping[str, str],
) -> dict[str, Any]:
    raise NotImplementedError


def _load_content_addressed_result_bundle(
    run_root: Path,
    *,
    runtime_verification: Mapping[str, Any],
) -> dict[str, Any]:
    raise NotImplementedError


def _validate_replay(
    inputs: Mapping[str, Any],
    replay: Mapping[str, Any],
    execution_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    raise NotImplementedError


def _receipt_body(*, verification_sha256: str) -> dict[str, Any]:
    raise NotImplementedError


def _status_payload(
    *,
    verified: bool,
    receipt_sha256: str | None,
    error_type: str | None,
) -> dict[str, Any]:
    raise NotImplementedError
