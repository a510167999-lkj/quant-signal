"""Path A P3: monthly slow-update adaptive layer (catalog §5).

Each calendar month, evaluate the frozen arm pool over the past 126 trading days
(data-as-of only) and select one arm per a pre-registered rule. Write each
decision to a ledger. The selection rule and tiebreaks are frozen in the specs
module; no in-window grid search. The engine simulates the monthly decisions in
chronological order (walk-forward style: each decision uses only past data),
then evaluates the resulting spliced equity against the always-e4_primary baseline.

WF splice != formal final-OOS.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p0_drawdown_overlay_specs as p0_specs
from app import factor_v3_path_a_p1_acceptance_protocol as p1a
from app import factor_v3_path_a_p2_small_variants as p2
from app import factor_v3_path_a_p2_small_variants_specs as p2_specs
from app import factor_v3_path_a_p3_monthly_slow_update_specs as specs
from app import factor_v3_train_window_freeze_contract as freeze
from app.research_equity import _equity_points_from_slot_daily_returns
from app.research_sweep import _trade_metrics

STAGE_GOAL_ID = "path-a-p3-monthly-slow-update/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A P3：月度慢更新（≤3 冻结臂，每月用过去 126 交易日按事前准则选臂，"
    "写 ledger，换档冷却 20 交易日）；WF 风格模拟；禁止估计窗内搜参；"
    "永不自动交易；WF 拼接 ≠ formal final-OOS；不保证 50/15。"
)
REPORT_SCHEMA = "path-a-p3-monthly-slow-update-report/v1"
POINTER_SCHEMA = "path-a-p3-monthly-slow-update-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_p3_monthly_slow_update")


class PathAP3Error(ValueError):
    """Raised when P3 stage fails closed."""


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
        raise PathAP3Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


# ---------------------------------------------------------------------------
# Arm trade preparation (reuse P0 overlay / P2 vol-target logic)
# ---------------------------------------------------------------------------

def _arm_trades(
    arm_id: str,
    *,
    base_selected: list[dict[str, Any]],
    baseline_curve: list[dict[str, Any]],
    train_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the full train-window trade list for a given arm (no slicing).

    Each arm reuses the frozen P0/P2 machinery so its economic identity matches
    the catalog. e4_primary is the raw kernel selection; p0_loss_streak_3 applies
    the loss-streak overlay; p2_vol_target_10 applies the vol-target scaling.
    """

    if arm_id == "e4_primary":
        return list(base_selected)
    if arm_id == "p0_loss_streak_3":
        applied = p0.apply_p0_overlay(
            base_selected,
            deepcopy(specs.CIRCUIT_BREAKER_OVERLAY),
            kernel_selected=base_selected,
        )
        return applied["accepted"]
    if arm_id == "p2_vol_target_10":
        kernel = p0._kernel_dict()
        scale_by_date = p2._vol_target_scale_series(
            baseline_curve,
            lookback_days=p2_specs.VOL_TARGET_LOOKBACK_DAYS,
            annual_target_pct=p2_specs.VOL_TARGET_ANNUAL_PCT,
            min_scale=p2_specs.VOL_TARGET_MIN_SCALE,
            max_scale=p2_specs.VOL_TARGET_MAX_SCALE,
        )
        return p2._apply_vol_target(base_selected, scale_by_date)
    raise PathAP3Error(f"unknown arm_id: {arm_id}")


# ---------------------------------------------------------------------------
# Monthly decision points
# ---------------------------------------------------------------------------

def _month_decision_points(signal_dates: list[str]) -> list[str]:
    """First available signal_date in each calendar month (chronological)."""

    if not signal_dates:
        return []
    points: list[str] = []
    seen_months: set[tuple[int, int]] = set()
    for d in sorted(signal_dates):
        if len(d) < 7:
            continue
        ym = (int(d[:4]), int(d[5:7]))
        if ym in seen_months:
            continue
        seen_months.add(ym)
        points.append(d)
    return points


