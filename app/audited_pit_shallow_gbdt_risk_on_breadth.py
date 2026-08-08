"""Frozen risk-on breadth filter for the shallow GBDT development hypothesis."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from app.audited_pit_shallow_gbdt import SHALLOW_GBDT_OOF_SPEC


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


MARKET_BREADTH_GATE = {
    "feature": "cross_section_above_ma20_fraction",
    "comparison": "greater_than_or_equal_unrounded_float64",
    "minimum": 0.5,
    "missing_policy": "fail_closed_run",
}

SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC = deepcopy(SHALLOW_GBDT_OOF_SPEC)
SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC.update(
    {
        "schema_version": (
            "development-pit-cross-sectional-shallow-gbdt-risk-on-breadth-"
            "utility-logit-rolling-126-oof/v1"
        ),
        "signal_tag": (
            "cross_sectional_shallow_gbdt_risk_on_breadth_"
            "utility_logit_rolling_126_oof"
        ),
    }
)
SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC["selection"] = {
    **SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC["selection"],
    "market_breadth_gate": dict(MARKET_BREADTH_GATE),
}
_SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC_SHA256 = (
    "9b3df2039a3d39b999fd15856c5e8460fe23212b13217625bd21727018adfd19"
)


def assert_frozen_risk_on_breadth_strategy(
    strategy_spec: Mapping[str, Any],
) -> None:
    actual = dict(strategy_spec)
    expected = dict(SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC)
    if (
        actual != expected
        or _canonical_sha256(actual)
        != _SHALLOW_GBDT_RISK_ON_BREADTH_OOF_SPEC_SHA256
    ):
        raise ValueError("risk-on breadth strategy is not frozen")
