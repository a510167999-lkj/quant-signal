"""Path A E2: sandwich search between E1 Pareto anchors (no TCB).

Anchors from E1:
  - high-return: favorable market, top3/max2 ~53.5% / -16.6%
  - low-drawdown: breakout+breadth single-name favorable ~38.7% / -13.6%

E2 densifies the space between them under train-locked qualified trades.
Development-only; never auto-trade.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal
from app.research_sweep import sweep_qualified_trades

STAGE_GOAL_ID = "path-a-e2-sandwich-matrix/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A E2：在 E1 双锚点之间夹逼搜索（有利市紧仓 vs breakout/breadth 低回撤），"
    "1x 暴露 + 现实成本 + slot-daily；development_only；无 TCB；不日更；永不自动交易。"
)
REPORT_SCHEMA = "path-a-e2-sandwich-matrix-report/v1"
POINTER_SCHEMA = "path-a-e2-sandwich-matrix-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_e2_sandwich_matrix")

# E1 anchors (development-only reference points)
E1_HIGH_RETURN_ANCHOR = {
    "label": "e1_anchor_mkt_fav_top3_max2",
    "ret_pct": 53.53,
    "mdd_pct": -16.60,
}
E1_LOW_DRAWDOWN_ANCHOR = {
    "label": "e1_anchor_breakout_breadth_single_fav",
    "ret_pct": 38.73,
    "mdd_pct": -13.64,
}


class PathAE2Error(ValueError):
    """Raised when path-A E2 matrix fails closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAE2Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _base(**overrides: Any) -> dict[str, Any]:
    row = {
        "hold_days": 5,
        "exposure_multiplier": 1.0,
        "annual_financing_rate_pct": 0.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "capital_model": "slot-daily",
        "min_trades": 15,
        "fixed_spec": True,
        "symbol_cooldown_days": 5,
        "market_levels": None,
        "required_signal_tags": None,
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "prior_high_trailing_activation_pct": 0.0,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    }
    row.update(overrides)
    return row


