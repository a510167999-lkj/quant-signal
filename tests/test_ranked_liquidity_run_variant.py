from __future__ import annotations

import hashlib
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as shallow_gbdt
from app import audited_pit_shallow_gbdt_probability_budget as probability_budget
from app import audited_pit_shallow_gbdt_risk_on_breadth as risk_on_breadth
from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
)


def _sidecar_schemas(prefix: str, version: int) -> dict[str, str]:
    return {
        name: f"{prefix}-{name}-sidecar/v{version}"
        for name in ("features", "models", "execution", "selection")
    }


def test_resolved_ridge_rolling_variant_preserves_legacy_v3_contract():
    variant = ridge.resolve_ranked_liquidity_run_variant(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC
    )

    assert variant["strategy_schema_version"] == (
        "development-pit-cross-sectional-ranked-liquidity-"
        "ridge-rolling-oof/v3"
    )
    assert variant["progress_schema_version"] == (
        "ranked-liquidity-replay-progress/v3"
    )
    assert variant["producer_schema_version"] == (
        "audited-pit-ranked-liquidity-producer/v3"
    )
    assert variant["result_schema_version"] == (
        "ranked-liquidity-ridge-result/v3"
    )
    assert variant["sidecar_schema_versions"] == _sidecar_schemas(
        "ranked-liquidity", 3
    )
    assert variant["strict_outcome_schema_version"] == (
        "ranked-liquidity-ridge-strict-outcome/v3"
    )
    assert variant["sweep_schema_version"] == (
        "strict-ranked-liquidity-ridge-fixed-oof/v3"
    )
    assert variant["model_adapter"].model_id == "continuous_ridge"
    assert variant["main_rank_mode"] == "predicted_net_return"
    assert variant["baseline_rank_mode"] == "signal_date_amount"
    assert variant["score_contract"] == dict(RIDGE_SCORE_CONTRACT)
    assert variant["producer_binding"]() == ridge._producer_binding(
        artifact_version=3
    )


def test_resolved_shallow_gbdt_variant_uses_its_own_schema_family():
    variant = ridge.resolve_ranked_liquidity_run_variant(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
    )

    assert variant["strategy_schema_version"] == (
        "development-pit-cross-sectional-shallow-gbdt-"
        "utility-logit-rolling-126-oof/v1"
    )
    assert variant["progress_schema_version"] == (
        "ranked-liquidity-shallow-gbdt-replay-progress/v1"
    )
    assert variant["producer_schema_version"] == (
        "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
    )
    assert variant["result_schema_version"] == (
        "ranked-liquidity-shallow-gbdt-result/v1"
    )
    assert variant["sidecar_schema_versions"] == _sidecar_schemas(
        "ranked-liquidity-shallow-gbdt", 1
    )
    assert variant["strict_outcome_schema_version"] == (
        "ranked-liquidity-shallow-gbdt-strict-outcome/v1"
    )
    assert variant["sweep_schema_version"] == (
        "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
    )
    assert variant["model_adapter"].model_id == "shallow_gbdt_utility_logit"
    assert variant["main_rank_mode"] == "positive_utility_probability"
    assert variant["baseline_rank_mode"] == "signal_date_amount"
    assert variant["score_contract"] == dict(SHALLOW_GBDT_SCORE_CONTRACT)


def test_shallow_gbdt_producer_binding_binds_runtime_model_and_contract():
    variant = ridge.resolve_ranked_liquidity_run_variant(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
    )
    binding = variant["producer_binding"]()
    identity = {
        key: value for key, value in binding.items() if key != "root_sha256"
    }

    assert binding["schema_version"] == (
        "audited-pit-ranked-liquidity-shallow-gbdt-producer/v1"
    )
    assert binding["shared_ranked_liquidity_module_sha256"] == (
        hashlib.sha256(Path(ridge.__file__).read_bytes()).hexdigest()
    )
    assert binding["shallow_gbdt_module_sha256"] == (
        hashlib.sha256(Path(shallow_gbdt.__file__).read_bytes()).hexdigest()
    )
    assert binding["python_version"] == sys.version
    assert binding["numpy_version"] == np.__version__
    assert binding["pandas_version"] == pd.__version__
    assert binding["xgboost_version"] == "3.2.0"
    assert isinstance(binding["xgboost_build_info"], dict)
    assert binding["xgboost_build_info"]
    assert binding["xgboost_parameters"] == dict(
        shallow_gbdt.FROZEN_XGBOOST_PARAMS
    )
    assert binding["num_boost_round"] == shallow_gbdt.NUM_BOOST_ROUND
    assert binding["score_contract_sha256"] == ridge._sha256(
        dict(SHALLOW_GBDT_SCORE_CONTRACT)
    )
    assert binding["root_sha256"] == ridge._sha256(identity)


