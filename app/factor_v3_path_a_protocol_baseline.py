"""Score pre-registered kernels on the locked train/holdout split."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import merged_kernel
from app.factor_v3_path_a_protocol_baseline_specs import (
    STAGE_GOAL_ID,
    apply_rank_key,
    assert_variants_obey_protocol,
    iter_protocol_alt_variants,
    iter_protocol_baseline_variants,
    iter_protocol_factor_variants,
)
from app.factor_v3_path_a_protocol_signal import DEFAULT_QT_PATH as SIGNAL_QT
from app.factor_v3_path_a_protocol_signal_specs import (
    SIGNAL_FAMILY,
    iter_protocol_signal_variants,
)
from app.storage import write_json

DEFAULT_QT = Path("data/research_cache/qualified_hold5_stop5_3y_jiaoch_holdout_xl.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_baseline")
REPORT_SCHEMA = "path-a-protocol-baseline-report/v1"


class PathAProtocolBaselineError(ValueError):
    """Raised when the locked-split baseline cannot be scored."""


def _score_partition(
    trades: list[dict[str, Any]],
    variant: dict[str, Any],
) -> dict[str, Any]:
    kernel = merged_kernel(variant)
    ranked = apply_rank_key(trades, variant.get("rank_key"))
    if not ranked:
        metrics = p0._metrics_bundle([])
        empty = True
    else:
        try:
            selected = p0._select_kernel_trades(ranked, kernel_override=kernel)
            metrics = p0._metrics_bundle(selected)
            empty = False
        except p0.PathAP0Error:
            metrics = p0._metrics_bundle([])
            empty = True
    full_ret = metrics.get("portfolio_compounded_return_pct")
    full_mdd = metrics.get("portfolio_max_drawdown_pct")
    latest_ret = metrics.get("rolling_1y_latest_return_pct")
    latest_mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")
    summary = metrics.get("rolling_12m_summary") or {}
    dual = False if empty else proto.meets_locked_split_targets(
        latest_12m_net_return_pct=latest_ret,
        partition_max_drawdown_pct=full_mdd,
    )
    return {
        "empty_selection": empty,
        "selected_trade_count": metrics.get("selected_trade_count"),
        "full_path_return_pct": full_ret,
        "full_path_mdd_pct": full_mdd,
        "latest_1y_return_pct": latest_ret,
        "latest_1y_mdd_pct": latest_mdd,
        "rolling_both_pass_rate": summary.get("both_pass_rate"),
        "rolling_window_count": summary.get("window_count"),
        "dual_pass_50_15": dual,
        "dual_pass_via": "locked_split latest_12m + partition_mdd",
        "full_path_return_is_not_annualized": True,
    }


def score_protocol_baseline(
    train_trades: list[dict[str, Any]],
    holdout_trades: list[dict[str, Any]],
    *,
    variants: tuple[dict[str, Any], ...] | None = None,
    stage_goal_id: str = STAGE_GOAL_ID,
) -> dict[str, Any]:
    chosen = variants or iter_protocol_baseline_variants()
    assert_variants_obey_protocol(chosen)
    ids = [str(row["candidate_id"]) for row in chosen]
    overlap = set(ids) & proto.CONTAMINATED_CANDIDATE_IDS
    if overlap:
        raise PathAProtocolBaselineError(
            f"baseline reuses contaminated ids: {sorted(overlap)}"
        )
    rows: list[dict[str, Any]] = []
    for variant in chosen:
        train = _score_partition(train_trades, variant)
        holdout = _score_partition(holdout_trades, variant)
        rows.append(
            {
                "candidate_id": variant["candidate_id"],
                "role": variant["role"],
                "rationale": variant["rationale"],
                "rank_key": variant.get("rank_key") or "rank_score",
                "kernel": variant["kernel"],
                "development_only": True,
                "promotable": False,
                "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
                "train": train,
                "holdout": holdout,
                "holdout_dual_pass_50_15": holdout["dual_pass_50_15"],
            }
        )
    passed = [row for row in rows if row["holdout_dual_pass_50_15"]]
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": stage_goal_id,
        "protocol": proto.protocol_descriptor(),
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "variant_count": len(rows),
        "holdout_dual_pass_ids": [row["candidate_id"] for row in passed],
        "current_development_candidate": (
            passed[0]["candidate_id"] if passed else None
        ),
        "fifty_fifteen_holdout_met": bool(passed),
        "rejected_slice_candidate_id": proto.REJECTED_SLICE_CANDIDATE_ID,
        "rejected_slice_status": proto.REJECTED_SLICE_STATUS,
        "variants": rows,
    }


def build_path_a_protocol_baseline(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
    family: str = "baseline",
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAProtocolBaselineError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    chosen = str(family or "baseline").strip().casefold()
    default_qt = SIGNAL_QT if chosen == "signal" else DEFAULT_QT
    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / default_qt)
    )
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAProtocolBaselineError(f"qualified trades missing: {qt_path}")
    meta = (qt.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != "jiaoch-daily-bars/shares-cny/v2":
        raise PathAProtocolBaselineError("QT is not Jiaoch stk_mins v2")
    if meta.get("slice") == "traded":
        raise PathAProtocolBaselineError(
            "refuses the contaminated 191-name traded slice"
        )
    if chosen == "signal" and meta.get("signal_family") != SIGNAL_FAMILY:
        raise PathAProtocolBaselineError("QT is not the protocol signal book")
    all_trades = train_replay._filter_train_trades(
        list(qt.get("qualified_trades") or [])
    )
    train_trades = proto.filter_trades_for_partition(all_trades, "train")
    holdout_trades = proto.filter_trades_for_partition(all_trades, "holdout")
    if chosen == "factor":
        report = score_protocol_baseline(
            train_trades,
            holdout_trades,
            variants=iter_protocol_factor_variants(),
            stage_goal_id="path-a-protocol-factor/v1",
        )
    elif chosen == "alt":
        report = score_protocol_baseline(
            train_trades,
            holdout_trades,
            variants=iter_protocol_alt_variants(),
            stage_goal_id="path-a-protocol-alt/v1",
        )
    elif chosen == "signal":
        report = score_protocol_baseline(
            train_trades,
            holdout_trades,
            variants=iter_protocol_signal_variants(),
            stage_goal_id="path-a-protocol-signal/v1",
        )
    elif chosen == "baseline":
        report = score_protocol_baseline(train_trades, holdout_trades)
    else:
        raise PathAProtocolBaselineError(f"unknown family: {family!r}")
    report["qualified_trades_path"] = str(qt_path)
    report["source_version"] = meta.get("source_version")
    report["slice"] = meta.get("slice")
    report["train_trade_count"] = len(train_trades)
    report["holdout_trade_count"] = len(holdout_trades)
    return report


def format_protocol_baseline_table(report: dict[str, Any]) -> str:
    lines = [
        f"stage={report.get('stage_goal_id')}",
        f"protocol={(report.get('protocol') or {}).get('protocol_id')}",
        f"slice={report.get('slice')}",
        f"source={report.get('source_version')}",
        f"train_trades={report.get('train_trade_count')}",
        f"holdout_trades={report.get('holdout_trade_count')}",
        f"fifty_fifteen_holdout_met={report.get('fifty_fifteen_holdout_met')}",
        f"holdout_dual_pass_ids={report.get('holdout_dual_pass_ids')}",
        f"rejected={report.get('rejected_slice_candidate_id')} "
        f"{report.get('rejected_slice_status')}",
        "id\ttrain_1y\ttrain_mdd\ttrain_dual\tholdout_1y\tholdout_mdd\tholdout_dual\tselected_h",
    ]
    for row in report.get("variants") or []:
        train = row["train"]
        holdout = row["holdout"]
        lines.append(
            "\t".join(
                [
                    str(row["candidate_id"]),
                    f"{train['latest_1y_return_pct']}",
                    f"{train['full_path_mdd_pct']}",
                    str(train["dual_pass_50_15"]).lower(),
                    f"{holdout['latest_1y_return_pct']}",
                    f"{holdout['full_path_mdd_pct']}",
                    str(holdout["dual_pass_50_15"]).lower(),
                    str(holdout["selected_trade_count"]),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def write_path_a_protocol_baseline(
    report: dict[str, Any], *, output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_protocol_baseline_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "fifty_fifteen_holdout_met": report.get("fifty_fifteen_holdout_met"),
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }
