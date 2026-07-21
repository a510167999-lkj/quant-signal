"""Supervised parent control for one registered historical precompute run."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import stat
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any, Mapping, Sequence

from app import jobs
from app import research_precompute_control
from app import research_validation
from app.research_control_quarantine import (
    quarantine_binding_sha256_v1,
    validate_exact_open_set_v1,
    validate_frozen_quarantine_binding_v1,
)
from app.research_partitions import load_temporal_partition_contract


class PrecomputeParentError(RuntimeError):
    pass


_TERMINAL_EVENTS = {"decision", "failed", "aborted"}
_OWNED_PARENT_GUARDS: set[str] = set()
_INTEGER_OPTIONS = (
    ("max_deep", "--max-deep"),
    ("top_n", "--top-n"),
    ("hold_days", "--hold-days"),
    ("lookback_days", "--lookback-days"),
    ("max_universe_symbols", "--max-universe-symbols"),
)


@contextmanager
def _exclusive_parent_guard(registered_record_hash: str):
    if os.name != "nt":
        raise PrecomputeParentError("precompute parent guard requires Windows")
    name = f"Local\\quant-signal-lkj-precompute-{registered_record_hash}"
    if name in _OWNED_PARENT_GUARDS:
        raise PrecomputeParentError("another precompute parent owns this launch")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait = kernel32.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel32.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create_mutex(None, False, name)
    if not handle:
        raise PrecomputeParentError("precompute parent guard could not be created")
    acquired = False
    try:
        result = int(wait(handle, 0))
        if result not in {0, 0x80}:
            raise PrecomputeParentError("another precompute parent owns this launch")
        acquired = True
        _OWNED_PARENT_GUARDS.add(name)
        yield {"name": name, "abandoned": result == 0x80}
    finally:
        if acquired:
            _OWNED_PARENT_GUARDS.discard(name)
            release(handle)
        close(handle)
_OPTIONAL_NUMBER_OPTIONS = (
    ("min_score", "--min-score"),
    ("min_prior_win_rate", "--min-prior-win-rate"),
    ("min_prior_avg_return", "--min-prior-avg-return"),
    ("max_prior_avg_adverse", "--max-prior-avg-adverse"),
)
_OPTIONAL_INTEGER_OPTIONS = (
    ("announcement_lookback_days", "--announcement-lookback-days"),
)
_OPTIONAL_TEXT_OPTIONS = (
    ("require_announcement_event", "--require-announcement-event"),
    ("exclude_announcement_event", "--exclude-announcement-event"),
    ("require_market_level", "--require-market-level"),
    ("require_signal_tag", "--require-signal-tag"),
    ("exclude_signal_tag", "--exclude-signal-tag"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parent control for one registered research precompute",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", allow_abbrev=False)
    run.add_argument("--ledger-path", required=True)
    run.add_argument("--experiment-id", required=True)
    run.add_argument("--registered-record-hash", required=True)
    run.add_argument("--expected-current-tip-sequence", required=True, type=int)
    run.add_argument("--expected-current-tip-record-hash", required=True)
    return parser


def _select_exact_launchable_registration(
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_tip_sequence: int,
    expected_tip_record_hash: str,
) -> dict[str, Any]:
    records = [dict(row) for row in rows]
    if not records:
        raise PrecomputeParentError("precompute ledger is empty")
    tip = records[-1]
    if (
        tip.get("sequence") != expected_tip_sequence
        or tip.get("record_hash") != expected_tip_record_hash
    ):
        raise PrecomputeParentError("precompute ledger tip changed")
    matches = [
        row
        for row in records
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
    ]
    if len(matches) != 1:
        raise PrecomputeParentError("precompute registration is not unique")
    target = matches[0]
    if (
        target.get("event_type") != "registered"
        or target.get("sequence") != expected_tip_sequence
        or target.get("record_hash") != expected_tip_record_hash
    ):
        raise PrecomputeParentError("precompute registration is not the ledger tip")
    latest_by_experiment: dict[str, dict[str, Any]] = {}
    for row in records:
        item_experiment_id = row.get("experiment_id")
        if not isinstance(item_experiment_id, str) or not item_experiment_id:
            raise PrecomputeParentError("precompute ledger experiment id is invalid")
        latest_by_experiment[item_experiment_id] = row
    nonterminal = {
        item_experiment_id: row
        for item_experiment_id, row in latest_by_experiment.items()
        if row.get("event_type") not in _TERMINAL_EVENTS
    }
    if set(nonterminal) != {experiment_id} or nonterminal[experiment_id] != target:
        raise PrecomputeParentError("precompute ledger has another nonterminal experiment")
    return target


def _select_exact_quarantined_launchable_registration_v1(
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_tip_sequence: int,
    expected_tip_record_hash: str,
    quarantine_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Select one tip registration while preserving the only frozen open rows."""

    records = [dict(row) for row in rows]
    if not records:
        raise PrecomputeParentError("precompute ledger is empty")
    tip = records[-1]
    if (
        tip.get("sequence") != expected_tip_sequence
        or tip.get("record_hash") != expected_tip_record_hash
    ):
        raise PrecomputeParentError("precompute ledger tip changed")
    matches = [
        row
        for row in records
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
    ]
    if len(matches) != 1:
        raise PrecomputeParentError("precompute registration is not unique")
    target = matches[0]
    if (
        target.get("event_type") != "registered"
        or target.get("sequence") != expected_tip_sequence
        or target.get("record_hash") != expected_tip_record_hash
    ):
        raise PrecomputeParentError("precompute registration is not the ledger tip")
    latest_by_experiment: dict[str, dict[str, Any]] = {}
    for row in records:
        item_experiment_id = row.get("experiment_id")
        if not isinstance(item_experiment_id, str) or not item_experiment_id:
            raise PrecomputeParentError("precompute ledger experiment id is invalid")
        latest_by_experiment[item_experiment_id] = row
    open_rows = sorted(
        (
            row
            for row in latest_by_experiment.values()
            if row.get("event_type") not in _TERMINAL_EVENTS
        ),
        key=lambda row: row.get("sequence", -1),
    )
    try:
        binding = validate_frozen_quarantine_binding_v1(quarantine_binding)
        validate_exact_open_set_v1(open_rows, binding=binding, target=target)
    except (TypeError, ValueError) as exc:
        raise PrecomputeParentError(
            "precompute ledger quarantine open set is invalid"
        ) from exc
    return target


