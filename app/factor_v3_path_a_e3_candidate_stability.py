"""Path A E3: freeze dual-pass candidates and audit rolling stability.

Does **not** claim an effective strategy. Latest-12m dual-pass from E2 must also
survive rolling-window stress and sample-thickness checks before OOS planning.
No TCB; no auto-trade; train-locked trades only.
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

STAGE_GOAL_ID = "path-a-e3-candidate-stability/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A E3：冻结 E2 development 双过候选规格，审查 rolling 窗口通过率与样本厚度；"
    "development_only；无 TCB；不日更；永不自动交易；不因 latest 双过宣称有效。"
)
REPORT_SCHEMA = "path-a-e3-candidate-stability-report/v1"
POINTER_SCHEMA = "path-a-e3-candidate-stability-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_e3_candidate_stability")

# Minimum bar before considering a candidate "stability-ready for OOS planning".
# These are path-A research gates, not formal authority.
MIN_SELECTED_TRADES = 40
MIN_SIGNAL_DAYS = 30
MIN_ROLLING_WINDOWS = 20
MIN_BOTH_PASS_WINDOW_RATE = 0.25  # at least 25% of rolling 12m windows dual-pass
MAX_WORST_WINDOW_MDD_ABS = 25.0  # worst window drawdown cannot be catastrophic
MIN_WORST_WINDOW_RETURN = 0.0  # worst full window return should not be deeply negative if possible (soft)


class PathAE3Error(ValueError):
    """Raised when path-A E3 stability audit fails closed."""


# Frozen candidate book from E2 dual-pass set (unique economic specs).
FROZEN_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "robust_fav_ma60_t3m2",
        "role": "primary_preferred",
        "rationale": "E2 dual-pass with thicker book (60 trades) vs single-name cluster",
        "top_n": 3,
        "max_active_positions": 2,
        "symbol_cooldown_days": 5,
        "market_levels": ["favorable"],
        "required_signal_tags": ["breadth_ma20_gte_60"],
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    },
    {
        "candidate_id": "thin_bo_br_single_fav",
        "role": "high_return_thin",
        "rationale": "E2 top dual-pass return; only ~30 trades — stability risk",
        "top_n": 1,
        "max_active_positions": 1,
        "symbol_cooldown_days": 10,
        "market_levels": ["favorable"],
        "required_signal_tags": ["breakout_20d", "breadth_advancing_gte_50"],
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    },
    {
        "candidate_id": "thin_bo_br_single_fav_pl15",
        "role": "high_return_thin_exit",
        "rationale": "Same thin book with 15% full profit-lock",
        "top_n": 1,
        "max_active_positions": 1,
        "symbol_cooldown_days": 10,
        "market_levels": ["favorable"],
        "required_signal_tags": ["breakout_20d", "breadth_advancing_gte_50"],
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": 15.0,
        "partial_profit_fraction": 1.0,
    },
    {
        "candidate_id": "thin_bo_br_single_fav_tr7",
        "role": "high_return_thin_exit",
        "rationale": "Same thin book with 7% prior-high trailing",
        "top_n": 1,
        "max_active_positions": 1,
        "symbol_cooldown_days": 10,
        "market_levels": ["favorable"],
        "required_signal_tags": ["breakout_20d", "breadth_advancing_gte_50"],
        "excluded_signal_tags": None,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": 7.0,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    },
)


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
        raise PathAE3Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


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
        raise PathAE3Error("no trades remain after train-window filter")
    return out


def _window_stats(windows: list[dict[str, Any]]) -> dict[str, Any]:
    if not windows:
        return {
            "window_count": 0,
            "both_pass_count": 0,
            "both_pass_rate": 0.0,
            "return_pass_count": 0,
            "drawdown_pass_count": 0,
            "min_return_pct": None,
            "max_return_pct": None,
            "median_return_pct": None,
            "worst_mdd_pct": None,
            "best_mdd_pct": None,
            "median_mdd_pct": None,
            "first_window": None,
            "last_window": None,
            "worst_return_window": None,
            "worst_mdd_window": None,
        }

    rets: list[float] = []
    mdds: list[float] = []
    both = 0
    ret_pass = 0
    dd_pass = 0
    worst_ret_w = None
    worst_mdd_w = None
    for w in windows:
        ret = w.get("return_pct_raw")
        if ret is None:
            ret = w.get("return_pct")
        mdd = w.get("max_drawdown_pct_raw")
        if mdd is None:
            mdd = w.get("max_drawdown_pct")
        if ret is None or mdd is None:
            continue
        ret_f = float(ret)
        mdd_f = float(mdd)
        rets.append(ret_f)
        mdds.append(mdd_f)
        rp = ret_f >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
        dp = abs(mdd_f) <= goal.TARGET_MAX_DRAWDOWN_PCT
        if rp:
            ret_pass += 1
        if dp:
            dd_pass += 1
        if rp and dp:
            both += 1
        if worst_ret_w is None or ret_f < float(
            worst_ret_w.get("return_pct_raw", worst_ret_w.get("return_pct"))
        ):
            worst_ret_w = w
        if worst_mdd_w is None or mdd_f < float(
            worst_mdd_w.get("max_drawdown_pct_raw", worst_mdd_w.get("max_drawdown_pct"))
        ):
            worst_mdd_w = w

    n = len(rets)
    rets_sorted = sorted(rets)
    mdds_sorted = sorted(mdds)
    mid = n // 2
    median_ret = (
        rets_sorted[mid]
        if n % 2 == 1
        else (rets_sorted[mid - 1] + rets_sorted[mid]) / 2.0
    ) if n else None
    median_mdd = (
        mdds_sorted[mid]
        if n % 2 == 1
        else (mdds_sorted[mid - 1] + mdds_sorted[mid]) / 2.0
    ) if n else None

    def _brief(w: dict[str, Any] | None) -> dict[str, Any] | None:
        if not w:
            return None
        return {
            "start_date": w.get("start_date"),
            "end_date": w.get("end_date"),
            "return_pct": w.get("return_pct_raw", w.get("return_pct")),
            "max_drawdown_pct": w.get("max_drawdown_pct_raw", w.get("max_drawdown_pct")),
            "selected_trade_count": w.get("selected_trade_count"),
            "win_rate_pct": w.get("win_rate_pct_raw", w.get("win_rate_pct")),
        }

    return {
        "window_count": n,
        "both_pass_count": both,
        "both_pass_rate": (both / n) if n else 0.0,
        "return_pass_count": ret_pass,
        "return_pass_rate": (ret_pass / n) if n else 0.0,
        "drawdown_pass_count": dd_pass,
        "drawdown_pass_rate": (dd_pass / n) if n else 0.0,
        "min_return_pct": min(rets) if rets else None,
        "max_return_pct": max(rets) if rets else None,
        "median_return_pct": median_ret,
        "worst_mdd_pct": min(mdds) if mdds else None,
        "best_mdd_pct": max(mdds) if mdds else None,
        "median_mdd_pct": median_mdd,
        "first_window": _brief(windows[0]),
        "last_window": _brief(windows[-1]),
        "worst_return_window": _brief(worst_ret_w),
        "worst_mdd_window": _brief(worst_mdd_w),
    }


def _evaluate_candidate(
    trades: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    trade_copy = deepcopy(trades)
    payload = sweep_qualified_trades(
        trade_copy,
        hold_days=5,
        top_n=int(candidate["top_n"]),
        symbol_cooldown_days=int(candidate["symbol_cooldown_days"]),
        max_active_positions=int(candidate["max_active_positions"]),
        min_trades=15,
        max_filter_size=1,
        target_win_rate_pct=52.0,
        target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
        target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        exposure_multipliers=[1.0],
        annual_financing_rate_pct=0.0,
        roundtrip_cost_bps=25.0,
        slippage_bps=10.0,
        capital_model="slot-daily",
        required_signal_tags=candidate.get("required_signal_tags"),
        excluded_signal_tags=candidate.get("excluded_signal_tags"),
        market_levels=candidate.get("market_levels"),
        force_exposure_multipliers=True,
        pre_exit_calendar_gap_days=int(candidate.get("pre_exit_calendar_gap_days") or 0),
        prior_high_trailing_stop_pct=candidate.get("prior_high_trailing_stop_pct"),
        prior_high_trailing_activation_pct=0.0,
        partial_profit_activation_pct=candidate.get("partial_profit_activation_pct"),
        partial_profit_fraction=float(candidate.get("partial_profit_fraction") or 0.0),
        correlation_threshold=None,
        fixed_spec=True,
    )
    rows = payload.get("top") or []
    if not rows:
        return {
            "candidate_id": candidate["candidate_id"],
            "role": candidate["role"],
            "rationale": candidate["rationale"],
            "spec": _spec_view(candidate),
            "error": "no_rows",
            "latest_dual_pass": False,
            "stability_ready_for_oos_planning": False,
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
    windows = row.get("rolling_1y_windows") or []
    rolling = _window_stats(windows)
    trades_n = int(row.get("selected_trade_count") or 0)
    days_n = int(row.get("signal_days") or 0)

    thickness_ok = trades_n >= MIN_SELECTED_TRADES and days_n >= MIN_SIGNAL_DAYS
    rolling_count_ok = rolling["window_count"] >= MIN_ROLLING_WINDOWS
    both_rate_ok = rolling["both_pass_rate"] >= MIN_BOTH_PASS_WINDOW_RATE
    worst_mdd = rolling["worst_mdd_pct"]
    worst_mdd_ok = (
        worst_mdd is not None and abs(float(worst_mdd)) <= MAX_WORST_WINDOW_MDD_ABS
    )
    stability_ready = (
        latest_dual
        and thickness_ok
        and rolling_count_ok
        and both_rate_ok
        and worst_mdd_ok
    )

    gates = {
        "latest_dual_pass_50_15": latest_dual,
        "min_selected_trades": {
            "ok": trades_n >= MIN_SELECTED_TRADES,
            "value": trades_n,
            "threshold": MIN_SELECTED_TRADES,
        },
        "min_signal_days": {
            "ok": days_n >= MIN_SIGNAL_DAYS,
            "value": days_n,
            "threshold": MIN_SIGNAL_DAYS,
        },
        "min_rolling_windows": {
            "ok": rolling_count_ok,
            "value": rolling["window_count"],
            "threshold": MIN_ROLLING_WINDOWS,
        },
        "min_both_pass_window_rate": {
            "ok": both_rate_ok,
            "value": rolling["both_pass_rate"],
            "threshold": MIN_BOTH_PASS_WINDOW_RATE,
        },
        "max_worst_window_mdd_abs": {
            "ok": worst_mdd_ok,
            "value": worst_mdd,
            "threshold": MAX_WORST_WINDOW_MDD_ABS,
        },
    }

    return {
        "candidate_id": candidate["candidate_id"],
        "role": candidate["role"],
        "rationale": candidate["rationale"],
        "spec": _spec_view(candidate),
        "latest": {
            "rolling_1y_latest_return_pct": ret,
            "rolling_1y_latest_max_drawdown_pct": mdd,
            "calmar_latest_12m": row.get("calmar_latest_12m_raw", row.get("calmar_latest_12m")),
            "selected_trade_count": trades_n,
            "signal_days": days_n,
            "trade_win_rate_pct": row.get(
                "trade_win_rate_pct_raw", row.get("trade_win_rate_pct")
            ),
            "trade_profit_factor": row.get(
                "trade_profit_factor_raw", row.get("trade_profit_factor")
            ),
            "rolling_1y_latest_full_window": row.get("rolling_1y_latest_full_window"),
            "evaluation_start": row.get("rolling_1y_evaluation_start_date"),
            "evaluation_end": row.get("rolling_1y_evaluation_end_date"),
            "dual_pass_50_15": latest_dual,
        },
        "rolling": rolling,
        "gates": gates,
        "stability_ready_for_oos_planning": stability_ready,
        "oos_ready": False,  # never true in E3; OOS not run here
        "effective_strategy": False,
    }


def _spec_view(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "top_n": candidate["top_n"],
        "max_active_positions": candidate["max_active_positions"],
        "symbol_cooldown_days": candidate["symbol_cooldown_days"],
        "market_levels": candidate.get("market_levels"),
        "required_signal_tags": candidate.get("required_signal_tags"),
        "excluded_signal_tags": candidate.get("excluded_signal_tags"),
        "capital_model": "slot-daily",
        "exposure_multiplier": 1.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "hold_days": 5,
        "pre_exit_calendar_gap_days": candidate.get("pre_exit_calendar_gap_days"),
        "prior_high_trailing_stop_pct": candidate.get("prior_high_trailing_stop_pct"),
        "partial_profit_activation_pct": candidate.get("partial_profit_activation_pct"),
        "partial_profit_fraction": candidate.get("partial_profit_fraction"),
    }


def build_path_a_e3_candidate_stability(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()
    trades_path = (root / (qualified_trades_path or DEFAULT_QUALIFIED_TRADES)).resolve()
    if not trades_path.is_file():
        raise PathAE3Error(f"qualified trades missing: {trades_path}")

    payload = json.loads(trades_path.read_text(encoding="utf-8"))
    trades = payload.get("qualified_trades")
    if type(trades) is not list or not trades:
        raise PathAE3Error("qualified_trades empty")
    trades = _filter_trades_to_train_window(trades)
    file_sha = hashlib.sha256(trades_path.read_bytes()).hexdigest()

    evaluations = [_evaluate_candidate(trades, c) for c in FROZEN_CANDIDATES]
    ready = [e for e in evaluations if e.get("stability_ready_for_oos_planning")]
    latest_dual = [e for e in evaluations if e.get("latest", {}).get("dual_pass_50_15")]

    # Rank by: stability ready, then both_pass_rate, then trade count, then return
    def rank_key(e: dict[str, Any]) -> tuple:
        latest = e.get("latest") or {}
        rolling = e.get("rolling") or {}
        return (
            1 if e.get("stability_ready_for_oos_planning") else 0,
            float(rolling.get("both_pass_rate") or 0.0),
            int(latest.get("selected_trade_count") or 0),
            float(latest.get("rolling_1y_latest_return_pct") or -999),
            -abs(float(latest.get("rolling_1y_latest_max_drawdown_pct") or 999)),
        )

    ranked = sorted(evaluations, key=rank_key, reverse=True)
    primary = next(
        (e for e in ranked if e.get("role") == "primary_preferred"),
        ranked[0] if ranked else None,
    )

    try:
        rel = str(trades_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(trades_path)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_e3_candidate_stability",
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
        "targets": {
            "rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            "max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        },
        "candidates": evaluations,
        "ranked_candidates": [
            {
                "candidate_id": e.get("candidate_id"),
                "role": e.get("role"),
                "latest_dual_pass": (e.get("latest") or {}).get("dual_pass_50_15"),
                "both_pass_rate": (e.get("rolling") or {}).get("both_pass_rate"),
                "selected_trade_count": (e.get("latest") or {}).get(
                    "selected_trade_count"
                ),
                "stability_ready_for_oos_planning": e.get(
                    "stability_ready_for_oos_planning"
                ),
            }
            for e in ranked
        ],
        "primary_candidate_id": (primary or {}).get("candidate_id"),
        "latest_dual_pass_count": len(latest_dual),
        "stability_ready_count": len(ready),
        "any_stability_ready_for_oos_planning": bool(ready),
        "effective_strategy_found": False,
        "oos_authorized": False,
        "ok": True,
        "notes": (
            "ok=true means E3 stability audit completed. "
            "effective_strategy_found remains false. "
            "OOS is not authorized unless stability_ready_for_oos_planning is true "
            "for at least one candidate."
        ),
        "next_actions": (
            [
                "无候选通过稳定性门槛：回到 path A 矩阵，优先提高 rolling both-pass 率与样本厚度",
                "不启动 final-OOS",
                "不补 TCB；永不自动交易",
            ]
            if not ready
            else [
                "对 stability_ready 候选规划 final-OOS / 密封区评估（另开阶段）",
                "OOS 前冻结规格哈希与数据哈希",
                "不补 TCB；永不自动交易",
            ]
        ),
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_e3_candidate_stability(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAE3Error("refuse to write non-ok report")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-e3-candidate-stability.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAE3Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_e3_candidate_stability",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "primary_candidate_id": report.get("primary_candidate_id"),
        "latest_dual_pass_count": report.get("latest_dual_pass_count"),
        "stability_ready_count": report.get("stability_ready_count"),
        "any_stability_ready_for_oos_planning": report.get(
            "any_stability_ready_for_oos_planning"
        ),
        "effective_strategy_found": False,
        "oos_authorized": False,
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
    "FROZEN_CANDIDATES",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAE3Error",
    "build_path_a_e3_candidate_stability",
    "write_path_a_e3_candidate_stability",
]
