"""Diagnostic alt split for frozen sig_pull_negext_h5.

User asked to try a calendar cut at 2025-01-01 instead of the locked
2025-07-01 holdout. The spec is not refit.

This window overlaps the locked train (2025-01-01..2025-06-30) and the
locked holdout (2025-07-01..2026-07-03). It is not independent OOS and
not an effective strategy.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_protocol_baseline as baseline
from app import factor_v3_path_a_protocol_reclaim_freeze as freeze
from app import factor_v3_path_a_protocol_reclaim_oos as oos
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal import DEFAULT_HOLD_QT_PATH
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    FROZEN_RECLAIM_VARIANT,
    SIGNAL_HOLD_FAMILY,
)
from app.jiaoch_live_market import JIAOCH_DAILY_CACHE_SOURCE_VERSION
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-reclaim-alt-split/v1"
REPORT_SCHEMA = "path-a-protocol-reclaim-alt-split-report/v1"
ALT_SPLIT_ID = "calendar-cut-2025-01-01/v1"
ALT_TRAIN_START = proto.TRAIN_START
ALT_EVAL_START = "2025-01-01"
DEFAULT_EVAL_END = "2026-08-14"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_reclaim_alt_split")


class PathAReclaimAltSplitError(ValueError):
    """Raised when the diagnostic alt split cannot be scored."""


def partition_for_alt_signal_date(
    signal_date: str,
    *,
    eval_end: str = DEFAULT_EVAL_END,
) -> str | None:
    day = str(signal_date or "")[:10]
    if not day:
        return None
    if ALT_TRAIN_START <= day < ALT_EVAL_START:
        return "train"
    if ALT_EVAL_START <= day <= eval_end:
        return "eval"
    return None


def filter_alt_trades(
    trades: list[dict[str, Any]],
    partition: str,
    *,
    eval_end: str = DEFAULT_EVAL_END,
) -> list[dict[str, Any]]:
    wanted = str(partition)
    return [
        trade
        for trade in trades
        if partition_for_alt_signal_date(
            str(trade.get("signal_date") or ""), eval_end=eval_end
        )
        == wanted
    ]


def _load_trades(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = train_replay._load_json(path)
    if payload is None:
        raise PathAReclaimAltSplitError(f"qualified trades missing: {path}")
    meta = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != JIAOCH_DAILY_CACHE_SOURCE_VERSION:
        raise PathAReclaimAltSplitError(f"QT is not Jiaoch stk_mins v2: {path}")
    if meta.get("slice") == "traded":
        raise PathAReclaimAltSplitError("refuses the contaminated 191-name slice")
    return list(payload.get("qualified_trades") or []), meta


def load_alt_split_trades(
    *,
    repo_root: Path,
    hold_qt_path: Path | None = None,
    oos_qt_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hold_path = (
        Path(hold_qt_path).resolve()
        if hold_qt_path is not None
        else (repo_root / DEFAULT_HOLD_QT_PATH)
    )
    trades, hold_meta = _load_trades(hold_path)
    family = hold_meta.get("signal_family")
    if family != SIGNAL_HOLD_FAMILY:
        raise PathAReclaimAltSplitError("hold QT is not the protocol signal-hold book")
    oos_path = (
        Path(oos_qt_path).resolve()
        if oos_qt_path is not None
        else (repo_root / oos.DEFAULT_QT_PATH)
    )
    extra_meta: dict[str, Any] = {}
    if oos_path.is_file():
        extra, extra_meta = _load_trades(oos_path)
        if extra_meta.get("signal_family") not in {
            oos.SIGNAL_FAMILY,
            SIGNAL_HOLD_FAMILY,
        }:
            raise PathAReclaimAltSplitError("OOS QT family is not reclaim")
        trades.extend(extra)
    return trades, {
        "hold_qt_path": str(hold_path),
        "oos_qt_path": str(oos_path) if oos_path.is_file() else None,
        "hold_name_count": hold_meta.get("name_count"),
        "oos_name_count": extra_meta.get("name_count"),
        "raw_trade_count": len(trades),
    }


def overlap_note() -> dict[str, Any]:
    return {
        "locked_train": f"{proto.TRAIN_START}..{proto.HOLDOUT_START} exclusive",
        "locked_holdout": f"{proto.HOLDOUT_START}..{proto.HOLDOUT_END}",
        "alt_train": f"{ALT_TRAIN_START}..{ALT_EVAL_START} exclusive",
        "alt_eval_overlaps_locked_train": f"{ALT_EVAL_START}..2025-06-30",
        "alt_eval_overlaps_locked_holdout": f"{proto.HOLDOUT_START}..{proto.HOLDOUT_END}",
        "independent_oos": False,
        "reason": (
            "eval includes locked-train H1 2025 and the holdout used to pick "
            "sig_pull_negext_h5"
        ),
    }


def build_path_a_protocol_reclaim_alt_split(
    *,
    repo_root: Path | None = None,
    hold_qt_path: Path | None = None,
    oos_qt_path: Path | None = None,
    eval_end: str = DEFAULT_EVAL_END,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAReclaimAltSplitError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    if eval_end < ALT_EVAL_START:
        raise PathAReclaimAltSplitError(
            f"eval_end {eval_end} is before alt eval start {ALT_EVAL_START}"
        )
    root = (repo_root or Path.cwd()).resolve()
    trades, loaded = load_alt_split_trades(
        repo_root=root,
        hold_qt_path=hold_qt_path,
        oos_qt_path=oos_qt_path,
    )
    train_trades = filter_alt_trades(trades, "train", eval_end=eval_end)
    eval_trades = filter_alt_trades(trades, "eval", eval_end=eval_end)
    scored = baseline.score_protocol_baseline(
        train_trades,
        eval_trades,
        variants=(FROZEN_RECLAIM_VARIANT,),
        stage_goal_id=STAGE_GOAL_ID,
    )
    row = (scored.get("variants") or [None])[0]
    if row is None:
        raise PathAReclaimAltSplitError("frozen variant produced no score row")
    spec = freeze.frozen_reclaim_spec()
    card = freeze.build_frozen_reclaim_card()
    eval_score = row["holdout"]
    train_score = row["train"]
    evaluable = oos.twelve_month_window_evaluable(ALT_EVAL_START, eval_end)
    latest = eval_score.get("latest_1y_return_pct")
    mdd = eval_score.get("full_path_mdd_pct")
    dual = False
    if evaluable and latest is not None and mdd is not None:
        dual = goal.meets_primary_performance_targets(
            rolling_12m_net_return_pct=float(latest),
            max_drawdown_pct=float(mdd),
        )
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "alt_split_id": ALT_SPLIT_ID,
        "candidate_id": FROZEN_RECLAIM_CANDIDATE_ID,
        "candidate_spec_sha256": card["candidate_spec_sha256"],
        "spec": spec,
        "locked_protocol": proto.protocol_descriptor(),
        "alt_window": {
            "train_start": ALT_TRAIN_START,
            "eval_start": ALT_EVAL_START,
            "eval_end": eval_end,
        },
        "overlap": overlap_note(),
        "loaded": loaded,
        "train_trade_count": len(train_trades),
        "eval_trade_count": len(eval_trades),
        "train": train_score,
        "eval": eval_score,
        "eval_twelve_month_evaluable": evaluable,
        "eval_dual_pass_26_15": dual,
        "independent_oos": False,
        "formal_final_oos": False,
        "effective_strategy": False,
        "promotable": False,
        "development_only": True,
        "zero_refit": True,
        "parameter_search": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def format_reclaim_alt_split_table(report: dict[str, Any]) -> str:
    train = report.get("train") or {}
    ev = report.get("eval") or {}
    window = report.get("alt_window") or {}
    return "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"split={report.get('alt_split_id')}",
            f"candidate={report.get('candidate_id')}",
            f"spec_sha256={report.get('candidate_spec_sha256')}",
            f"train={window.get('train_start')}..{window.get('eval_start')}",
            f"eval={window.get('eval_start')}..{window.get('eval_end')}",
            f"independent_oos={report.get('independent_oos')}",
            f"eval_twelve_month_evaluable="
            f"{report.get('eval_twelve_month_evaluable')}",
            f"eval_dual_pass_26_15={report.get('eval_dual_pass_26_15')}",
            f"effective_strategy={report.get('effective_strategy')}",
            f"train_1y={train.get('latest_1y_return_pct')} "
            f"train_mdd={train.get('full_path_mdd_pct')} "
            f"train_n={train.get('selected_trade_count')}",
            f"eval_1y={ev.get('latest_1y_return_pct')} "
            f"eval_mdd={ev.get('full_path_mdd_pct')} "
            f"eval_n={ev.get('selected_trade_count')}",
            "",
        ]
    )


def write_path_a_protocol_reclaim_alt_split(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_reclaim_alt_split_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "effective_strategy": False,
        "independent_oos": False,
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
    }
