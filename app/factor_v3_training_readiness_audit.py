"""Development-only audit of Factor V3 path readiness toward the research goal.

This module never starts training, never opens embargo/final-OOS, and never
registers production profiles. It only inspects repository contracts and local
research-run artifacts to answer: what still blocks a formal development
training matrix?
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app import audited_pit_factor_v3_formal_materializer_v2_authority as materializer_auth
from app import audited_pit_factor_v3_formal_materializer_v2_contract as materializer
from app import factor_authority_compound_contract_v2 as compound
from app import factor_authority_compound_native_client as compound_native
from app import research_goal_contract as goal

# --- Self-set near-term stage goal (agent operating target) ---
STAGE_GOAL_ID = "factor-v3-dev-matrix-readiness/v1"
STAGE_GOAL_SUMMARY = (
    "在 development-only 边界内，打通 Jiaoch 正式训练集物化前置："
    "daily-basic 733 日完整权威 + feature-history 250 预窗权威 + "
    "compound/activation 正式门控 + materializer v2 正式注册路径；"
    "不碰 embargo/final-OOS/生产/自动交易。"
)

DEFAULT_DAILY_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_development_733_http_live"
)
DEFAULT_FEATURE_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
)

JIAOCH_ENV_CANDIDATES = (
    "JIAOCH_POINTS_TOKEN",
    "JIAOCH_TOKEN",
    "TUSHARE_TOKEN",
    "TS_TOKEN",
    "POINTS_TOKEN",
)


@dataclass
class CheckResult:
    id: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass
class ReadinessReport:
    stage_goal_id: str
    stage_goal_summary: str
    research_goal: dict[str, Any]
    checks: list[CheckResult] = field(default_factory=list)
    blocking_gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    ready_for_formal_development_materialization: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _env_token_present() -> tuple[bool, str]:
    present = [name for name in JIAOCH_ENV_CANDIDATES if os.getenv(name)]
    env_file = Path(".env")
    names_in_dotenv: list[str] = []
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name = line.split("=", 1)[0].strip()
                if name in JIAOCH_ENV_CANDIDATES:
                    names_in_dotenv.append(name)
        except OSError:
            pass
    if present:
        return True, f"process env has {present}"
    if names_in_dotenv:
        return True, f".env declares {names_in_dotenv} (value not inspected)"
    return False, "no Jiaoch/Tushare token env candidate found in process or .env"


def audit_research_goal_contract() -> list[CheckResult]:
    checks: list[CheckResult] = []
    descriptor = goal.research_goal_descriptor()
    checks.append(
        CheckResult(
            id="research_goal_present",
            ok=descriptor.get("schema") == goal.RESEARCH_GOAL_SCHEMA,
            detail=str(descriptor.get("summary", "")),
            blocking=True,
        )
    )
    checks.append(
        CheckResult(
            id="market_scope_excludes_bse_star",
            ok=(
                list(goal.DOWNSTREAM_ELIGIBLE_SEGMENTS)
                == ["SSE_MAIN", "SZSE_CHINEXT", "SZSE_MAIN"]
                and list(goal.DOWNSTREAM_EXCLUDED_SEGMENTS) == ["BSE", "SSE_STAR"]
            ),
            detail=(
                f"eligible={goal.DOWNSTREAM_ELIGIBLE_SEGMENTS} "
                f"excluded={goal.DOWNSTREAM_EXCLUDED_SEGMENTS}"
            ),
        )
    )
    checks.append(
        CheckResult(
            id="performance_targets_50_15",
            ok=(
                goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 50.0
                and goal.TARGET_MAX_DRAWDOWN_PCT == 15.0
            ),
            detail=(
                f"return>={goal.TARGET_ROLLING_12M_NET_RETURN_PCT} "
                f"mdd<={goal.TARGET_MAX_DRAWDOWN_PCT}"
            ),
        )
    )
    checks.append(
        CheckResult(
            id="automatic_trading_disabled",
            ok=goal.AUTOMATIC_TRADING_ALLOWED is False,
            detail=f"automatic_trading_allowed={goal.AUTOMATIC_TRADING_ALLOWED}",
        )
    )
    return checks


def audit_code_registration_gates() -> list[CheckResult]:
    checks: list[CheckResult] = []
    checks.append(
        CheckResult(
            id="compound_production_registration_closed",
            ok=(
                compound.REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH is None
                and compound_native.REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256 is None
                and compound_native.HANDOFF_READY == 0
            ),
            detail=(
                "compound production registry intentionally closed "
                f"(handoff_ready={compound_native.HANDOFF_READY})"
            ),
            blocking=False,
        )
    )
    closed = (
        materializer_auth.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256 is None
        and materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256 is None
        and materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
    )
    checks.append(
        CheckResult(
            id="materializer_production_registration_closed",
            ok=closed,
            detail="materializer formal TCB/activation slots currently unregistered",
            blocking=False,
        )
    )
    checks.append(
        CheckResult(
            id="materializer_formal_registration_ready",
            ok=not closed,
            detail=(
                "formal activation/TCB/broker slots must be registered for "
                "development materialization (still not production)"
            ),
            blocking=True,
        )
    )
    checks.append(
        CheckResult(
            id="materializer_calendar_contract_733",
            ok=dict(materializer.EXACT_CALENDAR_COUNTS)
            == {
                "prewindow_session_count": 250,
                "development_session_count": 483,
                "all_market_session_count": 733,
                "source_date_count": 732,
            },
            detail=str(dict(materializer.EXACT_CALENDAR_COUNTS)),
        )
    )
    checks.append(
        CheckResult(
            id="materializer_segments_match_research_goal",
            ok=(
                materializer.DOWNSTREAM_ELIGIBLE_SEGMENTS
                == goal.DOWNSTREAM_ELIGIBLE_SEGMENTS
            ),
            detail=str(materializer.DOWNSTREAM_ELIGIBLE_SEGMENTS),
        )
    )
    return checks


def audit_jiaoch_credentials() -> list[CheckResult]:
    ok, detail = _env_token_present()
    return [
        CheckResult(
            id="jiaoch_credential_available",
            ok=ok,
            detail=detail,
            blocking=True,
        )
    ]


def audit_daily_basic_run(run_root: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    state_path = run_root / "state.json"
    state = _read_json(state_path)
    if state is None:
        checks.append(
            CheckResult(
                id="daily_basic_state_present",
                ok=False,
                detail=f"missing or invalid {state_path}",
            )
        )
        return checks
    status = state.get("status")
    completed = int(state.get("completed_session_count") or 0)
    required = int(materializer.EXACT_CALENDAR_COUNTS["all_market_session_count"])
    checks.append(
        CheckResult(
            id="daily_basic_state_present",
            ok=True,
            detail=f"schema={state.get('schema')} status={status}",
            blocking=False,
        )
    )
    checks.append(
        CheckResult(
            id="daily_basic_run_succeeded",
            ok=status == "succeeded" or status == "verified",
            detail=f"status={status}",
        )
    )
    checks.append(
        CheckResult(
            id="daily_basic_session_coverage_733",
            ok=completed >= required,
            detail=f"completed_session_count={completed} required={required}",
        )
    )
    return checks


def audit_feature_history_run(run_root: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    state_path = run_root / "state.json"
    state = _read_json(state_path)
    if state is None:
        checks.append(
            CheckResult(
                id="feature_history_state_present",
                ok=False,
                detail=f"missing or invalid {state_path}",
            )
        )
        return checks
    status = state.get("status")
    receipt = state.get("receipt") if isinstance(state.get("receipt"), dict) else {}
    completed = int(state.get("completed_session_count") or 0)
    required = int(materializer.EXACT_CALENDAR_COUNTS["prewindow_session_count"])
    publication = (
        state.get("collection_publication")
        if isinstance(state.get("collection_publication"), dict)
        else {}
    )
    checks.append(
        CheckResult(
            id="feature_history_state_present",
            ok=True,
            detail=f"schema={state.get('schema')} status={status}",
            blocking=False,
        )
    )
    checks.append(
        CheckResult(
            id="feature_history_prewindow_250",
            ok=completed >= required,
            detail=f"completed_session_count={completed} required={required}",
        )
    )
    checks.append(
        CheckResult(
            id="feature_history_receipt_verified",
            ok=receipt.get("verified") is True,
            detail=(
                f"verified={receipt.get('verified')} "
                f"authority_status={receipt.get('authority_status')}"
            ),
        )
    )
    checks.append(
        CheckResult(
            id="feature_history_formal_materialization_eligible",
            ok=receipt.get("formal_materialization_eligible") is True,
            detail=f"formal_materialization_eligible={receipt.get('formal_materialization_eligible')}",
        )
    )
    checks.append(
        CheckResult(
            id="feature_history_publication_durable",
            ok=publication.get("publication_status")
            == "DURABLE_POSTVERIFIED_AND_RETURNED",
            detail=f"publication_status={publication.get('publication_status')}",
            blocking=False,
        )
    )
    # partial dirs indicate incomplete publish surface
    collection_root = run_root / "collection-publication"
    partials = []
    if collection_root.is_dir():
        partials = [
            p.name for p in collection_root.iterdir() if ".partial" in p.name.lower()
        ]
    checks.append(
        CheckResult(
            id="feature_history_no_partial_publication_dirs",
            ok=not partials,
            detail=(
                "no partial publication directories"
                if not partials
                else f"partial dirs present: {partials[:5]}"
            ),
        )
    )
    return checks


def build_next_actions(checks: list[CheckResult]) -> list[str]:
    failed = {c.id for c in checks if c.blocking and not c.ok}
    actions: list[str] = []
    if "jiaoch_credential_available" in failed:
        actions.append(
            "配置 Jiaoch/Tushare token 到 .env（如 JIAOCH_POINTS_TOKEN / TUSHARE_TOKEN），"
            "不要把 token 写入 git。"
        )
    if "daily_basic_run_succeeded" in failed or "daily_basic_session_coverage_733" in failed:
        actions.append(
            "修复并重跑 factor-v3 daily-basic 正式采集到 733/733 成功态"
            "（当前 run 为 failed 且仅 26 日）。"
        )
    if "feature_history_formal_materialization_eligible" in failed:
        actions.append(
            "feature-history 目前仅 VERIFIED_FEATURE_HISTORY_ONLY；"
            "需补齐 formal materialization 资格链后再放行。"
        )
    if "feature_history_no_partial_publication_dirs" in failed:
        actions.append(
            "清理或完成 feature-history collection-publication 的 .partial 目录，"
            "避免半发布状态被误用。"
        )
    if "materializer_formal_registration_ready" in failed:
        actions.append(
            "设计 development-only 的 materializer/activation 正式注册夹具"
            "（仍禁止 production_profile / 自动交易）。"
        )
    if not actions:
        actions.append(
            "前置已齐：可启动 development-only formal materialization 试跑并保留证据。"
        )
    actions.append(
        "在未通过 final-OOS 与 50%/15% 门槛前，禁止注册生产 profile、禁止自动下单。"
    )
    return actions


def run_factor_v3_training_readiness_audit(
    *,
    repo_root: Path | None = None,
    daily_run: Path | None = None,
    feature_run: Path | None = None,
) -> ReadinessReport:
    root = (repo_root or Path.cwd()).resolve()
    daily = (root / (daily_run or DEFAULT_DAILY_RUN)).resolve()
    feature = (root / (feature_run or DEFAULT_FEATURE_RUN)).resolve()

    checks: list[CheckResult] = []
    checks.extend(audit_research_goal_contract())
    checks.extend(audit_code_registration_gates())
    checks.extend(audit_jiaoch_credentials())
    checks.extend(audit_daily_basic_run(daily))
    checks.extend(audit_feature_history_run(feature))

    blocking = [c.detail if c.detail else c.id for c in checks if c.blocking and not c.ok]
    # prefer ids for clarity
    blocking_ids = [c.id for c in checks if c.blocking and not c.ok]
    ready = not blocking_ids
    report = ReadinessReport(
        stage_goal_id=STAGE_GOAL_ID,
        stage_goal_summary=STAGE_GOAL_SUMMARY,
        research_goal=goal.research_goal_descriptor(),
        checks=checks,
        blocking_gaps=blocking_ids,
        next_actions=build_next_actions(checks),
        ready_for_formal_development_materialization=ready,
    )
    return report


def render_markdown(report: ReadinessReport) -> str:
    lines = [
        f"# Factor V3 Training Readiness — `{report.stage_goal_id}`",
        "",
        f"**Stage goal:** {report.stage_goal_summary}",
        "",
        f"**Ready for formal development materialization:** "
        f"`{report.ready_for_formal_development_materialization}`",
        "",
        "## Research goal",
        "",
        f"- Data: `{report.research_goal['data_source_policy']}`",
        f"- Eligible: `{report.research_goal['downstream_eligible_segments']}`",
        f"- Excluded: `{report.research_goal['downstream_excluded_segments']}`",
        f"- Targets: return ≥ {report.research_goal['target_rolling_12m_net_return_pct']}% , "
        f"MDD ≤ {report.research_goal['target_max_drawdown_pct']}%",
        "",
        "## Checks",
        "",
        "| id | ok | blocking | detail |",
        "|----|----|----------|--------|",
    ]
    for check in report.checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(
            f"| `{check.id}` | {'✅' if check.ok else '❌'} | "
            f"{'yes' if check.blocking else 'no'} | {detail} |"
        )
    lines.extend(
        [
            "",
            "## Blocking gaps",
            "",
        ]
    )
    if report.blocking_gaps:
        for gap in report.blocking_gaps:
            lines.append(f"- `{gap}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Next actions", ""])
    for action in report.next_actions:
        lines.append(f"1. {action}")
    lines.append("")
    return "\n".join(lines)
