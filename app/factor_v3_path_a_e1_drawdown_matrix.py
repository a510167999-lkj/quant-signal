"""Path A E1: drawdown-focused strategy variants (no TCB).

Replays sealed ``qualified_hold5_stop5`` trades under the train-window freeze
with a small fixed-spec matrix aimed at MDD <= 15% while keeping rolling 12m
return near/above 50%. Development-only; never auto-trade.
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

STAGE_GOAL_ID = "path-a-e1-drawdown-matrix/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A E1：在 train-locked 密封 qualified trades 上扫降回撤变体"
    "（仓位/退出/市场过滤/1x 暴露/现实成本）；development_only；无 TCB；"
    "不日更；永不自动交易。"
)
REPORT_SCHEMA = "path-a-e1-drawdown-matrix-report/v1"
POINTER_SCHEMA = "path-a-e1-drawdown-matrix-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_e1_drawdown_matrix")


class PathAE1Error(ValueError):
    """Raised when path-A E1 matrix fails closed."""


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
        raise PathAE1Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _variant_grid() -> list[dict[str, Any]]:
    """Curated drawdown-reduction grid (kept small for local runs)."""

    base = {
        "hold_days": 5,
        "exposure_multiplier": 1.0,
        "annual_financing_rate_pct": 0.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "capital_model": "slot-daily",
        "min_trades": 20,
        "fixed_spec": True,
    }
    variants: list[dict[str, Any]] = []

    # Portfolio tightness
    for top_n, max_pos, cooldown in (
        (3, 3, 5),
        (3, 2, 5),
        (2, 2, 5),
        (3, 3, 10),
        (2, 2, 10),
        (3, 4, 5),
    ):
        variants.append(
            {
                **base,
                "label": f"pos_top{top_n}_max{max_pos}_cd{cooldown}",
                "top_n": top_n,
                "max_active_positions": max_pos,
                "symbol_cooldown_days": cooldown,
                "market_levels": None,
                "required_signal_tags": None,
                "excluded_signal_tags": None,
                "pre_exit_calendar_gap_days": 0,
                "prior_high_trailing_stop_pct": None,
                "partial_profit_activation_pct": None,
                "partial_profit_fraction": 0.0,
            }
        )

    # Market regime filters on tight book
    for levels, tag in (
        (["favorable"], "fav"),
        (["favorable", "neutral"], "fav_neu"),
    ):
        variants.append(
            {
                **base,
                "label": f"mkt_{tag}_top3_max2",
                "top_n": 3,
                "max_active_positions": 2,
                "symbol_cooldown_days": 5,
                "market_levels": levels,
                "required_signal_tags": None,
                "excluded_signal_tags": None,
                "pre_exit_calendar_gap_days": 0,
                "prior_high_trailing_stop_pct": None,
                "partial_profit_activation_pct": None,
                "partial_profit_fraction": 0.0,
            }
        )

    # Classic research slice (breakout + breadth) on tight book
    variants.append(
        {
            **base,
            "label": "breakout_breadth_top3_max2",
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ["favorable", "neutral"],
            "required_signal_tags": ["breakout_20d", "breadth_advancing_gte_50"],
            "excluded_signal_tags": None,
            "pre_exit_calendar_gap_days": 0,
            "prior_high_trailing_stop_pct": None,
            "partial_profit_activation_pct": None,
            "partial_profit_fraction": 0.0,
        }
    )

    # More aggressive drawdown cuts (single-name / favorable-only)
    for top_n, max_pos, levels, tag in (
        (1, 1, None, "single"),
        (1, 1, ["favorable"], "single_fav"),
        (2, 1, ["favorable"], "top2_max1_fav"),
        (3, 2, ["favorable"], "top3_max2_fav"),
        (3, 2, ["favorable"], "top3_max2_fav_gap7"),
    ):
        variants.append(
            {
                **base,
                "label": f"tight_{tag}",
                "top_n": top_n,
                "max_active_positions": max_pos,
                "symbol_cooldown_days": 10,
                "market_levels": levels,
                "required_signal_tags": None,
                "excluded_signal_tags": None,
                "pre_exit_calendar_gap_days": 7 if "gap7" in tag else 0,
                "prior_high_trailing_stop_pct": 7.0 if "gap7" in tag else None,
                "partial_profit_activation_pct": 15.0 if "gap7" in tag else None,
                "partial_profit_fraction": 1.0 if "gap7" in tag else 0.0,
            }
        )
    variants.append(
        {
            **base,
            "label": "breakout_breadth_single_fav",
            "top_n": 1,
            "max_active_positions": 1,
            "symbol_cooldown_days": 10,
            "market_levels": ["favorable"],
            "required_signal_tags": ["breakout_20d", "breadth_advancing_gte_50"],
            "excluded_signal_tags": None,
            "pre_exit_calendar_gap_days": 7,
            "prior_high_trailing_stop_pct": 7.0,
            "partial_profit_activation_pct": 15.0,
            "partial_profit_fraction": 1.0,
        }
    )

    # Holiday pre-exit + trailing / profit lock on control-like book
    for gap in (0, 7):
        for trail in (None, 7.0, 10.0):
            for profit_lock in (None, 18.0):
                label = f"ctrl_gap{gap}_trail{trail or 0}_pl{profit_lock or 0}"
                variants.append(
                    {
                        **base,
                        "label": label,
                        "top_n": 3,
                        "max_active_positions": 3,
                        "symbol_cooldown_days": 5,
                        "market_levels": ["favorable", "neutral"],
                        "required_signal_tags": None,
                        "excluded_signal_tags": None,
                        "pre_exit_calendar_gap_days": gap,
                        "prior_high_trailing_stop_pct": trail,
                        "prior_high_trailing_activation_pct": 0.0 if trail else 0.0,
                        "partial_profit_activation_pct": profit_lock,
                        "partial_profit_fraction": 1.0 if profit_lock else 0.0,
                    }
                )

    # Deduplicate by label
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
        raise PathAE1Error("no trades remain after train-window filter")
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
        "portfolio_compounded_return_pct": metrics.get(
            "portfolio_compounded_return_pct_raw"
        )
        or metrics.get("portfolio_compounded_return_pct"),
        "portfolio_max_drawdown_pct": metrics.get("portfolio_max_drawdown_pct_raw")
        or metrics.get("portfolio_max_drawdown_pct"),
        "rolling_1y_latest_full_window": metrics.get("rolling_1y_latest_full_window"),
        "pass_return_50": pass_ret,
        "pass_drawdown_15": pass_dd,
        "pass_both_50_15": pass_ret and pass_dd,
    }


def _run_variant(
    trades: list[dict[str, Any]],
    variant: dict[str, Any],
) -> dict[str, Any]:
    # Deepcopy so trailing/profit-lock mutations never leak across variants.
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
    except Exception as exc:  # keep matrix running
        return {
            "label": variant["label"],
            "variant": {
                k: v
                for k, v in variant.items()
                if k
                not in {
                    # keep compact
                }
            },
            "error": f"{type(exc).__name__}: {exc}",
            "scored": False,
            "pass_both_50_15": False,
        }

    rows = payload.get("top") or payload.get("rows") or []
    if not rows:
        return {
            "label": variant["label"],
            "variant": variant,
            "error": "no_rows",
            "scored": False,
            "pass_both_50_15": False,
            "sweep_returned_count": payload.get("returned_count"),
            "sweep_diagnostics_keys": sorted((payload.get("diagnostics") or {}).keys()),
        }
    # Prefer exposure 1.0 row
    row = rows[0]
    for candidate in rows:
        if float(candidate.get("exposure_multiplier") or 0) == 1.0:
            row = candidate
            break
    score = _score_metrics(row)
    return {
        "label": variant["label"],
        "variant": {
            "top_n": variant["top_n"],
            "max_active_positions": variant["max_active_positions"],
            "symbol_cooldown_days": variant["symbol_cooldown_days"],
            "capital_model": variant["capital_model"],
            "exposure_multiplier": variant["exposure_multiplier"],
            "roundtrip_cost_bps": variant["roundtrip_cost_bps"],
            "slippage_bps": variant["slippage_bps"],
            "market_levels": variant.get("market_levels"),
            "required_signal_tags": variant.get("required_signal_tags"),
            "pre_exit_calendar_gap_days": variant.get("pre_exit_calendar_gap_days"),
            "prior_high_trailing_stop_pct": variant.get("prior_high_trailing_stop_pct"),
            "partial_profit_activation_pct": variant.get(
                "partial_profit_activation_pct"
            ),
            "partial_profit_fraction": variant.get("partial_profit_fraction"),
        },
        "spec_label": row.get("label"),
        **score,
        "target_all_pass": row.get("target_all_pass"),
    }


def build_path_a_e1_drawdown_matrix(
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
        raise PathAE1Error(f"qualified trades missing: {trades_path}")

    payload = json.loads(trades_path.read_text(encoding="utf-8"))
    trades = payload.get("qualified_trades")
    if type(trades) is not list or not trades:
        raise PathAE1Error("qualified_trades empty")
    trades = _filter_trades_to_train_window(trades)
    file_sha = hashlib.sha256(trades_path.read_bytes()).hexdigest()

    variants = _variant_grid()
    if max_variants is not None:
        variants = variants[: max(1, int(max_variants))]

    results: list[dict[str, Any]] = []
    for variant in variants:
        results.append(_run_variant(trades, variant))

    scored = [r for r in results if r.get("scored") is True]
    both = [r for r in scored if r.get("pass_both_50_15") is True]
    pass_dd = [r for r in scored if r.get("pass_drawdown_15") is True]
    pass_ret = [r for r in scored if r.get("pass_return_50") is True]

    def sort_key(row: dict[str, Any]) -> tuple:
        return (
            1 if row.get("pass_both_50_15") else 0,
            1 if row.get("pass_drawdown_15") else 0,
            1 if row.get("pass_return_50") else 0,
            -abs(float(row.get("rolling_1y_latest_max_drawdown_pct") or 999)),
            float(row.get("rolling_1y_latest_return_pct") or -999),
        )

    ranked = sorted(scored, key=sort_key, reverse=True)
    best = ranked[0] if ranked else None

    try:
        rel = str(trades_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(trades_path)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_e1_drawdown_matrix",
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
        "source_qualified_trades": {
            "path": rel,
            "file_sha256": file_sha,
            "trade_count_raw": len(payload.get("qualified_trades") or []),
            "trade_count_train_filtered": len(trades),
            "hold_days_source": (payload.get("summary") or {}).get("hold_days"),
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
        "top_variants": ranked[:12],
        "all_results": results,
        "ok": True,
        "notes": (
            "ok=true means E1 matrix completed under path A. "
            "effective_strategy_found stays false until OOS-validated. "
            "Costs: 25bps roundtrip + 10bps slippage; exposure 1.0; slot-daily."
        ),
        "next_actions": [
            "若仍无双过：继续围绕 best_variant 细化（更严过滤/更紧退出）",
            "若有双过：做 rolling 稳定性，再规划 embargo/final-OOS",
            "不补 TCB；永不自动交易",
        ],
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_e1_drawdown_matrix(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAE1Error("refuse to write non-ok report")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-e1-drawdown-matrix.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAE1Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)
    best = report.get("best_variant") or {}
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_e1_drawdown_matrix",
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
        "best_label": best.get("label"),
        "best_return_pct": best.get("rolling_1y_latest_return_pct"),
        "best_mdd_pct": best.get("rolling_1y_latest_max_drawdown_pct"),
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
    "PathAE1Error",
    "build_path_a_e1_drawdown_matrix",
    "write_path_a_e1_drawdown_matrix",
]
