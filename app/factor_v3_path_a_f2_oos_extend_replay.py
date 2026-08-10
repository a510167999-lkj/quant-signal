"""Path A F2: extend post-train OOS window and re-run zero-refit.

Builds on F1 shadow OOS collect + qualified generation. Does not unseal
frozen-v1 final_oos, does not refit the frozen candidate, never auto-trades.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_path_a_f1_oos_collect_replay as f1
from app import factor_v3_train_window_freeze_contract as freeze
from app.factor_v3_path_a_f1_oos_qualified import generate_path_a_oos_qualified_trades
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)

STAGE_GOAL_ID = "path-a-f2-oos-extend-replay/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A F2：扩长 post-train OOS 行情窗，重生 OOS qualified，"
    "对 F0 冻结候选 zero-refit 重放；不打开 formal final_oos；不重拟合；永不自动交易。"
)
REPORT_SCHEMA = "path-a-f2-oos-extend-replay-report/v1"
POINTER_SCHEMA = "path-a-f2-oos-extend-replay-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_f2_oos_extend_replay")
DEFAULT_F1_POINTER = Path("data/research_runs/path_a_f1_oos_collect_replay/LATEST.json")
DEFAULT_OOS_END = "2026-08-11"  # inclusive request end; open sessions may stop earlier
SHADOW_ROLE = f1.SHADOW_ROLE
OOS_COLLECTION_START = f1.OOS_COLLECTION_START


class PathAF2Error(ValueError):
    """Raised when path-A F2 fails closed."""


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
        raise PathAF2Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _collect_day_chunk(
    *,
    repo_root: Path,
    day: str,
    store_dir: Path,
) -> dict[str, Any]:
    """Collect a single calendar day to avoid trade_cal partition overlap bugs."""

    shadow_path = (repo_root / f1.DEFAULT_SHADOW_CONTRACT).resolve()
    contract = load_temporal_partition_contract(str(shadow_path))
    assert_range_allowed(contract, SHADOW_ROLE, day, day, "collect")
    if not os.getenv("JIAOCH_TOKEN"):
        raise PathAF2Error("JIAOCH_TOKEN required for OOS market collect")

    progress = store_dir.parent / f".path_a_oos_f2_day_{day.replace('-', '')}_progress.json"
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "app.jobs",
        "research-current-pool-fetch-market-jiaoch",
        "--store-dir",
        str(store_dir),
        "--start-date",
        day,
        "--end-date",
        day,
        "--workers",
        "1",
        "--batch-size",
        "1",
        "--timeout",
        "60",
        "--temporal-contract-path",
        str(shadow_path),
        "--temporal-role",
        SHADOW_ROLE,
        "--progress-path",
        str(progress),
    ]
    env = dict(os.environ)
    env["VPS_RUNTIME_ROLE"] = REQUIRED_RUNTIME_ROLE
    completed = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    report = None
    if completed.returncode == 0 and completed.stdout:
        text = completed.stdout.strip()
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    report = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
    return {
        "day": day,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "report": report,
        "stderr_tail": (completed.stderr or "")[-1500:],
        "stdout_tail": (completed.stdout or "")[-1500:],
    }


def extend_oos_market_to(
    *,
    repo_root: Path,
    target_end_date: str,
    store_dir: Path | None = None,
) -> dict[str, Any]:
    """Day-chunk collect from day-after current max through target_end_date."""

    root = repo_root.resolve()
    store = (store_dir or (root / f1.DEFAULT_OOS_STORE)).resolve()
    before = f1._market_session_bounds(store)
    max_day = before.get("max_trade_date")
    if not max_day:
        raise PathAF2Error("OOS store has no sessions; run F1 collect first")

    # Candidate open days from Jiaoch calendar via day-chunk attempts.
    # We walk ISO days from max_day+1 to target_end inclusive; non-open/empty
    # days fail closed and are recorded.
    from datetime import date, timedelta

    start = date.fromisoformat(str(max_day)[:10]) + timedelta(days=1)
    end = date.fromisoformat(str(target_end_date)[:10])
    attempts: list[dict[str, Any]] = []
    if start > end:
        return {
            "attempted": False,
            "reason": "store already covers target_end_date",
            "before": before,
            "after": before,
            "attempts": [],
        }

    day = start
    while day <= end:
        day_s = day.isoformat()
        try:
            result = _collect_day_chunk(repo_root=root, day=day_s, store_dir=store)
        except Exception as exc:  # noqa: BLE001
            result = {
                "day": day_s,
                "ok": False,
                "returncode": -1,
                "error": str(exc),
            }
        attempts.append(result)
        day += timedelta(days=1)

    after = f1._market_session_bounds(store)
    return {
        "attempted": True,
        "target_end_date": target_end_date,
        "before": before,
        "after": after,
        "sessions_added": int(after.get("session_count") or 0)
        - int(before.get("session_count") or 0),
        "attempts": attempts,
        "ok": int(after.get("session_count") or 0)
        > int(before.get("session_count") or 0)
        or any(a.get("ok") for a in attempts),
    }


def build_path_a_f2_oos_extend_replay(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
    extend: bool = True,
    generate_qualified: bool = True,
    oos_end_date: str = DEFAULT_OOS_END,
    max_universe_symbols: int = 300,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()

    f1_pointer = _load_json(root / DEFAULT_F1_POINTER) or {}
    frozen_v1 = load_temporal_partition_contract(str(root / f1.DEFAULT_FROZEN_V1))
    final = next(r for r in frozen_v1["roles"] if r["name"] == "final_oos")
    formal_final_oos_still_sealed = (
        final.get("sealed") is True and list(final.get("permitted_operations") or []) == []
    )

    shadow = load_temporal_partition_contract(str(root / f1.DEFAULT_SHADOW_CONTRACT))
    assert_range_allowed(
        shadow, SHADOW_ROLE, OOS_COLLECTION_START, oos_end_date, "evaluate"
    )

    candidate = f1._load_frozen_candidate(root)
    store_path = root / f1.DEFAULT_OOS_STORE
    store_before = f1._market_session_bounds(store_path)

    extend_result: dict[str, Any] | None = None
    if extend:
        extend_result = extend_oos_market_to(
            repo_root=root,
            target_end_date=oos_end_date,
            store_dir=store_path,
        )
    store_after = f1._market_session_bounds(store_path)

    oos_qt_path = root / f1.DEFAULT_OOS_QUALIFIED
    oos_qt_before = f1._audit_oos_trades(oos_qt_path)

    gen_result: dict[str, Any] | None = None
    if generate_qualified:
        gen_result = generate_path_a_oos_qualified_trades(
            repo_root=root,
            oos_store=store_path,
            output_path=oos_qt_path,
            max_universe_symbols=max_universe_symbols,
        )
    oos_qt_after = f1._audit_oos_trades(oos_qt_path)

    oos_trades: list[dict[str, Any]] = []
    payload = _load_json(oos_qt_path)
    if payload:
        oos_trades = [
            t
            for t in (payload.get("qualified_trades") or [])
            if str(t.get("signal_date") or "")[:10] >= OOS_COLLECTION_START
            and str(t.get("signal_date") or "")[:10] > freeze.TRAIN_INCLUSIVE_SESSION_END
        ]
    replay = f1._zero_refit_replay(oos_trades, candidate["spec"], min_trades=1)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = [
        {
            "code": "formal_final_oos_still_sealed",
            "detail": "frozen-v1 final_oos remains sealed; F2 is shadow diagnostic only",
        }
    ]
    if int(store_after.get("post_train_session_count") or 0) == 0:
        blockers.append(
            {
                "code": "oos_market_empty",
                "detail": "OOS store has no post-train sessions",
            }
        )
    if int(oos_qt_after.get("oos_trade_count") or 0) == 0:
        blockers.append(
            {
                "code": "oos_qualified_trades_missing",
                "detail": "no OOS qualified trades after F2 generate",
            }
        )

    f1_sessions = int(f1_pointer.get("oos_post_train_sessions") or 0)
    f1_qt = int(f1_pointer.get("oos_qualified_trade_count") or 0)
    f1_selected = int(f1_pointer.get("zero_refit_selected_trades") or 0)

    comparison = {
        "f1_pointer_report_sha256": f1_pointer.get("report_sha256"),
        "f1_oos_sessions": f1_sessions,
        "f2_oos_sessions": int(store_after.get("post_train_session_count") or 0),
        "sessions_delta": int(store_after.get("post_train_session_count") or 0)
        - f1_sessions,
        "f1_oos_qualified_trades": f1_qt,
        "f2_oos_qualified_trades": int(oos_qt_after.get("oos_trade_count") or 0),
        "qualified_trades_delta": int(oos_qt_after.get("oos_trade_count") or 0) - f1_qt,
        "f1_zero_refit_selected": f1_selected,
        "f2_zero_refit_selected": int(replay.get("selected_trade_count") or 0),
        "f1_store_max": (oos_qt_before.get("last_signal_date")),
        "f2_store_session_max": store_after.get("max_trade_date"),
        "f2_qt_signal_max": oos_qt_after.get("last_signal_date"),
    }

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_f2_oos_extend_replay",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "frozen_candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_spec_sha256": candidate["candidate_spec_sha256"],
            "path": candidate["path"],
        },
        "evaluate_policy": {
            "name": "path_a_shadow_post_train_zero_refit/v1",
            "formal_final_oos": False,
            "refit_allowed": False,
            "parameter_search_allowed": False,
            "automatic_trading_allowed": False,
            "shadow_role": SHADOW_ROLE,
            "shadow_contract_sha256": shadow.get("contract_sha256"),
        },
        "formal_final_oos_still_sealed": formal_final_oos_still_sealed,
        "oos_market_before": store_before,
        "oos_market_after": store_after,
        "extend_attempt": extend_result,
        "generate_qualified_attempt": gen_result,
        "oos_qualified_before": oos_qt_before,
        "oos_qualified_after": oos_qt_after,
        "zero_refit_replay": replay,
        "comparison_to_f1": comparison,
        "blockers": blockers,
        "warnings": warnings,
        "shadow_oos_extend_ok": int(comparison.get("sessions_delta") or 0) > 0
        or (
            extend_result is not None
            and extend_result.get("reason") == "store already covers target_end_date"
        ),
        "shadow_oos_replay_ok": bool(replay.get("executed"))
        and int(oos_qt_after.get("oos_trade_count") or 0) > 0,
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "ok": True,
        "notes": (
            "ok=true means F2 pipeline completed. "
            "effective_strategy_found stays false. formal final-OOS remains sealed. "
            "Market extend uses day-chunk collect to avoid trade_cal partition overlap."
        ),
        "next_actions": [
            a
            for a in [
                (
                    "no new market sessions added; wait for next close or check Jiaoch daily"
                    if int(comparison.get("sessions_delta") or 0) == 0
                    and extend
                    else None
                ),
                (
                    "frozen filters still select 0 OOS trades; extend further or review regime"
                    if bool(replay.get("executed"))
                    and int(replay.get("selected_trade_count") or 0) == 0
                    and int(oos_qt_after.get("oos_trade_count") or 0) > 0
                    else None
                ),
                "do not unseal frozen-v1 casually",
                "never auto-trade; never merge OOS into train",
            ]
            if a
        ],
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_f2_oos_extend_replay(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAF2Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-f2-oos-extend-replay.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAF2Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    comparison = report.get("comparison_to_f1") or {}
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_f2_oos_extend_replay",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "shadow_oos_extend_ok": report.get("shadow_oos_extend_ok"),
        "shadow_oos_replay_ok": report.get("shadow_oos_replay_ok"),
        "blocker_codes": [b.get("code") for b in report.get("blockers") or []],
        "candidate_id": (report.get("frozen_candidate") or {}).get("candidate_id"),
        "candidate_spec_sha256": (report.get("frozen_candidate") or {}).get(
            "candidate_spec_sha256"
        ),
        "oos_post_train_sessions": comparison.get("f2_oos_sessions"),
        "oos_sessions_delta_vs_f1": comparison.get("sessions_delta"),
        "oos_qualified_trade_count": comparison.get("f2_oos_qualified_trades"),
        "oos_qualified_delta_vs_f1": comparison.get("qualified_trades_delta"),
        "zero_refit_selected_trades": comparison.get("f2_zero_refit_selected"),
        "oos_session_max": (report.get("oos_market_after") or {}).get("max_trade_date"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OOS_END",
    "DEFAULT_OUTPUT_ROOT",
    "OOS_COLLECTION_START",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAF2Error",
    "build_path_a_f2_oos_extend_replay",
    "extend_oos_market_to",
    "write_path_a_f2_oos_extend_replay",
]
