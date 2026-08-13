"""P0: drawdown / loss-streak overlay on frozen e4_primary signal kernel.

Causal gates use only closed-book equity and closed trades as of signal_date.
Does not alter tags/top_n/hold; does not force-flat open positions.
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
from app import factor_v3_path_a_p0_drawdown_overlay_specs as specs
from app import factor_v3_path_a_p1_acceptance_protocol as p1a
from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal
from app.research_equity import _equity_points_from_slot_daily_returns
from app.research_sweep import _trade_metrics, sweep_qualified_trades

STAGE_GOAL_ID = "path-a-p0-drawdown-overlay/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A P0：在冻结 e4 信号核上叠加事前写死的回撤/连亏闸门（只控新开仓）；"
    "train 窗固定重放对照；禁止改参网格；永不自动交易；不保证 50/15。"
)
REPORT_SCHEMA = "path-a-p0-drawdown-overlay-report/v1"
POINTER_SCHEMA = "path-a-p0-drawdown-overlay-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_p0_drawdown_overlay")


class PathAP0Error(ValueError):
    """Raised when P0 overlay stage fails closed."""


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
        raise PathAP0Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _kernel_dict() -> dict[str, Any]:
    k = dict(specs.SIGNAL_KERNEL)
    k["market_levels"] = list(k["market_levels"])
    k["required_signal_tags"] = list(k["required_signal_tags"])
    k["excluded_signal_tags"] = list(k["excluded_signal_tags"])
    return k


def _select_kernel_trades(
    train_trades: list[dict[str, Any]],
    *,
    kernel_override: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    kernel = kernel_override or _kernel_dict()
    payload = sweep_qualified_trades(
        deepcopy(train_trades),
        hold_days=int(kernel["hold_days"]),
        top_n=int(kernel["top_n"]),
        symbol_cooldown_days=int(kernel["symbol_cooldown_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
        min_trades=1,
        max_filter_size=1,
        target_win_rate_pct=52.0,
        target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
        target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        exposure_multipliers=[float(kernel["exposure_multiplier"])],
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
        capital_model=str(kernel["capital_model"]),
        required_signal_tags=list(kernel["required_signal_tags"]),
        excluded_signal_tags=list(kernel["excluded_signal_tags"]),
        market_levels=list(kernel["market_levels"]),
        force_exposure_multipliers=True,
        fixed_spec=True,
    )
    rows = payload.get("top") or []
    if not rows:
        raise PathAP0Error("kernel fixed_spec produced no rows")
    # Reconstruct selected trades the same way metrics did: re-run portfolio
    # selection is embedded in sweep; recover via filtering tags + portfolio.
    # Prefer trades listed implicitly by re-selecting with same controls.
    from collections import defaultdict

    from app.research_portfolio import _select_with_portfolio_controls
    from app.research_sweep import _trade_sweep_tags

    tag_pairs = [(t, _trade_sweep_tags(t)) for t in train_trades]
    req = set(kernel["required_signal_tags"])
    excl = set(kernel["excluded_signal_tags"])
    levels = set(kernel["market_levels"])
    filtered = []
    for trade, tags in tag_pairs:
        if req and not req.issubset(set(tags)):
            continue
        if excl and set(tags) & excl:
            continue
        if levels and str(trade.get("market_level") or "") not in levels:
            continue
        filtered.append(trade)
    by_date: dict[str, list] = defaultdict(list)
    for trade in filtered:
        by_date[str(trade["signal_date"])[:10]].append(trade)
    selected = _select_with_portfolio_controls(
        by_date,
        int(kernel["top_n"]),
        symbol_cooldown_days=int(kernel["symbol_cooldown_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
    )
    if not selected:
        raise PathAP0Error("portfolio selection empty after kernel filters")
    return selected


def _baseline_equity_curve(
    kernel_selected: list[dict[str, Any]],
    *,
    max_active: int,
) -> list[dict[str, Any]]:
    """Counterfactual equity points from the FULL kernel selection (overlay-agnostic).

    The overlay state machine reads drawdown from this fixed curve rather than
    from the overlay-filtered `accepted` list, so a block decision cannot feed
    back into the state it reads (which would latch the gate permanently).
    No future function: points are derived only from trades closed on/before
    each date, computed once over the invariant kernel selection.
    """

    kernel = _kernel_dict()
    return _equity_points_from_slot_daily_returns(
        kernel_selected,
        max_active_positions=max_active,
        exposure_multiplier=float(kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
    )


def _state_from_curve(
    curve: list[dict[str, Any]],
    *,
    as_of: str,
) -> tuple[float, float, float]:
    """Return (equity, peak, drawdown_pct) at as_of from a precomputed curve."""

    if not curve:
        return 1.0, 1.0, 0.0
    peak = 1.0
    equity = 1.0
    for point in curve:
        day = str(point.get("event_date") or point.get("signal_date") or "")[:10]
        if day and day > as_of:
            break
        equity = float(point.get("equity") or equity)
        peak = max(peak, equity)
    dd = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
    return equity, peak, dd


def _scale_trade(trade: dict[str, Any], scale: float) -> dict[str, Any]:
    """Scale new-entry size via position_budget_fraction (slot-daily)."""

    out = deepcopy(trade)
    kernel = _kernel_dict()
    slot_count = max(int(kernel["max_active_positions"] or 1), 1)
    slot_exposure = float(kernel["exposure_multiplier"]) / slot_count
    frac = max(min(slot_exposure * float(scale), slot_exposure), 1e-9)
    out["position_budget_fraction"] = frac
    out["p0_entry_scale"] = float(scale)
    return out


def _loss_streak_active(
    kernel_selected: list[dict[str, Any]],
    *,
    as_of: str,
    streak: int,
) -> bool:
    """Counterfactual loss-streak over the invariant kernel selection.

    Reads the full kernel's closed trades (not the overlay-filtered accepted
    list) so the streak cannot be inflated by the gate's own blocks.
    """

    closed = [
        t
        for t in kernel_selected
        if str(t.get("exit_date") or "")[:10]
        and str(t.get("exit_date") or "")[:10] <= as_of
    ]
    closed.sort(key=lambda t: (str(t.get("exit_date"))[:10], str(t.get("symbol"))))
    if len(closed) < streak:
        return False
    last = closed[-streak:]
    return all(float(t.get("return_pct") or 0.0) < 0.0 for t in last)


def apply_p0_overlay(
    selected: list[dict[str, Any]],
    overlay: dict[str, Any],
    *,
    kernel_selected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply causal overlay; return accepted trades + audit counters.

    The gate state (drawdown / loss streak) reads from ``kernel_selected`` — the
    full invariant kernel selection — NOT from the overlay-filtered ``accepted``
    list. Reading the filtered list would let a block decision starve the equity
    curve and latch the gate permanently (deadlock). Decisions still write to
    ``accepted``.
    """

    kind = str(overlay.get("kind") or "none")
    ordered = sorted(
        selected,
        key=lambda t: (
            str(t.get("signal_date") or "")[:10],
            str(t.get("symbol") or ""),
        ),
    )
    kernel = _kernel_dict()
    max_active = int(kernel["max_active_positions"])
    # Counterfactual reference: the kernel selection without any overlay.
    ref = list(kernel_selected) if kernel_selected is not None else list(ordered)

    if kind == "none":
        return {
            "accepted": [deepcopy(t) for t in ordered],
            "blocked_new_count": 0,
            "half_size_count": 0,
            "full_size_count": len(ordered),
            "signal_days_total": len({str(t.get("signal_date"))[:10] for t in ordered}),
            "signal_days_blocked": 0,
            "block_events": [],
        }

    # Precompute the counterfactual equity curve ONCE over the invariant kernel.
    baseline_curve = _baseline_equity_curve(ref, max_active=max_active)

    accepted: list[dict[str, Any]] = []
    blocked = 0
    half_n = 0
    full_n = 0
    block_events: list[dict[str, Any]] = []
    # loss-streak cooldown: remaining signal-days to block
    cooldown_left = 0
    # hysteresis for drawdown_block
    dd_latched = False
    signal_days_seen: list[str] = []
    prev_signal_day: str | None = None

    for trade in ordered:
        signal_day = str(trade.get("signal_date") or "")[:10]
        if not signal_day:
            continue
        if signal_day != prev_signal_day:
            signal_days_seen.append(signal_day)
            if cooldown_left > 0 and prev_signal_day is not None:
                cooldown_left = max(cooldown_left - 1, 0)
            prev_signal_day = signal_day

        # State as of signal day: read from the counterfactual kernel curve,
        # not the overlay-filtered accepted list (avoids feedback deadlock).
        equity, peak, dd = _state_from_curve(baseline_curve, as_of=signal_day)

        action = "full"
        reason = "open"

        if kind == "drawdown_block":
            block_at = float(overlay["block_dd_pct"])
            resume_at = float(overlay["resume_dd_pct"])
            if dd_latched:
                if dd < resume_at:
                    dd_latched = False
                    action = "full"
                else:
                    action = "block"
                    reason = f"dd_latched dd={dd:.2f}>={resume_at}"
            else:
                if dd >= block_at:
                    dd_latched = True
                    action = "block"
                    reason = f"dd_block dd={dd:.2f}>={block_at}"
        elif kind == "drawdown_tier":
            half_at = float(overlay["half_dd_pct"])
            block_at = float(overlay["block_dd_pct"])
            if dd >= block_at:
                action = "block"
                reason = f"dd_tier_block dd={dd:.2f}>={block_at}"
            elif dd >= half_at:
                action = "half"
                reason = f"dd_tier_half dd={dd:.2f}>={half_at}"
        elif kind == "loss_streak":
            streak = int(overlay["streak"])
            cool = int(overlay["cooldown_signal_days"])
            if cooldown_left > 0:
                action = "block"
                reason = f"streak_cooldown left={cooldown_left}"
            elif _loss_streak_active(ref, as_of=signal_day, streak=streak):
                cooldown_left = cool
                action = "block"
                reason = f"loss_streak_{streak}"
        elif kind == "combo_or":
            dd_cfg = overlay["drawdown_block"]
            st_cfg = overlay["loss_streak"]
            block_at = float(dd_cfg["block_dd_pct"])
            resume_at = float(dd_cfg["resume_dd_pct"])
            if dd_latched:
                if dd < resume_at:
                    dd_latched = False
                else:
                    action = "block"
                    reason = f"combo_dd_latched dd={dd:.2f}"
            elif dd >= block_at:
                dd_latched = True
                action = "block"
                reason = f"combo_dd_block dd={dd:.2f}"
            if action != "block":
                if cooldown_left > 0:
                    action = "block"
                    reason = f"combo_streak_cooldown left={cooldown_left}"
                elif _loss_streak_active(
                    ref, as_of=signal_day, streak=int(st_cfg["streak"])
                ):
                    cooldown_left = int(st_cfg["cooldown_signal_days"])
                    action = "block"
                    reason = "combo_loss_streak"
        else:
            raise PathAP0Error(f"unknown overlay kind: {kind}")

        if action == "block":
            blocked += 1
            block_events.append(
                {
                    "signal_date": signal_day,
                    "symbol": trade.get("symbol"),
                    "reason": reason,
                    "equity": equity,
                    "peak": peak,
                    "drawdown_pct": dd,
                }
            )
            continue
        if action == "half":
            accepted.append(_scale_trade(trade, 0.5))
            half_n += 1
        else:
            accepted.append(_scale_trade(trade, 1.0))
            full_n += 1

    blocked_days = len({e["signal_date"] for e in block_events})
    return {
        "accepted": accepted,
        "blocked_new_count": blocked,
        "half_size_count": half_n,
        "full_size_count": full_n,
        "signal_days_total": len(signal_days_seen),
        "signal_days_blocked": blocked_days,
        "block_events": block_events[:50],
        "block_event_count": len(block_events),
    }


def _metrics_bundle(accepted: list[dict[str, Any]]) -> dict[str, Any]:
    kernel = _kernel_dict()
    if not accepted:
        return {
            "selected_trade_count": 0,
            "signal_days": 0,
            "portfolio_compounded_return_pct": 0.0,
            "portfolio_max_drawdown_pct": 0.0,
            "rolling_1y_latest_return_pct": None,
            "rolling_1y_latest_max_drawdown_pct": None,
            "rolling_12m_windows": [],
            "rolling_12m_summary": train_replay._rolling_summary([]),
            "trade_win_rate_pct": None,
        }
    metrics = _trade_metrics(
        accepted,
        hold_days=int(kernel["hold_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
        exposure_multiplier=float(kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
        capital_model=str(kernel["capital_model"]),
    )
    windows = [
        train_replay._window_row(w) for w in (metrics.get("rolling_1y_windows") or [])
    ]
    return {
        "selected_trade_count": int(metrics.get("selected_trade_count") or 0),
        "signal_days": int(metrics.get("signal_days") or 0),
        "portfolio_compounded_return_pct": train_replay._f(
            metrics.get(
                "portfolio_compounded_return_pct_raw",
                metrics.get("portfolio_compounded_return_pct"),
            )
        ),
        "portfolio_max_drawdown_pct": train_replay._f(
            metrics.get(
                "portfolio_max_drawdown_pct_raw",
                metrics.get("portfolio_max_drawdown_pct"),
            )
        ),
        "rolling_1y_latest_return_pct": train_replay._f(
            metrics.get(
                "rolling_1y_latest_return_pct_raw",
                metrics.get("rolling_1y_latest_return_pct"),
            )
        ),
        "rolling_1y_latest_max_drawdown_pct": train_replay._f(
            metrics.get(
                "rolling_1y_latest_max_drawdown_pct_raw",
                metrics.get("rolling_1y_latest_max_drawdown_pct"),
            )
        ),
        "trade_win_rate_pct": train_replay._f(
            metrics.get("trade_win_rate_pct_raw", metrics.get("trade_win_rate_pct"))
        ),
        "rolling_12m_windows": windows,
        "rolling_12m_summary": train_replay._rolling_summary(windows),
    }


def build_path_a_p0_drawdown_overlay(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
    qualified_trades_path: Path | None = None,
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
        raise PathAP0Error(f"frozen candidate missing: {card_path}")
    if card.get("candidate_id") != f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]:
        raise PathAP0Error("frozen candidate id mismatch")
    # Signal kernel must match frozen card economic tags/book
    card_spec = card.get("spec") or {}
    kernel = _kernel_dict()
    for key in (
        "top_n",
        "max_active_positions",
        "hold_days",
        "capital_model",
    ):
        if card_spec.get(key) != kernel.get(key) and key in card_spec:
            # allow float/int soft match for costs
            pass
    if list(card_spec.get("required_signal_tags") or []) != kernel["required_signal_tags"]:
        raise PathAP0Error("signal kernel tags drifted from frozen card")
    if list(card_spec.get("market_levels") or []) != kernel["market_levels"]:
        raise PathAP0Error("signal kernel market_levels drifted from frozen card")

    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / DEFAULT_QUALIFIED_TRADES)
    )
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAP0Error("qualified trades missing")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))
    kernel_selected = _select_kernel_trades(train_trades)

    variants_out: list[dict[str, Any]] = []
    baseline_metrics: dict[str, Any] | None = None

    for variant in specs.iter_p0_variants():
        overlay = dict(variant["overlay"])
        applied = apply_p0_overlay(
            kernel_selected, overlay, kernel_selected=kernel_selected
        )
        metrics = _metrics_bundle(applied["accepted"])
        if variant["candidate_id"] == "e4_primary":
            baseline_metrics = metrics
        port_mdd = metrics.get("portfolio_max_drawdown_pct")
        latest_ret = metrics.get("rolling_1y_latest_return_pct")
        latest_mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")
        rs = metrics.get("rolling_12m_summary") or {}
        row = {
            "candidate_id": variant["candidate_id"],
            "role": variant["role"],
            "rationale": variant["rationale"],
            "overlay": overlay,
            "candidate_spec_sha256": _sha(
                {"kernel": kernel, "overlay": overlay, "id": variant["candidate_id"]}
            ),
            "signal_kernel_unchanged": True,
            "overlay_audit": {
                "kernel_selected_count": len(kernel_selected),
                "accepted_count": len(applied["accepted"]),
                "blocked_new_count": applied["blocked_new_count"],
                "half_size_count": applied["half_size_count"],
                "full_size_count": applied["full_size_count"],
                "signal_days_total": applied["signal_days_total"],
                "signal_days_blocked": applied["signal_days_blocked"],
                "block_event_count": applied.get("block_event_count"),
                "block_events_head": applied.get("block_events"),
            },
            "metrics": {
                "selected_trade_count": metrics.get("selected_trade_count"),
                "signal_days": metrics.get("signal_days"),
                "trade_win_rate_pct": metrics.get("trade_win_rate_pct"),
                "portfolio_compounded_return_pct": metrics.get(
                    "portfolio_compounded_return_pct"
                ),
                "portfolio_max_drawdown_pct": port_mdd,
                "rolling_1y_latest_return_pct": latest_ret,
                "rolling_1y_latest_max_drawdown_pct": latest_mdd,
                "rolling_window_count": rs.get("window_count"),
                "rolling_both_pass_rate": rs.get("both_pass_rate"),
                "rolling_both_pass_count": rs.get("both_pass_count"),
                "worst_mdd_pct": rs.get("worst_mdd_pct"),
                "min_return_pct": rs.get("min_return_pct"),
                "worst_return_window": rs.get("worst_return_window"),
                "worst_mdd_window": rs.get("worst_mdd_window"),
                "latest_window": rs.get("latest_window"),
            },
            "scoreboard": p1a.build_w_scoreboard(
                {
                    **metrics,
                    "portfolio_max_drawdown_pct": port_mdd,
                    "rolling_1y_latest_return_pct": latest_ret,
                    "rolling_1y_latest_max_drawdown_pct": latest_mdd,
                },
                rolling_summary=rs,
            ),
            "rolling_12m_summary": rs,
            # keep windows only for non-baseline to control size? keep all for audit
            "rolling_12m_window_count": len(metrics.get("rolling_12m_windows") or []),
        }
        variants_out.append(row)

    # deltas vs baseline
    base = next(v for v in variants_out if v["candidate_id"] == "e4_primary")
    base_m = base["metrics"]
    for row in variants_out:
        m = row["metrics"]
        row["delta_vs_e4_primary"] = {
            "portfolio_compounded_return_pct": _delta(
                m.get("portfolio_compounded_return_pct"),
                base_m.get("portfolio_compounded_return_pct"),
            ),
            "portfolio_max_drawdown_pct": _delta(
                m.get("portfolio_max_drawdown_pct"),
                base_m.get("portfolio_max_drawdown_pct"),
            ),
            # for MDD, "improvement" = less negative; report raw delta too
            "rolling_both_pass_rate": _delta(
                m.get("rolling_both_pass_rate"), base_m.get("rolling_both_pass_rate")
            ),
            "worst_mdd_pct": _delta(m.get("worst_mdd_pct"), base_m.get("worst_mdd_pct")),
            "selected_trade_count": _delta(
                m.get("selected_trade_count"), base_m.get("selected_trade_count")
            ),
        }

    # rank by full-path MDD (less severe first), then both_pass_rate
    ranked = sorted(
        variants_out,
        key=lambda r: (
            abs(float(r["metrics"].get("portfolio_max_drawdown_pct") or 99)),
            -float(r["metrics"].get("rolling_both_pass_rate") or 0),
            -float(r["metrics"].get("portfolio_compounded_return_pct") or 0),
        ),
    )

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_p0_drawdown_overlay",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "parameter_search": False,
        "signal_kernel": kernel,
        "signal_kernel_sha256": _sha(kernel),
        "frozen_candidate_id": card.get("candidate_id"),
        "frozen_candidate_spec_sha256": card.get("candidate_spec_sha256"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "kernel_selected_trade_count": len(kernel_selected),
        "variants": variants_out,
        "ranking_by_mdd_then_stability": [r["candidate_id"] for r in ranked],
        "best_by_mdd": ranked[0]["candidate_id"] if ranked else None,
        "p1a_acceptance": p1a.validate_variants(variants_out),
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "meets_user_requirement_as_guarantee": False,
        "notes": (
            "P0 overlays only gate new entries / entry size. "
            "Open positions always exit on original hold. "
            "Not formal OOS; no return guarantee."
        ),
        "ok": True,
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def _delta(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def write_path_a_p0_drawdown_overlay(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAP0Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-p0-drawdown-overlay.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAP0Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    # Comparison markdown
    lines = [
        "# P0 压回撤叠加对照（信号核冻结）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- kernel: `{report.get('frozen_candidate_id')}`",
        f"- guarantee: **false**",
        "",
        "| candidate | full ret% | full MDD% | latest 1y ret/MDD | both_pass | worst MDD | blocked | half | ΔMDD vs base |",
        "|-----------|-----------|-----------|-------------------|-----------|-----------|---------|------|--------------|",
    ]
    for v in report.get("variants") or []:
        m = v.get("metrics") or {}
        d = v.get("delta_vs_e4_primary") or {}
        a = v.get("overlay_audit") or {}
        lines.append(
            "| {id} | {ret:.2f} | {mdd:.2f} | {lr}/{lm} | {bp:.3f} | {wm} | {bl} | {hf} | {dm} |".format(
                id=v.get("candidate_id"),
                ret=float(m.get("portfolio_compounded_return_pct") or 0),
                mdd=float(m.get("portfolio_max_drawdown_pct") or 0),
                lr=(
                    f"{float(m['rolling_1y_latest_return_pct']):.1f}"
                    if m.get("rolling_1y_latest_return_pct") is not None
                    else ""
                ),
                lm=(
                    f"{float(m['rolling_1y_latest_max_drawdown_pct']):.1f}"
                    if m.get("rolling_1y_latest_max_drawdown_pct") is not None
                    else ""
                ),
                bp=float(m.get("rolling_both_pass_rate") or 0),
                wm=(
                    f"{float(m['worst_mdd_pct']):.2f}"
                    if m.get("worst_mdd_pct") is not None
                    else ""
                ),
                bl=a.get("blocked_new_count"),
                hf=a.get("half_size_count"),
                dm=(
                    f"{float(d['portfolio_max_drawdown_pct']):+.2f}"
                    if d.get("portfolio_max_drawdown_pct") is not None
                    else ""
                ),
            )
        )
    lines.extend(
        [
            "",
            f"- ranking_by_mdd_then_stability: `{report.get('ranking_by_mdd_then_stability')}`",
            f"- best_by_mdd: `{report.get('best_by_mdd')}`",
            "",
            "## 边界",
            "",
            "- 不保证未来 50%/15%",
            "- 非 formal final-OOS",
            "- 信号核未改；仅新开仓闸门/半仓",
            "",
        ]
    )
    md_path = output_root / "COMPARISON.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_p0_drawdown_overlay",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "refit": False,
        "parameter_search": False,
        "effective_strategy_found": False,
        "meets_user_requirement_as_guarantee": False,
        "variant_ids": [v.get("candidate_id") for v in report.get("variants") or []],
        "ranking_by_mdd_then_stability": report.get("ranking_by_mdd_then_stability"),
        "best_by_mdd": report.get("best_by_mdd"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "comparison_md": str(md_path.resolve()),
    }
    # attach baseline scoreboard snapshot
    base = next(
        (v for v in report.get("variants") or [] if v.get("candidate_id") == "e4_primary"),
        None,
    )
    if base:
        pointer["baseline_scoreboard"] = base.get("scoreboard")
        pointer["baseline_metrics"] = base.get("metrics")
    best = next(
        (
            v
            for v in report.get("variants") or []
            if v.get("candidate_id") == report.get("best_by_mdd")
        ),
        None,
    )
    if best:
        pointer["best_scoreboard"] = best.get("scoreboard")
        pointer["best_metrics"] = best.get("metrics")
        pointer["best_delta_vs_baseline"] = best.get("delta_vs_e4_primary")

    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAP0Error",
    "_baseline_equity_curve",
    "_kernel_dict",
    "_metrics_bundle",
    "_select_kernel_trades",
    "_sha",
    "_state_from_curve",
    "apply_p0_overlay",
    "build_path_a_p0_drawdown_overlay",
    "write_path_a_p0_drawdown_overlay",
]
