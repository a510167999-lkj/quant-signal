"""Path A: frozen-candidate fixed-spec replay on ~2y train window.

No parameter search, no refit, no auto-trade. Uses sealed train-window trades
only (signal_date within train freeze). Documents bias boundaries explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal
from app.research_sweep import sweep_qualified_trades

STAGE_GOAL_ID = "path-a-frozen-train-window-replay/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A：冻结候选在约 2 年 train 窗上固定规格完整重放；"
    "禁止改参/重拟合；禁止使用 train 截止后信号；输出滚动 12 月明细与偏差边界；"
    "永不自动交易；不宣称 formal final-OOS / 有效策略保证。"
)
REPORT_SCHEMA = "path-a-frozen-train-window-replay-report/v1"
POINTER_SCHEMA = "path-a-frozen-train-window-replay-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_frozen_train_window_replay")


class PathAFrozenTrainReplayError(ValueError):
    """Raised when frozen train-window replay fails closed."""


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAFrozenTrainReplayError(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _filter_train_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in trades:
        d = str(trade.get("signal_date") or "")[:10]
        if not d:
            continue
        if d >= freeze.TRAIN_EXCLUSIVE_END_DATE:
            continue
        if d > freeze.TRAIN_INCLUSIVE_SESSION_END:
            continue
        out.append(trade)
    if not out:
        raise PathAFrozenTrainReplayError("no trades after train-window filter")
    return out


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pass_50_15(ret: float | None, mdd: float | None) -> bool:
    if ret is None or mdd is None:
        return False
    return ret >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT and abs(mdd) <= goal.TARGET_MAX_DRAWDOWN_PCT


def _window_row(w: dict[str, Any]) -> dict[str, Any]:
    ret = _f(w.get("return_pct_raw", w.get("return_pct")))
    mdd = _f(w.get("max_drawdown_pct_raw", w.get("max_drawdown_pct")))
    return {
        "start_date": w.get("start_date"),
        "end_date": w.get("end_date"),
        "return_pct": ret,
        "max_drawdown_pct": mdd,
        "selected_trade_count": w.get("selected_trade_count", w.get("trade_count")),
        "signal_days": w.get("signal_days"),
        "win_rate_pct": _f(w.get("win_rate_pct_raw", w.get("win_rate_pct"))),
        "profit_factor": _f(w.get("profit_factor_raw", w.get("profit_factor"))),
        "calmar": _f(w.get("calmar_raw", w.get("calmar"))),
        "pass_return_50": ret is not None
        and ret >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "pass_drawdown_15": mdd is not None and abs(mdd) <= goal.TARGET_MAX_DRAWDOWN_PCT,
        "pass_both_50_15": _pass_50_15(ret, mdd),
    }


def _rolling_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    if not windows:
        return {
            "window_count": 0,
            "both_pass_count": 0,
            "both_pass_rate": 0.0,
            "return_pass_count": 0,
            "return_pass_rate": 0.0,
            "drawdown_pass_count": 0,
            "drawdown_pass_rate": 0.0,
            "min_return_pct": None,
            "max_return_pct": None,
            "worst_mdd_pct": None,
            "best_mdd_pct": None,
            "worst_return_window": None,
            "worst_mdd_window": None,
            "latest_window": None,
        }
    rets = [float(w["return_pct"]) for w in windows if w.get("return_pct") is not None]
    mdds = [
        float(w["max_drawdown_pct"])
        for w in windows
        if w.get("max_drawdown_pct") is not None
    ]
    both = sum(1 for w in windows if w.get("pass_both_50_15"))
    ret_p = sum(1 for w in windows if w.get("pass_return_50"))
    dd_p = sum(1 for w in windows if w.get("pass_drawdown_15"))
    n = len(windows)
    worst_ret = min(windows, key=lambda w: float(w.get("return_pct") or 0.0))
    worst_mdd = min(
        windows, key=lambda w: float(w.get("max_drawdown_pct") or 0.0)
    )
    return {
        "window_count": n,
        "both_pass_count": both,
        "both_pass_rate": both / n if n else 0.0,
        "return_pass_count": ret_p,
        "return_pass_rate": ret_p / n if n else 0.0,
        "drawdown_pass_count": dd_p,
        "drawdown_pass_rate": dd_p / n if n else 0.0,
        "min_return_pct": min(rets) if rets else None,
        "max_return_pct": max(rets) if rets else None,
        "worst_mdd_pct": min(mdds) if mdds else None,
        "best_mdd_pct": max(mdds) if mdds else None,
        "worst_return_window": {
            "start_date": worst_ret.get("start_date"),
            "end_date": worst_ret.get("end_date"),
            "return_pct": worst_ret.get("return_pct"),
            "max_drawdown_pct": worst_ret.get("max_drawdown_pct"),
        },
        "worst_mdd_window": {
            "start_date": worst_mdd.get("start_date"),
            "end_date": worst_mdd.get("end_date"),
            "return_pct": worst_mdd.get("return_pct"),
            "max_drawdown_pct": worst_mdd.get("max_drawdown_pct"),
        },
        "latest_window": {
            "start_date": windows[-1].get("start_date"),
            "end_date": windows[-1].get("end_date"),
            "return_pct": windows[-1].get("return_pct"),
            "max_drawdown_pct": windows[-1].get("max_drawdown_pct"),
            "pass_both_50_15": windows[-1].get("pass_both_50_15"),
        },
    }


def _bias_boundary_document(
    *,
    qualified_summary: dict[str, Any] | None,
    candidate_id: str,
) -> dict[str, Any]:
    return {
        "guarantees": {
            "meets_50_15_on_any_future_period": False,
            "meets_50_15_on_all_historical_rolling_windows": False,
            "formal_final_oos_passed": False,
            "effective_strategy_certified": False,
            "no_bias_whatsoever": False,
        },
        "what_this_replay_is": [
            "Fixed frozen candidate spec; no parameter search / refit",
            "Trades restricted to train freeze signal_date window only",
            "Costs/slippage fixed at candidate spec (25/10 bps default on card)",
            "Rolling 12m windows reported for transparency, not as a guarantee",
        ],
        "what_this_replay_is_not": [
            "Not formal final-OOS under frozen-v1",
            "Not a promise of future 50%/15%",
            "Not free of selection bias (candidate was chosen on development data)",
            "Not a pure PIT universe proof",
        ],
        "lookahead_and_bias_risks": [
            {
                "code": "parameter_selection_on_same_development_window",
                "severity": "high",
                "detail": (
                    f"Candidate {candidate_id} was selected via E0–E4 on the same "
                    "development/train-adjacent history; this replay is in-sample "
                    "relative to that selection process."
                ),
            },
            {
                "code": "qualified_trades_seed_survivorship_caveat",
                "severity": "medium",
                "detail": (
                    str(
                        (qualified_summary or {}).get("research_caveat")
                        or "Historical prefilter may use current-snapshot symbol seed."
                    )
                ),
            },
            {
                "code": "contaminated_diagnostic_partition",
                "severity": "medium",
                "detail": (
                    "frozen-v1 labels 2024-01-01..2026-07-03 as contaminated_diagnostic "
                    "(collect/publish/diagnose only); not a clean sealed final proof set."
                ),
            },
            {
                "code": "entry_model_next_open_heuristic",
                "severity": "low",
                "detail": (
                    "Research path uses next-open style execution heuristics on cached "
                    "bars; not full audited artifact-native fill proof for every trade."
                ),
            },
            {
                "code": "no_future_signal_date_in_train_filter",
                "severity": "controlled",
                "detail": (
                    f"signal_date must be <= {freeze.TRAIN_INCLUSIVE_SESSION_END} and "
                    f"< {freeze.TRAIN_EXCLUSIVE_END_DATE}; post-train rows excluded."
                ),
            },
        ],
        "user_targets": {
            "rolling_12m_net_return_pct_min": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            "max_drawdown_pct_abs_max": goal.TARGET_MAX_DRAWDOWN_PCT,
            "automatic_trading_allowed": False,
        },
    }


def build_path_a_frozen_train_window_replay(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
    qualified_trades_path: Path | None = None,
    frozen_candidate_path: Path | None = None,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()

    card_path = (frozen_candidate_path or (root / DEFAULT_FROZEN_CANDIDATE)).resolve()
    card = _load_json(card_path)
    if card is None:
        raise PathAFrozenTrainReplayError(f"frozen candidate missing: {card_path}")
    expected_id = f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]
    if card.get("candidate_id") != expected_id:
        raise PathAFrozenTrainReplayError(
            f"candidate_id mismatch: {card.get('candidate_id')!r} != {expected_id!r}"
        )
    spec = card.get("spec") or {}
    if _sha(spec) != card.get("candidate_spec_sha256") and _sha(
        f0.FROZEN_PRIMARY_CANDIDATE["spec"]
    ) != card.get("candidate_spec_sha256"):
        # Prefer card self-consistency; also accept identity with F0 primary.
        if _sha(spec) != _sha(f0.FROZEN_PRIMARY_CANDIDATE["spec"]):
            raise PathAFrozenTrainReplayError("frozen candidate spec hash mismatch")

    qt_path = (qualified_trades_path or (root / DEFAULT_QUALIFIED_TRADES)).resolve()
    qt_payload = _load_json(qt_path)
    if qt_payload is None:
        raise PathAFrozenTrainReplayError(f"qualified trades missing: {qt_path}")
    raw_trades = list(qt_payload.get("qualified_trades") or [])
    train_trades = _filter_train_trades(raw_trades)
    dates = sorted(str(t.get("signal_date") or "")[:10] for t in train_trades)

    payload = sweep_qualified_trades(
        deepcopy(train_trades),
        hold_days=int(spec.get("hold_days") or 5),
        top_n=int(spec.get("top_n") or 3),
        symbol_cooldown_days=int(spec.get("symbol_cooldown_days") or 5),
        max_active_positions=int(spec.get("max_active_positions") or 2),
        min_trades=1,
        max_filter_size=1,
        target_win_rate_pct=52.0,
        target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
        target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        exposure_multipliers=[float(spec.get("exposure_multiplier") or 1.0)],
        annual_financing_rate_pct=float(spec.get("annual_financing_rate_pct") or 0.0),
        roundtrip_cost_bps=float(spec.get("roundtrip_cost_bps") or 25.0),
        slippage_bps=float(spec.get("slippage_bps") or 10.0),
        capital_model=str(spec.get("capital_model") or "slot-daily"),
        required_signal_tags=list(spec.get("required_signal_tags") or []),
        excluded_signal_tags=list(spec.get("excluded_signal_tags") or []),
        market_levels=list(spec.get("market_levels") or []),
        force_exposure_multipliers=True,
        pre_exit_calendar_gap_days=int(spec.get("pre_exit_calendar_gap_days") or 0),
        prior_high_trailing_stop_pct=spec.get("prior_high_trailing_stop_pct"),
        prior_high_trailing_activation_pct=0.0,
        partial_profit_activation_pct=spec.get("partial_profit_activation_pct"),
        partial_profit_fraction=float(spec.get("partial_profit_fraction") or 0.0),
        correlation_threshold=None,
        fixed_spec=True,
    )
    rows = payload.get("top") or []
    if not rows:
        raise PathAFrozenTrainReplayError("fixed_spec produced no rows on train window")
    row = rows[0]

    port_ret = _f(
        row.get("portfolio_compounded_return_pct_raw", row.get("portfolio_compounded_return_pct"))
    )
    port_mdd = _f(
        row.get("portfolio_max_drawdown_pct_raw", row.get("portfolio_max_drawdown_pct"))
    )
    latest_ret = _f(
        row.get("rolling_1y_latest_return_pct_raw", row.get("rolling_1y_latest_return_pct"))
    )
    latest_mdd = _f(
        row.get(
            "rolling_1y_latest_max_drawdown_pct_raw",
            row.get("rolling_1y_latest_max_drawdown_pct"),
        )
    )
    windows = [_window_row(w) for w in (row.get("rolling_1y_windows") or [])]
    rolling = _rolling_summary(windows)

    full_path_pass = _pass_50_15(port_ret, port_mdd)
    # Full-path return is total compounded, not rolling-12m; only MDD has same unit.
    # For "2y full path" we report both: rolling latest pass and full-path MDD vs 15%.
    full_path_mdd_pass = port_mdd is not None and abs(port_mdd) <= goal.TARGET_MAX_DRAWDOWN_PCT
    latest_roll_pass = _pass_50_15(latest_ret, latest_mdd)

    verdict = {
        "latest_rolling_12m_pass_50_15": latest_roll_pass,
        "full_path_max_drawdown_pass_15": full_path_mdd_pass,
        "all_rolling_windows_pass_50_15": rolling.get("both_pass_rate") == 1.0
        and int(rolling.get("window_count") or 0) > 0,
        "meets_user_requirement_as_guarantee": False,
        "interpretation": (
            "Latest rolling-12m may pass while full-path MDD or many rolling windows fail. "
            "This is historical fixed-spec evidence only — not a guarantee."
        ),
    }

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_frozen_train_window_replay",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "refit": False,
        "parameter_search": False,
        "fixed_spec": True,
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "frozen_candidate": {
            "candidate_id": card.get("candidate_id"),
            "candidate_spec_sha256": card.get("candidate_spec_sha256"),
            "spec": spec,
            "path": str(card_path),
            "f0_report_sha256": card.get("f0_report_sha256"),
        },
        "input_qualified_trades": {
            "path": str(qt_path),
            "file_sha256": _file_sha256(qt_path),
            "raw_trade_count": len(raw_trades),
            "train_filtered_trade_count": len(train_trades),
            "first_signal_date": dates[0] if dates else None,
            "last_signal_date": dates[-1] if dates else None,
            "approx_span_years": (
                round(
                    (
                        date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])
                    ).days
                    / 365.25,
                    2,
                )
                if dates
                else None
            ),
            "summary_caveat": (qt_payload.get("summary") or {}).get("research_caveat"),
        },
        "replay_metrics": {
            "selected_trade_count": int(row.get("selected_trade_count") or 0),
            "signal_days": int(row.get("signal_days") or 0),
            "trade_win_rate_pct": _f(row.get("trade_win_rate_pct_raw", row.get("trade_win_rate_pct"))),
            "portfolio_compounded_return_pct": port_ret,
            "portfolio_max_drawdown_pct": port_mdd,
            "rolling_1y_latest_return_pct": latest_ret,
            "rolling_1y_latest_max_drawdown_pct": latest_mdd,
            "rolling_1y_evaluation_start_date": row.get("rolling_1y_evaluation_start_date"),
            "rolling_1y_evaluation_end_date": row.get("rolling_1y_evaluation_end_date"),
            "portfolio_calmar_latest_1y": _f(
                row.get("portfolio_calmar_latest_1y_raw", row.get("portfolio_calmar_latest_1y"))
            ),
        },
        "rolling_12m_summary": rolling,
        "rolling_12m_windows": windows,
        "verdict": verdict,
        "bias_and_boundary": _bias_boundary_document(
            qualified_summary=qt_payload.get("summary")
            if isinstance(qt_payload.get("summary"), dict)
            else None,
            candidate_id=str(card.get("candidate_id")),
        ),
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "ok": True,
        "notes": (
            "ok=true means fixed-spec train-window replay completed. "
            "Do not treat as effective-strategy certification or future guarantee."
        ),
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_frozen_train_window_replay(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAFrozenTrainReplayError("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-frozen-train-window-replay.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAFrozenTrainReplayError("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    # Human-readable rolling table (markdown)
    windows = report.get("rolling_12m_windows") or []
    lines = [
        "# 冻结候选 Train 窗固定规格重放（约 2 年）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- candidate: `{(report.get('frozen_candidate') or {}).get('candidate_id')}`",
        f"- fixed_spec / refit: `{report.get('fixed_spec')}` / `{report.get('refit')}`",
        f"- signal span: `{(report.get('input_qualified_trades') or {}).get('first_signal_date')}`"
        f" .. `{(report.get('input_qualified_trades') or {}).get('last_signal_date')}`"
        f" (~`{(report.get('input_qualified_trades') or {}).get('approx_span_years')}`y)",
        "",
        "## 核心指标",
        "",
    ]
    m = report.get("replay_metrics") or {}
    v = report.get("verdict") or {}
    rs = report.get("rolling_12m_summary") or {}
    lines.extend(
        [
            f"- selected_trades / signal_days: **{m.get('selected_trade_count')}** / **{m.get('signal_days')}**",
            f"- full-path compounded return: **{m.get('portfolio_compounded_return_pct')}%**",
            f"- full-path max drawdown: **{m.get('portfolio_max_drawdown_pct')}%** "
            f"(pass_15={v.get('full_path_max_drawdown_pass_15')})",
            f"- latest rolling-12m return / MDD: **{m.get('rolling_1y_latest_return_pct')}%** / "
            f"**{m.get('rolling_1y_latest_max_drawdown_pct')}%** "
            f"(pass_50_15={v.get('latest_rolling_12m_pass_50_15')})",
            f"- rolling windows: **{rs.get('window_count')}**; both-pass rate: "
            f"**{rs.get('both_pass_rate')}** "
            f"({rs.get('both_pass_count')}/{rs.get('window_count')})",
            f"- worst rolling return: **{(rs.get('worst_return_window') or {}).get('return_pct')}%** "
            f"({(rs.get('worst_return_window') or {}).get('start_date')}.."
            f"{(rs.get('worst_return_window') or {}).get('end_date')})",
            f"- worst rolling MDD: **{(rs.get('worst_mdd_window') or {}).get('max_drawdown_pct')}%** "
            f"({(rs.get('worst_mdd_window') or {}).get('start_date')}.."
            f"{(rs.get('worst_mdd_window') or {}).get('end_date')})",
            "",
            "## 边界（不能保证）",
            "",
            "- **不保证**未来任意时期满足 50%/15%",
            "- **不保证**全部历史滚动窗满足 50%/15%",
            "- **不是** formal final-OOS，**不是**有效策略认证",
            "- 候选在 development 上筛选 → 本重放相对选参过程存在样本内偏差",
            "- qualified 可能含当前快照种子的存活/流动性偏差（见 caveat）",
            "",
            "## 滚动 12 月明细",
            "",
            "| # | start | end | return% | MDD% | ret≥50 | dd≤15 | both | trades | win% | PF | Calmar |",
            "|---|-------|-----|---------|------|--------|-------|------|--------|------|----|--------|",
        ]
    )
    for i, w in enumerate(windows, 1):
        lines.append(
            "| {i} | {s} | {e} | {r:.2f} | {m:.2f} | {pr} | {pd} | {pb} | {t} | {wrate} | {pf} | {cal} |".format(
                i=i,
                s=w.get("start_date"),
                e=w.get("end_date"),
                r=float(w.get("return_pct") or 0.0),
                m=float(w.get("max_drawdown_pct") or 0.0),
                pr="Y" if w.get("pass_return_50") else "N",
                pd="Y" if w.get("pass_drawdown_15") else "N",
                pb="Y" if w.get("pass_both_50_15") else "N",
                t=w.get("selected_trade_count"),
                wrate=(
                    f"{float(w.get('win_rate_pct')):.1f}"
                    if w.get("win_rate_pct") is not None
                    else ""
                ),
                pf=(
                    f"{float(w.get('profit_factor')):.2f}"
                    if w.get("profit_factor") is not None
                    else ""
                ),
                cal=(
                    f"{float(w.get('calmar')):.2f}"
                    if w.get("calmar") is not None
                    else ""
                ),
            )
        )
    md_path = output_root / "ROLLING_WINDOWS.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Compact CSV for spreadsheet use
    csv_path = output_root / "rolling_windows.csv"
    csv_lines = [
        "idx,start_date,end_date,return_pct,max_drawdown_pct,pass_return_50,pass_drawdown_15,pass_both_50_15,selected_trade_count,signal_days,win_rate_pct,profit_factor,calmar"
    ]
    for i, w in enumerate(windows, 1):
        csv_lines.append(
            ",".join(
                [
                    str(i),
                    str(w.get("start_date") or ""),
                    str(w.get("end_date") or ""),
                    "" if w.get("return_pct") is None else f"{float(w['return_pct']):.6f}",
                    ""
                    if w.get("max_drawdown_pct") is None
                    else f"{float(w['max_drawdown_pct']):.6f}",
                    "1" if w.get("pass_return_50") else "0",
                    "1" if w.get("pass_drawdown_15") else "0",
                    "1" if w.get("pass_both_50_15") else "0",
                    str(w.get("selected_trade_count") or ""),
                    str(w.get("signal_days") or ""),
                    ""
                    if w.get("win_rate_pct") is None
                    else f"{float(w['win_rate_pct']):.6f}",
                    ""
                    if w.get("profit_factor") is None
                    else f"{float(w['profit_factor']):.6f}",
                    "" if w.get("calmar") is None else f"{float(w['calmar']):.6f}",
                ]
            )
        )
    csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_frozen_train_window_replay",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "fixed_spec": True,
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "candidate_id": (report.get("frozen_candidate") or {}).get("candidate_id"),
        "candidate_spec_sha256": (report.get("frozen_candidate") or {}).get(
            "candidate_spec_sha256"
        ),
        "train_first_signal_date": (report.get("input_qualified_trades") or {}).get(
            "first_signal_date"
        ),
        "train_last_signal_date": (report.get("input_qualified_trades") or {}).get(
            "last_signal_date"
        ),
        "approx_span_years": (report.get("input_qualified_trades") or {}).get(
            "approx_span_years"
        ),
        "selected_trade_count": m.get("selected_trade_count"),
        "portfolio_compounded_return_pct": m.get("portfolio_compounded_return_pct"),
        "portfolio_max_drawdown_pct": m.get("portfolio_max_drawdown_pct"),
        "rolling_1y_latest_return_pct": m.get("rolling_1y_latest_return_pct"),
        "rolling_1y_latest_max_drawdown_pct": m.get("rolling_1y_latest_max_drawdown_pct"),
        "rolling_window_count": rs.get("window_count"),
        "rolling_both_pass_rate": rs.get("both_pass_rate"),
        "latest_rolling_12m_pass_50_15": v.get("latest_rolling_12m_pass_50_15"),
        "full_path_max_drawdown_pass_15": v.get("full_path_max_drawdown_pass_15"),
        "all_rolling_windows_pass_50_15": v.get("all_rolling_windows_pass_50_15"),
        "meets_user_requirement_as_guarantee": False,
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "rolling_windows_md": str(md_path.resolve()),
        "rolling_windows_csv": str(csv_path.resolve()),
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
    "PathAFrozenTrainReplayError",
    "build_path_a_frozen_train_window_replay",
    "write_path_a_frozen_train_window_replay",
]
