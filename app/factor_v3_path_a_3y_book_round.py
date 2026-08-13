"""Score a frozen 3y selection-book set on existing Jiaoch v2 QT."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import (
    COMBO_ROUND_VARIANTS,
    FAILED_BOOK_IDS,
    FAILED_COMBO_IDS,
    FAILED_COMBO_OVERLAY_IDS,
    FAILED_OVERLAY_IDS,
    STAGE_GOAL_ID,
    iter_book_round_variants,
    iter_clip_round_variants,
    iter_combo_overlay_variants,
    iter_combo_round_variants,
    merged_kernel,
)
from app.storage import write_json

DEFAULT_QT = Path("data/research_cache/qualified_hold5_stop5_3y_jiaoch.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_3y_book_round")
REPORT_SCHEMA = "path-a-3y-book-round-report/v1"


class PathA3yBookRoundError(ValueError):
    """Raised when the 3y book round cannot be scored."""


def _dual_pass(full_return_pct: float | None, full_mdd_pct: float | None) -> bool:
    if full_return_pct is None or full_mdd_pct is None:
        return False
    return goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=float(full_return_pct),
        max_drawdown_pct=float(full_mdd_pct),
    )


def score_book_round(
    train_trades: list[dict[str, Any]],
    *,
    variants: tuple[dict[str, Any], ...] | None = None,
    excluded_ids: frozenset[str] | None = None,
    stage_goal_id: str = STAGE_GOAL_ID,
) -> dict[str, Any]:
    """Score book kernels on already-loaded trades. No network I/O."""

    chosen = variants or iter_book_round_variants()
    banned = excluded_ids if excluded_ids is not None else FAILED_OVERLAY_IDS
    ids = [str(row["candidate_id"]) for row in chosen]
    overlap = set(ids) & set(banned)
    if overlap:
        raise PathA3yBookRoundError(f"round reuses already-failed ids: {sorted(overlap)}")
    rows: list[dict[str, Any]] = []
    for variant in chosen:
        kernel = merged_kernel(variant)
        try:
            selected = p0._select_kernel_trades(train_trades, kernel_override=kernel)
            metrics = p0._metrics_bundle(selected)
            empty = False
        except p0.PathAP0Error:
            selected = []
            metrics = p0._metrics_bundle([])
            empty = True
        full_ret = metrics.get("portfolio_compounded_return_pct")
        full_mdd = metrics.get("portfolio_max_drawdown_pct")
        dual = False if empty else _dual_pass(full_ret, full_mdd)
        rows.append(
            {
                "candidate_id": variant["candidate_id"],
                "role": variant["role"],
                "rationale": variant["rationale"],
                "kernel": {
                    "top_n": kernel["top_n"],
                    "max_active_positions": kernel["max_active_positions"],
                    "symbol_cooldown_days": kernel["symbol_cooldown_days"],
                    "market_levels": list(kernel["market_levels"]),
                    "required_signal_tags": list(kernel["required_signal_tags"]),
                    "excluded_signal_tags": list(kernel["excluded_signal_tags"]),
                },
                "development_only": True,
                "promotable": False,
                "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
                "empty_selection": empty,
                "full_path_return_pct": full_ret,
                "full_path_mdd_pct": full_mdd,
                "latest_1y_return_pct": metrics.get("rolling_1y_latest_return_pct"),
                "latest_1y_mdd_pct": metrics.get("rolling_1y_latest_max_drawdown_pct"),
                "selected_trade_count": metrics.get("selected_trade_count"),
                "dual_pass_50_15": dual,
                "dual_pass_via": "research_goal_contract.meets_primary_performance_targets",
            }
        )

    passed = [row for row in rows if row["dual_pass_50_15"]]
    ranked = sorted(
        rows,
        key=lambda r: (
            1 if r["empty_selection"] else 0,
            abs(float(r["full_path_mdd_pct"] or 99.0)),
            -float(r["full_path_return_pct"] or -999.0),
        ),
    )
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": stage_goal_id,
        "development_only": True,
        "promotable": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "variant_count": len(rows),
        "failed_ids_excluded": sorted(banned),
        "dual_pass_ids": [row["candidate_id"] for row in passed],
        "current_development_candidate": (
            passed[0]["candidate_id"] if passed else None
        ),
        "best_by_mdd_then_return": ranked[0]["candidate_id"] if ranked else None,
        "fifty_fifteen_met": bool(passed),
        "variants": rows,
    }


def _combo_base_variant(base_id: str) -> dict[str, Any]:
    for row in COMBO_ROUND_VARIANTS:
        if str(row["candidate_id"]) == base_id:
            return row
    raise PathA3yBookRoundError(f"unknown combo base: {base_id!r}")


def score_combo_overlay_round(
    train_trades: list[dict[str, Any]],
    *,
    variants: tuple[dict[str, Any], ...] | None = None,
    excluded_ids: frozenset[str] | None = None,
    stage_goal_id: str = "path-a-3y-combo-overlay-round/v1",
    base_id: str = "combo_vol_t2_m1",
) -> dict[str, Any]:
    """Score overlays on the closest combo book. No network I/O."""

    chosen = variants or iter_combo_overlay_variants()
    banned = (
        excluded_ids
        if excluded_ids is not None
        else (FAILED_OVERLAY_IDS | FAILED_BOOK_IDS | FAILED_COMBO_IDS)
    )
    ids = [str(row["candidate_id"]) for row in chosen]
    overlap = set(ids) & set(banned)
    if overlap:
        raise PathA3yBookRoundError(f"round reuses already-failed ids: {sorted(overlap)}")
    base = _combo_base_variant(base_id)
    kernel = merged_kernel(base)
    try:
        selected = p0._select_kernel_trades(train_trades, kernel_override=kernel)
        empty_base = False
    except p0.PathAP0Error:
        selected = []
        empty_base = True

    rows: list[dict[str, Any]] = []
    for variant in chosen:
        overlay = dict(variant["overlay"])
        if empty_base:
            applied = {
                "accepted": [],
                "blocked_new_count": 0,
                "half_size_count": 0,
            }
            metrics = p0._metrics_bundle([])
            empty = True
        else:
            applied = p0.apply_p0_overlay(
                selected, overlay, kernel_selected=selected
            )
            metrics = p0._metrics_bundle(applied["accepted"])
            empty = False
        full_ret = metrics.get("portfolio_compounded_return_pct")
        full_mdd = metrics.get("portfolio_max_drawdown_pct")
        dual = False if empty else _dual_pass(full_ret, full_mdd)
        rows.append(
            {
                "candidate_id": variant["candidate_id"],
                "role": variant.get("role") or "combo_overlay",
                "rationale": variant["rationale"],
                "base_id": variant.get("base_id") or base_id,
                "overlay": overlay,
                "kernel": {
                    "top_n": kernel["top_n"],
                    "max_active_positions": kernel["max_active_positions"],
                    "symbol_cooldown_days": kernel["symbol_cooldown_days"],
                    "market_levels": list(kernel["market_levels"]),
                    "required_signal_tags": list(kernel["required_signal_tags"]),
                    "excluded_signal_tags": list(kernel["excluded_signal_tags"]),
                },
                "development_only": True,
                "promotable": False,
                "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
                "empty_selection": empty,
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
            1 if r["empty_selection"] else 0,
            abs(float(r["full_path_mdd_pct"] or 99.0)),
            -float(r["full_path_return_pct"] or -999.0),
        ),
    )
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": stage_goal_id,
        "development_only": True,
        "promotable": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "variant_count": len(rows),
        "failed_ids_excluded": sorted(banned),
        "base_id": base_id,
        "base_selected_count": len(selected),
        "dual_pass_ids": [row["candidate_id"] for row in passed],
        "current_development_candidate": (
            passed[0]["candidate_id"] if passed else None
        ),
        "best_by_mdd_then_return": ranked[0]["candidate_id"] if ranked else None,
        "fifty_fifteen_met": bool(passed),
        "variants": rows,
    }


def score_clip_round(
    train_trades: list[dict[str, Any]],
    *,
    variants: tuple[dict[str, Any], ...] | None = None,
    excluded_ids: frozenset[str] | None = None,
    stage_goal_id: str = "path-a-3y-clip-round/v1",
) -> dict[str, Any]:
    """Score tag-clip kernels / skip filters on already-loaded trades."""

    chosen = variants or iter_clip_round_variants()
    banned = (
        excluded_ids
        if excluded_ids is not None
        else (
            FAILED_OVERLAY_IDS
            | FAILED_BOOK_IDS
            | FAILED_COMBO_IDS
            | FAILED_COMBO_OVERLAY_IDS
        )
    )
    ids = [str(row["candidate_id"]) for row in chosen]
    overlap = set(ids) & set(banned)
    if overlap:
        raise PathA3yBookRoundError(f"round reuses already-failed ids: {sorted(overlap)}")
    rows: list[dict[str, Any]] = []
    for variant in chosen:
        kernel = merged_kernel(variant)
        skip_tags = tuple(variant.get("skip_tags") or ())
        try:
            selected = p0._select_kernel_trades(train_trades, kernel_override=kernel)
            if skip_tags:
                skip = set(skip_tags)
                selected = [
                    trade
                    for trade in selected
                    if not (set(trade.get("signal_tags") or []) & skip)
                ]
            if not selected:
                raise p0.PathAP0Error("clip selection empty")
            metrics = p0._metrics_bundle(selected)
            empty = False
        except p0.PathAP0Error:
            selected = []
            metrics = p0._metrics_bundle([])
            empty = True
        full_ret = metrics.get("portfolio_compounded_return_pct")
        full_mdd = metrics.get("portfolio_max_drawdown_pct")
        dual = False if empty else _dual_pass(full_ret, full_mdd)
        rows.append(
            {
                "candidate_id": variant["candidate_id"],
                "role": variant["role"],
                "rationale": variant["rationale"],
                "base_id": variant.get("base_id"),
                "skip_tags": list(skip_tags),
                "kernel": {
                    "top_n": kernel["top_n"],
                    "max_active_positions": kernel["max_active_positions"],
                    "symbol_cooldown_days": kernel["symbol_cooldown_days"],
                    "market_levels": list(kernel["market_levels"]),
                    "required_signal_tags": list(kernel["required_signal_tags"]),
                    "excluded_signal_tags": list(kernel["excluded_signal_tags"]),
                },
                "development_only": True,
                "promotable": False,
                "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
                "empty_selection": empty,
                "full_path_return_pct": full_ret,
                "full_path_mdd_pct": full_mdd,
                "latest_1y_return_pct": metrics.get("rolling_1y_latest_return_pct"),
                "latest_1y_mdd_pct": metrics.get("rolling_1y_latest_max_drawdown_pct"),
                "selected_trade_count": metrics.get("selected_trade_count"),
                "dual_pass_50_15": dual,
                "dual_pass_via": "research_goal_contract.meets_primary_performance_targets",
            }
        )

    passed = [row for row in rows if row["dual_pass_50_15"]]
    ranked = sorted(
        rows,
        key=lambda r: (
            1 if r["empty_selection"] else 0,
            abs(float(r["full_path_mdd_pct"] or 99.0)),
            -float(r["full_path_return_pct"] or -999.0),
        ),
    )
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": stage_goal_id,
        "development_only": True,
        "promotable": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "variant_count": len(rows),
        "failed_ids_excluded": sorted(banned),
        "dual_pass_ids": [row["candidate_id"] for row in passed],
        "current_development_candidate": (
            passed[0]["candidate_id"] if passed else None
        ),
        "best_by_mdd_then_return": ranked[0]["candidate_id"] if ranked else None,
        "fifty_fifteen_met": bool(passed),
        "variants": rows,
    }


def build_path_a_3y_book_round(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
    family: str = "book",
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathA3yBookRoundError(
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
        raise PathA3yBookRoundError(f"qualified trades missing: {qt_path}")
    meta = (qt.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != "jiaoch-daily-bars/shares-cny/v2":
        raise PathA3yBookRoundError("3y QT is not Jiaoch stk_mins v2")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))
    chosen = str(family or "book").strip().casefold()
    if chosen == "combo":
        report = score_book_round(
            train_trades,
            variants=iter_combo_round_variants(),
            excluded_ids=FAILED_OVERLAY_IDS | FAILED_BOOK_IDS,
            stage_goal_id="path-a-3y-combo-round/v1",
        )
    elif chosen == "book":
        report = score_book_round(train_trades)
    elif chosen == "combo_overlay":
        report = score_combo_overlay_round(train_trades)
    elif chosen == "clip":
        report = score_clip_round(train_trades)
    else:
        raise PathA3yBookRoundError(f"unknown family: {family!r}")
    report["qualified_trades_path"] = str(qt_path)
    report["source_policy"] = meta.get("source_policy")
    report["source_version"] = meta.get("source_version")
    report["window"] = meta.get("window")
    return report


def format_book_round_table(report: dict[str, Any]) -> str:
    lines = [
        f"stage={report.get('stage_goal_id')}",
        f"window={report.get('window')}",
        f"source={report.get('source_version')}",
        f"fifty_fifteen_met={report.get('fifty_fifteen_met')}",
        f"dual_pass_ids={report.get('dual_pass_ids')}",
        f"best_by_mdd_then_return={report.get('best_by_mdd_then_return')}",
        f"current_development_candidate={report.get('current_development_candidate')}",
        "id\tfull_ret\tfull_mdd\tlatest_1y_ret\tlatest_1y_mdd\tselected\tdual_pass_50_15",
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
                    str(row["selected_trade_count"]),
                    str(row["dual_pass_50_15"]).lower(),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def write_path_a_3y_book_round(
    report: dict[str, Any], *, output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_book_round_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "fifty_fifteen_met": report.get("fifty_fifteen_met"),
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }
