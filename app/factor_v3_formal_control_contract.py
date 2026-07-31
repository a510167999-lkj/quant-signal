"""Shared canonical contract for the Factor V3 formal control plane."""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence


class FormalControlContractError(ValueError):
    pass


WORKER_PROTOCOL = "factor-v3-formal-supervisor-worker/v2"
WORKER_TERMINAL_SCHEMA = "factor-v3-formal-bootstrap-worker-terminal/v2"
PUBLICATION_COMPLETION_SCHEMA = "factor-v3-formal-bootstrap-publication-completion/v2"
STDLIB_POLICY_SCHEMA = "factor-v3-formal-stdlib-policy/v2"
EXECUTION_REPLAY_SCOPE = "factor-v3-formal-bootstrap-execution/v1"
STDLIB_ROOT_ENVIRONMENT = "FACTOR_V3_FORMAL_STDLIB_INVENTORY_ROOT_SHA256"
PREFLIGHT_TERMINAL_GUARD_BINDING_ENVIRONMENT = "FACTOR_V3_FORMAL_PREFLIGHT_TERMINAL_GUARD_BINDING"
PREFLIGHT_TERMINAL_GUARD_SCHEMA = "factor-v3-formal-preflight-terminal-guard/v1"
PREFLIGHT_TERMINAL_GUARD_PROVIDER_IDENTITY = "external-win32-native-supervisor/v1"
PREFLIGHT_TERMINAL_GUARD_ACTIONS = ("build-spec", "preflight")
WORKER_ACTION_BY_LAUNCH_ACTION = {
    "build-spec": "build-spec",
    "preflight": "preflight",
    "resume": "run",
    "run": "run",
    "verify": "verify",
}
ACTION_SECRET_ENVIRONMENT = {
    "build-spec": [],
    "preflight": [],
    "resume": ["JIAOCH_TOKEN"],
    "run": ["JIAOCH_TOKEN"],
    "verify": [],
}
PUBLIC_ENVIRONMENT = ["SYSTEMROOT", "TEMP", "TMP", "WINDIR"]
FIXED_ENVIRONMENT = [
    "FACTOR_V3_FORMAL_LAUNCH_ACTION",
    "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256",
    "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL",
    PREFLIGHT_TERMINAL_GUARD_BINDING_ENVIRONMENT,
    STDLIB_ROOT_ENVIRONMENT,
]
WORKER_TERMINAL_FIELDS = [
    "artifacts",
    "authorization_nonce_sha256",
    "bootstrap_execution_authorization_sha256",
    "launch_action",
    "launch_authorization_sha256",
    "result",
    "schema",
    "status",
    "stdlib_inventory_root_sha256",
    "worker_action",
]

