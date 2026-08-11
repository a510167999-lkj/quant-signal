"""Path A F3: daily post-close OOS roll — push end-date and re-run F2.

Intended for after each A-share close: extend shadow OOS market by day chunks,
regenerate OOS qualified trades, zero-refit frozen candidate, append roll log.

Does not unseal formal final_oos, does not refit, never auto-trades.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import factor_v3_path_a_f1_oos_collect_replay as f1
from app import factor_v3_path_a_f2_oos_extend_replay as f2
from app import factor_v3_train_window_freeze_contract as freeze

# Frozen rule allowed levels (mirrors F0 candidate; read-only for monitoring).
_FROZEN_MARKET_LEVELS = ("favorable", "neutral")

STAGE_GOAL_ID = "path-a-f3-oos-daily-roll/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A F3：每个交易日收盘后自动推高 OOS end-date，日增扩采并 zero-refit；"
    "滚长 shadow OOS 样本；不打开 formal final_oos；不重拟合；永不自动交易。"
)
REPORT_SCHEMA = "path-a-f3-oos-daily-roll-report/v1"
POINTER_SCHEMA = "path-a-f3-oos-daily-roll-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_f3_oos_daily_roll")
DEFAULT_F2_POINTER = Path("data/research_runs/path_a_f2_oos_extend_replay/LATEST.json")
DEFAULT_TZ = "Asia/Shanghai"
# After 15:00 close, bar data often stabilizes later; default schedule hint.
DEFAULT_SCHEDULE_HINT_LOCAL = "18:30"


class PathAF3Error(ValueError):
    """Raised when path-A F3 fails closed."""


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


def _oos_market_level_snapshot(qt_path: Path) -> dict[str, Any]:
    """Read-only market_level distribution from the OOS qualified trades file.

    Lets the daily roll surface "still all cautious/defensive" at a glance
    instead of silently selecting 0.
    """

    payload = f1._load_json(qt_path)
    if payload is None:
        return {"present": False, "by_trade": {}, "by_day": {},
                "frozen_rule_pass_trades": 0}
    trades = [
        t for t in (payload.get("qualified_trades") or [])
        if str(t.get("signal_date") or "")[:10] >= f1.OOS_COLLECTION_START
    ]
    by_trade: dict[str, int] = {}
    day_level: dict[str, str] = {}
    for t in trades:
        level = str(t.get("market_level") or "unknown")
        by_trade[level] = by_trade.get(level, 0) + 1
        d = str(t.get("signal_date") or "")[:10]
        if d and d not in day_level:
            day_level[d] = level
    by_day: dict[str, int] = {}
    for level in day_level.values():
        by_day[level] = by_day.get(level, 0) + 1
    frozen_pass = sum(
        n for lvl, n in by_trade.items() if lvl in _FROZEN_MARKET_LEVELS
    )
    return {
        "present": True,
        "by_trade": by_trade,
        "by_day": by_day,
        "frozen_rule_pass_trades": frozen_pass,
        "frozen_rule_allowed_levels": list(_FROZEN_MARKET_LEVELS),
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAF3Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def today_in_tz(tz_name: str = DEFAULT_TZ) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def resolve_oos_end_date(
    *,
    as_of: str | None = None,
    tz_name: str = DEFAULT_TZ,
) -> str:
    """Inclusive OOS collect end date for a roll run.

    Default: calendar today in Asia/Shanghai (post-close operator runs same day).
    """

    if as_of:
        return date.fromisoformat(str(as_of)[:10]).isoformat()
    return today_in_tz(tz_name).isoformat()


def append_roll_history(
    output_root: Path,
    row: dict[str, Any],
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "roll_history.jsonl"
    line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def build_path_a_f3_oos_daily_roll(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
    as_of: str | None = None,
    tz_name: str = DEFAULT_TZ,
    extend: bool = True,
    generate_qualified: bool = True,
    max_universe_symbols: int = 300,
    skip_if_no_new_session: bool = False,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()
    oos_end = resolve_oos_end_date(as_of=as_of, tz_name=tz_name)

    store_before = f1._market_session_bounds(root / f1.DEFAULT_OOS_STORE)
    max_before = store_before.get("max_trade_date")
    if max_before and str(max_before)[:10] > oos_end:
        # Operator clock behind store (should not happen); still allow report.
        pass

    skipped = False
    skip_reason = None
    f2_report: dict[str, Any] | None = None
    f2_pointer: dict[str, Any] | None = None

    if (
        skip_if_no_new_session
        and max_before
        and str(max_before)[:10] >= oos_end
    ):
        skipped = True
        skip_reason = (
            f"store max {max_before} already covers oos_end_date {oos_end}; "
            "skip extend/generate/refit"
        )
    else:
        f2_report = f2.build_path_a_f2_oos_extend_replay(
            repo_root=root,
            require_local_research=require_local_research,
            extend=extend,
            generate_qualified=generate_qualified,
            oos_end_date=oos_end,
            max_universe_symbols=max_universe_symbols,
        )
        f2_pointer = f2.write_path_a_f2_oos_extend_replay(
            f2_report,
            output_root=(root / f2.DEFAULT_OUTPUT_ROOT).resolve(),
        )

    store_after = f1._market_session_bounds(root / f1.DEFAULT_OOS_STORE)
    oos_qt = f1._audit_oos_trades(root / f1.DEFAULT_OOS_QUALIFIED)
    f2_latest = _load_json(root / DEFAULT_F2_POINTER) or f2_pointer or {}

    sessions_before = int(store_before.get("post_train_session_count") or 0)
    sessions_after = int(store_after.get("post_train_session_count") or 0)
    sessions_delta = sessions_after - sessions_before

    replay = (f2_report or {}).get("zero_refit_replay") if f2_report else None
    if replay is None and not skipped:
        replay = {}
    if skipped:
        # Still surface last known zero-refit from F2 pointer if present.
        replay = {
            "executed": False,
            "reason": "skipped_no_new_session",
            "selected_trade_count": f2_latest.get("zero_refit_selected_trades"),
        }

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = [
        {
            "code": "formal_final_oos_still_sealed",
            "detail": "daily roll is shadow diagnostic only; frozen-v1 final_oos sealed",
        }
    ]
    if not skipped and f2_report:
        for b in f2_report.get("blockers") or []:
            blockers.append(b)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_f3_oos_daily_roll",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "timezone": tz_name,
        "schedule_hint_local": DEFAULT_SCHEDULE_HINT_LOCAL,
        "oos_end_date": oos_end,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "store_before": store_before,
        "store_after": store_after,
        "sessions_before": sessions_before,
        "sessions_after": sessions_after,
        "sessions_delta": sessions_delta,
        "oos_qualified_audit": oos_qt,
        "oos_market_level_snapshot": _oos_market_level_snapshot(
            root / f1.DEFAULT_OOS_QUALIFIED
        ),
        "f2_pointer": {
            "report_sha256": f2_latest.get("report_sha256"),
            "oos_post_train_sessions": f2_latest.get("oos_post_train_sessions"),
            "oos_qualified_trade_count": f2_latest.get("oos_qualified_trade_count"),
            "zero_refit_selected_trades": f2_latest.get("zero_refit_selected_trades"),
            "oos_session_max": f2_latest.get("oos_session_max"),
        },
        "f2_report_sha256": (f2_report or {}).get("report_sha256"),
        "zero_refit_replay": replay,
        "blockers": blockers,
        "warnings": warnings,
        "roll_advanced": sessions_delta > 0,
        "shadow_oos_replay_ok": bool((replay or {}).get("executed"))
        or (
            not skipped
            and int(oos_qt.get("oos_trade_count") or 0) > 0
            and f2_report is not None
        ),
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "ok": True,
        "notes": (
            "ok=true means the daily roll pipeline finished. "
            "roll_advanced=true only when new market sessions were added. "
            "Never auto-trade; train freeze unchanged."
        ),
        "next_actions": [
            a
            for a in [
                (
                    "no new session today; re-run after next close or when Jiaoch daily is ready"
                    if sessions_delta == 0 and not skipped
                    else None
                ),
                (
                    "store already covered as-of date; nothing to roll"
                    if skipped
                    else None
                ),
                (
                    "frozen filters still select 0; keep rolling to grow sample"
                    if not skipped
                    and int((replay or {}).get("selected_trade_count") or 0) == 0
                    and int(oos_qt.get("oos_trade_count") or 0) > 0
                    else None
                ),
                "never auto-trade; never merge OOS into train",
            ]
            if a
        ],
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_f3_oos_daily_roll(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAF3Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-f3-oos-daily-roll.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAF3Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_f3_oos_daily_roll",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "oos_end_date": report.get("oos_end_date"),
        "skipped": report.get("skipped"),
        "roll_advanced": report.get("roll_advanced"),
        "sessions_after": report.get("sessions_after"),
        "sessions_delta": report.get("sessions_delta"),
        "oos_session_max": (report.get("store_after") or {}).get("max_trade_date"),
        "oos_qualified_trade_count": (
            (report.get("oos_qualified_audit") or {}).get("oos_trade_count")
        ),
        "zero_refit_selected_trades": (
            (report.get("zero_refit_replay") or {}).get("selected_trade_count")
        ),
        "f2_report_sha256": report.get("f2_report_sha256"),
        "blocker_codes": [b.get("code") for b in report.get("blockers") or []],
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "rolled_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    history_row = {
        "rolled_at_utc": pointer["rolled_at_utc"],
        "oos_end_date": pointer.get("oos_end_date"),
        "roll_advanced": pointer.get("roll_advanced"),
        "sessions_delta": pointer.get("sessions_delta"),
        "sessions_after": pointer.get("sessions_after"),
        "oos_session_max": pointer.get("oos_session_max"),
        "oos_qualified_trade_count": pointer.get("oos_qualified_trade_count"),
        "zero_refit_selected_trades": pointer.get("zero_refit_selected_trades"),
        "report_sha256": digest,
        "skipped": pointer.get("skipped"),
    }
    history_path = append_roll_history(output_root, history_row)
    pointer["roll_history_path"] = str(history_path.resolve())
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_SCHEDULE_HINT_LOCAL",
    "DEFAULT_TZ",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAF3Error",
    "build_path_a_f3_oos_daily_roll",
    "resolve_oos_end_date",
    "today_in_tz",
    "write_path_a_f3_oos_daily_roll",
]
