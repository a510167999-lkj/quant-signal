"""Path A F0: formal final-OOS readiness for the frozen E4 primary candidate.

Freezes the E4 stability-ready strategy spec and audits whether sealed
final-OOS can actually be run under frozen-v1 + local artifacts.

Does NOT unseal partitions, does NOT claim effective strategy, does NOT trade.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal
from app.research_partitions import load_temporal_partition_contract

STAGE_GOAL_ID = "path-a-f0-oos-readiness/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A F0：冻结 E4 首选策略规格，审计 formal final-OOS 是否可执行；"
    "若数据/分区阻塞则明确阻断原因与采集计划；development_only 候选不等于有效策略；"
    "永不自动交易；训练窗仍不日更。"
)
REPORT_SCHEMA = "path-a-f0-oos-readiness-report/v1"
POINTER_SCHEMA = "path-a-f0-oos-readiness-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_TEMPORAL_CONTRACT = Path("data/research_partitions/frozen-v1.json")
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_E4_POINTER = Path(
    "data/research_runs/path_a_e4_stability_improve/LATEST.json"
)
DEFAULT_E4_HOLDOUT = Path(
    "data/research_runs/path_a_e4_stability_improve/holdout_check.json"
)
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_f0_oos_readiness")

# Frozen primary candidate from E4 (economic identity).
FROZEN_PRIMARY_CANDIDATE = {
    "candidate_id": "e4_primary_fn_ma60_bo_ex_gapdown_t3m2",
    "source_e4_label": "t3m2__fn__ma60_bo__x0__ex_gapdown",
    "spec": {
        "top_n": 3,
        "max_active_positions": 2,
        "symbol_cooldown_days": 5,
        "market_levels": ["favorable", "neutral"],
        "required_signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        "excluded_signal_tags": ["price_gap_down"],
        "hold_days": 5,
        "capital_model": "slot-daily",
        "exposure_multiplier": 1.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "annual_financing_rate_pct": 0.0,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    },
    "development_metrics_reference": {
        "rolling_1y_latest_return_pct": 82.94001456859164,
        "rolling_1y_latest_max_drawdown_pct": -12.182849856349042,
        "selected_trade_count": 81,
        "signal_days": 64,
        "rolling_both_pass_rate": 0.3645833333333333,
        "worst_window_mdd_pct": -21.536698189769332,
    },
}


class PathAF0Error(ValueError):
    """Raised when path-A F0 readiness audit fails closed."""


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
        raise PathAF0Error(
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


def _role_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    roles = contract.get("roles") or []
    out: dict[str, dict[str, Any]] = {}
    for role in roles:
        if isinstance(role, dict) and isinstance(role.get("name"), str):
            out[role["name"]] = role
    return out


def _audit_qualified_trades(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if payload is None:
        return {
            "present": False,
            "path": str(path),
            "blocker": "qualified_trades_missing",
        }
    trades = payload.get("qualified_trades") or []
    dates = sorted(
        {
            str(t.get("signal_date") or "")[:10]
            for t in trades
            if str(t.get("signal_date") or "")[:10]
        }
    )
    post_train = [d for d in dates if d > freeze.TRAIN_INCLUSIVE_SESSION_END]
    post_embargo_start = [d for d in dates if d >= "2026-07-04"]
    post_final_oos_start = [d for d in dates if d >= "2026-07-13"]
    return {
        "present": True,
        "path": str(path),
        "file_sha256": _file_sha256(path),
        "trade_count": len(trades),
        "signal_day_count": len(dates),
        "first_signal_date": dates[0] if dates else None,
        "last_signal_date": dates[-1] if dates else None,
        "post_train_end_signal_days": len(post_train),
        "post_embargo_start_signal_days": len(post_embargo_start),
        "post_final_oos_start_signal_days": len(post_final_oos_start),
        "summary_end_date": (payload.get("summary") or {}).get("end_date"),
    }


def _audit_e4_primary(repo: Path) -> dict[str, Any]:
    pointer = _load_json(repo / DEFAULT_E4_POINTER)
    if pointer is None:
        return {"present": False, "blocker": "e4_pointer_missing"}
    evidence_path = Path(str(pointer.get("evidence_path") or ""))
    report = _load_json(evidence_path) if evidence_path.is_file() else None
    holdout = _load_json(repo / DEFAULT_E4_HOLDOUT)
    primary_label = FROZEN_PRIMARY_CANDIDATE["source_e4_label"]
    matched = None
    if report:
        for row in report.get("stability_ready_variants") or []:
            if row.get("label") == primary_label:
                matched = row
                break
        if matched is None and (report.get("best_stability_ready") or {}).get(
            "label"
        ) == primary_label:
            matched = report.get("best_stability_ready")
    holdout_match = None
    if holdout:
        for row in holdout.get("results") or []:
            if row.get("label") == primary_label:
                holdout_match = row
                break
    return {
        "present": pointer.get("ok") is True,
        "pointer_path": _rel(repo, repo / DEFAULT_E4_POINTER),
        "e4_report_sha256": pointer.get("report_sha256"),
        "e4_stability_ready_count": pointer.get("stability_ready_count"),
        "primary_label_matched": matched is not None,
        "primary_from_e4": {
            "label": (matched or {}).get("label"),
            "latest": (matched or {}).get("latest"),
            "rolling_both_pass_rate": ((matched or {}).get("rolling") or {}).get(
                "both_pass_rate"
            ),
            "stability_ready_for_oos_planning": (matched or {}).get(
                "stability_ready_for_oos_planning"
            ),
        }
        if matched
        else None,
        "internal_holdout": {
            "present": holdout is not None,
            "path": _rel(repo, repo / DEFAULT_E4_HOLDOUT)
            if (repo / DEFAULT_E4_HOLDOUT).is_file()
            else None,
            "holdout_dual_pass_count": (holdout or {}).get("holdout_dual_pass_count"),
            "primary_holdout": (holdout_match or {}).get("holdout_slice")
            if holdout_match
            else None,
        },
    }


def build_path_a_f0_oos_readiness(
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
    contract_path = (root / DEFAULT_TEMPORAL_CONTRACT).resolve()
    if not contract_path.is_file():
        raise PathAF0Error(f"temporal contract missing: {contract_path}")

    contract = load_temporal_partition_contract(str(contract_path))
    roles = _role_map(contract if isinstance(contract, dict) else {})
    # load_temporal may return enriched structure; also read raw for sealed flags
    raw_contract = _load_json(contract_path) or {}
    raw_roles = _role_map(raw_contract)

    final = raw_roles.get("final_oos") or roles.get("final_oos") or {}
    embargo = raw_roles.get("embargo") or roles.get("embargo") or {}
    contaminated = (
        raw_roles.get("contaminated_diagnostic")
        or roles.get("contaminated_diagnostic")
        or {}
    )

    trades = _audit_qualified_trades((root / DEFAULT_QUALIFIED_TRADES).resolve())
    e4 = _audit_e4_primary(root)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # Partition blockers
    if final.get("sealed") is True and list(final.get("permitted_operations") or []) == []:
        blockers.append(
            {
                "code": "final_oos_sealed_no_ops",
                "detail": (
                    "frozen-v1 final_oos is sealed with empty permitted_operations; "
                    "backtest/evaluate on final_oos is forbidden until a later unseal policy"
                ),
            }
        )
    if embargo.get("sealed") is True and list(embargo.get("permitted_operations") or []) == []:
        warnings.append(
            {
                "code": "embargo_sealed_empty",
                "detail": (
                    f"embargo {embargo.get('start_date')}..{embargo.get('end_date')} "
                    "sealed with no operations"
                ),
            }
        )

    # Data blockers for true forward OOS
    if not trades.get("present"):
        blockers.append(
            {
                "code": "qualified_trades_missing",
                "detail": "no qualified trades artifact for strategy replay",
            }
        )
    else:
        if int(trades.get("post_final_oos_start_signal_days") or 0) == 0:
            blockers.append(
                {
                    "code": "no_final_oos_trade_rows",
                    "detail": (
                        "qualified trades have zero signal days on/after final_oos "
                        f"start {final.get('start_date')}; last signal="
                        f"{trades.get('last_signal_date')}"
                    ),
                }
            )
        if int(trades.get("post_train_end_signal_days") or 0) == 0:
            warnings.append(
                {
                    "code": "no_post_train_trade_rows",
                    "detail": (
                        "no signal days after train inclusive end "
                        f"{freeze.TRAIN_INCLUSIVE_SESSION_END}; cannot do post-freeze OOS "
                        "without new collection"
                    ),
                }
            )

    if e4.get("present") is not True:
        blockers.append(
            {
                "code": "e4_not_ready",
                "detail": "E4 stability-improve pointer missing or not ok",
            }
        )
    elif e4.get("primary_label_matched") is not True:
        blockers.append(
            {
                "code": "primary_candidate_not_in_e4_ready_set",
                "detail": (
                    f"frozen primary {FROZEN_PRIMARY_CANDIDATE['source_e4_label']} "
                    "not found among E4 stability_ready variants"
                ),
            }
        )
    elif not (
        (e4.get("primary_from_e4") or {}).get("stability_ready_for_oos_planning")
        is True
    ):
        blockers.append(
            {
                "code": "primary_not_stability_ready",
                "detail": "primary candidate is not stability_ready_for_oos_planning",
            }
        )

    # Research vs formal status
    formal_final_oos_executable = len(blockers) == 0
    # Path A may still use internal holdout evidence (already done in E4)
    internal_holdout_ok = bool(
        ((e4.get("internal_holdout") or {}).get("primary_holdout") or {}).get(
            "pass_both_50_15"
        )
    )

    candidate_freeze = {
        **FROZEN_PRIMARY_CANDIDATE,
        "candidate_spec_sha256": _sha(FROZEN_PRIMARY_CANDIDATE["spec"]),
        "train_window": freeze.train_window_freeze_descriptor(),
        "qualified_trades_file_sha256": trades.get("file_sha256"),
        "e4_report_sha256": e4.get("e4_report_sha256"),
    }

    collect_plan = {
        "purpose": "enable_forward_oos_after_train_freeze",
        "train_remains_frozen": True,
        "train_inclusive_session_end": freeze.TRAIN_INCLUSIVE_SESSION_END,
        "train_exclusive_end_date": freeze.TRAIN_EXCLUSIVE_END_DATE,
        "oos_collection_start": "2026-07-04",
        "oos_target_roles": {
            "embargo": {
                "start": embargo.get("start_date"),
                "end": embargo.get("end_date"),
                "note": "sealed; do not use for fitting; optional shadow only if policy allows later",
            },
            "final_oos": {
                "start": final.get("start_date"),
                "end": final.get("end_date"),
                "note": "requires unseal/evaluate policy + market/universe/risk/history rebuild",
            },
        },
        "required_artifacts_for_true_oos": [
            "trade calendar sessions covering oos window",
            "current_pool market generations for oos sessions",
            "universe/risk/history descriptors bound to oos as-of",
            "qualified trades regenerated or extended without refitting filters on oos labels",
            "temporal contract allowing evaluate on final_oos (policy change or frozen-v2)",
        ],
        "explicit_non_goals": [
            "do not append oos days into training set",
            "do not auto-trade",
            "do not unseal by editing frozen-v1 casually without audit",
        ],
    }

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_f0_oos_readiness",
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "temporal_contract": {
            "path": _rel(root, contract_path),
            "contract_sha256": raw_contract.get("contract_sha256")
            or contract.get("contract_sha256"),
            "roles": {
                name: {
                    "start_date": role.get("start_date"),
                    "end_date": role.get("end_date"),
                    "sealed": role.get("sealed"),
                    "permitted_operations": role.get("permitted_operations"),
                }
                for name, role in raw_roles.items()
            },
        },
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "frozen_primary_candidate": candidate_freeze,
        "e4_binding": e4,
        "qualified_trades_audit": trades,
        "blockers": blockers,
        "warnings": warnings,
        "formal_final_oos_executable": formal_final_oos_executable,
        "internal_holdout_available": internal_holdout_ok,
        "oos_authorized": formal_final_oos_executable,
        "effective_strategy_found": False,
        "candidate_status": (
            "development_stability_ready_with_internal_holdout"
            if internal_holdout_ok
            else "development_only"
        ),
        "collect_plan": collect_plan,
        "ok": True,
        "notes": (
            "ok=true means F0 readiness audit completed. "
            "effective_strategy_found remains false until formal final-OOS passes. "
            "Training cutoff stays frozen; OOS collection is a separate future step."
        ),
        "next_actions": (
            [
                "formal final-OOS is executable under current local artifacts; run evaluate-only pipeline",
                "do not refit parameters on OOS",
                "never auto-trade",
            ]
            if formal_final_oos_executable
            else [
                "blocked: collect/extend market+qualified trades after train end for final_oos window",
                "blocked: final_oos currently sealed with no permitted operations under frozen-v1",
                "keep candidate frozen; do not treat development/holdout as final validity",
                "optional later: audited frozen-v2 if partition policy must change",
                "never auto-trade; do not unfreeze training set",
            ]
        ),
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_f0_oos_readiness(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAF0Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-f0-oos-readiness.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAF0Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    # Also write a small immutable candidate freeze card for later OOS stages.
    card = {
        "schema": "path-a-frozen-strategy-candidate/v1",
        "candidate_id": report["frozen_primary_candidate"]["candidate_id"],
        "candidate_spec_sha256": report["frozen_primary_candidate"][
            "candidate_spec_sha256"
        ],
        "spec": report["frozen_primary_candidate"]["spec"],
        "qualified_trades_file_sha256": report["frozen_primary_candidate"].get(
            "qualified_trades_file_sha256"
        ),
        "e4_report_sha256": report["frozen_primary_candidate"].get("e4_report_sha256"),
        "f0_report_sha256": digest,
        "development_only": True,
        "effective_strategy": False,
        "automatic_trading_allowed": False,
    }
    card_path = output_root / "FROZEN_CANDIDATE.json"
    card_path.write_text(
        json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_f0_oos_readiness",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "formal_final_oos_executable": report.get("formal_final_oos_executable"),
        "oos_authorized": report.get("oos_authorized"),
        "effective_strategy_found": False,
        "blocker_codes": [b.get("code") for b in report.get("blockers") or []],
        "candidate_id": report["frozen_primary_candidate"]["candidate_id"],
        "candidate_spec_sha256": report["frozen_primary_candidate"][
            "candidate_spec_sha256"
        ],
        "candidate_status": report.get("candidate_status"),
        "internal_holdout_available": report.get("internal_holdout_available"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_candidate_path": str(card_path.resolve()),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "FROZEN_PRIMARY_CANDIDATE",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAF0Error",
    "build_path_a_f0_oos_readiness",
    "write_path_a_f0_oos_readiness",
]