def test_probability_budget_variant_has_a_separate_role_bound_contract():
    variant = ridge.resolve_ranked_liquidity_run_variant(
        probability_budget.SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC
    )
    binding = variant["producer_binding"]()

    assert variant["strategy_schema_version"] == (
        "development-pit-cross-sectional-shallow-gbdt-probability-budget-"
        "utility-logit-rolling-126-oof/v1"
    )
    assert variant["model_adapter"].model_id == "shallow_gbdt_probability_budget"
    assert variant["main_rank_mode"] == "positive_utility_probability"
    assert variant["baseline_rank_mode"] == "positive_utility_probability"
    assert variant["main_apply_position_budget"] is True
    assert variant["baseline_apply_position_budget"] is False
    assert variant["control_name"] == "equal_weight_control"
    assert variant["selection_evidence_mode"] == "role_separated"
    assert binding["schema_version"] == (
        "audited-pit-ranked-liquidity-shallow-gbdt-probability-budget-"
        "producer/v1"
    )
    assert binding["probability_budget_module_sha256"] == hashlib.sha256(
        Path(probability_budget.__file__).read_bytes()
    ).hexdigest()
    assert binding["probability_budget_strategy_sha256"] == (
        probability_budget._SHALLOW_GBDT_PROBABILITY_BUDGET_OOF_SPEC_SHA256
    )


def test_risk_on_breadth_variant_and_producer_binding_are_content_bound():
    variant = ridge.resolve_ranked_liquidity_run_variant(
        risk_on_breadth.SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC
    )
    binding = variant["producer_binding"]()
    identity = {
        key: value for key, value in binding.items() if key != "root_sha256"
    }

    assert variant["model_adapter"].model_id == (
        "shallow_gbdt_risk_on_breadth"
    )
    assert variant["market_breadth_filter"] is True
    assert variant["sweep_schema_version"] == (
        "strict-ranked-liquidity-shallow-gbdt-risk-on-breadth-fixed-oof/v1"
    )
    assert binding["schema_version"] == (
        "audited-pit-ranked-liquidity-shallow-gbdt-risk-on-breadth-"
        "producer/v1"
    )
    assert binding["base_shallow_gbdt_producer_root_sha256"] == (
        ridge._shallow_gbdt_producer_binding()["root_sha256"]
    )
    assert binding["risk_on_breadth_module_sha256"] == hashlib.sha256(
        Path(risk_on_breadth.__file__).read_bytes()
    ).hexdigest()
    assert binding["risk_on_breadth_strategy_sha256"] == (
        risk_on_breadth._SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC_SHA256
    )
    assert binding["root_sha256"] == ridge._sha256(identity)

    tampered = dict(binding)
    tampered["risk_on_breadth_module_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="producer code changed"):
        ridge._assert_producer_binding_unchanged(tampered)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda spec: spec.update({"schema_version": "forged/v1"}),
        lambda spec: spec["selection"].update(
            {"score_contract": {**spec["selection"]["score_contract"], "value": 0.0}}
        ),
        lambda spec: spec["model"].update({"num_boost_round": 201}),
    ),
)
def test_shallow_gbdt_variant_rejects_any_strategy_or_contract_drift(
    mutation,
):
    spec = deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
    mutation(spec)

    with pytest.raises(ValueError, match="frozen"):
        ridge.resolve_ranked_liquidity_run_variant(spec)
