"""Path A F1: post-train OOS collection inventory + zero-refit replay.

Does NOT unseal frozen-v1 final_oos. Uses a Path-A-only shadow partition
(`path-a-shadow-post-train-oos/v1`) so collect/evaluate can proceed for
dates after train end without merging into the training set.

Never auto-trades. Never claims formal final-OOS or effective strategy
until formal partition policy allows it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_sweep import sweep_qualified_trades

STAGE_GOAL_ID = "path-a-f1-oos-collect-replay/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A F1：在训练窗冻结前提下采集 post-train OOS 行情（不并入训练集），"
    "对 F0 冻结候选做 zero-refit 重放；使用 shadow 分区，不打开 frozen-v1 final_oos；"
    "永不自动交易；不宣称 formal final-OOS / 有效策略。"
)
REPORT_SCHEMA = "path-a-f1-oos-collect-replay-report/v1"
POINTER_SCHEMA = "path-a-f1-oos-collect-replay-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_F0_POINTER = Path("data/research_runs/path_a_f0_oos_readiness/LATEST.json")
DEFAULT_SHADOW_CONTRACT = Path(
    "data/research_partitions/path-a-shadow-post-train-oos-v1.json"
)
DEFAULT_FROZEN_V1 = Path("data/research_partitions/frozen-v1.json")
DEFAULT_TRAIN_QUALIFIED = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OOS_QUALIFIED = Path(
    "data/research_cache/path_a_oos/qualified_hold5_stop5_oos.json"
)
DEFAULT_OOS_STORE = Path("data/research_pit_store/path_a_oos_market_full")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_f1_oos_collect_replay")

OOS_COLLECTION_START = "2026-07-04"
SHADOW_ROLE = "shadow_post_train_oos"
DEFAULT_OOS_END = "2026-08-08"


class PathAF1Error(ValueError):
    """Raised when path-A F1 fails closed."""


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAF1Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _rel(repo: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _market_session_bounds(store_dir: Path) -> dict[str, Any]:
    meta = store_dir / "metadata.sqlite3"
    if not meta.is_file():
        return {
            "present": False,
            "store_dir": str(store_dir),
            "session_count": 0,
            "min_trade_date": None,
            "max_trade_date": None,
            "post_train_session_count": 0,
        }
    con = sqlite3.connect(f"file:{meta.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            """
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM market_session_generation_head
            """
        ).fetchone()
        n, mn, mx = int(row[0] or 0), row[1], row[2]
        post = con.execute(
            """
            SELECT COUNT(*) FROM market_session_generation_head
            WHERE trade_date >= ?
            """,
            (OOS_COLLECTION_START,),
        ).fetchone()[0]
        cal = con.execute(
            """
            SELECT MIN(cal_date), MAX(cal_date), COUNT(*)
            FROM trade_sessions WHERE is_open = 1
            """
        ).fetchone()
    finally:
        con.close()
    return {
        "present": True,
        "store_dir": str(store_dir),
        "session_count": n,
        "min_trade_date": mn,
        "max_trade_date": mx,
        "post_train_session_count": int(post or 0),
        "calendar_open_min": cal[0] if cal else None,
        "calendar_open_max": cal[1] if cal else None,
        "calendar_open_count": int(cal[2] or 0) if cal else 0,
    }


def _audit_oos_trades(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if payload is None:
        return {
            "present": False,
            "path": str(path),
            "trade_count": 0,
            "oos_trade_count": 0,
            "first_signal_date": None,
            "last_signal_date": None,
        }
    trades = payload.get("qualified_trades") or []
    oos = [
        t
        for t in trades
        if str(t.get("signal_date") or "")[:10] >= OOS_COLLECTION_START
    ]
    dates = sorted(
        {
            str(t.get("signal_date") or "")[:10]
            for t in oos
            if str(t.get("signal_date") or "")[:10]
        }
    )
    return {
        "present": True,
        "path": str(path),
        "file_sha256": _file_sha256(path),
        "trade_count": len(trades),
        "oos_trade_count": len(oos),
        "first_signal_date": dates[0] if dates else None,
        "last_signal_date": dates[-1] if dates else None,
        "signal_day_count": len(dates),
    }


def _load_frozen_candidate(repo: Path) -> dict[str, Any]:
    card_path = (repo / DEFAULT_FROZEN_CANDIDATE).resolve()
    card = _load_json(card_path)
    if card is None:
        raise PathAF1Error(f"frozen candidate card missing: {card_path}")
    expected_id = f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]
    if card.get("candidate_id") != expected_id:
        raise PathAF1Error(
            f"candidate_id mismatch: {card.get('candidate_id')!r} != {expected_id!r}"
        )
    expected_spec_sha = _sha(f0.FROZEN_PRIMARY_CANDIDATE["spec"])
    if card.get("candidate_spec_sha256") != expected_spec_sha:
        # Allow card to carry its own sha if equal to recomputed from card.spec
        card_spec_sha = _sha(card.get("spec") or {})
        if card.get("candidate_spec_sha256") != card_spec_sha:
            raise PathAF1Error("frozen candidate spec hash mismatch")
        if card_spec_sha != expected_spec_sha:
            raise PathAF1Error("frozen candidate drifted from F0 primary identity")
    return {
        "path": _rel(repo, card_path),
        "card": card,
        "candidate_id": card["candidate_id"],
        "candidate_spec_sha256": card["candidate_spec_sha256"],
        "spec": card["spec"],
    }


def _zero_refit_replay(
    trades: list[dict[str, Any]],
    spec: dict[str, Any],
    *,
    min_trades: int = 1,
) -> dict[str, Any]:
    """Replay frozen spec with no parameter search (fixed_spec=True)."""

    if not trades:
        return {
            "executed": False,
            "reason": "no_oos_trades",
            "selected_trade_count": 0,
        }
    payload = sweep_qualified_trades(
        deepcopy(trades),
        hold_days=int(spec.get("hold_days") or 5),
        top_n=int(spec.get("top_n") or 3),
        symbol_cooldown_days=int(spec.get("symbol_cooldown_days") or 5),
        max_active_positions=int(spec.get("max_active_positions") or 2),
        min_trades=min_trades,
        max_filter_size=1,
        target_win_rate_pct=52.0,
        target_drawdown_pct=goal.TARGET_MAX_DRAWDOWN_PCT,
        target_one_year_return_pct=goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        exposure_multipliers=[float(spec.get("exposure_multiplier") or 1.0)],
        annual_financing_rate_pct=float(spec.get("annual_financing_rate_pct") or 0.0),
        roundtrip_cost_bps=float(spec.get("roundtrip_cost_bps") or 25.0),
        slippage_bps=float(spec.get("slippage_bps") or 10.0),
        capital_model=str(spec.get("capital_model") or "slot-daily"),
        required_signal_tags=list(spec.get("required_signal_tags") or []),
        excluded_signal_tags=list(spec.get("excluded_signal_tags") or []),
        market_levels=list(spec.get("market_levels") or []),
        force_exposure_multipliers=True,
        pre_exit_calendar_gap_days=int(spec.get("pre_exit_calendar_gap_days") or 0),
        prior_high_trailing_stop_pct=spec.get("prior_high_trailing_stop_pct"),
        prior_high_trailing_activation_pct=0.0,
        partial_profit_activation_pct=spec.get("partial_profit_activation_pct"),
        partial_profit_fraction=float(spec.get("partial_profit_fraction") or 0.0),
        correlation_threshold=None,
        fixed_spec=True,
    )
    rows = payload.get("top") or []
    if not rows:
        return {
            "executed": True,
            "matched_rows": 0,
            "reason": "fixed_spec_produced_no_rows",
            "selected_trade_count": 0,
            "pass_both_50_15": False,
        }
    row = rows[0]
    ret = row.get(
        "rolling_1y_latest_return_pct_raw",
        row.get("rolling_1y_latest_return_pct"),
    )
    mdd = row.get(
        "rolling_1y_latest_max_drawdown_pct_raw",
        row.get("rolling_1y_latest_max_drawdown_pct"),
    )
    # Short OOS windows often lack a full rolling-1y; also report portfolio totals.
    port_ret = row.get("portfolio_compounded_return_pct")
    port_mdd = row.get("portfolio_max_drawdown_pct")
    ret_f = float(ret) if ret is not None else None
    mdd_f = float(mdd) if mdd is not None else None
    port_ret_f = float(port_ret) if port_ret is not None else None
    port_mdd_f = float(port_mdd) if port_mdd is not None else None
    dual = (
        ret_f is not None
        and mdd_f is not None
        and ret_f >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
        and abs(mdd_f) <= goal.TARGET_MAX_DRAWDOWN_PCT
    )
    return {
        "executed": True,
        "matched_rows": 1,
        "refit": False,
        "fixed_spec": True,
        "selected_trade_count": int(row.get("selected_trade_count") or 0),
        "signal_days": int(row.get("signal_days") or 0),
        "trade_win_rate_pct": row.get("trade_win_rate_pct"),
        "portfolio_compounded_return_pct": port_ret_f,
        "portfolio_max_drawdown_pct": port_mdd_f,
        "rolling_1y_latest_return_pct": ret_f,
        "rolling_1y_latest_max_drawdown_pct": mdd_f,
        "pass_both_50_15_on_rolling_1y": dual,
        "note": (
            "Short post-train OOS windows usually lack full rolling-12m coverage; "
            "portfolio totals are diagnostic only, not formal final-OOS pass."
        ),
    }


def collect_path_a_oos_market(
    *,
    repo_root: Path,
    start_date: str = OOS_COLLECTION_START,
    end_date: str = DEFAULT_OOS_END,
    store_dir: Path | None = None,
    workers: int = 1,
    batch_size: int = 1,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Collect OOS market into a store separate from train (subprocess CLI)."""

    root = repo_root.resolve()
    shadow_path = (root / DEFAULT_SHADOW_CONTRACT).resolve()
    contract = load_temporal_partition_contract(str(shadow_path))
    assert_range_allowed(contract, SHADOW_ROLE, start_date, end_date, "collect")

    out_store = (store_dir or (root / DEFAULT_OOS_STORE)).resolve()
    out_store.mkdir(parents=True, exist_ok=True)
    progress = out_store.parent / ".path_a_oos_market_progress.json"

    env = dict(os.environ)
    env["VPS_RUNTIME_ROLE"] = REQUIRED_RUNTIME_ROLE
    # Ensure token from .env is already in process env of caller.
    if not env.get("JIAOCH_TOKEN"):
        raise PathAF1Error("JIAOCH_TOKEN required for OOS market collect")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "app.jobs",
        "research-current-pool-fetch-market-jiaoch",
        "--store-dir",
        str(out_store),
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--workers",
        str(workers),
        "--batch-size",
        str(batch_size),
        "--timeout",
        str(timeout_seconds),
        "--temporal-contract-path",
        str(shadow_path),
        "--temporal-role",
        SHADOW_ROLE,
        "--progress-path",
        str(progress),
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    stdout_tail = (completed.stdout or "")[-4000:]
    stderr_tail = (completed.stderr or "")[-2000:]
    report_json = None
    if completed.returncode == 0 and completed.stdout:
        try:
            # last JSON object in stdout
            text = completed.stdout.strip()
            # find last line that looks like JSON object
            for line in reversed(text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    report_json = json.loads(line)
                    break
            if report_json is None and text.startswith("{"):
                report_json = json.loads(text)
        except json.JSONDecodeError:
            report_json = None
    return {
        "attempted": True,
        "returncode": completed.returncode,
        "store_dir": str(out_store),
        "start_date": start_date,
        "end_date": end_date,
        "temporal_role": SHADOW_ROLE,
        "shadow_contract_sha256": contract.get("contract_sha256"),
        "progress_path": str(progress),
        "report": report_json,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "ok": completed.returncode == 0,
    }


def build_path_a_f1_oos_collect_replay(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
    collect: bool = False,
    oos_end_date: str = DEFAULT_OOS_END,
    oos_qualified_path: Path | None = None,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()

    # frozen-v1 remains sealed for formal final_oos
    frozen_v1 = load_temporal_partition_contract(str(root / DEFAULT_FROZEN_V1))
    final = next(r for r in frozen_v1["roles"] if r["name"] == "final_oos")
    formal_final_oos_still_sealed = (
        final.get("sealed") is True and list(final.get("permitted_operations") or []) == []
    )

    shadow_path = (root / DEFAULT_SHADOW_CONTRACT).resolve()
    if not shadow_path.is_file():
        raise PathAF1Error(f"shadow contract missing: {shadow_path}")
    shadow = load_temporal_partition_contract(str(shadow_path))
    assert_range_allowed(
        shadow, SHADOW_ROLE, OOS_COLLECTION_START, oos_end_date, "evaluate"
    )
    assert_range_allowed(
        shadow, SHADOW_ROLE, OOS_COLLECTION_START, oos_end_date, "collect"
    )

    candidate = _load_frozen_candidate(root)
    f0_pointer = _load_json(root / DEFAULT_F0_POINTER) or {}

    train_store = _market_session_bounds(
        root / "data/research_pit_store/current_pool_market_v2"
    )
    oos_store_path = root / DEFAULT_OOS_STORE
    oos_store_before = _market_session_bounds(oos_store_path)

    collect_result: dict[str, Any] | None = None
    if collect:
        collect_result = collect_path_a_oos_market(
            repo_root=root,
            start_date=OOS_COLLECTION_START,
            end_date=oos_end_date,
            store_dir=oos_store_path,
        )
    oos_store_after = _market_session_bounds(oos_store_path)

    train_qt = _audit_oos_trades(root / DEFAULT_TRAIN_QUALIFIED)
    # train file is train-only; oos counts expected 0
    oos_qt_path = (
        oos_qualified_path
        if oos_qualified_path is not None
        else (root / DEFAULT_OOS_QUALIFIED)
    )
    oos_qt = _audit_oos_trades(oos_qt_path)

    # Load OOS trades for zero-refit if present
    oos_trades: list[dict[str, Any]] = []
    oos_payload = _load_json(oos_qt_path)
    if oos_payload:
        oos_trades = [
            t
            for t in (oos_payload.get("qualified_trades") or [])
            if str(t.get("signal_date") or "")[:10] >= OOS_COLLECTION_START
        ]
        # Safety: never use train-window labels for OOS claim
        oos_trades = [
            t
            for t in oos_trades
            if str(t.get("signal_date") or "")[:10] > freeze.TRAIN_INCLUSIVE_SESSION_END
        ]

    replay = _zero_refit_replay(oos_trades, candidate["spec"], min_trades=1)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if formal_final_oos_still_sealed:
        warnings.append(
            {
                "code": "formal_final_oos_still_sealed",
                "detail": (
                    "frozen-v1 final_oos remains sealed empty-ops; F1 uses shadow "
                    "partition only and does not claim formal final-OOS"
                ),
            }
        )

    if int(oos_store_after.get("post_train_session_count") or 0) == 0:
        blockers.append(
            {
                "code": "oos_market_not_collected",
                "detail": (
                    f"OOS store has no sessions >= {OOS_COLLECTION_START}; "
                    "run with --collect after JIAOCH_TOKEN is available"
                ),
            }
        )

    if int(oos_qt.get("oos_trade_count") or 0) == 0:
        blockers.append(
            {
                "code": "oos_qualified_trades_missing",
                "detail": (
                    "no post-train qualified trades for zero-refit; "
                    f"expected optional artifact at {DEFAULT_OOS_QUALIFIED.as_posix()} "
                    "(generate via research-historical-sweep into path_a_oos cache, "
                    "never overwrite train qualified_hold5_stop5.json)"
                ),
            }
        )

    if not os.getenv("JIAOCH_TOKEN") and collect:
        blockers.append(
            {
                "code": "jiaoch_token_missing",
                "detail": "JIAOCH_TOKEN not set in environment for collect",
            }
        )

    evaluate_policy = {
        "name": "path_a_shadow_post_train_zero_refit/v1",
        "formal_final_oos": False,
        "uses_frozen_v1_final_oos": False,
        "shadow_role": SHADOW_ROLE,
        "shadow_contract_sha256": shadow.get("contract_sha256"),
        "train_remains_frozen": True,
        "train_inclusive_session_end": freeze.TRAIN_INCLUSIVE_SESSION_END,
        "oos_collection_start": OOS_COLLECTION_START,
        "refit_allowed": False,
        "parameter_search_allowed": False,
        "automatic_trading_allowed": False,
        "effective_strategy_claim_allowed": False,
        "notes": (
            "Shadow OOS evaluate may run collect+zero-refit diagnostics after train end. "
            "It does not unseal frozen-v1 final_oos and does not promote effective strategy."
        ),
    }

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_f1_oos_collect_replay",
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
            "f0_report_sha256": candidate["card"].get("f0_report_sha256"),
            "f0_pointer_sha256": f0_pointer.get("report_sha256"),
        },
        "evaluate_policy": evaluate_policy,
        "formal_final_oos_still_sealed": formal_final_oos_still_sealed,
        "train_market_store": train_store,
        "oos_market_store_before": oos_store_before,
        "oos_market_store_after": oos_store_after,
        "collect_attempt": collect_result,
        "train_qualified_audit": {
            "path": _rel(root, root / DEFAULT_TRAIN_QUALIFIED),
            "last_signal_date": train_qt.get("last_signal_date"),
            "oos_trade_count_in_train_file": train_qt.get("oos_trade_count"),
            "note": "train qualified file must not be overwritten by OOS collection",
        },
        "oos_qualified_audit": oos_qt,
        "zero_refit_replay": replay,
        "blockers": blockers,
        "warnings": warnings,
        "shadow_oos_collect_ok": int(oos_store_after.get("post_train_session_count") or 0)
        > 0,
        "shadow_oos_replay_ok": bool(replay.get("executed"))
        and int(replay.get("selected_trade_count") or 0) > 0,
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "ok": True,
        "notes": (
            "ok=true means F1 audit/collect/replay pipeline completed without crash. "
            "effective_strategy_found stays false. formal final-OOS remains sealed."
        ),
        "next_actions": [
            a
            for a in [
                (
                    "continue/resume OOS market collect into path_a_oos_market "
                    f"for {OOS_COLLECTION_START}..{oos_end_date}"
                    if int(oos_store_after.get("post_train_session_count") or 0) == 0
                    else None
                ),
                (
                    "generate OOS qualified trades into data/research_cache/path_a_oos/ "
                    "without touching train cache / train freeze"
                    if int(oos_qt.get("oos_trade_count") or 0) == 0
                    else None
                ),
                "re-run F1 zero-refit after OOS qualified trades exist",
                "do not unseal frozen-v1 casually; formal final-OOS still blocked",
                "never auto-trade; never merge OOS into train",
            ]
            if a
        ],
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_f1_oos_collect_replay(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAF1Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-f1-oos-collect-replay.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAF1Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_f1_oos_collect_replay",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "formal_final_oos_executable": False,
        "effective_strategy_found": False,
        "shadow_oos_collect_ok": report.get("shadow_oos_collect_ok"),
        "shadow_oos_replay_ok": report.get("shadow_oos_replay_ok"),
        "blocker_codes": [b.get("code") for b in report.get("blockers") or []],
        "candidate_id": (report.get("frozen_candidate") or {}).get("candidate_id"),
        "candidate_spec_sha256": (report.get("frozen_candidate") or {}).get(
            "candidate_spec_sha256"
        ),
        "oos_post_train_sessions": (
            (report.get("oos_market_store_after") or {}).get("post_train_session_count")
        ),
        "oos_qualified_trade_count": (
            (report.get("oos_qualified_audit") or {}).get("oos_trade_count")
        ),
        "zero_refit_selected_trades": (
            (report.get("zero_refit_replay") or {}).get("selected_trade_count")
        ),
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
    "DEFAULT_OOS_STORE",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_SHADOW_CONTRACT",
    "OOS_COLLECTION_START",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "SHADOW_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAF1Error",
    "build_path_a_f1_oos_collect_replay",
    "collect_path_a_oos_market",
    "write_path_a_f1_oos_collect_replay",
]
