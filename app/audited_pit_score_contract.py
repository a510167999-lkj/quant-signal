"""Explicit score semantics shared by audited PIT model variants."""

from __future__ import annotations

import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping


RIDGE_SCORE_CONTRACT: Mapping[str, Any] = MappingProxyType(
    {
        "field": "predicted_net_return_pct",
        "semantic": "predicted_net_return_after_costs",
        "unit": "percentage_points",
        "domain": "finite_real",
        "gate": "strict_gt",
        "value": 0.0,
        "main_rank_mode": "score_descending",
    }
)

SHALLOW_GBDT_SCORE_CONTRACT: Mapping[str, Any] = MappingProxyType(
    {
        "field": "predicted_positive_utility_probability",
        "semantic": (
            "predicted_positive_date_normalized_clipped_utility_probability"
        ),
        "unit": "probability",
        "domain": "closed_interval_0_1",
        "gate": "strict_gt",
        "value": 0.5,
        "main_rank_mode": "score_descending",
    }
)


def _frozen_contract(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    value = dict(contract)
    if value == dict(RIDGE_SCORE_CONTRACT):
        return RIDGE_SCORE_CONTRACT
    if value == dict(SHALLOW_GBDT_SCORE_CONTRACT):
        return SHALLOW_GBDT_SCORE_CONTRACT
    raise ValueError("score contract must match a frozen contract")


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def candidate_score(
    candidate: Mapping[str, Any],
    *,
    contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> float:
    contract = _frozen_contract(contract)
    field = str(contract.get("field") or "")
    if not field or field not in candidate:
        raise ValueError("candidate score field is missing")
    if (
        field != "predicted_net_return_pct"
        and "predicted_net_return_pct" in candidate
    ):
        raise ValueError(
            "predicted_net_return_pct is forbidden by this score contract"
        )
    score = _finite_number(candidate[field], label="candidate score")
    domain = contract.get("domain")
    if domain == "finite_real":
        return score
    if domain == "closed_interval_0_1":
        if score < 0.0 or score > 1.0:
            raise ValueError("candidate score is outside contract domain")
        return score
    raise ValueError("score contract domain is unsupported")


def candidate_passes_gate(
    candidate: Mapping[str, Any],
    *,
    contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> bool:
    contract = _frozen_contract(contract)
    if contract.get("gate") != "strict_gt":
        raise ValueError("score contract gate is unsupported")
    threshold = _finite_number(
        contract.get("value"),
        label="score contract gate value",
    )
    return candidate_score(candidate, contract=contract) > threshold


def selection_rank_key(
    candidate: Mapping[str, Any],
    *,
    rank_mode: str,
    contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> tuple[Any, ...]:
    contract = _frozen_contract(contract)
    amount = _finite_number(
        candidate.get("candidate_amount"),
        label="candidate amount",
    )
    security_id = candidate.get("security_id")
    if not isinstance(security_id, str) or not security_id:
        raise ValueError("candidate security_id is invalid")
    score = candidate_score(candidate, contract=contract)
    if rank_mode in {"main", "predicted_net_return"}:
        if contract.get("main_rank_mode") != "score_descending":
            raise ValueError("score contract rank mode is unsupported")
        return (
            -score,
            -amount,
            security_id,
        )
    if rank_mode in {"baseline", "signal_date_amount"}:
        return (-amount, security_id)
    raise ValueError("selection rank mode is unsupported")


def score_evidence_payload(
    candidate: Mapping[str, Any],
    *,
    contract: Mapping[str, Any] = RIDGE_SCORE_CONTRACT,
) -> dict[str, Any]:
    score = candidate_score(candidate, contract=contract)
    field = str(contract["field"])
    return {
        **candidate,
        field: score,
        "score": score,
        "rank_score": score,
    }
