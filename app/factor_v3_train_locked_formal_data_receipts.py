"""Publish train-locked formal *upstream* data receipt binder.

Binds already-verified Factor V3 upstream receipts (daily-basic 733 exact-set,
feature-history 250 history-only, frozen attestation) under the train-window
freeze (before 2026-08-01, no daily incremental sync).

This is **not** a parent-source or evaluation formal authority receipt — those
still require registered native TCB. It is the durable formal data surface that
*does* exist today for strategy research under the freeze.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app import factor_v3_train_window_freeze_contract as freeze
from app import research_goal_contract as goal

BUNDLE_SCHEMA = "factor-v3-train-locked-formal-data-receipt-bundle/v1"
POINTER_SCHEMA = "factor-v3-train-locked-formal-data-receipt-pointer/v1"
STAGE_GOAL_ID = "factor-v3-train-locked-formal-data-receipts/v1"
STAGE_GOAL_SUMMARY = (
    "在 train exclusive end=2026-08-01 之前锁定训练集："
    "封存 daily-basic 733 + feature-history 250 + attestation 为 durable formal "
    "上游数据收据绑定；不日更同步；不宣称 parent/eval formal 已齐；"
    "不 formal_materialization_eligible；永不自动交易。"
)
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
DEFAULT_OUTPUT_ROOT = Path(
    "data/research_runs/factor_v3_train_locked_formal_data_receipts"
)


class TrainLockedFormalDataReceiptError(ValueError):
    """Raised when train-locked formal data binding fails closed."""


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainLockedFormalDataReceiptError(
            f"invalid json: {path}: {exc}"
        ) from exc
    if type(value) is not dict:
        raise TrainLockedFormalDataReceiptError(f"json root must be object: {path}")
    return value


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise TrainLockedFormalDataReceiptError(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _bind_daily_basic(repo_root: Path, daily_run: Path) -> dict[str, Any]:
    state_path = daily_run / "state.json"
    if not state_path.is_file():
        raise TrainLockedFormalDataReceiptError(f"missing daily state: {state_path}")
    state = _read_json(state_path)
    if state.get("status") not in {"verified", "succeeded"}:
        raise TrainLockedFormalDataReceiptError(
            f"daily-basic status not verified: {state.get('status')}"
        )
    completed = int(state.get("completed_session_count") or 0)
    if completed != freeze.TRAIN_ALL_MARKET_SESSION_COUNT:
        raise TrainLockedFormalDataReceiptError(
            f"daily-basic completed_session_count={completed} "
            f"!= {freeze.TRAIN_ALL_MARKET_SESSION_COUNT}"
        )
    pub = state.get("exact_set_publication")
    if type(pub) is not dict:
        raise TrainLockedFormalDataReceiptError("daily exact_set_publication missing")
    rel = pub.get("receipt_relative_path")
    declared = pub.get("receipt_sha256")
    if type(rel) is not str or type(declared) is not str:
        raise TrainLockedFormalDataReceiptError("daily receipt path/sha missing")
    receipt_path = (daily_run / "exact-set-authority" / rel).resolve()
    if not receipt_path.is_file():
        raise TrainLockedFormalDataReceiptError(f"daily receipt missing: {receipt_path}")
    file_sha = _file_sha256(receipt_path)
    if file_sha != declared:
        raise TrainLockedFormalDataReceiptError(
            f"daily receipt file sha mismatch: {file_sha} != {declared}"
        )
    receipt = _read_json(receipt_path)
    dates = receipt.get("trade_dates")
    if type(dates) is not list or not all(type(d) is str for d in dates):
        raise TrainLockedFormalDataReceiptError("daily receipt trade_dates invalid")
    freeze.assert_session_dates_in_train_window(dates, label="daily_basic.trade_dates")
    if len(dates) != freeze.TRAIN_ALL_MARKET_SESSION_COUNT:
        raise TrainLockedFormalDataReceiptError(
            f"daily trade_dates length {len(dates)} != "
            f"{freeze.TRAIN_ALL_MARKET_SESSION_COUNT}"
        )
    if dates[-1] != freeze.TRAIN_INCLUSIVE_SESSION_END:
        raise TrainLockedFormalDataReceiptError(
            f"daily last session {dates[-1]} != sealed train end "
            f"{freeze.TRAIN_INCLUSIVE_SESSION_END}"
        )
    return {
        "role": "daily_basic_733_exact_set_receipt",
        "formal_upstream_data": True,
        "path": _rel(repo_root, receipt_path),
        "file_sha256": file_sha,
        "declared_receipt_sha256": declared,
        "schema": receipt.get("schema"),
        "authority_status": receipt.get("authority_status"),
        "verified_flag_from_state": True,
        "trade_date_count": len(dates),
        "first_trade_date": dates[0],
        "last_trade_date": dates[-1],
        "factor_v3_development_materialization_input_eligible": receipt.get(
            "factor_v3_development_materialization_input_eligible"
        ),
        "formal_factor_v3_materialization_performed": receipt.get(
            "formal_factor_v3_materialization_performed"
        ),
        "within_train_exclusive_end": True,
    }


def _bind_feature_history(repo_root: Path, feature_run: Path) -> dict[str, Any]:
    state_path = feature_run / "state.json"
    if not state_path.is_file():
        raise TrainLockedFormalDataReceiptError(f"missing feature-history state: {state_path}")
    state = _read_json(state_path)
    receipt = state.get("receipt")
    if type(receipt) is not dict:
        raise TrainLockedFormalDataReceiptError("feature-history receipt missing")
    if receipt.get("verified") is not True:
        raise TrainLockedFormalDataReceiptError("feature-history receipt not verified")
    if receipt.get("authority_status") != "VERIFIED_FEATURE_HISTORY_ONLY":
        raise TrainLockedFormalDataReceiptError(
            f"unexpected FH authority_status={receipt.get('authority_status')}"
        )
    if receipt.get("factor_materialization_eligible") is not False:
        raise TrainLockedFormalDataReceiptError(
            "feature-history must remain factor_materialization_eligible=False"
        )
    session_count = int(receipt.get("session_count") or 0)
    if session_count != freeze.TRAIN_PREWINDOW_SESSION_COUNT:
        raise TrainLockedFormalDataReceiptError(
            f"feature-history session_count={session_count} != "
            f"{freeze.TRAIN_PREWINDOW_SESSION_COUNT}"
        )
    pub = state.get("collection_publication")
    if type(pub) is not dict:
        raise TrainLockedFormalDataReceiptError("feature-history publication missing")
    if pub.get("publication_status") != "DURABLE_POSTVERIFIED_AND_RETURNED":
        raise TrainLockedFormalDataReceiptError(
            f"feature-history publication not durable: {pub.get('publication_status')}"
        )
    manifest_rel = pub.get("authority_manifest_relative_path")
    manifest_sha = pub.get("authority_manifest_sha256")
    manifest_binding: dict[str, Any] | None = None
    if type(manifest_rel) is str and type(manifest_sha) is str:
        manifest_path = (
            feature_run / "collection-publication" / manifest_rel
        ).resolve()
        if manifest_path.is_file():
            file_sha = _file_sha256(manifest_path)
            if file_sha != manifest_sha:
                raise TrainLockedFormalDataReceiptError(
                    "feature-history manifest file sha mismatch"
                )
            manifest_binding = {
                "path": _rel(repo_root, manifest_path),
                "file_sha256": file_sha,
            }
    return {
        "role": "feature_history_250_history_only_receipt",
        "formal_upstream_data": True,
        "embedded_in_state_path": _rel(repo_root, state_path),
        "receipt_sha256": receipt.get("receipt_sha256"),
        "authority_status": receipt.get("authority_status"),
        "feature_history_only": receipt.get("feature_history_only"),
        "factor_materialization_eligible": False,
        "session_count": session_count,
        "publication_status": pub.get("publication_status"),
        "collection_publication_manifest_sha256": receipt.get(
            "collection_publication_manifest_sha256"
        ),
        "manifest_binding": manifest_binding,
        "within_train_exclusive_end": True,
        "note": (
            "history-only evidence for prewindow; not formal materialization eligibility"
        ),
    }


def _bind_attestation(repo_root: Path, attestation_root: Path) -> dict[str, Any]:
    if not attestation_root.is_dir():
        raise TrainLockedFormalDataReceiptError(
            f"attestation root missing: {attestation_root}"
        )
    candidates = sorted(attestation_root.rglob("*.json"))
    if not candidates:
        raise TrainLockedFormalDataReceiptError("no attestation json files")
    # Prefer content-addressed file where stem or parent name equals file sha.
    chosen: Path | None = None
    for path in candidates:
        try:
            digest = _file_sha256(path)
        except OSError:
            continue
        if path.stem == digest or path.parent.name == digest:
            chosen = path
            break
    if chosen is None:
        chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    file_sha = _file_sha256(chosen)
    payload = _read_json(chosen)
    if payload.get("verified") is not True:
        raise TrainLockedFormalDataReceiptError(
            f"attestation not verified: {chosen}"
        )
    return {
        "role": "feature_history_frozen_source_attestation",
        "formal_upstream_data": True,
        "path": _rel(repo_root, chosen),
        "file_sha256": file_sha,
        "schema": payload.get("schema"),
        "verified": True,
        "within_train_exclusive_end": True,
    }


def build_train_locked_formal_data_receipt_bundle(
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

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()
    daily = (root / (daily_run or DEFAULT_DAILY_RUN)).resolve()
    feature = (root / (feature_run or DEFAULT_FEATURE_RUN)).resolve()
    attestation = (root / (attestation_root or DEFAULT_ATTESTATION_ROOT)).resolve()

    daily_binding = _bind_daily_basic(root, daily)
    fh_binding = _bind_feature_history(root, feature)
    att_binding = _bind_attestation(root, attestation)

    missing_formal = [
        {
            "role": "parent_source_authority_receipt",
            "present": False,
            "reason": "registered native TCB / global attempt ledger unavailable",
        },
        {
            "role": "factor_v2_evaluation_authority_receipt",
            "present": False,
            "reason": "no durable formal evaluation authority receipt published",
        },
        {
            "role": "parent_source_terminal_epoch_chain",
            "present": False,
            "reason": "depends on registered parent-source formal attempt authority",
        },
    ]

    unsigned = {
        "schema": BUNDLE_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "development_only": True,
        "vps_runtime_role": role,
        "research_goal_summary": goal.research_goal_descriptor().get("summary"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "daily_incremental_sync_required": False,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "upstream_formal_data_receipts": {
            "daily_basic": daily_binding,
            "feature_history": fh_binding,
            "feature_history_attestation": att_binding,
        },
        "upstream_formal_data_complete": True,
        "parent_eval_formal_receipts": missing_formal,
        "parent_eval_formal_complete": False,
        "ok": True,
        "notes": (
            "ok=true means train-locked upstream formal data receipts are bound "
            "and sealed under the freeze. Parent/eval formal receipts remain "
            "missing. Not a strategy performance claim."
        ),
    }
    return {**unsigned, "bundle_sha256": _sha(unsigned)}


def write_train_locked_formal_data_receipts(
    bundle: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if bundle.get("ok") is not True:
        raise TrainLockedFormalDataReceiptError("refuse to write non-ok bundle")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = bundle["bundle_sha256"]
    cas_dir = output_root / "sha256" / digest[:2] / digest
    cas_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = cas_dir / "train-locked-formal-data-receipt-bundle.json"
    raw = _canonical_bytes(bundle) + b"\n"
    # create-only semantics
    if evidence_path.exists():
        existing = evidence_path.read_bytes()
        if existing != raw:
            raise TrainLockedFormalDataReceiptError(
                "CAS path exists with different content"
            )
    else:
        evidence_path.write_bytes(raw)
    file_sha = hashlib.sha256(raw).hexdigest()
    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "ok": True,
        "development_only": True,
        "production_profile_registered": False,
        "automatic_trading_allowed": False,
        "formal_materialization_eligible": False,
        "daily_incremental_sync_required": False,
        "train_exclusive_end_date": freeze.TRAIN_EXCLUSIVE_END_DATE,
        "train_inclusive_session_end": freeze.TRAIN_INCLUSIVE_SESSION_END,
        "upstream_formal_data_complete": True,
        "parent_eval_formal_complete": False,
        "bundle_sha256": digest,
        "evidence_path": str(evidence_path.resolve()),
        "evidence_file_sha256": file_sha,
    }
    pointer_path = output_root / "LATEST.json"
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


__all__ = [
    "BUNDLE_SCHEMA",
    "DEFAULT_OUTPUT_ROOT",
    "POINTER_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "TrainLockedFormalDataReceiptError",
    "build_train_locked_formal_data_receipt_bundle",
    "write_train_locked_formal_data_receipts",
]
