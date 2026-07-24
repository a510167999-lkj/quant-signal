"""RED contract for the frozen shallow-GBDT result bundle."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as shallow_gbdt


def _candidate() -> dict:
    return {
        "candidate_key": "candidate-1",
        "signal_date": "2025-01-02",
        "security_id": "cn-a-share:000001.SZ",
        "symbol": "000001.SZ",
        "signal_industry": "industry-a",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-10",
        "candidate_amount": 100.0,
        "right_censored": False,
        "return_pct": 1.25,
        "predicted_positive_utility_probability": 0.75,
        "score": 0.75,
        "rank_score": 0.75,
    }


def _shared_receipts() -> dict:
    candidate = _candidate()
    outcome_candidate = ridge._outcome_payload_from_scored_candidate(
        candidate,
        score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
    )
    return {
        "source": {
            "coverage_audit_sha256": "a" * 64,
            "artifact_root_sha256": "b" * 64,
            "temporal_contract_sha256": "c" * 64,
            "security_code_transition_contract_sha256": "d" * 64,
            "temporal_role": "development",
            "market_session_count": 504,
            "exact_membership_session_count": 504,
        },
        "features": {
            "bar_loader_receipt": {"schema_version": "synthetic-bars/v1"},
            "feature_receipt": {
                "schema_version": "synthetic-features/v1",
                "session_count": 504,
                "sessions_sha256": "e" * 64,
                "feature_rows_sha256": "f" * 64,
            },
        },
        "model": {
            "oof_receipt": {
                "schema_version": (
                    "audited-pit-shallow-gbdt-rolling-oof-receipt/v1"
                ),
                "receipt_sha256": "1" * 64,
                "oof_scores_sha256": "2" * 64,
                "oof_candidate_count": 1,
            },
            "oof_replay_verification": {
                "verified": True,
                "receipt_sha256": "1" * 64,
                "fold_count": 6,
                "oof_candidate_count": 1,
            },
        },
        "execution": {
            "security_code_transition_evidence": {
                "schema_version": "synthetic-transition/v1"
            },
            "bulk_next_open_evidence_receipt": {
                "schema_version": "synthetic-next-open/v1"
            },
            "tail_cutoff_receipt": {
                "schema_version": "synthetic-tail-cutoff/v1"
            },
            "entry_preflight_receipt": {
                "schema_version": "synthetic-entry/v1"
            },
            "outcome_receipt": {
                "schema_version": "synthetic-outcome/v1"
            },
            "completed_candidates": [outcome_candidate],
            "right_censored_positions": [],
        },
        "selection": {
            "scored_execution_candidates": [candidate],
            "positive_candidates": [candidate],
            "positive_pool_receipt": ridge._positive_score_pool(
                [candidate],
                score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
            )[1],
            "main_sweep": {
                "schema_version": (
                    "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
                ),
                "top": [{}],
            },
            "amount_baseline_sweep": {
                "schema_version": (
                    "strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1"
                ),
                "top": [{}],
            },
            "main_selection_receipt": {
                "schema_version": (
                    "shallow-gbdt-industry-selection-receipt/v1"
                )
            },
            "amount_baseline_selection_receipt": {
                "schema_version": (
                    "shallow-gbdt-industry-selection-receipt/v1"
                )
            },
            "main_selected": [candidate],
            "baseline_selected": [],
            "advancement_gate_passed": False,
        },
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "intraday_fill_claimed": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "advancement_gate_passed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
    }


def _build_payloads() -> dict:
    """The new builder receives only already-produced shared receipts."""
    return ridge.build_ranked_liquidity_result_payloads(
        strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
        shared_receipts=_shared_receipts(),
    )


def test_shallow_gbdt_variant_payload_builder_uses_frozen_schemas_and_probability_only():
    payloads = _build_payloads()
    variant = ridge.resolve_ranked_liquidity_run_variant(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC
    )

    assert payloads["main_payload"]["schema_version"] == variant[
        "result_schema_version"
    ]
    assert payloads["main_payload"]["strategy"] == {
        **shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
        "strategy_sha256": shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256,
    }
    assert set(payloads["sidecar_payloads"]) == {
        "features",
        "models",
        "execution",
        "selection",
    }
    for name, sidecar in payloads["sidecar_payloads"].items():
        assert sidecar["schema_version"] == variant[
            "sidecar_schema_versions"
        ][name]
        assert sidecar["strategy_sha256"] == shallow_gbdt._SHALLOW_GBDT_OOF_SPEC_SHA256
        assert sidecar["source"] == payloads["main_payload"]["source"]
        assert sidecar["producer_code"] == payloads["producer_code"]

    selection = payloads["sidecar_payloads"]["selection"]
    evidence = selection["scored_execution_candidate_evidence"]
    assert evidence["schema_version"] == (
        "ranked-liquidity-shallow-gbdt-score-evidence/v1"
    )
    assert evidence["columns"][5] == "predicted_positive_utility_probability"
    assert "predicted_net_return_pct" not in evidence["columns"]
    assert all(
        "predicted_net_return_pct" not in selected
        for selected in selection["selected_evidence"]
    )


def test_shallow_gbdt_result_bundle_replays_inputs_and_rejects_schema_tamper(
    monkeypatch,
    tmp_path: Path,
):
    payloads = _build_payloads()
    bundle = ridge._write_result_bundle(
        tmp_path / "bundle",
        main_payload=payloads["main_payload"],
        sidecar_payloads=payloads["sidecar_payloads"],
        expected_producer_code=payloads["producer_code"],
    )
    replay_calls = []

    def fake_independent_replay(
        tail_features,
        outcome_candidates,
        sessions,
        scored_oof,
        receipt,
        **kwargs,
    ):
        replay_calls.append(
            (tail_features, outcome_candidates, sessions, scored_oof, receipt)
        )
        return {
            "verified": True,
            "receipt_sha256": receipt["receipt_sha256"],
            "fold_count": 6,
            "oof_candidate_count": 1,
        }

    monkeypatch.setattr(
        ridge,
        "_verify_shallow_gbdt_result_bundle_oof_replay",
        fake_independent_replay,
    )
    replay_inputs = {
        "tail_features": pd.DataFrame(),
        "outcome_candidates": [],
        "sessions": [],
        "scored_oof": pd.DataFrame(
            [
                {
                    "candidate_key": "candidate-1",
                    "signal_date": "2025-01-02",
                    "predicted_positive_utility_probability": 0.75,
                }
            ]
        ),
    }
    verified = ridge.verify_shallow_gbdt_result_bundle(
        bundle,
        **replay_inputs,
    )

    assert verified["verified"] is True
    assert replay_calls

    tampered = deepcopy(bundle)
    main_document = json.loads(
        Path(tampered["artifact"]["path"]).read_text(encoding="utf-8")
    )
    main_document.pop("artifact_sha256")
    selection_document = json.loads(
        Path(
            tampered["runtime_sidecars"]["selection"]["path"]
        ).read_text(encoding="utf-8")
    )
    selection_document.pop("artifact_sha256")
    selection_document["schema_version"] = (
        "ranked-liquidity-shallow-gbdt-selection-sidecar/v999"
    )
    selection_runtime = ridge._write_content_addressed(
        tmp_path / "tampered" / "sidecars",
        selection_document,
    )
    tampered["runtime_sidecars"]["selection"] = selection_runtime
    main_document["sidecars"]["selection"] = ridge._stable_sidecar_reference(
        selection_runtime
    )
    tampered["artifact"] = ridge._write_content_addressed(
        tmp_path / "tampered",
        main_document,
    )

    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(
            tampered,
            **replay_inputs,
        )