def _sandwich_grid() -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []

    # --- High-return side: add filters/exits onto fav top3/max2 ---
    high_books = [
        ("hr_fav_t3m2", 3, 2, ["favorable"], None, 5),
        ("hr_fav_t3m2_cd10", 3, 2, ["favorable"], None, 10),
        ("hr_fav_t2m2", 2, 2, ["favorable"], None, 5),
        ("hr_fav_t3m3", 3, 3, ["favorable"], None, 5),
        ("hr_fn_t3m2", 3, 2, ["favorable", "neutral"], None, 5),
    ]
    high_filters = [
        ("nofilt", None),
        ("bo", ["breakout_20d"]),
        ("br50", ["breadth_advancing_gte_50"]),
        ("br60", ["breadth_advancing_gte_60"]),
        ("ma60", ["breadth_ma20_gte_60"]),
        ("bo_br50", ["breakout_20d", "breadth_advancing_gte_50"]),
        ("bo_br60", ["breakout_20d", "breadth_advancing_gte_60"]),
        ("bo_ma60", ["breakout_20d", "breadth_ma20_gte_60"]),
        ("mom", ["moderate_20d_momentum"]),
        ("bo_mom", ["breakout_20d", "moderate_20d_momentum"]),
    ]
    high_exits = [
        ("x0", 0, None, None, 0.0),
        ("x_gap7", 7, None, None, 0.0),
        ("x_tr7", 0, 7.0, None, 0.0),
        ("x_tr10", 0, 10.0, None, 0.0),
        ("x_pl15", 0, None, 15.0, 1.0),
        ("x_pl18", 0, None, 18.0, 1.0),
        ("x_gap7_tr7", 7, 7.0, None, 0.0),
        ("x_gap7_pl15", 7, None, 15.0, 1.0),
        ("x_tr7_pl15", 0, 7.0, 15.0, 1.0),
        ("x_gap7_tr7_pl15", 7, 7.0, 15.0, 1.0),
    ]

    # Full product would be large; sample structured product of key combos.
    for book_name, top_n, max_pos, levels, _req, cd in high_books:
        for filt_name, tags in high_filters:
            # default no-exit + a few exit packages for promising filters
            exit_subset = high_exits if filt_name in {
                "nofilt",
                "bo",
                "br50",
                "bo_br50",
                "bo_br60",
                "bo_ma60",
            } else [high_exits[0], high_exits[2], high_exits[4], high_exits[6]]
            for exit_name, gap, trail, pl, plf in exit_subset:
                variants.append(
                    _base(
                        label=f"{book_name}__{filt_name}__{exit_name}",
                        top_n=top_n,
                        max_active_positions=max_pos,
                        symbol_cooldown_days=cd,
                        market_levels=levels,
                        required_signal_tags=tags,
                        pre_exit_calendar_gap_days=gap,
                        prior_high_trailing_stop_pct=trail,
                        partial_profit_activation_pct=pl,
                        partial_profit_fraction=plf,
                    )
                )

    # --- Low-drawdown side: relax single-name breakout/breadth ---
    low_books = [
        ("ld_bo_br_t1m1", 1, 1, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], 10),
        ("ld_bo_br_t2m1", 2, 1, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], 10),
        ("ld_bo_br_t2m2", 2, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], 5),
        ("ld_bo_br_t3m2", 3, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], 5),
        ("ld_bo_br_t3m3", 3, 3, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], 5),
        ("ld_bo_br_fn_t2m2", 2, 2, ["favorable", "neutral"], ["breakout_20d", "breadth_advancing_gte_50"], 5),
        ("ld_bo_br_fn_t3m2", 3, 2, ["favorable", "neutral"], ["breakout_20d", "breadth_advancing_gte_50"], 5),
        ("ld_bo_only_t2m2", 2, 2, ["favorable"], ["breakout_20d"], 5),
        ("ld_br50_only_t2m2", 2, 2, ["favorable"], ["breadth_advancing_gte_50"], 5),
        ("ld_bo_br60_t2m2", 2, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_60"], 5),
        ("ld_bo_br60_t3m2", 3, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_60"], 5),
    ]
    low_exits = [
        ("x0", 0, None, None, 0.0),
        ("x_gap7", 7, None, None, 0.0),
        ("x_tr7", 0, 7.0, None, 0.0),
        ("x_pl15", 0, None, 15.0, 1.0),
        ("x_gap7_tr7", 7, 7.0, None, 0.0),
        ("x_gap7_pl15", 7, None, 15.0, 1.0),
        ("x_gap7_tr7_pl15", 7, 7.0, 15.0, 1.0),  # e1 low-dd style package
    ]
    for book_name, top_n, max_pos, levels, tags, cd in low_books:
        for exit_name, gap, trail, pl, plf in low_exits:
            variants.append(
                _base(
                    label=f"{book_name}__{exit_name}",
                    top_n=top_n,
                    max_active_positions=max_pos,
                    symbol_cooldown_days=cd,
                    market_levels=levels,
                    required_signal_tags=tags,
                    pre_exit_calendar_gap_days=gap,
                    prior_high_trailing_stop_pct=trail,
                    partial_profit_activation_pct=pl,
                    partial_profit_fraction=plf,
                )
            )

    # --- Mid: exclude large gap-ups (execution/chase risk) ---
    for levels, tag in (
        (["favorable"], "fav"),
        (["favorable", "neutral"], "fn"),
    ):
        for tags, filt in (
            (None, "nofilt"),
            (["breakout_20d"], "bo"),
            (["breakout_20d", "breadth_advancing_gte_50"], "bo_br50"),
        ):
            variants.append(
                _base(
                    label=f"mid_excl_gap5_{tag}_{filt}_t3m2",
                    top_n=3,
                    max_active_positions=2,
                    market_levels=levels,
                    required_signal_tags=tags,
                    excluded_signal_tags=["price_gap_up_gte_5"],
                )
            )
            variants.append(
                _base(
                    label=f"mid_excl_gap25_{tag}_{filt}_t3m2",
                    top_n=3,
                    max_active_positions=2,
                    market_levels=levels,
                    required_signal_tags=tags,
                    excluded_signal_tags=["price_gap_up_2_to_5", "price_gap_up_gte_5"],
                )
            )

    # Dedup labels
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in variants:
        if row["label"] in seen:
            continue
        seen.add(row["label"])
        unique.append(row)
    return unique


def _filter_trades_to_train_window(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in trades:
        signal_date = str(trade.get("signal_date") or "")[:10]
        if not signal_date:
            continue
        if signal_date >= freeze.TRAIN_EXCLUSIVE_END_DATE:
            continue
        if signal_date > freeze.TRAIN_INCLUSIVE_SESSION_END:
            continue
        out.append(trade)
    if not out:
        raise PathAE2Error("no trades remain after train-window filter")
    return out


def _score_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    ret = metrics.get("rolling_1y_latest_return_pct_raw")
    if ret is None:
        ret = metrics.get("rolling_1y_latest_return_pct")
    mdd = metrics.get("rolling_1y_latest_max_drawdown_pct_raw")
    if mdd is None:
        mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")
    if ret is None or mdd is None:
        return {
            "scored": False,
            "pass_return_50": False,
            "pass_drawdown_15": False,
            "pass_both_50_15": False,
        }
    ret_f = float(ret)
    mdd_f = float(mdd)
    pass_ret = ret_f >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
    pass_dd = abs(mdd_f) <= goal.TARGET_MAX_DRAWDOWN_PCT
    # Distance helpers for sandwich ranking
    ret_gap = goal.TARGET_ROLLING_12M_NET_RETURN_PCT - ret_f  # >0 means short of return
    dd_gap = abs(mdd_f) - goal.TARGET_MAX_DRAWDOWN_PCT  # >0 means over drawdown
    return {
        "scored": True,
        "rolling_1y_latest_return_pct": ret_f,
        "rolling_1y_latest_max_drawdown_pct": mdd_f,
        "calmar_latest_12m": metrics.get("calmar_latest_12m_raw")
        or metrics.get("calmar_latest_12m"),
        "selected_trade_count": metrics.get("selected_trade_count"),
        "signal_days": metrics.get("signal_days"),
        "trade_win_rate_pct": metrics.get("trade_win_rate_pct_raw")
        or metrics.get("trade_win_rate_pct"),
        "trade_profit_factor": metrics.get("trade_profit_factor_raw")
        or metrics.get("trade_profit_factor"),
        "rolling_1y_latest_full_window": metrics.get("rolling_1y_latest_full_window"),
        "pass_return_50": pass_ret,
        "pass_drawdown_15": pass_dd,
        "pass_both_50_15": pass_ret and pass_dd,
        "return_shortfall_pct": max(ret_gap, 0.0),
        "drawdown_excess_pct": max(dd_gap, 0.0),
        "combined_shortfall_pct": max(ret_gap, 0.0) + max(dd_gap, 0.0),
    }


def _run_variant(
    trades: list[dict[str, Any]],
    variant: dict[str, Any],
) -> dict[str, Any]:
    trade_copy = deepcopy(trades)
    try:
        payload = sweep_qualified_trades(
            trade_copy,
            hold_days=int(variant["hold_days"]),
            top_n=int(variant["top_n"]),
            symbol_cooldown_days=int(variant["symbol_cooldown_days"]),
            max_active_positions=int(variant["max_active_positions"]),
            min_trades=int(variant["min_trades"]),
            max_filter_size=1,
            target_win_rate_pct=52.0,
            target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
            target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            exposure_multipliers=[float(variant["exposure_multiplier"])],
            annual_financing_rate_pct=float(variant["annual_financing_rate_pct"]),
            roundtrip_cost_bps=float(variant["roundtrip_cost_bps"]),
            slippage_bps=float(variant["slippage_bps"]),
            capital_model=str(variant["capital_model"]),
            required_signal_tags=variant.get("required_signal_tags"),
            excluded_signal_tags=variant.get("excluded_signal_tags"),
            market_levels=variant.get("market_levels"),
            force_exposure_multipliers=True,
            pre_exit_calendar_gap_days=int(variant.get("pre_exit_calendar_gap_days") or 0),
            prior_high_trailing_stop_pct=variant.get("prior_high_trailing_stop_pct"),
            prior_high_trailing_activation_pct=float(
                variant.get("prior_high_trailing_activation_pct") or 0.0
            ),
            partial_profit_activation_pct=variant.get("partial_profit_activation_pct"),
            partial_profit_fraction=float(variant.get("partial_profit_fraction") or 0.0),
            correlation_threshold=None,
            fixed_spec=True,
        )
    except Exception as exc:
        return {
            "label": variant["label"],
            "variant": _compact_variant(variant),
            "error": f"{type(exc).__name__}: {exc}",
            "scored": False,
            "pass_both_50_15": False,
            "combined_shortfall_pct": 999.0,
        }

    rows = payload.get("top") or payload.get("rows") or []
    if not rows:
        return {
            "label": variant["label"],
            "variant": _compact_variant(variant),
            "error": "no_rows",
            "scored": False,
            "pass_both_50_15": False,
            "combined_shortfall_pct": 999.0,
            "returned_count": payload.get("returned_count"),
        }
    row = rows[0]
    for candidate in rows:
        if float(candidate.get("exposure_multiplier") or 0) == 1.0:
            row = candidate
            break
    score = _score_metrics(row)
    return {
        "label": variant["label"],
        "variant": _compact_variant(variant),
        "spec_label": row.get("label"),
        **score,
        "target_all_pass": row.get("target_all_pass"),
    }


def _compact_variant(variant: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "top_n",
        "max_active_positions",
        "symbol_cooldown_days",
        "capital_model",
        "exposure_multiplier",
        "roundtrip_cost_bps",
        "slippage_bps",
        "market_levels",
        "required_signal_tags",
        "excluded_signal_tags",
        "pre_exit_calendar_gap_days",
        "prior_high_trailing_stop_pct",
        "partial_profit_activation_pct",
        "partial_profit_fraction",
    )
    return {k: variant.get(k) for k in keys}


def build_path_a_e2_sandwich_matrix(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
    max_variants: int | None = None,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()
    trades_path = (root / (qualified_trades_path or DEFAULT_QUALIFIED_TRADES)).resolve()
    if not trades_path.is_file():
        raise PathAE2Error(f"qualified trades missing: {trades_path}")

    payload = json.loads(trades_path.read_text(encoding="utf-8"))
    trades = payload.get("qualified_trades")
    if type(trades) is not list or not trades:
        raise PathAE2Error("qualified_trades empty")
    trades = _filter_trades_to_train_window(trades)
    file_sha = hashlib.sha256(trades_path.read_bytes()).hexdigest()

    variants = _sandwich_grid()
    if max_variants is not None:
        variants = variants[: max(1, int(max_variants))]

    results = [_run_variant(trades, variant) for variant in variants]
    scored = [r for r in results if r.get("scored") is True]
    both = [r for r in scored if r.get("pass_both_50_15") is True]
    pass_dd = [r for r in scored if r.get("pass_drawdown_15") is True]
    pass_ret = [r for r in scored if r.get("pass_return_50") is True]

    def sort_key(row: dict[str, Any]) -> tuple:
        return (
            1 if row.get("pass_both_50_15") else 0,
            -float(row.get("combined_shortfall_pct") or 999),
            1 if row.get("pass_drawdown_15") else 0,
            1 if row.get("pass_return_50") else 0,
            float(row.get("rolling_1y_latest_return_pct") or -999),
            -abs(float(row.get("rolling_1y_latest_max_drawdown_pct") or 999)),
        )

    ranked = sorted(scored, key=sort_key, reverse=True)
    best = ranked[0] if ranked else None

    # Nearest-to-frontier subsets
    near_return = sorted(
        [r for r in scored if r.get("pass_return_50")],
        key=lambda r: abs(float(r.get("rolling_1y_latest_max_drawdown_pct") or 999)),
    )[:8]
    near_dd = sorted(
        [r for r in scored if r.get("pass_drawdown_15")],
        key=lambda r: -float(r.get("rolling_1y_latest_return_pct") or -999),
    )[:8]

    try:
        rel = str(trades_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(trades_path)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_e2_sandwich_matrix",
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "daily_incremental_sync_required": False,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_required": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "e1_anchors": {
            "high_return": E1_HIGH_RETURN_ANCHOR,
            "low_drawdown": E1_LOW_DRAWDOWN_ANCHOR,
        },
        "source_qualified_trades": {
            "path": rel,
            "file_sha256": file_sha,
            "trade_count_raw": len(payload.get("qualified_trades") or []),
            "trade_count_train_filtered": len(trades),
        },
        "targets": {
            "rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            "max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        },
        "variant_count": len(variants),
        "scored_count": len(scored),
        "pass_return_count": len(pass_ret),
        "pass_drawdown_count": len(pass_dd),
        "pass_both_count": len(both),
        "any_variant_passes_50_15": bool(both),
        "effective_strategy_found": False,
        "best_variant": best,
        "top_variants": ranked[:15],
        "nearest_pass_return_by_drawdown": near_return,
        "nearest_pass_drawdown_by_return": near_dd,
        "all_results": results,
        "ok": True,
        "notes": (
            "ok=true means E2 sandwich matrix completed. "
            "effective_strategy_found remains false until OOS-validated. "
            "Ranking minimizes combined shortfall to 50/15."
        ),
        "next_actions": [
            "若有双过：锁定规格做 rolling 稳定性，再规划 final-OOS",
            "若仍无：围绕最小 combined_shortfall 再加密 1 轮",
            "不补 TCB；永不自动交易",
        ],
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_e2_sandwich_matrix(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAE2Error("refuse to write non-ok report")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-e2-sandwich-matrix.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAE2Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)
    best = report.get("best_variant") or {}
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_e2_sandwich_matrix",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "any_variant_passes_50_15": report.get("any_variant_passes_50_15"),
        "effective_strategy_found": False,
        "pass_both_count": report.get("pass_both_count"),
        "pass_drawdown_count": report.get("pass_drawdown_count"),
        "pass_return_count": report.get("pass_return_count"),
        "best_label": best.get("label"),
        "best_return_pct": best.get("rolling_1y_latest_return_pct"),
        "best_mdd_pct": best.get("rolling_1y_latest_max_drawdown_pct"),
        "best_combined_shortfall_pct": best.get("combined_shortfall_pct"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_QUALIFIED_TRADES",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAE2Error",
    "build_path_a_e2_sandwich_matrix",
    "write_path_a_e2_sandwich_matrix",
]