_POLICY_FIELDS = {
    "absent_paths",
    "entries",
    "pycache_prefix",
    "roots",
    "schema",
}
_ROOT_FIELDS = {"path", "role"}
_ENTRY_FIELDS = {
    "bytes",
    "is_package",
    "kind",
    "module",
    "path",
    "relative_path",
    "root",
    "sha256",
}
_ROOT_ROLES = {"platstdlib", "stdlib"}
_ENTRY_KINDS = {"dll", "extension", "source"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FormalControlContractError("formal control value is not canonical JSON") from exc


def sha256_bytes(raw: bytes) -> str:
    if type(raw) is not bytes:
        raise FormalControlContractError("formal control bytes rejected")
    return hashlib.sha256(raw).hexdigest()


def worker_environment_policy() -> dict[str, Any]:
    return {
        "inherit_parent": False,
        "marker_names": list(FIXED_ENVIRONMENT),
        "public_passthrough_names": list(PUBLIC_ENVIRONMENT),
        "required_secret_names_by_action": {
            action: list(names) for action, names in ACTION_SECRET_ENVIRONMENT.items()
        },
    }


def worker_protocol_descriptor() -> dict[str, Any]:
    return {
        "action_secret_environment": {
            action: list(names) for action, names in ACTION_SECRET_ENVIRONMENT.items()
        },
        "fixed_environment": list(FIXED_ENVIRONMENT),
        "protocol": WORKER_PROTOCOL,
        "public_environment": list(PUBLIC_ENVIRONMENT),
        "terminal_fields": list(WORKER_TERMINAL_FIELDS),
        "terminal_schema": WORKER_TERMINAL_SCHEMA,
    }


def preflight_terminal_guard_contract() -> dict[str, Any]:
    return {
        "acquire_before": "planned-run-root-initial-snapshot",
        "atomic_terminal_operation": ("planned-run-root-postverify-and-success-buffer"),
        "hold_until": "supervisor-terminal-output-flush",
        "protected_actions": list(PREFLIGHT_TERMINAL_GUARD_ACTIONS),
        "protected_path_field": "run_root",
        "provider_identity": PREFLIGHT_TERMINAL_GUARD_PROVIDER_IDENTITY,
        "schema": PREFLIGHT_TERMINAL_GUARD_SCHEMA,
        "worker_binding_environment": (PREFLIGHT_TERMINAL_GUARD_BINDING_ENVIRONMENT),
        "write_policy": "deny-create-delete-rename-replace",
    }


def preflight_terminal_guard_request(
    *,
    action: str,
    run_root: str,
) -> dict[str, Any] | None:
    if action not in WORKER_ACTION_BY_LAUNCH_ACTION.values():
        raise FormalControlContractError("preflight terminal guard action rejected")
    if action not in PREFLIGHT_TERMINAL_GUARD_ACTIONS:
        return None
    protected_root = Path(_absolute_path(run_root))
    return {
        **preflight_terminal_guard_contract(),
        "action": action,
        "parent_path": str(protected_root.parent),
        "run_root": str(protected_root),
    }


def control_contract_descriptor() -> dict[str, Any]:
    return {
        "completion_schema": PUBLICATION_COMPLETION_SCHEMA,
        "environment_policy": worker_environment_policy(),
        "execution_replay_scope": EXECUTION_REPLAY_SCOPE,
        "preflight_terminal_guard": preflight_terminal_guard_contract(),
        "stdlib_policy_schema": STDLIB_POLICY_SCHEMA,
        "stdlib_root_environment": STDLIB_ROOT_ENVIRONMENT,
        "worker_action_by_launch_action": dict(WORKER_ACTION_BY_LAUNCH_ACTION),
        "worker_protocol": worker_protocol_descriptor(),
    }


def control_contract_descriptor_sha256() -> str:
    return sha256_bytes(canonical_bytes(control_contract_descriptor()))


def exact_worker_argv(
    *,
    python_executable_path: str,
    bootstrap_worker_path: str,
    pycache_prefix: str,
) -> list[str]:
    for value in (
        python_executable_path,
        bootstrap_worker_path,
        pycache_prefix,
    ):
        _absolute_path(value)
    return [
        python_executable_path,
        "-I",
        "-B",
        "-S",
        "-X",
        f"pycache_prefix={pycache_prefix}",
        bootstrap_worker_path,
    ]


def _absolute_path(value: Any) -> str:
    if type(value) is not str or not value:
        raise FormalControlContractError("formal control absolute path rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalControlContractError("formal control absolute path rejected")
    return str(path)


def _path_key(value: str) -> tuple[str, str]:
    return os.path.normcase(value), value


def _normalized_root(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != _ROOT_FIELDS:
        raise FormalControlContractError("stdlib root rejected")
    path = _absolute_path(value.get("path"))
    role = value.get("role")
    if role not in _ROOT_ROLES:
        raise FormalControlContractError("stdlib root role rejected")
    return {"path": path, "role": str(role)}


def _normalized_entry(value: Any, *, roots: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _ENTRY_FIELDS:
        raise FormalControlContractError("stdlib entry rejected")
    byte_count = value.get("bytes")
    is_package = value.get("is_package")
    kind = value.get("kind")
    module = value.get("module")
    root = _absolute_path(value.get("root"))
    path = _absolute_path(value.get("path"))
    relative_path = value.get("relative_path")
    digest = value.get("sha256")
    if (
        root not in roots
        or type(byte_count) is not int
        or isinstance(byte_count, bool)
        or byte_count < 0
        or type(is_package) is not bool
        or kind not in _ENTRY_KINDS
        or (kind in {"extension", "dll"} and byte_count == 0)
        or type(relative_path) is not str
        or not relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        or type(digest) is not str
        or _SHA256_RE.fullmatch(digest) is None
    ):
        raise FormalControlContractError("stdlib entry rejected")
    expected_path = Path(root) / Path(*relative_path.split("/"))
    if os.path.normcase(str(expected_path)) != os.path.normcase(path):
        raise FormalControlContractError("stdlib entry topology rejected")
    if kind == "dll":
        if module is not None or is_package:
            raise FormalControlContractError("stdlib DLL entry rejected")
    elif type(module) is not str or not module:
        raise FormalControlContractError("stdlib module entry rejected")
    return {
        "bytes": byte_count,
        "is_package": is_package,
        "kind": str(kind),
        "module": module,
        "path": path,
        "relative_path": relative_path,
        "root": root,
        "sha256": digest,
    }


def canonical_stdlib_policy(
    *,
    roots: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    absent_paths: Sequence[str],
    pycache_prefix: str,
) -> dict[str, Any]:
    if (
        isinstance(roots, (str, bytes))
        or isinstance(entries, (str, bytes))
        or isinstance(absent_paths, (str, bytes))
    ):
        raise FormalControlContractError("stdlib policy collection rejected")
    normalized_roots = [_normalized_root(dict(root)) for root in roots]
    normalized_roots.sort(key=lambda item: _path_key(item["path"]))
    root_paths = [item["path"] for item in normalized_roots]
    if (
        not normalized_roots
        or len(set(map(os.path.normcase, root_paths))) != len(root_paths)
        or len({item["role"] for item in normalized_roots}) != len(normalized_roots)
    ):
        raise FormalControlContractError("stdlib roots rejected")
    normalized_entries = [
        _normalized_entry(dict(entry), roots=set(root_paths)) for entry in entries
    ]
    normalized_entries.sort(key=lambda item: _path_key(item["path"]))
    entry_paths = [item["path"] for item in normalized_entries]
    modules = [str(item["module"]) for item in normalized_entries if item["module"] is not None]
    if (
        not normalized_entries
        or len(set(map(os.path.normcase, entry_paths))) != len(entry_paths)
        or len(modules) != len(set(modules))
    ):
        raise FormalControlContractError("stdlib entries rejected")
    normalized_absent = sorted(
        (_absolute_path(value) for value in absent_paths),
        key=_path_key,
    )
    if len(set(map(os.path.normcase, normalized_absent))) != len(normalized_absent) or set(
        map(os.path.normcase, normalized_absent)
    ) & set(map(os.path.normcase, entry_paths)):
        raise FormalControlContractError("stdlib absent paths rejected")
    return {
        "absent_paths": normalized_absent,
        "entries": normalized_entries,
        "pycache_prefix": _absolute_path(pycache_prefix),
        "roots": normalized_roots,
        "schema": STDLIB_POLICY_SCHEMA,
    }


def stdlib_policy_root_sha256(value: Mapping[str, Any]) -> str:
    policy = validate_stdlib_policy(value)
    return sha256_bytes(canonical_bytes(policy))


def validate_stdlib_policy(
    value: Mapping[str, Any],
    *,
    expected_root_sha256: str | None = None,
    require_filesystem: bool = False,
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != _POLICY_FIELDS
        or value.get("schema") != STDLIB_POLICY_SCHEMA
        or type(value.get("roots")) is not list
        or type(value.get("entries")) is not list
        or type(value.get("absent_paths")) is not list
    ):
        raise FormalControlContractError("stdlib policy rejected")
    normalized = canonical_stdlib_policy(
        roots=value["roots"],
        entries=value["entries"],
        absent_paths=value["absent_paths"],
        pycache_prefix=value.get("pycache_prefix"),
    )
    if normalized != value:
        raise FormalControlContractError("stdlib policy is not canonical")
    digest = sha256_bytes(canonical_bytes(normalized))
    if expected_root_sha256 is not None and (
        type(expected_root_sha256) is not str
        or _SHA256_RE.fullmatch(expected_root_sha256) is None
        or digest != expected_root_sha256
    ):
        raise FormalControlContractError("stdlib policy root rejected")
    if require_filesystem:
        expected_entry_paths = {
            os.path.normcase(str(item["path"])) for item in normalized["entries"]
        }
        observed_entry_paths: set[str] = set()
        for root in normalized["roots"]:
            path = Path(root["path"])
            if not path.is_dir() or path.is_symlink():
                raise FormalControlContractError("stdlib root filesystem rejected")
            for directory, names, filenames in os.walk(path, topdown=True):
                current = Path(directory)
                names[:] = sorted(
                    name for name in names if name not in {"__pycache__", "site-packages"}
                )
                for filename in filenames:
                    candidate = current / filename
                    if filename.endswith(
                        (
                            ".py",
                            *importlib.machinery.EXTENSION_SUFFIXES,
                            ".dll",
                        )
                    ):
                        observed_entry_paths.add(os.path.normcase(str(candidate)))
        if observed_entry_paths != expected_entry_paths:
            raise FormalControlContractError("stdlib filesystem inventory rejected")
        for entry in normalized["entries"]:
            path = Path(entry["path"])
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise FormalControlContractError("stdlib entry filesystem rejected") from exc
            if (
                path.is_symlink()
                or len(raw) != entry["bytes"]
                or sha256_bytes(raw) != entry["sha256"]
            ):
                raise FormalControlContractError("stdlib entry filesystem rejected")
        for value_path in normalized["absent_paths"]:
            if Path(value_path).exists():
                raise FormalControlContractError("stdlib absent path exists")
        pycache = Path(normalized["pycache_prefix"])
        try:
            pycache_metadata = pycache.lstat()
        except OSError:
            raise FormalControlContractError("stdlib pycache blocker rejected") from None
        if (
            not stat.S_ISREG(pycache_metadata.st_mode)
            or pycache.is_symlink()
            or pycache_metadata.st_size <= 0
            or int(getattr(pycache_metadata, "st_nlink", 1)) != 1
        ):
            raise FormalControlContractError("stdlib pycache blocker rejected")
    return normalized
