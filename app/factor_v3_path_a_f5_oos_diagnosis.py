"""Path A F5: OOS market-level diagnosis (read-only, no rule change).

Answers "why does the frozen rule select 0 OOS trades?": projects the OOS
qualified trades' market_breadth onto the stock-breadth state machine, reports
per-day level + trigger reason, runs a counterfactual ({favorable,neutral,cautious})
and a favorable-threshold sensitivity (how many days miss by 1 of 4 conditions).

This is diagnosis only — it changes NO rule, NO threshold, NO signal kernel.
Shadow diagnostic != formal final-OOS.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p0_drawdown_overlay_specs as p0_specs
from app import factor_v3_train_window_freeze_contract as freeze
from app.research_context import _stock_breadth_market_context

STAGE_GOAL_ID = "path-a-f5-oos-diagnosis/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A F5：OOS market_level 诊断（只读）——逐日 market_context + 反事实放宽 + "
    "favorable 阈值敏感性；不改任何规则；shadow 诊断 ≠ formal final-OOS；不自动交易。"
)
REPORT_SCHEMA = "path-a-f5-oos-diagnosis-report/v1"
POINTER_SCHEMA = "path-a-f5-oos-diagnosis-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_OOS_QUALIFIED = Path("data/research_cache/path_a_oos/qualified_hold5_stop5_oos.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_f5_oos_diagnosis")

# Frozen rule market-level set (F0 candidate) — used for prefilter accounting.
FROZEN_MARKET_LEVELS = ("favorable", "neutral")
# Counterfactual: if we relaxed to include cautious (diagnosis only, not applied).
COUNTERFACTUAL_MARKET_LEVELS = ("favorable", "neutral", "cautious")

# Favorable threshold (research_context._stock_breadth_market_context) — mirrored
# here for sensitivity analysis. These are the CURRENT frozen thresholds; this
# module never changes them.
FAV_CONDITIONS = (
    ("above_ma20_pct", ">=", 65),
    ("above_ma60_pct", ">=", 60),
    ("return_20d_positive_pct", ">=", 60),
    ("median_return_20d_pct", ">=", 3),
)


class PathAF5Error(ValueError):
    """Raised when F5 diagnosis fails closed."""


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
        raise PathAF5Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _favorable_conditions_met(breadth: dict[str, Any]) -> dict[str, Any]:
    """Check each favorable condition against raw breadth values."""

    results = {}
    met_count = 0
    for field, op, threshold in FAV_CONDITIONS:
        val = breadth.get(field)
        passed = False
        if val is not None:
            try:
                passed = float(val) >= threshold
            except (TypeError, ValueError):
                passed = False
        if passed:
            met_count += 1
        results[field] = {"value": val, "threshold": threshold, "passed": passed}
    return {"conditions": results, "met_count": met_count, "total": len(FAV_CONDITIONS)}


def build_path_a_f5_oos_diagnosis(
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
        raise PathAF5Error(f"frozen candidate missing: {card_path}")

    qt_path = root / DEFAULT_OOS_QUALIFIED
    payload = train_replay._load_json(qt_path)
    if payload is None:
        raise PathAF5Error(f"OOS qualified trades missing: {qt_path}")
    oos_trades = list(payload.get("qualified_trades") or [])

    # Per signal-date: deduplicate breadth (same day shares breadth), recompute
    # level via the frozen state machine for audit, and tally.
    by_date: dict[str, dict[str, Any]] = {}
    for t in oos_trades:
        d = str(t.get("signal_date") or "")[:10]
        if not d:
            continue
        breadth = t.get("market_breadth") or {}
        mc = t.get("market_context") or {}
        if d not in by_date:
            recomputed = _stock_breadth_market_context(breadth) if breadth else {}
            by_date[d] = {
                "signal_date": d,
                "stored_level": mc.get("level") or t.get("market_level"),
                "stored_reasons": mc.get("reasons") or [],
                "recomputed_level": recomputed.get("level"),
                "recomputed_reasons": recomputed.get("reasons") or [],
                "breadth": breadth,
                "trade_symbols": [],
            }
        by_date[d]["trade_symbols"].append(t.get("symbol"))

    daily_rows = []
    level_dist_by_day: dict[str, int] = {}
    level_dist_by_trade: dict[str, int] = {}
    fav_sensitivity = []
    for d in sorted(by_date):
        row = by_date[d]
        breadth = row["breadth"]
        level = row["recomputed_level"] or row["stored_level"] or "unknown"
        level_dist_by_day[level] = level_dist_by_day.get(level, 0) + 1
        level_dist_by_trade[level] = level_dist_by_trade.get(level, 0) + len(
            row["trade_symbols"]
        )
        fav = _favorable_conditions_met(breadth) if breadth else {
            "conditions": {}, "met_count": 0, "total": len(FAV_CONDITIONS)
        }
        fav_sensitivity.append({
            "signal_date": d,
            "level": level,
            "favorable_met_count": fav["met_count"],
            "favorable_total": fav["total"],
            "miss_count": fav["total"] - fav["met_count"],
            "conditions": fav["conditions"],
        })
        daily_rows.append({
            "signal_date": d,
            "stored_level": row["stored_level"],
            "recomputed_level": row["recomputed_level"],
            "stored_reasons": row["stored_reasons"],
            "recomputed_reasons": row["recomputed_reasons"],
            "trade_count": len(row["trade_symbols"]),
            "breadth_excerpt": {
                k: breadth.get(k) for k in (
                    "above_ma20_pct", "above_ma60_pct", "advancing_pct",
                    "median_return_20d_pct", "median_return_60d_pct",
                    "return_20d_positive_pct", "eligible_count", "coverage_pct",
                ) if k in breadth
            },
            "favorable_check": fav,
        })

    # Frozen-rule prefilter: how many days / trades pass {favorable,neutral}?
    frozen_pass_days = sum(
        1 for r in daily_rows
        if (r["recomputed_level"] or r["stored_level"]) in FROZEN_MARKET_LEVELS
    )
    frozen_pass_trades = sum(
        r["trade_count"] for r in daily_rows
        if (r["recomputed_level"] or r["stored_level"]) in FROZEN_MARKET_LEVELS
    )
    # Counterfactual: include cautious
    cf_pass_days = sum(
        1 for r in daily_rows
        if (r["recomputed_level"] or r["stored_level"]) in COUNTERFACTUAL_MARKET_LEVELS
    )
    cf_pass_trades = sum(
        r["trade_count"] for r in daily_rows
        if (r["recomputed_level"] or r["stored_level"]) in COUNTERFACTUAL_MARKET_LEVELS
    )

    # Favorable "near-miss": days that met 3/4 conditions (missed by 1)
    near_miss = [s for s in fav_sensitivity if s["miss_count"] == 1]
    two_miss = [s for s in fav_sensitivity if s["miss_count"] == 2]

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_f5_oos_diagnosis",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "parameter_search": False,
        "frozen_candidate_id": card.get("candidate_id"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "oos_qualified_trade_count": len(oos_trades),
        "oos_signal_date_count": len(by_date),
        "market_level_distribution_by_day": level_dist_by_day,
        "market_level_distribution_by_trade": level_dist_by_trade,
        "frozen_rule_prefilter": {
            "allowed_levels": list(FROZEN_MARKET_LEVELS),
            "pass_days": frozen_pass_days,
            "pass_trades": frozen_pass_trades,
            "blocked_days": len(by_date) - frozen_pass_days,
            "blocked_trades": len(oos_trades) - frozen_pass_trades,
        },
        "counterfactual_include_cautious": {
            "allowed_levels": list(COUNTERFACTUAL_MARKET_LEVELS),
            "pass_days": cf_pass_days,
            "pass_trades": cf_pass_trades,
            "note": "diagnosis only; NOT applied — would require new catalog version",
        },
        "favorable_threshold_sensitivity": {
            "conditions": [
                {"field": f, "op": op, "threshold": t} for f, op, t in FAV_CONDITIONS
            ],
            "near_miss_count": len(near_miss),
            "near_miss_dates": [s["signal_date"] for s in near_miss],
            "two_miss_count": len(two_miss),
            "two_miss_dates": [s["signal_date"] for s in two_miss],
            "per_day": fav_sensitivity,
        },
        "daily_detail": daily_rows,
        "diagnosis_conclusion": _conclusion(
            level_dist_by_day, frozen_pass_trades, cf_pass_trades, len(near_miss)
        ),
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "meets_user_requirement_as_guarantee": False,
        "notes": (
            "Diagnosis only. No rule/threshold/signal-kernel change. "
            "Counterfactual is hypothetical accounting, NOT applied. "
            "Shadow diagnostic != formal final-OOS."
        ),
        "ok": True,
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def _conclusion(
    level_dist: dict[str, int],
    frozen_pass: int,
    cf_pass: int,
    near_miss: int,
) -> str:
    fav_neutral = level_dist.get("favorable", 0) + level_dist.get("neutral", 0)
    cautious = level_dist.get("cautious", 0)
    defensive = level_dist.get("defensive", 0)
    parts = [
        f"OOS 期 {sum(level_dist.values())} 个信号日："
        f"favorable/neutral={fav_neutral}，cautious={cautious}，defensive={defensive}。",
        f"冻结规则({{favorable,neutral}}) 命中 {frozen_pass} 笔交易。",
    ]
    if frozen_pass == 0:
        parts.append("冻结规则在 OOS 期选中 0 笔——这是 market_level 准入导致，非 bug。")
    if cf_pass > frozen_pass:
        parts.append(
            f"反事实（加 cautious）会有 {cf_pass} 笔通过——但这只是诊断，不放宽准入。"
        )
    if near_miss > 0:
        parts.append(
            f"favorable 近 miss（差 1 条）{near_miss} 天——阈值苛刻度量化参考。"
        )
    return " ".join(parts)


def write_path_a_f5_oos_diagnosis(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAF5Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-f5-oos-diagnosis.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAF5Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    dist = report.get("market_level_distribution_by_day") or {}
    pre = report.get("frozen_rule_prefilter") or {}
    cf = report.get("counterfactual_include_cautious") or {}
    sens = report.get("favorable_threshold_sensitivity") or {}
    lines = [
        "# F5 OOS market_level 诊断（只读，不改规则）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- OOS 信号日: {report.get('oos_signal_date_count')} · QT 笔数: {report.get('oos_qualified_trade_count')}",
        f"- guarantee: **false** · shadow 诊断 ≠ formal final-OOS",
        "",
        "## market_level 分布（按信号日）",
        "",
        f"- favorable: {dist.get('favorable', 0)}",
        f"- neutral: {dist.get('neutral', 0)}",
        f"- cautious: {dist.get('cautious', 0)}",
        f"- defensive: {dist.get('defensive', 0)}",
        "",
        "## 冻结规则 prefilter（{favorable,neutral}）",
        "",
        f"- 命中信号日: {pre.get('pass_days')} · 命中交易: {pre.get('pass_trades')}",
        f"- 丢弃信号日: {pre.get('blocked_days')} · 丢弃交易: {pre.get('blocked_trades')}",
        "",
        "## 反事实（加 cautious，诊断用，不执行）",
        "",
        f"- 命中信号日: {cf.get('pass_days')} · 命中交易: {cf.get('pass_trades')}",
        "",
        "## favorable 阈值敏感性（4 条全 AND）",
        "",
        f"- 近 miss（差 1 条）: {sens.get('near_miss_count')} 天 {sens.get('near_miss_dates')}",
        f"- 差 2 条: {sens.get('two_miss_count')} 天 {sens.get('two_miss_dates')}",
        "",
        "## 结论",
        "",
        report.get("diagnosis_conclusion"),
        "",
        "## 边界",
        "",
        "- 本报告不改任何规则/阈值/信号核",
        "- 反事实放宽只是计数，**不**执行",
        "- 非 formal final-OOS；不自动交易",
        "",
    ]
    md_path = output_root / "DIAGNOSIS.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_f5_oos_diagnosis",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "refit": False,
        "parameter_search": False,
        "effective_strategy_found": False,
        "meets_user_requirement_as_guarantee": False,
        "oos_signal_date_count": report.get("oos_signal_date_count"),
        "oos_qualified_trade_count": report.get("oos_qualified_trade_count"),
        "market_level_distribution_by_day": report.get("market_level_distribution_by_day"),
        "frozen_rule_prefilter": report.get("frozen_rule_prefilter"),
        "counterfactual_include_cautious": report.get("counterfactual_include_cautious"),
        "diagnosis_conclusion": report.get("diagnosis_conclusion"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "diagnosis_md": str(md_path.resolve()),
    }
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
    "PathAF5Error",
    "build_path_a_f5_oos_diagnosis",
    "write_path_a_f5_oos_diagnosis",
]