def _select_exact_orphaned_launch(
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_registration_sequence: int,
    expected_registration_record_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    records = [dict(row) for row in rows]
    registrations = [
        row
        for row in records
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    if len(registrations) != 1:
        return None
    registered = registrations[0]
    if (
        registered.get("sequence") != expected_registration_sequence
        or registered.get("record_hash") != expected_registration_record_hash
        or not records
    ):
        return None
    launch = records[-1]
    if (
        launch.get("event_type") != "precompute_launch_started"
        or launch.get("experiment_id") != experiment_id
        or launch.get("registered_record_hash") != registered_record_hash
        or launch.get("sequence") != expected_registration_sequence + 1
        or launch.get("previous_record_hash") != registered_record_hash
    ):
        return None
    latest_by_experiment: dict[str, dict[str, Any]] = {}
    for row in records:
        item_experiment_id = row.get("experiment_id")
        if not isinstance(item_experiment_id, str) or not item_experiment_id:
            raise PrecomputeParentError("precompute ledger experiment id is invalid")
        latest_by_experiment[item_experiment_id] = row
    nonterminal = {
        key: value
        for key, value in latest_by_experiment.items()
        if value.get("event_type") not in _TERMINAL_EVENTS
    }
    if set(nonterminal) != {experiment_id} or nonterminal[experiment_id] != launch:
        raise PrecomputeParentError("orphaned launch is not the sole nonterminal")
    return registered, launch


def _select_exact_quarantined_orphaned_launch_v1(
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
    expected_registration_sequence: int,
    expected_registration_record_hash: str,
    quarantine_binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    records = [dict(row) for row in rows]
    registrations = [
        row
        for row in records
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    if len(registrations) != 1:
        return None
    registered = registrations[0]
    if (
        registered.get("sequence") != expected_registration_sequence
        or registered.get("record_hash") != expected_registration_record_hash
        or not records
    ):
        return None
    launch = records[-1]
    if (
        launch.get("event_type") != "precompute_launch_started"
        or launch.get("experiment_id") != experiment_id
        or launch.get("registered_record_hash") != registered_record_hash
        or launch.get("sequence") != expected_registration_sequence + 1
        or launch.get("previous_record_hash") != registered_record_hash
    ):
        return None
    def latest_open_rows(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        latest_by_experiment: dict[str, dict[str, Any]] = {}
        for row in values:
            item_experiment_id = row.get("experiment_id")
            if not isinstance(item_experiment_id, str) or not item_experiment_id:
                raise PrecomputeParentError("precompute ledger experiment id is invalid")
            latest_by_experiment[item_experiment_id] = dict(row)
        return sorted(
            (
                row
                for row in latest_by_experiment.values()
                if row.get("event_type") not in _TERMINAL_EVENTS
            ),
            key=lambda row: row.get("sequence", -1),
        )

    try:
        binding = validate_frozen_quarantine_binding_v1(quarantine_binding)
        quarantine_sha256 = quarantine_binding_sha256_v1(binding)
        prelaunch_rows = [
            row for row in records if row.get("sequence", -1) <= registered["sequence"]
        ]
        validate_exact_open_set_v1(
            latest_open_rows(prelaunch_rows), binding=binding, target=registered
        )
    except (TypeError, ValueError) as exc:
        raise PrecomputeParentError(
            "orphaned launch quarantine prelaunch set is invalid"
        ) from exc

    open_rows = latest_open_rows(records)
    frozen_by_experiment = {
        row["experiment_id"]: row for row in binding["records"]
    }
    if (
        len(open_rows) != len(frozen_by_experiment) + 1
        or {row.get("experiment_id") for row in open_rows}
        != {*frozen_by_experiment, experiment_id}
        or launch.get("precompute_launch_started_schema_version")
        != "research-precompute-launch-started/v2"
        or launch.get("legacy_quarantine_sha256") != quarantine_sha256
    ):
        raise PrecomputeParentError("orphaned launch quarantine open set is invalid")
    for item_experiment_id, expected in frozen_by_experiment.items():
        actual = next(
            row for row in open_rows if row.get("experiment_id") == item_experiment_id
        )
        if {
            field: actual.get(field)
            for field in ("experiment_id", "sequence", "record_hash", "event_type")
        } != expected:
            raise PrecomputeParentError(
                "orphaned launch quarantine open set is invalid"
            )
    if open_rows[-1] != launch:
        raise PrecomputeParentError("orphaned launch quarantine open set is invalid")
    return registered, launch


def _registration_contract_schema(
    rows: Sequence[Mapping[str, Any]],
    *,
    experiment_id: str,
    registered_record_hash: str,
) -> tuple[str, Mapping[str, Any]]:
    matches = [
        dict(row)
        for row in rows
        if row.get("experiment_id") == experiment_id
        and row.get("record_hash") == registered_record_hash
        and row.get("event_type") == "registered"
    ]
    if len(matches) != 1:
        raise PrecomputeParentError("precompute registration is not unique")
    contract = matches[0].get("registration_contract")
    if not isinstance(contract, Mapping):
        raise PrecomputeParentError("precompute registration contract is invalid")
    schema = contract.get("schema_version")
    if schema not in {
        "research-validation-registration/v3",
        "research-validation-registration/v4",
    }:
        raise PrecomputeParentError("precompute registration contract is unsupported")
    return str(schema), contract


def _integer_token(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PrecomputeParentError(f"precompute plan {label} is not an integer")
    return str(value)


def _number_token(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrecomputeParentError(f"precompute plan {label} is not numeric")
    if not math.isfinite(float(value)):
        raise PrecomputeParentError(f"precompute plan {label} is not finite")
    return str(value)


def _text_token(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
        or value.startswith("-")
    ):
        raise PrecomputeParentError(f"precompute plan {label} is invalid")
    return value


def _temporal_contract_path(workspace: Path, expected_sha256: str) -> Path:
    path = workspace / "data" / "research_partitions" / "frozen-v1.json"
    try:
        resolved = path.resolve(strict=True)
        payload = load_temporal_partition_contract(resolved)
    except (OSError, UnicodeError, ValueError) as exc:
        raise PrecomputeParentError("precompute temporal contract is unavailable") from exc
    if (
        path != resolved
        or path.is_symlink()
        or not path.is_file()
        or not isinstance(payload, dict)
        or payload.get("contract_sha256") != expected_sha256
    ):
        raise PrecomputeParentError("precompute temporal contract binding mismatch")
    return resolved


def build_registered_child_args_v1(
    *,
    workspace_root: str | Path,
    registered_event: Mapping[str, Any],
    verified_control: Mapping[str, Any],
) -> list[str]:
    workspace = Path(workspace_root)
    resolved_workspace = workspace.resolve(strict=True)
    if workspace != resolved_workspace or not workspace.is_dir():
        raise PrecomputeParentError("precompute workspace is invalid")
    execution = research_precompute_control.precompute_execution_from_registration_v1(
        registered_event, verified_control
    )
    contract = registered_event.get("registration_contract")
    input_plan = contract.get("input_plan") if isinstance(contract, Mapping) else None
    plan = input_plan.get("payload") if isinstance(input_plan, Mapping) else None
    if (
        not isinstance(plan, Mapping)
        or plan.get("schema_version")
        not in {
            "research-treatment-input-plan/v4",
            "research-treatment-input-plan/v5",
            "research-treatment-input-plan/v6",
            "research-treatment-input-plan/v7",
        }
        or Path(execution["workspace_root"]) != resolved_workspace
    ):
        raise PrecomputeParentError("precompute parent requires an exact controlled plan")
    v5 = plan["schema_version"] == "research-treatment-input-plan/v5"
    v6 = plan["schema_version"] == "research-treatment-input-plan/v6"
    v7 = plan["schema_version"] == "research-treatment-input-plan/v7"
    if v5 and (
        execution.get("schema_version") != "research-precompute-execution-plan/v2"
        or execution.get("launcher_ready_schema") != "research-launcher-ready/v4"
    ):
        raise PrecomputeParentError("precompute parent requires the v5 launch protocol")
    if v6 and (
        execution.get("schema_version") != "research-precompute-execution-plan/v3"
        or execution.get("launcher_ready_schema") != "research-launcher-ready/v5"
    ):
        raise PrecomputeParentError("precompute parent requires the v6 launch protocol")
    if v7 and (
        execution.get("schema_version") != "research-precompute-execution-plan/v4"
        or execution.get("launcher_ready_schema") != "research-launcher-ready/v5"
    ):
        raise PrecomputeParentError("precompute parent requires the v7 launch protocol")
    parameters = plan["parameters"]
    if any(
        parameters.get(key) is not expected
        for key, expected in (
            ("live_snapshot", False),
            ("announcement_context", False),
            ("margin_eligibility_context", False),
        )
    ) or any(
        parameters.get(key) is not None
        for key in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct")
    ):
        raise PrecomputeParentError("precompute plan violates offline treatment defaults")
    authority = plan["authority"]
    fixture = plan["development_payload_fixture"]
    temporal_path = _temporal_contract_path(
        resolved_workspace, authority["temporal_contract_sha256"]
    )
    if (v5 or v6 or v7) and Path(execution["temporal_contract_path"]) != temporal_path:
        raise PrecomputeParentError("precompute temporal contract path mismatch")
    tokens = [
        "-m",
        "app.jobs",
        "research-historical-universe",
        "--start-date",
        str(plan["start_date"]),
        "--end-date",
        str(plan["end_date"]),
    ]
    for key, option in _INTEGER_OPTIONS:
        tokens.extend((option, _integer_token(parameters.get(key), key)))
    tokens.extend(
        (
            "--composite-pit-descriptor-path",
            str(fixture["source_descriptor_path"]),
            "--expected-composite-root-sha256",
            str(authority["composite_root_sha256"]),
            "--temporal-contract-path",
            str(temporal_path),
            "--expected-temporal-contract-sha256",
            str(authority["temporal_contract_sha256"]),
            "--expected-temporal-role",
            str(plan["temporal_role"]),
            "--cache-dir",
            str(execution["cache_dir"]),
            "--progress-every",
            _integer_token(
                execution.get("progress_every", 25), "progress_every"
            ),
        )
    )
    if parameters.get("buy_only") is True:
        tokens.append("--buy-only")
    elif parameters.get("buy_only") is not False:
        raise PrecomputeParentError("precompute plan buy_only is invalid")
    for key, option in _OPTIONAL_NUMBER_OPTIONS:
        value = parameters.get(key)
        if value is not None:
            tokens.extend((option, _number_token(value, key)))
    for key, option in _OPTIONAL_INTEGER_OPTIONS:
        value = parameters.get(key)
        if value is not None:
            tokens.extend((option, _integer_token(value, key)))
    for key, option in _OPTIONAL_TEXT_OPTIONS:
        value = parameters.get(key)
        if value is not None:
            tokens.extend((option, _text_token(value, key)))
    for key, option in (
        ("require_all_announcement_events", "--require-all-announcement-events"),
        ("require_all_signal_tags", "--require-all-signal-tags"),
    ):
        value = parameters.get(key)
        if value is True:
            tokens.append(option)
        elif value is not False:
            raise PrecomputeParentError(f"precompute plan {key} is invalid")
    tokens.extend(
        (
            "--symbol-cooldown-days",
            _integer_token(parameters.get("symbol_cooldown_days"), "symbol_cooldown_days"),
            "--max-active-positions",
            _integer_token(parameters.get("max_active_positions"), "max_active_positions"),
            "--qualified-trades-output",
            str(execution["qualified_trades_output_path"]),
        )
    )
    return tokens


_PARENT_CONTROL_OPTIONS = {
    "--ledger-path",
    "--experiment-id",
    "--registered-record-hash",
    "--expected-current-tip-sequence",
    "--expected-current-tip-record-hash",
}


def _reject_duplicate_parent_options(argv: Sequence[str]) -> None:
    counts = {option: 0 for option in _PARENT_CONTROL_OPTIONS}
    for token in argv:
        option = str(token).split("=", 1)[0]
        if option in counts:
            counts[option] += 1
    if any(count > 1 for count in counts.values()):
        raise PrecomputeParentError("precompute parent control option is duplicated")


def _exact_directory(path: Path, *, workspace: Path) -> Path:
    if (
        not path.is_absolute()
        or path.drive.casefold() != "e:"
        or path.resolve(strict=True) != path
        or path.is_symlink()
        or not path.is_dir()
    ):
        raise PrecomputeParentError("precompute parent directory is unsafe")
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise PrecomputeParentError("precompute parent directory is outside workspace") from exc
    return path


def _validate_parent_environment(workspace: Path) -> None:
    for key in (
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "XDG_CACHE_HOME",
        "PYTHONPYCACHEPREFIX",
    ):
        value = os.environ.get(key)
        if not value:
            raise PrecomputeParentError(f"precompute parent {key} is missing")
        path = Path(value)
        if (
            not path.is_absolute()
            or path.drive.casefold() != "e:"
            or path.resolve(strict=True) != path
            or not path.is_dir()
            or path.is_symlink()
            or int(getattr(path.lstat(), "st_file_attributes", 0))
            & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        ):
            raise PrecomputeParentError(f"precompute parent {key} is outside E")
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise PrecomputeParentError(
                f"precompute parent {key} is outside workspace"
            ) from exc
    if os.environ.get("PYTHONNOUSERSITE") != "1" or os.environ.get(
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
    ) != "1":
        raise PrecomputeParentError("precompute parent isolation flags are missing")


def _require_existing_ledger_lock(ledger: Path, *, workspace: Path) -> Path:
    lock_path = ledger.with_name(ledger.name + ".lock")
    try:
        metadata = lock_path.lstat()
        resolved = lock_path.resolve(strict=True)
    except OSError as exc:
        raise PrecomputeParentError("precompute ledger lock is unavailable") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        resolved != lock_path
        or lock_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_nlink != 1
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise PrecomputeParentError("precompute ledger lock is unsafe")
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise PrecomputeParentError("precompute ledger lock is outside workspace") from exc
    return resolved


def _prepare_v5_run_skeleton(
    *, workspace: Path, execution: Mapping[str, Any], control: Mapping[str, Any]
) -> None:
    run_root = _exact_directory(Path(execution["run_root"]), workspace=workspace)
    control_root = _exact_directory(run_root / "control", workspace=workspace)
    proof_path = Path(control["parent_proof_path"])
    if proof_path.parent != control_root or not proof_path.is_file():
        raise PrecomputeParentError("precompute parent proof is outside the control root")
    if {item.name for item in run_root.iterdir()} != {"control"}:
        raise PrecomputeParentError("precompute run root is not a fresh proof-only skeleton")
    if {item.name for item in control_root.iterdir()} != {proof_path.name}:
        raise PrecomputeParentError("precompute control root is not proof-only")
    paths = {
        "output": Path(execution["qualified_trades_output_path"]),
        "cache": Path(execution["cache_dir"]),
        "claims": Path(execution["claim_parent"]),
        "sandbox": Path(execution["sandbox_root"]),
        "audit": Path(execution["audit_dir"]),
        "result": Path(execution["run_result_receipt_path"]),
    }
    if (
        paths["output"].parent != run_root
        or paths["cache"].parent != run_root
        or paths["claims"].parent != control_root
        or paths["sandbox"].parent != control_root
        or paths["audit"].parent != paths["sandbox"]
        or paths["result"].parent != control_root
        or any(path.exists() for path in paths.values())
    ):
        raise PrecomputeParentError("precompute run skeleton is dirty or aliased")


def _verified_parent_bindings(
    registered: Mapping[str, Any], *, workspace: Path, ledger_path: Path
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    contract = registered.get("registration_contract")
    control = contract.get("precompute_control") if isinstance(contract, Mapping) else None
    if not isinstance(control, Mapping):
        raise PrecomputeParentError("precompute parent control binding is missing")
    execution = research_precompute_control.precompute_execution_from_registration_v1(
        registered, control
    )
    registration_schema = contract.get("schema_version") if isinstance(contract, Mapping) else None
    input_plan = contract.get("input_plan") if isinstance(contract, Mapping) else None
    plan = input_plan.get("payload") if isinstance(input_plan, Mapping) else None
    expected_execution_schema = {
        "research-validation-registration/v3": "research-precompute-execution-plan/v2",
        "research-validation-registration/v4": {
            "research-treatment-input-plan/v6": "research-precompute-execution-plan/v3",
            "research-treatment-input-plan/v7": "research-precompute-execution-plan/v4",
        }.get(plan.get("schema_version") if isinstance(plan, Mapping) else None),
    }.get(registration_schema)
    if (
        Path(execution["ledger_path"]) != ledger_path
        or execution.get("schema_version") != expected_execution_schema
    ):
        raise PrecomputeParentError("precompute parent execution binding mismatch")
    registration_plan_payload = (
        contract.get("input_plan", {}).get("payload")
        if isinstance(contract, Mapping)
        else None
    )
    if registration_schema == "research-validation-registration/v4":
        quarantine_sha256 = contract.get("legacy_quarantine_sha256")
        if (
            not isinstance(quarantine_sha256, str)
            or len(quarantine_sha256) != 64
            or execution.get("legacy_quarantine_sha256") != quarantine_sha256
        ):
            raise PrecomputeParentError("precompute parent quarantine binding mismatch")
    research_precompute_control.verify_precompute_parent_proof_v1(
        control["parent_proof_path"],
        workspace_root=workspace,
        expected_file_sha256=control["parent_proof_file_sha256"],
        expected_canonical_sha256=control["parent_proof_canonical_sha256"],
        expected_verified_control=control,
    )
    if (
        registration_schema == "research-validation-registration/v4"
        and isinstance(registration_plan_payload, Mapping)
        and registration_plan_payload.get("schema_version")
        == "research-treatment-input-plan/v7"
    ):
        plan_path = Path(control["publication_root"]) / "treatment-plan.json"
        try:
            published_plan, published_artifact = jobs._load_validation_input_plan(
                str(plan_path)
            )
        except (OSError, TypeError, ValueError) as exc:
            raise PrecomputeParentError(
                "precompute parent published plan binding is invalid"
            ) from exc
        registration_plan = registration_plan_payload
        registration_artifact = contract.get("input_plan", {}).get("artifact")
        if (
            published_plan != registration_plan
            or published_artifact != registration_artifact
            or published_plan.get("schema_version")
            == "research-treatment-input-plan/v7"
            and (
                published_plan["control_request_binding"]["control_source_bundle_sha256"]
                != control["control_source_bundle_sha256"]
                or published_plan["control_request_binding"]["ledger"]["path"]
                != contract.get("precompute_ledger", {}).get("ledger_path")
                or published_plan["control_request_binding"]["ledger"][
                    "expected_tip_sequence"
                ]
                != contract.get("precompute_ledger", {}).get("expected_tip_sequence")
                or published_plan["control_request_binding"]["ledger"][
                    "expected_tip_record_hash"
                ]
                != contract.get("precompute_ledger", {}).get("expected_tip_record_hash")
                or published_plan["control_request_binding"]["ledger"][
                    "expected_lock_file_sha256"
                ]
                != contract.get("precompute_ledger", {}).get("lock_file_sha256")
                or published_plan["control_request_binding"]["ledger"][
                    "expected_file_sha256"
                ]
                != contract.get("precompute_ledger", {}).get(
                    "pre_registration_ledger_file_sha256"
                )
                or execution.get("control_request_binding_sha256")
                != jobs._canonical_payload_sha256(
                    published_plan["control_request_binding"]
                )
            )
        ):
            raise PrecomputeParentError("precompute parent control request binding mismatch")
    return control, execution


def main(argv: Sequence[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    _reject_duplicate_parent_options(tokens)
    args = _parser().parse_args(tokens)
    workspace = Path(__file__).resolve().parent.parent
    if workspace.drive.casefold() != "e:" or Path.cwd().resolve() != workspace:
        raise PrecomputeParentError("precompute parent must run from the E workspace")
    _validate_parent_environment(workspace)
    python_executable = Path(sys.executable).resolve(strict=True)
    try:
        python_executable.relative_to(workspace / ".venv")
    except ValueError as exc:
        raise PrecomputeParentError("precompute parent Python is outside the E venv") from exc
    ledger_path = Path(args.ledger_path)
    if (
        not ledger_path.is_absolute()
        or ledger_path.drive.casefold() != "e:"
        or ledger_path.resolve(strict=True) != ledger_path
        or ledger_path.is_symlink()
        or not ledger_path.is_file()
    ):
        raise PrecomputeParentError("precompute ledger path is unsafe")
    _require_existing_ledger_lock(ledger_path, workspace=workspace)
    with _exclusive_parent_guard(args.registered_record_hash):
        rows = research_validation.read_experiment_ledger(str(ledger_path))
        registration_schema, registration_contract = _registration_contract_schema(
            rows,
            experiment_id=args.experiment_id,
            registered_record_hash=args.registered_record_hash,
        )
        v4_registration = registration_schema == "research-validation-registration/v4"
        quarantine_binding = (
            registration_contract.get("legacy_quarantine") if v4_registration else None
        )
        if v4_registration and not isinstance(quarantine_binding, Mapping):
            raise PrecomputeParentError("precompute parent quarantine binding is missing")
        orphaned = (
            _select_exact_quarantined_orphaned_launch_v1(
                rows,
                experiment_id=args.experiment_id,
                registered_record_hash=args.registered_record_hash,
                expected_registration_sequence=args.expected_current_tip_sequence,
                expected_registration_record_hash=args.expected_current_tip_record_hash,
                quarantine_binding=quarantine_binding,
            )
            if v4_registration
            else _select_exact_orphaned_launch(
                rows,
                experiment_id=args.experiment_id,
                registered_record_hash=args.registered_record_hash,
                expected_registration_sequence=args.expected_current_tip_sequence,
                expected_registration_record_hash=args.expected_current_tip_record_hash,
            )
        )
        if orphaned is not None:
            registered, launch = orphaned
            control, orphan_execution = _verified_parent_bindings(
                registered, workspace=workspace, ledger_path=ledger_path
            )
            expected_claim = (
                research_precompute_control.expected_precompute_run_claim_identity_v2(
                    orphan_execution["claim_parent"],
                    workspace_root=workspace,
                    registered_event=registered,
                    verified_control=control,
                )
            )
            expected_lease = research_precompute_control.expected_precompute_launch_lease_identity_v2(
                orphan_execution["claim_parent"],
                workspace_root=workspace,
                ledger_path=ledger_path,
                registered_event=registered,
                verified_control=control,
                expected_run_claim_file_sha256=expected_claim["sha256"],
            )
            if (
                launch.get("run_claim_file_sha256") != expected_claim["sha256"]
                or launch.get("launch_lease_file_sha256") != expected_lease["sha256"]
            ):
                raise PrecomputeParentError("orphaned launch identity mismatch")
            exact_state = research_precompute_control._read_exact_v2_launch_state(
                ledger_path,
                registered_event=registered,
                run_claim_file_sha256=expected_claim["sha256"],
                launch_lease_file_sha256=expected_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
                legacy_quarantine_sha256=(
                    registration_contract["legacy_quarantine_sha256"]
                    if v4_registration
                    else None
                ),
            )
            if exact_state != launch:
                raise PrecomputeParentError("orphaned launch state changed")
            failed = research_validation.fail_authorized_precompute_launch_if_current(
                str(ledger_path),
                experiment_id=registered["experiment_id"],
                registered_record_hash=registered["record_hash"],
                launch_started_record_hash=launch["record_hash"],
                run_claim_file_sha256=expected_claim["sha256"],
                launch_lease_file_sha256=expected_lease["sha256"],
                parent_proof_file_sha256=control["parent_proof_file_sha256"],
                parent_proof_canonical_sha256=control[
                    "parent_proof_canonical_sha256"
                ],
                failure_code="ORPHANED_PRECOMPUTE_LAUNCH",
                failure_phase="precompute_restart_reconcile",
                error_type="ParentProcessTerminated",
            )
            print(json.dumps(failed, ensure_ascii=False, sort_keys=True, default=str))
            return 2
        registered = (
            _select_exact_quarantined_launchable_registration_v1(
                rows,
                experiment_id=args.experiment_id,
                registered_record_hash=args.registered_record_hash,
                expected_tip_sequence=args.expected_current_tip_sequence,
                expected_tip_record_hash=args.expected_current_tip_record_hash,
                quarantine_binding=quarantine_binding,
            )
            if v4_registration
            else _select_exact_launchable_registration(
                rows,
                experiment_id=args.experiment_id,
                registered_record_hash=args.registered_record_hash,
                expected_tip_sequence=args.expected_current_tip_sequence,
                expected_tip_record_hash=args.expected_current_tip_record_hash,
            )
        )
        control, execution = _verified_parent_bindings(
            registered, workspace=workspace, ledger_path=ledger_path
        )
        child_args = build_registered_child_args_v1(
            workspace_root=workspace,
            registered_event=registered,
            verified_control=control,
        )
        _prepare_v5_run_skeleton(
            workspace=workspace, execution=execution, control=control
        )
        run_supervised = (
            research_precompute_control.run_registered_precompute_supervised_v3
            if v4_registration
            else research_precompute_control.run_registered_precompute_supervised_v2
        )
        result = run_supervised(
            workspace_root=workspace,
            python_executable=python_executable,
            sandbox_root=execution["sandbox_root"],
            audit_dir=execution["audit_dir"],
            claim_parent=execution["claim_parent"],
            ledger_path=ledger_path,
            registered_event=registered,
            verified_control=control,
            child_args=child_args,
            environment=os.environ,
            ready_timeout_seconds=30.0,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
