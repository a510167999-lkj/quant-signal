"""P1-A acceptance scoreboard protocol (worst-window dashboard).

Forces every candidate variant to report W1–W6 (catalog §3.2 P1-A). A variant
missing any W field is recorded as a violation; any PR/report carrying a
violation fails acceptance. This is a reporting/validation layer only — it does
not recompute metrics, it projects an existing metrics bundle onto the fixed
W scoreboard.
"""

from __future__ import annotations

from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import research_goal_contract as goal

STAGE_GOAL_ID = "path-a-p1-acceptance-protocol/v1"
PROTOCOL_VERSION = "v1"

# Catalog §3.2 P1-A — the six mandatory worst-window fields.
W_FIELDS: tuple[str, ...] = (
    "W1_worst_rolling_return_pct",
    "W2_worst_rolling_mdd_pct",
    "W3_both_pass_rate",
    "W4_return_pass_rate",
    "W4_drawdown_pass_rate",
    "W5_latest_rolling_pass_50_15",
    "W6_full_path_return_pct",
    "W6_full_path_mdd_pct",
    "W6_full_path_mdd_pass_15",
)


def build_w_scoreboard(
    metrics: dict[str, Any] | None,
    *,
    rolling_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a metrics bundle (+ optional rolling summary) onto W1–W6.

    Reuses train_replay helpers; no recomputation. All fields default to None
    when the source bundle lacks them, so missing data shows up as a violation
    rather than a silent zero.
    """

    metrics = metrics or {}
    rs = rolling_summary or metrics.get("rolling_12m_summary") or {}
    latest_ret = metrics.get("rolling_1y_latest_return_pct")
    latest_mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")
    full_mdd = metrics.get("portfolio_max_drawdown_pct")
    return {
        "W1_worst_rolling_return_pct": rs.get("min_return_pct"),
        "W2_worst_rolling_mdd_pct": rs.get("worst_mdd_pct"),
        "W3_both_pass_rate": rs.get("both_pass_rate"),
        "W4_return_pass_rate": rs.get("return_pass_rate"),
        "W4_drawdown_pass_rate": rs.get("drawdown_pass_rate"),
        "W5_latest_rolling_pass_50_15": train_replay._pass_50_15(latest_ret, latest_mdd),
        "W6_full_path_return_pct": metrics.get("portfolio_compounded_return_pct"),
        "W6_full_path_mdd_pct": full_mdd,
        "W6_full_path_mdd_pass_15": full_mdd is not None
        and abs(float(full_mdd)) <= goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def missing_w_fields(scoreboard: dict[str, Any] | None) -> list[str]:
    """Return the W fields that are absent or None in a scoreboard."""

    if not scoreboard:
        return list(W_FIELDS)
    return [f for f in W_FIELDS if scoreboard.get(f) is None]


def validate_variants(
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate that every variant carries a complete W scoreboard.

    Returns ``{all_complete: bool, violations: [{candidate_id, missing}]}``.
    Acceptance fails when ``all_complete`` is False (catalog §3.2 P1-A rule).
    """

    violations: list[dict[str, Any]] = []
    for var in variants:
        cid = var.get("candidate_id") or var.get("id") or "<unknown>"
        board = var.get("scoreboard") or var.get("w_scoreboard")
        missing = missing_w_fields(board)
        if missing:
            violations.append({"candidate_id": cid, "missing": missing})
    return {
        "protocol_version": PROTOCOL_VERSION,
        "w_fields": list(W_FIELDS),
        "variant_count": len(variants),
        "all_complete": not violations,
        "violations": violations,
    }


__all__ = [
    "PROTOCOL_VERSION",
    "STAGE_GOAL_ID",
    "W_FIELDS",
    "build_w_scoreboard",
    "missing_w_fields",
    "validate_variants",
]
