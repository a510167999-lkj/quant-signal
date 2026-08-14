"""Freeze sig_pull_negext_h5 as the accepted development hypothesis.

User accepted this rule. Holdout is about +26.5% / -9.5%. The live contract
is 26/15, so the number dual-passes this replay. It is still not an effective
strategy: holdout was already opened during search. Independent OOS remains.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_protocol_baseline as baseline
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal import DEFAULT_HOLD_QT_PATH
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    FROZEN_RECLAIM_VARIANT,
    SIGNAL_HOLD_FAMILY,
)
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-reclaim-freeze/v1"
CARD_SCHEMA = "path-a-protocol-reclaim-frozen-candidate/v1"
REPORT_SCHEMA = "path-a-protocol-reclaim-freeze-report/v1"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_reclaim_freeze")


class PathAReclaimFreezeError(ValueError):
    """Raised when the accepted reclaim hypothesis cannot be frozen."""


def _sha(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def frozen_reclaim_spec() -> dict[str, Any]:
    variant = dict(FROZEN_RECLAIM_VARIANT)
    kernel = dict(variant["kernel"])
    return {
        "candidate_id": FROZEN_RECLAIM_CANDIDATE_ID,
        "rank_key": variant["rank_key"],
        "kernel": {
            "top_n": kernel["top_n"],
            "max_active_positions": kernel["max_active_positions"],
            "symbol_cooldown_days": kernel["symbol_cooldown_days"],
            "market_levels": list(kernel["market_levels"]),
            "required_signal_tags": list(kernel["required_signal_tags"]),
            "excluded_signal_tags": list(kernel["excluded_signal_tags"]),
            "hold_days": 5,
            "stop_loss_pct": 5.0,
            "capital_model": "slot-daily",
            "exposure_multiplier": 1.0,
            "roundtrip_cost_bps": 25.0,
            "slippage_bps": 10.0,
        },
        "entry": {
            "identity": "trend_pullback_ma20_reclaim",
            "rank": "neg_ext20",
            "market_levels": ["favorable", "neutral"],
        },
        "rationale": variant["rationale"],
    }


def build_frozen_reclaim_card() -> dict[str, Any]:
    spec = frozen_reclaim_spec()
    return {
        "schema": CARD_SCHEMA,
        "candidate_id": spec["candidate_id"],
        "candidate_spec_sha256": _sha(spec),
        "spec": spec,
        "user_accepted_hypothesis": True,
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "meets_primary_targets": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "refit": False,
        "parameter_search": False,
        "search_universe_policy": proto.SEARCH_UNIVERSE_POLICY,
        "data_source_policy": goal.DATA_SOURCE_POLICY,
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def build_path_a_protocol_reclaim_freeze(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAReclaimFreezeError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / DEFAULT_HOLD_QT_PATH)
    )
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAReclaimFreezeError(f"qualified trades missing: {qt_path}")
    meta = (qt.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != "jiaoch-daily-bars/shares-cny/v2":
        raise PathAReclaimFreezeError("QT is not Jiaoch stk_mins v2")
    if meta.get("signal_family") != SIGNAL_HOLD_FAMILY:
        raise PathAReclaimFreezeError("QT is not the protocol signal-hold book")
    if meta.get("slice") == "traded":
        raise PathAReclaimFreezeError("refuses the contaminated 191-name slice")
    all_trades = train_replay._filter_train_trades(
        list(qt.get("qualified_trades") or [])
    )
    train_trades = proto.filter_trades_for_partition(all_trades, "train")
    holdout_trades = proto.filter_trades_for_partition(all_trades, "holdout")
    scored = baseline.score_protocol_baseline(
        train_trades,
        holdout_trades,
        variants=(FROZEN_RECLAIM_VARIANT,),
        stage_goal_id=STAGE_GOAL_ID,
    )
    row = (scored.get("variants") or [None])[0]
    if row is None:
        raise PathAReclaimFreezeError("frozen variant produced no score row")
    card = build_frozen_reclaim_card()
    holdout = row["holdout"]
    card["meets_primary_targets"] = bool(holdout["dual_pass_50_15"])
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "protocol": proto.protocol_descriptor(),
        "card": card,
        "qualified_trades_path": str(qt_path),
        "source_version": meta.get("source_version"),
        "signal_family": meta.get("signal_family"),
        "name_count": meta.get("name_count"),
        "train_trade_count": len(train_trades),
        "holdout_trade_count": len(holdout_trades),
        "train": row["train"],
        "holdout": holdout,
        "holdout_dual_pass_26_15": holdout["dual_pass_50_15"],
        "current_development_candidate": FROZEN_RECLAIM_CANDIDATE_ID,
        "user_accepted_hypothesis": True,
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "zero_refit": True,
    }


def format_reclaim_freeze_table(report: dict[str, Any]) -> str:
    train = report.get("train") or {}
    holdout = report.get("holdout") or {}
    card = report.get("card") or {}
    return "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"candidate={report.get('current_development_candidate')}",
            f"spec_sha256={card.get('candidate_spec_sha256')}",
            f"user_accepted_hypothesis={report.get('user_accepted_hypothesis')}",
            f"effective_strategy={report.get('effective_strategy')}",
            f"holdout_dual_pass_26_15={report.get('holdout_dual_pass_26_15')}",
            f"train_1y={train.get('latest_1y_return_pct')} "
            f"train_mdd={train.get('full_path_mdd_pct')}",
            f"holdout_1y={holdout.get('latest_1y_return_pct')} "
            f"holdout_mdd={holdout.get('full_path_mdd_pct')} "
            f"selected={holdout.get('selected_trade_count')}",
            "",
        ]
    )


def write_path_a_protocol_reclaim_freeze(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "FROZEN_CANDIDATE.json"), report.get("card"))
    write_json(str(output_root / "LATEST.json"), report)
    table = format_reclaim_freeze_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "effective_strategy": False,
        "table_path": str(output_root / "TABLE.txt"),
        "card_path": str(output_root / "FROZEN_CANDIDATE.json"),
    }
