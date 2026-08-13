"""Score a frozen 3y drawdown-reduction set on existing Jiaoch v2 QT.

Uses the shipped P0 overlay + metrics path. Dual-pass is always
``research_goal_contract.meets_primary_performance_targets``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_drawdown_round_specs import (
    STAGE_GOAL_ID,
    iter_drawdown_round_variants,
)
from app.storage import write_json

DEFAULT_QT = Path("data/research_cache/qualified_hold5_stop5_3y_jiaoch.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_3y_drawdown_round")
REPORT_SCHEMA = "path-a-3y-drawdown-round-report/v1"


class PathA3yDrawdownRoundError(ValueError):
    """Raised when the 3y drawdown round cannot be scored."""


def _dual_pass(full_return_pct: float | None, full_mdd_pct: float | None) -> bool:
    if full_return_pct is None or full_mdd_pct is None:
        return False
    return goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=float(full_return_pct),
        max_drawdown_pct=float(full_mdd_pct),
    )


def score_drawdown_round(
    kernel_selected: list[dict[str, Any]],
    *,
    variants: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    """Score overlays on already-selected e4 kernel trades. No I/O."""

    chosen = variants or iter_drawdown_round_variants()
    rows: list[dict[str, Any]] = []
    for variant in chosen:
        overlay = dict(variant["overlay"])
        applied = p0.apply_p0_overlay(
            kernel_selected, overlay, kernel_selected=kernel_selected
        )
        metrics = p0._metrics_bundle(applied["accepted"])
        full_ret = metrics.get("portfolio_compounded_return_pct")
        full_mdd = metrics.get("portfolio_max_drawdown_pct")
        dual = _dual_pass(full_ret, full_mdd)
        rows.append(
            {
                "candidate_id": variant["candidate_id"],
                "role": variant["role"],
                "rationale": variant["rationale"],
                "overlay": overlay,
                "signal_kernel_unchanged": True,
                "development_only": True,
                "promotable": False,
                "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
                "full_path_return_pct": full_ret,
                "full_path_mdd_pct": full_mdd,
                "latest_1y_return_pct": metrics.get("rolling_1y_latest_return_pct"),
                "latest_1y_mdd_pct": metrics.get("rolling_1y_latest_max_drawdown_pct"),
                "selected_trade_count": metrics.get("selected_trade_count"),
                "dual_pass_50_15": dual,
                "dual_pass_via": "research_goal_contract.meets_primary_performance_targets",
                "overlay_audit": {
                    "accepted_count": len(applied["accepted"]),
                    "blocked_new_count": applied["blocked_new_count"],
                    "half_size_count": applied["half_size_count"],
                },
            }
        )

    passed = [row for row in rows if row["dual_pass_50_15"]]
    ranked = sorted(
        rows,
        key=lambda r: (
            abs(float(r["full_path_mdd_pct"] or 99.0)),
            -float(r["full_path_return_pct"] or -999.0),
        ),
    )
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "development_only": True,
        "promotable": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "variant_count": len(rows),
        "dual_pass_ids": [row["candidate_id"] for row in passed],
        "current_development_candidate": (
            passed[0]["candidate_id"] if passed else None
        ),
        "best_by_mdd_then_return": ranked[0]["candidate_id"] if ranked else None,
        "fifty_fifteen_met": bool(passed),
        "variants": rows,
    }


def build_path_a_3y_drawdown_round(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathA3yDrawdownRoundError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / DEFAULT_QT)
    )
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathA3yDrawdownRoundError(f"qualified trades missing: {qt_path}")
    meta = (qt.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != "jiaoch-daily-bars/shares-cny/v2":
        raise PathA3yDrawdownRoundError("3y QT is not Jiaoch stk_mins v2")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))
    kernel_selected = p0._select_kernel_trades(train_trades)
    report = score_drawdown_round(kernel_selected)
    report["qualified_trades_path"] = str(qt_path)
    report["kernel_selected_count"] = len(kernel_selected)
    report["source_policy"] = meta.get("source_policy")
    report["source_version"] = meta.get("source_version")
    report["window"] = meta.get("window")
    return report


def format_drawdown_round_table(report: dict[str, Any]) -> str:
    lines = [
        f"stage={report.get('stage_goal_id')}",
        f"window={report.get('window')}",
        f"source={report.get('source_version')}",
        f"kernel_selected={report.get('kernel_selected_count')}",
        f"fifty_fifteen_met={report.get('fifty_fifteen_met')}",
        f"dual_pass_ids={report.get('dual_pass_ids')}",
        f"best_by_mdd_then_return={report.get('best_by_mdd_then_return')}",
        f"current_development_candidate={report.get('current_development_candidate')}",
        "id\tfull_ret\tfull_mdd\tlatest_1y_ret\tlatest_1y_mdd\tdual_pass_50_15",
    ]
    for row in report.get("variants") or []:
        lines.append(
            "\t".join(
                [
                    str(row["candidate_id"]),
                    f"{row['full_path_return_pct']}",
                    f"{row['full_path_mdd_pct']}",
                    f"{row['latest_1y_return_pct']}",
                    f"{row['latest_1y_mdd_pct']}",
                    str(row["dual_pass_50_15"]).lower(),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def write_path_a_3y_drawdown_round(
    report: dict[str, Any], *, output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_drawdown_round_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "fifty_fifteen_met": report.get("fifty_fifteen_met"),
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }
