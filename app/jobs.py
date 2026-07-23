import argparse
import hashlib
import json
import math
import os
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.a_share_universe import select_deep_scan_candidates
from app.audited_pit_development_replay import (
    run_audited_pit_breadth_development_replay,
    run_audited_pit_development_replay,
)
from app.audited_pit_loss_attribution import run_audited_pit_loss_attribution
from app.artifact_native_evidence import (
    build_artifact_native_evidence,
    verify_artifact_native_evidence,
    write_artifact_native_evidence,
)
from app.config import Settings, get_settings
from app.current_pool import build_current_pool_coverage, classify_current_pool_item
from app.current_pool_gate import load_current_pool_audit
from app.current_pool_history_source import (
    build_current_pool_history_summary,
    verify_current_pool_history_descriptor,
)
from app.current_pool_development_replay import run_current_pool_development_replay
from app.current_pool_source import (
    _strict_json_loads,
    fetch_jiaoch_current_pool_descriptor,
    verify_current_pool_universe_descriptor,
)
from app.durable_io import fsync_directory
from app.current_pool_risk_source import (
    fetch_jiaoch_current_pool_risk_descriptor,
    verify_current_pool_risk_descriptor,
)
from app.industry_history import IndustryHistoryProvider
from app.logging_setup import configure_logging
from app.margin_eligibility import MarginEligibilityProvider
from app.production_status import build_production_status, process_health_alert
from app.recommendation_evidence import (
    build_profile_evidence_receipt,
    verify_profile_evidence_receipt,
)
from app.recommendations import RUN_SLOT_AUTO, RUN_SLOT_CONTEXTS, RecommendationService
from app.research_backtest import (
    run_candidate_research_backtest,
    run_historical_universe_research_backtest,
)
from app.research_composite_universe import load_composite_universe_descriptor
from app.research_development_payload_fixture import (
    DevelopmentPayloadFixtureError,
    verify_development_payload_fixture,
)
from app.research_launcher_ack import wait_for_launcher_ack
from app.research_control_quarantine import (
    quarantine_binding_sha256_v1,
    validate_frozen_quarantine_binding_v1,
)
from app.research_pit import (
    build_pit_universe_payload,
    verify_research_evidence_bundle,
    write_pit_universe_artifact,
    write_research_evidence_bundle,
    write_strict_qualified_trades_payload,
    write_strict_research_evidence_bundle,
)
from app.research_pit_collector import (
    ControlledTushareCollector,
    SystemTrustedClock,
    UrllibTushareTransport,
)
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_pit_store import (
    MAX_CNINFO_PDF_BYTES,
    MAX_RAW_BYTES,
    AuditedPointInTimeUniverse,
    PITReceiptStore,
)
from app.research_pit_sources import resolve_tushare_source
from app.research_precompute_control import (
    build_precompute_ledger_binding_v2,
    build_precompute_ledger_binding_v3,
    build_precompute_ledger_binding_v4,
    consume_registered_precompute_launch_v1,
    consume_registered_precompute_launch_v2,
    consume_registered_precompute_launch_v3,
    load_precompute_parent_proof_v1,
    precompute_control_source_bundle_v2,
    precompute_control_source_bundle_v3,
    publish_precompute_parent_proof_v2,
    publish_precompute_parent_proof_v3,
    publish_precompute_parent_proof_v4,
    register_precompute_v1,
    verify_precompute_publication_v1,
    verify_registered_precompute_v1,
    verify_registered_precompute_v2,
    verify_registered_precompute_v3,
    verify_registered_precompute_run_result_v1,
    verify_registered_precompute_run_result_v2,
)
from app.research_sweep import sweep_qualified_trades
from app.research_validation import (
    append_experiment_event,
    audited_authority_from_universe,
    claim_precomputed_experiment,
    claim_registered_experiment,
    complete_validation_started_experiment_if_current,
    decide_completed_validation_if_current,
    fail_precomputed_experiment_if_current,
    fail_validation_started_experiment_if_current,
    preflight_precomputed_experiment,
    preflight_registered_experiment,
    read_experiment_ledger,
    run_frozen_strategy_validation,
    validate_point_in_time_contract,
    write_report_artifact,
)
from app.storage import read_json, write_json


DISCLAIMER = "仅供个人量化研究和交易辅助，不构成投资建议；实盘前请结合仓位、流动性、交易成本和个人风险承受能力。"
DATA_PROVIDER = None


def _get_data_provider():
    global DATA_PROVIDER
    if DATA_PROVIDER is None:
        from app.main import DATA_PROVIDER as production_data_provider

        DATA_PROVIDER = production_data_provider
    return DATA_PROVIDER


class _OfflineTreatmentProvider:
    def history(self, *_args, **_kwargs):
        raise ValueError("offline treatment forbids provider access")


def _settings_from_frozen_treatment_plan(plan: dict) -> Settings:
    fingerprint = _validate_research_generation_settings_fingerprint(
        plan.get("settings_fingerprint")
    )
    values = dict(fingerprint["values"])
    values["min_backtest_trades"] = int(values["min_backtest_trades"])
    settings = Settings(**values)
    _validate_historical_treatment_settings(settings, plan)
    return settings


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_launcher_ready(payload: dict) -> None:
    print(
        "READY "
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        flush=True,
    )


def _wait_for_launcher_ack(ready_payload: dict, *, timeout_seconds: float = 30.0) -> dict:
    return wait_for_launcher_ack(ready_payload, timeout_seconds=timeout_seconds)


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


_PRECOMPUTE_PUBLICATION_ARGUMENTS = (
    "plan_publication_root",
    "plan_publication_audit_root",
    "expected_plan_publication_id",
    "expected_published_plan_file_sha256",
    "expected_plan_prepublish_file_sha256",
    "expected_plan_publication_result_file_sha256",
    "expected_plan_writer_claim_file_sha256",
    "expected_precompute_control_source_bundle_sha256",
    "minimum_registration_sequence_exclusive",
)
_PRECOMPUTE_PARENT_PROOF_ARGUMENTS = (
    "plan_publication_parent_proof_path",
    "expected_plan_publication_parent_proof_file_sha256",
    "expected_plan_publication_parent_proof_canonical_sha256",
)
_PRECOMPUTE_RUNTIME_ARGUMENTS = (
    "precompute_ledger_path",
    "precompute_registered_record_hash",
    "precompute_launch_started_record_hash",
    "precompute_run_claim_path",
    "expected_precompute_run_claim_file_sha256",
    "precompute_launch_lease_path",
    "expected_precompute_launch_lease_file_sha256",
)
_PRECOMPUTE_RESULT_ARGUMENTS = (
    "precompute_completed_record_hash",
    "precompute_run_result_path",
    "expected_precompute_run_result_file_sha256",
)
_PRECOMPUTE_CAS_ARGUMENTS = (
    "expected_ledger_sequence",
    "expected_ledger_record_hash",
    "allowed_nonterminal_record",
)


def _precompute_argument_present(args, name: str) -> bool:
    value = getattr(args, name, None)
    return bool(value) if name == "allowed_nonterminal_record" else value is not None


def _validate_precompute_argument_phase(
    args, *, plan_schema: str | None, phase: str
) -> None:
    if phase not in {"register", "historical", "validation"}:
        raise ValueError("precompute argument phase is invalid")
    publication_present = any(
        _precompute_argument_present(args, name)
        for name in _PRECOMPUTE_PUBLICATION_ARGUMENTS
    )
    proof_present = [
        _precompute_argument_present(args, name)
        for name in _PRECOMPUTE_PARENT_PROOF_ARGUMENTS
    ]
    runtime_present = any(
        _precompute_argument_present(args, name)
        for name in _PRECOMPUTE_RUNTIME_ARGUMENTS
    ) or _precompute_argument_present(args, "supervised_launch_mode")
    result_present = any(
        _precompute_argument_present(args, name)
        for name in _PRECOMPUTE_RESULT_ARGUMENTS
    )
    cas_present = any(
        _precompute_argument_present(args, name)
        for name in _PRECOMPUTE_CAS_ARGUMENTS
    )
    if plan_schema not in {
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        if (
            publication_present
            or any(proof_present)
            or runtime_present
            or result_present
            or cas_present
        ):
            raise ValueError("legacy plan forbids precompute control arguments")
        return
    if plan_schema == "research-treatment-input-plan/v4" and any(proof_present):
        raise ValueError("v4 treatment plan forbids parent proof arguments")
    if plan_schema in {
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        if phase == "register":
            if proof_present != [True, False, False]:
                raise ValueError("v5 registration parent proof arguments are invalid")
        elif proof_present != [True, True, True]:
            raise ValueError("v5 runtime parent proof arguments are incomplete")
        if phase != "register" and publication_present:
            raise ValueError("v5 child forbids secret publication arguments")
    if (
        plan_schema
        in {
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
        and getattr(args, "allowed_nonterminal_record", None)
    ):
        raise ValueError("v6 registration forbids CLI quarantine records")
    if phase == "register" and (runtime_present or result_present):
        raise ValueError("v4 register-only forbids runtime or result arguments")
    if phase == "historical" and (result_present or cas_present):
        raise ValueError("v4 historical run forbids result or CAS arguments")
    if phase == "validation" and cas_present:
        raise ValueError("v4 validation forbids registration CAS arguments")


def _add_precompute_control_arguments(parser, *, registration_cas: bool) -> None:
    parser.add_argument("--plan-publication-root", default=None)
    parser.add_argument("--plan-publication-audit-root", default=None)
    parser.add_argument("--expected-plan-publication-id", default=None)
    parser.add_argument("--expected-published-plan-file-sha256", default=None)
    parser.add_argument("--expected-plan-prepublish-file-sha256", default=None)
    parser.add_argument(
        "--expected-plan-publication-result-file-sha256", default=None
    )
    parser.add_argument("--expected-plan-writer-claim-file-sha256", default=None)
    parser.add_argument(
        "--expected-precompute-control-source-bundle-sha256", default=None
    )
    parser.add_argument(
        "--minimum-registration-sequence-exclusive", type=int, default=None
    )
    parser.add_argument("--plan-publication-parent-proof-path", default=None)
    parser.add_argument(
        "--expected-plan-publication-parent-proof-file-sha256", default=None
    )
    parser.add_argument(
        "--expected-plan-publication-parent-proof-canonical-sha256", default=None
    )
    parser.add_argument("--precompute-registered-record-hash", default=None)
    parser.add_argument("--precompute-launch-started-record-hash", default=None)
    parser.add_argument("--precompute-ledger-path", default=None)
    parser.add_argument("--precompute-run-claim-path", default=None)
    parser.add_argument("--expected-precompute-run-claim-file-sha256", default=None)
    parser.add_argument("--precompute-launch-lease-path", default=None)
    parser.add_argument(
        "--expected-precompute-launch-lease-file-sha256", default=None
    )
    parser.add_argument("--precompute-completed-record-hash", default=None)
    parser.add_argument("--precompute-run-result-path", default=None)
    parser.add_argument(
        "--expected-precompute-run-result-file-sha256", default=None
    )
    if registration_cas:
        parser.add_argument("--expected-ledger-sequence", type=int, default=None)
        parser.add_argument("--expected-ledger-record-hash", default=None)
        parser.add_argument("--allowed-nonterminal-record", action="append", default=[])


def _verify_precompute_control_for_treatment(
    args, plan: dict, plan_artifact: dict
) -> dict | None:
    values = [getattr(args, name, None) for name in _PRECOMPUTE_PUBLICATION_ARGUMENTS]
    controlled_plan = plan.get("schema_version") in {
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }
    proof_values = [
        getattr(args, name, None) for name in _PRECOMPUTE_PARENT_PROOF_ARGUMENTS
    ]
    if not any(value is not None for value in (*values, *proof_values)):
        if controlled_plan:
            raise ValueError("v4 treatment plan requires precompute control arguments")
        return None
    if not controlled_plan:
        raise ValueError("precompute publication control requires a v4 treatment plan")
    workspace = Path(__file__).resolve().parent.parent
    plan_schema = plan.get("schema_version")
    execution = plan.get("precompute_execution")
    if not isinstance(execution, dict):
        raise ValueError("precompute execution plan is missing")
    if plan_schema in {
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        proof_path = Path(str(args.plan_publication_parent_proof_path or ""))
        if proof_path != Path(execution["parent_proof_path"]):
            raise ValueError("precompute parent proof path mismatch")
        if getattr(args, "register_only", False):
            if any(value is None for value in values) or any(
                value is not None for value in proof_values[1:]
            ):
                raise ValueError("v5 registration publication control is incomplete")
            publication_root = Path(args.plan_publication_root)
            expected_plan_path = publication_root / "treatment-plan.json"
            supplied_plan_path = Path(args.input_plan_path)
            if (
                not supplied_plan_path.is_absolute()
                or supplied_plan_path != expected_plan_path
                or supplied_plan_path.resolve() != expected_plan_path.resolve()
                or plan_artifact.get("sha256")
                != args.expected_published_plan_file_sha256
            ):
                raise ValueError("v5 published plan binding mismatch")
            authorization = os.environ.get(
                "RESEARCH_PLAN_PUBLICATION_COMPLETION_AUTHORIZATION"
            )
            if not authorization:
                raise ValueError(
                    "precompute publication completion authorization is missing"
                )
            control_request_binding = (
                _validate_control_request_binding_v1(
                    plan.get("control_request_binding")
                )
                if plan_schema == "research-treatment-input-plan/v7"
                else None
            )
            if control_request_binding is not None and (
                args.expected_precompute_control_source_bundle_sha256
                != control_request_binding["control_source_bundle_sha256"]
                or args.minimum_registration_sequence_exclusive
                != control_request_binding["ledger"]["expected_tip_sequence"]
            ):
                raise ValueError("v7 publication control request binding mismatch")
            publish_parent_proof = (
                publish_precompute_parent_proof_v4
                if plan_schema == "research-treatment-input-plan/v7"
                else (
                    publish_precompute_parent_proof_v3
                    if plan_schema == "research-treatment-input-plan/v6"
                    else publish_precompute_parent_proof_v2
                )
            )
            published = publish_parent_proof(
                proof_path,
                workspace_root=workspace,
                publication_root=Path(args.plan_publication_root),
                audit_root=Path(args.plan_publication_audit_root),
                expected_publication_id=args.expected_plan_publication_id,
                expected_plan_file_sha256=args.expected_published_plan_file_sha256,
                expected_prepublish_evidence_file_sha256=(
                    args.expected_plan_prepublish_file_sha256
                ),
                expected_result_file_sha256=(
                    args.expected_plan_publication_result_file_sha256
                ),
                expected_writer_claim_file_sha256=(
                    args.expected_plan_writer_claim_file_sha256
                ),
                completion_authorization=authorization,
                expected_fixture_id=plan["development_payload_fixture"]["fixture_id"],
                expected_fixture_manifest_file_sha256=plan[
                    "development_payload_fixture"
                ]["manifest_file_sha256"],
                expected_control_source_bundle_sha256=(
                    args.expected_precompute_control_source_bundle_sha256
                ),
                minimum_registration_sequence_exclusive=(
                    args.minimum_registration_sequence_exclusive
                ),
            )
            return published["verified_control"]
        if any(value is not None for value in values) or any(
            value is None for value in proof_values
        ):
            raise ValueError("v5 child parent proof control is incomplete")
        loaded = load_precompute_parent_proof_v1(
            proof_path,
            workspace_root=workspace,
            expected_file_sha256=(
                args.expected_plan_publication_parent_proof_file_sha256
            ),
            expected_canonical_sha256=(
                args.expected_plan_publication_parent_proof_canonical_sha256
            ),
        )
        verified_control = loaded["verified_control"]
        fixture = plan["development_payload_fixture"]
        source_bundle = (
            precompute_control_source_bundle_v3(workspace)
            if plan_schema
            in {
                "research-treatment-input-plan/v6",
                "research-treatment-input-plan/v7",
            }
            else precompute_control_source_bundle_v2(workspace)
        )
        expected_plan_path = (
            Path(verified_control["publication_root"]) / "treatment-plan.json"
        )
        supplied_plan_path = Path(args.input_plan_path)
        if (
            not supplied_plan_path.is_absolute()
            or supplied_plan_path != expected_plan_path
            or supplied_plan_path.resolve() != expected_plan_path.resolve()
            or plan_artifact.get("sha256") != verified_control["plan_file_sha256"]
            or plan.get("plan_sha256") != verified_control["plan_sha256"]
            or fixture.get("fixture_id") != verified_control["fixture_id"]
            or fixture.get("manifest_file_sha256")
            != verified_control["fixture_manifest_file_sha256"]
            or source_bundle.get("root_sha256")
            != verified_control["control_source_bundle_sha256"]
            or (
                plan_schema == "research-treatment-input-plan/v7"
                and verified_control["control_source_bundle_sha256"]
                != _validate_control_request_binding_v1(
                    plan.get("control_request_binding")
                )["control_source_bundle_sha256"]
            )
        ):
            raise ValueError("v5 parent proof control binding mismatch")
        return verified_control
    if any(value is None for value in values):
        raise ValueError("precompute publication control arguments are incomplete")
    publication_root = Path(args.plan_publication_root)
    expected_plan_path = publication_root / "treatment-plan.json"
    supplied_plan_path = Path(args.input_plan_path)
    if (
        not supplied_plan_path.is_absolute()
        or supplied_plan_path != expected_plan_path
        or supplied_plan_path.resolve() != expected_plan_path.resolve()
    ):
        raise ValueError("precompute plan path is not the published treatment plan")
    if plan_artifact.get("sha256") != args.expected_published_plan_file_sha256:
        raise ValueError("precompute published plan artifact SHA mismatch")
    authorization = os.environ.get(
        "RESEARCH_PLAN_PUBLICATION_COMPLETION_AUTHORIZATION"
    )
    if not authorization:
        raise ValueError("precompute publication completion authorization is missing")
    fixture = plan["development_payload_fixture"]
    return verify_precompute_publication_v1(
        workspace_root=workspace,
        publication_root=publication_root,
        audit_root=Path(args.plan_publication_audit_root),
        expected_publication_id=args.expected_plan_publication_id,
        expected_plan_file_sha256=args.expected_published_plan_file_sha256,
        expected_prepublish_evidence_file_sha256=(
            args.expected_plan_prepublish_file_sha256
        ),
        expected_result_file_sha256=(
            args.expected_plan_publication_result_file_sha256
        ),
        expected_writer_claim_file_sha256=(
            args.expected_plan_writer_claim_file_sha256
        ),
        completion_authorization=authorization,
        expected_fixture_id=fixture["fixture_id"],
        expected_fixture_manifest_file_sha256=fixture["manifest_file_sha256"],
        expected_control_source_bundle_sha256=(
            args.expected_precompute_control_source_bundle_sha256
        ),
        minimum_registration_sequence_exclusive=(
            args.minimum_registration_sequence_exclusive
        ),
    )


def _parse_allowed_nonterminal_records(values: list[str]) -> list[dict]:
    records = []
    for value in values:
        try:
            payload = _strict_json_loads(value.encode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("allowed nonterminal record is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("allowed nonterminal record is invalid")
        records.append(payload)
    return records


def _verify_registered_precompute_for_args(
    args, plan: dict, control: dict, *, consume_launch: bool
) -> dict:
    required = (
        args.precompute_ledger_path,
        args.precompute_registered_record_hash,
        args.precompute_run_claim_path,
        args.expected_precompute_run_claim_file_sha256,
        args.precompute_launch_lease_path,
        args.expected_precompute_launch_lease_file_sha256,
    )
    controlled_launch = plan.get("schema_version") in {
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }
    if controlled_launch:
        required = (*required, args.precompute_launch_started_record_hash)
    if any(value is None for value in required):
        raise ValueError("registered precompute control arguments are incomplete")
    if args.supervised_launch_mode != "run":
        raise ValueError("registered precompute requires supervised run mode")
    v6 = plan.get("schema_version") in {
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }
    verifier = (
        consume_registered_precompute_launch_v3
        if v6 and consume_launch
        else (
            consume_registered_precompute_launch_v2
            if controlled_launch and consume_launch
            else (
                verify_registered_precompute_v3
                if v6
                else (
                    verify_registered_precompute_v2
                    if controlled_launch
                    else (
                        consume_registered_precompute_launch_v1
                        if consume_launch
                        else verify_registered_precompute_v1
                    )
                )
            )
        )
    )
    kwargs = {
        "workspace_root": Path(__file__).resolve().parent.parent,
        "experiment_id": plan["experiment_id"],
        "registered_record_hash": args.precompute_registered_record_hash,
        "verified_control": control,
        "run_claim_path": args.precompute_run_claim_path,
        "expected_run_claim_file_sha256": (
            args.expected_precompute_run_claim_file_sha256
        ),
        "launch_lease_path": args.precompute_launch_lease_path,
        "expected_launch_lease_file_sha256": (
            args.expected_precompute_launch_lease_file_sha256
        ),
    }
    if controlled_launch:
        kwargs["launch_started_record_hash"] = (
            args.precompute_launch_started_record_hash
        )
    return verifier(
        args.precompute_ledger_path,
        **kwargs,
    )


def _verify_validation_precompute_for_args(
    args,
    plan: dict,
    control: dict,
    *,
    validation_started_record_hash: str | None = None,
) -> dict:
    schema_version = plan.get("schema_version")
    controlled_launch = schema_version in {
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }
    v6 = schema_version in {
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }
    required = (
        args.ledger_path,
        args.registered_record_hash,
        args.precompute_ledger_path,
        args.precompute_registered_record_hash,
        args.precompute_run_claim_path,
        args.expected_precompute_run_claim_file_sha256,
        args.precompute_launch_lease_path,
        args.expected_precompute_launch_lease_file_sha256,
        args.precompute_completed_record_hash,
        args.precompute_run_result_path,
        args.expected_precompute_run_result_file_sha256,
    )
    if controlled_launch:
        required = (*required, args.precompute_launch_started_record_hash)
    if any(value is None for value in required):
        raise ValueError("validation precompute control arguments are incomplete")
    if Path(args.ledger_path).resolve() != Path(args.precompute_ledger_path).resolve():
        raise ValueError("validation precompute ledger path mismatch")
    if args.registered_record_hash != args.precompute_registered_record_hash:
        raise ValueError("validation precompute registered record hash mismatch")
    verifier_kwargs = {
        "workspace_root": Path(__file__).resolve().parent.parent,
        "experiment_id": plan["experiment_id"],
        "registered_record_hash": args.registered_record_hash,
        "verified_control": control,
        "run_claim_path": args.precompute_run_claim_path,
        "expected_run_claim_file_sha256": (
            args.expected_precompute_run_claim_file_sha256
        ),
        "launch_lease_path": args.precompute_launch_lease_path,
        "expected_launch_lease_file_sha256": (
            args.expected_precompute_launch_lease_file_sha256
        ),
        "precompute_completed_record_hash": (
            args.precompute_completed_record_hash
        ),
        "run_result_path": args.precompute_run_result_path,
        "expected_run_result_file_sha256": (
            args.expected_precompute_run_result_file_sha256
        ),
    }
    if controlled_launch:
        verifier_kwargs["launch_started_record_hash"] = (
            args.precompute_launch_started_record_hash
        )
    if validation_started_record_hash is not None:
        verifier_kwargs["validation_started_record_hash"] = (
            validation_started_record_hash
        )
    verifier = (
        verify_registered_precompute_run_result_v2
        if v6
        else verify_registered_precompute_run_result_v1
    )
    return verifier(
        args.ledger_path,
        **verifier_kwargs,
    )


def _verify_precompute_output_binding(payload: dict, expected_ready: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("qualified output precompute control binding mismatch")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("qualified output precompute control binding mismatch")
    if summary.get("precompute_control") != expected_ready:
        raise ValueError("qualified output precompute control binding mismatch")


def _verify_precompute_result_output_binding(
    *,
    payload: dict,
    qualified_path: Path,
    qualified_raw: bytes,
    verified_result: dict,
) -> None:
    expected_ready = verified_result.get("ready")
    result_payload = verified_result.get("run_result_payload")
    if not isinstance(expected_ready, dict) or not isinstance(result_payload, dict):
        raise ValueError("precompute run result binding is invalid")
    qualified_output = result_payload.get("qualified_output")
    trades = payload.get("qualified_trades")
    if (
        not isinstance(qualified_output, dict)
        or not isinstance(trades, list)
        or qualified_output.get("path") != str(qualified_path.resolve())
        or qualified_output.get("basename") != qualified_path.name
        or qualified_output.get("bytes") != len(qualified_raw)
        or qualified_output.get("sha256")
        != hashlib.sha256(qualified_raw).hexdigest()
        or qualified_output.get("qualified_trade_count") != len(trades)
    ):
        raise ValueError("qualified output run result binding mismatch")
    _verify_precompute_output_binding(payload, expected_ready)


def _positive_float_arg(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive finite number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive finite number")
    return parsed


def _validate_audited_authority_args(args) -> str:
    single_path = getattr(args, "audited_pit_universe_path", None)
    composite_path = getattr(args, "composite_pit_descriptor_path", None)
    if bool(single_path) == bool(composite_path):
        raise ValueError("exactly one audited PIT authority path is required")
    if composite_path:
        if not getattr(args, "expected_composite_root_sha256", None):
            raise ValueError("composite PIT authority requires its external root")
        if getattr(args, "expected_artifact_root_sha256", None) or getattr(
            args, "expected_coverage_audit_sha256", None
        ):
            raise ValueError("composite PIT authority forbids single-artifact anchors")
        return "ordered_composite"
    if not getattr(args, "expected_artifact_root_sha256", None) or not getattr(
        args, "expected_coverage_audit_sha256", None
    ):
        raise ValueError("single PIT authority requires artifact and coverage anchors")
    if getattr(args, "expected_composite_root_sha256", None):
        raise ValueError("single PIT authority forbids a composite root")
    return "single_artifact"


def _open_audited_authority(args):
    kind = _validate_audited_authority_args(args)
    if kind == "ordered_composite":
        universe = load_composite_universe_descriptor(
            args.composite_pit_descriptor_path,
            expected_composite_root_sha256=args.expected_composite_root_sha256,
        )
        if (
            universe.temporal_contract_sha256
            != args.expected_temporal_contract_sha256
            or universe.temporal_role != args.expected_temporal_role
        ):
            universe.close()
            raise ValueError("composite PIT temporal authority mismatch")
        return universe
    return AuditedPointInTimeUniverse.from_file(
        args.audited_pit_universe_path,
        expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
        expected_artifact_root_sha256=args.expected_artifact_root_sha256,
        expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
        expected_temporal_role=args.expected_temporal_role,
    )


def _exposure_multipliers(args) -> list[float]:
    exposure_multiplier = getattr(args, "exposure_multiplier", None)
    if exposure_multiplier is not None:
        return [max(float(exposure_multiplier), 0.0)]
    if not getattr(args, "exposure_sweep", False):
        return [1.0]
    maximum = max(float(getattr(args, "max_exposure_multiplier", 6.0) or 1.0), 1.0)
    values = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0]
    if maximum not in values:
        values.append(maximum)
    return [value for value in sorted(set(values)) if value <= maximum]


def _force_exposure_multipliers(args) -> bool:
    return getattr(args, "exposure_multiplier", None) is not None


def _split_csv_arg(value: str):
    if value is None:
        return None
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _probe_jiaoch_connectivity(timeout_seconds: float = 10.0) -> dict:
    """诊断 jiaoch 数据源在当前主机上的可达性,纯只读,不落盘。

    分四步逐级判定,任何一步失败立即返回已收集的信息:
      1. clock gate(Windows 上历史性 fail-closed)
      2. source profile 解析(token 存在性)
      3. DNS 解析
      4. HTTPS POST(可选,需 token)

    返回的 dict 直接交给 `_print_json`,便于 `jiaoch-connectivity-check` CLI 和
    未来 production-check 复用。不改任何 fail-closed 闸门,仅做探测。
    """
    from app.research_pit_collector import (
        PITCollectionError,
        SystemTrustedClock,
        UrllibTushareTransport,
    )

    import os
    import socket
    import ssl
    import time
    from urllib.parse import urlparse

    probe_started_at = time.time()
    payload: dict = {
        "probe": "jiaoch-connectivity/v1",
        "steps": [],
        "overall_status": "unknown",
    }

    def _step(name: str, **fields) -> dict:
        step = {"step": name, **fields}
        payload["steps"].append(step)
        return step

    # Step 1: clock gate(Windows 上历史性 fail-closed,先报告)
    try:
        clock_evidence = SystemTrustedClock().assert_synchronized()
        _step(
            "clock_gate",
            status="pass",
            source=clock_evidence.get("source"),
            synchronized=clock_evidence.get("synchronized"),
        )
    except PITCollectionError as exc:
        _step(
            "clock_gate",
            status="fail_closed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        payload["overall_status"] = "blocked_clock_gate"
        payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
        return payload

    # Step 2: source profile(URL 固定,token 可选——缺 token 也要能测 DNS/TLS)
    token = str(os.getenv("JIAOCH_TOKEN") or "")
    api_url = "https://jiaoch.site"
    proxy_url = None
    network_route = "direct"
    configured_proxy = str(os.getenv("JIAOCH_PROXY_URL") or "")
    if configured_proxy:
        try:
            from app.research_pit_sources import _validate_loopback_http_proxy

            proxy_url = _validate_loopback_http_proxy(configured_proxy)
            network_route = "loopback_http_proxy"
        except ValueError as exc:
            _step(
                "source_profile",
                status="fail",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            payload["overall_status"] = "blocked_source_config"
            payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
            return payload
    _step(
        "source_profile",
        status="pass",
        api_url=api_url,
        network_route=network_route,
        token_present=bool(token),
    )

    # Step 3: DNS + TCP(不需要 token)
    host = urlparse(api_url).hostname
    dns_started = time.time()
    try:
        addrinfo = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addrs = sorted({info[4][0] for info in addrinfo})
        _step(
            "dns_resolution",
            status="pass",
            host=host,
            addresses=addrs,
            elapsed_ms=int((time.time() - dns_started) * 1000),
        )
    except socket.gaierror as exc:
        _step(
            "dns_resolution",
            status="fail",
            host=host,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        payload["overall_status"] = "blocked_dns"
        payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
        return payload

    # Step 4: TLS 握手(不需要 token)
    port = urlparse(api_url).port or 443
    tls_started = time.time()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                subject = dict(x[0] for x in cert.get("subject", ())) if cert else {}
                issuer = dict(x[0] for x in cert.get("issuer", ())) if cert else {}
                tls_version = ssock.version()
        _step(
            "tls_handshake",
            status="pass",
            host=host,
            port=port,
            tls_version=tls_version,
            cert_subject_cn=subject.get("commonName"),
            cert_issuer_cn=issuer.get("commonName"),
            cert_not_after=cert.get("notAfter") if cert else None,
            elapsed_ms=int((time.time() - tls_started) * 1000),
        )
    except (ssl.SSLError, socket.timeout, OSError) as exc:
        _step(
            "tls_handshake",
            status="fail",
            host=host,
            port=port,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            elapsed_ms=int((time.time() - tls_started) * 1000),
        )
        payload["overall_status"] = "blocked_tls"
        payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
        return payload

    # Step 5: HTTPS POST(需要 token,最小 stock_basic 只取 1 行)
    if not token:
        _step(
            "https_post",
            status="skipped",
            reason="JIAOCH_TOKEN not set",
        )
        payload["overall_status"] = "ok_without_token"
        payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
        return payload

    post_started = time.time()
    transport = UrllibTushareTransport(proxy_url=proxy_url)
    body = (
        '{"api_name":"stock_basic","token":"<redacted>","params":'
        '{"exchange":"","list_status":"L","limit":"1"},'
        '"fields":"ts_code,symbol,name,area,industry,list_date"}'
    )
    # 真实 wire body 带真实 token;此处只在内存构造,不落盘
    wire_body = body.replace("<redacted>", token).encode("utf-8")
    try:
        response = transport.post(
            url=f"{api_url.rstrip('/')}/stock_basic",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-jiaoch-connectivity-probe/1",
            },
            body=wire_body,
            timeout_s=float(timeout_seconds),
            max_body_bytes=8 * 1024 * 1024,
        )
        body_echo = token.encode() in (response.body or b"")
        _step(
            "https_post",
            status="pass" if response.status == 200 and response.body_complete and not body_echo else "fail",
            http_status=response.status,
            body_complete=response.body_complete,
            body_bytes=len(response.body or b""),
            credential_echoed=body_echo,
            elapsed_ms=int((time.time() - post_started) * 1000),
        )
        payload["overall_status"] = "ok" if response.status == 200 and response.body_complete and not body_echo else "blocked_http"
    except Exception as exc:
        _step(
            "https_post",
            status="fail",
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            elapsed_ms=int((time.time() - post_started) * 1000),
        )
        payload["overall_status"] = "blocked_post"

    payload["elapsed_ms"] = int((time.time() - probe_started_at) * 1000)
    return payload



def _compact_research_sweep_payload(payload, sweep_payload, output_limit=12):
    source_summary = payload.get("summary") or {}
    source_keys = [
        "start_date",
        "end_date",
        "candidate_mode",
        "max_universe_symbols",
        "daily_prefilter_max_deep",
        "historical_candidate_days",
        "historical_market_breadth_days",
        "industry_rotation_context_enabled",
        "industry_rotation_max_boards",
        "industry_rotation_days",
        "dragon_tiger_context_enabled",
        "dragon_tiger_days",
        "dragon_tiger_fetch_errors",
        "dragon_tiger_caveat",
        "margin_eligibility_context_enabled",
        "margin_eligibility_scope",
        "margin_eligibility_days",
        "margin_eligibility_fetch_errors",
        "margin_eligibility_caveat",
        "candidate_count",
        "fetched_symbols",
        "raw_qualified_trade_count",
        "selected_trade_count",
        "trade_win_rate_pct",
        "portfolio_compounded_return_pct",
        "portfolio_max_drawdown_pct",
        "stop_loss_pct",
        "take_profit_pct",
        "trailing_stop_pct",
    ]
    row_keys = [
        "label",
        "required_signal_tags",
        "excluded_signal_tags",
        "market_levels",
        "exposure_multiplier",
        "annual_financing_rate_pct",
        "roundtrip_cost_bps",
        "slippage_bps",
        "capital_model",
        "pre_exit_calendar_gap_days",
        "prior_high_trailing_stop_pct",
        "prior_high_trailing_activation_pct",
        "partial_profit_activation_pct",
        "partial_profit_fraction",
        "correlation_threshold",
        "correlation_lookback_days",
        "correlation_skip_count",
        "selected_trade_count",
        "trade_win_count",
        "trade_nonwin_count",
        "signal_days",
        "trade_win_rate_pct",
        "trade_avg_return_pct",
        "portfolio_compounded_return_pct",
        "portfolio_max_drawdown_pct",
        "rolling_1y_latest_return_pct",
        "rolling_1y_latest_full_window",
        "rolling_1y_latest_trade_count",
        "rolling_1y_latest_active_position_days",
        "target_win_drawdown_pass",
        "target_one_year_return_pass",
        "target_all_pass",
        "target_gap_1y_return_pct",
    ]
    return {
        "source_summary": {key: source_summary.get(key) for key in source_keys},
        "sweep_summary": {
            key: sweep_payload.get(key)
            for key in [
                "qualified_trade_count",
                "available_tag_count",
                "spec_count",
                "returned_count",
                "target_win_drawdown_pass_count",
                "target_all_pass_count",
                "target_win_rate_pct",
                "target_drawdown_pct",
                "target_one_year_return_pct",
                "min_trades",
                "exposure_multipliers",
                "annual_financing_rate_pct",
                "roundtrip_cost_bps",
                "slippage_bps",
                "capital_model",
                "pre_exit_calendar_gap_days",
                "prior_high_trailing_stop_pct",
                "prior_high_trailing_activation_pct",
                "partial_profit_activation_pct",
                "partial_profit_fraction",
                "excluded_signal_tags",
                "correlation_threshold",
                "correlation_lookback_days",
                "correlation_min_periods",
            ]
        },
        "diagnostics": sweep_payload.get("diagnostics") or {},
        "top": [
            {key: row.get(key) for key in row_keys}
            for row in (sweep_payload.get("top") or [])[: max(0, output_limit)]
        ],
    }


def _parse_hold_days_list(value: str) -> list[int]:
    days = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            day = int(item)
        except ValueError:
            continue
        if day > 0 and day not in days:
            days.append(day)
    return days or [3, 5, 7, 10]


def _read_bounded_regular_file_snapshot(path: Path, *, max_bytes: int) -> bytes:
    """Read one immutable regular-file snapshot through a single descriptor."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("input artifact path is missing or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input artifact must be a regular file")
        if before.st_size <= 0:
            raise ValueError("input artifact size is invalid")
        if before.st_size > max_bytes:
            raise ValueError("input artifact exceeds size limit")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise ValueError("input artifact changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _load_qualified_trades_payload(path: str, *, raw_bytes: bytes = None) -> dict:
    if raw_bytes is None:
        payload = read_json(path, {})
    else:
        try:
            payload = _strict_json_loads(raw_bytes)
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("qualified trades payload is not valid strict JSON") from exc
    if isinstance(payload, list):
        return {"summary": {}, "qualified_trades": payload}
    if not isinstance(payload, dict):
        return {"summary": {}, "qualified_trades": []}
    return {
        "summary": payload.get("summary") or payload.get("source_summary") or {},
        "qualified_trades": payload.get("qualified_trades") or [],
    }


def _write_qualified_trades_payload(path: str, payload) -> dict:
    export_payload = {
        "summary": payload.get("summary") or {},
        "qualified_trades": payload.get("qualified_trades") or [],
    }
    write_json(path, export_payload)
    return {
        "path": path,
        "qualified_trade_count": len(export_payload["qualified_trades"]),
    }


def _treatment_input_plan_binding(plan: dict, artifact: dict) -> dict:
    producer_source_manifest = historical_treatment_producer_source_manifest()
    if producer_source_manifest["bundle_sha256"] != plan["producer_source_sha256"]:
        raise ValueError("treatment producer source bundle changed")
    return {
        "schema_version": "research-treatment-input-plan-binding/v2",
        "input_plan_sha256": plan["plan_sha256"],
        "input_plan_artifact_sha256": artifact["sha256"],
        "experiment_id": plan["experiment_id"],
        "producer": plan["producer"],
        "producer_source_sha256": plan["producer_source_sha256"],
        "producer_source_manifest": producer_source_manifest,
        "settings_fingerprint": plan["settings_fingerprint"],
    }


def _verify_treatment_bound_payload(payload: dict, plan: dict, artifact: dict) -> None:
    summary = payload.get("summary") if isinstance(payload, dict) else None
    trades = payload.get("qualified_trades") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or not isinstance(trades, list):
        raise ValueError("treatment output payload is incomplete")
    if summary.get("treatment_input_plan") != _treatment_input_plan_binding(
        plan, artifact
    ):
        raise ValueError("treatment output input-plan binding mismatch")
    parameters = plan["parameters"]
    for key in ("hold_days", "top_n", "symbol_cooldown_days", "max_active_positions"):
        if summary.get(key) != parameters[key]:
            raise ValueError(f"treatment output summary {key} mismatch")
    planned_holding_sessions = int(parameters["hold_days"])
    for trade in trades:
        if (
            not isinstance(trade, dict)
            or trade.get("planned_holding_sessions") != planned_holding_sessions
        ):
            raise ValueError("treatment trade holding-session evidence mismatch")


def _write_treatment_qualified_trades_payload(
    path: str,
    payload: dict,
    *,
    plan: dict,
    plan_artifact: dict,
) -> dict:
    export_payload = {
        "summary": payload.get("summary") or {},
        "qualified_trades": payload.get("qualified_trades") or [],
    }
    _verify_treatment_bound_payload(export_payload, plan, plan_artifact)
    encoded = (
        json.dumps(export_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, output_path)
        except FileExistsError as exc:
            raise ValueError("treatment output already exists; overwrite is forbidden") from exc
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    return {
        "path": path,
        "qualified_trade_count": len(export_payload["qualified_trades"]),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "input_plan_sha256": plan["plan_sha256"],
    }


def _materialize_content_addressed_snapshot(
    raw: bytes,
    *,
    sha256: str,
    output_dir: str,
) -> Path:
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("input snapshot hash mismatch")
    snapshot_dir = Path(output_dir) / "input-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"qualified-{sha256}.json"
    if snapshot_path.exists():
        existing = _read_bounded_regular_file_snapshot(
            snapshot_path, max_bytes=max(1, len(raw))
        )
        if existing != raw:
            raise ValueError("content-addressed input snapshot collision")
        return snapshot_path
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{snapshot_path.name}.", suffix=".tmp", dir=str(snapshot_dir)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, snapshot_path)
        except FileExistsError:
            existing = _read_bounded_regular_file_snapshot(
                snapshot_path, max_bytes=max(1, len(raw))
            )
            if existing != raw:
                raise ValueError("content-addressed input snapshot collision")
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    published = _read_bounded_regular_file_snapshot(
        snapshot_path, max_bytes=max(1, len(raw))
    )
    if published != raw:
        raise ValueError("content-addressed input snapshot verification failed")
    return snapshot_path


def _verified_artifact_descriptor(
    descriptor: dict,
    *,
    artifact_root: Path,
    label: str,
    max_bytes: int = 64 * 1024 * 1024,
) -> dict:
    """Return one normalized, byte-verified descriptor inside artifact_root."""

    if not isinstance(descriptor, dict):
        raise ValueError(f"{label} descriptor is invalid")
    path_value = descriptor.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError(f"{label} descriptor path is invalid")
    path = Path(path_value).resolve()
    root = artifact_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} artifact is outside its root") from exc
    raw = _read_bounded_regular_file_snapshot(path, max_bytes=max_bytes)
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    expected_sha256 = descriptor.get("file_sha256") or descriptor.get("sha256")
    if expected_sha256 != actual_sha256 or descriptor.get("bytes") != len(raw):
        raise ValueError(f"{label} artifact descriptor mismatch")
    if descriptor.get("filename") not in {None, path.name}:
        raise ValueError(f"{label} artifact filename mismatch")
    return {
        **descriptor,
        "path": str(path),
        "sha256": actual_sha256,
        "bytes": len(raw),
    }


def _canonical_payload_sha256(payload) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_controlled_validation_completion_failure(
    args, validation_started_event: dict, *, error_type: str
) -> dict:
    """Terminalize a v3 validation after an exception without reopening its claim."""

    validation_started_hash = validation_started_event.get("record_hash")
    if not isinstance(validation_started_hash, str) or len(validation_started_hash) != 64:
        raise ValueError("controlled validation started record hash is invalid")
    rows = read_experiment_ledger(
        args.ledger_path,
        require_existing_lock=True,
    )
    states = [
        row for row in rows if row.get("experiment_id") == args.experiment_id
    ]
    if not states:
        raise ValueError("controlled validation ledger state is missing")
    latest = states[-1]
    if latest.get("event_type") in {"failed", "aborted", "decision"}:
        return latest
    if (
        latest.get("registered_record_hash") != args.registered_record_hash
        or latest.get("validation_started_record_hash", validation_started_hash)
        != validation_started_hash
    ):
        raise ValueError("controlled validation ledger state is unbound")
    if latest.get("event_type") == "validation_started":
        return fail_validation_started_experiment_if_current(
            args.ledger_path,
            experiment_id=args.experiment_id,
            registered_record_hash=args.registered_record_hash,
            validation_started_record_hash=validation_started_hash,
            failure_code="VALIDATION_INTERNAL_ERROR",
            failure_phase="purged_validation",
            error_type=error_type,
        )
    if latest.get("event_type") == "completed":
        failure_sha256 = _canonical_payload_sha256(
            {
                "schema_version": "research-purged-validation-control-failure/v1",
                "error_type": error_type,
            }
        )
        return decide_completed_validation_if_current(
            args.ledger_path,
            experiment_id=args.experiment_id,
            registered_record_hash=args.registered_record_hash,
            validation_started_record_hash=validation_started_hash,
            completed_record_hash=latest["record_hash"],
            decision_code="PURGED_RESULT_INVALID",
            validation_result_sha256=failure_sha256,
            gate_result_sha256=failure_sha256,
            all_gates_pass=False,
        )
    raise ValueError("controlled validation ledger state is not recoverable")


_HISTORICAL_TREATMENT_SOURCE_FILES = (
    "jobs.py",
    "a_share_universe.py",
    "artifact_native_evidence.py",
    "artifact_outcome_evidence.py",
    "config.py",
    "execution.py",
    "indicators.py",
    "research_artifact_replay.py",
    "research_backtest.py",
    "research_common.py",
    "research_composite_universe.py",
    "research_context.py",
    "research_development_payload_fixture.py",
    "research_equity.py",
    "research_market_data.py",
    "research_partitions.py",
    "research_pit_store.py",
    "research_portfolio.py",
    "research_scope.py",
    "research_stats.py",
    "signal_tags.py",
    "signals.py",
    "strategy_signal_evidence.py",
)
_TREATMENT_PARAMETER_KEYS = {
    "max_deep",
    "top_n",
    "hold_days",
    "lookback_days",
    "max_universe_symbols",
    "live_snapshot",
    "buy_only",
    "min_score",
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "symbol_cooldown_days",
    "max_active_positions",
    "announcement_context",
    "announcement_lookback_days",
    "require_announcement_event",
    "require_all_announcement_events",
    "exclude_announcement_event",
    "require_market_level",
    "require_signal_tag",
    "require_all_signal_tags",
    "exclude_signal_tag",
    "min_prior_win_rate",
    "min_prior_avg_return",
    "max_prior_avg_adverse",
    "margin_eligibility_context",
}
_TREATMENT_MUTABLE_PARAMETERS = {
    "max_deep",
    "top_n",
    "hold_days",
    "buy_only",
    "min_score",
    "symbol_cooldown_days",
    "max_active_positions",
    "require_market_level",
    "require_signal_tag",
    "require_all_signal_tags",
    "exclude_signal_tag",
    "min_prior_win_rate",
    "min_prior_avg_return",
    "max_prior_avg_adverse",
}
_RESEARCH_GENERATION_SETTINGS_FIELDS = (
    "scan_min_amount",
    "scan_min_price",
    "scan_max_price",
    "max_entry_gap_up_pct",
    "max_entry_gap_down_pct",
    "locked_limit_gap_pct",
    "max_entry_intraday_range_pct",
    "min_backtest_trades",
    "min_backtest_win_rate",
    "min_backtest_avg_return",
    "max_backtest_avg_adverse",
)


def historical_treatment_producer_source_sha256() -> str:
    return historical_treatment_producer_source_manifest()["bundle_sha256"]


def historical_treatment_producer_source_manifest() -> dict:
    app_dir = Path(__file__).resolve().parent
    files = {}
    for relative_path in _HISTORICAL_TREATMENT_SOURCE_FILES:
        raw = _read_bounded_regular_file_snapshot(
            app_dir / relative_path, max_bytes=4 * 1024 * 1024
        )
        files[f"app/{relative_path}"] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "schema_version": "research-historical-producer-source-bundle/v2",
        "files": files,
    }
    manifest["bundle_sha256"] = _canonical_payload_sha256(manifest)
    return manifest


def research_generation_settings_fingerprint(settings) -> dict:
    values = {}
    for field in _RESEARCH_GENERATION_SETTINGS_FIELDS:
        try:
            value = float(getattr(settings, field))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"research generation setting {field} is invalid") from exc
        if not math.isfinite(value):
            raise ValueError(f"research generation setting {field} is not finite")
        values[field] = value
    payload = {
        "schema_version": "research-generation-settings/v2",
        "values": values,
    }
    payload["settings_sha256"] = _canonical_payload_sha256(payload)
    return payload


def _require_lower_sha256(value, label: str, *, allow_none: bool = False):
    if value is None and allow_none:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


_DEVELOPMENT_PAYLOAD_FIXTURE_BINDING_KEYS = {
    "schema_version",
    "workspace_root",
    "fixture_root",
    "fixture_id",
    "manifest_file_sha256",
    "receipt_file_sha256",
    "payload_descriptor_file_sha256",
    "payload_files_root_sha256",
    "source_descriptor_path",
    "source_descriptor_file_sha256",
    "composite_root_sha256",
    "temporal_contract_sha256",
    "producer_source_sha256",
    "temporal_role",
    "date_bounds",
    "source_anchors",
}

_DEVELOPMENT_TREATMENT_DATA_CUTOFF = "2026-07-10"
_DEVELOPMENT_TREATMENT_ACCEPTANCE_GATES = {
    "net_annualized_return_pct_min": 50.0,
    "win_rate_pct_min": 52.0,
    "win_rate_pct_max": 60.0,
    "max_drawdown_pct_max": 15.0,
    "profit_factor_min": 1.3,
    "calmar_min": 1.5,
}
_DEVELOPMENT_TREATMENT_VALIDATION_PROTOCOL = {
    "schema_version": "research-purged-validation-protocol/v1",
    "train_days": 365,
    "validation_days": 90,
    "step_days": 90,
    "embargo_days": 5,
    "minimum_oos_trades": 200,
    "exposure_multiplier": 1.0,
    "pre_exit_calendar_gap_days": 0,
    "partial_profit_activation_pct": None,
    "partial_profit_fraction": 0.0,
    "correlation_threshold": None,
    "correlation_lookback_days": 60,
    "artifact_dir_relative": "validation-artifacts",
    "pre_purged_development_gate": {
        "schema_version": "research-development-pre-purged-gate/v1",
        "fixed_spec": True,
        "minimum_trades": 200,
    },
}

_PRECOMPUTE_EXECUTION_PLAN_KEYS_V1 = {
    "schema_version",
    "control_required",
    "launcher_ready_schema",
    "minimum_registration_sequence_exclusive",
    "workspace_root",
    "ledger_path",
    "run_root",
    "qualified_trades_output_path",
    "cache_dir",
    "claim_parent",
    "sandbox_root",
    "audit_dir",
    "run_result_receipt_path",
    "network_calls_allowed",
    "single_writer",
}
_PRECOMPUTE_EXECUTION_PLAN_KEYS_V2 = {
    *_PRECOMPUTE_EXECUTION_PLAN_KEYS_V1,
    "temporal_contract_path",
    "progress_every",
    "parent_proof_path",
}
_PRECOMPUTE_EXECUTION_PLAN_KEYS_V3 = {
    *_PRECOMPUTE_EXECUTION_PLAN_KEYS_V2,
    "legacy_quarantine_sha256",
}
_PRECOMPUTE_EXECUTION_PLAN_KEYS_V4 = {
    *_PRECOMPUTE_EXECUTION_PLAN_KEYS_V3,
    "control_request_binding_sha256",
}
_CONTROL_REQUEST_BINDING_KEYS_V1 = {
    "schema_version",
    "request_sha256",
    "control_source_bundle_sha256",
    "ledger",
}
_CONTROL_REQUEST_BINDING_LEDGER_KEYS_V1 = {
    "path",
    "expected_file_sha256",
    "expected_lock_file_sha256",
    "expected_tip_sequence",
    "expected_tip_record_hash",
}


def _validate_development_treatment_acceptance_gates(payload) -> dict:
    if (
        not isinstance(payload, dict)
        or set(payload) != set(_DEVELOPMENT_TREATMENT_ACCEPTANCE_GATES)
    ):
        raise ValueError("treatment acceptance gates are invalid")
    for key, expected in _DEVELOPMENT_TREATMENT_ACCEPTANCE_GATES.items():
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("treatment acceptance gates are invalid")
        if not math.isfinite(float(value)) or float(value) != expected:
            raise ValueError("treatment acceptance gates are invalid")
    return payload


def _validate_development_treatment_validation_protocol(payload) -> dict:
    if payload != _DEVELOPMENT_TREATMENT_VALIDATION_PROTOCOL:
        raise ValueError("treatment validation protocol is invalid")
    return payload


def _validate_control_request_binding_v1(payload) -> dict:
    if not isinstance(payload, dict) or set(payload) != _CONTROL_REQUEST_BINDING_KEYS_V1:
        raise ValueError("treatment control request binding fields are invalid")
    if payload.get("schema_version") != "research-control-request-binding/v1":
        raise ValueError("treatment control request binding schema is invalid")
    _require_lower_sha256(
        payload.get("request_sha256"), "treatment control request hash"
    )
    _require_lower_sha256(
        payload.get("control_source_bundle_sha256"),
        "treatment control source bundle hash",
    )
    ledger = payload.get("ledger")
    expected_ledger_path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "research_experiments"
        / "ledger.jsonl"
    )
    if (
        not isinstance(ledger, dict)
        or set(ledger) != _CONTROL_REQUEST_BINDING_LEDGER_KEYS_V1
        or ledger.get("path") != str(expected_ledger_path)
        or ledger.get("expected_tip_sequence") != 126
    ):
        raise ValueError("treatment control request ledger binding is invalid")
    _require_lower_sha256(
        ledger.get("expected_file_sha256"), "treatment control ledger file hash"
    )
    _require_lower_sha256(
        ledger.get("expected_lock_file_sha256"),
        "treatment control ledger lock hash",
    )
    _require_lower_sha256(
        ledger.get("expected_tip_record_hash"),
        "treatment control ledger tip hash",
    )
    return payload


def _validate_precompute_execution_plan(
    payload,
    *,
    output_basename: str,
    experiment_id: str,
    legacy_quarantine: dict | None = None,
) -> dict:
    schema = payload.get("schema_version") if isinstance(payload, dict) else None
    expected_keys = {
        "research-precompute-execution-plan/v1": _PRECOMPUTE_EXECUTION_PLAN_KEYS_V1,
        "research-precompute-execution-plan/v2": _PRECOMPUTE_EXECUTION_PLAN_KEYS_V2,
        "research-precompute-execution-plan/v3": _PRECOMPUTE_EXECUTION_PLAN_KEYS_V3,
        "research-precompute-execution-plan/v4": _PRECOMPUTE_EXECUTION_PLAN_KEYS_V4,
    }.get(schema)
    expected_ready = {
        "research-precompute-execution-plan/v1": "research-launcher-ready/v3",
        "research-precompute-execution-plan/v2": "research-launcher-ready/v4",
        "research-precompute-execution-plan/v3": "research-launcher-ready/v5",
        "research-precompute-execution-plan/v4": "research-launcher-ready/v5",
    }.get(schema)
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or schema
        not in {
            "research-precompute-execution-plan/v1",
            "research-precompute-execution-plan/v2",
            "research-precompute-execution-plan/v3",
            "research-precompute-execution-plan/v4",
        }
        or payload.get("control_required") is not True
        or payload.get("launcher_ready_schema") != expected_ready
        or payload.get("minimum_registration_sequence_exclusive") != 126
        or payload.get("network_calls_allowed") != 0
        or payload.get("single_writer") is not True
    ):
        raise ValueError("treatment precompute execution contract is invalid")

    workspace = Path(__file__).resolve().parent.parent
    supplied_workspace = Path(str(payload.get("workspace_root") or ""))
    if (
        not supplied_workspace.is_absolute()
        or supplied_workspace != workspace
        or supplied_workspace.resolve(strict=True) != workspace
    ):
        raise ValueError("treatment precompute workspace path is invalid")

    path_keys = {
        key: Path(str(payload.get(key) or ""))
        for key in (
            "ledger_path",
            "run_root",
            "qualified_trades_output_path",
            "cache_dir",
            "claim_parent",
            "sandbox_root",
            "audit_dir",
            "run_result_receipt_path",
            *(
                ("temporal_contract_path", "parent_proof_path")
                if schema
                in {
                    "research-precompute-execution-plan/v2",
                    "research-precompute-execution-plan/v3",
                    "research-precompute-execution-plan/v4",
                }
                else ()
            ),
        )
    }
    for key, path in path_keys.items():
        if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
            raise ValueError(f"treatment precompute {key} path is invalid")
        resolved = path.resolve(strict=False)
        if path != resolved:
            raise ValueError(f"treatment precompute {key} path alias is forbidden")
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                f"treatment precompute {key} path is outside workspace"
            ) from exc
        existing = resolved
        while not existing.exists():
            if existing == workspace:
                break
            existing = existing.parent
        metadata = existing.lstat()
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if existing.is_symlink() or attributes & int(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError(f"treatment precompute {key} path is unsafe")

    expected_ledger = workspace / "data" / "research_experiments" / "ledger.jsonl"
    if path_keys["ledger_path"] != expected_ledger:
        raise ValueError("treatment precompute ledger path is invalid")
    run_root = path_keys["run_root"]
    if schema in {
        "research-precompute-execution-plan/v2",
        "research-precompute-execution-plan/v3",
        "research-precompute-execution-plan/v4",
    }:
        expected_run_parent = workspace / "tmp" / (
            "research-precompute-runs-v3"
            if schema
            in {
                "research-precompute-execution-plan/v3",
                "research-precompute-execution-plan/v4",
            }
            else "research-precompute-runs-v2"
        )
        if (
            run_root.parent != expected_run_parent
            or Path(experiment_id).name != experiment_id
            or run_root.name != experiment_id
        ):
            raise ValueError("treatment precompute run root namespace mismatch")
    control_root = run_root / "control"
    expected_paths = {
        "qualified_trades_output_path": run_root / output_basename,
        "cache_dir": run_root / "cache",
        "claim_parent": control_root / "claims",
        "sandbox_root": control_root / "sandbox",
        "audit_dir": control_root / "sandbox" / "audit",
        "run_result_receipt_path": control_root / "precompute-run-result.json",
        **(
            {
                "temporal_contract_path": workspace
                / "data"
                / "research_partitions"
                / "frozen-v1.json",
                "parent_proof_path": control_root
                / "parent-publication-proof.json",
            }
            if schema
            in {
                "research-precompute-execution-plan/v2",
                "research-precompute-execution-plan/v3",
                "research-precompute-execution-plan/v4",
            }
            else {}
        ),
    }
    for key, expected in expected_paths.items():
        if path_keys[key] != expected:
            label = "output path" if key == "qualified_trades_output_path" else key
            raise ValueError(f"treatment precompute {label} binding mismatch")
    if schema in {
        "research-precompute-execution-plan/v2",
        "research-precompute-execution-plan/v3",
        "research-precompute-execution-plan/v4",
    } and (
        isinstance(payload.get("progress_every"), bool)
        or payload.get("progress_every") != 25
    ):
        raise ValueError("treatment precompute progress interval is invalid")
    if schema in {
        "research-precompute-execution-plan/v3",
        "research-precompute-execution-plan/v4",
    }:
        if legacy_quarantine is None or (
            payload.get("legacy_quarantine_sha256")
            != quarantine_binding_sha256_v1(legacy_quarantine)
        ):
            raise ValueError("treatment precompute quarantine binding is invalid")
    if schema == "research-precompute-execution-plan/v4":
        _require_lower_sha256(
            payload.get("control_request_binding_sha256"),
            "treatment control request binding hash",
        )
    return payload


def _validate_development_payload_fixture_binding(payload) -> dict:
    if (
        not isinstance(payload, dict)
        or set(payload) != _DEVELOPMENT_PAYLOAD_FIXTURE_BINDING_KEYS
        or payload.get("schema_version")
        != "research-development-payload-fixture-binding/v1"
    ):
        raise ValueError("treatment development fixture binding fields are invalid")
    for key in ("workspace_root", "fixture_root", "source_descriptor_path"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
            raise ValueError(f"treatment development fixture {key} is invalid")
    for key in (
        "fixture_id",
        "manifest_file_sha256",
        "receipt_file_sha256",
        "payload_descriptor_file_sha256",
        "payload_files_root_sha256",
        "source_descriptor_file_sha256",
        "composite_root_sha256",
        "temporal_contract_sha256",
        "producer_source_sha256",
    ):
        _require_lower_sha256(payload.get(key), f"treatment development fixture {key}")
    if payload.get("temporal_role") != "development":
        raise ValueError("treatment development fixture temporal role is invalid")
    if payload.get("date_bounds") != {
        "start_date": "2022-01-04",
        "end_date": "2023-12-29",
    }:
        raise ValueError("treatment development fixture date bounds are invalid")
    anchors = payload.get("source_anchors")
    if (
        not isinstance(anchors, list)
        or len(anchors) != 2
        or any(not isinstance(anchor, dict) for anchor in anchors)
    ):
        raise ValueError("treatment development fixture source anchors are invalid")
    return payload


def _absolute_non_alias_path(value: str, label: str) -> Path:
    declared = Path(value)
    if not declared.is_absolute():
        raise ValueError(f"treatment development fixture {label} is invalid")
    resolved = declared.resolve()
    if os.path.normcase(str(declared)) != os.path.normcase(str(resolved)):
        raise ValueError(f"treatment development fixture {label} alias is invalid")
    try:
        metadata = declared.lstat()
    except OSError as exc:
        raise ValueError(f"treatment development fixture {label} is missing") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if declared.is_symlink() or attributes & int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError(f"treatment development fixture {label} alias is invalid")
    return resolved


def _fixture_bound_source_descriptor_path(binding: dict) -> Path:
    workspace = _absolute_non_alias_path(
        binding["workspace_root"], "workspace root"
    )
    if workspace != Path.cwd().resolve():
        raise ValueError("treatment development fixture workspace root mismatch")
    descriptor = _absolute_non_alias_path(
        binding["source_descriptor_path"], "source descriptor"
    )
    try:
        descriptor.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            "treatment development fixture source descriptor is outside workspace"
        ) from exc
    raw = _read_bounded_regular_file_snapshot(descriptor, max_bytes=64 * 1024)
    if hashlib.sha256(raw).hexdigest() != binding["source_descriptor_file_sha256"]:
        raise ValueError("treatment development fixture source descriptor hash mismatch")
    return descriptor


def _verify_development_payload_fixture_binding(
    binding: dict,
    *,
    composite_pit_descriptor_path: str,
) -> dict:
    descriptor = _fixture_bound_source_descriptor_path(binding)
    supplied_descriptor = _absolute_non_alias_path(
        composite_pit_descriptor_path, "source descriptor"
    )
    if supplied_descriptor != descriptor:
        raise ValueError("treatment development fixture source descriptor mismatch")
    try:
        verified = verify_development_payload_fixture(
            binding["fixture_root"],
            expected_fixture_id=binding["fixture_id"],
            expected_manifest_file_sha256=binding["manifest_file_sha256"],
            expected_receipt_file_sha256=binding["receipt_file_sha256"],
            expected_composite_root_sha256=binding["composite_root_sha256"],
            expected_temporal_contract_sha256=binding["temporal_contract_sha256"],
            expected_producer_source_sha256=binding["producer_source_sha256"],
            expected_source_descriptor_file_sha256=binding[
                "source_descriptor_file_sha256"
            ],
            expected_source_anchors=binding["source_anchors"],
            expected_workspace_root=binding["workspace_root"],
        )
    except DevelopmentPayloadFixtureError as exc:
        raise ValueError("treatment development fixture verification failed") from exc
    expected_values = {
        "fixture_id": binding["fixture_id"],
        "manifest_file_sha256": binding["manifest_file_sha256"],
        "receipt_file_sha256": binding["receipt_file_sha256"],
        "payload_descriptor_file_sha256": binding[
            "payload_descriptor_file_sha256"
        ],
        "payload_files_root_sha256": binding["payload_files_root_sha256"],
        "source_descriptor_file_sha256": binding[
            "source_descriptor_file_sha256"
        ],
        "temporal_role": binding["temporal_role"],
        "date_bounds": binding["date_bounds"],
        "composite_root_sha256": binding["composite_root_sha256"],
        "temporal_contract_sha256": binding["temporal_contract_sha256"],
        "segments": binding["source_anchors"],
    }
    if any(verified.get(key) != value for key, value in expected_values.items()):
        raise ValueError("treatment development fixture binding mismatch")
    if Path(verified.get("path") or "").resolve() != Path(
        binding["fixture_root"]
    ).resolve() or Path(verified.get("workspace_root") or "").resolve() != Path(
        binding["workspace_root"]
    ).resolve():
        raise ValueError("treatment development fixture path mismatch")
    if _read_bounded_regular_file_snapshot(descriptor, max_bytes=64 * 1024) != _read_bounded_regular_file_snapshot(
        supplied_descriptor, max_bytes=64 * 1024
    ):
        raise ValueError("treatment development fixture source descriptor changed")
    return verified


def _validate_research_generation_settings_fingerprint(payload) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "values",
        "settings_sha256",
    }:
        raise ValueError("research generation settings fingerprint fields are invalid")
    if payload.get("schema_version") != "research-generation-settings/v2":
        raise ValueError("research generation settings fingerprint schema is invalid")
    values = payload.get("values")
    if not isinstance(values, dict) or set(values) != set(
        _RESEARCH_GENERATION_SETTINGS_FIELDS
    ):
        raise ValueError("research generation settings fields are invalid")
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"research generation setting {field} must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"research generation setting {field} is not finite")
    claimed = _require_lower_sha256(
        payload.get("settings_sha256"), "research generation settings hash"
    )
    unhashed = dict(payload)
    unhashed.pop("settings_sha256")
    if _canonical_payload_sha256(unhashed) != claimed:
        raise ValueError("research generation settings hash mismatch")
    return payload


def _validate_treatment_input_plan_payload(payload: dict, raw: bytes) -> tuple[dict, dict]:
    base_keys = {
        "schema_version",
        "experiment_id",
        "producer",
        "producer_source_sha256",
        "output_basename",
        "temporal_role",
        "start_date",
        "end_date",
        "authority",
        "settings_fingerprint",
        "treatment",
        "baseline_parameters",
        "parameters",
        "plan_sha256",
    }
    schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
    if schema_version == "research-treatment-input-plan/v2":
        expected_keys = base_keys
    elif schema_version in {
        "research-treatment-input-plan/v3",
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        expected_keys = {
            *base_keys,
            "development_payload_fixture",
            "data_cutoff",
            "acceptance_gates",
            *(
                {"validation_protocol"}
                if schema_version
                in {
                    "research-treatment-input-plan/v5",
                    "research-treatment-input-plan/v6",
                    "research-treatment-input-plan/v7",
                }
                else set()
            ),
            *(
                {"legacy_quarantine"}
                if schema_version
                in {
                    "research-treatment-input-plan/v6",
                    "research-treatment-input-plan/v7",
                }
                else set()
            ),
            *(
                {"control_request_binding"}
                if schema_version == "research-treatment-input-plan/v7"
                else set()
            ),
            *(
                {"precompute_execution"}
                if schema_version
                in {
                    "research-treatment-input-plan/v4",
                    "research-treatment-input-plan/v5",
                    "research-treatment-input-plan/v6",
                    "research-treatment-input-plan/v7",
                }
                else set()
            ),
        }
    else:
        raise ValueError("unsupported treatment input plan schema")
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("treatment input plan fields are invalid")
    experiment_id = str(payload.get("experiment_id") or "").strip()
    if not experiment_id or len(experiment_id) > 128:
        raise ValueError("treatment input plan experiment_id is invalid")
    if payload.get("producer") != "research-historical-universe":
        raise ValueError("treatment input plan producer is invalid")
    producer_sha = _require_lower_sha256(
        payload.get("producer_source_sha256"), "treatment producer source hash"
    )
    if producer_sha != historical_treatment_producer_source_sha256():
        raise ValueError("treatment producer source hash mismatch")
    output_basename = str(payload.get("output_basename") or "").strip()
    if not output_basename or Path(output_basename).name != output_basename:
        raise ValueError("treatment output basename is invalid")
    if payload.get("temporal_role") != "development":
        raise ValueError("treatment input plan temporal role must be development")
    try:
        start = date.fromisoformat(str(payload.get("start_date")))
        end = date.fromisoformat(str(payload.get("end_date")))
    except ValueError as exc:
        raise ValueError("treatment input plan date is invalid") from exc
    if start > end:
        raise ValueError("treatment input plan date range is reversed")

    authority = payload.get("authority")
    authority_keys = {
        "kind",
        "artifact_root_sha256",
        "coverage_audit_sha256",
        "composite_root_sha256",
        "temporal_contract_sha256",
    }
    if not isinstance(authority, dict) or set(authority) != authority_keys:
        raise ValueError("treatment authority fields are invalid")
    if authority.get("kind") == "single_audited_artifact":
        _require_lower_sha256(
            authority.get("artifact_root_sha256"), "treatment artifact root hash"
        )
        _require_lower_sha256(
            authority.get("coverage_audit_sha256"), "treatment coverage audit hash"
        )
        if authority.get("composite_root_sha256") is not None:
            raise ValueError("single treatment authority forbids a composite root")
    elif authority.get("kind") == "ordered_composite":
        _require_lower_sha256(
            authority.get("composite_root_sha256"), "treatment composite root hash"
        )
        if (
            authority.get("artifact_root_sha256") is not None
            or authority.get("coverage_audit_sha256") is not None
        ):
            raise ValueError("composite treatment authority forbids single-artifact roots")
    else:
        raise ValueError("treatment authority kind is invalid")
    _require_lower_sha256(
        authority.get("temporal_contract_sha256"), "treatment temporal contract hash"
    )
    if schema_version in {
        "research-treatment-input-plan/v3",
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        binding = _validate_development_payload_fixture_binding(
            payload.get("development_payload_fixture")
        )
        if (
            authority.get("kind") != "ordered_composite"
            or authority.get("composite_root_sha256")
            != binding["composite_root_sha256"]
            or authority.get("temporal_contract_sha256")
            != binding["temporal_contract_sha256"]
        ):
            raise ValueError("treatment development fixture authority mismatch")
        if payload.get("data_cutoff") != _DEVELOPMENT_TREATMENT_DATA_CUTOFF:
            raise ValueError("treatment data cutoff is invalid")
        _validate_development_treatment_acceptance_gates(
            payload.get("acceptance_gates")
        )
        if schema_version in {
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }:
            _validate_development_treatment_validation_protocol(
                payload.get("validation_protocol")
            )
        if schema_version in {
            "research-treatment-input-plan/v4",
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }:
            legacy_quarantine = (
                validate_frozen_quarantine_binding_v1(
                    payload.get("legacy_quarantine")
                )
                if schema_version
                in {
                    "research-treatment-input-plan/v6",
                    "research-treatment-input-plan/v7",
                }
                else None
            )
            control_request_binding = (
                _validate_control_request_binding_v1(
                    payload.get("control_request_binding")
                )
                if schema_version == "research-treatment-input-plan/v7"
                else None
            )
            _validate_precompute_execution_plan(
                payload.get("precompute_execution"),
                output_basename=output_basename,
                experiment_id=experiment_id,
                legacy_quarantine=legacy_quarantine,
            )
            execution_schema = payload["precompute_execution"]["schema_version"]
            expected_execution_schema = {
                "research-treatment-input-plan/v4": "research-precompute-execution-plan/v1",
                "research-treatment-input-plan/v5": "research-precompute-execution-plan/v2",
                "research-treatment-input-plan/v6": "research-precompute-execution-plan/v3",
                "research-treatment-input-plan/v7": "research-precompute-execution-plan/v4",
            }[schema_version]
            if execution_schema != expected_execution_schema:
                raise ValueError("treatment plan execution schema mismatch")
            if control_request_binding is not None and (
                payload["precompute_execution"].get("control_request_binding_sha256")
                != _canonical_payload_sha256(control_request_binding)
            ):
                raise ValueError("treatment control request binding mismatch")
    _validate_research_generation_settings_fingerprint(
        payload.get("settings_fingerprint")
    )

    parameters = payload.get("parameters")
    baseline_parameters = payload.get("baseline_parameters")
    if not isinstance(parameters, dict) or set(parameters) != _TREATMENT_PARAMETER_KEYS:
        raise ValueError("treatment parameter fields are invalid")
    if (
        not isinstance(baseline_parameters, dict)
        or set(baseline_parameters) != _TREATMENT_PARAMETER_KEYS
    ):
        raise ValueError("treatment baseline parameter fields are invalid")
    treatment = payload.get("treatment")
    if not isinstance(treatment, dict) or set(treatment) != {
        "parameter",
        "baseline",
        "candidate",
    }:
        raise ValueError("treatment single-change fields are invalid")
    parameter = treatment.get("parameter")
    if parameter not in _TREATMENT_MUTABLE_PARAMETERS:
        raise ValueError("treatment parameter is not allowed")
    for label in ("baseline", "candidate"):
        value = treatment.get(label)
        if isinstance(value, (dict, list)) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError(f"treatment {label} must be a finite scalar")
    if treatment.get("baseline") == treatment.get("candidate"):
        raise ValueError("treatment baseline and candidate must differ")
    if baseline_parameters.get(parameter) != treatment.get("baseline"):
        raise ValueError("treatment baseline does not match baseline parameters")
    if parameters.get(parameter) != treatment.get("candidate"):
        raise ValueError("treatment candidate does not match generation parameters")
    changed_parameters = {
        key
        for key in _TREATMENT_PARAMETER_KEYS
        if baseline_parameters.get(key) != parameters.get(key)
    }
    if changed_parameters != {parameter}:
        raise ValueError("treatment plan must change exactly one generation parameter")
    plan_sha256 = _require_lower_sha256(
        payload.get("plan_sha256"), "treatment plan hash"
    )
    unhashed = dict(payload)
    unhashed.pop("plan_sha256")
    if _canonical_payload_sha256(unhashed) != plan_sha256:
        raise ValueError("treatment plan hash mismatch")
    return payload, {
        "basename": None,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _load_treatment_input_plan(path: str) -> tuple[dict, dict]:
    plan_path = Path(path)
    raw = _read_bounded_regular_file_snapshot(plan_path, max_bytes=64 * 1024)
    try:
        payload = _strict_json_loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("treatment input plan is not valid strict JSON") from exc
    payload, descriptor = _validate_treatment_input_plan_payload(payload, raw)
    descriptor["basename"] = plan_path.name
    return payload, descriptor


def _historical_treatment_parameters(args) -> dict:
    return {key: getattr(args, key) for key in sorted(_TREATMENT_PARAMETER_KEYS)}


def _historical_treatment_authority(args) -> dict:
    if args.audited_pit_universe_path:
        return {
            "kind": "single_audited_artifact",
            "artifact_root_sha256": args.expected_artifact_root_sha256,
            "coverage_audit_sha256": args.expected_coverage_audit_sha256,
            "composite_root_sha256": None,
            "temporal_contract_sha256": args.expected_temporal_contract_sha256,
        }
    if args.composite_pit_descriptor_path:
        return {
            "kind": "ordered_composite",
            "artifact_root_sha256": None,
            "coverage_audit_sha256": None,
            "composite_root_sha256": args.expected_composite_root_sha256,
            "temporal_contract_sha256": args.expected_temporal_contract_sha256,
        }
    raise ValueError("treatment plan requires audited or composite authority")


def _validate_historical_treatment_plan(args, plan: dict) -> None:
    if (
        args.industry_rotation_context
        or args.industry_rotation_max_boards != 40
        or args.dragon_tiger_context
    ):
        raise ValueError("treatment plan forbids ignored industry or dragon-tiger CLI flags")
    if (
        args.live_snapshot
        or args.announcement_context
        or args.margin_eligibility_context
        or args.stop_loss_pct is not None
        or args.take_profit_pct is not None
        or args.trailing_stop_pct is not None
    ):
        raise ValueError("treatment plan requires artifact-native external-context defaults")
    if not args.qualified_trades_output:
        raise ValueError("treatment plan requires a qualified trades output")
    if Path(args.qualified_trades_output).name != plan["output_basename"]:
        raise ValueError("treatment plan output basename mismatch")
    if args.start_date != plan["start_date"] or args.end_date != plan["end_date"]:
        raise ValueError("treatment plan date mismatch")
    if args.expected_temporal_role != plan["temporal_role"]:
        raise ValueError("treatment plan temporal role mismatch")
    if _historical_treatment_authority(args) != plan["authority"]:
        raise ValueError("treatment plan authority mismatch")
    if plan["schema_version"] in {
        "research-treatment-input-plan/v3",
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        _verify_development_payload_fixture_binding(
            plan["development_payload_fixture"],
            composite_pit_descriptor_path=args.composite_pit_descriptor_path,
        )
    if plan["schema_version"] in {
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        execution = plan["precompute_execution"]
        if Path(args.qualified_trades_output) != Path(
            execution["qualified_trades_output_path"]
        ):
            raise ValueError("treatment plan exact output path mismatch")
        if Path(args.cache_dir) != Path(execution["cache_dir"]):
            raise ValueError("treatment plan exact cache path mismatch")
        if plan["schema_version"] in {
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        } and (
            Path(args.temporal_contract_path)
            != Path(execution["temporal_contract_path"])
            or args.progress_every != execution["progress_every"]
        ):
            raise ValueError("treatment plan exact runtime binding mismatch")
    if _historical_treatment_parameters(args) != plan["parameters"]:
        raise ValueError("treatment plan generation parameters mismatch")
    if Path(args.qualified_trades_output).exists():
        raise ValueError("treatment output already exists; overwrite is forbidden")


def _validate_historical_treatment_settings(settings, plan: dict) -> None:
    if research_generation_settings_fingerprint(settings) != plan["settings_fingerprint"]:
        raise ValueError("treatment plan research generation settings mismatch")


def _validate_treatment_plan_for_validation(
    args,
    *,
    authority_kind: str,
    strategy: dict,
    plan: dict,
) -> None:
    expected_kind = (
        "ordered_composite"
        if authority_kind == "ordered_composite"
        else "single_audited_artifact"
    )
    if plan["experiment_id"] != args.experiment_id:
        raise ValueError("treatment plan experiment_id mismatch")
    if plan["output_basename"] != Path(args.qualified_trades_path).name:
        raise ValueError("treatment plan qualified basename mismatch")
    if plan["start_date"] != args.start_date or plan["end_date"] != args.end_date:
        raise ValueError("treatment plan validation date mismatch")
    if plan["temporal_role"] != args.expected_temporal_role:
        raise ValueError("treatment plan validation temporal role mismatch")
    authority = plan["authority"]
    if authority["kind"] != expected_kind:
        raise ValueError("treatment plan validation authority kind mismatch")
    if authority["temporal_contract_sha256"] != args.expected_temporal_contract_sha256:
        raise ValueError("treatment plan validation temporal hash mismatch")
    if expected_kind == "single_audited_artifact":
        if (
            authority["artifact_root_sha256"] != args.expected_artifact_root_sha256
            or authority["coverage_audit_sha256"]
            != args.expected_coverage_audit_sha256
        ):
            raise ValueError("treatment plan validation authority mismatch")
    elif authority["composite_root_sha256"] != args.expected_composite_root_sha256:
        raise ValueError("treatment plan validation composite authority mismatch")
    if plan["schema_version"] in {
        "research-treatment-input-plan/v3",
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        _verify_development_payload_fixture_binding(
            plan["development_payload_fixture"],
            composite_pit_descriptor_path=args.composite_pit_descriptor_path,
        )
        expected_gates = _DEVELOPMENT_TREATMENT_ACCEPTANCE_GATES
        actual_gates = {
            "net_annualized_return_pct_min": strategy[
                "target_one_year_return_pct"
            ],
            "win_rate_pct_min": strategy["target_win_rate_pct"],
            "win_rate_pct_max": strategy["target_win_rate_max_pct"],
            "max_drawdown_pct_max": strategy["target_drawdown_pct"],
            "profit_factor_min": strategy["target_profit_factor"],
            "calmar_min": strategy["target_calmar"],
        }
        if actual_gates != expected_gates:
            raise ValueError("treatment plan validation acceptance gates mismatch")
        if plan["schema_version"] in {
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }:
            protocol = _validate_development_treatment_validation_protocol(
                plan["validation_protocol"]
            )
            exact_args = {
                "train_days": args.train_days,
                "validation_days": args.validation_days,
                "step_days": args.step_days,
                "embargo_days": args.embargo_days,
                "minimum_oos_trades": args.minimum_oos_trades,
                "exposure_multiplier": strategy["exposure_multiplier"],
                "pre_exit_calendar_gap_days": strategy[
                    "pre_exit_calendar_gap_days"
                ],
                "partial_profit_activation_pct": strategy[
                    "partial_profit_activation_pct"
                ],
                "partial_profit_fraction": strategy["partial_profit_fraction"],
                "correlation_threshold": strategy["correlation_threshold"],
                "correlation_lookback_days": strategy[
                    "correlation_lookback_days"
                ],
            }
            if any(protocol[key] != value for key, value in exact_args.items()):
                raise ValueError("treatment plan validation protocol mismatch")
            expected_artifact_dir = (
                Path(plan["precompute_execution"]["run_root"])
                / protocol["artifact_dir_relative"]
            )
            if Path(args.artifact_dir) != expected_artifact_dir:
                raise ValueError("treatment plan validation artifact path mismatch")
    if plan["schema_version"] in {
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        execution = plan["precompute_execution"]
        if Path(args.qualified_trades_path) != Path(
            execution["qualified_trades_output_path"]
        ):
            raise ValueError("treatment plan validation exact output path mismatch")
        if Path(args.ledger_path) != Path(execution["ledger_path"]):
            raise ValueError("treatment plan validation exact ledger path mismatch")
    parameters = plan["parameters"]
    expected_strategy = {
        "hold_days": parameters["hold_days"],
        "top_n": parameters["top_n"],
        "symbol_cooldown_days": parameters["symbol_cooldown_days"],
        "max_active_positions": parameters["max_active_positions"],
        "required_signal_tags": _split_csv_arg(parameters["require_signal_tag"])
        or [],
        "excluded_signal_tags": _split_csv_arg(parameters["exclude_signal_tag"])
        or [],
        "market_levels": _split_csv_arg(parameters["require_market_level"]) or [],
    }
    if any(strategy.get(key) != value for key, value in expected_strategy.items()):
        raise ValueError("treatment plan validation strategy mismatch")
    if parameters["require_all_signal_tags"]:
        raise ValueError("treatment plan requires unsupported all-tag validation semantics")


def _load_validation_input_plan(path: str) -> tuple[dict, dict]:
    plan_path = Path(path)
    raw = _read_bounded_regular_file_snapshot(plan_path, max_bytes=64 * 1024)
    try:
        payload = _strict_json_loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("validation input plan is not valid strict JSON") from exc
    if isinstance(payload, dict) and payload.get("schema_version") in {
        "research-treatment-input-plan/v2",
        "research-treatment-input-plan/v3",
        "research-treatment-input-plan/v4",
        "research-treatment-input-plan/v5",
        "research-treatment-input-plan/v6",
        "research-treatment-input-plan/v7",
    }:
        payload, descriptor = _validate_treatment_input_plan_payload(payload, raw)
        descriptor["basename"] = plan_path.name
        return payload, descriptor
    expected_keys = {
        "schema_version",
        "qualified_trades_basename",
        "authority_kind",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("validation input plan fields are invalid")
    if payload.get("schema_version") != "research-validation-input-plan/v1":
        raise ValueError("unsupported validation input plan schema")
    basename = str(payload.get("qualified_trades_basename") or "").strip()
    if not basename or Path(basename).name != basename:
        raise ValueError("validation input plan qualified basename is invalid")
    if payload.get("authority_kind") not in {
        "single_audited_artifact",
        "ordered_composite",
    }:
        raise ValueError("validation input plan authority kind is invalid")
    descriptor = {
        "basename": plan_path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    return payload, descriptor


def _preflight_treatment_validation_artifacts(
    args,
    *,
    qualified_raw: bytes,
    qualified_sha256: str,
    payload: dict,
    treatment_input_plan: dict,
    input_plan_artifact: dict,
    audited_universe,
) -> dict:
    """Compile and verify treatment evidence without consuming its ledger claim.

    This stage performs deterministic integrity work only.  Its artifacts are
    non-authoritative unless the caller subsequently wins the atomic claim and
    records a completed validation event binding every returned descriptor.
    """

    _verify_treatment_bound_payload(
        payload,
        treatment_input_plan,
        input_plan_artifact,
    )
    original_trades = payload.get("qualified_trades") or []
    if not original_trades:
        raise ValueError("treatment output contains no qualified trades")

    artifact_root = Path(args.artifact_dir).resolve()
    snapshot_path = _materialize_content_addressed_snapshot(
        qualified_raw,
        sha256=qualified_sha256,
        output_dir=args.artifact_dir,
    )
    snapshot_artifact = {
        "path": str(snapshot_path.resolve()),
        "sha256": qualified_sha256,
        "bytes": len(qualified_raw),
    }
    snapshot_artifact = _verified_artifact_descriptor(
        snapshot_artifact,
        artifact_root=artifact_root,
        label="qualified input snapshot",
    )
    snapshot_raw = _read_bounded_regular_file_snapshot(
        snapshot_path,
        max_bytes=64 * 1024 * 1024,
    )
    if snapshot_raw != qualified_raw:
        raise ValueError("treatment input snapshot changed")
    snapshot_payload = _load_qualified_trades_payload(
        str(snapshot_path), raw_bytes=snapshot_raw
    )
    _verify_treatment_bound_payload(
        snapshot_payload,
        treatment_input_plan,
        input_plan_artifact,
    )
    snapshot_trades = snapshot_payload.get("qualified_trades") or []
    if snapshot_trades != original_trades:
        raise ValueError("treatment input snapshot trade rows changed")

    native_payload = build_artifact_native_evidence(
        audited_universe=audited_universe,
        qualified_trades=snapshot_trades,
        qualified_trades_path=str(snapshot_path.resolve()),
        artifact_root=str(artifact_root),
    )
    native_artifact = write_artifact_native_evidence(
        args.artifact_dir, native_payload
    )
    native_artifact = _verified_artifact_descriptor(
        native_artifact,
        artifact_root=artifact_root,
        label="artifact-native evidence",
    )
    verified_native = verify_artifact_native_evidence(
        native_artifact["path"],
        audited_universe=audited_universe,
        artifact_root=str(artifact_root),
    )
    native_eligibility = verified_native.get("eligibility") or {}
    if (
        native_eligibility.get("eligible_for_development_validation") is not True
        or native_eligibility.get("eligible_for_final_validation") is not False
        or native_eligibility.get("final_oos_eligible") is not False
        or native_eligibility.get("reasons") != []
    ):
        raise ValueError(
            "artifact-native evidence is not eligible for development validation"
        )
    if (
        _read_bounded_regular_file_snapshot(
            snapshot_path, max_bytes=64 * 1024 * 1024
        )
        != qualified_raw
    ):
        raise ValueError("native evidence input snapshot changed")

    strict_artifact = write_strict_research_evidence_bundle(
        args.artifact_dir,
        audited_universe=audited_universe,
        artifact_native_evidence_path=native_artifact["path"],
    )
    strict_artifact = _verified_artifact_descriptor(
        strict_artifact,
        artifact_root=artifact_root,
        label="strict research evidence",
    )
    verified_strict = verify_research_evidence_bundle(
        strict_artifact["path"],
        audited_universe=audited_universe,
        artifact_root=str(artifact_root),
    )
    strict_eligibility = verified_strict.get("eligibility") or {}
    if (
        strict_eligibility.get("eligible_for_development_validation") is not True
        or strict_eligibility.get("eligible_for_final_validation") is not False
        or strict_eligibility.get("final_oos_eligible") is not False
        or strict_eligibility.get("reasons") != []
    ):
        raise ValueError(
            "strict research evidence is not eligible for development validation"
        )

    compiled_artifact = write_strict_qualified_trades_payload(
        args.artifact_dir,
        source_payload_path=str(snapshot_path.resolve()),
        strict_evidence_bundle_path=strict_artifact["path"],
        audited_universe=audited_universe,
    )
    compiled_artifact = _verified_artifact_descriptor(
        compiled_artifact,
        artifact_root=artifact_root,
        label="strict qualified trades",
    )
    compiled_path = Path(compiled_artifact["path"])
    compiled_raw = _read_bounded_regular_file_snapshot(
        compiled_path,
        max_bytes=64 * 1024 * 1024,
    )
    if hashlib.sha256(compiled_raw).hexdigest() != compiled_artifact["sha256"]:
        raise ValueError("strict qualified trades artifact descriptor mismatch")
    compiled_payload = _load_qualified_trades_payload(
        str(compiled_path), raw_bytes=compiled_raw
    )
    _verify_treatment_bound_payload(
        compiled_payload,
        treatment_input_plan,
        input_plan_artifact,
    )
    compiled_trades = compiled_payload.get("qualified_trades") or []
    if compiled_trades != original_trades:
        raise ValueError("strict qualified trades changed treatment rows")
    compiled_summary = compiled_payload.get("summary") or {}
    data_contract = validate_point_in_time_contract(
        compiled_summary,
        compiled_trades,
        artifact_base_dir=str(artifact_root),
        declared_start_date=args.start_date,
        declared_end_date=args.end_date,
        audited_universe=audited_universe,
    )
    if (
        data_contract.get("eligible_for_development_validation") is not True
        or data_contract.get("eligible_for_final_validation") is not False
        or data_contract.get("final_oos_eligible") is not False
    ):
        raise ValueError("strict qualified trades contract eligibility mismatch")

    return {
        "payload": compiled_payload,
        "qualified_trades": compiled_trades,
        "data_contract": data_contract,
        "qualified_input_snapshot": snapshot_artifact,
        "artifact_native_evidence": verified_native,
        "artifact_native_evidence_artifact": native_artifact,
        "strict_evidence_artifact": strict_artifact,
        "strict_qualified_artifact": compiled_artifact,
    }


def _reverify_treatment_validation_artifacts_after_claim(
    args,
    *,
    qualified_raw: bytes,
    treatment_input_plan: dict,
    input_plan_artifact: dict,
    audited_universe,
    preflight: dict,
    claimed_input_artifacts: dict,
) -> dict:
    """Rehash the claimed artifact chain before any strategy evaluation."""

    artifact_root = Path(args.artifact_dir).resolve()
    raw_expected = claimed_input_artifacts.get("qualified_treatment_output")
    raw_path = Path(str((raw_expected or {}).get("path") or "")).resolve()
    raw_actual = _verified_artifact_descriptor(
        raw_expected,
        artifact_root=raw_path.parent,
        label="qualified treatment output",
    )
    if raw_actual != raw_expected:
        raise ValueError("qualified treatment output changed after validation claim")
    if (
        _read_bounded_regular_file_snapshot(
            raw_path, max_bytes=64 * 1024 * 1024
        )
        != qualified_raw
    ):
        raise ValueError("claimed qualified treatment output changed")
    descriptor_labels = {
        "qualified_input_snapshot": "qualified input snapshot",
        "artifact_native_evidence_artifact": "artifact-native evidence",
        "strict_evidence_artifact": "strict research evidence",
        "strict_qualified_artifact": "strict qualified trades",
    }
    verified_descriptors = {}
    for key, label in descriptor_labels.items():
        expected = preflight[key]
        actual = _verified_artifact_descriptor(
            expected,
            artifact_root=artifact_root,
            label=label,
        )
        if actual != expected:
            raise ValueError(f"{label} changed after validation claim")
        verified_descriptors[key] = actual

    snapshot = verified_descriptors["qualified_input_snapshot"]
    snapshot_raw = _read_bounded_regular_file_snapshot(
        Path(snapshot["path"]), max_bytes=64 * 1024 * 1024
    )
    if snapshot_raw != qualified_raw:
        raise ValueError("claimed qualified input snapshot changed")

    native_artifact = verified_descriptors[
        "artifact_native_evidence_artifact"
    ]
    verified_native = verify_artifact_native_evidence(
        native_artifact["path"],
        audited_universe=audited_universe,
        artifact_root=str(artifact_root),
    )
    if verified_native != preflight["artifact_native_evidence"]:
        raise ValueError("artifact-native evidence changed after validation claim")

    strict_artifact = verified_descriptors["strict_evidence_artifact"]
    verified_strict = verify_research_evidence_bundle(
        strict_artifact["path"],
        audited_universe=audited_universe,
        artifact_root=str(artifact_root),
    )
    if (
        strict_artifact.get("evidence_bundle_sha256")
        != verified_strict.get("evidence_bundle_sha256")
    ):
        raise ValueError("strict evidence changed after validation claim")

    compiled_artifact = verified_descriptors["strict_qualified_artifact"]
    compiled_raw = _read_bounded_regular_file_snapshot(
        Path(compiled_artifact["path"]), max_bytes=64 * 1024 * 1024
    )
    compiled_payload = _load_qualified_trades_payload(
        compiled_artifact["path"], raw_bytes=compiled_raw
    )
    _verify_treatment_bound_payload(
        compiled_payload,
        treatment_input_plan,
        input_plan_artifact,
    )
    compiled_trades = compiled_payload.get("qualified_trades") or []
    if compiled_trades != preflight["qualified_trades"]:
        raise ValueError("strict qualified trades changed after validation claim")
    data_contract = validate_point_in_time_contract(
        compiled_payload.get("summary") or {},
        compiled_trades,
        artifact_base_dir=str(artifact_root),
        declared_start_date=args.start_date,
        declared_end_date=args.end_date,
        audited_universe=audited_universe,
    )
    if data_contract != preflight["data_contract"]:
        raise ValueError("strict qualified contract changed after validation claim")
    final_raw = _verified_artifact_descriptor(
        raw_expected,
        artifact_root=raw_path.parent,
        label="qualified treatment output",
    )
    if final_raw != raw_expected:
        raise ValueError("qualified treatment output changed during claim verification")
    for key, label in descriptor_labels.items():
        if (
            _verified_artifact_descriptor(
                preflight[key],
                artifact_root=artifact_root,
                label=label,
            )
            != preflight[key]
        ):
            raise ValueError(f"{label} changed during claim verification")
    return {
        **preflight,
        **verified_descriptors,
        "payload": compiled_payload,
        "qualified_trades": compiled_trades,
        "data_contract": data_contract,
        "artifact_native_evidence": verified_native,
    }


def _build_validation_registration_contract(
    args,
    *,
    authority_kind: str,
    strategy: dict,
    validation: dict,
    input_plan: dict,
    input_plan_artifact: dict,
    single_change: str,
    precompute_control: dict | None = None,
) -> dict:
    precompute_ledger = None
    controlled_v5 = (
        precompute_control is not None
        and input_plan.get("schema_version") == "research-treatment-input-plan/v5"
    )
    controlled_v6 = (
        precompute_control is not None
        and input_plan.get("schema_version")
        in {
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
    )
    controlled_v7 = (
        precompute_control is not None
        and input_plan.get("schema_version") == "research-treatment-input-plan/v7"
    )
    legacy_quarantine = None
    legacy_quarantine_sha256 = None
    control_request_binding = None
    if precompute_control is not None:
        if (
            args.expected_ledger_sequence is None
            or args.expected_ledger_record_hash is None
        ):
            raise ValueError("controlled registration ledger tip is missing")
        ledger_before = None
        lock_before = None
        if controlled_v7:
            control_request_binding = _validate_control_request_binding_v1(
                input_plan.get("control_request_binding")
            )
            frozen_ledger = control_request_binding["ledger"]
            ledger_path = Path(args.ledger_path)
            lock_path = ledger_path.with_name(ledger_path.name + ".lock")
            if (
                str(ledger_path) != frozen_ledger["path"]
                or args.expected_ledger_sequence
                != frozen_ledger["expected_tip_sequence"]
                or args.expected_ledger_record_hash
                != frozen_ledger["expected_tip_record_hash"]
                or precompute_control.get("control_source_bundle_sha256")
                != control_request_binding["control_source_bundle_sha256"]
            ):
                raise ValueError("v7 registration control request binding mismatch")
            ledger_before = _read_bounded_regular_file_snapshot(
                ledger_path, max_bytes=64 * 1024 * 1024
            )
            lock_before = _read_bounded_regular_file_snapshot(
                lock_path, max_bytes=1024 * 1024
            )
            if (
                hashlib.sha256(ledger_before).hexdigest()
                != frozen_ledger["expected_file_sha256"]
                or hashlib.sha256(lock_before).hexdigest()
                != frozen_ledger["expected_lock_file_sha256"]
            ):
                raise ValueError("v7 registration ledger snapshot drifted")
        ledger_binding_builder = (
            build_precompute_ledger_binding_v4
            if controlled_v7
            else (
                build_precompute_ledger_binding_v3
                if controlled_v5 or controlled_v6
                else build_precompute_ledger_binding_v2
            )
        )
        precompute_ledger = ledger_binding_builder(
            args.ledger_path,
            workspace_root=Path(__file__).resolve().parent.parent,
            expected_sequence=args.expected_ledger_sequence,
            expected_record_hash=args.expected_ledger_record_hash,
        )
        if controlled_v7:
            ledger_path = Path(args.ledger_path)
            lock_path = ledger_path.with_name(ledger_path.name + ".lock")
            ledger_after = _read_bounded_regular_file_snapshot(
                ledger_path, max_bytes=64 * 1024 * 1024
            )
            lock_after = _read_bounded_regular_file_snapshot(
                lock_path, max_bytes=1024 * 1024
            )
            if (
                ledger_before != ledger_after
                or lock_before != lock_after
                or precompute_ledger.get("expected_tip_sequence")
                != control_request_binding["ledger"]["expected_tip_sequence"]
                or precompute_ledger.get("expected_tip_record_hash")
                != control_request_binding["ledger"]["expected_tip_record_hash"]
                or precompute_ledger.get("lock_file_sha256")
                != control_request_binding["ledger"]["expected_lock_file_sha256"]
                or precompute_ledger.get("pre_registration_ledger_file_sha256")
                != control_request_binding["ledger"]["expected_file_sha256"]
            ):
                raise ValueError("v7 registration ledger binding changed")
        if (controlled_v5 or controlled_v6) and precompute_control.get("schema_version") != (
            "research-precompute-control-binding/v2"
        ):
            raise ValueError("v5 registration requires parent publication proof")
        if controlled_v6:
            try:
                legacy_quarantine = validate_frozen_quarantine_binding_v1(
                    input_plan.get("legacy_quarantine")
                )
            except ValueError as exc:
                raise ValueError("v6 registration quarantine is invalid") from exc
            legacy_quarantine_sha256 = quarantine_binding_sha256_v1(
                legacy_quarantine
            )
            execution = input_plan.get("precompute_execution")
            if (
                not isinstance(execution, dict)
                or execution.get("schema_version")
                != (
                    "research-precompute-execution-plan/v4"
                    if controlled_v7
                    else "research-precompute-execution-plan/v3"
                )
                or execution.get("legacy_quarantine_sha256")
                != legacy_quarantine_sha256
                or (
                    controlled_v7
                    and execution.get("control_request_binding_sha256")
                    != _canonical_payload_sha256(control_request_binding)
                )
            ):
                raise ValueError("v6 registration quarantine is invalid")
    return {
        "schema_version": (
            "research-validation-registration/v4"
            if controlled_v6
            else (
                "research-validation-registration/v3"
                if controlled_v5
                else (
                    "research-validation-registration/v2"
                    if precompute_control is not None
                    else "research-validation-registration/v1"
                )
            )
        ),
        "intent": {
            "hypothesis": args.hypothesis,
            "expected_mechanism": args.expected_mechanism,
            "falsification_criterion": args.falsification_criterion,
            "exit_criterion": args.exit_criterion,
            "single_change": single_change,
        },
        "strategy": strategy,
        "validation": validation,
        "temporal_authority": {
            "authority_kind": authority_kind,
            "temporal_contract_sha256": args.expected_temporal_contract_sha256,
            "expected_temporal_role": args.expected_temporal_role,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "final_oos_start": args.final_oos_start,
            "expected_artifact_root_sha256": args.expected_artifact_root_sha256,
            "expected_composite_root_sha256": args.expected_composite_root_sha256,
            "expected_coverage_audit_sha256": args.expected_coverage_audit_sha256,
        },
        "input_plan": {
            "payload": input_plan,
            "artifact": input_plan_artifact,
        },
        **(
            {
                "precompute_control": precompute_control,
                "precompute_ledger": precompute_ledger,
                **(
                    {
                        "precompute_lifecycle": {
                            "schema_version": "research-precompute-lifecycle/v1",
                            "launch_started_event_schema": (
                                "research-precompute-launch-started/v1"
                            ),
                            "launcher_ready_schema": "research-launcher-ready/v4",
                            "run_result_schema": "research-precompute-run-result/v3",
                            "global_tip_cas": True,
                            "sole_nonterminal_cas": True,
                        }
                    }
                    if controlled_v5
                    else (
                        {
                            "precompute_lifecycle": {
                                "schema_version": "research-precompute-lifecycle/v2",
                                "launch_started_event_schema": (
                                    "research-precompute-launch-started/v2"
                                ),
                                "launcher_ready_schema": "research-launcher-ready/v5",
                                "run_result_schema": "research-precompute-run-result/v4",
                                "global_tip_cas": True,
                                "exact_quarantine_open_set_cas": True,
                                "legacy_quarantine_sha256": legacy_quarantine_sha256,
                            },
                            "legacy_quarantine": legacy_quarantine,
                            "legacy_quarantine_sha256": legacy_quarantine_sha256,
                        }
                        if controlled_v6
                        else {}
                    )
                ),
            }
            if precompute_control is not None
            else {}
        ),
    }


def _load_json_rows(path: str, *keys: str) -> list:
    payload = read_json(path, [])
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"expected JSON row list: {path}")


def _load_current_pool_descriptor(path: str, expected_schema: str) -> dict:
    try:
        descriptor_path = Path(path)
        payload = _strict_json_loads(descriptor_path.read_bytes())
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != expected_schema
            or payload.get("source_id") != "jiaoch"
            or not isinstance(payload.get("items"), list)
        ):
            raise ValueError
        as_of = str(payload.get("as_of") or "")
        if len(as_of) == 10:
            source_date = date.fromisoformat(as_of).isoformat()
        else:
            instant = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError
            source_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if expected_schema in {
            "current-pool-universe-input/v1",
            "current-pool-risk-input/v1",
        }:
            embedded_sha256 = payload.get("descriptor_sha256")
            unsigned_payload = {
                key: value for key, value in payload.items() if key != "descriptor_sha256"
            }
            canonical_unsigned = json.dumps(
                unsigned_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            verified_sha256 = hashlib.sha256(canonical_unsigned).hexdigest()
            if embedded_sha256 != verified_sha256:
                raise ValueError
            if len(descriptor_path.stem) == 64 and descriptor_path.stem != verified_sha256:
                raise ValueError
            retrieved_at = datetime.fromisoformat(str(payload.get("retrieved_at") or ""))
            if (
                retrieved_at.tzinfo is None
                or retrieved_at.utcoffset() is None
                or retrieved_at.utcoffset().total_seconds() != 8 * 60 * 60
                or retrieved_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                != source_date
            ):
                raise ValueError
            if expected_schema == "current-pool-universe-input/v1":
                verify_current_pool_universe_descriptor(payload)
                if payload.get("partition_coverage") != {
                    "exchanges": ["SSE", "SZSE"],
                    "list_statuses": ["L", "D", "P", "G"],
                    "partition_count": 8,
                }:
                    raise ValueError
                if payload.get("risk_snapshot_complete") is not False:
                    raise ValueError
                if payload.get("production_recommendation_eligible") is not False:
                    raise ValueError
                if payload.get("risk_coverage") != {
                    "stock_st": "not_collected",
                    "namechange": "not_collected",
                    "suspend_d": "not_collected",
                }:
                    raise ValueError
            else:
                verify_current_pool_risk_descriptor(payload)
                expected_years = int(source_date[:4]) - 1989
                if payload.get("partition_coverage") != {
                    "stock_st": 1,
                    "suspend_d": 1,
                    "namechange": expected_years,
                    "partition_count": expected_years + 2,
                }:
                    raise ValueError
                if (
                    payload.get("risk_snapshot_complete") is not True
                    or payload.get("risk_gate_passed") is not True
                    or payload.get("production_recommendation_eligible") is not False
                    or not isinstance(payload.get("universe_descriptor_sha256"), str)
                    or len(payload["universe_descriptor_sha256"]) != 64
                ):
                    raise ValueError
                seen_risk_symbols = set()
                for item in payload["items"]:
                    if (
                        not isinstance(item, dict)
                        or set(item)
                        != {
                            "ts_code",
                            "is_st",
                            "st_type",
                            "is_suspended",
                            "suspension_reason",
                            "active_name",
                        }
                        or not isinstance(item.get("ts_code"), str)
                        or type(item.get("is_st")) is not bool
                        or type(item.get("is_suspended")) is not bool
                        or (
                            item.get("st_type") is not None
                            and not isinstance(item.get("st_type"), str)
                        )
                            or (
                                item.get("suspension_reason") is not None
                                and item.get("suspension_reason")
                                not in {
                                    "suspended",
                                    "resume_day_no_new_entry",
                                    "conflict",
                                }
                        )
                        or (
                            item.get("active_name") is not None
                            and not isinstance(item.get("active_name"), str)
                        )
                        or item["ts_code"] in seen_risk_symbols
                    ):
                        raise ValueError
                    seen_risk_symbols.add(item["ts_code"])
                receipts = payload.get("partition_receipts")
                if not isinstance(receipts, list) or len(receipts) != expected_years + 2:
                    raise ValueError
                for receipt in receipts:
                    if (
                        not isinstance(receipt, dict)
                        or set(receipt)
                        != {"api_name", "params", "row_count", "rows_sha256"}
                        or receipt.get("api_name")
                        not in {"stock_st", "suspend_d", "namechange"}
                        or not isinstance(receipt.get("params"), dict)
                        or type(receipt.get("row_count")) is not int
                        or receipt["row_count"] < 0
                        or not isinstance(receipt.get("rows_sha256"), str)
                        or len(receipt["rows_sha256"]) != 64
                    ):
                        raise ValueError
        elif (
            expected_schema == "current-pool-history-summary/v1"
            and "descriptor_sha256" in payload
        ):
            embedded_sha256 = payload.get("descriptor_sha256")
            unsigned_payload = {
                key: value for key, value in payload.items() if key != "descriptor_sha256"
            }
            verified_sha256 = hashlib.sha256(
                json.dumps(
                    unsigned_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            if embedded_sha256 != verified_sha256:
                raise ValueError
            if len(descriptor_path.stem) == 64 and descriptor_path.stem != verified_sha256:
                raise ValueError
        else:
            canonical_unsigned = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            verified_sha256 = hashlib.sha256(canonical_unsigned).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("current-pool input descriptor rejected") from exc
    return {
        "payload": payload,
        "source_as_of": source_date,
        "descriptor_sha256": verified_sha256,
    }


def _load_current_pool_universe(descriptor: dict) -> list[dict]:
    items = descriptor["payload"]["items"]
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("current-pool input descriptor rejected")
    required = ("market", "exchange", "list_status")
    if any(not all(str(item.get(key) or "").strip() for key in required) for item in items):
        raise ValueError(
            "current-pool universe items require structured market, exchange, and list_status"
        )
    return items


def _load_current_pool_history_summary(descriptor: dict) -> dict[str, int]:
    items = descriptor["payload"]["items"]
    histories: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("history summary items must be objects")
        symbol = str(item.get("ts_code") or item.get("symbol") or "").strip()
        if not symbol or "bar_count" not in item or symbol in histories:
            raise ValueError("history summary items require unique symbol and bar_count")
        histories[symbol] = item["bar_count"]
    return histories


def _write_current_pool_audit(output_dir: str, payload: dict) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{payload['canonical_sha256']}.json"
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    created = not destination.exists()
    if created:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f"{destination.name}.", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            fsync_directory(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif destination.read_bytes() != content:
        raise ValueError("content-addressed current-pool audit mismatch")
    return {
        "canonical_sha256": payload["canonical_sha256"],
        "path": str(destination),
        "created": created,
    }


class CurrentPoolPublishUncertainStateError(RuntimeError):
    """A failed publish rollback left the target's durable state unknown."""


def _fsync_directory(directory: Path) -> None:
    fsync_directory(directory)


def _atomic_replace_bytes(target: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.rollback.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _publish_current_pool_audit(
    source_path: str,
    target_path: str,
    *,
    now: datetime,
    max_age_hours: int,
) -> dict:
    source = Path(source_path)
    target = Path(target_path)
    source_bytes = source.read_bytes()
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        verified = load_current_pool_audit(
            temporary,
            now=now,
            max_age_hours=max_age_hours,
        )
        target_is_symlink = target.is_symlink()
        target_exists = target.exists()
        previous_target_bytes = (
            target.read_bytes() if target_exists and not target_is_symlink else None
        )
        published = (
            target_is_symlink
            or not target_exists
            or previous_target_bytes != source_bytes
        )
        if published:
            os.replace(temporary, target)
            try:
                _fsync_directory(directory)
            except OSError:
                try:
                    if previous_target_bytes is None:
                        try:
                            target.unlink()
                        except FileNotFoundError:
                            pass
                    else:
                        _atomic_replace_bytes(target, previous_target_bytes)
                    _fsync_directory(directory)
                except Exception as rollback_error:
                    raise CurrentPoolPublishUncertainStateError(
                        "current-pool publish state is uncertain after directory "
                        "fsync and rollback failure"
                    ) from rollback_error
                raise
        return {
            "canonical_sha256": verified["canonical_sha256"],
            "source_path": str(source),
            "path": str(target),
            "bytes_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "published": published,
        }
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _warm_market_cache(args) -> dict:
    settings = get_settings()
    provider = _get_data_provider()
    service = RecommendationService(settings, provider, DISCLAIMER)
    industry_payload = service.industry.build_map(use_cache_on_error=True)
    industry_map = industry_payload.get("symbol_map", {})
    snapshot = service.universe.snapshot(use_cache_on_error=True)
    max_deep = args.max_deep or settings.scan_max_deep
    candidates = select_deep_scan_candidates(
        snapshot=snapshot,
        max_deep=max_deep,
        min_amount=settings.scan_min_amount,
        min_price=settings.scan_min_price,
        max_price=settings.scan_max_price,
        industry_map=industry_map,
        per_industry_top_n=settings.scan_per_industry_top_n,
        industry_top_n=settings.scan_industry_top_n,
    )
    targets = [
        {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
        {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
    ]
    seen = {("%s:%s" % (item["market"], item["symbol"])) for item in targets}
    for candidate in candidates:
        key = "%s:%s" % (candidate.get("market", "a"), candidate.get("symbol"))
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "symbol": str(candidate.get("symbol")),
                "market": str(candidate.get("market") or "a"),
                "name": candidate.get("name"),
            }
        )

    workers = max(1, min(int(args.workers or 1), 12))
    errors = []
    warmed = []

    def warm_one(target):
        frame, source = provider.history(
            symbol=target["symbol"],
            market=target["market"],
            lookback_days=args.lookback_days,
            adjust=args.adjust,
        )
        return {
            **target,
            "rows": len(frame),
            "latest_date": str(frame["date"].iloc[-1])[:10],
            "source": source,
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(warm_one, target): target for target in targets}
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                warmed.append(future.result())
            except Exception as exc:
                errors.append({**target, "message": str(exc)})

    warmed.sort(key=lambda item: (item.get("market", ""), item.get("symbol", "")))
    return {
        "target_count": len(targets),
        "warmed_count": len(warmed),
        "error_count": len(errors),
        "workers": workers,
        "lookback_days": args.lookback_days,
        "adjust": args.adjust,
        "cache_path": settings.market_data_cache_path,
        "candidate_count": len(candidates),
        "industry_count": len(
            {item.get("industry") for item in candidates if item.get("industry")}
        ),
        "errors": errors[:50],
        "sample": warmed[:10],
    }


def _compact_hold_sweep_result(hold_days: int, payload, sweep_payload, output_limit=5):
    compact = _compact_research_sweep_payload(payload, sweep_payload, output_limit=output_limit)
    return {
        "hold_days": hold_days,
        "source_summary": compact["source_summary"],
        "sweep_summary": compact["sweep_summary"],
        "diagnostics": compact["diagnostics"],
        "top": compact["top"],
    }


def main(argv=None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Quant signal scheduled jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-recommendations")
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--max-deep", type=int, default=None)
    generate.add_argument(
        "--run-slot",
        default=RUN_SLOT_AUTO,
        choices=[RUN_SLOT_AUTO, *RUN_SLOT_CONTEXTS.keys()],
    )
    generate.add_argument(
        "--target-trade-date",
        default=None,
        help="目标 A 股交易日，YYYY-MM-DD；使用 next 表示下一交易日",
    )

    warm = subparsers.add_parser("warm-market-cache")
    warm.add_argument("--max-deep", type=int, default=None)
    warm.add_argument("--workers", type=int, default=4)
    warm.add_argument("--lookback-days", type=int, default=620)
    warm.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])

    mootdx_check = subparsers.add_parser("mootdx-l1-check")
    mootdx_check.add_argument("--symbols", default="600519,000001,301308,002607")
    mootdx_check.add_argument("--servers", default=None)
    mootdx_check.add_argument("--timeout-seconds", type=float, default=None)

    jiaoch_check = subparsers.add_parser(
        "jiaoch-connectivity-check",
        help="Probe jiaoch source reachability (clock gate, DNS, TLS, HTTPS POST).",
    )
    jiaoch_check.add_argument("--timeout-seconds", type=float, default=10.0)

    monitor = subparsers.add_parser("monitor-recommendations")
    monitor.add_argument("--force", action="store_true")

    planned_exits = subparsers.add_parser("monitor-planned-exits")
    planned_exits.add_argument("--force", action="store_true")

    production_check = subparsers.add_parser("production-check")
    production_check.add_argument("--no-alert", action="store_true")

    research = subparsers.add_parser("research-backtest")
    research.add_argument("--start-date", default="2024-07-05")
    research.add_argument("--max-deep", type=int, default=120)
    research.add_argument("--top-n", type=int, default=10)
    research.add_argument("--hold-days", type=int, default=10)
    research.add_argument("--lookback-days", type=int, default=620)
    research.add_argument("--live-snapshot", action="store_true")
    research.add_argument("--cache-dir", default="data/research_cache")
    research.add_argument("--progress-every", type=int, default=10)
    research.add_argument("--buy-only", action="store_true")
    research.add_argument("--min-score", type=float, default=None)
    research.add_argument("--stop-loss-pct", type=float, default=None)
    research.add_argument("--take-profit-pct", type=float, default=None)
    research.add_argument("--trailing-stop-pct", type=float, default=None)
    research.add_argument("--symbol-cooldown-days", type=int, default=0)
    research.add_argument("--max-active-positions", type=int, default=0)
    research.add_argument("--announcement-context", action="store_true")
    research.add_argument("--announcement-lookback-days", type=int, default=None)
    research.add_argument("--require-announcement-event", default=None)
    research.add_argument("--require-all-announcement-events", action="store_true")
    research.add_argument("--exclude-announcement-event", default=None)
    research.add_argument("--require-market-level", default=None)
    research.add_argument("--require-signal-tag", default=None)
    research.add_argument("--require-all-signal-tags", action="store_true")
    research.add_argument("--exclude-signal-tag", default=None)
    research.add_argument("--min-prior-win-rate", type=float, default=None)
    research.add_argument("--min-prior-avg-return", type=float, default=None)
    research.add_argument("--max-prior-avg-adverse", type=float, default=None)
    research.add_argument("--margin-eligibility-context", action="store_true")
    research.add_argument("--include-qualified-trades", action="store_true")
    research.add_argument("--qualified-trades-output", default=None)

    historical = subparsers.add_parser("research-historical-universe")
    historical.add_argument("--start-date", default="2024-07-05")
    historical.add_argument("--end-date", required=True)
    historical.add_argument("--max-deep", type=int, default=80)
    historical.add_argument("--top-n", type=int, default=10)
    historical.add_argument("--hold-days", type=int, default=10)
    historical.add_argument("--lookback-days", type=int, default=620)
    historical.add_argument("--max-universe-symbols", type=int, default=0)
    historical_pit = historical.add_mutually_exclusive_group(required=True)
    historical_pit.add_argument("--pit-universe-path", default=None)
    historical_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_pit.add_argument("--composite-pit-descriptor-path", default=None)
    historical.add_argument("--expected-coverage-audit-sha256", default=None)
    historical.add_argument("--expected-artifact-root-sha256", default=None)
    historical.add_argument("--expected-composite-root-sha256", default=None)
    historical.add_argument("--temporal-contract-path", required=True)
    historical.add_argument("--expected-temporal-contract-sha256", required=True)
    historical.add_argument("--expected-temporal-role", required=True)
    historical.add_argument("--live-snapshot", action="store_true")
    historical.add_argument("--cache-dir", default="data/research_cache")
    historical.add_argument("--progress-every", type=int, default=25)
    historical.add_argument("--buy-only", action="store_true")
    historical.add_argument("--min-score", type=float, default=None)
    historical.add_argument("--stop-loss-pct", type=float, default=None)
    historical.add_argument("--take-profit-pct", type=float, default=None)
    historical.add_argument("--trailing-stop-pct", type=float, default=None)
    historical.add_argument("--symbol-cooldown-days", type=int, default=10)
    historical.add_argument("--max-active-positions", type=int, default=10)
    historical.add_argument("--announcement-context", action="store_true")
    historical.add_argument("--announcement-lookback-days", type=int, default=None)
    historical.add_argument("--require-announcement-event", default=None)
    historical.add_argument("--require-all-announcement-events", action="store_true")
    historical.add_argument("--exclude-announcement-event", default=None)
    historical.add_argument("--require-market-level", default=None)
    historical.add_argument("--require-signal-tag", default=None)
    historical.add_argument("--require-all-signal-tags", action="store_true")
    historical.add_argument("--exclude-signal-tag", default=None)
    historical.add_argument("--min-prior-win-rate", type=float, default=None)
    historical.add_argument("--min-prior-avg-return", type=float, default=None)
    historical.add_argument("--max-prior-avg-adverse", type=float, default=None)
    historical.add_argument("--industry-rotation-context", action="store_true")
    historical.add_argument("--industry-rotation-max-boards", type=int, default=40)
    historical.add_argument("--margin-eligibility-context", action="store_true")
    historical.add_argument("--dragon-tiger-context", action="store_true")
    historical.add_argument("--include-qualified-trades", action="store_true")
    historical.add_argument("--qualified-trades-output", default=None)
    historical.add_argument("--input-plan-path", default=None)
    historical.add_argument(
        "--supervised-launch-mode", choices=("probe", "run"), default=None
    )
    _add_precompute_control_arguments(historical, registration_cas=False)

    historical_sweep = subparsers.add_parser("research-historical-sweep")
    historical_sweep.add_argument("--start-date", default="2024-07-05")
    historical_sweep.add_argument("--end-date", required=True)
    historical_sweep.add_argument("--max-deep", type=int, default=80)
    historical_sweep.add_argument("--top-n", type=int, default=10)
    historical_sweep.add_argument("--hold-days", type=int, default=10)
    historical_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_sweep.add_argument("--max-universe-symbols", type=int, default=0)
    historical_sweep_pit = historical_sweep.add_mutually_exclusive_group(required=True)
    historical_sweep_pit.add_argument("--pit-universe-path", default=None)
    historical_sweep_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_sweep.add_argument("--expected-coverage-audit-sha256", default=None)
    historical_sweep.add_argument("--expected-artifact-root-sha256", required=True)
    historical_sweep.add_argument("--temporal-contract-path", required=True)
    historical_sweep.add_argument("--expected-temporal-contract-sha256", required=True)
    historical_sweep.add_argument("--expected-temporal-role", required=True)
    historical_sweep.add_argument("--live-snapshot", action="store_true")
    historical_sweep.add_argument("--cache-dir", default="data/research_cache")
    historical_sweep.add_argument("--progress-every", type=int, default=50)
    historical_sweep.add_argument("--stop-loss-pct", type=float, default=None)
    historical_sweep.add_argument("--take-profit-pct", type=float, default=None)
    historical_sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    historical_sweep.add_argument("--symbol-cooldown-days", type=int, default=10)
    historical_sweep.add_argument("--max-active-positions", type=int, default=10)
    historical_sweep.add_argument("--min-trades", type=int, default=20)
    historical_sweep.add_argument("--max-filter-size", type=int, default=3)
    historical_sweep.add_argument("--required-signal-tags", default=None)
    historical_sweep.add_argument("--excluded-signal-tags", default=None)
    historical_sweep.add_argument("--market-levels", default=None)
    historical_sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    historical_sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    historical_sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    historical_sweep.add_argument("--industry-rotation-context", action="store_true")
    historical_sweep.add_argument("--industry-rotation-max-boards", type=int, default=40)
    historical_sweep.add_argument("--margin-eligibility-context", action="store_true")
    historical_sweep.add_argument("--dragon-tiger-context", action="store_true")
    historical_sweep.add_argument("--exposure-sweep", action="store_true")
    historical_sweep.add_argument("--exposure-multiplier", type=float, default=None)
    historical_sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    historical_sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    historical_sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    historical_sweep.add_argument("--slippage-bps", type=float, default=0.0)
    historical_sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    historical_sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    historical_sweep.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    historical_sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    historical_sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    historical_sweep.add_argument("--correlation-threshold", type=float, default=None)
    historical_sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    historical_sweep.add_argument("--correlation-min-periods", type=int, default=20)
    historical_sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    historical_sweep.add_argument("--correlation-cache-dir", default=None)
    historical_sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    historical_sweep.add_argument("--output-limit", type=int, default=12)
    historical_sweep.add_argument("--compact", action="store_true")
    historical_sweep.add_argument("--qualified-trades-output", default=None)

    historical_hold_sweep = subparsers.add_parser("research-historical-hold-sweep")
    historical_hold_sweep.add_argument("--start-date", default="2024-07-05")
    historical_hold_sweep.add_argument("--end-date", required=True)
    historical_hold_sweep.add_argument("--max-deep", type=int, default=80)
    historical_hold_sweep.add_argument("--top-n", type=int, default=10)
    historical_hold_sweep.add_argument("--hold-days-list", default="3,5,7,10")
    historical_hold_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--max-universe-symbols", type=int, default=0)
    historical_hold_sweep_pit = historical_hold_sweep.add_mutually_exclusive_group(required=True)
    historical_hold_sweep_pit.add_argument("--pit-universe-path", default=None)
    historical_hold_sweep_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_hold_sweep.add_argument("--expected-coverage-audit-sha256", default=None)
    historical_hold_sweep.add_argument("--expected-artifact-root-sha256", required=True)
    historical_hold_sweep.add_argument("--temporal-contract-path", required=True)
    historical_hold_sweep.add_argument("--expected-temporal-contract-sha256", required=True)
    historical_hold_sweep.add_argument("--expected-temporal-role", required=True)
    historical_hold_sweep.add_argument("--live-snapshot", action="store_true")
    historical_hold_sweep.add_argument("--cache-dir", default="data/research_cache")
    historical_hold_sweep.add_argument("--progress-every", type=int, default=50)
    historical_hold_sweep.add_argument("--stop-loss-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--take-profit-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--symbol-cooldown-days", type=int, default=None)
    historical_hold_sweep.add_argument("--max-active-positions", type=int, default=10)
    historical_hold_sweep.add_argument("--min-trades", type=int, default=20)
    historical_hold_sweep.add_argument("--max-filter-size", type=int, default=3)
    historical_hold_sweep.add_argument("--required-signal-tags", default=None)
    historical_hold_sweep.add_argument("--excluded-signal-tags", default=None)
    historical_hold_sweep.add_argument("--market-levels", default=None)
    historical_hold_sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    historical_hold_sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    historical_hold_sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    historical_hold_sweep.add_argument("--margin-eligibility-context", action="store_true")
    historical_hold_sweep.add_argument("--dragon-tiger-context", action="store_true")
    historical_hold_sweep.add_argument("--exposure-sweep", action="store_true")
    historical_hold_sweep.add_argument("--exposure-multiplier", type=float, default=None)
    historical_hold_sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    historical_hold_sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    historical_hold_sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    historical_hold_sweep.add_argument("--slippage-bps", type=float, default=0.0)
    historical_hold_sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    historical_hold_sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    historical_hold_sweep.add_argument(
        "--prior-high-trailing-activation-pct", type=float, default=0.0
    )
    historical_hold_sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    historical_hold_sweep.add_argument("--correlation-threshold", type=float, default=None)
    historical_hold_sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    historical_hold_sweep.add_argument("--correlation-min-periods", type=int, default=20)
    historical_hold_sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--correlation-cache-dir", default=None)
    historical_hold_sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    historical_hold_sweep.add_argument("--output-limit", type=int, default=5)
    historical_hold_sweep.add_argument("--compact", action="store_true")

    industry_check = subparsers.add_parser("industry-history-check")
    industry_check.add_argument("--start-date", default="2024-07-05")
    industry_check.add_argument("--end-date", default=None)
    industry_check.add_argument("--max-boards", type=int, default=3)

    margin_check = subparsers.add_parser("margin-eligibility-check")
    margin_check.add_argument("--as-of", default=None)

    sweep = subparsers.add_parser("research-sweep")
    sweep.add_argument("--start-date", default="2024-07-05")
    sweep.add_argument("--max-deep", type=int, default=80)
    sweep.add_argument("--top-n", type=int, default=10)
    sweep.add_argument("--hold-days", type=int, default=10)
    sweep.add_argument("--lookback-days", type=int, default=620)
    sweep.add_argument("--cache-dir", default="data/research_cache")
    sweep.add_argument("--progress-every", type=int, default=0)
    sweep.add_argument("--stop-loss-pct", type=float, default=None)
    sweep.add_argument("--take-profit-pct", type=float, default=None)
    sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    sweep.add_argument("--symbol-cooldown-days", type=int, default=10)
    sweep.add_argument("--max-active-positions", type=int, default=10)
    sweep.add_argument("--min-trades", type=int, default=20)
    sweep.add_argument("--max-filter-size", type=int, default=3)
    sweep.add_argument("--required-signal-tags", default=None)
    sweep.add_argument("--excluded-signal-tags", default=None)
    sweep.add_argument("--market-levels", default=None)
    sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    sweep.add_argument("--margin-eligibility-context", action="store_true")
    sweep.add_argument("--exposure-sweep", action="store_true")
    sweep.add_argument("--exposure-multiplier", type=float, default=None)
    sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    sweep.add_argument("--slippage-bps", type=float, default=0.0)
    sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    sweep.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    sweep.add_argument("--correlation-threshold", type=float, default=None)
    sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    sweep.add_argument("--correlation-min-periods", type=int, default=20)
    sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    sweep.add_argument("--correlation-cache-dir", default="data/research_cache")
    sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    sweep.add_argument("--output-limit", type=int, default=12)
    sweep.add_argument("--compact", action="store_true")
    sweep.add_argument("--qualified-trades-output", default=None)

    sweep_file = subparsers.add_parser("research-sweep-file")
    sweep_file.add_argument("--qualified-trades-path", required=True)
    sweep_file.add_argument("--audited-pit-universe-path", required=True)
    sweep_file.add_argument("--start-date", required=True)
    sweep_file.add_argument("--end-date", required=True)
    sweep_file.add_argument("--temporal-contract-path", required=True)
    sweep_file.add_argument("--expected-coverage-audit-sha256", required=True)
    sweep_file.add_argument("--expected-artifact-root-sha256", required=True)
    sweep_file.add_argument("--expected-temporal-contract-sha256", required=True)
    sweep_file.add_argument("--expected-temporal-role", required=True)
    sweep_file.add_argument("--top-n", type=int, default=10)
    sweep_file.add_argument("--hold-days", type=int, default=10)
    sweep_file.add_argument("--symbol-cooldown-days", type=int, default=10)
    sweep_file.add_argument("--max-active-positions", type=int, default=10)
    sweep_file.add_argument("--min-trades", type=int, default=20)
    sweep_file.add_argument("--max-filter-size", type=int, default=3)
    sweep_file.add_argument("--required-signal-tags", default=None)
    sweep_file.add_argument("--excluded-signal-tags", default=None)
    sweep_file.add_argument("--market-levels", default=None)
    sweep_file.add_argument("--target-win-rate-pct", type=float, default=52.0)
    sweep_file.add_argument("--target-drawdown-pct", type=float, default=15.0)
    sweep_file.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    sweep_file.add_argument("--exposure-sweep", action="store_true")
    sweep_file.add_argument("--exposure-multiplier", type=float, default=None)
    sweep_file.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    sweep_file.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    sweep_file.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    sweep_file.add_argument("--slippage-bps", type=float, default=0.0)
    sweep_file.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    sweep_file.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    sweep_file.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    sweep_file.add_argument("--partial-profit-activation-pct", type=float, default=None)
    sweep_file.add_argument("--partial-profit-fraction", type=float, default=0.0)
    sweep_file.add_argument("--correlation-threshold", type=float, default=None)
    sweep_file.add_argument("--correlation-lookback-days", type=int, default=60)
    sweep_file.add_argument("--correlation-min-periods", type=int, default=20)
    sweep_file.add_argument("--correlation-history-lookback-days", type=int, default=620)
    sweep_file.add_argument("--correlation-cache-dir", default=None)
    sweep_file.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    sweep_file.add_argument("--output-limit", type=int, default=12)
    sweep_file.add_argument("--compact", action="store_true")

    validate_file = subparsers.add_parser("research-validate-file")
    validate_file.add_argument("--qualified-trades-path", required=True)
    validate_authority = validate_file.add_mutually_exclusive_group(required=True)
    validate_authority.add_argument("--audited-pit-universe-path")
    validate_authority.add_argument("--composite-pit-descriptor-path")
    validate_file.add_argument("--experiment-id", required=True)
    validate_file.add_argument("--hypothesis", required=True)
    validate_file.add_argument("--expected-mechanism", required=True)
    validate_file.add_argument("--falsification-criterion", required=True)
    validate_file.add_argument("--exit-criterion", required=True)
    validate_file.add_argument("--final-oos-start", required=True)
    validate_file.add_argument("--start-date", required=True)
    validate_file.add_argument("--end-date", required=True)
    validate_file.add_argument("--temporal-contract-path", required=True)
    validate_file.add_argument("--expected-coverage-audit-sha256")
    validate_file.add_argument("--expected-temporal-contract-sha256", required=True)
    validate_file.add_argument("--expected-temporal-role", required=True)
    validate_file.add_argument("--expected-artifact-root-sha256")
    validate_file.add_argument("--expected-composite-root-sha256")
    validate_file.add_argument("--train-days", type=int, default=365)
    validate_file.add_argument("--validation-days", type=int, default=90)
    validate_file.add_argument("--step-days", type=int, default=90)
    validate_file.add_argument("--embargo-days", type=int, default=5)
    validate_file.add_argument("--minimum-oos-trades", type=int, default=200)
    validate_file.add_argument("--ledger-path", default="data/research_experiments/ledger.jsonl")
    validate_file.add_argument("--artifact-dir", default="data/research_experiments/artifacts")
    validate_file.add_argument("--hold-days", type=int, default=5)
    validate_file.add_argument("--top-n", type=int, default=10)
    validate_file.add_argument("--symbol-cooldown-days", type=int, default=5)
    validate_file.add_argument("--max-active-positions", type=int, default=3)
    validate_file.add_argument("--required-signal-tags", default=None)
    validate_file.add_argument("--excluded-signal-tags", default=None)
    validate_file.add_argument("--market-levels", default=None)
    validate_file.add_argument("--exposure-multiplier", type=float, default=1.0)
    validate_file.add_argument("--annual-financing-rate-pct", type=float, default=8.0)
    validate_file.add_argument("--roundtrip-cost-bps", type=float, default=25.0)
    validate_file.add_argument("--slippage-bps", type=float, default=10.0)
    validate_file.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="slot-daily",
    )
    validate_file.add_argument("--target-win-rate-pct", type=float, default=52.0)
    validate_file.add_argument("--target-win-rate-max-pct", type=float, default=60.0)
    validate_file.add_argument("--target-drawdown-pct", type=float, default=15.0)
    validate_file.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    validate_file.add_argument("--target-profit-factor", type=float, default=1.3)
    validate_file.add_argument("--target-calmar", type=float, default=1.5)
    validate_file.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    validate_file.add_argument("--partial-profit-activation-pct", type=float, default=None)
    validate_file.add_argument("--partial-profit-fraction", type=float, default=0.0)
    validate_file.add_argument("--correlation-threshold", type=float, default=None)
    validate_file.add_argument("--correlation-lookback-days", type=int, default=60)
    validate_file.add_argument("--input-plan-path", default=None)
    validate_file.add_argument("--register-only", action="store_true")
    validate_file.add_argument("--registered-record-hash", default=None)
    validate_file.add_argument("--correlation-cache-dir", default=None)
    _add_precompute_control_arguments(validate_file, registration_cas=True)

    build_pit = subparsers.add_parser("research-build-pit-universe")
    build_pit.add_argument("--security-master-path", required=True)
    build_pit.add_argument("--trade-calendar-path", required=True)
    build_pit.add_argument("--daily-universe-path", required=True)
    build_pit.add_argument("--source-manifest-path", required=True)
    build_pit.add_argument("--start-date", required=True)
    build_pit.add_argument("--end-date", required=True)
    build_pit.add_argument("--output-dir", default="data/research_artifacts/pit_universe")

    pit_ingest = subparsers.add_parser("research-pit-ingest-response")
    pit_ingest.add_argument("--store-dir", required=True)
    pit_ingest.add_argument(
        "--dataset", required=True, choices=["stock_basic", "trade_cal", "bak_basic"]
    )
    pit_ingest.add_argument("--partition-key", required=True)
    pit_ingest.add_argument("--endpoint", required=True)
    pit_ingest.add_argument("--params-json", required=True)
    pit_ingest.add_argument("--raw-response-path", required=True)
    pit_ingest.add_argument("--http-status", type=int, required=True)
    pit_ingest.add_argument("--retrieved-at", required=True)
    pit_ingest.add_argument("--row-cap", type=int, required=True)

    pit_ingest_suspension = subparsers.add_parser("research-pit-ingest-cninfo-suspension")
    pit_ingest_suspension.add_argument("--store-dir", required=True)
    pit_ingest_suspension.add_argument("--ts-code", required=True)
    pit_ingest_suspension.add_argument("--start-pdf-path", required=True)
    pit_ingest_suspension.add_argument("--start-source-url", required=True)
    pit_ingest_suspension.add_argument("--start-published-at", required=True)
    pit_ingest_suspension.add_argument("--start-retrieved-at", required=True)
    pit_ingest_suspension.add_argument("--resume-pdf-path", required=True)
    pit_ingest_suspension.add_argument("--resume-source-url", required=True)
    pit_ingest_suspension.add_argument("--resume-published-at", required=True)
    pit_ingest_suspension.add_argument("--resume-retrieved-at", required=True)

    pit_audit = subparsers.add_parser("research-pit-audit-store")
    pit_audit.add_argument("--store-dir", required=True)
    pit_audit.add_argument("--start-date", required=True)
    pit_audit.add_argument("--end-date", required=True)
    pit_audit.add_argument("--calendar-exchanges", default="SSE,SZSE")

    current_pool_audit = subparsers.add_parser("research-current-pool-audit")
    current_pool_audit.add_argument("--universe-path", required=True)
    current_pool_audit.add_argument("--history-summary-path", required=True)
    current_pool_audit.add_argument("--risk-path", required=True)
    current_pool_audit.add_argument("--output-dir", required=True)
    current_pool_audit.add_argument("--min-signal-bars", type=int, default=90)

    current_pool_publish = subparsers.add_parser("research-current-pool-publish")
    current_pool_publish.add_argument("--audit-path", required=True)
    current_pool_publish.add_argument("--target-path")
    current_pool_publish.add_argument("--max-age-hours", type=int)

    current_pool_fetch = subparsers.add_parser("research-current-pool-fetch-jiaoch")
    current_pool_fetch.add_argument("--as-of", required=True)
    current_pool_fetch.add_argument("--output-dir", required=True)
    current_pool_fetch.add_argument("--timeout-seconds", type=float, default=30.0)

    current_pool_risk_fetch = subparsers.add_parser("research-current-pool-fetch-risk-jiaoch")
    current_pool_risk_fetch.add_argument("--as-of", required=True)
    current_pool_risk_fetch.add_argument("--universe-path", required=True)
    current_pool_risk_fetch.add_argument("--output-dir", required=True)
    current_pool_risk_fetch.add_argument("--timeout-seconds", type=float, default=30.0)

    current_pool_history = subparsers.add_parser(
        "research-current-pool-build-history-summary"
    )
    current_pool_history.add_argument("--universe-path", required=True)
    current_pool_history.add_argument("--store-dir", required=True)
    current_pool_history.add_argument("--start-date", required=True)
    current_pool_history.add_argument("--end-date", required=True)
    current_pool_history.add_argument("--as-of", required=True)
    current_pool_history.add_argument("--output-dir", required=True)

    current_pool_development_replay = subparsers.add_parser(
        "research-current-pool-development-replay"
    )
    current_pool_development_replay.add_argument("--store-dir", required=True)
    current_pool_development_replay.add_argument("--universe-path", required=True)
    current_pool_development_replay.add_argument("--history-summary-path", required=True)
    current_pool_development_replay.add_argument("--temporal-contract-path", required=True)
    current_pool_development_replay.add_argument("--output-dir", required=True)

    audited_pit_development_replay = subparsers.add_parser(
        "research-audited-pit-development-replay"
    )
    audited_pit_development_replay.add_argument(
        "--audited-pit-universe-path", required=True
    )
    audited_pit_development_replay.add_argument(
        "--expected-coverage-audit-sha256", required=True
    )
    audited_pit_development_replay.add_argument(
        "--expected-artifact-root-sha256", required=True
    )
    audited_pit_development_replay.add_argument(
        "--temporal-contract-path", required=True
    )
    audited_pit_development_replay.add_argument(
        "--expected-temporal-contract-sha256", required=True
    )
    audited_pit_development_replay.add_argument("--start-date", required=True)
    audited_pit_development_replay.add_argument("--end-date", required=True)
    audited_pit_development_replay.add_argument("--output-dir", required=True)

    audited_pit_breadth_replay = subparsers.add_parser(
        "research-audited-pit-breadth-development-replay"
    )
    audited_pit_breadth_replay.add_argument(
        "--audited-pit-universe-path", required=True
    )
    audited_pit_breadth_replay.add_argument(
        "--expected-coverage-audit-sha256", required=True
    )
    audited_pit_breadth_replay.add_argument(
        "--expected-artifact-root-sha256", required=True
    )
    audited_pit_breadth_replay.add_argument(
        "--temporal-contract-path", required=True
    )
    audited_pit_breadth_replay.add_argument(
        "--expected-temporal-contract-sha256", required=True
    )
    audited_pit_breadth_replay.add_argument("--start-date", required=True)
    audited_pit_breadth_replay.add_argument("--end-date", required=True)
    audited_pit_breadth_replay.add_argument("--output-dir", required=True)

    audited_pit_loss_attribution = subparsers.add_parser(
        "research-audited-pit-loss-attribution"
    )
    audited_pit_loss_attribution.add_argument(
        "--audited-pit-universe-path", required=True
    )
    audited_pit_loss_attribution.add_argument(
        "--expected-coverage-audit-sha256", required=True
    )
    audited_pit_loss_attribution.add_argument(
        "--expected-artifact-root-sha256", required=True
    )
    audited_pit_loss_attribution.add_argument(
        "--temporal-contract-path", required=True
    )
    audited_pit_loss_attribution.add_argument(
        "--expected-temporal-contract-sha256", required=True
    )
    audited_pit_loss_attribution.add_argument("--start-date", required=True)
    audited_pit_loss_attribution.add_argument("--end-date", required=True)
    audited_pit_loss_attribution.add_argument("--output-dir", required=True)

    pit_publish = subparsers.add_parser("research-pit-publish-universe")
    pit_publish.add_argument("--store-dir", required=True)
    pit_publish.add_argument("--start-date", required=True)
    pit_publish.add_argument("--end-date", required=True)
    pit_publish.add_argument("--expected-coverage-audit-sha256", required=True)
    pit_publish.add_argument("--output-dir", default="data/research_artifacts/audited_pit_universe")
    pit_publish.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_publish.add_argument("--temporal-role", default="development")

    evidence_bundle = subparsers.add_parser("research-build-evidence-bundle")
    evidence_bundle.add_argument("--pit-universe-path", required=True)
    evidence_bundle.add_argument("--market-data-manifest-path", required=True)
    evidence_bundle.add_argument("--audited-pit-universe-path", required=True)
    evidence_bundle.add_argument("--expected-coverage-audit-sha256", required=True)
    evidence_bundle.add_argument("--expected-artifact-root-sha256", required=True)
    evidence_bundle.add_argument("--expected-temporal-contract-sha256", required=True)
    evidence_bundle.add_argument("--expected-temporal-role", required=True)
    evidence_bundle.add_argument("--output-dir", required=True)

    native_evidence = subparsers.add_parser("research-build-artifact-native-evidence")
    native_evidence.add_argument("--qualified-trades-path", required=True)
    native_authority = native_evidence.add_mutually_exclusive_group(required=True)
    native_authority.add_argument("--audited-pit-universe-path")
    native_authority.add_argument("--composite-pit-descriptor-path")
    native_evidence.add_argument("--expected-coverage-audit-sha256")
    native_evidence.add_argument("--expected-artifact-root-sha256")
    native_evidence.add_argument("--expected-composite-root-sha256")
    native_evidence.add_argument("--expected-temporal-contract-sha256", required=True)
    native_evidence.add_argument("--expected-temporal-role", required=True)
    native_evidence.add_argument("--output-dir", required=True)

    strict_evidence = subparsers.add_parser("research-build-strict-evidence-bundle")
    strict_evidence.add_argument("--artifact-native-evidence-path", required=True)
    strict_authority = strict_evidence.add_mutually_exclusive_group(required=True)
    strict_authority.add_argument("--audited-pit-universe-path")
    strict_authority.add_argument("--composite-pit-descriptor-path")
    strict_evidence.add_argument("--expected-coverage-audit-sha256")
    strict_evidence.add_argument("--expected-artifact-root-sha256")
    strict_evidence.add_argument("--expected-composite-root-sha256")
    strict_evidence.add_argument("--expected-temporal-contract-sha256", required=True)
    strict_evidence.add_argument("--expected-temporal-role", required=True)
    strict_evidence.add_argument("--output-dir", required=True)

    pit_fetch = subparsers.add_parser("research-pit-fetch-tushare")
    pit_fetch.add_argument("--store-dir", required=True)
    pit_fetch.add_argument("--start-date", required=True)
    pit_fetch.add_argument("--end-date", required=True)
    pit_fetch.add_argument("--source-profile", choices=("official", "jiaoch"), default="jiaoch")
    pit_fetch.add_argument("--api-url")
    pit_fetch.add_argument("--allow-insecure-official-http", action="store_true")
    pit_fetch.add_argument("--max-attempts", type=int, default=3)
    pit_fetch.add_argument("--timeout-seconds", type=float, default=30.0)
    pit_fetch.add_argument("--workers", type=int, choices=range(1, 9), default=1)
    pit_fetch.add_argument("--no-resume", action="store_true")
    pit_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_fetch.add_argument("--temporal-role", default="development")

    pit_calendar_fetch = subparsers.add_parser("research-pit-fetch-calendars")
    pit_calendar_fetch.add_argument("--store-dir", required=True)
    pit_calendar_fetch.add_argument("--start-date", required=True)
    pit_calendar_fetch.add_argument("--end-date", required=True)
    pit_calendar_fetch.add_argument(
        "--source-profile", choices=("official", "jiaoch"), default="jiaoch"
    )
    pit_calendar_fetch.add_argument("--api-url")
    pit_calendar_fetch.add_argument("--allow-insecure-official-http", action="store_true")
    pit_calendar_fetch.add_argument("--max-attempts", type=int, default=3)
    pit_calendar_fetch.add_argument("--timeout-seconds", type=float, default=30.0)
    pit_calendar_fetch.add_argument("--workers", type=int, choices=range(1, 9), default=1)
    pit_calendar_fetch.add_argument("--no-resume", action="store_true")
    pit_calendar_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_calendar_fetch.add_argument("--temporal-role", default="development")

    current_pool_market_fetch = subparsers.add_parser(
        "research-current-pool-fetch-market-jiaoch"
    )
    current_pool_market_fetch.add_argument("--store-dir", required=True)
    current_pool_market_fetch.add_argument("--start-date", required=True)
    current_pool_market_fetch.add_argument("--end-date", required=True)
    current_pool_market_fetch.set_defaults(
        source_profile="jiaoch",
        api_url=None,
        allow_insecure_official_http=False,
    )
    current_pool_market_fetch.add_argument(
        "--max-attempts", type=_positive_int_arg, default=3
    )
    current_pool_market_fetch.add_argument(
        "--timeout",
        "--timeout-seconds",
        dest="timeout_seconds",
        type=_positive_float_arg,
        default=30.0,
    )
    current_pool_market_fetch.add_argument(
        "--workers", type=int, choices=range(1, 3), default=2
    )
    current_pool_market_fetch.add_argument(
        "--batch-size", type=int, choices=range(1, 51), default=10
    )
    current_pool_market_fetch.add_argument("--progress-path")
    current_pool_market_fetch.add_argument("--no-resume", action="store_true")
    current_pool_market_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    current_pool_market_fetch.add_argument("--temporal-role", default="development")

    args = parser.parse_args(argv)
    if args.command in {"research-backtest", "research-sweep"}:
        raise ValueError(
            "legacy mutable research command is disabled; use audited historical or file validation commands"
        )
    if args.command in {
        "research-historical-sweep",
        "research-historical-hold-sweep",
        "research-sweep-file",
        "research-validate-file",
    }:
        correlation_threshold = getattr(args, "correlation_threshold", None)
        correlation_cache_dir = getattr(args, "correlation_cache_dir", None)
        if (
            correlation_threshold is not None and float(correlation_threshold) != 0.0
        ) or correlation_cache_dir is not None:
            raise ValueError("frozen research forbids legacy correlation cache or adapter usage")
    historical_treatment_plan = None
    historical_treatment_plan_artifact = None
    historical_precompute_control = None
    launcher_ready = None
    if args.command == "research-historical-universe" and args.input_plan_path:
        (
            historical_treatment_plan,
            historical_treatment_plan_artifact,
        ) = _load_treatment_input_plan(args.input_plan_path)
        _validate_precompute_argument_phase(
            args,
            plan_schema=historical_treatment_plan["schema_version"],
            phase="historical",
        )
        historical_precompute_control = _verify_precompute_control_for_treatment(
            args, historical_treatment_plan, historical_treatment_plan_artifact
        )
        _validate_historical_treatment_plan(args, historical_treatment_plan)
    if (
        historical_treatment_plan is not None
        and historical_treatment_plan["schema_version"]
        in {
            "research-treatment-input-plan/v4",
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
    ):
        settings = _settings_from_frozen_treatment_plan(historical_treatment_plan)
    elif args.command in {
            "research-validate-file",
            "research-build-pit-universe",
            "research-pit-ingest-response",
            "research-pit-ingest-cninfo-suspension",
            "research-pit-audit-store",
            "research-current-pool-audit",
            "research-current-pool-fetch-jiaoch",
            "research-current-pool-fetch-risk-jiaoch",
            "research-current-pool-fetch-market-jiaoch",
            "research-current-pool-build-history-summary",
            "research-pit-publish-universe",
            "research-pit-fetch-tushare",
            "research-build-evidence-bundle",
            "research-build-artifact-native-evidence",
        }:
        settings = None
    else:
        settings = get_settings()
    if historical_treatment_plan is not None:
        _validate_historical_treatment_settings(settings, historical_treatment_plan)
    if (
        args.command == "research-historical-universe"
        and args.supervised_launch_mode is not None
    ):
        if (
            historical_treatment_plan is None
            or historical_treatment_plan["schema_version"]
            not in {
                "research-treatment-input-plan/v4",
                "research-treatment-input-plan/v5",
                "research-treatment-input-plan/v6",
                "research-treatment-input-plan/v7",
            }
        ):
            raise ValueError("supervised launch requires a controlled treatment plan")
        fixture_binding = historical_treatment_plan["development_payload_fixture"]
        _verify_development_payload_fixture_binding(
            fixture_binding,
            composite_pit_descriptor_path=args.composite_pit_descriptor_path,
        )
        if historical_precompute_control is None:
            raise ValueError("v4 supervised launch requires registered precompute control")
        launcher_ready = _verify_registered_precompute_for_args(
            args,
            historical_treatment_plan,
            historical_precompute_control,
            consume_launch=True,
        )
        _print_launcher_ready(launcher_ready)
        _wait_for_launcher_ack(launcher_ready)
        if args.supervised_launch_mode == "probe":
            return 0
    service = (
        RecommendationService(settings, _get_data_provider(), DISCLAIMER)
        if args.command
        in {
            "generate-recommendations",
            "mootdx-l1-check",
            "monitor-recommendations",
            "monitor-planned-exits",
        }
        else None
    )

    if args.command == "generate-recommendations":
        payload = service.generate_daily_recommendations(
            force=args.force,
            max_deep=args.max_deep,
            run_slot=args.run_slot,
            target_trade_date=args.target_trade_date,
        )
        _print_json(
            {
                "generated_at": payload.get("generated_at"),
                "trade_date": payload.get("trade_date"),
                "target_trade_date": payload.get("target_trade_date"),
                "recommendation_status": payload.get("recommendation_status"),
                "evidence_scope": payload.get("evidence_scope"),
                "live_proof": payload.get("live_proof", False),
                "auto_order": payload.get("auto_order", False),
                "profile_gate": payload.get("profile_gate"),
                "strategy_profile": payload.get("strategy_profile"),
                "run_slot": payload.get("run_slot"),
                "run_slot_label": payload.get("run_slot_label"),
                "summary": payload.get("summary"),
                "top_symbols": [item.get("symbol") for item in payload.get("items", [])[:10]],
            }
        )
        return 0

    if args.command == "warm-market-cache":
        _print_json(_warm_market_cache(args))
        return 0

    if args.command == "mootdx-l1-check":
        symbols = _split_csv_arg(args.symbols) or []
        provider = service.l1_quotes
        if args.servers is not None or args.timeout_seconds is not None:
            from app.mootdx_l1 import MootdxL1QuoteProvider

            provider = MootdxL1QuoteProvider(
                servers=args.servers if args.servers is not None else settings.mootdx_servers,
                timeout_seconds=args.timeout_seconds
                if args.timeout_seconds is not None
                else settings.mootdx_timeout_seconds,
                enabled=True,
            )
        payload = provider.quotes(symbols)
        payload["symbols"] = symbols
        _print_json(payload)
        return 0

    if args.command == "jiaoch-connectivity-check":
        payload = _probe_jiaoch_connectivity(timeout_seconds=args.timeout_seconds)
        _print_json(payload)
        # Exit code: 0 if reachable end-to-end, 1 if any step failed (still prints payload)
        return 0 if payload.get("overall_status") in {"ok", "ok_without_token"} else 1

    if args.command == "monitor-recommendations":
        payload = service.monitor_recommendations(force=args.force)
        _print_json(payload)
        return 0

    if args.command == "monitor-planned-exits":
        payload = service.monitor_planned_exits(force=args.force)
        _print_json(payload)
        return 0

    if args.command == "production-check":
        payload = build_production_status(settings)
        if not args.no_alert:
            payload["alert"] = process_health_alert(settings, payload)
        _print_json(payload)
        return {"healthy": 0, "degraded": 1, "unhealthy": 2}.get(payload.get("status"), 2)

    if args.command == "research-current-pool-publish":
        publish_settings = settings or get_settings()
        max_age_hours = (
            args.max_age_hours
            if args.max_age_hours is not None
            else publish_settings.production_current_pool_max_age_hours
        )
        report = _publish_current_pool_audit(
            args.audit_path,
            args.target_path or publish_settings.current_pool_audit_path,
            now=datetime.now(ZoneInfo("Asia/Shanghai")),
            max_age_hours=max_age_hours,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-fetch-jiaoch":
        report = fetch_jiaoch_current_pool_descriptor(
            as_of=args.as_of,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-fetch-risk-jiaoch":
        report = fetch_jiaoch_current_pool_risk_descriptor(
            as_of=args.as_of,
            universe_path=args.universe_path,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-build-history-summary":
        report = build_current_pool_history_summary(
            universe_path=args.universe_path,
            store_dir=args.store_dir,
            history_start=args.start_date,
            history_end=args.end_date,
            as_of=args.as_of,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-development-replay":
        report = run_current_pool_development_replay(
            settings=settings or get_settings(),
            store_dir=args.store_dir,
            universe_path=args.universe_path,
            history_summary_path=args.history_summary_path,
            temporal_contract_path=args.temporal_contract_path,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-audited-pit-development-replay":
        report = run_audited_pit_development_replay(
            settings=settings or get_settings(),
            audited_pit_universe_path=args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            start_date=args.start_date,
            end_date=args.end_date,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-audited-pit-breadth-development-replay":
        report = run_audited_pit_breadth_development_replay(
            settings=settings or get_settings(),
            audited_pit_universe_path=args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            start_date=args.start_date,
            end_date=args.end_date,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-audited-pit-loss-attribution":
        report = run_audited_pit_loss_attribution(
            settings=settings or get_settings(),
            audited_pit_universe_path=args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            start_date=args.start_date,
            end_date=args.end_date,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-audit":
        universe_descriptor = _load_current_pool_descriptor(
            args.universe_path, "current-pool-universe-input/v1"
        )
        history_descriptor = _load_current_pool_descriptor(
            args.history_summary_path, "current-pool-history-summary/v1"
        )
        risk_descriptor = _load_current_pool_descriptor(
            args.risk_path, "current-pool-risk-input/v1"
        )
        if "descriptor_sha256" in history_descriptor["payload"]:
            try:
                verified_history = verify_current_pool_history_descriptor(
                    history_descriptor["payload"], universe_descriptor["payload"]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("current-pool input descriptor rejected") from exc
            if (
                verified_history["descriptor_sha256"]
                != history_descriptor["descriptor_sha256"]
            ):
                raise ValueError("current-pool input descriptor rejected")
        if not (
            universe_descriptor["source_as_of"]
            == history_descriptor["source_as_of"]
            == risk_descriptor["source_as_of"]
        ):
            raise ValueError("current-pool input descriptor rejected")
        universe_items = _load_current_pool_universe(universe_descriptor)
        try:
            verified_risk = verify_current_pool_risk_descriptor(
                risk_descriptor["payload"], universe_descriptor["payload"]
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("current-pool input descriptor rejected") from exc
        if (
            risk_descriptor["payload"].get("universe_descriptor_sha256")
            != universe_descriptor["descriptor_sha256"]
        ):
            raise ValueError("current-pool input descriptor rejected")
        risk_by_symbol = {
            item["ts_code"]: item for item in risk_descriptor["payload"]["items"]
        }
        if set(risk_by_symbol) != verified_risk["symbols"]:
            raise ValueError("current-pool input descriptor rejected")
        merged_universe_items = []
        for item in universe_items:
            risk = risk_by_symbol[item["ts_code"]]
            merged = dict(item)
            merged["original_name"] = item.get("name")
            if risk["active_name"] is not None:
                merged["name"] = risk["active_name"]
            merged["risk_flags"] = {
                "is_st": risk["is_st"],
                "is_suspended": risk["is_suspended"],
            }
            merged_universe_items.append(merged)
        histories = _load_current_pool_history_summary(history_descriptor)
        audit = build_current_pool_coverage(
            merged_universe_items,
            histories,
            min_signal_bars=args.min_signal_bars,
        )
        classifications = {
            row["symbol"]: row
            for row in map(classify_current_pool_item, merged_universe_items)
        }
        for status in audit["item_history_status"]:
            status["signal_allowed_today"] = classifications[status["symbol"]][
                "signal_allowed_today"
            ]
        universe_symbols = {row["symbol"] for row in audit["item_history_status"]}
        history_symbols = {str(symbol).strip().upper().split(".", 1)[0] for symbol in histories}
        if not history_symbols.issubset(universe_symbols):
            raise ValueError("current-pool input descriptor rejected")
        audit.pop("canonical_sha256")
        audit.update(
            {
                "evidence_scope": "development_only",
                "source_as_of": universe_descriptor["source_as_of"],
                "source_ids": {
                    "universe": "jiaoch",
                    "history_summary": "jiaoch",
                    "risk_snapshot": "jiaoch",
                },
                "input_descriptor_sha256": {
                    "universe": universe_descriptor["descriptor_sha256"],
                    "history_summary": history_descriptor["descriptor_sha256"],
                    "risk_snapshot": risk_descriptor["descriptor_sha256"],
                },
                "risk_snapshot_complete": True,
                "risk_gate_passed": True,
                "production_recommendation_eligible": False,
                "universe_items": sorted(
                    merged_universe_items, key=lambda item: item["ts_code"]
                ),
            }
        )
        canonical = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        audit["canonical_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        _print_json(_write_current_pool_audit(args.output_dir, audit))
        return 0

    if args.command == "research-backtest":
        payload = run_candidate_research_backtest(
            settings=settings,
            provider=_get_data_provider(),
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            buy_only=args.buy_only,
            min_score=args.min_score,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_announcement_context=args.announcement_context,
            announcement_lookback_days=args.announcement_lookback_days,
            require_announcement_events=args.require_announcement_event,
            require_all_announcement_events=args.require_all_announcement_events,
            exclude_announcement_events=args.exclude_announcement_event,
            require_market_levels=args.require_market_level,
            require_signal_tags=args.require_signal_tag,
            require_all_signal_tags=args.require_all_signal_tags,
            exclude_signal_tags=args.exclude_signal_tag,
            min_prior_win_rate=args.min_prior_win_rate,
            min_prior_avg_return=args.min_prior_avg_return,
            max_prior_avg_adverse=args.max_prior_avg_adverse,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=bool(
                args.include_qualified_trades or args.qualified_trades_output
            ),
        )
        if args.qualified_trades_output:
            payload["qualified_trades_output"] = _write_qualified_trades_payload(
                args.qualified_trades_output,
                payload,
            )
            if not args.include_qualified_trades:
                payload.pop("qualified_trades", None)
        _print_json(payload)
        return 0

    if args.command == "industry-history-check":
        provider = IndustryHistoryProvider(settings.industry_history_cache_dir)
        payload = provider.source_check(
            start_date=args.start_date,
            end_date=args.end_date or date.today().isoformat(),
            max_boards=args.max_boards,
        )
        _print_json(payload)
        return 0

    if args.command == "margin-eligibility-check":
        provider = MarginEligibilityProvider(
            settings.margin_eligibility_cache_path,
            settings.enable_margin_eligibility_context,
        )
        payload = provider.source_check(as_of=args.as_of)
        _print_json(payload)
        return 0

    if args.command == "research-historical-universe":
        expected_composite_descriptor_file_sha256 = None
        if (
            historical_treatment_plan is not None
            and historical_treatment_plan["schema_version"]
            in {
            "research-treatment-input-plan/v3",
            "research-treatment-input-plan/v4",
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
            }
        ):
            _verify_development_payload_fixture_binding(
                historical_treatment_plan["development_payload_fixture"],
                composite_pit_descriptor_path=args.composite_pit_descriptor_path,
            )
            expected_composite_descriptor_file_sha256 = historical_treatment_plan[
                "development_payload_fixture"
            ]["source_descriptor_file_sha256"]
        if historical_precompute_control is not None:
            reverified_control = _verify_precompute_control_for_treatment(
                args, historical_treatment_plan, historical_treatment_plan_artifact
            )
            if reverified_control != historical_precompute_control:
                raise ValueError("precompute publication control changed before backtest")
            reverified_ready = _verify_registered_precompute_for_args(
                args,
                historical_treatment_plan,
                historical_precompute_control,
                consume_launch=False,
            )
            if reverified_ready != launcher_ready:
                raise ValueError("registered precompute control changed before backtest")
        payload = run_historical_universe_research_backtest(
            settings=settings,
            provider=(
                _OfflineTreatmentProvider()
                if historical_treatment_plan is not None
                and historical_treatment_plan["schema_version"]
                in {
                    "research-treatment-input-plan/v4",
                    "research-treatment-input-plan/v5",
                    "research-treatment-input-plan/v6",
                    "research-treatment-input-plan/v7",
                }
                else _get_data_provider()
            ),
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            max_universe_symbols=args.max_universe_symbols,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            buy_only=args.buy_only,
            min_score=args.min_score,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_announcement_context=args.announcement_context,
            announcement_lookback_days=args.announcement_lookback_days,
            require_announcement_events=args.require_announcement_event,
            require_all_announcement_events=args.require_all_announcement_events,
            exclude_announcement_events=args.exclude_announcement_event,
            require_market_levels=args.require_market_level,
            require_signal_tags=args.require_signal_tag,
            require_all_signal_tags=args.require_all_signal_tags,
            exclude_signal_tags=args.exclude_signal_tag,
            min_prior_win_rate=args.min_prior_win_rate,
            min_prior_avg_return=args.min_prior_avg_return,
            max_prior_avg_adverse=args.max_prior_avg_adverse,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=bool(
                args.include_qualified_trades or args.qualified_trades_output
            ),
            pit_universe_path=args.pit_universe_path,
            audited_pit_universe_path=args.audited_pit_universe_path,
            composite_pit_descriptor_path=args.composite_pit_descriptor_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_composite_root_sha256=args.expected_composite_root_sha256,
            expected_composite_descriptor_file_sha256=expected_composite_descriptor_file_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        if historical_treatment_plan is not None:
            summary = payload.get("summary")
            if not isinstance(summary, dict):
                raise ValueError("treatment backtest summary is missing")
            summary["treatment_input_plan"] = _treatment_input_plan_binding(
                historical_treatment_plan,
                historical_treatment_plan_artifact,
            )
            if historical_precompute_control is not None:
                summary["precompute_control"] = launcher_ready
            _verify_treatment_bound_payload(
                payload,
                historical_treatment_plan,
                historical_treatment_plan_artifact,
            )
        if args.qualified_trades_output:
            if historical_treatment_plan is None:
                payload["qualified_trades_output"] = _write_qualified_trades_payload(
                    args.qualified_trades_output,
                    payload,
                )
            else:
                payload[
                    "qualified_trades_output"
                ] = _write_treatment_qualified_trades_payload(
                    args.qualified_trades_output,
                    payload,
                    plan=historical_treatment_plan,
                    plan_artifact=historical_treatment_plan_artifact,
                )
            if not args.include_qualified_trades:
                payload.pop("qualified_trades", None)
        _print_json(payload)
        return 0

    if args.command == "research-historical-sweep":
        payload = run_historical_universe_research_backtest(
            settings=settings,
            provider=_get_data_provider(),
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            max_universe_symbols=args.max_universe_symbols,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_industry_rotation_context=args.industry_rotation_context,
            industry_rotation_max_boards=args.industry_rotation_max_boards,
            use_margin_eligibility_context=args.margin_eligibility_context,
            use_dragon_tiger_context=args.dragon_tiger_context,
            include_qualified_trades=True,
            pit_universe_path=args.pit_universe_path,
            audited_pit_universe_path=args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        qualified_trades_output = (
            _write_qualified_trades_payload(args.qualified_trades_output, payload)
            if args.qualified_trades_output
            else None
        )
        sweep_payload = sweep_qualified_trades(
            payload.get("qualified_trades") or [],
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                payload, sweep_payload, args.output_limit
            )
            if qualified_trades_output:
                compact_payload["qualified_trades_output"] = qualified_trades_output
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary"),
                "qualified_trades_output": qualified_trades_output,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-historical-hold-sweep":
        results = []
        for hold_days in _parse_hold_days_list(args.hold_days_list):
            cooldown_days = args.symbol_cooldown_days
            if cooldown_days is None:
                cooldown_days = hold_days
            payload = run_historical_universe_research_backtest(
                settings=settings,
                provider=_get_data_provider(),
                start_date=args.start_date,
                end_date=args.end_date,
                max_deep=args.max_deep,
                top_n=args.top_n,
                hold_days=hold_days,
                lookback_days=args.lookback_days,
                max_universe_symbols=args.max_universe_symbols,
                use_live_snapshot=args.live_snapshot,
                cache_dir=args.cache_dir,
                progress_every=args.progress_every,
                stop_loss_pct=args.stop_loss_pct,
                take_profit_pct=args.take_profit_pct,
                trailing_stop_pct=args.trailing_stop_pct,
                symbol_cooldown_days=cooldown_days,
                max_active_positions=args.max_active_positions,
                use_margin_eligibility_context=args.margin_eligibility_context,
                use_dragon_tiger_context=args.dragon_tiger_context,
                include_qualified_trades=True,
                pit_universe_path=args.pit_universe_path,
                audited_pit_universe_path=args.audited_pit_universe_path,
                expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
                expected_artifact_root_sha256=args.expected_artifact_root_sha256,
                temporal_contract_path=args.temporal_contract_path,
                expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
                expected_temporal_role=args.expected_temporal_role,
            )
            sweep_payload = sweep_qualified_trades(
                payload.get("qualified_trades") or [],
                hold_days=hold_days,
                top_n=args.top_n,
                symbol_cooldown_days=cooldown_days,
                max_active_positions=args.max_active_positions,
                min_trades=args.min_trades,
                max_filter_size=args.max_filter_size,
                target_win_rate_pct=args.target_win_rate_pct,
                target_drawdown_pct=args.target_drawdown_pct,
                target_one_year_return_pct=args.target_one_year_return_pct,
                exposure_multipliers=_exposure_multipliers(args),
                annual_financing_rate_pct=args.annual_financing_rate_pct,
                roundtrip_cost_bps=args.roundtrip_cost_bps,
                slippage_bps=args.slippage_bps,
                capital_model=args.capital_model,
                required_signal_tags=_split_csv_arg(args.required_signal_tags),
                excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
                market_levels=_split_csv_arg(args.market_levels),
                force_exposure_multipliers=_force_exposure_multipliers(args),
                pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
                prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
                prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
                partial_profit_activation_pct=args.partial_profit_activation_pct,
                partial_profit_fraction=args.partial_profit_fraction,
                correlation_threshold=args.correlation_threshold,
                correlation_lookback_days=args.correlation_lookback_days,
                correlation_cache_dir=args.correlation_cache_dir,
                correlation_min_periods=args.correlation_min_periods,
                correlation_history_lookback_days=args.correlation_history_lookback_days,
            )
            results.append(
                _compact_hold_sweep_result(
                    hold_days,
                    payload,
                    sweep_payload,
                    output_limit=args.output_limit,
                )
            )
        results.sort(
            key=lambda item: (
                ((item.get("diagnostics") or {}).get("best_target_win_drawdown") or {}).get(
                    "rolling_1y_latest_return_pct"
                )
                or -999
            ),
            reverse=True,
        )
        _print_json(
            {
                "hold_days": _parse_hold_days_list(args.hold_days_list),
                "result_count": len(results),
                "results": results,
            }
        )
        return 0

    if args.command == "research-sweep":
        payload = run_candidate_research_backtest(
            settings=settings,
            provider=_get_data_provider(),
            start_date=args.start_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=True,
        )
        qualified_trades_output = (
            _write_qualified_trades_payload(args.qualified_trades_output, payload)
            if args.qualified_trades_output
            else None
        )
        sweep_payload = sweep_qualified_trades(
            payload.get("qualified_trades") or [],
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                payload, sweep_payload, args.output_limit
            )
            if qualified_trades_output:
                compact_payload["qualified_trades_output"] = qualified_trades_output
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary"),
                "qualified_trades_output": qualified_trades_output,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-sweep-file":
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        if temporal_contract["contract_sha256"] != args.expected_temporal_contract_sha256:
            raise ValueError("expected temporal contract hash mismatch")
        if args.expected_temporal_role != "development":
            raise ValueError("ordinary sweep requires temporal role development")
        assert_range_allowed(
            temporal_contract,
            args.expected_temporal_role,
            args.start_date,
            args.end_date,
            "backtest",
        )
        if args.correlation_threshold is not None and float(args.correlation_threshold) != 0.0:
            raise ValueError("frozen sweep forbids legacy correlation cache reads")
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            payload = _load_qualified_trades_payload(args.qualified_trades_path)
            source_summary = payload.get("summary") or {}
            source_contract = source_summary.get("research_data_contract") or {}
            if (
                source_summary.get("artifact_root_sha256")
                or source_contract.get("artifact_root_sha256")
            ) != audited_universe.artifact_root_sha256:
                raise ValueError("qualified payload artifact root mismatch")
            if (
                source_summary.get("coverage_audit_sha256")
                or source_contract.get("coverage_audit_sha256")
            ) != audited_universe.coverage_audit_sha256:
                raise ValueError("qualified payload coverage audit mismatch")
            qualified_trades = payload.get("qualified_trades") or []
            validate_point_in_time_contract(
                source_summary,
                qualified_trades,
                artifact_base_dir=str(Path(args.qualified_trades_path).resolve().parent),
                declared_start_date=args.start_date,
                declared_end_date=args.end_date,
                audited_universe=audited_universe,
            )
        finally:
            audited_universe.close()
        sweep_payload = sweep_qualified_trades(
            qualified_trades,
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        source_payload = {"summary": payload.get("summary") or {}}
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                source_payload, sweep_payload, args.output_limit
            )
            compact_payload["qualified_trades_path"] = args.qualified_trades_path
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary") or {},
                "qualified_trades_path": args.qualified_trades_path,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-validate-file":
        authority_kind = _validate_audited_authority_args(args)
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        if temporal_contract["contract_sha256"] != args.expected_temporal_contract_sha256:
            raise ValueError("expected temporal contract hash mismatch")
        if args.expected_temporal_role != "development":
            raise ValueError("ordinary validation requires temporal role development")
        assert_range_allowed(
            temporal_contract,
            args.expected_temporal_role,
            args.start_date,
            args.end_date,
            "validate",
        )
        if args.final_oos_start != "2026-07-13":
            raise ValueError("final_oos_start must equal frozen boundary 2026-07-13")
        strategy = {
            "hold_days": args.hold_days,
            "top_n": args.top_n,
            "symbol_cooldown_days": args.symbol_cooldown_days,
            "max_active_positions": args.max_active_positions,
            "required_signal_tags": _split_csv_arg(args.required_signal_tags) or [],
            "excluded_signal_tags": _split_csv_arg(args.excluded_signal_tags) or [],
            "market_levels": _split_csv_arg(args.market_levels) or [],
            "exposure_multiplier": args.exposure_multiplier,
            "annual_financing_rate_pct": args.annual_financing_rate_pct,
            "roundtrip_cost_bps": args.roundtrip_cost_bps,
            "slippage_bps": args.slippage_bps,
            "capital_model": args.capital_model,
            "target_win_rate_pct": args.target_win_rate_pct,
            "target_win_rate_max_pct": args.target_win_rate_max_pct,
            "target_drawdown_pct": args.target_drawdown_pct,
            "target_one_year_return_pct": args.target_one_year_return_pct,
            "target_profit_factor": args.target_profit_factor,
            "target_calmar": args.target_calmar,
            "pre_exit_calendar_gap_days": args.pre_exit_calendar_gap_days,
            "partial_profit_activation_pct": args.partial_profit_activation_pct,
            "partial_profit_fraction": args.partial_profit_fraction,
            "correlation_threshold": args.correlation_threshold,
            "correlation_lookback_days": args.correlation_lookback_days,
        }
        validation = {
            "train_days": args.train_days,
            "validation_days": args.validation_days,
            "step_days": args.step_days,
            "embargo_days": args.embargo_days,
            "final_oos_start": args.final_oos_start,
            "minimum_oos_trades": args.minimum_oos_trades,
        }
        if args.register_only and args.registered_record_hash:
            raise ValueError("register-only and registered-record-hash are mutually exclusive")
        preregistration_mode = bool(args.register_only or args.registered_record_hash)
        if preregistration_mode and not args.input_plan_path:
            raise ValueError("pre-registration mode requires an input plan")
        if args.input_plan_path and not preregistration_mode:
            raise ValueError("input plan requires register-only or registered-record-hash")
        qualified_descriptor = {
            "basename": Path(args.qualified_trades_path).name,
            "sha256": None,
        }
        validation_started_event = None
        registered_event = None
        treatment_input_plan = None
        input_plan_artifact = None
        precompute_control = None
        validation_precompute_ready = None
        validation_precompute_state = None
        controlled_v3_validation = False
        single_change = "strict_purged_walk_forward_validation"
        if preregistration_mode:
            input_plan, input_plan_artifact = _load_validation_input_plan(
                args.input_plan_path
            )
            _validate_precompute_argument_phase(
                args,
                plan_schema=input_plan["schema_version"],
                phase="register" if args.register_only else "validation",
            )
            if input_plan["schema_version"] in {
                "research-treatment-input-plan/v2",
                "research-treatment-input-plan/v3",
                "research-treatment-input-plan/v4",
                "research-treatment-input-plan/v5",
                "research-treatment-input-plan/v6",
                "research-treatment-input-plan/v7",
            }:
                treatment_input_plan = input_plan
                single_change = str(input_plan["treatment"]["parameter"])
                _validate_treatment_plan_for_validation(
                    args,
                    authority_kind=authority_kind,
                    strategy=strategy,
                    plan=input_plan,
                )
                precompute_control = _verify_precompute_control_for_treatment(
                    args, input_plan, input_plan_artifact
                )
            else:
                expected_plan_authority = (
                    "ordered_composite"
                    if authority_kind == "ordered_composite"
                    else "single_audited_artifact"
                )
                if input_plan["authority_kind"] != expected_plan_authority:
                    raise ValueError("validation input plan authority kind mismatch")
                if (
                    input_plan["qualified_trades_basename"]
                    != qualified_descriptor["basename"]
                ):
                    raise ValueError("validation input plan qualified basename mismatch")
            registration_contract = _build_validation_registration_contract(
                args,
                authority_kind=authority_kind,
                strategy=strategy,
                validation=validation,
                input_plan=input_plan,
                input_plan_artifact=input_plan_artifact,
                single_change=single_change,
                precompute_control=precompute_control,
            )
            registration_contract_sha256 = _canonical_payload_sha256(
                registration_contract
            )
            controlled_v3_validation = registration_contract.get(
                "schema_version"
            ) in {
                "research-validation-registration/v3",
                "research-validation-registration/v4",
            }
            if args.register_only:
                registration_event = {
                    "event_id": f"{args.experiment_id}:registered",
                    "experiment_id": args.experiment_id,
                    "event_type": "registered",
                    "hypothesis": args.hypothesis,
                    "expected_mechanism": args.expected_mechanism,
                    "single_change": single_change,
                    "falsification_criterion": args.falsification_criterion,
                    "exit_criterion": args.exit_criterion,
                    "qualified_trades_artifact": qualified_descriptor,
                    "strategy": strategy,
                    "validation": validation,
                    "input_plan_artifact": input_plan_artifact,
                    "registration_contract": registration_contract,
                    "registration_contract_sha256": registration_contract_sha256,
                }
                if precompute_control is None:
                    registered_event = append_experiment_event(
                        args.ledger_path, registration_event
                    )
                else:
                    if (
                        args.expected_ledger_sequence is None
                        or args.expected_ledger_record_hash is None
                    ):
                        raise ValueError("controlled registration ledger tip is missing")
                    registered_event = register_precompute_v1(
                        args.ledger_path,
                        workspace_root=Path(__file__).resolve().parent.parent,
                        event=registration_event,
                        verified_control=precompute_control,
                        expected_sequence=args.expected_ledger_sequence,
                        expected_record_hash=args.expected_ledger_record_hash,
                        allowed_nonterminal_records=(
                            input_plan["legacy_quarantine"]["records"]
                            if input_plan.get("schema_version")
                            in {
                                "research-treatment-input-plan/v6",
                                "research-treatment-input-plan/v7",
                            }
                            else _parse_allowed_nonterminal_records(
                                args.allowed_nonterminal_record
                            )
                        ),
                        minimum_sequence_exclusive=(
                            args.minimum_registration_sequence_exclusive
                        ),
                    )
                _print_json(registered_event)
                return 0
        else:
            _validate_precompute_argument_phase(
                args, plan_schema=None, phase="validation"
            )
            registered_event = append_experiment_event(
                args.ledger_path,
                {
                    "event_id": f"{args.experiment_id}:registered",
                    "experiment_id": args.experiment_id,
                    "event_type": "registered",
                    "hypothesis": args.hypothesis,
                    "expected_mechanism": args.expected_mechanism,
                    "single_change": single_change,
                    "falsification_criterion": args.falsification_criterion,
                    "exit_criterion": args.exit_criterion,
                    "qualified_trades_artifact": qualified_descriptor,
                    "strategy": strategy,
                    "validation": validation,
                },
            )
        audited_universe = None
        native_evidence = None
        native_evidence_artifact = None
        qualified_input_snapshot_artifact = None
        strict_evidence_artifact = None
        strict_qualified_artifact = None
        preflight = None
        claimed_input_artifacts = None
        terminal_recorded = False
        try:
            if preregistration_mode:
                if precompute_control is None:
                    preflight_registered_experiment(
                        args.ledger_path,
                        experiment_id=args.experiment_id,
                        registered_record_hash=args.registered_record_hash,
                        expected_registration_contract=registration_contract,
                        expected_input_plan_artifact=input_plan_artifact,
                    )
                else:
                    validation_precompute_state = (
                        _verify_validation_precompute_for_args(
                            args, treatment_input_plan, precompute_control
                        )
                    )
                    validation_precompute_ready = validation_precompute_state[
                        "ready"
                    ]
                    precomputed_preflight = preflight_precomputed_experiment(
                        args.ledger_path,
                        experiment_id=args.experiment_id,
                        registered_record_hash=args.registered_record_hash,
                        launch_started_record_hash=(
                            args.precompute_launch_started_record_hash
                        ),
                        precompute_completed_record_hash=(
                            args.precompute_completed_record_hash
                        ),
                        expected_registration_contract=registration_contract,
                        expected_input_plan_artifact=input_plan_artifact,
                        expected_run_result_artifact=validation_precompute_state[
                            "run_result_artifact"
                        ],
                    )
                    if (
                        precomputed_preflight["registered"]
                        != validation_precompute_state["registered"]
                        or precomputed_preflight["precompute_completed"]
                        != validation_precompute_state["precompute_completed"]
                        or precomputed_preflight.get("precompute_launch_started")
                        != validation_precompute_state.get(
                            "precompute_launch_started"
                        )
                    ):
                        raise ValueError(
                            "precomputed validation state changed before input read"
                        )
            qualified_path = Path(args.qualified_trades_path)
            qualified_raw = _read_bounded_regular_file_snapshot(
                qualified_path, max_bytes=64 * 1024 * 1024
            )
            qualified_sha256 = hashlib.sha256(qualified_raw).hexdigest()
            payload = _load_qualified_trades_payload(
                args.qualified_trades_path, raw_bytes=qualified_raw
            )
            source_summary = payload.get("summary") or {}
            if treatment_input_plan is not None:
                _verify_treatment_bound_payload(
                    payload,
                    treatment_input_plan,
                    input_plan_artifact,
                )
            if validation_precompute_ready is not None:
                _verify_precompute_result_output_binding(
                    payload=payload,
                    qualified_path=qualified_path,
                    qualified_raw=qualified_raw,
                    verified_result=validation_precompute_state,
                )
            source_contract = source_summary.get("research_data_contract") or {}
            audited_universe = _open_audited_authority(args)
            if (
                source_summary.get("artifact_root_sha256")
                or source_contract.get("artifact_root_sha256")
            ) != audited_universe.artifact_root_sha256:
                raise ValueError("expected artifact root hash mismatch")
            if (
                source_summary.get("coverage_audit_sha256")
                or source_contract.get("coverage_audit_sha256")
            ) != audited_universe.coverage_audit_sha256:
                raise ValueError("expected coverage audit hash mismatch")
            qualified_trades = payload.get("qualified_trades") or []
            if treatment_input_plan is not None:
                preflight = _preflight_treatment_validation_artifacts(
                    args,
                    qualified_raw=qualified_raw,
                    qualified_sha256=qualified_sha256,
                    payload=payload,
                    treatment_input_plan=treatment_input_plan,
                    input_plan_artifact=input_plan_artifact,
                    audited_universe=audited_universe,
                )
                payload = preflight["payload"]
                source_summary = payload.get("summary") or {}
                qualified_trades = preflight["qualified_trades"]
                data_contract = preflight["data_contract"]
                qualified_input_snapshot_artifact = preflight[
                    "qualified_input_snapshot"
                ]
                native_evidence = preflight["artifact_native_evidence"]
                native_evidence_artifact = preflight[
                    "artifact_native_evidence_artifact"
                ]
                strict_evidence_artifact = preflight["strict_evidence_artifact"]
                strict_qualified_artifact = preflight[
                    "strict_qualified_artifact"
                ]
            else:
                data_contract = validate_point_in_time_contract(
                    source_summary,
                    qualified_trades,
                    artifact_base_dir=str(
                        Path(args.qualified_trades_path).resolve().parent
                    ),
                    declared_start_date=args.start_date,
                    declared_end_date=args.end_date,
                    audited_universe=audited_universe,
                )
            verified_authority = data_contract["verified_authority"]
            expected_root = (
                args.expected_composite_root_sha256
                if authority_kind == "ordered_composite"
                else args.expected_artifact_root_sha256
            )
            verified_root = (
                verified_authority.get("composite_root_sha256")
                if authority_kind == "ordered_composite"
                else verified_authority.get("artifact_root_sha256")
            )
            if verified_root != expected_root:
                raise ValueError("verified artifact root hash mismatch")
            if (
                authority_kind == "single_artifact"
                and verified_authority["coverage_audit_sha256"]
                != args.expected_coverage_audit_sha256
            ):
                raise ValueError("verified coverage audit hash mismatch")
            if treatment_input_plan is not None:
                claimed_input_artifacts = {
                    "qualified_treatment_output": {
                        "basename": qualified_descriptor["basename"],
                        "path": str(qualified_path.resolve()),
                        "sha256": qualified_sha256,
                        "bytes": len(qualified_raw),
                    },
                    "qualified_input_snapshot": preflight[
                        "qualified_input_snapshot"
                    ],
                    "artifact_native_evidence": preflight[
                        "artifact_native_evidence_artifact"
                    ],
                    "strict_research_evidence": preflight[
                        "strict_evidence_artifact"
                    ],
                    "strict_qualified_trades": preflight[
                        "strict_qualified_artifact"
                    ],
                }
                if validation_precompute_state is not None:
                    claimed_input_artifacts["precompute_run_result"] = (
                        validation_precompute_state["run_result_artifact"]
                    )
            if preregistration_mode:
                if validation_precompute_ready is not None:
                    reverified_control = _verify_precompute_control_for_treatment(
                        args, treatment_input_plan, input_plan_artifact
                    )
                    if reverified_control != precompute_control:
                        raise ValueError(
                            "precompute publication changed before validation claim"
                        )
                    reverified_ready = _verify_validation_precompute_for_args(
                        args, treatment_input_plan, precompute_control
                    )
                    if reverified_ready != validation_precompute_state:
                        raise ValueError(
                            "registered precompute changed before validation claim"
                        )
                    _verify_precompute_result_output_binding(
                        payload=payload,
                        qualified_path=qualified_path,
                        qualified_raw=qualified_raw,
                        verified_result=reverified_ready,
                    )
                if precompute_control is None:
                    claimed = claim_registered_experiment(
                        args.ledger_path,
                        experiment_id=args.experiment_id,
                        registered_record_hash=args.registered_record_hash,
                        expected_registration_contract=registration_contract,
                        expected_input_plan_artifact=input_plan_artifact,
                        claimed_input_artifacts=claimed_input_artifacts,
                    )
                else:
                    claimed = claim_precomputed_experiment(
                        args.ledger_path,
                        experiment_id=args.experiment_id,
                        registered_record_hash=args.registered_record_hash,
                        launch_started_record_hash=(
                            args.precompute_launch_started_record_hash
                        ),
                        precompute_completed_record_hash=(
                            args.precompute_completed_record_hash
                        ),
                        expected_registration_contract=registration_contract,
                        expected_input_plan_artifact=input_plan_artifact,
                        expected_run_result_artifact=(
                            validation_precompute_state["run_result_artifact"]
                        ),
                        claimed_input_artifacts=claimed_input_artifacts,
                    )
                registered_event = claimed["registered"]
                validation_started_event = claimed["validation_started"]
            if treatment_input_plan is not None:
                if validation_started_event.get("claimed_input_artifacts") != (
                    claimed_input_artifacts
                ):
                    raise ValueError("validation claim input artifact binding mismatch")
                if precompute_control is not None:
                    post_claim_control = _verify_precompute_control_for_treatment(
                        args, treatment_input_plan, input_plan_artifact
                    )
                    if post_claim_control != precompute_control:
                        raise ValueError(
                            "precompute publication changed after validation claim"
                        )
                    post_claim_precompute = (
                        _verify_validation_precompute_for_args(
                            args,
                            treatment_input_plan,
                            precompute_control,
                            validation_started_record_hash=(
                                validation_started_event["record_hash"]
                            ),
                        )
                    )
                    if (
                        post_claim_precompute.get("registered")
                        != validation_precompute_state.get("registered")
                        or post_claim_precompute.get("precompute_completed")
                        != validation_precompute_state.get("precompute_completed")
                        or post_claim_precompute.get("precompute_launch_started")
                        != validation_precompute_state.get(
                            "precompute_launch_started"
                        )
                        or post_claim_precompute.get("run_result_artifact")
                        != validation_precompute_state.get("run_result_artifact")
                        or post_claim_precompute.get("validation_started")
                        != validation_started_event
                    ):
                        raise ValueError(
                            "precompute result changed after validation claim"
                        )
                    _verify_precompute_result_output_binding(
                        payload=payload,
                        qualified_path=qualified_path,
                        qualified_raw=qualified_raw,
                        verified_result=post_claim_precompute,
                    )
                preflight = _reverify_treatment_validation_artifacts_after_claim(
                    args,
                    qualified_raw=qualified_raw,
                    treatment_input_plan=treatment_input_plan,
                    input_plan_artifact=input_plan_artifact,
                    audited_universe=audited_universe,
                    preflight=preflight,
                    claimed_input_artifacts=claimed_input_artifacts,
                )
                payload = preflight["payload"]
                source_summary = payload.get("summary") or {}
                qualified_trades = preflight["qualified_trades"]
                data_contract = preflight["data_contract"]
                qualified_input_snapshot_artifact = preflight[
                    "qualified_input_snapshot"
                ]
                native_evidence = preflight["artifact_native_evidence"]
                native_evidence_artifact = preflight[
                    "artifact_native_evidence_artifact"
                ]
                strict_evidence_artifact = preflight["strict_evidence_artifact"]
                strict_qualified_artifact = preflight[
                    "strict_qualified_artifact"
                ]
            report = run_frozen_strategy_validation(
                qualified_trades,
                strategy=strategy,
                validation=validation,
            )
            report_output = {
                "experiment_id": args.experiment_id,
                "data_contract": data_contract,
                **report,
            }
            report_artifact = write_report_artifact(args.artifact_dir, report_output)
            native_authority_keys = {
                "artifact_root_sha256",
                "coverage_audit_sha256",
                "temporal_contract_sha256",
                "temporal_role",
                "artifact_manifest_sha256",
                "market_generation_root_sha256",
                "stock_generation_lineage_sha256",
            }
            verified_authority_payload = data_contract.get("verified_authority") or {}
            native_authority_available = (
                native_authority_keys.issubset(verified_authority_payload)
                or verified_authority_payload.get("authority_kind")
                == "ordered_composite"
            )
            if treatment_input_plan is None and native_authority_available:
                qualified_input_snapshot_path = _materialize_content_addressed_snapshot(
                    qualified_raw,
                    sha256=qualified_sha256,
                    output_dir=args.artifact_dir,
                )
                qualified_input_snapshot_artifact = {
                    "path": str(qualified_input_snapshot_path.resolve()),
                    "sha256": qualified_sha256,
                    "bytes": len(qualified_raw),
                }
                native_evidence = build_artifact_native_evidence(
                    audited_universe=audited_universe,
                    qualified_trades=qualified_trades,
                    qualified_trades_path=str(qualified_input_snapshot_path.resolve()),
                    artifact_root=str(Path(args.artifact_dir).resolve()),
                )
                native_evidence_artifact = write_artifact_native_evidence(
                    args.artifact_dir, native_evidence
                )
                native_evidence = verify_artifact_native_evidence(
                    native_evidence_artifact["path"],
                    audited_universe=audited_universe,
                    artifact_root=str(Path(args.artifact_dir).resolve()),
                )
                verified_snapshot = _read_bounded_regular_file_snapshot(
                    qualified_input_snapshot_path,
                    max_bytes=64 * 1024 * 1024,
                )
                if verified_snapshot != qualified_raw:
                    raise ValueError("native evidence input snapshot changed")
            native_eligibility = (native_evidence or {}).get("eligibility") or {}
            selection_replay = report.get("strategy_selection_replay") or {}
            selection_replay_bound = (
                selection_replay.get("bound") is True
                and selection_replay.get("selection_receipt_bound") is True
                and selection_replay.get("candidate_pool_sha256") == report.get("dataset_sha256")
                and selection_replay.get("strategy_sha256") == report.get("strategy_sha256")
            )
            evidence = {
                "pit_contract": bool(data_contract.get("verified_authority")),
                "temporal_contract": bool(
                    data_contract.get("verified_authority", {}).get("temporal_contract_sha256")
                ),
                "cost_slippage": float(strategy.get("roundtrip_cost_bps", 0)) > 0
                and float(strategy.get("slippage_bps", 0)) >= 0,
                "artifact_execution": bool(native_eligibility.get("qualified_trade_lineage_bound")),
                "strategy_signal_replay": bool(
                    native_eligibility.get("strategy_signal_replay_bound")
                ),
                "strategy_entry_decision": bool(
                    native_eligibility.get("strategy_entry_decision_bound")
                ),
                "strategy_selection_replay": selection_replay_bound,
                "outcome_replay": bool(native_eligibility.get("outcome_replay_bound")),
                "double_cost": False,
                "regime": False,
                "final_oos": report.get("final_oos", {}).get("status") == "loaded",
                "shadow": False,
                "live_monitoring": False,
                "pit_verified": bool(data_contract.get("verified_authority")),
                "data_contract": data_contract,
                "artifact_native_evidence": native_evidence_artifact,
                "qualified_input_snapshot": qualified_input_snapshot_artifact,
                "strict_research_evidence": strict_evidence_artifact,
                "strict_qualified_trades": strict_qualified_artifact,
                "upstream_treatment_output": (
                    claimed_input_artifacts["qualified_treatment_output"]
                    if claimed_input_artifacts is not None
                    else None
                ),
            }
            profile_receipt = build_profile_evidence_receipt(
                experiment_id=args.experiment_id,
                strategy=strategy,
                validation=validation,
                validation_report=report,
                rolling_12m=(report.get("aggregate_validation") or {}).get("rolling_1y_windows")
                or [],
                evidence=evidence,
                source_artifact=(
                    strict_qualified_artifact
                    if treatment_input_plan is not None
                    else {
                        "path": str(Path(args.qualified_trades_path).resolve()),
                        "sha256": qualified_sha256,
                        "bytes": len(qualified_raw),
                    }
                ),
                report_artifact=report_artifact,
                ledger_anchor=(
                    {
                        "registered": {
                            "event_id": registered_event.get("event_id"),
                            "sequence": registered_event.get("sequence"),
                            "record_hash": registered_event.get("record_hash"),
                        },
                        "validation_started": {
                            "event_id": validation_started_event.get("event_id"),
                            "sequence": validation_started_event.get("sequence"),
                            "record_hash": validation_started_event.get("record_hash"),
                        },
                    }
                    if validation_started_event is not None
                    else {
                        "event_id": registered_event.get("event_id"),
                        "sequence": registered_event.get("sequence"),
                        "record_hash": registered_event.get("record_hash"),
                    }
                ),
            )
            profile_verification = verify_profile_evidence_receipt(profile_receipt)
            if profile_verification.get("ok") is not True:
                raise ValueError(
                    "profile evidence receipt verification failed: "
                    + ",".join(profile_verification.get("errors") or [])
                )
            profile_receipt_artifact = write_report_artifact(args.artifact_dir, profile_receipt)
        except BaseException as exc:
            terminal_event = "failed" if isinstance(exc, Exception) else "aborted"
            error_code = (
                "VALIDATION_ABORTED"
                if terminal_event == "aborted"
                else "VALIDATION_INPUT_REJECTED"
                if isinstance(exc, ValueError)
                else "VALIDATION_INTERNAL_ERROR"
            )
            if validation_started_event is not None and controlled_v3_validation:
                _seal_controlled_validation_completion_failure(
                    args,
                    validation_started_event,
                    error_type=type(exc).__name__,
                )
                terminal_recorded = True
            elif validation_started_event is not None or not preregistration_mode:
                append_experiment_event(
                    args.ledger_path,
                    {
                        "event_id": f"{args.experiment_id}:{terminal_event}",
                        "experiment_id": args.experiment_id,
                        "event_type": terminal_event,
                        "error_code": error_code,
                        "error_type": type(exc).__name__,
                        "message": "validation aborted"
                        if terminal_event == "aborted"
                        else "validation failed",
                    },
                )
                terminal_recorded = True
            elif precompute_control is not None:
                try:
                    fail_precomputed_experiment_if_current(
                        args.ledger_path,
                        experiment_id=args.experiment_id,
                        registered_record_hash=args.registered_record_hash,
                        launch_started_record_hash=(
                            args.precompute_launch_started_record_hash
                        ),
                        precompute_completed_record_hash=(
                            args.precompute_completed_record_hash
                        ),
                        failure_code=error_code,
                        failure_phase="validation_preclaim",
                        error_type=type(exc).__name__,
                    )
                    terminal_recorded = True
                except BaseException as terminal_exc:
                    if hasattr(exc, "add_note"):
                        exc.add_note(
                            "precomputed terminalization was not appended: "
                            f"{type(terminal_exc).__name__}"
                        )
            raise
        finally:
            if audited_universe is not None:
                try:
                    audited_universe.close()
                except BaseException as close_exc:
                    if (
                        not terminal_recorded
                        and (
                            validation_started_event is not None
                            or not preregistration_mode
                        )
                    ):
                        if controlled_v3_validation:
                            _seal_controlled_validation_completion_failure(
                                args,
                                validation_started_event,
                                error_type=type(close_exc).__name__,
                            )
                        else:
                            append_experiment_event(
                                args.ledger_path,
                                {
                                    "event_id": f"{args.experiment_id}:failed",
                                    "experiment_id": args.experiment_id,
                                    "event_type": "failed",
                                    "error_code": "VALIDATION_INTERNAL_ERROR",
                                    "error_type": type(close_exc).__name__,
                                    "message": "validation authority close failed",
                                },
                            )
                        terminal_recorded = True
                    raise
        try:
            completion_event = {
                "event_id": f"{args.experiment_id}:completed",
                "experiment_id": args.experiment_id,
                "event_type": "completed",
                "qualified_trades_artifact": {
                    "basename": qualified_descriptor["basename"],
                    "sha256": qualified_sha256,
                    "bytes": len(qualified_raw),
                },
                **(
                    {
                        "qualified_input_snapshot": qualified_input_snapshot_artifact,
                        "artifact_native_evidence": native_evidence_artifact,
                        "strict_research_evidence": strict_evidence_artifact,
                        "strict_qualified_trades": strict_qualified_artifact,
                    }
                    if treatment_input_plan is not None
                    else {}
                ),
                **(
                    {
                        "registered_record_hash": registered_event["record_hash"],
                        "validation_started_record_hash": validation_started_event[
                            "record_hash"
                        ],
                        "input_plan_sha256": (
                            treatment_input_plan["plan_sha256"]
                            if treatment_input_plan is not None
                            else input_plan_artifact["sha256"]
                        ),
                        "input_plan_artifact_sha256": input_plan_artifact["sha256"],
                    }
                    if validation_started_event is not None
                    else {}
                ),
                "dataset_sha256": report["dataset_sha256"],
                "strategy_sha256": report["strategy_sha256"],
                "validation_sha256": report["validation_sha256"],
                "data_contract": data_contract,
                "report_artifact": report_artifact,
                "profile_evidence_receipt": profile_receipt_artifact,
                "qualification": report["qualification"],
                "aggregate_validation": report["aggregate_validation"],
                    "final_oos": report["final_oos"],
                }
            if controlled_v3_validation:
                complete_validation_started_experiment_if_current(
                    args.ledger_path,
                    experiment_id=args.experiment_id,
                    registered_record_hash=args.registered_record_hash,
                    validation_started_record_hash=validation_started_event[
                        "record_hash"
                    ],
                    completion_event=completion_event,
                )
            else:
                append_experiment_event(args.ledger_path, completion_event)
        except BaseException as exc:
            if validation_started_event is not None and controlled_v3_validation:
                _seal_controlled_validation_completion_failure(
                    args,
                    validation_started_event,
                    error_type=type(exc).__name__,
                )
            elif validation_started_event is not None or not preregistration_mode:
                append_experiment_event(
                    args.ledger_path,
                    {
                        "event_id": f"{args.experiment_id}:failed",
                        "experiment_id": args.experiment_id,
                        "event_type": "failed",
                        "error_code": "VALIDATION_INTERNAL_ERROR",
                        "error_type": type(exc).__name__,
                        "message": "validation completion recording failed",
                    },
                )
            raise
        _print_json(
            {
                "ledger_path": args.ledger_path,
                "report_artifact": report_artifact,
                "profile_evidence_receipt": profile_receipt_artifact,
                **report_output,
            }
        )
        return 0

    if args.command == "research-build-pit-universe":
        source_manifest = read_json(args.source_manifest_path, {})
        if not isinstance(source_manifest, dict):
            raise ValueError("source manifest must be a JSON object")
        payload = build_pit_universe_payload(
            security_master=_load_json_rows(
                args.security_master_path, "items", "security_master", "data"
            ),
            trade_calendar=_load_json_rows(
                args.trade_calendar_path, "items", "sessions", "trade_calendar", "data"
            ),
            daily_universe=_load_json_rows(
                args.daily_universe_path, "items", "daily_universe", "data"
            ),
            source_manifest=source_manifest,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        artifact = write_pit_universe_artifact(args.output_dir, payload)
        _print_json(
            {
                "artifact": artifact,
                "coverage": payload["coverage"],
                "hashes": payload["hashes"],
                "quality": payload["quality"],
            }
        )
        return 0

    if args.command == "research-pit-ingest-response":
        try:
            params = json.loads(args.params_json)
        except json.JSONDecodeError as exc:
            raise ValueError("--params-json must be valid JSON") from exc
        if not isinstance(params, dict):
            raise ValueError("--params-json must decode to an object")
        raw_response_path = Path(args.raw_response_path)
        if raw_response_path.stat().st_size > MAX_RAW_BYTES[args.dataset]:
            raise ValueError("raw response file exceeds the dataset byte limit")
        receipt = PITReceiptStore(args.store_dir).ingest_tushare_response(
            dataset=args.dataset,
            partition_key=args.partition_key,
            endpoint=args.endpoint,
            params=params,
            raw_bytes=raw_response_path.read_bytes(),
            http_status=args.http_status,
            retrieved_at=args.retrieved_at,
            row_cap=args.row_cap,
        )
        _print_json(receipt)
        return 0

    if args.command == "research-pit-ingest-cninfo-suspension":

        def read_pdf(path_value, label):
            path = Path(path_value)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} PDF path is missing or unsafe")
            if path.stat().st_size > MAX_CNINFO_PDF_BYTES:
                raise ValueError(f"{label} PDF exceeds the official evidence byte limit")
            return path.read_bytes()

        interval = PITReceiptStore(args.store_dir).ingest_cninfo_suspension_interval(
            ts_code=args.ts_code,
            start_raw_bytes=read_pdf(args.start_pdf_path, "start"),
            start_source_url=args.start_source_url,
            start_published_at=args.start_published_at,
            start_retrieved_at=args.start_retrieved_at,
            resume_raw_bytes=read_pdf(args.resume_pdf_path, "resume"),
            resume_source_url=args.resume_source_url,
            resume_published_at=args.resume_published_at,
            resume_retrieved_at=args.resume_retrieved_at,
        )
        _print_json(interval)
        return 0

    if args.command == "research-pit-audit-store":
        exchanges = tuple(
            item.strip().upper() for item in str(args.calendar_exchanges).split(",") if item.strip()
        )
        audit = PITReceiptStore(args.store_dir).audit_coverage(
            start_date=args.start_date,
            end_date=args.end_date,
            calendar_exchanges=exchanges,
        )
        _print_json(audit)
        return 0

    if args.command == "research-pit-publish-universe":
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        assert_range_allowed(
            temporal_contract,
            args.temporal_role,
            args.start_date,
            args.end_date,
            "publish",
        )
        artifact = PITReceiptStore(args.store_dir).publish_universe_artifact(
            args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            temporal_contract_sha256=temporal_contract["contract_sha256"],
            temporal_role=args.temporal_role,
            permitted_operation="publish",
            promotion_eligible=False,
        )
        _print_json(artifact)
        return 0

    if args.command == "research-build-evidence-bundle":
        output_root = Path(args.output_dir).resolve()
        pit_path = Path(args.pit_universe_path).resolve()
        market_path = Path(args.market_data_manifest_path).resolve()
        try:
            pit_path.relative_to(output_root)
            market_path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError(
                "PIT universe and market manifest must be inside --output-dir"
            ) from exc
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            authority = audited_authority_from_universe(audited_universe)
            evidence = write_research_evidence_bundle(
                str(output_root),
                pit_universe_path=str(pit_path),
                market_data_manifest_path=str(market_path),
                audited_authority=authority,
            )
            verified = verify_research_evidence_bundle(evidence["path"])
        finally:
            audited_universe.close()
        _print_json(
            {
                "status": "development_integrity_only",
                "strict_validation_eligible": False,
                "reason": "evidence bundle still contains unresolved component and lineage eligibility reasons",
                "audited_authority": authority,
                "evidence": {
                    "path": evidence["path"],
                    "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
                    "schema_version": verified["schema_version"],
                    "coverage": verified["coverage"],
                    "eligibility": verified["eligibility"],
                },
            }
        )
        return 0

    if args.command == "research-build-artifact-native-evidence":
        _validate_audited_authority_args(args)
        qualified_path = Path(args.qualified_trades_path).resolve()
        qualified_payload = _load_qualified_trades_payload(str(qualified_path))
        qualified_trades = qualified_payload.get("qualified_trades") or []
        audited_universe = _open_audited_authority(args)
        try:
            evidence_payload = build_artifact_native_evidence(
                audited_universe=audited_universe,
                qualified_trades=qualified_trades,
                qualified_trades_path=str(qualified_path),
                artifact_root=str(qualified_path.parent),
            )
            descriptor = write_artifact_native_evidence(args.output_dir, evidence_payload)
            verified = verify_artifact_native_evidence(
                descriptor["path"],
                audited_universe=audited_universe,
                artifact_root=str(qualified_path.parent),
            )
        finally:
            audited_universe.close()
        native_development_eligible = (verified.get("eligibility") or {}).get(
            "eligible_for_development_validation"
        ) is True
        _print_json(
            {
                "status": (
                    "development_native_evidence_complete"
                    if native_development_eligible
                    else "development_execution_integrity_only"
                ),
                "ready_for_strict_compilation": native_development_eligible,
                # Native evidence alone is intentionally not the data contract;
                # the next compiler step must bind it into a v3 strict bundle.
                "strict_validation_eligible": False,
                "artifact_root": str(qualified_path.parent),
                "evidence": descriptor,
                "verified": bool(verified.get("verified")),
                "eligibility": verified.get("eligibility"),
                "strategy_signal_replay": verified.get("strategy_signal_replay"),
                "trade_lineage_sha256": verified.get("trade_lineage_sha256"),
            }
        )
        return 0

    if args.command == "research-build-strict-evidence-bundle":
        _validate_audited_authority_args(args)
        output_root = Path(args.output_dir).resolve()
        native_path = Path(args.artifact_native_evidence_path).resolve()
        try:
            native_path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("artifact-native evidence must be inside --output-dir") from exc
        audited_universe = _open_audited_authority(args)
        try:
            descriptor = write_strict_research_evidence_bundle(
                str(output_root),
                audited_universe=audited_universe,
                artifact_native_evidence_path=str(native_path),
            )
            verified = verify_research_evidence_bundle(
                descriptor["path"],
                audited_universe=audited_universe,
                artifact_root=str(output_root),
            )
            native_qualified_path = (
                output_root / verified["artifact_native_evidence"]["qualified_trades"]["path"]
            )
            compiled_qualified = write_strict_qualified_trades_payload(
                str(output_root),
                source_payload_path=str(native_qualified_path),
                strict_evidence_bundle_path=descriptor["path"],
                audited_universe=audited_universe,
            )
        finally:
            audited_universe.close()
        _print_json(
            {
                "status": "development_validation_eligible",
                "eligible_for_development_validation": True,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
                "live_proof": False,
                "automatic_order_submission": False,
                "evidence": descriptor,
                "compiled_qualified_trades": compiled_qualified,
                "validation_verified": compiled_qualified["validation_verified"],
                "eligibility": verified["eligibility"],
                "audited_authority": verified["audited_authority"],
                "qualified_trades_sha256": verified["qualified_trades_sha256"],
                "qualified_trade_lineage_sha256": verified["qualified_trade_lineage_sha256"],
            }
        )
        return 0

    if args.command in {
        "research-pit-fetch-tushare",
        "research-pit-fetch-calendars",
        "research-current-pool-fetch-market-jiaoch",
    }:
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        assert_range_allowed(
            temporal_contract,
            args.temporal_role,
            args.start_date,
            args.end_date,
            "collect",
        )
        source = resolve_tushare_source(
            args.source_profile,
            api_url=args.api_url,
            allow_insecure_http=args.allow_insecure_official_http,
        )
        collector = ControlledTushareCollector(
            store=PITReceiptStore(args.store_dir),
            token=source.token,
            api_url=source.api_url,
            transport=UrllibTushareTransport(proxy_url=source.proxy_url),
            clock=SystemTrustedClock(),
            max_attempts=args.max_attempts,
            timeout_s=args.timeout_seconds,
            allow_insecure_http=args.allow_insecure_official_http,
            allowed_hosts=source.allowed_hosts,
            source_profile=source.name,
            request_protocol=source.request_protocol,
            row_cap_overrides=dict(source.row_cap_overrides),
            network_route=source.network_route,
            proxy_endpoint=source.proxy_url,
            temporal_contract=temporal_contract,
            temporal_role=args.temporal_role,
            temporal_contract_sha256=temporal_contract["contract_sha256"],
            temporal_start_date=args.start_date,
            temporal_end_date=args.end_date,
            workers=args.workers,
        )
        if args.command == "research-pit-fetch-calendars":
            report = collector.collect_trade_calendars(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
            )
        elif args.command == "research-current-pool-fetch-market-jiaoch":
            report = collector.collect_current_pool_market(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
                batch_size=args.batch_size,
                progress_path=(Path(args.progress_path) if args.progress_path else None),
            )
        else:
            report = collector.collect(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
                workers=args.workers,
            )
        _print_json(report)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
