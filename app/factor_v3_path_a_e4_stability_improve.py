"""Path A E4: improve rolling stability of preferred development candidates.

Starts from E3 primary (fav + ma60 + top3/max2) and densifies filters/exits/
positioning to raise rolling both-pass rate and cut worst-window MDD.
Auto-iterates a large fixed grid; no TCB; no auto-trade; train-locked only.
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

STAGE_GOAL_ID = "path-a-e4-stability-improve/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A E4：以 E3 robust ma60 为母版自动扫稳定性改进变体，"
    "目标提高 rolling 双过率、压低最差窗 MDD；development_only；无 TCB；"
    "不日更；永不自动交易；未过稳定性前不授权 OOS。"
)
REPORT_SCHEMA = "path-a-e4-stability-improve-report/v1"
POINTER_SCHEMA = "path-a-e4-stability-improve-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_e4_stability_improve")

# Same stability gates as E3 (must stay aligned).
MIN_SELECTED_TRADES = 40
MIN_SIGNAL_DAYS = 30
MIN_ROLLING_WINDOWS = 20
MIN_BOTH_PASS_WINDOW_RATE = 0.25
MAX_WORST_WINDOW_MDD_ABS = 25.0


class PathAE4Error(ValueError):
    """Raised when path-A E4 fails closed."""


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
        raise PathAE4Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _filter_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for trade in trades:
        d = str(trade.get("signal_date") or "")[:10]
        if not d or d >= freeze.TRAIN_EXCLUSIVE_END_DATE:
            continue
        if d > freeze.TRAIN_INCLUSIVE_SESSION_END:
            continue
        out.append(trade)
    if not out:
        raise PathAE4Error("no trades after train filter")
    return out


def _window_stats(windows: list[dict[str, Any]]) -> dict[str, Any]:
    if not windows:
        return {
            "window_count": 0,
            "both_pass_count": 0,
            "both_pass_rate": 0.0,
            "return_pass_rate": 0.0,
            "drawdown_pass_rate": 0.0,
            "min_return_pct": None,
            "max_return_pct": None,
            "worst_mdd_pct": None,
            "best_mdd_pct": None,
            "worst_return_window": None,
            "worst_mdd_window": None,
        }
    rets: list[float] = []
    mdds: list[float] = []
    both = ret_pass = dd_pass = 0
    worst_ret_w = worst_mdd_w = None
    for w in windows:
        ret = w.get("return_pct_raw", w.get("return_pct"))
        mdd = w.get("max_drawdown_pct_raw", w.get("max_drawdown_pct"))
        if ret is None or mdd is None:
            continue
        ret_f = float(ret)
        mdd_f = float(mdd)
        rets.append(ret_f)
        mdds.append(mdd_f)
        rp = ret_f >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
        dp = abs(mdd_f) <= goal.TARGET_MAX_DRAWDOWN_PCT
        ret_pass += int(rp)
        dd_pass += int(dp)
        both += int(rp and dp)
        if worst_ret_w is None or ret_f < float(
            worst_ret_w.get("return_pct_raw", worst_ret_w.get("return_pct"))
        ):
            worst_ret_w = w
        if worst_mdd_w is None or mdd_f < float(
            worst_mdd_w.get("max_drawdown_pct_raw", worst_mdd_w.get("max_drawdown_pct"))
        ):
            worst_mdd_w = w
    n = len(rets)

    def brief(w: dict[str, Any] | None) -> dict[str, Any] | None:
        if not w:
            return None
        return {
            "start_date": w.get("start_date"),
            "end_date": w.get("end_date"),
            "return_pct": w.get("return_pct_raw", w.get("return_pct")),
            "max_drawdown_pct": w.get("max_drawdown_pct_raw", w.get("max_drawdown_pct")),
            "selected_trade_count": w.get("selected_trade_count"),
        }

    return {
        "window_count": n,
        "both_pass_count": both,
        "both_pass_rate": both / n if n else 0.0,
        "return_pass_rate": ret_pass / n if n else 0.0,
        "drawdown_pass_rate": dd_pass / n if n else 0.0,
        "min_return_pct": min(rets) if rets else None,
        "max_return_pct": max(rets) if rets else None,
        "worst_mdd_pct": min(mdds) if mdds else None,
        "best_mdd_pct": max(mdds) if mdds else None,
        "worst_return_window": brief(worst_ret_w),
        "worst_mdd_window": brief(worst_mdd_w),
    }


def _stability_flags(
    *,
    latest_dual: bool,
    trades_n: int,
    days_n: int,
    rolling: dict[str, Any],
) -> dict[str, Any]:
    worst_mdd = rolling.get("worst_mdd_pct")
    thickness_ok = trades_n >= MIN_SELECTED_TRADES and days_n >= MIN_SIGNAL_DAYS
    rolling_count_ok = int(rolling.get("window_count") or 0) >= MIN_ROLLING_WINDOWS
    both_rate = float(rolling.get("both_pass_rate") or 0.0)
    both_rate_ok = both_rate >= MIN_BOTH_PASS_WINDOW_RATE
    worst_mdd_ok = (
        worst_mdd is not None and abs(float(worst_mdd)) <= MAX_WORST_WINDOW_MDD_ABS
    )
    ready = (
        latest_dual
        and thickness_ok
        and rolling_count_ok
        and both_rate_ok
        and worst_mdd_ok
    )
    return {
        "latest_dual_pass_50_15": latest_dual,
        "thickness_ok": thickness_ok,
        "rolling_count_ok": rolling_count_ok,
        "both_rate_ok": both_rate_ok,
        "worst_mdd_ok": worst_mdd_ok,
        "stability_ready_for_oos_planning": ready,
        "both_pass_rate": both_rate,
        "selected_trade_count": trades_n,
        "signal_days": days_n,
        "worst_mdd_pct": worst_mdd,
    }


def _base_spec(**overrides: Any) -> dict[str, Any]:
    row = {
        "hold_days": 5,
        "exposure_multiplier": 1.0,
        "annual_financing_rate_pct": 0.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "capital_model": "slot-daily",
        "min_trades": 15,
        "symbol_cooldown_days": 5,
        "market_levels": ["favorable"],
        "required_signal_tags": ["breadth_ma20_gte_60"],
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
        "top_n": 3,
        "max_active_positions": 2,
    }
    row.update(overrides)
    return row


def _e4_variant_grid() -> list[dict[str, Any]]:
    """Dense grid around robust ma60 + a few hybrid expansions."""

    variants: list[dict[str, Any]] = []

    # --- Round 1: position book x filters x exits on favorable ---
    books = [
        ("t3m2", 3, 2, 5),
        ("t3m2_cd10", 3, 2, 10),
        ("t2m2", 2, 2, 5),
        ("t2m1", 2, 1, 5),
        ("t3m3", 3, 3, 5),
        ("t3m1", 3, 1, 5),
        ("t1m1", 1, 1, 10),
    ]
    filters = [
        ("ma60", ["breadth_ma20_gte_60"]),
        ("ma60_bo", ["breadth_ma20_gte_60", "breakout_20d"]),
        ("ma60_br50", ["breadth_ma20_gte_60", "breadth_advancing_gte_50"]),
        ("ma60_br60", ["breadth_ma20_gte_60", "breadth_advancing_gte_60"]),
        ("ma60_mom", ["breadth_ma20_gte_60", "moderate_20d_momentum"]),
        ("ma60_bo_br50", ["breadth_ma20_gte_60", "breakout_20d", "breadth_advancing_gte_50"]),
        ("ma60_rsi", ["breadth_ma20_gte_60", "balanced_rsi"]),
        ("ma60_gap02", ["breadth_ma20_gte_60", "price_gap_up_0_to_2"]),
    ]
    markets = [
        ("fav", ["favorable"]),
        ("fn", ["favorable", "neutral"]),
    ]
    exits = [
        ("x0", 0, None, None, 0.0),
        ("x_gap7", 7, None, None, 0.0),
        ("x_tr5", 0, 5.0, None, 0.0),
        ("x_tr7", 0, 7.0, None, 0.0),
        ("x_tr10", 0, 10.0, None, 0.0),
        ("x_pl12", 0, None, 12.0, 1.0),
        ("x_pl15", 0, None, 15.0, 1.0),
        ("x_pl18", 0, None, 18.0, 1.0),
        ("x_gap7_tr7", 7, 7.0, None, 0.0),
        ("x_gap7_pl15", 7, None, 15.0, 1.0),
        ("x_tr7_pl15", 0, 7.0, 15.0, 1.0),
        ("x_gap7_tr7_pl15", 7, 7.0, 15.0, 1.0),
        ("x_tr5_pl12", 0, 5.0, 12.0, 1.0),
    ]
    excludes = [
        ("ex0", None),
        ("ex_gap5", ["price_gap_up_gte_5"]),
        ("ex_gap25", ["price_gap_up_2_to_5", "price_gap_up_gte_5"]),
        ("ex_gapdown", ["price_gap_down"]),
    ]

    # Structured product but cap explosion: full books x subset filters x subset exits x excludes
    primary_filters = [
        "ma60",
        "ma60_bo",
        "ma60_br50",
        "ma60_br60",
        "ma60_mom",
        "ma60_bo_br50",
        "ma60_rsi",
    ]
    primary_exits = [
        "x0",
        "x_gap7",
        "x_tr7",
        "x_tr10",
        "x_pl15",
        "x_gap7_tr7",
        "x_gap7_pl15",
        "x_tr7_pl15",
        "x_gap7_tr7_pl15",
        "x_tr5_pl12",
    ]

    filt_map = dict(filters)
    exit_map = {e[0]: e for e in exits}

    for book_name, top_n, max_pos, cd in books:
        for mkt_name, levels in markets:
            for filt_name in primary_filters:
                tags = filt_map[filt_name]
                for exit_name in primary_exits:
                    _en, gap, trail, pl, plf = exit_map[exit_name]
                    for ex_name, excl in excludes:
                        # skip some low-value combos to keep runtime sane
                        if book_name == "t1m1" and filt_name not in {
                            "ma60",
                            "ma60_bo",
                            "ma60_bo_br50",
                        }:
                            continue
                        if mkt_name == "fn" and filt_name not in {
                            "ma60",
                            "ma60_bo",
                            "ma60_br50",
                            "ma60_bo_br50",
                        }:
                            continue
                        if ex_name != "ex0" and exit_name not in {
                            "x0",
                            "x_tr7",
                            "x_pl15",
                            "x_gap7_tr7",
                        }:
                            continue
                        variants.append(
                            _base_spec(
                                label=(
                                    f"{book_name}__{mkt_name}__{filt_name}__"
                                    f"{exit_name}__{ex_name}"
                                ),
                                top_n=top_n,
                                max_active_positions=max_pos,
                                symbol_cooldown_days=cd,
                                market_levels=levels,
                                required_signal_tags=tags,
                                excluded_signal_tags=excl,
                                pre_exit_calendar_gap_days=gap,
                                prior_high_trailing_stop_pct=trail,
                                partial_profit_activation_pct=pl,
                                partial_profit_fraction=plf,
                            )
                        )

    # Round 2: hybrid non-ma60 controls near E2 dual-pass space
    for top_n, max_pos, levels, tags, tag in (
        (3, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], "bo_br50"),
        (2, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_50"], "bo_br50_t2"),
        (3, 2, ["favorable"], ["breakout_20d", "breadth_advancing_gte_60"], "bo_br60"),
        (3, 2, ["favorable"], ["breadth_advancing_gte_60"], "br60"),
        (2, 2, ["favorable"], ["breadth_ma20_gte_60", "breakout_20d"], "ma60_bo_t2"),
        (3, 2, ["favorable", "neutral"], ["breadth_ma20_gte_60"], "ma60_fn"),
    ):
        for exit_name in ("x0", "x_tr7", "x_pl15", "x_gap7_tr7", "x_gap7_tr7_pl15"):
            _en, gap, trail, pl, plf = exit_map[exit_name]
            variants.append(
                _base_spec(
                    label=f"hyb_{tag}__t{top_n}m{max_pos}__{exit_name}",
                    top_n=top_n,
                    max_active_positions=max_pos,
                    market_levels=levels,
                    required_signal_tags=tags,
                    pre_exit_calendar_gap_days=gap,
                    prior_high_trailing_stop_pct=trail,
                    partial_profit_activation_pct=pl,
                    partial_profit_fraction=plf,
                )
            )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in variants:
        if row["label"] in seen:
            continue
        seen.add(row["label"])
        unique.append(row)
    return unique


def _run_one(trades: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = sweep_qualified_trades(
            deepcopy(trades),
            hold_days=int(spec["hold_days"]),
            top_n=int(spec["top_n"]),
            symbol_cooldown_days=int(spec["symbol_cooldown_days"]),
            max_active_positions=int(spec["max_active_positions"]),
            min_trades=int(spec["min_trades"]),
            max_filter_size=1,
            target_win_rate_pct=52.0,
            target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
            target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            exposure_multipliers=[1.0],
            annual_financing_rate_pct=0.0,
            roundtrip_cost_bps=float(spec["roundtrip_cost_bps"]),
            slippage_bps=float(spec["slippage_bps"]),
            capital_model="slot-daily",
            required_signal_tags=spec.get("required_signal_tags"),
            excluded_signal_tags=spec.get("excluded_signal_tags"),
            market_levels=spec.get("market_levels"),
            force_exposure_multipliers=True,
            pre_exit_calendar_gap_days=int(spec.get("pre_exit_calendar_gap_days") or 0),
            prior_high_trailing_stop_pct=spec.get("prior_high_trailing_stop_pct"),
            prior_high_trailing_activation_pct=0.0,
            partial_profit_activation_pct=spec.get("partial_profit_activation_pct"),
            partial_profit_fraction=float(spec.get("partial_profit_fraction") or 0.0),
            correlation_threshold=None,
            fixed_spec=True,
        )
    except Exception as exc:
        return {
            "label": spec["label"],
            "error": f"{type(exc).__name__}: {exc}",
            "scored": False,
            "stability_ready_for_oos_planning": False,
            "score_rank": -1e9,
        }

    rows = payload.get("top") or []
    if not rows:
        return {
            "label": spec["label"],
            "error": "no_rows",
            "scored": False,
            "stability_ready_for_oos_planning": False,
            "score_rank": -1e9,
            "spec": _compact(spec),
        }
    row = rows[0]
    ret = float(
        row.get("rolling_1y_latest_return_pct_raw", row.get("rolling_1y_latest_return_pct"))
    )
    mdd = float(
        row.get(
            "rolling_1y_latest_max_drawdown_pct_raw",
            row.get("rolling_1y_latest_max_drawdown_pct"),
        )
    )
    latest_dual = (
        ret >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
        and abs(mdd) <= goal.TARGET_MAX_DRAWDOWN_PCT
    )
    rolling = _window_stats(row.get("rolling_1y_windows") or [])
    trades_n = int(row.get("selected_trade_count") or 0)
    days_n = int(row.get("signal_days") or 0)
    flags = _stability_flags(
        latest_dual=latest_dual,
        trades_n=trades_n,
        days_n=days_n,
        rolling=rolling,
    )
    # Rank: stability ready first, then both_pass_rate, then milder worst mdd,
    # then trade count, then latest return.
    worst = rolling.get("worst_mdd_pct")
    worst_term = (100.0 - abs(float(worst))) * 2.0 if worst is not None else 0.0
    score_rank = (
        (1e6 if flags["stability_ready_for_oos_planning"] else 0.0)
        + float(rolling.get("both_pass_rate") or 0.0) * 1000.0
        + worst_term
        + min(trades_n, 200) * 0.1
        + (ret if latest_dual else ret * 0.2)
    )
    return {
        "label": spec["label"],
        "spec": _compact(spec),
        "scored": True,
        "latest": {
            "rolling_1y_latest_return_pct": ret,
            "rolling_1y_latest_max_drawdown_pct": mdd,
            "selected_trade_count": trades_n,
            "signal_days": days_n,
            "trade_win_rate_pct": row.get(
                "trade_win_rate_pct_raw", row.get("trade_win_rate_pct")
            ),
            "trade_profit_factor": row.get(
                "trade_profit_factor_raw", row.get("trade_profit_factor")
            ),
            "dual_pass_50_15": latest_dual,
        },
        "rolling": rolling,
        "flags": flags,
        "stability_ready_for_oos_planning": flags[
            "stability_ready_for_oos_planning"
        ],
        "score_rank": score_rank,
    }


def _compact(spec: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "top_n",
        "max_active_positions",
        "symbol_cooldown_days",
        "market_levels",
        "required_signal_tags",
        "excluded_signal_tags",
        "pre_exit_calendar_gap_days",
        "prior_high_trailing_stop_pct",
        "partial_profit_activation_pct",
        "partial_profit_fraction",
        "capital_model",
        "exposure_multiplier",
        "roundtrip_cost_bps",
        "slippage_bps",
        "hold_days",
    )
    return {k: spec.get(k) for k in keys}


def build_path_a_e4_stability_improve(
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
        raise PathAE4Error(f"missing trades: {trades_path}")

    payload = json.loads(trades_path.read_text(encoding="utf-8"))
    trades = _filter_trades(payload.get("qualified_trades") or [])
    file_sha = hashlib.sha256(trades_path.read_bytes()).hexdigest()

    variants = _e4_variant_grid()
    if max_variants is not None:
        variants = variants[: max(1, int(max_variants))]

    results = [_run_one(trades, v) for v in variants]
    scored = [r for r in results if r.get("scored")]
    ready = [r for r in scored if r.get("stability_ready_for_oos_planning")]
    latest_dual = [
        r for r in scored if (r.get("latest") or {}).get("dual_pass_50_15")
    ]
    ranked = sorted(scored, key=lambda r: float(r.get("score_rank") or -1e9), reverse=True)

    best = ranked[0] if ranked else None
    best_ready = ready[0] if ready else None
    if ready:
        ready_sorted = sorted(
            ready, key=lambda r: float(r.get("score_rank") or -1e9), reverse=True
        )
        best_ready = ready_sorted[0]

    try:
        rel = str(trades_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(trades_path)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_e4_stability_improve",
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "daily_incremental_sync_required": False,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "stability_gates": {
            "min_selected_trades": MIN_SELECTED_TRADES,
            "min_signal_days": MIN_SIGNAL_DAYS,
            "min_rolling_windows": MIN_ROLLING_WINDOWS,
            "min_both_pass_window_rate": MIN_BOTH_PASS_WINDOW_RATE,
            "max_worst_window_mdd_abs": MAX_WORST_WINDOW_MDD_ABS,
        },
        "source_qualified_trades": {
            "path": rel,
            "file_sha256": file_sha,
            "trade_count_train_filtered": len(trades),
        },
        "variant_count": len(variants),
        "scored_count": len(scored),
        "latest_dual_pass_count": len(latest_dual),
        "stability_ready_count": len(ready),
        "any_stability_ready_for_oos_planning": bool(ready),
        "oos_authorized": bool(ready),
        "effective_strategy_found": False,
        "best_overall": best,
        "best_stability_ready": best_ready,
        "top_variants": ranked[:20],
        "stability_ready_variants": ready[:20],
        # Keep full results for audit but may be large
        "all_results": results,
        "ok": True,
        "notes": (
            "ok=true means E4 auto grid completed. "
            "effective_strategy_found remains false until sealed OOS succeeds. "
            "oos_authorized only if at least one stability_ready variant exists."
        ),
        "next_actions": (
            [
                "对 stability_ready 规格冻结哈希并规划 final-OOS 阶段",
                "OOS 前不再改参数",
                "不补 TCB；永不自动交易",
            ]
            if ready
            else [
                "仍无 stability_ready：继续改母规则/换因子/扩有效成交密度（train freeze 内）",
                "不启动 final-OOS",
                "不补 TCB；永不自动交易",
            ]
        ),
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_e4_stability_improve(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAE4Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-e4-stability-improve.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAE4Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    best = report.get("best_overall") or {}
    best_ready = report.get("best_stability_ready") or {}
    latest = (best.get("latest") or {}) if best else {}
    rolling = (best.get("rolling") or {}) if best else {}
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_e4_stability_improve",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "variant_count": report.get("variant_count"),
        "scored_count": report.get("scored_count"),
        "latest_dual_pass_count": report.get("latest_dual_pass_count"),
        "stability_ready_count": report.get("stability_ready_count"),
        "any_stability_ready_for_oos_planning": report.get(
            "any_stability_ready_for_oos_planning"
        ),
        "oos_authorized": report.get("oos_authorized"),
        "effective_strategy_found": False,
        "best_label": best.get("label"),
        "best_return_pct": latest.get("rolling_1y_latest_return_pct"),
        "best_mdd_pct": latest.get("rolling_1y_latest_max_drawdown_pct"),
        "best_both_pass_rate": rolling.get("both_pass_rate"),
        "best_worst_mdd_pct": rolling.get("worst_mdd_pct"),
        "best_stability_ready_label": best_ready.get("label"),
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
    "PathAE4Error",
    "build_path_a_e4_stability_improve",
    "write_path_a_e4_stability_improve",
]
