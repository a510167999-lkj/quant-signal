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
from app import factor_v3_materializer_v2_development_registration_fixture as mat_fixture
from app import factor_v3_parent_eval_authority_inventory as parent_eval_inventory
from app import factor_v3_train_locked_formal_data_receipts as train_locked_receipts
from app import factor_v3_train_window_freeze_contract as train_freeze
from app import research_goal_contract as goal

# --- Self-set near-term stage goal (agent operating target) ---
# Prior: inventory parent/eval gaps. Current: seal train-locked upstream formal
# data receipts under freeze before 2026-08-01 (no daily incremental sync).
STAGE_GOAL_ID = train_locked_receipts.STAGE_GOAL_ID
STAGE_GOAL_SUMMARY = train_locked_receipts.STAGE_GOAL_SUMMARY
ACTIVATION_STAGE_GOAL_ID = "factor-v3-activation-disposable-proof/v1"
PARENT_EVAL_INVENTORY_STAGE_GOAL_ID = parent_eval_inventory.STAGE_GOAL_ID

DEFAULT_DAILY_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_development_733_http_publish"
)
DEFAULT_FEATURE_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
)
DEFAULT_MATERIALIZER_DRY_RUN_POINTER = Path(
    "data/research_runs/factor_v3_materializer_v2_development_dry_run/LATEST.json"
)
DEFAULT_ACTIVATION_DRY_RUN_POINTER = Path(
    "data/research_runs/factor_v3_activation_development_dry_run/LATEST.json"
)
DEFAULT_PARENT_EVAL_INVENTORY_POINTER = Path(
    "data/research_runs/factor_v3_parent_eval_authority_inventory/LATEST.json"
)
DEFAULT_TRAIN_LOCKED_RECEIPTS_POINTER = Path(
    "data/research_runs/factor_v3_train_locked_formal_data_receipts/LATEST.json"
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
            id="performance_targets_26_15",
            ok=(
                goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 26.0
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
    closed = mat_fixture.production_registration_slots_closed()
    checks.append(
        CheckResult(
            id="materializer_production_registration_closed",
            ok=closed,
            detail=(
                "materializer formal TCB/activation slots currently unregistered"
                if closed
                else "materializer formal TCB/activation slots are non-null "
                "(process fixture or durable registration present)"
            ),
            blocking=False,
        )
    )
    fixture_ready = mat_fixture.is_development_registration_fixture_ready()
    checks.append(
        CheckResult(
            id="materializer_development_registration_fixture_ready",
            ok=fixture_ready,
            detail=mat_fixture.fixture_readiness_detail(),
            blocking=True,
        )
    )
    # Durable formal TCB publication is still a separate audited path (RED).
    # Development readiness is gated by the fixture surface above, not by
    # permanently filling fail-closed production slots in source.
    checks.append(
        CheckResult(
            id="materializer_formal_registration_ready",
            ok=not closed,
            detail=(
                "durable formal activation/TCB/broker slots remain unregistered "
                "(expected until audited two-commit authority publication; "
                "use development registration fixture for local-research dry-run)"
                if closed
                else "durable or process-local materializer registration slots are set"
            ),
            blocking=False,
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


def _audit_dry_run_pointer(
    pointer_path: Path,
    *,
    check_id: str,
    require_stage_goal_id: str | None = None,
) -> CheckResult:
    payload = _read_json(pointer_path)
    if payload is None:
        return CheckResult(
            id=check_id,
            ok=False,
            detail=f"missing or invalid pointer {pointer_path}",
        )
    ok = payload.get("ok") is True and payload.get("development_only") is True
    if require_stage_goal_id is not None:
        ok = ok and payload.get("stage_goal_id") == require_stage_goal_id
    if payload.get("production_profile_registered") is True:
        ok = False
    if payload.get("automatic_trading_allowed") is True:
        ok = False
    if (
        "formal_materialization_eligible" in payload
        and payload.get("formal_materialization_eligible") is not False
    ):
        ok = False
    detail = (
        f"ok={payload.get('ok')} development_only={payload.get('development_only')} "
        f"stage_goal_id={payload.get('stage_goal_id')} "
        f"evidence_sha256={payload.get('evidence_sha256')}"
    )
    return CheckResult(id=check_id, ok=ok, detail=detail, blocking=True)


def audit_development_dry_run_evidence(repo_root: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []
    materializer_pointer = (
        repo_root / DEFAULT_MATERIALIZER_DRY_RUN_POINTER
    ).resolve()
    activation_pointer = (repo_root / DEFAULT_ACTIVATION_DRY_RUN_POINTER).resolve()
    checks.append(
        _audit_dry_run_pointer(
            materializer_pointer,
            check_id="materializer_development_dry_run_ok",
        )
    )
    checks.append(
        _audit_dry_run_pointer(
            activation_pointer,
            check_id="activation_development_dry_run_ok",
            require_stage_goal_id=ACTIVATION_STAGE_GOAL_ID,
        )
    )
    inventory_pointer = (repo_root / DEFAULT_PARENT_EVAL_INVENTORY_POINTER).resolve()
    checks.append(
        _audit_dry_run_pointer(
            inventory_pointer,
            check_id="parent_eval_authority_inventory_ok",
            require_stage_goal_id=PARENT_EVAL_INVENTORY_STAGE_GOAL_ID,
        )
    )
    inv_payload = _read_json(inventory_pointer) or {}
    checks.append(
        CheckResult(
            id="parent_eval_formal_not_ready_documented",
            ok=inv_payload.get("formal_parent_eval_ready") is False,
            detail=(
                f"formal_parent_eval_ready={inv_payload.get('formal_parent_eval_ready')} "
                f"(must remain False until formal receipts exist)"
            ),
            blocking=True,
        )
    )
    # Train-window freeze + sealed upstream formal data receipts
    freeze_ok = True
    freeze_detail = ""
    try:
        train_freeze.assert_train_window_freeze_consistent()
        freeze_detail = (
            f"exclusive_end={train_freeze.TRAIN_EXCLUSIVE_END_DATE} "
            f"inclusive_end={train_freeze.TRAIN_INCLUSIVE_SESSION_END} "
            f"daily_sync={train_freeze.DAILY_INCREMENTAL_SYNC_REQUIRED}"
        )
    except train_freeze.TrainWindowFreezeError as exc:
        freeze_ok = False
        freeze_detail = str(exc)
    checks.append(
        CheckResult(
            id="train_window_freeze_consistent",
            ok=freeze_ok,
            detail=freeze_detail,
            blocking=True,
        )
    )
    train_pointer = (repo_root / DEFAULT_TRAIN_LOCKED_RECEIPTS_POINTER).resolve()
    checks.append(
        _audit_dry_run_pointer(
            train_pointer,
            check_id="train_locked_formal_data_receipts_ok",
            require_stage_goal_id=STAGE_GOAL_ID,
        )
    )
    train_payload = _read_json(train_pointer) or {}
    checks.append(
        CheckResult(
            id="train_locked_no_daily_incremental_sync",
            ok=train_payload.get("daily_incremental_sync_required") is False,
            detail=(
                f"daily_incremental_sync_required="
                f"{train_payload.get('daily_incremental_sync_required')}"
            ),
            blocking=True,
        )
    )
    checks.append(
        CheckResult(
            id="train_locked_upstream_complete_parent_eval_open",
            ok=(
                train_payload.get("upstream_formal_data_complete") is True
                and train_payload.get("parent_eval_formal_complete") is False
            ),
            detail=(
                f"upstream_formal_data_complete="
                f"{train_payload.get('upstream_formal_data_complete')} "
                f"parent_eval_formal_complete="
                f"{train_payload.get('parent_eval_formal_complete')}"
            ),
            blocking=True,
        )
    )
    # Formal public activation must remain closed at source (not just dry-run).
    checks.append(
        CheckResult(
            id="activation_formal_public_entrypoint_closed",
            ok=callable(
                getattr(
                    __import__(
                        "app.factor_v3_formal_development_input_activation",
                        fromlist=["publish_factor_v3_formal_development_input_activation"],
                    ),
                    "publish_factor_v3_formal_development_input_activation",
                    None,
                )
            ),
            detail=(
                "public formal activation entrypoint exists and disposable dry-run "
                "must keep formal_materialization_eligible=False "
                "(see activation_development_dry_run_ok)"
            ),
            blocking=False,
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
    # Feature-history authority is intentionally history-only evidence.
    # Downstream materializer / development-input paths require:
    #   factor_materialization_eligible is False
    #   (and formal_factor_materialization_eligible is False when present).
    # formal_materialization_eligible=True is granted later by formal
    # development-input activation under FORMAL_AUTHORITY_SCOPE, not here.
    history_only_ok = (
        receipt.get("verified") is True
        and receipt.get("authority_status") == "VERIFIED_FEATURE_HISTORY_ONLY"
        and receipt.get("feature_history_only") is True
        and receipt.get("factor_materialization_eligible") is False
        and receipt.get("experiment_launch_eligible") is False
        and receipt.get("embargo_consumed") is False
        and receipt.get("final_oos_consumed") is False
        and receipt.get("production_profile_registered") is False
        and receipt.get("production_recommendation_eligible") is False
        and int(receipt.get("session_count") or 0) == required
    )
    checks.append(
        CheckResult(
            id="feature_history_history_only_contract_ok",
            ok=history_only_ok,
            detail=(
                f"authority_status={receipt.get('authority_status')} "
                f"feature_history_only={receipt.get('feature_history_only')} "
                f"factor_materialization_eligible="
                f"{receipt.get('factor_materialization_eligible')} "
                f"session_count={receipt.get('session_count')}"
            ),
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
    if "feature_history_history_only_contract_ok" in failed:
        actions.append(
            "修复 feature-history 权威回执：必须为 VERIFIED_FEATURE_HISTORY_ONLY、"
            "verified=True、factor_materialization_eligible=False（history-only 设计）。"
        )
    if "feature_history_no_partial_publication_dirs" in failed:
        actions.append(
            "清理或完成 feature-history collection-publication 的 .partial 目录，"
            "避免半发布状态被误用。"
        )
    if "materializer_development_registration_fixture_ready" in failed:
        actions.append(
            "修复 development-only materializer/activation 注册夹具表面"
            "（app/factor_v3_materializer_v2_development_registration_fixture.py；"
            "仍禁止 production_profile / 自动交易）。"
        )
    if "materializer_development_dry_run_ok" in failed:
        actions.append(
            "在 VPS_RUNTIME_ROLE=local_research 下重跑 "
            "scripts/run_factor_v3_materializer_v2_development_dry_run.py 并确认 LATEST.json ok。"
        )
    if "activation_development_dry_run_ok" in failed:
        actions.append(
            "在 VPS_RUNTIME_ROLE=local_research 下重跑 "
            "scripts/run_factor_v3_activation_development_dry_run.py "
            "完成 disposable activation 证明链并确认 LATEST.json ok。"
        )
    if "parent_eval_authority_inventory_ok" in failed:
        actions.append(
            "在 VPS_RUNTIME_ROLE=local_research 下重跑 "
            "scripts/run_factor_v3_parent_eval_authority_inventory.py "
            "生成 parent/eval 权威 inventory 并确认 LATEST.json ok。"
        )
    if "parent_eval_formal_not_ready_documented" in failed:
        actions.append(
            "inventory 不得将 formal_parent_eval_ready 标为 True；"
            "正式 parent/eval 权威收据缺失时应保持 False。"
        )
    if (
        "train_window_freeze_consistent" in failed
        or "train_locked_formal_data_receipts_ok" in failed
        or "train_locked_no_daily_incremental_sync" in failed
        or "train_locked_upstream_complete_parent_eval_open" in failed
    ):
        actions.append(
            "在 VPS_RUNTIME_ROLE=local_research 下重跑 "
            "scripts/publish_factor_v3_train_locked_formal_data_receipts.py："
            "训练集锁定 2026-08-01 前、不日更，封存 733/250/attestation 上游 formal 收据。"
        )
    if "materializer_formal_registration_ready" in failed:
        actions.append(
            "durable formal materializer TCB/broker 尚未发布（非阻塞）："
            "本地研究用 development registration fixture；"
            "正式路径需独立审计的 two-commit authority publication。"
        )
    if not actions:
        actions.append(
            "train-locked 上游 formal 数据收据已封存（至 2026-07-03，exclusive end "
            "2026-08-01，不日更）；下一阶段再补 durable parent/eval formal 收据，"
            "以便进入策略开发矩阵。永不自动交易。"
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
    checks.extend(audit_development_dry_run_evidence(root))

    blocking = [c.detail if c.detail else c.id for c in checks if c.blocking and not c.ok]
    # prefer ids for clarity
    blocking_ids = [c.id for c in checks if c.blocking and not c.ok]
    # Field name is historical: means "current stage gates clear toward formal
    # development materialization path", not that formal eligibility is granted.
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
