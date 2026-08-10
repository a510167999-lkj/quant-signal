"""Path A strategy matrix baseline (no TCB).

Uses existing development-only Factor V2 evaluation under the train-window
freeze. Does not claim formal authority, OOS validity, or production profile.
Never enables automatic trading.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal

STAGE_GOAL_ID = "path-a-strategy-matrix-baseline/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A（无 TCB）：在 train-locked 密封窗内，对已有 development Factor V2 "
    "evaluation 做 50%/15% 基线记分；development_only；不日更；不 formal 晋级；"
    "永不自动交易。目标是启动策略矩阵，不是盖 formal 章。"
)
REPORT_SCHEMA = "path-a-strategy-matrix-baseline-report/v1"
POINTER_SCHEMA = "path-a-strategy-matrix-baseline-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_EVALUATION_ARTIFACT = Path(
    "data/research_runs/audited_pit_factor_v2_development_evaluation_v1_development_4_retry_5/"
    "511f640c8dc80415b9c79d190f05fded479060ee41482f9a6d74280829cc0a0e.json"
)
DEFAULT_OUTPUT_ROOT = Path(
    "data/research_runs/path_a_strategy_matrix_baseline"
)


class PathAStrategyMatrixError(ValueError):
    """Raised when path-A baseline scoring fails closed."""


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
        raise PathAStrategyMatrixError(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _score_arm(arm: str, payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("raw_metrics")
    if type(metrics) is not dict:
        raise PathAStrategyMatrixError(f"arm {arm} missing raw_metrics")
    ret = metrics.get("rolling_1y_latest_return_pct")
    mdd = metrics.get("rolling_1y_latest_max_drawdown_pct")
    if type(ret) not in (int, float) or type(mdd) not in (int, float):
        raise PathAStrategyMatrixError(f"arm {arm} missing return/drawdown metrics")
    ret_f = float(ret)
    mdd_f = float(mdd)
    pass_return = ret_f >= goal.TARGET_ROLLING_12M_NET_RETURN_PCT
    pass_drawdown = abs(mdd_f) <= goal.TARGET_MAX_DRAWDOWN_PCT
    return {
        "arm": arm,
        "rolling_1y_latest_return_pct": ret_f,
        "rolling_1y_latest_max_drawdown_pct": mdd_f,
        "calmar_latest_12m": metrics.get("calmar_latest_12m"),
        "selected_trade_count": metrics.get("selected_trade_count"),
        "trade_win_rate_pct": metrics.get("trade_win_rate_pct"),
        "trade_profit_factor": metrics.get("trade_profit_factor"),
        "rolling_1y_latest_full_window": metrics.get("rolling_1y_latest_full_window"),
        "pass_return_50": pass_return,
        "pass_drawdown_15": pass_drawdown,
        "pass_both_50_15": pass_return and pass_drawdown,
        "target_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
    }


def build_path_a_strategy_matrix_baseline(
    *,
    repo_root: Path | None = None,
    evaluation_artifact: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()
    artifact = (
        root / (evaluation_artifact or DEFAULT_EVALUATION_ARTIFACT)
    ).resolve()
    if not artifact.is_file():
        raise PathAStrategyMatrixError(f"evaluation artifact missing: {artifact}")

    raw = artifact.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PathAStrategyMatrixError(f"invalid evaluation json: {exc}") from exc
    if type(payload) is not dict:
        raise PathAStrategyMatrixError("evaluation root must be object")

    sessions = payload.get("evaluation_sessions")
    if type(sessions) is not list or not sessions:
        raise PathAStrategyMatrixError("evaluation_sessions missing")
    freeze.assert_session_dates_in_train_window(
        [str(s) for s in sessions],
        label="evaluation_sessions",
    )
    if str(sessions[-1]) > freeze.TRAIN_INCLUSIVE_SESSION_END:
        raise PathAStrategyMatrixError("evaluation extends past sealed train end")

    scope = payload.get("scope") if type(payload.get("scope")) is dict else {}
    if scope.get("final_oos_consumed") is True or scope.get("embargo_consumed") is True:
        raise PathAStrategyMatrixError("evaluation consumed embargo/final-OOS")
    if scope.get("production_recommendation_eligible") is True:
        raise PathAStrategyMatrixError("evaluation claims production eligibility")

    arms_payload = payload.get("arms")
    arm_order = payload.get("arm_order")
    if type(arms_payload) is not dict or type(arm_order) is not list:
        raise PathAStrategyMatrixError("arms/arm_order missing")

    arm_scores = []
    for arm in arm_order:
        if arm not in arms_payload or type(arms_payload[arm]) is not dict:
            raise PathAStrategyMatrixError(f"arm missing: {arm}")
        arm_scores.append(_score_arm(str(arm), arms_payload[arm]))

    any_pass = any(row["pass_both_50_15"] for row in arm_scores)
    best = max(
        arm_scores,
        key=lambda row: (
            row["pass_both_50_15"],
            row["pass_return_50"],
            row["pass_drawdown_15"],
            row["rolling_1y_latest_return_pct"],
        ),
    )

    try:
        rel_path = str(artifact.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_path = str(artifact)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_development_matrix",
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "daily_incremental_sync_required": False,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_required": False,
        "tcb_used": False,
        "final_oos_consumed": False,
        "embargo_consumed": False,
        "source_evaluation": {
            "path": rel_path,
            "file_sha256": file_sha,
            "schema_version": payload.get("schema_version"),
            "temporal_role": payload.get("temporal_role"),
            "session_count": len(sessions),
            "first_session": sessions[0],
            "last_session": sessions[-1],
            "scope": {
                "development_only": scope.get("development_only"),
                "point_in_time": scope.get("point_in_time"),
                "production_recommendation_eligible": scope.get(
                    "production_recommendation_eligible"
                ),
                "eligible_for_profile_registration": scope.get(
                    "eligible_for_profile_registration"
                ),
            },
        },
        "targets": {
            "rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
            "max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        },
        "arm_scores": arm_scores,
        "any_arm_passes_50_15": any_pass,
        "best_arm_by_heuristic": best["arm"],
        "effective_strategy_found": False,
        "ok": True,
        "notes": (
            "ok=true means baseline matrix scored under path A. "
            "effective_strategy_found remains false until an arm passes 50/15 "
            "and later survives independent OOS. This is not a formal claim."
        ),
        "next_actions": [
            "在 path A 下继续变体/过滤矩阵（降回撤优先：目标 MDD<=15，同时守住收益）",
            "候选出现后做 rolling 稳定性统计，再考虑 embargo/final-OOS",
            "不补 TCB，除非明确要 formal 权威晋级",
            "永不自动交易",
        ],
    }
    # effective_strategy_found only if both targets pass (still development_only)
    unsigned["effective_strategy_found"] = False
    if any_pass:
        unsigned["notes"] += (
            " At least one arm passes 50/15 on this development evaluation "
            "slice; still not OOS-validated."
        )
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_strategy_matrix_baseline(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAStrategyMatrixError("refuse to write non-ok report")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-strategy-matrix-baseline.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAStrategyMatrixError("CAS collision with different content")
    if not path.exists():
        path.write_bytes(raw)
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_development_matrix",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "any_arm_passes_50_15": report.get("any_arm_passes_50_15"),
        "effective_strategy_found": False,
        "best_arm_by_heuristic": report.get("best_arm_by_heuristic"),
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
    "DEFAULT_EVALUATION_ARTIFACT",
    "DEFAULT_OUTPUT_ROOT",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "PathAStrategyMatrixError",
    "build_path_a_strategy_matrix_baseline",
    "write_path_a_strategy_matrix_baseline",
]
