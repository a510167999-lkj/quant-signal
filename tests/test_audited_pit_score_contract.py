from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np
import pytest

from app.audited_pit_score_contract import (
    RIDGE_SCORE_CONTRACT,
    SHALLOW_GBDT_SCORE_CONTRACT,
    candidate_passes_gate,
    candidate_score,
    score_evidence_payload,
    selection_rank_key,
)


EXPECTED_RIDGE_SCORE_CONTRACT = {
    "field": "predicted_net_return_pct",
    "semantic": "predicted_net_return_after_costs",
    "unit": "percentage_points",
    "domain": "finite_real",
    "gate": "strict_gt",
    "value": 0.0,
    "main_rank_mode": "score_descending",
}

EXPECTED_SHALLOW_GBDT_SCORE_CONTRACT = {
    "field": "predicted_positive_utility_probability",
    "semantic": "predicted_positive_date_normalized_clipped_utility_probability",
    "unit": "probability",
    "domain": "closed_interval_0_1",
    "gate": "strict_gt",
    "value": 0.5,
    "main_rank_mode": "score_descending",
}


def _candidate(
    *,
    security_id: str = "cn-a-share:000001.SZ",
    amount: float = 1_000.0,
    ridge_score: float | None = None,
    gbdt_score: float | None = None,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "candidate_key": f"2026-07-23|{security_id}",
        "security_id": security_id,
        "candidate_amount": amount,
        "signal_industry": "制造",
    }
    if ridge_score is not None:
        candidate["predicted_net_return_pct"] = ridge_score
    if gbdt_score is not None:
        candidate["predicted_positive_utility_probability"] = gbdt_score
    return candidate


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        (RIDGE_SCORE_CONTRACT, EXPECTED_RIDGE_SCORE_CONTRACT),
        (
            SHALLOW_GBDT_SCORE_CONTRACT,
            EXPECTED_SHALLOW_GBDT_SCORE_CONTRACT,
        ),
    ],
)
def test_score_contracts_are_exact_and_immutable(
    contract: Mapping[str, object],
    expected: dict[str, object],
) -> None:
    assert isinstance(contract, Mapping)
    assert dict(contract) == expected

    with pytest.raises(TypeError):
        contract["field"] = "mutated"  # type: ignore[index]


