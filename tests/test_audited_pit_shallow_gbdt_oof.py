from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

import app.audited_pit_shallow_gbdt as gbdt
from app.audited_pit_shallow_gbdt import (
    FEATURE_NAMES,
    build_daily_utility_targets,
    build_shallow_gbdt_rolling_oof_scores,
    clip_training_net_returns,
    positive_utility_mask,
    verify_shallow_gbdt_rolling_oof_receipt,
)


FRICTION_PERCENTAGE_POINTS = 0.45


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _array_sha256(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(list(array.shape), separators=(",", ":")).encode("ascii")
    )
    digest.update(b"\0")
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _fixture() -> tuple[list[str], pd.DataFrame, list[dict[str, Any]]]:
    sessions = [
        value.strftime("%Y-%m-%d")
        for value in pd.date_range("2026-01-05", periods=8, freq="D")
    ]
    feature_rows: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for session_index, signal_date in enumerate(sessions):
        for member_index in range(40):
            candidate_key = f"{signal_date}|{member_index:03d}"
            row: dict[str, Any] = {
                "candidate_key": candidate_key,
                "signal_date": signal_date,
            }
            row[FEATURE_NAMES[0]] = float((member_index % 3) - 1)
            for feature_index, feature_name in enumerate(
                FEATURE_NAMES[1:],
                start=1,
            ):
                row[feature_name] = float(
                    session_index * 1_000
                    + member_index * 10
                    + feature_index
                ) / 10_000.0
            feature_rows.append(row)

            if session_index == 0 and member_index == 39:
                outcomes.append(
                    {
                        "candidate_key": candidate_key,
                        "right_censored": True,
                    }
                )
                continue
            if session_index == 1 and member_index == 39:
                continue
            if session_index + 1 >= len(sessions):
                continue
            if session_index == 0 and member_index == 0:
                net_return = 1_000.0
            else:
                magnitude = 1.0 + member_index / 100.0
                net_return = (
                    magnitude
                    if (session_index + member_index) % 2 == 0
                    else -magnitude
                )
            outcomes.append(
                {
                    "candidate_key": candidate_key,
                    "exit_date": sessions[session_index + 1],
                    "return_pct": net_return + FRICTION_PERCENTAGE_POINTS,
                    "right_censored": False,
                }
            )
    return sessions, pd.DataFrame(feature_rows), outcomes


def _install_fake_fold_model(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, np.ndarray]]:
    fit_calls: list[dict[str, np.ndarray]] = []

    def fake_fit(
        features: np.ndarray,
        labels: np.ndarray,
        weights: np.ndarray,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        matrix = np.ascontiguousarray(features, dtype=np.float64)
        label_values = np.ascontiguousarray(labels, dtype=np.float64)
        weight_values = np.ascontiguousarray(weights, dtype=np.float64)
        fit_calls.append(
            {
                "features": matrix.copy(),
                "labels": label_values.copy(),
                "weights": weight_values.copy(),
            }
        )
        receipt = {
            "schema_version": "fake-deterministic-fit/v1",
            "features_sha256": _array_sha256(matrix),
            "labels_sha256": _array_sha256(label_values),
            "weights_sha256": _array_sha256(weight_values),
        }
        return {"model_sha256": _canonical_sha256(receipt)}, receipt

    def fake_predict(
        booster: dict[str, str],
        features: np.ndarray,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        matrix = np.ascontiguousarray(features, dtype=np.float64)
        probabilities = np.where(
            matrix[:, 0] < 0.0,
            0.49,
            np.where(matrix[:, 0] > 0.0, 0.51, 0.5),
        ).astype(np.float64)
        receipt = {
            "schema_version": "fake-deterministic-predict/v1",
            "model_sha256": booster["model_sha256"],
            "features_sha256": _array_sha256(matrix),
            "probability_sha256": _array_sha256(probabilities),
        }
        return probabilities, receipt

    monkeypatch.setattr(gbdt, "fit_shallow_gbdt_fold", fake_fit)
    monkeypatch.setattr(gbdt, "predict_shallow_gbdt_fold", fake_predict)
    return fit_calls


def _first_fold_expected_training(
    sessions: list[str],
    features: pd.DataFrame,
    outcomes: list[dict[str, Any]],
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    outcome_lookup = {
        str(outcome["candidate_key"]): outcome for outcome in outcomes
    }
    training_rows: list[pd.Series] = []
    net_returns: list[float] = []
    for _, row in features.sort_values(
        ["signal_date", "candidate_key"],
        kind="mergesort",
    ).iterrows():
        if str(row["signal_date"]) not in set(sessions[:4]):
            continue
        outcome = outcome_lookup.get(str(row["candidate_key"]))
        if outcome is None or outcome.get("right_censored") is True:
            continue
        if str(outcome["exit_date"]) >= sessions[4]:
            continue
        training_rows.append(row)
        net_returns.append(
            float(outcome["return_pct"]) - FRICTION_PERCENTAGE_POINTS
        )
    training = pd.DataFrame(training_rows).reset_index(drop=True)
    clipped, _, _ = clip_training_net_returns(
        np.asarray(net_returns, dtype=np.float64)
    )
    labels, weights, daily_abs_sums = build_daily_utility_targets(
        clipped,
        training["signal_date"].to_numpy(dtype=object),
    )
    return training, clipped, labels, weights, daily_abs_sums


def _rehash_receipt(receipt: dict[str, Any]) -> None:
    for fold in receipt["folds"]:
        fold_without_self = {
            key: value
            for key, value in fold.items()
            if key != "receipt_sha256"
        }
        fold["receipt_sha256"] = _canonical_sha256(fold_without_self)
    receipt["folds_sha256"] = _canonical_sha256(receipt["folds"])
    receipt_without_self = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt_without_self)


def test_rolling_oof_defaults_freeze_126_by_63_design() -> None:
    signature = inspect.signature(build_shallow_gbdt_rolling_oof_scores)

    assert signature.parameters["minimum_training_sessions"].default == 126
    assert signature.parameters["training_window_sessions"].default == 126
    assert signature.parameters["validation_sessions"].default == 63


@pytest.mark.parametrize(
    "session_mutation",
    [
        lambda values: [values[1], values[0], *values[2:]],
        lambda values: [values[0], values[0], *values[2:]],
    ],
)
def test_rolling_oof_requires_ordered_unique_sessions(
    monkeypatch: pytest.MonkeyPatch,
    session_mutation: Any,
) -> None:
    sessions, features, outcomes = _fixture()
    _install_fake_fold_model(monkeypatch)

    with pytest.raises(ValueError, match="session"):
        build_shallow_gbdt_rolling_oof_scores(
            features,
            outcomes,
            session_mutation(sessions),
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )


def test_rolling_oof_freezes_purge_utility_targets_and_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, features, outcomes = _fixture()
    original_features = features.copy(deep=True)
    original_outcomes = deepcopy(outcomes)
    fit_calls = _install_fake_fold_model(monkeypatch)

    scored, receipt = build_shallow_gbdt_rolling_oof_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    )

    assert receipt["schema_version"] == (
        "audited-pit-shallow-gbdt-rolling-oof-receipt/v1"
    )
    assert receipt["minimum_training_sessions"] == 4
    assert receipt["training_window_sessions"] == 4
    assert receipt["validation_sessions"] == 2
    assert receipt["friction_percentage_points"] == 0.45
    assert receipt["purge"] == (
        "complete_exit_date_strictly_before_validation_start"
    )
    assert receipt["probability_column"] == (
        "predicted_positive_utility_probability"
    )
    assert receipt["probability_gate"] == "strictly_greater_than_0.5"
    assert receipt["fold_count"] == 2
    assert receipt["frozen_signal_sessions"] == sessions
    assert receipt["frozen_signal_sessions_sha256"] == _canonical_sha256(
        sessions
    )

    assert scored.columns[-1] == "predicted_positive_utility_probability"
    assert scored["candidate_key"].is_unique
    assert scored[["signal_date", "candidate_key"]].to_dict("records") == (
        scored.sort_values(
            ["signal_date", "candidate_key"],
            kind="mergesort",
        )[["signal_date", "candidate_key"]].to_dict("records")
    )
    assert len(scored) == 160
    probabilities = scored[
        "predicted_positive_utility_probability"
    ].to_numpy(dtype=np.float64)
    assert set(probabilities) == {0.49, 0.5, 0.51}
    assert not positive_utility_mask(
        np.array([0.49, 0.5], dtype=np.float64)
    ).any()
    assert positive_utility_mask(
        np.array([0.51], dtype=np.float64)
    ).all()

    training, clipped, labels, weights, daily_abs_sums = (
        _first_fold_expected_training(
            sessions,
            features,
            outcomes,
        )
    )
    assert len(fit_calls) == 2
    np.testing.assert_array_equal(
        fit_calls[0]["features"],
        training.loc[:, list(FEATURE_NAMES)].to_numpy(dtype=np.float64),
    )
    np.testing.assert_array_equal(fit_calls[0]["labels"], labels)
    np.testing.assert_array_equal(fit_calls[0]["weights"], weights)
    assert clipped.max() < 1_000.0
    for signal_date in training["signal_date"].unique():
        assert weights[
            training["signal_date"].to_numpy() == signal_date
        ].sum() == pytest.approx(1.0)

    first_fold = receipt["folds"][0]
    assert first_fold["validation_start"] == sessions[4]
    assert first_fold["validation_end"] == sessions[5]
    assert first_fold["training_window_type"] == (
        "trailing_frozen_signal_sessions"
    )
    assert first_fold["training_window_session_count"] == 4
    assert first_fold["training_window_start"] == sessions[0]
    assert first_fold["training_window_end"] == sessions[3]
    assert first_fold["training_window_sessions_sha256"] == (
        _canonical_sha256(sessions[:4])
    )
    assert first_fold["window_candidate_count"] == 160
    eligible_keys = sorted(
        {
            str(outcome["candidate_key"])
            for outcome in outcomes
            if str(outcome["candidate_key"]).split("|")[0] in sessions[:4]
            and outcome.get("right_censored") is False
        }
    )
    assert first_fold["eligible_complete_window_candidate_count"] == 158
    assert first_fold[
        "eligible_complete_window_candidate_keys_sha256"
    ] == _canonical_sha256(eligible_keys)
    assert first_fold["right_censored_window_candidate_count"] == 1
    assert first_fold["right_censored_window_candidate_keys_sha256"] == (
        _canonical_sha256([f"{sessions[0]}|039"])
    )
    assert first_fold["incomplete_window_candidate_count"] == 1
    assert first_fold["incomplete_window_candidate_keys_sha256"] == (
        _canonical_sha256([f"{sessions[1]}|039"])
    )
    purged_keys = [
        f"{sessions[3]}|{member_index:03d}"
        for member_index in range(40)
    ]
    assert first_fold["purged_immature_candidate_count"] == 40
    assert first_fold["purged_immature_candidate_keys_sha256"] == (
        _canonical_sha256(purged_keys)
    )
    expected_training_keys = training["candidate_key"].astype(str).tolist()
    assert first_fold["training_candidate_count"] == 118
    assert first_fold["training_candidate_keys_sha256"] == (
        _canonical_sha256(expected_training_keys)
    )
    assert first_fold["training_last_exit_date"] == sessions[3]
    assert first_fold["training_label"] == (
        "gross_return_pct_minus_0.45_then_fold_p99_symmetric_clip"
    )
    assert first_fold["clip"] == {
        "method": "absolute_p99_zero_based_nearest_rank_symmetric",
        "cutoff": pytest.approx(float(np.max(np.abs(clipped)))),
        "cutoff_index": math.ceil(0.99 * len(clipped)) - 1,
        "clipped_net_returns_sha256": _array_sha256(clipped),
    }
    assert first_fold["targets"] == {
        "label": "positive_clipped_net_return",
        "weight": "abs_clipped_return_over_same_signal_date_abs_sum",
        "labels_sha256": _array_sha256(labels),
        "weights_sha256": _array_sha256(weights),
        "daily_abs_sums_sha256": _array_sha256(daily_abs_sums),
    }
    assert first_fold["fit_receipt"]["schema_version"] == (
        "fake-deterministic-fit/v1"
    )
    assert first_fold["predict_receipt"]["schema_version"] == (
        "fake-deterministic-predict/v1"
    )
    validation_keys = [
        f"{signal_date}|{member_index:03d}"
        for signal_date in sessions[4:6]
        for member_index in range(40)
    ]
    assert first_fold["validation_candidate_count"] == 80
    assert first_fold["validation_signal_sessions"] == sessions[4:6]
    assert first_fold["validation_signal_sessions_sha256"] == (
        _canonical_sha256(sessions[4:6])
    )
    assert first_fold["validation_candidate_keys_sha256"] == (
        _canonical_sha256(validation_keys)
    )
    first_positive_rows = [
        {
            "candidate_key": str(row.candidate_key),
            "signal_date": str(row.signal_date),
            "predicted_positive_utility_probability": float(
                row.predicted_positive_utility_probability
            ),
        }
        for row in scored[
            scored["signal_date"].isin(sessions[4:6])
        ].itertuples(index=False)
        if float(row.predicted_positive_utility_probability) > 0.5
    ]
    assert first_fold["positive_utility_candidate_count"] == 26
    assert first_fold["positive_utility_candidate_keys_sha256"] == (
        _canonical_sha256(
            [row["candidate_key"] for row in first_positive_rows]
        )
    )
    assert first_fold["positive_utility_rows_sha256"] == (
        _canonical_sha256(first_positive_rows)
    )
    assert first_fold["receipt_sha256"] == _canonical_sha256(
        {
            key: value
            for key, value in first_fold.items()
            if key != "receipt_sha256"
        }
    )

    score_payload = [
        {
            "candidate_key": str(row.candidate_key),
            "signal_date": str(row.signal_date),
            "predicted_positive_utility_probability": float(
                row.predicted_positive_utility_probability
            ),
        }
        for row in scored.itertuples(index=False)
    ]
    assert receipt["oof_candidate_count"] == len(scored)
    assert receipt["oof_candidate_keys_sha256"] == _canonical_sha256(
        scored["candidate_key"].astype(str).tolist()
    )
    assert receipt["oof_scores_sha256"] == _canonical_sha256(score_payload)
    positive_rows = [
        row
        for row in score_payload
        if row["predicted_positive_utility_probability"] > 0.5
    ]
    assert receipt["positive_utility_candidate_count"] == 52
    assert receipt["positive_utility_candidate_keys_sha256"] == (
        _canonical_sha256(
            [row["candidate_key"] for row in positive_rows]
        )
    )
    assert receipt["positive_utility_rows_sha256"] == _canonical_sha256(
        positive_rows
    )
    assert receipt["folds_sha256"] == _canonical_sha256(receipt["folds"])
    assert receipt["receipt_sha256"] == _canonical_sha256(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
    )
    pd.testing.assert_frame_equal(features, original_features)
    assert outcomes == original_outcomes


def test_independent_verifier_replays_and_rejects_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, features, outcomes = _fixture()
    _install_fake_fold_model(monkeypatch)
    scored, receipt = build_shallow_gbdt_rolling_oof_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    )

    def public_builder_must_not_be_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("verifier must independently replay the receipt")

    monkeypatch.setattr(
        gbdt,
        "build_shallow_gbdt_rolling_oof_scores",
        public_builder_must_not_be_called,
    )
    for private_name in (
        "_compute_shallow_gbdt_rolling_oof",
        "_rolling_oof_inputs",
        "_rolling_fold_members",
        "_fold_receipt",
        "clip_training_net_returns",
        "build_daily_utility_targets",
    ):
        monkeypatch.setattr(
            gbdt,
            private_name,
            public_builder_must_not_be_called,
        )
    assert verify_shallow_gbdt_rolling_oof_receipt(
        features,
        outcomes,
        sessions,
        scored,
        receipt,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    ) == {
        "verified": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "fold_count": 2,
        "oof_candidate_count": len(scored),
    }

    tampered_receipt = deepcopy(receipt)
    tampered_receipt["folds"][0]["training_window_start"] = sessions[1]
    _rehash_receipt(tampered_receipt)
    with pytest.raises(ValueError, match="shallow GBDT rolling OOF"):
        verify_shallow_gbdt_rolling_oof_receipt(
            features,
            outcomes,
            sessions,
            scored,
            tampered_receipt,
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )


