"""Inventory local parent-source / evaluation / activation binding surfaces.

Stage goal: factor-v3-parent-eval-real-authority-inventory/v1

This module never publishes formal authority, never opens embargo/final-OOS,
and never enables trading. It only scans known local research paths and
classifies activation binding readiness for the next formal activation stage.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app import audited_pit_factor_v3_formal_materializer_v2_authority as materializer_auth
from app import factor_authority_compound_contract_v2 as compound
from app import factor_authority_compound_native_client as compound_native
from app import factor_v3_formal_development_input_activation as activation
from app import factor_v3_parent_source_development_authority as parent_auth
from app import research_goal_contract as goal

STAGE_GOAL_ID = "factor-v3-parent-eval-real-authority-inventory/v1"
STAGE_GOAL_SUMMARY = (
    "盘点 parent-source / factor-v2 evaluation 与 formal activation 绑定面："
    "记录本地真实工件路径与哈希，交叉对照 daily-basic 733 / feature-history 250 / "
    "attestation，列出 formal 权威缺口；不宣称 formal_materialization_eligible，"
    "不注册 production profile，不自动交易。"
)
INVENTORY_SCHEMA = "factor-v3-parent-eval-authority-inventory/v1"
POINTER_SCHEMA = "factor-v3-parent-eval-authority-inventory-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

DEFAULT_DAILY_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_development_733_http_publish"
)
DEFAULT_FEATURE_RUN = Path(
    "data/research_runs/audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
)
DEFAULT_ATTESTATION_ROOT = Path(
    "data/research_artifacts/factor_v3_feature_history_frozen_source_attestation_v6_http"
)
DEFAULT_EVALUATION_ARTIFACT = Path(
    "data/research_runs/audited_pit_factor_v2_development_evaluation_v1_development_4_retry_5/"
    "511f640c8dc80415b9c79d190f05fded479060ee41482f9a6d74280829cc0a0e.json"
)
DEFAULT_PARENT_REPLAY_ARTIFACT = Path(
    "data/research_runs/audited_pit_factor_v2_full_parent_replay_gate_v2_development_4/"
    "bd3a6e6b7e01bd33f183838df99713469ad2efd6c4e5f4fcdf71cc037e75704d.json"
)
DEFAULT_PARENT_SQLITE = Path(
    "data/research_runs/audited_pit_factor_v2_full_parent_replay_gate_v2_development_4/"
    "b8212df7abb4ca7eb9a1d2e86138f3a522339c5628c3248286d6e3b6aa0f859b.sqlite3"
)
DEFAULT_EVAL_PARENT_DATASET = Path(
    "data/research_runs/"
    "audited_pit_shallow_gbdt_training_dataset_v1_development_4_factor_v2_evaluation_parent_v1"
)
DEFAULT_MATERIALIZER_DRY_RUN = Path(
    "data/research_runs/factor_v3_materializer_v2_development_dry_run/LATEST.json"
)
DEFAULT_ACTIVATION_DRY_RUN = Path(
    "data/research_runs/factor_v3_activation_development_dry_run/LATEST.json"
)
DEFAULT_OUTPUT_ROOT = Path(
    "data/research_runs/factor_v3_parent_eval_authority_inventory"
)

# Formal activation requires these physical authority receipts (not research
# evaluation artifacts). Inventory marks them missing until formal producers run.
FORMAL_ACTIVATION_AUTHORITY_RECEIPT_ROLES = (
    "parent_source_authority_receipt",
    "parent_source_terminal_epoch_receipt",
    "factor_v2_evaluation_authority_receipt",
    "candidate_development_input_publication",
    "global_attempt_ledger_epoch_chain",
)


@dataclass
class ArtifactRecord:
    role: str
    present: bool
    path: str | None = None
    file_sha256: str | None = None
    byte_size: int | None = None
    schema: str | None = None
    notes: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _rel(repo_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _record_file(
    *,
    role: str,
    path: Path,
    repo_root: Path,
    notes: str = "",
    extract_fields: dict[str, Any] | None = None,
    schema_key: str = "schema",
) -> ArtifactRecord:
    if not path.exists():
        return ArtifactRecord(
            role=role,
            present=False,
            path=_rel(repo_root, path),
            notes=notes or "path missing",
        )
    if path.is_dir():
        return ArtifactRecord(
            role=role,
            present=True,
            path=_rel(repo_root, path),
            notes=notes or "directory present (content not fully hashed)",
            fields=extract_fields or {},
        )
    try:
        digest = _file_sha256(path)
        size = path.stat().st_size
    except OSError as exc:
        return ArtifactRecord(
            role=role,
            present=True,
            path=_rel(repo_root, path),
            notes=f"unreadable: {exc}",
        )
    schema = None
    fields = dict(extract_fields or {})
    if path.suffix.lower() == ".json" and size <= 8_000_000:
        payload = _read_json(path)
        if payload is not None:
            schema = (
                payload.get(schema_key)
                or payload.get("schema_version")
                or payload.get("schema")
            )
            if isinstance(schema, str):
                pass
            else:
                schema = None
            for key in (
                "verified",
                "authority_status",
                "formal_materialization_eligible",
                "factor_materialization_eligible",
                "production_profile_registered",
                "production_recommendation_eligible",
                "development_only",
                "temporal_role",
                "artifact_sha256",
                "receipt_sha256",
            ):
                if key in payload and key not in fields:
                    fields[key] = payload.get(key)
    return ArtifactRecord(
        role=role,
        present=True,
        path=_rel(repo_root, path),
        file_sha256=digest,
        byte_size=size,
        schema=schema if isinstance(schema, str) else None,
        notes=notes,
        fields=fields,
    )


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise ValueError(
            f"inventory requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} "
            f"(got {role!r})"
        )
    return role.strip()


def _inventory_daily_basic(repo_root: Path, daily_run: Path) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    state_path = daily_run / "state.json"
    state = _read_json(state_path)
    records.append(
        _record_file(
            role="daily_basic_run_state",
            path=state_path,
            repo_root=repo_root,
            notes="733 exact-set run state",
            extract_fields={
                "status": (state or {}).get("status"),
                "completed_session_count": (state or {}).get(
                    "completed_session_count"
                ),
            },
        )
    )
    if not state:
        return records
    pub = state.get("exact_set_publication")
    if isinstance(pub, dict):
        authority_root = daily_run / "exact-set-authority"
        for key, role in (
            ("receipt_relative_path", "daily_basic_authority_receipt"),
            ("publication_relative_path", "daily_basic_exact_set_publication"),
            ("attestation_relative_path", "daily_basic_exact_set_attestation"),
        ):
            rel = pub.get(key)
            if isinstance(rel, str) and rel:
                path = authority_root / rel
                expected = pub.get(key.replace("_relative_path", "_sha256"))
                rec = _record_file(
                    role=role,
                    path=path,
                    repo_root=repo_root,
                    notes=f"from exact_set_publication.{key}",
                )
                if (
                    rec.present
                    and isinstance(expected, str)
                    and rec.file_sha256
                    and rec.file_sha256 != expected
                ):
                    rec.notes += f"; file_sha mismatch expected={expected}"
                if isinstance(expected, str):
                    rec.fields["declared_sha256"] = expected
                records.append(rec)
        receipt = state.get("receipt")
        if isinstance(receipt, dict):
            records.append(
                ArtifactRecord(
                    role="daily_basic_state_receipt_summary",
                    present=True,
                    path=_rel(repo_root, state_path),
                    notes="embedded state.receipt summary",
                    fields={
                        "verified": receipt.get("verified"),
                        "receipt_sha256": receipt.get("receipt_sha256"),
                        "authority_root_sha256": receipt.get("authority_root_sha256"),
                    },
                )
            )
    return records


def _inventory_feature_history(
    repo_root: Path, feature_run: Path, attestation_root: Path
) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    state_path = feature_run / "state.json"
    state = _read_json(state_path)
    records.append(
        _record_file(
            role="feature_history_run_state",
            path=state_path,
            repo_root=repo_root,
            notes="prewindow_250 run state",
            extract_fields={
                "status": (state or {}).get("status"),
                "completed_session_count": (state or {}).get(
                    "completed_session_count"
                ),
            },
        )
    )
    if state:
        receipt = state.get("receipt") if isinstance(state.get("receipt"), dict) else {}
        records.append(
            ArtifactRecord(
                role="feature_history_authority_receipt_embedded",
                present=bool(receipt),
                path=_rel(repo_root, state_path),
                notes="history-only embedded receipt (not formal materialization eligible)",
                fields={
                    "verified": receipt.get("verified"),
                    "authority_status": receipt.get("authority_status"),
                    "feature_history_only": receipt.get("feature_history_only"),
                    "factor_materialization_eligible": receipt.get(
                        "factor_materialization_eligible"
                    ),
                    "session_count": receipt.get("session_count"),
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "collection_publication_manifest_sha256": receipt.get(
                        "collection_publication_manifest_sha256"
                    ),
                },
            )
        )
        pub = state.get("collection_publication")
        if isinstance(pub, dict):
            rel = pub.get("authority_manifest_relative_path")
            if isinstance(rel, str) and rel:
                path = feature_run / "collection-publication" / rel
                rec = _record_file(
                    role="feature_history_collection_manifest",
                    path=path,
                    repo_root=repo_root,
                    notes="DURABLE collection manifest candidate",
                )
                expected = pub.get("authority_manifest_sha256")
                if isinstance(expected, str):
                    rec.fields["declared_sha256"] = expected
                    if rec.file_sha256 and rec.file_sha256 != expected:
                        rec.notes += f"; file_sha mismatch expected={expected}"
                rec.fields["publication_status"] = pub.get("publication_status")
                records.append(rec)
            # collection publication receipts (issuance surface for activation)
            receipt_dir = (
                feature_run
                / "collection-publication"
                / "feature_history_collection_publication_receipts"
            )
            if receipt_dir.is_dir():
                jsons = sorted(receipt_dir.rglob("*.json"))
                if jsons:
                    # pick latest by mtime for inventory binding candidate
                    chosen = max(jsons, key=lambda p: p.stat().st_mtime)
                    records.append(
                        _record_file(
                            role="feature_history_collection_issuance_candidate",
                            path=chosen,
                            repo_root=repo_root,
                            notes=(
                                f"{len(jsons)} issuance/receipt json files under "
                                "feature_history_collection_publication_receipts"
                            ),
                        )
                    )
    # frozen attestation files
    if attestation_root.is_dir():
        att_files = sorted(attestation_root.rglob("*.json"))
        records.append(
            ArtifactRecord(
                role="feature_history_frozen_attestation_root",
                present=True,
                path=_rel(repo_root, attestation_root),
                notes=f"{len(att_files)} json attestation files",
                fields={"json_file_count": len(att_files)},
            )
        )
        if att_files:
            # Prefer files matching content-addressed name == sha256
            preferred = None
            for path in att_files:
                if path.stem == path.parent.name or len(path.stem) == 64:
                    preferred = path
            chosen = preferred or max(att_files, key=lambda p: p.stat().st_mtime)
            rec = _record_file(
                role="feature_history_frozen_attestation_candidate",
                path=chosen,
                repo_root=repo_root,
                notes="candidate attestation file for activation binding",
            )
            if rec.file_sha256 and chosen.stem != rec.file_sha256:
                # content-addressed layouts often use parent name as digest
                parent_name = chosen.parent.name
                if parent_name == rec.file_sha256:
                    rec.fields["cas_parent_matches"] = True
                else:
                    rec.fields["cas_parent_matches"] = False
            records.append(rec)
    else:
        records.append(
            ArtifactRecord(
                role="feature_history_frozen_attestation_root",
                present=False,
                path=_rel(repo_root, attestation_root),
                notes="attestation root missing",
            )
        )
    return records


def _inventory_parent_and_evaluation(repo_root: Path) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    records.append(
        _record_file(
            role="factor_v2_development_evaluation_artifact",
            path=repo_root / DEFAULT_EVALUATION_ARTIFACT,
            repo_root=repo_root,
            notes=(
                "research evaluation result artifact; NOT formal activation "
                "evaluation authority receipt schema"
            ),
        )
    )
    records.append(
        _record_file(
            role="factor_v2_full_parent_replay_gate_artifact",
            path=repo_root / DEFAULT_PARENT_REPLAY_ARTIFACT,
            repo_root=repo_root,
            notes=(
                "full-parent replay gate artifact; NOT formal parent-source "
                "authority receipt"
            ),
        )
    )
    records.append(
        _record_file(
            role="factor_v2_full_parent_replay_sqlite",
            path=repo_root / DEFAULT_PARENT_SQLITE,
            repo_root=repo_root,
            notes="large parent replay sqlite companion",
        )
    )
    records.append(
        _record_file(
            role="factor_v2_evaluation_parent_dataset_root",
            path=repo_root / DEFAULT_EVAL_PARENT_DATASET,
            repo_root=repo_root,
            notes="shallow_gbdt training dataset used as evaluation parent source",
        )
    )
    # Search for formal/disposable parent or evaluation authority receipt schemas.
    # Exclude dry-run worktrees — those only prove disposable contract engines.
    excluded_path_markers = (
        "factor_v3_activation_development_dry_run",
        "factor_v3_materializer_v2_development_dry_run",
        "factor_v3_parent_eval_authority_inventory",
        f"{Path('tmp').as_posix()}/",
        "\\tmp\\",
        ".pytest_cache",
        "__pycache__",
    )
    search_roots = [
        repo_root / "data/research_runs",
        repo_root / "data/research_artifacts",
    ]
    durable_parent_hits: list[str] = []
    durable_eval_hits: list[str] = []
    dry_run_parent_hits: list[str] = []
    dry_run_eval_hits: list[str] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            try:
                if path.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            path_text = str(path).replace("\\", "/")
            is_dry_run = any(marker in path_text for marker in excluded_path_markers)
            name = path.name.lower()
            path_l = path_text.lower()
            if (
                "parent" not in name
                and "evaluation" not in name
                and "authority" not in name
                and "parent-source" not in path_l
                and "evaluation" not in path_l
            ):
                continue
            payload = _read_json(path)
            if not payload:
                continue
            schema = str(
                payload.get("schema")
                or payload.get("schema_version")
                or ""
            )
            rel = _rel(repo_root, path) or str(path)
            parent_hit = (
                schema
                in {
                    activation.DISPOSABLE_PARENT_SOURCE_RECEIPT_SCHEMA,
                    parent_auth.AUTHORITY_RECEIPT_SCHEMA,
                }
                or "parent-source" in schema
            )
            eval_hit = schema in {
                activation.DISPOSABLE_EVALUATION_RECEIPT_SCHEMA,
                activation.FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA,
            } or ("evaluation" in schema and "authority" in schema)
            if parent_hit:
                target = dry_run_parent_hits if is_dry_run else durable_parent_hits
                target.append(f"{rel} schema={schema}")
            if eval_hit:
                target = dry_run_eval_hits if is_dry_run else durable_eval_hits
                target.append(f"{rel} schema={schema}")
    records.append(
        ArtifactRecord(
            role="durable_parent_source_authority_receipts_scan",
            present=bool(durable_parent_hits),
            notes=(
                f"durable hits={len(durable_parent_hits)}"
                if durable_parent_hits
                else "no durable formal/disposable parent-source authority receipts "
                "(excluding dry-run worktrees)"
            ),
            fields={"hits": durable_parent_hits[:20]},
        )
    )
    records.append(
        ArtifactRecord(
            role="durable_evaluation_authority_receipts_scan",
            present=bool(durable_eval_hits),
            notes=(
                f"durable hits={len(durable_eval_hits)}"
                if durable_eval_hits
                else "no durable formal/disposable evaluation authority receipts "
                "(excluding dry-run worktrees)"
            ),
            fields={"hits": durable_eval_hits[:20]},
        )
    )
    records.append(
        ArtifactRecord(
            role="dry_run_parent_source_authority_receipts_scan",
            present=bool(dry_run_parent_hits),
            notes=(
                f"dry-run fixture hits={len(dry_run_parent_hits)} "
                "(not durable formal authority)"
            ),
            fields={"hits": dry_run_parent_hits[:20]},
        )
    )
    records.append(
        ArtifactRecord(
            role="dry_run_evaluation_authority_receipts_scan",
            present=bool(dry_run_eval_hits),
            notes=(
                f"dry-run fixture hits={len(dry_run_eval_hits)} "
                "(not durable formal authority)"
            ),
            fields={"hits": dry_run_eval_hits[:20]},
        )
    )
    return records


def _inventory_dry_runs(repo_root: Path) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    for role, rel in (
        ("materializer_development_dry_run_pointer", DEFAULT_MATERIALIZER_DRY_RUN),
        ("activation_development_dry_run_pointer", DEFAULT_ACTIVATION_DRY_RUN),
    ):
        path = repo_root / rel
        payload = _read_json(path)
        records.append(
            ArtifactRecord(
                role=role,
                present=path.is_file(),
                path=_rel(repo_root, path),
                file_sha256=_file_sha256(path) if path.is_file() else None,
                notes="dry-run pointer",
                fields={
                    "ok": (payload or {}).get("ok"),
                    "development_only": (payload or {}).get("development_only"),
                    "stage_goal_id": (payload or {}).get("stage_goal_id"),
                    "evidence_sha256": (payload or {}).get("evidence_sha256"),
                    "formal_materialization_eligible": (payload or {}).get(
                        "formal_materialization_eligible"
                    ),
                },
            )
        )
    return records


def _inventory_registration_slots() -> list[ArtifactRecord]:
    return [
        ArtifactRecord(
            role="parent_source_registration_slots",
            present=True,
            notes="durable parent-source registration remains fail-closed",
            fields={
                "REGISTERED_GLOBAL_ATTEMPT_LEDGER_AUTHORITY_SHA256": (
                    parent_auth.REGISTERED_GLOBAL_ATTEMPT_LEDGER_AUTHORITY_SHA256
                ),
                "REGISTERED_NATIVE_TCB_AUTHORITY_ARTIFACT_SHA256": (
                    parent_auth.REGISTERED_NATIVE_TCB_AUTHORITY_ARTIFACT_SHA256
                ),
                "closed": (
                    parent_auth.REGISTERED_GLOBAL_ATTEMPT_LEDGER_AUTHORITY_SHA256
                    is None
                    and parent_auth.REGISTERED_NATIVE_TCB_AUTHORITY_ARTIFACT_SHA256
                    is None
                ),
            },
        ),
        ArtifactRecord(
            role="materializer_registration_slots",
            present=True,
            notes="durable materializer TCB/broker remains fail-closed",
            fields={
                "REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256": (
                    materializer_auth.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256
                ),
                "REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256": (
                    materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256
                ),
                "REGISTERED_MATERIALIZER_V2_NATIVE_BROKER_is_none": (
                    materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER is None
                ),
                "closed": (
                    materializer_auth.REGISTERED_ACTIVATION_AUTHORITY_LOCATOR_RAW_SHA256
                    is None
                    and materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_TCB_SHA256
                    is None
                    and materializer_auth.REGISTERED_MATERIALIZER_V2_NATIVE_BROKER
                    is None
                ),
            },
        ),
        ArtifactRecord(
            role="compound_registration_slots",
            present=True,
            notes="compound production registry remains closed",
            fields={
                "REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH": (
                    compound.REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH
                ),
                "REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256": (
                    compound_native.REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256
                ),
                "HANDOFF_READY": compound_native.HANDOFF_READY,
                "closed": (
                    compound.REGISTERED_COMPOUND_DEPLOYMENT_POLICY_AUTHORITY_PATH
                    is None
                    and compound_native.REGISTERED_COMPOUND_NATIVE_MANIFEST_SHA256
                    is None
                    and compound_native.HANDOFF_READY == 0
                ),
            },
        ),
    ]


def _classify_activation_bindings(records: list[ArtifactRecord]) -> list[dict[str, Any]]:
    by_role = {r.role: r for r in records}
    classifications: list[dict[str, Any]] = []

    def add(
        binding: str,
        status: str,
        *,
        source_role: str | None = None,
        detail: str = "",
    ) -> None:
        classifications.append(
            {
                "binding": binding,
                "status": status,
                "source_role": source_role,
                "detail": detail,
            }
        )

    # daily
    daily = by_role.get("daily_basic_authority_receipt")
    if daily and daily.present and daily.file_sha256:
        add(
            "daily_basic_authority_receipt_file_sha256",
            "bound_local_file",
            source_role=daily.role,
            detail=f"sha256={daily.file_sha256}",
        )
        add(
            "daily_basic_authority_receipt_path",
            "bound_local_file",
            source_role=daily.role,
            detail=daily.path or "",
        )
    else:
        add(
            "daily_basic_authority_receipt_file_sha256",
            "missing",
            detail="daily-basic exact-set receipt file not found",
        )
        add("daily_basic_authority_receipt_path", "missing")

    # feature history issuance
    issuance = by_role.get("feature_history_collection_issuance_candidate")
    if issuance and issuance.present and issuance.file_sha256:
        add(
            "feature_history_collection_issuance_file_sha256",
            "bound_local_candidate",
            source_role=issuance.role,
            detail=f"sha256={issuance.file_sha256}",
        )
        add(
            "feature_history_collection_issuance_path",
            "bound_local_candidate",
            source_role=issuance.role,
            detail=issuance.path or "",
        )
    else:
        add(
            "feature_history_collection_issuance_file_sha256",
            "missing",
            detail="no collection publication receipt candidate",
        )
        add("feature_history_collection_issuance_path", "missing")

    # attestation
    att = by_role.get("feature_history_frozen_attestation_candidate")
    if att and att.present and att.file_sha256:
        add(
            "feature_history_frozen_attestation_file_sha256",
            "bound_local_candidate",
            source_role=att.role,
            detail=f"sha256={att.file_sha256}",
        )
        add(
            "feature_history_frozen_attestation_path",
            "bound_local_candidate",
            source_role=att.role,
            detail=att.path or "",
        )
    else:
        add("feature_history_frozen_attestation_file_sha256", "missing")
        add("feature_history_frozen_attestation_path", "missing")

    # parent / evaluation formal receipts (durable only; dry-run fixtures excluded)
    parent_scan = by_role.get("durable_parent_source_authority_receipts_scan")
    eval_scan = by_role.get("durable_evaluation_authority_receipts_scan")
    parent_research = by_role.get("factor_v2_full_parent_replay_gate_artifact")
    eval_research = by_role.get("factor_v2_development_evaluation_artifact")
    dry_parent = by_role.get("dry_run_parent_source_authority_receipts_scan")
    dry_eval = by_role.get("dry_run_evaluation_authority_receipts_scan")

    if parent_scan and parent_scan.present:
        add(
            "parent_source_authority_receipt_file_sha256",
            "found_durable_receipt",
            source_role=parent_scan.role,
            detail=str(parent_scan.fields.get("hits", [])[:3]),
        )
    else:
        detail = (
            "research parent replay artifact present but not formal "
            "parent-source authority receipt"
            if parent_research and parent_research.present
            else "no durable parent-source authority receipt"
        )
        if dry_parent and dry_parent.present:
            detail += "; dry-run disposable fixtures exist but are non-durable"
        add(
            "parent_source_authority_receipt_file_sha256",
            "missing_formal_receipt",
            detail=detail,
        )
    add(
        "parent_source_authority_receipt_root_sha256",
        "missing_formal_receipt",
        detail="requires formal/disposable parent receipt self-hash root",
    )
    add(
        "parent_source_terminal_receipt_file_sha256",
        "missing_formal_receipt",
        detail="global epoch terminal receipt chain not published",
    )

    if eval_scan and eval_scan.present:
        add(
            "factor_v2_evaluation_authority_receipt_file_sha256",
            "found_durable_receipt",
            source_role=eval_scan.role,
            detail=str(eval_scan.fields.get("hits", [])[:3]),
        )
    else:
        detail = (
            "research evaluation artifact present but not formal evaluation "
            "authority receipt"
            if eval_research and eval_research.present
            else "no durable evaluation authority receipt"
        )
        if dry_eval and dry_eval.present:
            detail += "; dry-run disposable fixtures exist but are non-durable"
        add(
            "factor_v2_evaluation_authority_receipt_file_sha256",
            "missing_formal_receipt",
            detail=detail,
        )
    add(
        "factor_v2_evaluation_authority_receipt_root_sha256",
        "missing_formal_receipt",
        detail="requires formal/disposable evaluation receipt self-hash root",
    )

    # candidate + epoch + lease bindings — not present outside disposable dry-run fixtures
    for binding in (
        "candidate_authority_root_sha256",
        "candidate_descriptor_sha256",
        "candidate_publication_file_sha256",
        "attempt_key_sha256",
        "global_attempt_identity_sha256",
        "global_attempt_ledger_root",
        "global_run_claim_path",
        "global_run_receipt_path",
        "global_terminal_receipt_path",
        "global_verify_claim_path",
        "native_lease_identity_sha256",
        "native_lease_policy_version",
        "run_spec_sha256",
        "semantic_input_root_sha256",
    ):
        add(
            binding,
            "missing_formal_chain",
            detail="requires parent-source formal attempt/epoch or candidate publish",
        )

    return classifications


def _build_gaps(
    records: list[ArtifactRecord],
    bindings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    by_role = {r.role: r for r in records}

    def gap(code: str, detail: str, severity: str = "blocking_for_formal_activation") -> None:
        gaps.append({"code": code, "severity": severity, "detail": detail})

    # upstream that should already be green
    for role, code in (
        ("daily_basic_authority_receipt", "daily_basic_receipt_missing"),
        ("feature_history_authority_receipt_embedded", "feature_history_receipt_missing"),
        ("feature_history_frozen_attestation_candidate", "feature_history_attestation_missing"),
    ):
        rec = by_role.get(role)
        if rec is None or not rec.present:
            gap(code, f"{role} not present")

    # expected formal gaps (stage documents them) — durable only
    parent_scan = by_role.get("durable_parent_source_authority_receipts_scan")
    if parent_scan is None or not parent_scan.present:
        gap(
            "parent_source_formal_authority_receipt_missing",
            "no durable formal/disposable parent-source authority receipt on disk; "
            "research full-parent replay and/or dry-run fixtures are not substitutes",
        )
    eval_scan = by_role.get("durable_evaluation_authority_receipts_scan")
    if eval_scan is None or not eval_scan.present:
        gap(
            "evaluation_formal_authority_receipt_missing",
            "no durable formal/disposable factor-v2 evaluation authority receipt; "
            "research development-evaluation and/or dry-run fixtures are not substitutes",
        )

    parent_slots = by_role.get("parent_source_registration_slots")
    if parent_slots and parent_slots.fields.get("closed") is True:
        gap(
            "parent_source_registration_closed",
            "REGISTERED_GLOBAL_ATTEMPT_LEDGER / NATIVE_TCB still None",
            severity="expected_fail_closed",
        )
    mat_slots = by_role.get("materializer_registration_slots")
    if mat_slots and mat_slots.fields.get("closed") is True:
        gap(
            "materializer_registration_closed",
            "materializer REGISTERED_* still None",
            severity="expected_fail_closed",
        )

    missing_bindings = [
        b["binding"]
        for b in bindings
        if b["status"] in {"missing", "missing_formal_receipt", "missing_formal_chain"}
    ]
    if missing_bindings:
        gap(
            "activation_bindings_incomplete",
            f"{len(missing_bindings)}/{len(bindings)} activation bindings not formally bound",
            severity="blocking_for_formal_activation",
        )

    gap(
        "formal_materialization_eligible_not_granted",
        "inventory intentionally keeps formal_materialization_eligible=False",
        severity="policy",
    )
    return gaps


def build_parent_eval_authority_inventory(
    *,
    repo_root: Path | None = None,
    daily_run: Path | None = None,
    feature_run: Path | None = None,
    attestation_root: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    root = (repo_root or Path.cwd()).resolve()
    daily = (root / (daily_run or DEFAULT_DAILY_RUN)).resolve()
    feature = (root / (feature_run or DEFAULT_FEATURE_RUN)).resolve()
    attestation = (root / (attestation_root or DEFAULT_ATTESTATION_ROOT)).resolve()

    records: list[ArtifactRecord] = []
    records.extend(_inventory_daily_basic(root, daily))
    records.extend(_inventory_feature_history(root, feature, attestation))
    records.extend(_inventory_parent_and_evaluation(root))
    records.extend(_inventory_dry_runs(root))
    records.extend(_inventory_registration_slots())

    bindings = _classify_activation_bindings(records)
    gaps = _build_gaps(records, bindings)

    bound_ok = {
        "daily_basic": any(
            b["binding"].startswith("daily_basic") and b["status"].startswith("bound")
            for b in bindings
        ),
        "feature_history_issuance": any(
            b["binding"].startswith("feature_history_collection_issuance")
            and b["status"].startswith("bound")
            for b in bindings
        ),
        "feature_history_attestation": any(
            b["binding"].startswith("feature_history_frozen_attestation")
            and b["status"].startswith("bound")
            for b in bindings
        ),
        "parent_formal_receipt": any(
            b["binding"].startswith("parent_source_authority_receipt_file")
            and b["status"] == "found_durable_receipt"
            for b in bindings
        ),
        "evaluation_formal_receipt": any(
            b["binding"].startswith("factor_v2_evaluation_authority_receipt_file")
            and b["status"] == "found_durable_receipt"
            for b in bindings
        ),
    }

    # Stage acceptance: inventory complete + upstream present + formal gaps explicit.
    upstream_ok = (
        bound_ok["daily_basic"]
        and bound_ok["feature_history_issuance"]
        and bound_ok["feature_history_attestation"]
    )
    formal_gaps_documented = any(
        g["code"] == "parent_source_formal_authority_receipt_missing" for g in gaps
    ) and any(
        g["code"] == "evaluation_formal_authority_receipt_missing" for g in gaps
    )
    inventory_ok = upstream_ok and formal_gaps_documented

    unsigned = {
        "schema": INVENTORY_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "activation_input_binding_fields": list(activation.ACTIVATION_INPUT_BINDING_FIELDS),
        "formal_activation_authority_receipt_roles": list(
            FORMAL_ACTIVATION_AUTHORITY_RECEIPT_ROLES
        ),
        "artifacts": [r.to_dict() for r in records],
        "activation_binding_classification": bindings,
        "binding_coverage": bound_ok,
        "gaps": gaps,
        "gap_codes": [g["code"] for g in gaps],
        "upstream_ready_for_cross_bind": upstream_ok,
        "formal_parent_eval_ready": False,
        "ok": inventory_ok,
        "notes": (
            "ok=true means inventory completed with upstream daily/FH/attestation "
            "bound and formal parent/eval gaps explicitly documented — not that "
            "formal activation may proceed."
        ),
    }
    return {**unsigned, "inventory_sha256": _sha(unsigned)}


def write_inventory_evidence(
    inventory: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    digest = inventory["inventory_sha256"]
    evidence_path = evidence_dir / f"{digest[:16]}.json"
    raw = _canonical_bytes(inventory) + b"\n"
    evidence_path.write_bytes(raw)
    file_sha = hashlib.sha256(raw).hexdigest()
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "ok": inventory.get("ok") is True,
        "development_only": True,
        "production_profile_registered": False,
        "automatic_trading_allowed": False,
        "formal_materialization_eligible": False,
        "formal_parent_eval_ready": False,
        "inventory_sha256": digest,
        "evidence_path": str(evidence_path.resolve()),
        "evidence_file_sha256": file_sha,
        "gap_codes": inventory.get("gap_codes", []),
        "upstream_ready_for_cross_bind": inventory.get(
            "upstream_ready_for_cross_bind"
        ),
    }
    pointer_path = output_root / "LATEST.json"
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "INVENTORY_SCHEMA",
    "POINTER_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "build_parent_eval_authority_inventory",
    "write_inventory_evidence",
]