def test_candidate_score_reads_the_contract_field_not_generic_aliases() -> None:
    ridge_candidate = _candidate(ridge_score=1.25)
    ridge_candidate.update({"score": -99.0, "rank_score": 99.0})
    gbdt_candidate = _candidate(gbdt_score=0.75)
    gbdt_candidate.update({"score": -99.0, "rank_score": 99.0})

    assert candidate_score(ridge_candidate) == pytest.approx(1.25)
    assert candidate_score(
        gbdt_candidate,
        contract=SHALLOW_GBDT_SCORE_CONTRACT,
    ) == pytest.approx(0.75)

    with pytest.raises(ValueError, match="score field"):
        candidate_score(
            {"score": 0.75, "rank_score": 0.75},
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, "0.75", True])
def test_candidate_score_rejects_nonfinite_or_nonnumeric_values(
    invalid: object,
) -> None:
    candidate = _candidate(gbdt_score=0.75)
    candidate["predicted_positive_utility_probability"] = invalid

    with pytest.raises(ValueError):
        candidate_score(
            candidate,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


@pytest.mark.parametrize(
    "invalid",
    [
        np.nextafter(0.0, -math.inf),
        np.nextafter(1.0, math.inf),
    ],
)
def test_gbdt_candidate_score_enforces_closed_probability_domain(
    invalid: float,
) -> None:
    with pytest.raises(ValueError, match="domain"):
        candidate_score(
            _candidate(gbdt_score=invalid),
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


def test_gbdt_candidate_rejects_the_ridge_only_score_field() -> None:
    candidate = _candidate(ridge_score=0.20, gbdt_score=0.70)

    with pytest.raises(ValueError, match="predicted_net_return_pct"):
        candidate_score(
            candidate,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
        )


def test_gbdt_gate_is_exactly_strictly_greater_than_one_half() -> None:
    below = np.nextafter(0.5, -math.inf)
    above = np.nextafter(0.5, math.inf)

    assert not candidate_passes_gate(
        _candidate(gbdt_score=below),
        contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    assert not candidate_passes_gate(
        _candidate(gbdt_score=0.5),
        contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )
    assert candidate_passes_gate(
        _candidate(gbdt_score=above),
        contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )


@pytest.mark.parametrize(
    ("base", "field", "value"),
    [
        (SHALLOW_GBDT_SCORE_CONTRACT, "value", 0.0),
        (SHALLOW_GBDT_SCORE_CONTRACT, "semantic", "mutated"),
        (SHALLOW_GBDT_SCORE_CONTRACT, "unit", "percentage_points"),
        (RIDGE_SCORE_CONTRACT, "gate", "gte"),
        (RIDGE_SCORE_CONTRACT, "domain", "closed_interval_0_1"),
    ],
)
def test_public_helpers_reject_contract_drift(
    base: Mapping[str, object],
    field: str,
    value: object,
) -> None:
    mutated = {**base, field: value}

    with pytest.raises(ValueError, match="frozen"):
        candidate_passes_gate(
            _candidate(gbdt_score=0.75, ridge_score=1.25),
            contract=mutated,
        )


def test_selection_rank_key_uses_real_score_then_amount_then_security_id() -> None:
    candidates = [
        _candidate(
            security_id="cn-a-share:000004.SZ",
            amount=120.0,
            gbdt_score=0.70,
        ),
        _candidate(
            security_id="cn-a-share:000003.SZ",
            amount=90.0,
            gbdt_score=0.80,
        ),
        _candidate(
            security_id="cn-a-share:000002.SZ",
            amount=110.0,
            gbdt_score=0.80,
        ),
        _candidate(
            security_id="cn-a-share:000001.SZ",
            amount=110.0,
            gbdt_score=0.80,
        ),
    ]
    candidates[0].update({"score": 100.0, "rank_score": 100.0})

    ordered = sorted(
        candidates,
        key=lambda candidate: selection_rank_key(
            candidate,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
            rank_mode="main",
        ),
    )

    assert [candidate["security_id"] for candidate in ordered] == [
        "cn-a-share:000001.SZ",
        "cn-a-share:000002.SZ",
        "cn-a-share:000003.SZ",
        "cn-a-share:000004.SZ",
    ]


def test_baseline_rank_key_uses_only_amount_then_security_id() -> None:
    candidates = [
        _candidate(
            security_id="cn-a-share:000003.SZ",
            amount=90.0,
            gbdt_score=0.99,
        ),
        _candidate(
            security_id="cn-a-share:000002.SZ",
            amount=110.0,
            gbdt_score=0.51,
        ),
        _candidate(
            security_id="cn-a-share:000001.SZ",
            amount=110.0,
            gbdt_score=0.52,
        ),
    ]

    ordered = sorted(
        candidates,
        key=lambda candidate: selection_rank_key(
            candidate,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
            rank_mode="baseline",
        ),
    )

    assert [candidate["security_id"] for candidate in ordered] == [
        "cn-a-share:000001.SZ",
        "cn-a-share:000002.SZ",
        "cn-a-share:000003.SZ",
    ]

    invalid = _candidate(ridge_score=0.1, gbdt_score=0.75)
    with pytest.raises(ValueError, match="predicted_net_return_pct"):
        selection_rank_key(
            invalid,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
            rank_mode="baseline",
        )


def test_legacy_rank_mode_names_preserve_ridge_receipt_semantics() -> None:
    candidate = _candidate(ridge_score=1.25)

    assert selection_rank_key(
        candidate,
        rank_mode="predicted_net_return",
    ) == selection_rank_key(candidate, rank_mode="main")
    assert selection_rank_key(
        candidate,
        rank_mode="signal_date_amount",
    ) == selection_rank_key(candidate, rank_mode="baseline")


@pytest.mark.parametrize(
    ("candidate", "rank_mode"),
    [
        (_candidate(gbdt_score=0.75, amount=math.nan), "main"),
        (_candidate(gbdt_score=0.75, security_id=""), "baseline"),
        (_candidate(gbdt_score=0.75), "unsupported"),
    ],
)
def test_selection_rank_key_fails_closed_on_invalid_inputs(
    candidate: Mapping[str, object],
    rank_mode: str,
) -> None:
    with pytest.raises(ValueError):
        selection_rank_key(
            candidate,
            contract=SHALLOW_GBDT_SCORE_CONTRACT,
            rank_mode=rank_mode,
        )


def test_gbdt_score_evidence_uses_probability_for_all_score_fields() -> None:
    candidate = _candidate(gbdt_score=0.625)
    candidate.update({"score": -1.0, "rank_score": 2.0, "name": "样本公司"})

    payload = score_evidence_payload(
        candidate,
        contract=SHALLOW_GBDT_SCORE_CONTRACT,
    )

    assert payload["candidate_key"] == candidate["candidate_key"]
    assert payload["name"] == "样本公司"
    assert payload["predicted_positive_utility_probability"] == pytest.approx(
        0.625
    )
    assert payload["score"] == pytest.approx(0.625)
    assert payload["rank_score"] == pytest.approx(0.625)
    assert "predicted_net_return_pct" not in payload


def test_ridge_default_contract_preserves_legacy_score_payload_and_ranking() -> None:
    candidate = _candidate(
        security_id="cn-a-share:000001.SZ",
        amount=1_000.0,
        ridge_score=1.25,
    )
    candidate.update({"score": -1.0, "rank_score": 2.0})

    assert candidate_passes_gate(candidate)
    assert selection_rank_key(candidate, rank_mode="main") == (
        -1.25,
        -1_000.0,
        "cn-a-share:000001.SZ",
    )
    assert selection_rank_key(candidate, rank_mode="baseline") == (
        -1_000.0,
        "cn-a-share:000001.SZ",
    )
    assert score_evidence_payload(candidate) == {
        **candidate,
        "predicted_net_return_pct": 1.25,
        "score": 1.25,
        "rank_score": 1.25,
    }
