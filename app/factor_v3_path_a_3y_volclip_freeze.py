"""Freeze vol_skip_rsi_adv_s85 and zero-refit it. Not formal OOS."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_3y_book_round as rnd
from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import (
    FROZEN_VOLCLIP_CANDIDATE_ID,
    FROZEN_VOLCLIP_VARIANT,
)
from app.storage import write_json

STAGE_GOAL_ID = "path-a-3y-volclip-freeze/v1"
CARD_SCHEMA = "path-a-3y-volclip-frozen-candidate/v1"
REPORT_SCHEMA = "path-a-3y-volclip-freeze-report/v1"
DEFAULT_QT = Path("data/research_cache/qualified_hold5_stop5_3y_jiaoch.json")
DEFAULT_OOS_QT = Path("data/research_cache/path_a_oos/qualified_hold5_stop5_oos.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_3y_volclip_freeze")


class PathA3yVolclipFreezeError(ValueError):
    """Raised when the frozen volclip card cannot be replayed."""


def _sha(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def frozen_volclip_spec() -> dict[str, Any]:
    variant = FROZEN_VOLCLIP_VARIANT
    kernel = rnd.merged_kernel(variant)
    return {
        "candidate_id": FROZEN_VOLCLIP_CANDIDATE_ID,
        "base_id": variant.get("base_id"),
        "kernel": {
            "top_n": kernel["top_n"],
            "max_active_positions": kernel["max_active_positions"],
            "symbol_cooldown_days": kernel["symbol_cooldown_days"],
            "market_levels": list(kernel["market_levels"]),
            "required_signal_tags": list(kernel["required_signal_tags"]),
            "excluded_signal_tags": list(kernel["excluded_signal_tags"]),
            "hold_days": kernel["hold_days"],
            "capital_model": kernel["capital_model"],
            "exposure_multiplier": kernel["exposure_multiplier"],
            "roundtrip_cost_bps": kernel["roundtrip_cost_bps"],
            "slippage_bps": kernel["slippage_bps"],
        },
        "skip_tags": list(variant.get("skip_tags") or ()),
        "skip_if": dict(variant.get("skip_if") or {}),
        "entry_scale": variant.get("entry_scale"),
        "rationale": variant["rationale"],
    }


def build_frozen_volclip_card() -> dict[str, Any]:
    spec = frozen_volclip_spec()
    return {
        "schema": CARD_SCHEMA,
        "candidate_id": spec["candidate_id"],
        "candidate_spec_sha256": _sha(spec),
        "spec": spec,
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "refit": False,
        "parameter_search": False,
    }


def _score_variant(trades: list[dict[str, Any]]) -> dict[str, Any]:
    report = rnd.score_clip_round(
        trades,
        variants=(FROZEN_VOLCLIP_VARIANT,),
        excluded_ids=frozenset(),
        stage_goal_id=STAGE_GOAL_ID,
    )
    rows = report.get("variants") or []
    if not rows:
        raise PathA3yVolclipFreezeError("frozen variant produced no score row")
    return rows[0]


def _slice_summary(row: dict[str, Any]) -> dict[str, Any]:
    full_ret = row.get("full_path_return_pct")
    full_mdd = row.get("full_path_mdd_pct")
    latest_ret = row.get("latest_1y_return_pct")
    latest_mdd = row.get("latest_1y_mdd_pct")
    return {
        "selected_trade_count": row.get("selected_trade_count"),
        "empty_selection": row.get("empty_selection"),
        "full_path_return_pct": full_ret,
        "full_path_mdd_pct": full_mdd,
        "latest_1y_return_pct": latest_ret,
        "latest_1y_mdd_pct": latest_mdd,
        "dual_pass_50_15": bool(row.get("dual_pass_50_15")),
        "latest_dual_pass_50_15": bool(row.get("latest_dual_pass_50_15")),
        "dual_pass_via": row.get("dual_pass_via"),
    }


def build_path_a_3y_volclip_freeze(
    *,
    repo_root: Path | None = None,
    qualified_trades_path: Path | None = None,
    oos_qualified_trades_path: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathA3yVolclipFreezeError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    qt_path = (
        Path(qualified_trades_path).resolve()
        if qualified_trades_path is not None
        else (root / DEFAULT_QT)
    )
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathA3yVolclipFreezeError(f"qualified trades missing: {qt_path}")
    meta = (qt.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    if meta.get("source_version") != "jiaoch-daily-bars/shares-cny/v2":
        raise PathA3yVolclipFreezeError("3y QT is not Jiaoch stk_mins v2")
    train_trades = train_replay._filter_train_trades(
        list(qt.get("qualified_trades") or [])
    )
    card = build_frozen_volclip_card()
    train_row = _score_variant(train_trades)
    train_summary = _slice_summary(train_row)

    oos_path = (
        Path(oos_qualified_trades_path).resolve()
        if oos_qualified_trades_path is not None
        else (root / DEFAULT_OOS_QT)
    )
    oos_qt = train_replay._load_json(oos_path)
    oos_block: dict[str, Any]
    if oos_qt is None:
        oos_block = {
            "present": False,
            "path": str(oos_path),
            "note": "shadow OOS QT missing; not formal OOS",
        }
    else:
        oos_trades = list(oos_qt.get("qualified_trades") or [])
        oos_row = _score_variant(oos_trades)
        levels: dict[str, int] = {}
        for trade in oos_trades:
            key = str(trade.get("market_level") or "")
            levels[key] = levels.get(key, 0) + 1
        oos_block = {
            "present": True,
            "path": str(oos_path),
            "raw_trade_count": len(oos_trades),
            "market_level_counts": levels,
            "score": _slice_summary(oos_row),
            "formal_final_oos": False,
            "note": (
                "Path-A shadow diagnostic only. Formal final-OOS stays sealed. "
                "Empty selection is expected if no favorable/neutral days."
            ),
        }

    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "refit": False,
        "parameter_search": False,
        "frozen_candidate": card,
        "train_replay": {
            "qualified_trades_path": str(qt_path),
            "source_version": meta.get("source_version"),
            "window": meta.get("window"),
            "score": train_summary,
        },
        "shadow_oos": oos_block,
        "fifty_fifteen_train_met": bool(train_summary["dual_pass_50_15"]),
        "fifty_fifteen_train_latest_met": bool(train_summary["latest_dual_pass_50_15"]),
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
    }


def format_volclip_freeze_table(report: dict[str, Any]) -> str:
    card = report.get("frozen_candidate") or {}
    spec = card.get("spec") or {}
    train = (report.get("train_replay") or {}).get("score") or {}
    oos = report.get("shadow_oos") or {}
    oos_score = oos.get("score") or {}
    lines = [
        f"stage={report.get('stage_goal_id')}",
        f"candidate={card.get('candidate_id')}",
        f"spec_sha={card.get('candidate_spec_sha256')}",
        f"entry_scale={spec.get('entry_scale')}",
        f"skip_tags={spec.get('skip_tags')}",
        f"train_dual={train.get('dual_pass_50_15')} latest={train.get('latest_dual_pass_50_15')}",
        f"train_full={train.get('full_path_return_pct')}/{train.get('full_path_mdd_pct')}",
        f"train_1y={train.get('latest_1y_return_pct')}/{train.get('latest_1y_mdd_pct')}",
        f"train_selected={train.get('selected_trade_count')}",
        f"shadow_oos_present={oos.get('present')}",
        f"shadow_oos_raw={oos.get('raw_trade_count')}",
        f"shadow_oos_selected={(oos_score or {}).get('selected_trade_count')}",
        f"shadow_oos_levels={oos.get('market_level_counts')}",
        f"effective_strategy={report.get('effective_strategy')}",
        f"formal_final_oos_executable={report.get('formal_final_oos_executable')}",
    ]
    return "\n".join(lines) + "\n"


def write_path_a_3y_volclip_freeze(
    report: dict[str, Any], *, output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "FROZEN_CANDIDATE.json"), report["frozen_candidate"])
    write_json(str(output_root / "LATEST.json"), report)
    table = format_volclip_freeze_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "candidate_id": (report.get("frozen_candidate") or {}).get("candidate_id"),
        "effective_strategy": False,
        "table_path": str(output_root / "TABLE.txt"),
        "card_path": str(output_root / "FROZEN_CANDIDATE.json"),
        "report_path": str(output_root / "LATEST.json"),
    }