def _window_score(
    arm_trades: list[dict[str, Any]],
    *,
    window_end: str,
    window_days: int,
    kernel: dict[str, Any],
) -> dict[str, Any]:
    """Score an arm over the estimation window ending at window_end (inclusive).

    Uses only trades whose signal_date is within the past window_days trading
    days strictly before the decision point. Returns the selection keys.
    """

    # Build the chronological list of distinct signal dates <= window_end to
    # measure "trading days" as signal-day granularity (data-as-of).
    dates_in = sorted(
        {str(t.get("signal_date") or "")[:10] for t in arm_trades}
    )
    dates_up_to = [d for d in dates_in if d <= window_end]
    if not dates_up_to:
        return {"abs_mdd": 1e9, "compounded_return_pct": -1e9, "both_pass_rate": 0.0,
                "trade_count": 0}
    # the window covers the last window_days signal-dates up to (not incl.) the
    # decision point; here window_end is the day before the decision.
    window_dates = set(dates_up_to[-window_days:])
    window_trades = [
        t for t in arm_trades if str(t.get("signal_date") or "")[:10] in window_dates
    ]
    if not window_trades:
        return {"abs_mdd": 1e9, "compounded_return_pct": -1e9, "both_pass_rate": 0.0,
                "trade_count": 0}
    metrics = _trade_metrics(
        window_trades,
        hold_days=int(kernel["hold_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
        exposure_multiplier=float(kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
        capital_model=str(kernel["capital_model"]),
    )
    mdd = metrics.get("portfolio_max_drawdown_pct")
    return {
        "abs_mdd": abs(float(mdd)) if mdd is not None else 1e9,
        "compounded_return_pct": float(
            metrics.get("portfolio_compounded_return_pct") or -1e9
        ),
        "both_pass_rate": float(
            (metrics.get("rolling_12m_summary") or {}).get("both_pass_rate") or 0.0
        ),
        "trade_count": int(metrics.get("selected_trade_count") or 0),
    }


def _select_arm(scores: dict[str, dict[str, Any]]) -> str:
    """Apply the frozen selection rule + tiebreaks."""

    best_id: str | None = None
    best_key: tuple | None = None
    for arm_id, s in scores.items():
        # ascending abs_mdd, descending return, descending both_pass, ascending id
        key = (s["abs_mdd"], -s["compounded_return_pct"], -s["both_pass_rate"], arm_id)
        if best_key is None or key < best_key:
            best_key = key
            best_id = arm_id
    return best_id  # type: ignore[return-value]


def simulate_monthly_decisions(
    *,
    arm_trades_by_id: dict[str, list[dict[str, Any]]],
    all_signal_dates: list[str],
    window_days: int,
    cooldown_days: int,
    initial_arm: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk through monthly decision points; produce ledger + per-period arm map.

    Returns (ledger, periods) where each period is {start, end, arm_id}.
    Decisions use only data strictly before the decision date (the estimation
    window ends the signal-day before the decision point).
    """

    kernel = p0._kernel_dict()
    decision_points = _month_decision_points(all_signal_dates)
    if not decision_points:
        return [], []

    ledger: list[dict[str, Any]] = []
    periods: list[dict[str, Any]] = []
    current_arm = initial_arm
    last_switch_idx = 0  # decision point index when current arm was chosen
    decision_idx = 0

    # the first decision point cannot have prior data; seed with initial arm.
    for idx, dp in enumerate(decision_points):
        decision_idx = idx
        if idx == 0:
            ledger.append({
                "decision_date": dp,
                "selected_arm": current_arm,
                "scores": {},
                "reason": "initial_seed (no prior estimation window)",
                "data_cutoff": None,
                "switched": False,
            })
            continue

        # estimation window = past window_days signal dates ending the day
        # before this decision point (strictly past data only).
        prior_dates = sorted(d for d in all_signal_dates if d < dp)
        if not prior_dates:
            ledger.append({
                "decision_date": dp,
                "selected_arm": current_arm,
                "scores": {},
                "reason": "no_prior_data",
                "data_cutoff": None,
                "switched": False,
            })
            continue
        window_end = prior_dates[-1]
        scores: dict[str, dict[str, Any]] = {}
        for arm_id, arm_trades in arm_trades_by_id.items():
            scores[arm_id] = _window_score(
                arm_trades,
                window_end=window_end,
                window_days=window_days,
                kernel=kernel,
            )
        best = _select_arm(scores)
        switched = False
        days_since_switch = decision_idx - last_switch_idx
        if best != current_arm and best is not None:
            if days_since_switch >= _cooldown_in_decision_points(
                cooldown_days, all_signal_dates
            ):
                current_arm = best
                last_switch_idx = decision_idx
                switched = True
                reason = f"switch (cooldown_ok, days_since_switch={days_since_switch})"
            else:
                reason = (
                    f"hold (cooldown_active, best={best}, "
                    f"days_since_switch={days_since_switch})"
                )
        else:
            reason = "hold (current_is_best)"
        ledger.append({
            "decision_date": dp,
            "selected_arm": current_arm,
            "scores": {k: {kk: round(vv, 6) if isinstance(vv, float) else vv
                           for kk, vv in v.items()} for k, v in scores.items()},
            "reason": reason,
            "data_cutoff": window_end,
            "switched": switched,
        })

    # build periods from ledger: each period runs from one decision point to
    # just before the next; trades in that interval use the selected arm.
    for i, entry in enumerate(ledger):
        start = entry["decision_date"]
        end = ledger[i + 1]["decision_date"] if i + 1 < len(ledger) else "9999-99-99"
        periods.append({
            "start": start,
            "end": end,
            "arm_id": entry["selected_arm"],
            "decision_date": start,
        })
    return ledger, periods


def _cooldown_in_decision_points(
    cooldown_days: int,
    all_signal_dates: list[str],
) -> int:
    """Approximate the trading-day cooldown as a count of monthly decision points.

    With ~21 trading days per month and monthly cadence, 20 trading days ≈ 1
    decision point. We use max(1, round(cooldown_days / 21)) so a switch can
    happen at earliest the next monthly decision after a switch.
    """

    return max(1, round(cooldown_days / 21.0))


def _period_trades(
    periods: list[dict[str, Any]],
    arm_trades_by_id: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Collect trades for each period from the arm selected for that period."""

    out: list[dict[str, Any]] = []
    for period in periods:
        arm_trades = arm_trades_by_id.get(period["arm_id"], [])
        for t in arm_trades:
            d = str(t.get("signal_date") or "")[:10]
            if period["start"] <= d < period["end"]:
                out.append(deepcopy(t))
    return out


def _metrics_bundle(selected: list[dict[str, Any]]) -> dict[str, Any]:
    kernel = p0._kernel_dict()
    if not selected:
        return {
            "selected_trade_count": 0,
            "signal_days": 0,
            "portfolio_compounded_return_pct": 0.0,
            "portfolio_max_drawdown_pct": 0.0,
            "rolling_1y_latest_return_pct": None,
            "rolling_1y_latest_max_drawdown_pct": None,
            "rolling_12m_summary": train_replay._rolling_summary([]),
            "rolling_both_pass_rate": 0.0,
            "trade_win_rate_pct": None,
        }
    metrics = _trade_metrics(
        selected,
        hold_days=int(kernel["hold_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
        exposure_multiplier=float(kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
        capital_model=str(kernel["capital_model"]),
    )
    rs = train_replay._rolling_summary(
        [train_replay._window_row(w) for w in (metrics.get("rolling_1y_windows") or [])]
    )
    return {
        "selected_trade_count": int(metrics.get("selected_trade_count") or 0),
        "signal_days": int(metrics.get("signal_days") or 0),
        "portfolio_compounded_return_pct": metrics.get(
            "portfolio_compounded_return_pct"
        ),
        "portfolio_max_drawdown_pct": metrics.get("portfolio_max_drawdown_pct"),
        "rolling_1y_latest_return_pct": metrics.get("rolling_1y_latest_return_pct"),
        "rolling_1y_latest_max_drawdown_pct": metrics.get(
            "rolling_1y_latest_max_drawdown_pct"
        ),
        "rolling_12m_summary": rs,
        "rolling_both_pass_rate": rs.get("both_pass_rate"),
        "trade_win_rate_pct": metrics.get("trade_win_rate_pct"),
    }


def build_path_a_p3_monthly_slow_update(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()

    card_path = root / DEFAULT_FROZEN_CANDIDATE
    card = train_replay._load_json(card_path)
    if card is None:
        raise PathAP3Error(f"frozen candidate missing: {card_path}")
    if card.get("candidate_id") != f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]:
        raise PathAP3Error("frozen candidate id mismatch")

    qt_path = root / DEFAULT_QUALIFIED_TRADES
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAP3Error("qualified trades missing")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))
    base_kernel = p0._kernel_dict()
    base_selected = p0._select_kernel_trades(train_trades)
    baseline_curve = _equity_points_from_slot_daily_returns(
        base_selected,
        max_active_positions=int(base_kernel["max_active_positions"]),
        exposure_multiplier=float(base_kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(base_kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(base_kernel["roundtrip_cost_bps"]),
        slippage_bps=float(base_kernel["slippage_bps"]),
    )

    # Prepare each arm's full train-window trades once.
    arm_trades_by_id: dict[str, list[dict[str, Any]]] = {}
    for arm in specs.iter_arm_pool():
        arm_trades_by_id[arm["arm_id"]] = _arm_trades(
            arm["arm_id"],
            base_selected=base_selected,
            baseline_curve=baseline_curve,
            train_trades=train_trades,
        )

    all_signal_dates = sorted(
        {str(t.get("signal_date") or "")[:10] for t in base_selected}
    )
    ledger, periods = simulate_monthly_decisions(
        arm_trades_by_id=arm_trades_by_id,
        all_signal_dates=all_signal_dates,
        window_days=specs.ESTIMATION_WINDOW_TRADING_DAYS,
        cooldown_days=specs.SWITCH_COOLDOWN_TRADING_DAYS,
        initial_arm="e4_primary",
    )

    # Spliced P3 equity = trades selected per period by the chosen arm.
    p3_trades = _period_trades(periods, arm_trades_by_id)
    p3_metrics = _metrics_bundle(p3_trades)
    p3_scoreboard = p1a.build_w_scoreboard(p3_metrics, rolling_summary=p3_metrics.get("rolling_12m_summary"))

    # Baseline comparator: always e4_primary.
    base_metrics = _metrics_bundle(base_selected)
    base_scoreboard = p1a.build_w_scoreboard(base_metrics, rolling_summary=base_metrics.get("rolling_12m_summary"))

    # P0 winner comparator: always p0_loss_streak_3.
    p0_metrics = _metrics_bundle(arm_trades_by_id["p0_loss_streak_3"])
    p0_scoreboard = p1a.build_w_scoreboard(p0_metrics, rolling_summary=p0_metrics.get("rolling_12m_summary"))

    switch_count = sum(1 for e in ledger if e.get("switched"))
    arm_usage: dict[str, int] = {}
    for e in ledger:
        arm_usage[e["selected_arm"]] = arm_usage.get(e["selected_arm"], 0) + 1

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_p3_monthly_slow_update",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "parameter_search": False,
        "discipline": {
            "cadence": specs.UPDATE_CADENCE,
            "estimation_window_trading_days": specs.ESTIMATION_WINDOW_TRADING_DAYS,
            "switch_cooldown_trading_days": specs.SWITCH_COOLDOWN_TRADING_DAYS,
            "selection_criterion": specs.SELECTION_CRITERION,
            "selection_tiebreak": specs.SELECTION_TIEBREAK,
            "arm_pool_size": len(specs.ARM_POOL),
            "in_window_grid_search": False,
        },
        "signal_kernel": base_kernel,
        "signal_kernel_sha256": p0._sha(base_kernel),
        "frozen_candidate_id": card.get("candidate_id"),
        "frozen_candidate_spec_sha256": card.get("candidate_spec_sha256"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "kernel_selected_trade_count": len(base_selected),
        "decision_point_count": len(ledger),
        "switch_count": switch_count,
        "arm_usage_in_decisions": arm_usage,
        "ledger": ledger,
        "p3_spliced": {
            "metrics": p3_metrics,
            "scoreboard": p3_scoreboard,
            "p1a_acceptance": p1a.validate_variants(
                [{"candidate_id": "p3_monthly_slow_update", "scoreboard": p3_scoreboard}]
            ),
        },
        "baseline_always_e4_primary": {
            "metrics": base_metrics,
            "scoreboard": base_scoreboard,
        },
        "baseline_always_p0_loss_streak_3": {
            "metrics": p0_metrics,
            "scoreboard": p0_scoreboard,
        },
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "meets_user_requirement_as_guarantee": False,
        "notes": (
            "P3 monthly slow-update: decisions use only past estimation-window data. "
            "WF-style simulation; spliced != formal final-OOS. "
            "Short train (~2y) yields few decision points; limited statistical power."
        ),
        "ok": True,
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_p3_monthly_slow_update(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAP3Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-p3-monthly-slow-update.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAP3Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    p3m = (report.get("p3_spliced") or {}).get("metrics") or {}
    bm = (report.get("baseline_always_e4_primary") or {}).get("metrics") or {}
    p0m = (report.get("baseline_always_p0_loss_streak_3") or {}).get("metrics") or {}
    lines = [
        "# P3 月度慢更新（WF 风格模拟）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- 决策点: {report.get('decision_point_count')} · 换档: {report.get('switch_count')}",
        f"- 臂使用: `{report.get('arm_usage_in_decisions')}`",
        f"- guarantee: **false** · WF 拼接 ≠ formal final-OOS",
        "",
        "## 对照（train 固定重放）",
        "",
        "| 方案 | 收益% | MDD% | 双过率 | 笔数 |",
        "|------|-------|------|--------|------|",
        f"| 始终 e4_primary | {_fmt(bm.get('portfolio_compounded_return_pct'))} | {_fmt(bm.get('portfolio_max_drawdown_pct'))} | {_fmt(bm.get('rolling_both_pass_rate'))} | {bm.get('selected_trade_count')} |",
        f"| 始终 p0_loss_streak_3 | {_fmt(p0m.get('portfolio_compounded_return_pct'))} | {_fmt(p0m.get('portfolio_max_drawdown_pct'))} | {_fmt(p0m.get('rolling_both_pass_rate'))} | {p0m.get('selected_trade_count')} |",
        f"| **P3 月度慢更新** | {_fmt(p3m.get('portfolio_compounded_return_pct'))} | {_fmt(p3m.get('portfolio_max_drawdown_pct'))} | {_fmt(p3m.get('rolling_both_pass_rate'))} | {p3m.get('selected_trade_count')} |",
        "",
        "## Ledger（每月决策）",
        "",
        "| 日期 | 选臂 | 换档 | 原因 |",
        "|------|------|------|------|",
    ]
    for e in report.get("ledger") or []:
        flag = "✓" if e.get("switched") else ""
        lines.append(
            f"| {e.get('decision_date')} | `{e.get('selected_arm')}` | {flag} | {e.get('reason')} |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- WF 拼接成绩仍 ≠ formal final-OOS",
            "- 估计窗内未搜参；换档遵守冷却",
            "- 训练窗约 2y，决策点有限，统计意义有限",
            "",
        ]
    )
    md_path = output_root / "MONTHLY_LEDGER.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_p3_monthly_slow_update",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "refit": False,
        "parameter_search": False,
        "effective_strategy_found": False,
        "meets_user_requirement_as_guarantee": False,
        "decision_point_count": report.get("decision_point_count"),
        "switch_count": report.get("switch_count"),
        "arm_usage_in_decisions": report.get("arm_usage_in_decisions"),
        "p3_spliced_scoreboard": (report.get("p3_spliced") or {}).get("scoreboard"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "monthly_ledger_md": str(md_path.resolve()),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAP3Error",
    "build_path_a_p3_monthly_slow_update",
    "simulate_monthly_decisions",
    "write_path_a_p3_monthly_slow_update",
]
