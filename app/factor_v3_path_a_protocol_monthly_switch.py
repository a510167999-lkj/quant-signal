"""Monthly 3-arm switch: cash / bounce_dn2_negext / sig_pull_negext_h5.

Walk-forward. Decisions use only data before the month's first signal date.
Not formal OOS. Identities were already seen on holdout; the switch rule is new.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p3_monthly_slow_update as p3
from app import factor_v3_path_a_protocol_monthly_switch_specs as specs
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import merged_kernel
from app.factor_v3_path_a_protocol_baseline_specs import apply_rank_key
from app.factor_v3_path_a_protocol_signal import (
    DEFAULT_BOUNCE_QT_PATH,
    DEFAULT_HOLD_QT_PATH,
)
from app.jiaoch_live_market import JIAOCH_DAILY_CACHE_SOURCE_VERSION
from app.storage import write_json

STAGE_GOAL_ID = specs.STAGE_GOAL_ID
REPORT_SCHEMA = "path-a-protocol-monthly-switch-report/v1"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_monthly_switch")


class PathAMonthlySwitchError(ValueError):
    """Raised when the monthly identity switch cannot be scored."""


def select_live_arm(scores: dict[str, dict[str, Any]]) -> str:
    """Highest window return among arms with MDD <= 15%. Else cash."""

    eligible: list[tuple[str, dict[str, Any]]] = []
    for arm_id, score in scores.items():
        if arm_id == specs.CASH_ARM_ID:
            continue
        if int(score.get("trade_count") or 0) <= 0:
            continue
        if float(score.get("abs_mdd") or 1e9) > specs.MAX_ABS_MDD_PCT:
            continue
        if float(score.get("compounded_return_pct") or -1e9) <= -1e8:
            continue
        eligible.append((arm_id, score))
    if not eligible:
        return specs.CASH_ARM_ID
    eligible.sort(
        key=lambda item: (
            -float(item[1]["compounded_return_pct"]),
            float(item[1]["abs_mdd"]),
            item[0],
        )
    )
    return eligible[0][0]


def _locked_dates(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in trades:
        day = str(trade.get("signal_date") or "")[:10]
        if proto.TRAIN_START <= day <= proto.HOLDOUT_END:
            out.append(trade)
    return out


def _select_arm_trades(trades: list[dict[str, Any]], variant: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = apply_rank_key(trades, variant.get("rank_key"))
    if not ranked:
        return []
    try:
        return p0._select_kernel_trades(
            ranked, kernel_override=merged_kernel(variant)
        )
    except p0.PathAP0Error:
        return []


def _load_selected_arm(
    path: Path,
    *,
    variant: dict[str, Any],
    expected_family: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = train_replay._load_json(path)
    if payload is None:
        raise PathAMonthlySwitchError(f"qualified trades missing: {path}")
    meta = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != JIAOCH_DAILY_CACHE_SOURCE_VERSION:
        raise PathAMonthlySwitchError(f"QT is not Jiaoch v2: {path}")
    if meta.get("slice") == "traded":
        raise PathAMonthlySwitchError("refuses the contaminated 191-name slice")
    if meta.get("signal_family") != expected_family:
        raise PathAMonthlySwitchError(
            f"QT family {meta.get('signal_family')!r} != {expected_family!r}"
        )
    locked = _locked_dates(list(payload.get("qualified_trades") or []))
    return _select_arm_trades(locked, variant), meta


def simulate_monthly_switch(
    *,
    arm_trades_by_id: dict[str, list[dict[str, Any]]],
    calendar_dates: list[str],
    window_days: int = specs.ESTIMATION_WINDOW_TRADING_DAYS,
    cooldown_days: int = specs.SWITCH_COOLDOWN_TRADING_DAYS,
    initial_arm: str = specs.INITIAL_ARM_ID,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kernel = p0._kernel_dict()
    decision_points = p3._month_decision_points(calendar_dates)
    if not decision_points:
        return [], []
    ledger: list[dict[str, Any]] = []
    current = initial_arm
    last_switch_idx = 0
    cooldown_points = p3._cooldown_in_decision_points(cooldown_days, calendar_dates)
    live_ids = specs.live_arm_ids()
    for idx, day in enumerate(decision_points):
        if idx == 0:
            ledger.append(
                {
                    "decision_date": day,
                    "selected_arm": current,
                    "scores": {},
                    "reason": "initial_seed_cash",
                    "data_cutoff": None,
                    "switched": False,
                }
            )
            continue
        prior = [item for item in calendar_dates if item < day]
        if not prior:
            ledger.append(
                {
                    "decision_date": day,
                    "selected_arm": current,
                    "scores": {},
                    "reason": "no_prior_data",
                    "data_cutoff": None,
                    "switched": False,
                }
            )
            continue
        cutoff = prior[-1]
        scores = {
            arm_id: p3._window_score(
                arm_trades_by_id.get(arm_id) or [],
                window_end=cutoff,
                window_days=window_days,
                kernel=kernel,
            )
            for arm_id in live_ids
        }
        best = select_live_arm(scores)
        switched = False
        days_since = idx - last_switch_idx
        if best != current:
            if days_since >= cooldown_points:
                current = best
                last_switch_idx = idx
                switched = True
                reason = f"switch_to_{best}"
            else:
                reason = f"hold_cooldown_best_{best}"
        else:
            reason = "hold_current_is_best"
        ledger.append(
            {
                "decision_date": day,
                "selected_arm": current,
                "scores": scores,
                "reason": reason,
                "data_cutoff": cutoff,
                "switched": switched,
            }
        )
    periods: list[dict[str, Any]] = []
    for index, entry in enumerate(ledger):
        start = str(entry["decision_date"])
        end = (
            str(ledger[index + 1]["decision_date"])
            if index + 1 < len(ledger)
            else "9999-99-99"
        )
        periods.append(
            {
                "start": start,
                "end": end,
                "arm_id": entry["selected_arm"],
                "decision_date": start,
            }
        )
    return ledger, periods


def _score_book(trades: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = p3._metrics_bundle(trades)
    latest = metrics.get("rolling_1y_latest_return_pct")
    mdd = metrics.get("portfolio_max_drawdown_pct")
    dual = False
    if latest is not None and mdd is not None:
        dual = proto.meets_locked_split_targets(
            latest_12m_net_return_pct=float(latest),
            partition_max_drawdown_pct=float(mdd),
        )
    return {
        **metrics,
        "dual_pass_26_15": dual,
    }


def build_path_a_protocol_monthly_switch(
    *,
    repo_root: Path | None = None,
    bounce_qt_path: Path | None = None,
    reclaim_qt_path: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAMonthlySwitchError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    bounce_path = (
        Path(bounce_qt_path).resolve()
        if bounce_qt_path is not None
        else (root / DEFAULT_BOUNCE_QT_PATH)
    )
    reclaim_path = (
        Path(reclaim_qt_path).resolve()
        if reclaim_qt_path is not None
        else (root / DEFAULT_HOLD_QT_PATH)
    )
    bounce_selected, bounce_meta = _load_selected_arm(
        bounce_path,
        variant=specs.BOUNCE_VARIANT,
        expected_family="path-a-protocol-signal-bounce/v1",
    )
    reclaim_selected, reclaim_meta = _load_selected_arm(
        reclaim_path,
        variant=specs.RECLAIM_VARIANT,
        expected_family="path-a-protocol-signal-hold/v1",
    )
    arm_trades = {
        specs.BOUNCE_ARM_ID: bounce_selected,
        specs.RECLAIM_ARM_ID: reclaim_selected,
        specs.CASH_ARM_ID: [],
    }
    calendar = sorted(
        {
            str(trade.get("signal_date") or "")[:10]
            for trade in (bounce_selected + reclaim_selected)
            if str(trade.get("signal_date") or "")
        }
    )
    ledger, periods = simulate_monthly_switch(
        arm_trades_by_id=arm_trades,
        calendar_dates=calendar,
    )
    spliced = p3._period_trades(periods, arm_trades)
    train = proto.filter_trades_for_partition(spliced, "train")
    holdout = proto.filter_trades_for_partition(spliced, "holdout")
    usage: dict[str, int] = {}
    for entry in ledger:
        arm = str(entry["selected_arm"])
        usage[arm] = usage.get(arm, 0) + 1
    train_score = _score_book(train)
    holdout_score = _score_book(holdout)
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "selection_criterion": specs.SELECTION_CRITERION,
        "initial_arm": specs.INITIAL_ARM_ID,
        "arms": [
            {"arm_id": row["arm_id"], "zh": row["zh"]} for row in specs.iter_arm_pool()
        ],
        "bounce_qt": str(bounce_path),
        "reclaim_qt": str(reclaim_path),
        "bounce_name_count": bounce_meta.get("name_count"),
        "reclaim_name_count": reclaim_meta.get("name_count"),
        "bounce_selected": len(bounce_selected),
        "reclaim_selected": len(reclaim_selected),
        "decision_point_count": len(ledger),
        "switch_count": sum(1 for row in ledger if row.get("switched")),
        "arm_usage": usage,
        "ledger": ledger,
        "periods": periods,
        "spliced_trade_count": len(spliced),
        "train": train_score,
        "holdout": holdout_score,
        "both_partition_26_15": bool(
            train_score["dual_pass_26_15"] and holdout_score["dual_pass_26_15"]
        ),
        "always_bounce_holdout": _score_book(
            proto.filter_trades_for_partition(bounce_selected, "holdout")
        ),
        "always_reclaim_holdout": _score_book(
            proto.filter_trades_for_partition(reclaim_selected, "holdout")
        ),
        "independent_oos": False,
        "formal_final_oos": False,
        "effective_strategy": False,
        "promotable": False,
        "zero_refit": True,
        "parameter_search": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "vps_runtime_role": os.getenv("VPS_RUNTIME_ROLE", ""),
    }


def format_monthly_switch_table(report: dict[str, Any]) -> str:
    train = report.get("train") or {}
    holdout = report.get("holdout") or {}
    return "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"rule={report.get('selection_criterion')}",
            f"decisions={report.get('decision_point_count')} "
            f"switches={report.get('switch_count')} "
            f"usage={report.get('arm_usage')}",
            f"train_1y={train.get('rolling_1y_latest_return_pct')} "
            f"train_mdd={train.get('portfolio_max_drawdown_pct')} "
            f"train_dual={train.get('dual_pass_26_15')} "
            f"n={train.get('selected_trade_count')}",
            f"holdout_1y={holdout.get('rolling_1y_latest_return_pct')} "
            f"holdout_mdd={holdout.get('portfolio_max_drawdown_pct')} "
            f"holdout_dual={holdout.get('dual_pass_26_15')} "
            f"n={holdout.get('selected_trade_count')}",
            f"both_partition_26_15={report.get('both_partition_26_15')}",
            f"effective_strategy={report.get('effective_strategy')}",
            "",
        ]
    )


def write_path_a_protocol_monthly_switch(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    slim = deepcopy(report)
    # ledger scores are useful; keep them. Full periods stay.
    write_json(str(output_root / "LATEST.json"), slim)
    table = format_monthly_switch_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    (output_root / "LEDGER.txt").write_text(
        "\n".join(
            f"{row.get('decision_date')} {row.get('selected_arm')} "
            f"switch={row.get('switched')} {row.get('reason')}"
            for row in (report.get("ledger") or [])
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "effective_strategy": False,
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }
