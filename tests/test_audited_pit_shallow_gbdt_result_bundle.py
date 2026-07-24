"""RED contract for the frozen shallow-GBDT result bundle."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge
from app import audited_pit_shallow_gbdt as shallow_gbdt


_SESSIONS = pd.bdate_range(
    end="2025-01-07",
    periods=int(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC[
            "required_market_session_count"
        ]
    ),
).strftime("%Y-%m-%d").tolist()


def _candidate(
    index: int = 1,
    *,
    signal_date: str = "2025-01-02",
    entry_date: str = "2025-01-03",
    exit_date: str = "2025-01-06",
    probability: float = 0.75,
    amount: float = 100.0,
    industry: str = "industry-a",
    return_pct: float = 1.25,
) -> dict:
    start = _SESSIONS.index(entry_date)
    end = _SESSIONS.index(exit_date)
    active_sessions = _SESSIONS[start : end + 1]
    return {
        "candidate_key": f"candidate-{index}",
        "signal_date": signal_date,
        "security_id": f"cn-a-share:00000{index}.SZ",
        "symbol": f"00000{index}.SZ",
        "signal_industry": industry,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "candidate_amount": amount,
        "right_censored": False,
        "return_pct": return_pct,
        "max_adverse_pct": -1.0,
        "mark_to_market_path": [
            {
                "date": session,
                "close_return_pct": return_pct * (offset + 1) / len(active_sessions),
                "low_return_pct": -1.0,
            }
            for offset, session in enumerate(active_sessions)
        ],
        "predicted_positive_utility_probability": probability,
        "score": probability,
        "rank_score": probability,
    }


def _scored_candidates() -> list[dict]:
    return [
        _candidate(1, probability=0.90, amount=50.0, industry="industry-a"),
        _candidate(2, probability=0.70, amount=200.0, industry="industry-b", return_pct=-1.0),
        _candidate(
            3,
            signal_date="2025-01-03",
            entry_date="2025-01-06",
            exit_date="2025-01-07",
            probability=0.50,
            amount=300.0,
            industry="industry-c",
            return_pct=0.5,
        ),
        _candidate(
            4,
            signal_date="2025-01-03",
            entry_date="2025-01-06",
            exit_date="2025-01-07",
            probability=0.40,
            amount=150.0,
            industry="industry-d",
            return_pct=-0.5,
        ),
    ]


def _shared_receipts(
    candidates: list[dict] | None = None,
    *,
    sessions: list[str] | None = None,
) -> dict:
    session_dates = sessions or _SESSIONS
    evaluation_session_dates = session_dates[
        int(
            shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["walk_forward"][
                "minimum_training_sessions"
            ]
        ) :
    ]
    scored_candidates = candidates or _scored_candidates()
    positive_candidates, positive_pool_receipt = ridge._positive_score_pool(
        scored_candidates,
        score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
    )
    main_sweep, main_selection_receipt = ridge._evaluate_fixed_oof(
        positive_candidates,
        rank_mode="positive_utility_probability",
        evaluation_session_dates=evaluation_session_dates,
        strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
        sweep_schema_version="strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1",
        score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
    )
    amount_baseline_sweep, amount_baseline_selection_receipt = (
        ridge._evaluate_fixed_oof(
            positive_candidates,
            rank_mode="signal_date_amount",
            evaluation_session_dates=evaluation_session_dates,
            strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
            sweep_schema_version="strict-ranked-liquidity-shallow-gbdt-fixed-oof/v1",
            score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
        )
    )
    main_selected = [
        candidate
        for candidate in positive_candidates
        if ridge._selection_trade_key(candidate)
        in main_selection_receipt["selected_trade_keys"]
    ]
    baseline_selected = [
        candidate
        for candidate in positive_candidates
        if ridge._selection_trade_key(candidate)
        in amount_baseline_selection_receipt["selected_trade_keys"]
    ]
    completed_candidates = [
        ridge._outcome_payload_from_scored_candidate(
            candidate,
            score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
        )
        for candidate in scored_candidates
    ]
    outcome_membership = ridge._compact_outcome_membership_evidence(
        completed_candidates,
        [],
    )
    fold_ranges = ridge._fold_ranges(
        session_dates,
        minimum_training_sessions=int(
            shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["walk_forward"][
                "minimum_training_sessions"
            ]
        ),
        validation_sessions=int(
            shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["walk_forward"][
                "validation_sessions"
            ]
        ),
    )
    oof_receipt = {
        "schema_version": (
            "audited-pit-shallow-gbdt-rolling-oof-receipt/v1"
        ),
        "fold_count": len(fold_ranges),
        "folds": [
            {
                "validation_start": validation_start,
                "validation_end": validation_end,
            }
            for validation_start, validation_end in fold_ranges
        ],
        "frozen_signal_sessions": session_dates,
        "frozen_signal_sessions_sha256": ridge._sha256(session_dates),
        "oof_scores_sha256": "2" * 64,
        "oof_candidate_count": 4,
    }
    oof_receipt["receipt_sha256"] = ridge._sha256(oof_receipt)
    outcome_receipt = {
        "schema_version": (
            "ranked-liquidity-shallow-gbdt-strict-outcome/v1"
        ),
        "completed_candidate_count": len(completed_candidates),
        "completed_candidates_sha256": outcome_membership[
            "completed_candidates_sha256"
        ],
        "right_censored_position_count": 0,
        "right_censored_positions_sha256": outcome_membership[
            "right_censored_positions_sha256"
        ],
        "strict_outcome_candidate_payload_hashes_sha256": (
            outcome_membership[
                "strict_outcome_candidate_payload_hashes_sha256"
            ]
        ),
    }
    outcome_receipt["receipt_sha256"] = ridge._sha256(outcome_receipt)
    return {
        "source": {
            "coverage_audit_sha256": "a" * 64,
            "artifact_root_sha256": "b" * 64,
            "temporal_contract_sha256": "c" * 64,
            "security_code_transition_contract_sha256": "d" * 64,
            "temporal_role": "development",
            "market_session_count": len(session_dates),
            "exact_membership_session_count": len(session_dates),
        },
        "features": {
            "bar_loader_receipt": {"schema_version": "synthetic-bars/v1"},
            "feature_receipt": {
                "schema_version": "synthetic-features/v1",
                "session_count": len(session_dates),
                "sessions_sha256": ridge._sha256(session_dates),
                "feature_rows_sha256": "f" * 64,
            },
        },
        "model": {
            "oof_receipt": oof_receipt,
            "oof_replay_verification": {
                "verified": True,
                "receipt_sha256": oof_receipt["receipt_sha256"],
                "fold_count": len(fold_ranges),
                "oof_candidate_count": 4,
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
            "outcome_receipt": outcome_receipt,
            "completed_candidates": completed_candidates,
            "right_censored_positions": [],
        },
        "selection": {
            "scored_execution_candidates": scored_candidates,
            "positive_candidates": positive_candidates,
            "positive_pool_receipt": positive_pool_receipt,
            "main_sweep": main_sweep,
            "amount_baseline_sweep": amount_baseline_sweep,
            "main_selection_receipt": main_selection_receipt,
            "amount_baseline_selection_receipt": amount_baseline_selection_receipt,
            "main_selected": main_selected,
            "baseline_selected": baseline_selected,
            "advancement_gate_passed": ridge._advancement_gate_passes(
                main_sweep["top"][0],
                amount_baseline_sweep["top"][0],
            ),
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


def _build_payloads(
    candidates: list[dict] | None = None,
    *,
    sessions: list[str] | None = None,
) -> dict:
    """The new builder receives only already-produced shared receipts."""
    return ridge.build_ranked_liquidity_result_payloads(
        strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
        shared_receipts=_shared_receipts(candidates, sessions=sessions),
    )


def _replay_inputs(*, sessions: list[str] | None = None) -> dict:
    candidates = _scored_candidates()
    shared = _shared_receipts(candidates, sessions=sessions)
    return {
        "tail_features": pd.DataFrame(),
        "outcome_candidates": [
            ridge._outcome_payload_from_scored_candidate(
                candidate,
                score_contract=shallow_gbdt.SHALLOW_GBDT_SCORE_CONTRACT,
            )
            for candidate in candidates
        ],
        "sessions": sessions or _SESSIONS,
        "scored_oof": pd.DataFrame(
            [
                {
                    "candidate_key": candidate["candidate_key"],
                    "signal_date": candidate["signal_date"],
                    "predicted_positive_utility_probability": candidate[
                        "predicted_positive_utility_probability"
                    ],
                }
                for candidate in candidates
            ]
        ),
        "expected_source": shared["source"],
        "expected_outcome_receipt": shared["execution"][
            "outcome_receipt"
        ],
    }


def _bundle_with_fake_oof_replay(
    monkeypatch,
    tmp_path: Path,
    *,
    sessions: list[str] | None = None,
) -> tuple[dict, dict]:
    payloads = _build_payloads(sessions=sessions)
    bundle = ridge._write_result_bundle(
        tmp_path / "bundle",
        main_payload=payloads["main_payload"],
        sidecar_payloads=payloads["sidecar_payloads"],
        expected_producer_code=payloads["producer_code"],
    )

    def fake_independent_replay(
        tail_features,
        outcome_candidates,
        sessions,
        scored_oof,
        receipt,
        **kwargs,
    ):
        return {
            "verified": True,
            "receipt_sha256": receipt["receipt_sha256"],
            "fold_count": receipt["fold_count"],
            "oof_candidate_count": 4,
        }

    monkeypatch.setattr(
        ridge,
        "_verify_shallow_gbdt_result_bundle_oof_replay",
        fake_independent_replay,
    )
    return bundle, _replay_inputs(sessions=sessions)


def _tamper_selection_bundle(
    bundle: dict,
    tmp_path: Path,
    *,
    mutate_selection,
    mutate_main=None,
) -> dict:
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
    mutate_selection(selection_document)
    selection_runtime = ridge._write_content_addressed(
        tmp_path / "tampered" / "sidecars",
        selection_document,
    )
    tampered["runtime_sidecars"]["selection"] = selection_runtime
    main_document["sidecars"]["selection"] = ridge._stable_sidecar_reference(
        selection_runtime
    )
    if mutate_main is not None:
        mutate_main(main_document, selection_document)
    tampered["artifact"] = ridge._write_content_addressed(
        tmp_path / "tampered",
        main_document,
    )
    return tampered


def _tamper_bundle_documents(
    bundle: dict,
    tmp_path: Path,
    *,
    mutate_main=None,
    sidecar_mutators: dict | None = None,
) -> dict:
    tampered = deepcopy(bundle)
    main_document = json.loads(
        Path(tampered["artifact"]["path"]).read_text(encoding="utf-8")
    )
    main_document.pop("artifact_sha256")
    for name, mutate_sidecar in (sidecar_mutators or {}).items():
        sidecar_document = json.loads(
            Path(
                tampered["runtime_sidecars"][name]["path"]
            ).read_text(encoding="utf-8")
        )
        sidecar_document.pop("artifact_sha256")
        mutate_sidecar(sidecar_document)
        sidecar_runtime = ridge._write_content_addressed(
            tmp_path / "tampered" / "sidecars",
            sidecar_document,
        )
        tampered["runtime_sidecars"][name] = sidecar_runtime
        main_document["sidecars"][name] = ridge._stable_sidecar_reference(
            sidecar_runtime
        )
    if mutate_main is not None:
        mutate_main(main_document)
    tampered["artifact"] = ridge._write_content_addressed(
        tmp_path / "tampered",
        main_document,
    )
    return tampered


def _rewrite_score_evidence_probability(
    selection: dict,
    *,
    candidate_key: str,
    probability: float,
) -> None:
    evidence = selection["scored_execution_candidate_evidence"]
    probability_index = evidence["columns"].index(
        "predicted_positive_utility_probability"
    )
    for row in evidence["rows"]:
        if row[0] == candidate_key:
            row[probability_index] = probability
    evidence["rows_sha256"] = ridge._sha256(evidence["rows"])
    unsigned = {
        key: value for key, value in evidence.items() if key != "receipt_sha256"
    }
    evidence["receipt_sha256"] = ridge._sha256(unsigned)


def _rewrite_positive_pool_count_and_keys(selection: dict) -> None:
    receipt = selection["positive_pool_receipt"]
    receipt["positive_candidate_count"] = 1
    receipt["positive_candidate_keys_sha256"] = ridge._sha256(["candidate-1"])
    unsigned = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    receipt["receipt_sha256"] = ridge._sha256(unsigned)


def _rewrite_main_selection_receipt(selection: dict) -> None:
    receipt = selection["main_selection_receipt"]
    receipt["selected_trade_keys"] = list(
        reversed(receipt["selected_trade_keys"])
    )
    unsigned = {
        key: value
        for key, value in receipt.items()
        if key != "summary_receipt_sha256"
    }
    receipt["summary_receipt_sha256"] = ridge._sha256(unsigned)


def _rewrite_sweep_and_advancement_gate(selection: dict) -> None:
    row = selection["main_sweep"]["top"][0]
    row["target_full_development_quality_pass"] = True
    row["target_latest_12m_pass"] = True
    row["target_rolling_12m_stability_pass"] = True
    row["target_all_pass"] = True
    selection["main_sweep"]["target_all_pass_count"] = 1
    selection["main_sweep"]["target_rolling_12m_stability_pass_count"] = 1
    selection["advancement_gate_passed"] = True


def _bind_rewritten_sweeps_and_gate(main: dict, selection: dict) -> None:
    main["main_sweep"] = deepcopy(selection["main_sweep"])
    main["scope"]["advancement_gate_passed"] = True
    main["advancement_gate"]["all_required_gates_passed"] = True


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
    assert len(_SESSIONS) == int(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC[
            "required_market_session_count"
        ]
    )
    assert payloads["sidecar_payloads"]["models"]["oof_receipt"][
        "fold_count"
    ] == int(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["required_oof_fold_count"]
    )


def test_shallow_gbdt_payload_builder_rejects_positive_candidates_that_do_not_match_strict_score_gate():
    shared_receipts = _shared_receipts()
    shared_receipts["selection"]["positive_candidates"] = [
        _scored_candidates()[0],
        _scored_candidates()[2],
    ]

    with pytest.raises(ValueError, match="positive candidates"):
        ridge.build_ranked_liquidity_result_payloads(
            strategy_spec=shallow_gbdt.SHALLOW_GBDT_OOF_SPEC,
            shared_receipts=shared_receipts,
        )


def test_shallow_gbdt_result_bundle_rejects_truncated_frozen_session_geometry(
    monkeypatch,
    tmp_path: Path,
):
    truncated_sessions = _SESSIONS[63:]
    bundle, replay_inputs = _bundle_with_fake_oof_replay(
        monkeypatch,
        tmp_path,
        sessions=truncated_sessions,
    )
    model_document = json.loads(
        Path(bundle["runtime_sidecars"]["models"]["path"]).read_text(
            encoding="utf-8"
        )
    )

    assert model_document["oof_receipt"]["fold_count"] == 5
    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(
            bundle,
            **replay_inputs,
        )


def test_shallow_gbdt_result_bundle_rejects_rehashed_source_session_count_tamper(
    monkeypatch,
    tmp_path: Path,
):
    bundle, replay_inputs = _bundle_with_fake_oof_replay(
        monkeypatch,
        tmp_path,
    )

    def rewrite_source(document: dict) -> None:
        document["source"]["market_session_count"] = len(_SESSIONS) - 1
        document["source"]["exact_membership_session_count"] = (
            len(_SESSIONS) - 1
        )

    tampered = _tamper_bundle_documents(
        bundle,
        tmp_path,
        mutate_main=rewrite_source,
        sidecar_mutators={
            name: rewrite_source
            for name in bundle["runtime_sidecars"]
        },
    )
    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(
            tampered,
            **replay_inputs,
        )


def test_shallow_gbdt_result_bundle_rejects_rehashed_feature_session_binding_tamper(
    monkeypatch,
    tmp_path: Path,
):
    bundle, replay_inputs = _bundle_with_fake_oof_replay(
        monkeypatch,
        tmp_path,
    )

    def rewrite_feature_sessions(feature_sidecar: dict) -> None:
        receipt = feature_sidecar["feature_receipt"]
        receipt["session_count"] = len(_SESSIONS) - 1
        receipt["sessions_sha256"] = ridge._sha256(_SESSIONS[:-1])

    tampered = _tamper_bundle_documents(
        bundle,
        tmp_path,
        sidecar_mutators={"features": rewrite_feature_sessions},
    )
    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(
            tampered,
            **replay_inputs,
        )


def test_shallow_gbdt_result_bundle_rejects_rehashed_outcome_receipt_that_conflicts_with_membership(
    monkeypatch,
    tmp_path: Path,
):
    bundle, replay_inputs = _bundle_with_fake_oof_replay(
        monkeypatch,
        tmp_path,
    )

    def rewrite_outcome_receipt(execution_sidecar: dict) -> None:
        receipt = execution_sidecar["outcome_receipt"]
        receipt["schema_version"] = "forged-outcome/v999"
        receipt["completed_candidate_count"] = 999
        receipt["completed_candidates_sha256"] = "0" * 64
        receipt["right_censored_position_count"] = 999
        receipt["right_censored_positions_sha256"] = "1" * 64
        receipt["strict_outcome_candidate_payload_hashes_sha256"] = (
            "2" * 64
        )
        unsigned = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = ridge._sha256(unsigned)

    tampered = _tamper_bundle_documents(
        bundle,
        tmp_path,
        sidecar_mutators={"execution": rewrite_outcome_receipt},
    )
    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(
            tampered,
            **replay_inputs,
        )


@pytest.mark.parametrize(
    ("mutate_selection", "mutate_main"),
    [
        (
            lambda selection: _rewrite_score_evidence_probability(
                selection, candidate_key="candidate-1", probability=0.51
            ),
            None,
        ),
        (
            _rewrite_positive_pool_count_and_keys,
            lambda main, selection: main.__setitem__(
                "positive_pool_receipt_sha256",
                selection["positive_pool_receipt"]["receipt_sha256"],
            ),
        ),
        (
            _rewrite_main_selection_receipt,
            None,
        ),
        (
            _rewrite_sweep_and_advancement_gate,
            _bind_rewritten_sweeps_and_gate,
        ),
    ],
    ids=["score-evidence", "positive-pool", "selection-receipt", "sweep-gate"],
)
def test_shallow_gbdt_result_bundle_recomputes_selection_evidence_from_independent_oof_inputs(
    monkeypatch,
    tmp_path: Path,
    mutate_selection,
    mutate_main,
):
    bundle, replay_inputs = _bundle_with_fake_oof_replay(monkeypatch, tmp_path)

    assert ridge.verify_shallow_gbdt_result_bundle(bundle, **replay_inputs)["verified"]

    tampered = _tamper_selection_bundle(
        bundle,
        tmp_path,
        mutate_selection=mutate_selection,
        mutate_main=mutate_main,
    )
    with pytest.raises(
        ValueError,
        match="shallow GBDT result bundle verification failed",
    ):
        ridge.verify_shallow_gbdt_result_bundle(tampered, **replay_inputs)


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
            "oof_candidate_count": 4,
        }

    monkeypatch.setattr(
        ridge,
        "_verify_shallow_gbdt_result_bundle_oof_replay",
        fake_independent_replay,
    )
    replay_inputs = _replay_inputs()
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