def test_second_fold_drops_old_sessions_and_validation_outcomes_do_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, features, outcomes = _fixture()
    _install_fake_fold_model(monkeypatch)
    scored, receipt = build_shallow_gbdt_rolling_oof_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    )

    second_fold = receipt["folds"][1]
    assert second_fold["training_window_start"] == sessions[2]
    assert second_fold["training_window_end"] == sessions[5]
    assert second_fold["training_window_sessions_sha256"] == (
        _canonical_sha256(sessions[2:6])
    )
    expected_window_keys = [
        f"{signal_date}|{member_index:03d}"
        for signal_date in sessions[2:6]
        for member_index in range(40)
    ]
    assert second_fold["window_candidate_keys_sha256"] == (
        _canonical_sha256(expected_window_keys)
    )

    changed = deepcopy(outcomes)
    for outcome in changed:
        if str(outcome["candidate_key"]).split("|")[0] in sessions[4:6]:
            if outcome.get("right_censored") is False:
                outcome["return_pct"] = float(outcome["return_pct"]) + 500.0
    rescored, changed_receipt = build_shallow_gbdt_rolling_oof_scores(
        features,
        changed,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    )
    probability_column = "predicted_positive_utility_probability"
    pd.testing.assert_series_equal(
        scored.loc[
            scored["signal_date"].isin(sessions[4:6]),
            probability_column,
        ].reset_index(drop=True),
        rescored.loc[
            rescored["signal_date"].isin(sessions[4:6]),
            probability_column,
        ].reset_index(drop=True),
    )
    assert receipt["folds"][0] == changed_receipt["folds"][0]


def test_rolling_oof_rejects_nan_keys_and_non_boolean_censor_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, features, outcomes = _fixture()
    _install_fake_fold_model(monkeypatch)
    invalid_features = features.copy(deep=True)
    invalid_features.loc[0, "candidate_key"] = np.nan
    with pytest.raises(ValueError, match="key"):
        build_shallow_gbdt_rolling_oof_scores(
            invalid_features,
            outcomes,
            sessions,
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )

    invalid_outcomes = deepcopy(outcomes)
    invalid_outcomes[0] = {
        "candidate_key": invalid_outcomes[0]["candidate_key"],
        "exit_date": sessions[1],
        "return_pct": 1.0,
        "right_censored": 1,
    }
    with pytest.raises(ValueError, match="right_censored"):
        build_shallow_gbdt_rolling_oof_scores(
            features,
            invalid_outcomes,
            sessions,
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )

    tampered_scores = scored.copy(deep=True)
    probability_column = "predicted_positive_utility_probability"
    tampered_scores.loc[0, probability_column] = float(
        tampered_scores.loc[0, probability_column]
    ) + 0.001
    with pytest.raises(ValueError, match="shallow GBDT rolling OOF"):
        verify_shallow_gbdt_rolling_oof_receipt(
            features,
            outcomes,
            sessions,
            tampered_scores,
            receipt,
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )
