"""Path A P2: small variant comparison (catalog §4, at most 4 variants).

No grid search. Two declared changes only:
- p2_fav_only: market_levels = [favorable] (drop neutral)
- p2_vol_target_10: entry size = clip(sigma_target/sigma_realized, 0.25, 1.0),
  sigma_realized from the past L realized kernel equity daily returns (L=20).
- p2_fav_only_plus_p0_best: fav_only + P0 winning overlay.

Signal tags / top_n / hold_days are NOT changed. Reuses the P0 metrics bundle
and P1-A scoreboard.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p0_drawdown_overlay_specs as p0_specs
from app import factor_v3_path_a_p1_acceptance_protocol as p1a
from app import factor_v3_path_a_p2_small_variants_specs as specs
from app import factor_v3_train_window_freeze_contract as freeze
from app.research_equity import _equity_points_from_slot_daily_returns

STAGE_GOAL_ID = "path-a-p2-small-variants/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A P2：≤4 个事前声明的小变体（去 neutral / 波动目标仓位）；"
    "不扫大网格；不改信号标签核；强制 P1-A 看板；永不自动交易；不保证 50/15。"
)
REPORT_SCHEMA = "path-a-p2-small-variants-report/v1"
POINTER_SCHEMA = "path-a-p2-small-variants-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_p2_small_variants")


class PathAP2Error(ValueError):
    """Raised when P2 stage fails closed."""


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
        raise PathAP2Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _vol_target_scale_series(
    baseline_curve: list[dict[str, Any]],
    *,
    lookback_days: int,
    annual_target_pct: float,
    min_scale: float,
    max_scale: float,
) -> dict[str, float]:
    """Per-entry-date vol-target scale using only PAST realized daily returns.

    sigma_realized = std of the previous ``lookback_days`` daily equity returns
    (return_pct on each point, converted to fraction). sigma_target is the
    daily-equivalent of annual_target_pct (annual / sqrt(252)). The scale for
    an entry on date D uses only returns with event_date < D (strictly past).
    Returns {entry_date: scale}.
    """

    if not baseline_curve:
        return {}
    # daily return fractions in chronological order
    ordered = sorted(
        baseline_curve, key=lambda p: str(p.get("event_date") or "")[:10]
    )
    dates = [str(p.get("event_date") or "")[:10] for p in ordered]
    rets = [
        float(p.get("return_pct") or 0.0) / 100.0 for p in ordered
    ]
    sigma_target_daily = float(annual_target_pct) / 100.0 / math.sqrt(252.0)
    scale_by_date: dict[str, float] = {}
    for i, d in enumerate(dates):
        past = rets[:i]  # strictly before current date
        if len(past) < lookback_days:
            scale = min_scale  # not enough history => most conservative
        else:
            window = past[-lookback_days:]
            mean = sum(window) / len(window)
            var = sum((v - mean) ** 2 for v in window) / len(window)
            sigma = math.sqrt(var) if var > 0 else 0.0
            scale = (
                sigma_target_daily / sigma if sigma > 0 else max_scale
            )
        scale = max(min(float(scale), max_scale), min_scale)
        scale_by_date[d] = scale
    return scale_by_date


def _apply_vol_target(
    selected: list[dict[str, Any]],
    scale_by_date: dict[str, float],
) -> list[dict[str, Any]]:
    """Scale each trade's entry size by the vol-target factor at its entry date.

    Entry size is realized via position_budget_fraction (slot-daily), mirroring
    P0's _scale_trade but with a per-trade factor. The factor is fixed at entry
    time and not recomputed during the hold (catalog §4.3 rule).
    """

    kernel = p0._kernel_dict()
    slot_count = max(int(kernel["max_active_positions"] or 1), 1)
    slot_exposure = float(kernel["exposure_multiplier"]) / slot_count
    out: list[dict[str, Any]] = []
    for trade in selected:
        entry_day = str(trade.get("entry_date") or trade.get("signal_date") or "")[:10]
        scale = scale_by_date.get(entry_day)
        if scale is None:
            # fall back to the nearest past date with a scale (entry may land on
            # a non-curve date; pick the most recent earlier date)
            prior = [d for d in scale_by_date if d <= entry_day]
            scale = scale_by_date[prior[-1]] if prior else specs.VOL_TARGET_MIN_SCALE
        frac = max(min(slot_exposure * float(scale), slot_exposure), 1e-9)
        scaled = deepcopy(trade)
        scaled["position_budget_fraction"] = frac
        scaled["p2_vol_target_scale"] = float(scale)
        out.append(scaled)
    return out


def _kernel_with_levels(levels: list[str]) -> dict[str, Any]:
    kernel = p0._kernel_dict()
    kernel["market_levels"] = list(levels)
    return kernel


def build_path_a_p2_small_variants(
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
        raise PathAP2Error(f"frozen candidate missing: {card_path}")
    if card.get("candidate_id") != f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]:
        raise PathAP2Error("frozen candidate id mismatch")

    qt_path = root / DEFAULT_QUALIFIED_TRADES
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAP2Error("qualified trades missing")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))

    base_kernel = p0._kernel_dict()
    # Baseline (e4_primary) kernel selection — shared reference curve for vol_target.
    base_selected = p0._select_kernel_trades(train_trades)
    baseline_curve = _equity_points_from_slot_daily_returns(
        base_selected,
        max_active_positions=int(base_kernel["max_active_positions"]),
        exposure_multiplier=float(base_kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(base_kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(base_kernel["roundtrip_cost_bps"]),
        slippage_bps=float(base_kernel["slippage_bps"]),
    )

    variants_out: list[dict[str, Any]] = []
    for variant in specs.iter_p2_variants():
        change_raw = variant.get("change")
        if isinstance(change_raw, dict):
            change = change_raw
            kind = str(change.get("kind") or "none")
        else:
            change = {"kind": "none"}
            kind = str(change_raw or "none")
        applied_kernel = base_kernel
        applied_overlay = {"kind": "none"}
        note = ""

        if kind == "none":
            selected = list(base_selected)
        elif kind == "market_levels":
            applied_kernel = _kernel_with_levels(list(change["market_levels"]))
            selected = p0._select_kernel_trades(
                train_trades, kernel_override=applied_kernel
            )
        elif kind == "vol_target":
            selected = list(base_selected)
            scale_by_date = _vol_target_scale_series(
                baseline_curve,
                lookback_days=int(change["lookback_days"]),
                annual_target_pct=float(change["annual_target_pct"]),
                min_scale=float(change["min_scale"]),
                max_scale=float(change["max_scale"]),
            )
            selected = _apply_vol_target(selected, scale_by_date)
            note = (
                f"vol_target applied: scales "
                f"[min={min(scale_by_date.values()):.3f}, "
                f"max={max(scale_by_date.values()):.3f}] "
                f"over {len(scale_by_date)} entry dates"
                if scale_by_date
                else "vol_target: no scale points"
            )
        elif kind == "market_levels_plus_overlay":
            applied_kernel = _kernel_with_levels(list(change["market_levels"]))
            fav_selected = p0._select_kernel_trades(
                train_trades, kernel_override=applied_kernel
            )
            applied_overlay = deepcopy(change["overlay"])
            applied = p0.apply_p0_overlay(
                fav_selected,
                applied_overlay,
                kernel_selected=fav_selected,
            )
            selected = applied["accepted"]
            note = (
                f"fav_only + overlay({applied_overlay['kind']}): "
                f"accepted={len(selected)}/{len(fav_selected)} "
                f"blocked={applied['blocked_new_count']}"
            )
        else:
            raise PathAP2Error(f"unknown change kind: {kind}")

        metrics = p0._metrics_bundle(selected)
        rs = metrics.get("rolling_12m_summary") or {}
        # promote rolling summary fields to top-level metrics (align with P0
        # report structure) so ranking/delta/print consume them uniformly.
        metrics = {
            **metrics,
            "rolling_both_pass_rate": rs.get("both_pass_rate"),
            "rolling_both_pass_count": rs.get("both_pass_count"),
            "rolling_window_count": rs.get("window_count"),
            "worst_mdd_pct": rs.get("worst_mdd_pct"),
            "min_return_pct": rs.get("min_return_pct"),
            "worst_return_window": rs.get("worst_return_window"),
            "worst_mdd_window": rs.get("worst_mdd_window"),
            "latest_window": rs.get("latest_window"),
        }
        port_mdd = metrics.get("portfolio_max_drawdown_pct")
        latest_ret = metrics.get("rolling_1y_latest_return_pct")
        latest_mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")

        row = {
            "candidate_id": variant["candidate_id"],
            "role": variant["role"],
            "rationale": variant["rationale"],
            "change": change,
            "candidate_spec_sha256": _sha(
                {"kernel": applied_kernel, "change": change, "id": variant["candidate_id"]}
            ),
            "applied_kernel": applied_kernel,
            "applied_overlay": applied_overlay,
            "note": note,
            "selected_trade_count": len(selected),
            "metrics": metrics,
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
            "rolling_both_pass_rate": _delta(
                m.get("rolling_both_pass_rate"), base_m.get("rolling_both_pass_rate")
            ),
            "worst_mdd_pct": _delta(m.get("worst_mdd_pct"), base_m.get("worst_mdd_pct")),
            "selected_trade_count": _delta(
                m.get("selected_trade_count"), base_m.get("selected_trade_count")
            ),
        }

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
        "path": "A_no_tcb_p2_small_variants",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "parameter_search": False,
        "signal_kernel": base_kernel,
        "signal_kernel_sha256": p0._sha(base_kernel),
        "frozen_candidate_id": card.get("candidate_id"),
        "frozen_candidate_spec_sha256": card.get("candidate_spec_sha256"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "kernel_selected_trade_count": len(base_selected),
        "variants": variants_out,
        "ranking_by_mdd_then_stability": [r["candidate_id"] for r in ranked],
        "best_by_mdd": ranked[0]["candidate_id"] if ranked else None,
        "p1a_acceptance": p1a.validate_variants(variants_out),
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "meets_user_requirement_as_guarantee": False,
        "notes": (
            "P2 only changes market-level set or entry-size formula. "
            "Signal tags/top_n/hold_days unchanged. At most 4 variants (no grid). "
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


def write_path_a_p2_small_variants(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAP2Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-p2-small-variants.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAP2Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    lines = [
        "# P2 小版本对照（信号标签核冻结）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- kernel: `{report.get('frozen_candidate_id')}`",
        f"- guarantee: **false**",
        "",
        "| candidate | full ret% | full MDD% | latest 1y ret/MDD | both_pass | worst MDD | ΔMDD vs base |",
        "|-----------|-----------|-----------|-------------------|-----------|-----------|--------------|",
    ]
    for v in report.get("variants") or []:
        m = v.get("metrics") or {}
        d = v.get("delta_vs_e4_primary") or {}
        lines.append(
            "| {id} | {ret:.2f} | {mdd:.2f} | {lr}/{lm} | {bp:.3f} | {wm} | {dm} |".format(
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
            "- 信号标签核未改；仅市况集合 / 开仓暴露公式变化",
            "- 至多 4 变体（禁止扩网格）",
            "",
        ]
    )
    md_path = output_root / "COMPARISON.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_p2_small_variants",
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
    "PathAP2Error",
    "build_path_a_p2_small_variants",
    "write_path_a_p2_small_variants",
]
