"""Frozen, offline factor-v3 feature-history collection authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import hashlib
import hmac
import importlib
import inspect
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import types
from typing import Any
import uuid

from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    canonical_sha256,
)
from app.durable_io import fsync_directory
from app import jiaoch_points_raw_authority as raw_authority
from app.research_partitions import (
    PartitionContractError,
    assert_range_allowed,
    classify_date,
)
from app.research_security_code_transition import (
    SECURITY_CODE_TRANSITION_CONTRACT_SHA256,
)


__all__ = (
    "build_factor_v3_feature_history_collection_plan",
    "verify_factor_v3_feature_history_collection_authority",
    "verify_factor_v3_feature_history_collection_plan",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TRADE_CAL_MANIFEST_PATH_RE = re.compile(
    r"^trade_cal_manifest_candidates/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json$"
)
_COLLECTION_MANIFEST_PATH_RE = re.compile(
    r"^feature_history_collection_manifest_candidates/sha256/"
    r"([0-9a-f]{2})/([0-9a-f]{64})\.json$"
)
_COLLECTION_ISSUANCE_PATH_RE = re.compile(
    r"^feature_history_collection_publication_receipts/sha256/"
    r"([0-9a-f]{2})/([0-9a-f]{64})\.json$"
)
_COLLECTION_SNAPSHOT_INDEX_PATH_RE = re.compile(
    r"^feature_history_collection_snapshots/sha256/"
    r"([0-9a-f]{2})/([0-9a-f]{64})/snapshot-index\.json$"
)
_LOOPBACK_PROXY_RE = re.compile(
    r"^http://(?P<host>localhost|\[[0-9A-Fa-f:]+\]|[0-9.]+):"
    r"(?P<port>[1-9][0-9]{0,4})$"
)
_WIRE_DATE_RE = re.compile(r"^[0-9]{8}$")
_MAX_TRADE_CAL_MANIFEST_BYTES = 256 * 1024
_MAX_COLLECTION_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_COLLECTION_ISSUANCE_BYTES = 16 * 1024
_MAX_COLLECTION_SNAPSHOT_INDEX_BYTES = 2 * 1024 * 1024
_FROZEN_PARTITION_V1_SHA256 = "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227"
_FROZEN_DEVELOPMENT_SESSIONS_SHA256 = (
    "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
)
_FROZEN_DEVELOPMENT_START = "2024-07-05"
_FROZEN_DEVELOPMENT_END = "2026-07-03"
_FROZEN_DEVELOPMENT_SESSION_COUNT = 483
_REQUIRED_PRIOR_OPEN_SESSIONS = 250
_REQUIRED_MARKET_SHARDS = ("daily", "adj_factor", "stk_limit", "suspend_d")
_REQUIRED_FEATURE_HISTORY_APIS = ("bak_basic", *_REQUIRED_MARKET_SHARDS)
_NONEMPTY_MARKET_SHARDS = frozenset({"daily", "adj_factor", "stk_limit"})
_BOARD_COUNT_FIELDS = frozenset({"beijing", "chinext", "mainboard", "science_technology"})
_FROZEN_JIAOCH_SOURCE_AUTHORITY = {
    "base_origin": "https://jiaoch.site:443",
    "request_protocol": "tushare-path-per-interface/v1",
    "source_profile": "jiaoch",
}
_FROZEN_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT = {
    "routes": [
        {
            "api_name": api_name,
            "credential_slot_id": "points-primary",
            "purpose": "factor-v3-feature-history",
            "route_id": f"feature-history:points-primary:{api_name}",
        }
        for api_name in _REQUIRED_FEATURE_HISTORY_APIS
    ],
    "schema": "jiaoch-credential-feature-history-routing-policy/v1",
}
_FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256 = canonical_sha256(
    _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT
)
_TRADE_CAL_PUBLICATION_FIELDS = frozenset(
    {
        "authority_manifest_created",
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability",
        "publication_status",
        "schema",
    }
)
_COLLECTION_PUBLICATION_FIELDS = frozenset(
    {
        "authority_manifest_created",
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability",
        "publication_status",
        "schema",
    }
)
_COLLECTION_MANIFEST_FIELDS = frozenset(
    {
        "authority_status",
        "collection_plan_sha256",
        "development_session_refs_sha256",
        "embargo_consumed",
        "exact_nonempty_bak_basic_session_count",
        "experiment_launch_eligible",
        "factor_materialization_eligible",
        "factor_v3_feature_history_authority_contract_sha256",
        "factor_v3_points_contract_sha256",
        "feature_history_only",
        "feature_history_route_policy_descriptor",
        "final_oos_consumed",
        "pit_store_database_bytes",
        "pit_store_database_sha256",
        "pit_store_raw_artifact_set_sha256",
        "pit_store_raw_artifact_count",
        "pit_store_receipt_manifest_sha256",
        "producer_binding",
        "production_profile_registered",
        "production_recommendation_eligible",
        "publication_capability_sha256",
        "schema",
        "security_code_transition_contract_sha256",
        "session_authority_refs",
        "session_authority_refs_sha256",
        "session_count",
        "sessions_sha256",
        "snapshot_index_relative_path",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
        "suspend_d_authority_session_count",
        "upstream_beijing_preserved_session_count",
        "upstream_scope_root_sha256",
        "upstream_star_preserved_session_count",
    }
)
_COLLECTION_SNAPSHOT_INDEX_FIELDS = frozenset(
    {
        "database",
        "raw_artifact_count",
        "raw_artifact_set_sha256",
        "raw_artifacts",
        "receipt_manifest_sha256",
        "schema",
        "session_count",
        "sessions_sha256",
    }
)
_COLLECTION_SNAPSHOT_DATABASE_FIELDS = frozenset(
    {"bytes", "path", "sha256"}
)
_COLLECTION_SNAPSHOT_RAW_FIELDS = frozenset(
    {"dataset", "raw_bytes", "raw_path", "raw_sha256"}
)
_SESSION_AUTHORITY_REF_FIELDS = frozenset(
    {
        "bak_basic_normalized_rows_sha256",
        "bak_basic_raw_sha256",
        "bak_basic_receipt_sha256",
        "bak_basic_row_count",
        "daily_rows_root_sha256",
        "market_generation_id",
        "market_generation_lineage_sha256",
        "market_generation_manifest_sha256",
        "suspend_d_rows_root_sha256",
        "trade_date",
    }
)
_PRODUCER_FILES = (
    "audited_pit_factor_v3_feature_history_authority.py",
    "audited_pit_factor_v3_points_contract.py",
    "durable_io.py",
    "factor_v3_feature_history_runner.py",
    "jiaoch_credential_slots.py",
    "jiaoch_trade_cal_authority.py",
    "research_partitions.py",
    "research_pit_collector.py",
    "research_pit_sources.py",
    "research_pit_store.py",
    "research_security_code_transition.py",
)
_SAFETY = {
    "embargo_consumed": False,
    "experiment_launch_eligible": False,
    "final_oos_consumed": False,
    "formal_factor_materialization_eligible": False,
    "production_profile_registered": False,
    "production_recommendation_eligible": False,
}

FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT = {
    "schema_version": "audited-pit-factor-v3-feature-history-authority-contract/v2",
    "purpose": "feature_history_only",
    "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
    "development_sessions": {
        "count": _FROZEN_DEVELOPMENT_SESSION_COUNT,
        "end": _FROZEN_DEVELOPMENT_END,
        "sha256": _FROZEN_DEVELOPMENT_SESSIONS_SHA256,
        "start": _FROZEN_DEVELOPMENT_START,
    },
    "required_prior_open_sessions": _REQUIRED_PRIOR_OPEN_SESSIONS,
    "temporal_partition_contract": {
        "policy_version": "contamination-aware-forward-oos/v1",
        "sha256": _FROZEN_PARTITION_V1_SHA256,
    },
    "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
    "source_authority": {
        **_FROZEN_JIAOCH_SOURCE_AUTHORITY,
        "allowed_network_routes": ["direct", "loopback_http_proxy"],
        "interface_whitelist": list(_REQUIRED_FEATURE_HISTORY_APIS),
    },
    "feature_history_route_policy": {
        "credential_proof_claimed": False,
        "credential_slot_id": "points-primary",
        "document": deepcopy(_FROZEN_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT),
        "sha256": _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256,
    },
    "collector_recipe": {
        "bak_basic_spec_builder": "app.research_pit_collector.build_bak_basic_specs",
        "collector_class": "app.research_pit_collector.ControlledTushareCollector",
        "market_generation_method": "collect_market_session_generation",
        "market_generation_vintage": "historical_backfill",
        "market_shards": list(_REQUIRED_MARKET_SHARDS),
        "membership_method": "fetch_membership_snapshot",
        "network_execution_implemented": False,
    },
    "upstream_scope_policy": {
        "beijing_rows": "preserve_before_downstream_candidate_scope",
        "filter_applied_during_collection": False,
        "science_technology_rows": "preserve_before_downstream_candidate_scope",
    },
    "verification_recipe": {
        "caller_reported_collection_evidence_accepted": False,
        "pit_store_mode": "read_only_immutable_after_wal_shm_absence",
        "raw_and_normalized_receipts_replayed": True,
        "raw_artifact_identity_postverified": True,
        "closed_store_scope_verified": True,
        "content_addressed_snapshot_required": True,
        "live_pit_store_accepted_by_public_verifier": False,
        "snapshot_database_and_raw_replayed": True,
        "terminal_database_and_raw_snapshot_postverified": True,
        "collection_publication_capability_required": True,
        "collection_publication_issuance_required": True,
        "trade_calendar_publication_capability_required": True,
    },
    "forbidden_outputs_and_operations": [
        "candidate_generation",
        "label_generation",
        "target_generation",
        "train",
        "backtest",
        "validate",
        "experiment_launch",
        "production_profile_registration",
        "production_recommendation",
    ],
    "safety": deepcopy(_SAFETY),
}
FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256 = (
    "ba6e4f6e67871cb5e6341ac7cb9a84c7fa5f9c2055ac1fbbbdf9e1af35ab2cee"
)
if (
    canonical_sha256(FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT)
    != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
):
    raise RuntimeError("frozen factor-v3 feature history authority contract drifted")



def _strict_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_uuid4(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_iso_date(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.isoformat() != value:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_wire_date(value: Any, *, label: str) -> str:
    if type(value) is not str or _WIRE_DATE_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return parsed.isoformat()


def _strict_positive_int(value: Any, *, label: str, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _strict_mapping(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"factor-v3 feature history {label} fields rejected")
    return value


def _strict_sequence(value: Any, *, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _validated_feature_history_route_policy_descriptor(
    value: Any,
) -> dict[str, Any]:
    descriptor = _strict_mapping(
        value,
        fields=frozenset(
            {
                "credential_proof_claimed",
                "document",
                "schema",
                "sha256",
            }
        ),
        label="feature history route policy",
    )
    document = descriptor.get("document")
    if (
        descriptor.get("schema")
        != "jiaoch-credential-feature-history-policy-descriptor/v1"
        or descriptor.get("credential_proof_claimed") is not False
        or document != _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT
    ):
        raise ValueError("factor-v3 feature history route policy rejected")
    digest = _strict_sha256(
        descriptor.get("sha256"),
        label="feature history route policy sha256",
    )
    if (
        not hmac.compare_digest(digest, _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256)
        or not hmac.compare_digest(digest, canonical_sha256(document))
    ):
        raise ValueError("factor-v3 feature history route policy rejected")
    return deepcopy(descriptor)


def _validated_loopback_proxy(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("factor-v3 feature history Jiaoch source rejected")
    match = _LOOPBACK_PROXY_RE.fullmatch(value)
    if match is None:
        raise ValueError("factor-v3 feature history Jiaoch source rejected")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ValueError("factor-v3 feature history Jiaoch source rejected")
    host = match.group("host")
    if host == "localhost":
        return value
    candidate = host[1:-1] if host.startswith("[") else host
    try:
        if not ipaddress.ip_address(candidate).is_loopback:
            raise ValueError
    except ValueError:
        raise ValueError("factor-v3 feature history Jiaoch source rejected") from None
    return value


def _validated_frozen_jiaoch_source_authority(value: Any) -> dict[str, Any]:
    source = _strict_mapping(
        value,
        fields=frozenset(
            {
                "base_origin",
                "network_route",
                "proxy_endpoint",
                "request_protocol",
                "source_profile",
            }
        ),
        label="Jiaoch source authority",
    )
    if any(
        source.get(field) != expected
        for field, expected in _FROZEN_JIAOCH_SOURCE_AUTHORITY.items()
    ):
        raise ValueError("factor-v3 feature history Jiaoch source rejected")
    route = source.get("network_route")
    proxy = source.get("proxy_endpoint")
    if route == "direct":
        if proxy is not None:
            raise ValueError("factor-v3 feature history Jiaoch source rejected")
    elif route == "loopback_http_proxy":
        _validated_loopback_proxy(proxy)
    else:
        raise ValueError("factor-v3 feature history Jiaoch source rejected")
    return deepcopy(source)


def _strict_json_loads(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"factor-v3 feature history {label} rejected")
            output[key] = value
        return output

    def reject_constant(_value: str) -> None:
        raise ValueError(f"factor-v3 feature history {label} rejected")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError(f"factor-v3 feature history {label} rejected") from None
    if type(value) is not dict:
        raise ValueError(f"factor-v3 feature history {label} rejected")
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("factor-v3 feature history canonical JSON rejected") from None


def _validated_collection_publication(value: Any) -> dict[str, Any]:
    publication = _strict_mapping(
        value,
        fields=_COLLECTION_PUBLICATION_FIELDS,
        label="collection publication",
    )
    if (
        publication.get("schema")
        != "audited-pit-factor-v3-feature-history-collection-publication/v1"
        or publication.get("publication_status")
        != "DURABLE_POSTVERIFIED_AND_RETURNED"
        or publication.get("authority_manifest_created") is not True
    ):
        raise ValueError("factor-v3 feature history collection publication rejected")
    digest = _strict_sha256(
        publication.get("authority_manifest_sha256"),
        label="collection publication manifest sha256",
    )
    relative_path = publication.get("authority_manifest_relative_path")
    if type(relative_path) is not str:
        raise ValueError("factor-v3 feature history collection publication rejected")
    match = _COLLECTION_MANIFEST_PATH_RE.fullmatch(relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("factor-v3 feature history collection publication rejected")
    capability = _strict_uuid4(
        publication.get("publication_capability"),
        label="collection publication capability",
    )
    return {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": relative_path,
        "authority_manifest_sha256": digest,
        "publication_capability": capability,
        "publication_status": publication["publication_status"],
        "schema": publication["schema"],
    }


def _is_reparse_point(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISLNK(value.st_mode)
        or getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


@contextmanager
def _snapshot_directory_chain_guard(
    root: Path,
    directory: Path,
):
    resolved_root = root.resolve(strict=True)
    resolved_directory = directory.resolve(strict=True)
    try:
        relative = resolved_directory.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            "factor-v3 feature history snapshot path rejected"
        ) from None
    paths = [resolved_root]
    current = resolved_root
    for part in relative.parts:
        current = current / part
        paths.append(current)
    identities = []
    for path in paths:
        value = os.lstat(path)
        if (
            _is_reparse_point(path)
            or not stat.S_ISDIR(value.st_mode)
        ):
            raise ValueError(
                "factor-v3 feature history snapshot path rejected"
            )
        identities.append(
            (
                path,
                value.st_dev,
                value.st_ino,
                getattr(value, "st_file_attributes", 0),
            )
        )
    handles: list[tuple[int, Any]] = []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class _FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        get_information = kernel32.GetFileInformationByHandleEx
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        get_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        invalid_handle = ctypes.c_void_p(-1).value
        try:
            for position, path in enumerate(paths):
                handle = create_file(
                    str(path),
                    (
                        (
                            0x40000000
                            | 0x00000020
                            | 0x00000080
                            | 0x00100000
                        )
                        if position == len(paths) - 1
                        else 0x00000080
                    ),
                    (
                        0x00000001 | 0x00000002
                        if position == len(paths) - 1
                        else 0x00000001
                    ),
                    None,
                    3,
                    0x00200000 | 0x02000000,
                    None,
                )
                if handle in (None, invalid_handle):
                    raise OSError
                info = _FileAttributeTagInfo()
                if not get_information(
                    handle,
                    9,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ) or info.file_attributes & 0x00000400:
                    close_handle(handle)
                    raise OSError
                handles.append((handle, close_handle))
        except OSError:
            for handle, closer in reversed(handles):
                closer(handle)
            raise ValueError(
                "factor-v3 feature history snapshot path rejected"
            ) from None
    else:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            for path in paths:
                descriptor = os.open(path, flags)
                value = os.fstat(descriptor)
                if not stat.S_ISDIR(value.st_mode):
                    os.close(descriptor)
                    raise OSError
                handles.append((descriptor, os.close))
        except OSError:
            for descriptor, closer in reversed(handles):
                closer(descriptor)
            raise ValueError(
                "factor-v3 feature history snapshot path rejected"
            ) from None
    try:
        yield handles[-1][0]
        for (
            path,
            expected_device,
            expected_inode,
            expected_attributes,
        ) in identities:
            value = os.lstat(path)
            if (
                _is_reparse_point(path)
                or not stat.S_ISDIR(value.st_mode)
                or value.st_dev != expected_device
                or value.st_ino != expected_inode
                or getattr(value, "st_file_attributes", 0)
                != expected_attributes
                or not path.resolve(strict=True).is_relative_to(
                    resolved_root
                )
            ):
                raise ValueError(
                    "factor-v3 feature history snapshot path drifted"
                )
    finally:
        for handle, closer in reversed(handles):
            closer(handle)


def _flush_snapshot_parent(final_parent_anchor: int) -> None:
    if os.name != "nt":
        try:
            os.fsync(final_parent_anchor)
        except OSError:
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            ) from None
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = (wintypes.HANDLE,)
    flush_file_buffers.restype = wintypes.BOOL
    if not flush_file_buffers(final_parent_anchor):
        raise ValueError(
            "factor-v3 feature history snapshot promotion rejected"
        )


def _promote_snapshot_directory(
    *,
    staging: Path,
    final_root: Path,
    final_parent_anchor: int,
) -> None:
    if final_root.name in {"", ".", ".."} or final_root.parent == final_root:
        raise ValueError(
            "factor-v3 feature history snapshot path rejected"
        )
    if os.name != "nt":
        import ctypes
        import errno

        libc = ctypes.CDLL(None, use_errno=True)
        rename_noreplace = getattr(libc, "renameat2", None)
        if rename_noreplace is None:
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            )
        rename_noreplace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_noreplace.restype = ctypes.c_int
        ctypes.set_errno(0)
        try:
            result = rename_noreplace(
                -100,
                os.fsencode(staging),
                final_parent_anchor,
                os.fsencode(final_root.name),
                1,
            )
        except (OSError, ValueError, TypeError):
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            ) from None
        if result != 0:
            error_code = ctypes.get_errno()
            if error_code in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(
                    error_code,
                    "snapshot CAS already exists",
                    str(final_root),
                )
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            )
        _flush_snapshot_parent(final_parent_anchor)
        return

    import ctypes
    from ctypes import wintypes

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    class _FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", wintypes.BYTE),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    class _IoStatusValue(ctypes.Union):
        _fields_ = [
            ("status", wintypes.LONG),
            ("pointer", wintypes.LPVOID),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("value", _IoStatusValue),
            ("information", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    nt_set_information = ntdll.NtSetInformationFile
    nt_set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    )
    nt_set_information.restype = wintypes.LONG
    nt_status_to_error = ntdll.RtlNtStatusToDosError
    nt_status_to_error.argtypes = (wintypes.LONG,)
    nt_status_to_error.restype = wintypes.ULONG
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    source_handle = create_file(
        str(staging),
        0x00010000 | 0x00000080 | 0x00100000,
        0x00000001,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if source_handle in (None, invalid_handle):
        raise ValueError(
            "factor-v3 feature history snapshot promotion rejected"
        )
    try:
        attributes = _FileAttributeTagInfo()
        if not get_information(
            source_handle,
            9,
            ctypes.byref(attributes),
            ctypes.sizeof(attributes),
        ) or attributes.file_attributes & 0x00000400:
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            )
        encoded_name = final_root.name.encode("utf-16-le")
        offset = _FileRenameInfo.file_name.offset
        buffer = ctypes.create_string_buffer(
            ctypes.sizeof(_FileRenameInfo) + len(encoded_name)
        )
        information = _FileRenameInfo.from_buffer(buffer)
        information.replace_if_exists = 0
        information.root_directory = final_parent_anchor
        information.file_name_length = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(buffer) + offset,
            encoded_name,
            len(encoded_name),
        )
        io_status = _IoStatusBlock()
        status = nt_set_information(
            source_handle,
            ctypes.byref(io_status),
            buffer,
            len(buffer),
            10,
        )
        if status < 0:
            error_code = int(nt_status_to_error(status))
            if error_code in {80, 183}:
                raise FileExistsError(
                    error_code,
                    "snapshot CAS already exists",
                    str(final_root),
                )
            raise ValueError(
                "factor-v3 feature history snapshot promotion rejected"
            )
        _flush_snapshot_parent(final_parent_anchor)
    finally:
        close_handle(source_handle)


def _quarantine_unidentified_temporary(
    *,
    root: Path,
    parent: Path,
    temporary_path: Path,
    label: str,
) -> None:
    try:
        temporary_metadata = temporary_path.lstat()
    except OSError:
        raise ValueError(
            f"factor-v3 feature history collection {label} quarantine rejected"
        ) from None
    if (
        not stat.S_ISREG(temporary_metadata.st_mode)
        or _is_reparse_point(temporary_path)
    ):
        raise ValueError(
            f"factor-v3 feature history collection {label} quarantine rejected"
        )
    quarantine_parent = parent / ".quarantine"
    try:
        quarantine_parent.mkdir()
    except FileExistsError:
        pass
    except OSError:
        raise ValueError(
            f"factor-v3 feature history collection {label} quarantine rejected"
        ) from None
    try:
        fsync_directory(parent)
        quarantine_parent = raw_authority._safe_existing_directory(
            quarantine_parent,
            f"factor-v3 feature history collection {label} quarantine",
        )
        quarantine_path = quarantine_parent / f"{uuid.uuid4().hex}.quarantine"
        with _snapshot_directory_chain_guard(root, quarantine_parent) as final_parent_anchor:
            _promote_snapshot_directory(
                staging=temporary_path,
                final_root=quarantine_path,
                final_parent_anchor=final_parent_anchor,
            )
    except (OSError, ValueError):
        raise ValueError(
            f"factor-v3 feature history collection {label} quarantine rejected"
        ) from None


def _safe_collection_output_root(value: str | Path) -> Path:
    try:
        return raw_authority._safe_existing_directory(
            Path(value),
            "factor-v3 feature history collection output root",
        )
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history collection output root rejected") from None


def _collection_manifest_path(
    *,
    output_root: str | Path,
    relative_path: str,
    expected_sha256: str,
) -> Path:
    root = _safe_collection_output_root(output_root)
    match = _COLLECTION_MANIFEST_PATH_RE.fullmatch(relative_path)
    if match is None or match.group(1) != expected_sha256[:2] or match.group(2) != expected_sha256:
        raise ValueError("factor-v3 feature history collection manifest path rejected")
    path = root.joinpath(*relative_path.split("/"))
    try:
        if (
            _is_reparse_point(path)
            or not path.is_file()
            or path.resolve(strict=True).parent != path.parent.resolve(strict=True)
            or not path.resolve(strict=True).is_relative_to(root)
        ):
            raise OSError
    except OSError:
        raise ValueError("factor-v3 feature history collection manifest path rejected") from None
    return path


def _read_collection_manifest(
    *,
    output_root: str | Path,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    path = _collection_manifest_path(
        output_root=output_root,
        relative_path=publication["authority_manifest_relative_path"],
        expected_sha256=publication["authority_manifest_sha256"],
    )
    try:
        raw = raw_authority._read_safe_file(
            path,
            label="factor-v3 feature history collection manifest",
            max_bytes=_MAX_COLLECTION_MANIFEST_BYTES,
        )
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history collection manifest rejected") from None
    if (
        not raw
        or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), publication["authority_manifest_sha256"]
        )
    ):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    payload = _strict_json_loads(raw, label="collection manifest")
    if not hmac.compare_digest(raw, _canonical_bytes(payload)):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    return payload


def _write_collection_content_addressed_candidate(
    *,
    output_root: str | Path,
    relative_path: str,
    raw: bytes,
    path_pattern: re.Pattern[str],
    label: str,
) -> bool:
    if type(raw) is not bytes:
        raise ValueError(f"factor-v3 feature history collection {label} rejected")
    root = _safe_collection_output_root(output_root)
    if path_pattern.fullmatch(relative_path) is None:
        raise ValueError(f"factor-v3 feature history collection {label} rejected")
    parts = relative_path.split("/")
    digest = parts[-1].removesuffix(".json")
    destination: Path | None = None
    parent: Path | None = None
    temporary_path: Path | None = None
    temporary_identity: os.stat_result | None = None
    descriptor = -1
    completed = False
    try:
        parent = raw_authority._content_addressed_directory(
            root,
            parts[0],
            digest,
        )
        destination = parent / parts[-1]
        if destination.relative_to(root).as_posix() != relative_path:
            raise ValueError
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=str(parent),
        )
        temporary_path = Path(temporary_name)
        temporary_identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_metadata = temporary_path.lstat()
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or _is_reparse_point(temporary_path)
            or not os.path.samestat(temporary_metadata, temporary_identity)
        ):
            raise ValueError
        with _snapshot_directory_chain_guard(root, parent) as final_parent_anchor:
            _promote_snapshot_directory(
                staging=temporary_path,
                final_root=destination,
                final_parent_anchor=final_parent_anchor,
            )
        temporary_path = None
        fsync_directory(parent)
        stored = raw_authority._read_safe_file(
            destination,
            label=f"factor-v3 feature history collection {label}",
            max_bytes=len(raw),
            expected_size=len(raw),
        )
        if not hmac.compare_digest(stored, raw):
            raise ValueError(
                f"factor-v3 feature history collection {label} postverify rejected"
            )
        completed = True
    except (OSError, ValueError):
        raise ValueError(f"factor-v3 feature history collection {label} rejected") from None
    finally:
        if descriptor != -1 and temporary_identity is None:
            try:
                temporary_identity = os.fstat(descriptor)
            except OSError:
                pass
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not completed and temporary_identity is None and temporary_path is not None:
            if root is None or parent is None:
                raise ValueError(
                    f"factor-v3 feature history collection {label} quarantine rejected"
                ) from None
            _quarantine_unidentified_temporary(
                root=root,
                parent=parent,
                temporary_path=temporary_path,
                label=label,
            )
        elif not completed and temporary_identity is not None:
            for candidate in (destination, temporary_path):
                if candidate is None:
                    continue
                try:
                    metadata = candidate.lstat()
                    if (
                        stat.S_ISREG(metadata.st_mode)
                        and not _is_reparse_point(candidate)
                        and os.path.samestat(metadata, temporary_identity)
                    ):
                        candidate.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    raise ValueError(
                        f"factor-v3 feature history collection {label} temporary cleanup rejected"
                    ) from None
            if parent is not None:
                try:
                    fsync_directory(parent)
                except OSError:
                    raise ValueError(
                        f"factor-v3 feature history collection {label} temporary cleanup rejected"
                    ) from None
    return True


def _write_collection_manifest_candidate(
    *,
    output_root: str | Path,
    relative_path: str,
    raw: bytes,
) -> bool:
    return _write_collection_content_addressed_candidate(
        output_root=output_root,
        relative_path=relative_path,
        raw=raw,
        path_pattern=_COLLECTION_MANIFEST_PATH_RE,
        label="manifest",
    )


def _collection_publication_issuance(
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "authority_manifest_sha256": publication["authority_manifest_sha256"],
        "publication_capability_sha256": hashlib.sha256(
            publication["publication_capability"].encode("utf-8")
        ).hexdigest(),
        "publication_schema": publication["schema"],
        "publication_status": publication["publication_status"],
        "schema": "audited-pit-factor-v3-feature-history-publication-issuance/v1",
    }


def _collection_issuance_relative_path(issuance: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_bytes(issuance)).hexdigest()
    return (
        "feature_history_collection_publication_receipts/sha256/"
        f"{digest[:2]}/{digest}.json"
    )


def _write_collection_publication_issuance(
    *,
    output_root: str | Path,
    publication: Mapping[str, Any],
) -> None:
    issuance = _collection_publication_issuance(publication)
    raw = _canonical_bytes(issuance)
    if len(raw) > _MAX_COLLECTION_ISSUANCE_BYTES:
        raise ValueError("factor-v3 feature history collection issuance rejected")
    _write_collection_content_addressed_candidate(
        output_root=output_root,
        relative_path=_collection_issuance_relative_path(issuance),
        raw=raw,
        path_pattern=_COLLECTION_ISSUANCE_PATH_RE,
        label="issuance",
    )


def _validated_collection_publication_issuance(
    *,
    output_root: str | Path,
    publication: Mapping[str, Any],
) -> None:
    issuance = _collection_publication_issuance(publication)
    relative_path = _collection_issuance_relative_path(issuance)
    digest = relative_path.rsplit("/", 1)[1].removesuffix(".json")
    root = _safe_collection_output_root(output_root)
    path = root.joinpath(*relative_path.split("/"))
    try:
        raw = raw_authority._read_safe_file(
            path,
            label="factor-v3 feature history collection issuance",
            max_bytes=_MAX_COLLECTION_ISSUANCE_BYTES,
        )
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history collection issuance rejected") from None
    if (
        len(raw) > _MAX_COLLECTION_ISSUANCE_BYTES
        or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest)
    ):
        raise ValueError("factor-v3 feature history collection issuance rejected")
    stored = _strict_json_loads(raw, label="collection issuance")
    if not hmac.compare_digest(raw, _canonical_bytes(stored)) or stored != issuance:
        raise ValueError("factor-v3 feature history collection issuance rejected")


@contextmanager
def _snapshot_source_reader(path: Path):
    try:
        if _is_reparse_point(path) or not path.is_file():
            raise OSError
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError(
            "factor-v3 feature history snapshot source rejected"
        ) from None
    descriptor = -1
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class _FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        get_information = kernel32.GetFileInformationByHandleEx
        get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        get_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = create_file(
            str(resolved),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ValueError(
                "factor-v3 feature history snapshot source rejected"
            )
        info = _FileAttributeTagInfo()
        try:
            if not get_information(
                handle,
                9,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ) or info.file_attributes & 0x00000400:
                raise OSError
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            handle = invalid_handle
        except (OSError, ValueError):
            if handle != invalid_handle:
                close_handle(handle)
            raise ValueError(
                "factor-v3 feature history snapshot source rejected"
            ) from None
    else:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(resolved, flags)
        except OSError:
            raise ValueError(
                "factor-v3 feature history snapshot source rejected"
            ) from None
    try:
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(
                "factor-v3 feature history snapshot source rejected"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            yield handle, source_stat
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _snapshot_raw_descriptors_from_database(
    database_path: Path,
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    connection = sqlite3.connect(
        database_path.resolve(strict=True).as_uri()
        + "?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT dataset, raw_path, raw_sha256, raw_bytes
            FROM receipts
            WHERE raw_path IS NOT NULL AND raw_path != ''
            UNION ALL
            SELECT dataset, raw_path, raw_sha256, raw_bytes
            FROM fetch_attempts
            WHERE raw_path IS NOT NULL AND raw_path != ''
            ORDER BY raw_path COLLATE BINARY
            """
        )
        for stored in rows:
            dataset = str(stored["dataset"])
            raw_path = str(stored["raw_path"])
            raw_sha256 = _strict_sha256(
                stored["raw_sha256"],
                label="snapshot raw sha256",
            )
            raw_bytes = stored["raw_bytes"]
            relative = Path(*raw_path.split("/"))
            if (
                dataset not in _REQUIRED_FEATURE_HISTORY_APIS
                or type(raw_bytes) is not int
                or raw_bytes <= 0
                or relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != raw_path
                or len(relative.parts) != 4
                or relative.parts[0] != "raw"
                or relative.parts[1] != dataset
                or relative.parts[2] != raw_sha256[:2]
                or relative.stem != raw_sha256
                or relative.suffix != ".json"
            ):
                raise ValueError(
                    "factor-v3 feature history snapshot raw descriptor rejected"
                )
            descriptor = {
                "dataset": dataset,
                "raw_bytes": raw_bytes,
                "raw_path": raw_path,
                "raw_sha256": raw_sha256,
            }
            previous = records.setdefault(raw_path, descriptor)
            if previous != descriptor:
                raise ValueError(
                    "factor-v3 feature history snapshot raw descriptor rejected"
                )
    except sqlite3.DatabaseError:
        raise ValueError(
            "factor-v3 feature history snapshot database rejected"
        ) from None
    finally:
        connection.close()
    if not records:
        raise ValueError(
            "factor-v3 feature history snapshot raw descriptor rejected"
        )
    return [records[key] for key in sorted(records)]


def _copy_snapshot_member(
    *,
    source_root: Path,
    source_relative_path: str,
    destination_root: Path,
    expected_bytes: int | None,
    expected_sha256: str | None,
) -> dict[str, Any]:
    relative = Path(*source_relative_path.split("/"))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != source_relative_path
    ):
        raise ValueError(
            "factor-v3 feature history snapshot member rejected"
        )
    source = source_root.joinpath(*relative.parts)
    current = source_root
    try:
        for part in relative.parts:
            current = current / part
            if _is_reparse_point(current):
                raise OSError
        if not source.resolve(strict=True).is_relative_to(
            source_root.resolve(strict=True)
        ):
            raise OSError
    except OSError:
        raise ValueError(
            "factor-v3 feature history snapshot member rejected"
        ) from None
    destination = destination_root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    current = destination_root
    for part in relative.parts[:-1]:
        current = current / part
        if _is_reparse_point(current) or not current.is_dir():
            raise ValueError(
                "factor-v3 feature history snapshot member rejected"
            )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    output_descriptor = -1
    digest = hashlib.sha256()
    copied = 0
    try:
        with _snapshot_source_reader(source) as (
            source_handle,
            source_stat,
        ):
            if (
                expected_bytes is not None
                and source_stat.st_size != expected_bytes
            ):
                raise ValueError(
                    "factor-v3 feature history snapshot member rejected"
                )
            output_descriptor = os.open(destination, flags, 0o600)
            with os.fdopen(output_descriptor, "wb") as output_handle:
                output_descriptor = -1
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    copied += len(chunk)
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
    except (OSError, ValueError):
        try:
            if destination.exists() and not _is_reparse_point(
                destination
            ):
                destination.unlink()
        except OSError:
            pass
        raise ValueError(
            "factor-v3 feature history snapshot member rejected"
        ) from None
    finally:
        if output_descriptor >= 0:
            os.close(output_descriptor)
    observed_sha256 = digest.hexdigest()
    if (
        copied <= 0
        or (
            expected_bytes is not None
            and copied != expected_bytes
        )
        or (
            expected_sha256 is not None
            and not hmac.compare_digest(
                observed_sha256,
                expected_sha256,
            )
        )
        or destination.stat().st_size != copied
        or not hmac.compare_digest(
            _file_sha256(destination),
            observed_sha256,
        )
    ):
        destination.unlink(missing_ok=True)
        raise ValueError(
            "factor-v3 feature history snapshot member rejected"
        )
    fsync_directory(destination.parent)
    return {
        "bytes": copied,
        "path": source_relative_path,
        "sha256": observed_sha256,
    }


def _snapshot_index_relative_path(digest: str) -> str:
    validated = _strict_sha256(
        digest,
        label="snapshot index sha256",
    )
    return (
        "feature_history_collection_snapshots/sha256/"
        f"{validated[:2]}/{validated}/snapshot-index.json"
    )


def _snapshot_raw_artifact_set_sha256(
    descriptors: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_sha256(
        [
            {
                "raw_bytes": descriptor["raw_bytes"],
                "raw_path": descriptor["raw_path"],
                "raw_sha256": descriptor["raw_sha256"],
            }
            for descriptor in descriptors
        ]
    )


def _validated_snapshot_index(
    value: Any,
    *,
    sessions: Sequence[str],
) -> dict[str, Any]:
    index = _strict_mapping(
        value,
        fields=_COLLECTION_SNAPSHOT_INDEX_FIELDS,
        label="snapshot index",
    )
    database = _strict_mapping(
        index.get("database"),
        fields=_COLLECTION_SNAPSHOT_DATABASE_FIELDS,
        label="snapshot database",
    )
    if (
        index.get("schema")
        != "audited-pit-factor-v3-feature-history-snapshot-index/v1"
        or index.get("session_count") != len(sessions)
        or index.get("sessions_sha256") != canonical_sha256(sessions)
        or database.get("path") != "metadata.sqlite3"
        or type(database.get("bytes")) is not int
        or database["bytes"] <= 0
    ):
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        )
    _strict_sha256(
        database.get("sha256"),
        label="snapshot database sha256",
    )
    _strict_sha256(
        index.get("receipt_manifest_sha256"),
        label="snapshot receipt manifest sha256",
    )
    raw = index.get("raw_artifacts")
    if type(raw) is not list or not raw:
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        )
    normalized = []
    for item in raw:
        descriptor = _strict_mapping(
            item,
            fields=_COLLECTION_SNAPSHOT_RAW_FIELDS,
            label="snapshot raw descriptor",
        )
        dataset = descriptor.get("dataset")
        raw_path = descriptor.get("raw_path")
        raw_sha256 = _strict_sha256(
            descriptor.get("raw_sha256"),
            label="snapshot raw sha256",
        )
        raw_bytes = descriptor.get("raw_bytes")
        if type(raw_path) is not str:
            raise ValueError(
                "factor-v3 feature history snapshot index rejected"
            )
        relative = Path(*raw_path.split("/"))
        if (
            dataset not in _REQUIRED_FEATURE_HISTORY_APIS
            or type(raw_bytes) is not int
            or raw_bytes <= 0
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != raw_path
            or len(relative.parts) != 4
            or relative.parts[0] != "raw"
            or relative.parts[1] != dataset
            or relative.parts[2] != raw_sha256[:2]
            or relative.stem != raw_sha256
            or relative.suffix != ".json"
        ):
            raise ValueError(
                "factor-v3 feature history snapshot index rejected"
            )
        normalized.append(
            {
                "dataset": dataset,
                "raw_bytes": raw_bytes,
                "raw_path": raw_path,
                "raw_sha256": raw_sha256,
            }
        )
    if (
        normalized
        != sorted(normalized, key=lambda item: item["raw_path"])
        or len({item["raw_path"] for item in normalized})
        != len(normalized)
        or index.get("raw_artifact_count") != len(normalized)
        or index.get("raw_artifact_set_sha256")
        != _snapshot_raw_artifact_set_sha256(normalized)
    ):
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        )
    return {
        **deepcopy(index),
        "database": deepcopy(database),
        "raw_artifacts": normalized,
    }


def _read_and_replay_collection_snapshot(
    *,
    output_root: str | Path,
    relative_path: str,
    expected_sha256: str,
    sessions: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    digest = _strict_sha256(
        expected_sha256,
        label="snapshot index sha256",
    )
    match = _COLLECTION_SNAPSHOT_INDEX_PATH_RE.fullmatch(relative_path)
    if (
        match is None
        or match.group(1) != digest[:2]
        or match.group(2) != digest
    ):
        raise ValueError(
            "factor-v3 feature history snapshot index path rejected"
        )
    root = _safe_collection_output_root(output_root)
    path = root.joinpath(*relative_path.split("/"))
    snapshot_root = path.parent
    try:
        descendant = root
        for part in relative_path.split("/"):
            descendant = descendant / part
            if descendant.exists() and _is_reparse_point(descendant):
                raise OSError
        if (
            _is_reparse_point(path)
            or not path.is_file()
            or path.resolve(strict=True).parent
            != snapshot_root.resolve(strict=True)
            or not path.resolve(strict=True).is_relative_to(root)
        ):
            raise OSError
        stat_before = path.stat()
        if (
            stat_before.st_size <= 0
            or stat_before.st_size
            > _MAX_COLLECTION_SNAPSHOT_INDEX_BYTES
        ):
            raise OSError
        raw = path.read_bytes()
        stat_after = path.stat()
    except OSError:
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        ) from None
    if (
        len(raw) != stat_before.st_size
        or stat_after.st_size != stat_before.st_size
        or stat_after.st_mtime_ns != stat_before.st_mtime_ns
        or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(),
            digest,
        )
    ):
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        )
    index = _validated_snapshot_index(
        _strict_json_loads(raw, label="snapshot index"),
        sessions=sessions,
    )
    if not hmac.compare_digest(raw, _canonical_bytes(index)):
        raise ValueError(
            "factor-v3 feature history snapshot index rejected"
        )
    database_path = snapshot_root / index["database"]["path"]
    if (
        _is_reparse_point(database_path)
        or not database_path.is_file()
        or database_path.stat().st_size != index["database"]["bytes"]
        or not hmac.compare_digest(
            _file_sha256(database_path),
            index["database"]["sha256"],
        )
    ):
        raise ValueError(
            "factor-v3 feature history snapshot database rejected"
        )
    actual_paths = set()
    raw_root = snapshot_root / "raw"
    try:
        if _is_reparse_point(raw_root) or not raw_root.is_dir():
            raise OSError
        for candidate in raw_root.rglob("*"):
            if _is_reparse_point(candidate):
                raise OSError
            if candidate.is_file():
                actual_paths.add(
                    candidate.relative_to(snapshot_root).as_posix()
                )
    except (OSError, ValueError):
        raise ValueError(
            "factor-v3 feature history snapshot raw artifact rejected"
        ) from None
    expected_paths = {
        descriptor["raw_path"]
        for descriptor in index["raw_artifacts"]
    }
    if actual_paths != expected_paths:
        raise ValueError(
            "factor-v3 feature history snapshot raw artifact rejected"
        )
    for descriptor in index["raw_artifacts"]:
        member = snapshot_root.joinpath(
            *descriptor["raw_path"].split("/")
        )
        if (
            _is_reparse_point(member)
            or not member.is_file()
            or member.stat().st_size != descriptor["raw_bytes"]
            or not hmac.compare_digest(
                _file_sha256(member),
                descriptor["raw_sha256"],
            )
        ):
            raise ValueError(
                "factor-v3 feature history snapshot raw artifact rejected"
            )
    replay = _replay_pit_store(
        pit_store_root=snapshot_root,
        expected_database_sha256=index["database"]["sha256"],
        sessions=sessions,
        temporal_partition_contract=temporal_partition_contract,
    )
    terminal_stat = path.stat()
    if (
        replay["database_bytes"] != index["database"]["bytes"]
        or replay["receipt_manifest_sha256"]
        != index["receipt_manifest_sha256"]
        or replay["raw_artifact_count"]
        != index["raw_artifact_count"]
        or replay["raw_artifact_set_sha256"]
        != index["raw_artifact_set_sha256"]
        or terminal_stat.st_size != stat_before.st_size
        or terminal_stat.st_mtime_ns != stat_before.st_mtime_ns
        or not hmac.compare_digest(_file_sha256(path), digest)
    ):
        raise ValueError(
            "factor-v3 feature history snapshot replay rejected"
        )
    return {
        "index": index,
        "relative_path": relative_path,
        "replay": replay,
        "sha256": digest,
        "snapshot_root": snapshot_root,
    }


def _discard_successful_snapshot_staging(*, root: Path, staging: Path) -> None:
    try:
        resolved_root = root.resolve(strict=True)
        if (
            not staging.name.startswith(".feature-history-snapshot-")
            or not staging.name.endswith(".partial")
            or staging.parent.resolve(strict=True) != resolved_root
            or _is_reparse_point(staging)
            or not staging.is_dir()
        ):
            raise OSError
        for candidate in staging.rglob("*"):
            if _is_reparse_point(candidate):
                raise OSError
        shutil.rmtree(staging)
        fsync_directory(resolved_root)
    except OSError:
        raise ValueError(
            "factor-v3 feature history snapshot staging cleanup rejected"
        ) from None


def _capture_feature_history_snapshot(
    *,
    pit_store_root: str | Path,
    output_root: str | Path,
    sessions: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    source_root = Path(pit_store_root)
    root = _safe_collection_output_root(output_root)
    try:
        if (
            _is_reparse_point(source_root)
            or not source_root.is_dir()
            or source_root.resolve(strict=True) == root
            or source_root.resolve(strict=True).is_relative_to(root)
            or root.is_relative_to(source_root.resolve(strict=True))
        ):
            raise OSError
        source_root = source_root.resolve(strict=True)
    except OSError:
        raise ValueError(
            "factor-v3 feature history snapshot source root rejected"
        ) from None
    source_database = source_root / "metadata.sqlite3"
    if any(
        Path(f"{source_database}{suffix}").exists()
        for suffix in ("-wal", "-shm")
    ):
        raise ValueError(
            "factor-v3 feature history PIT store must be finalized without WAL or SHM"
        )
    staging = Path(
        tempfile.mkdtemp(
            prefix=".feature-history-snapshot-",
            suffix=".partial",
            dir=str(root),
        )
    )
    try:
        database = _copy_snapshot_member(
            source_root=source_root,
            source_relative_path="metadata.sqlite3",
            destination_root=staging,
            expected_bytes=None,
            expected_sha256=None,
        )
        descriptors = _snapshot_raw_descriptors_from_database(
            staging / "metadata.sqlite3"
        )
        source_raw_root = source_root / "raw"
        actual_source_paths = set()
        try:
            if (
                _is_reparse_point(source_raw_root)
                or not source_raw_root.is_dir()
            ):
                raise OSError
            for candidate in source_raw_root.rglob("*"):
                if _is_reparse_point(candidate):
                    raise OSError
                if candidate.is_file():
                    actual_source_paths.add(
                        candidate.relative_to(source_root).as_posix()
                    )
        except (OSError, ValueError):
            raise ValueError(
                "factor-v3 feature history snapshot source raw tree rejected"
            ) from None
        if actual_source_paths != {
            descriptor["raw_path"] for descriptor in descriptors
        }:
            raise ValueError(
                "factor-v3 feature history snapshot source raw tree rejected"
            )
        for descriptor in descriptors:
            copied = _copy_snapshot_member(
                source_root=source_root,
                source_relative_path=descriptor["raw_path"],
                destination_root=staging,
                expected_bytes=descriptor["raw_bytes"],
                expected_sha256=descriptor["raw_sha256"],
            )
            if copied != {
                "bytes": descriptor["raw_bytes"],
                "path": descriptor["raw_path"],
                "sha256": descriptor["raw_sha256"],
            }:
                raise ValueError(
                    "factor-v3 feature history snapshot raw copy rejected"
                )
        replay = _replay_pit_store(
            pit_store_root=staging,
            expected_database_sha256=database["sha256"],
            sessions=sessions,
            temporal_partition_contract=temporal_partition_contract,
        )
        if (
            replay["raw_artifact_count"] != len(descriptors)
            or replay["raw_artifact_set_sha256"]
            != _snapshot_raw_artifact_set_sha256(descriptors)
        ):
            raise ValueError(
                "factor-v3 feature history snapshot replay rejected"
            )
        index = {
            "database": database,
            "raw_artifact_count": len(descriptors),
            "raw_artifact_set_sha256": (
                _snapshot_raw_artifact_set_sha256(descriptors)
            ),
            "raw_artifacts": descriptors,
            "receipt_manifest_sha256": replay[
                "receipt_manifest_sha256"
            ],
            "schema": (
                "audited-pit-factor-v3-feature-history-snapshot-index/v1"
            ),
            "session_count": len(sessions),
            "sessions_sha256": canonical_sha256(sessions),
        }
        raw_index = _canonical_bytes(
            _validated_snapshot_index(index, sessions=sessions)
        )
        if len(raw_index) > _MAX_COLLECTION_SNAPSHOT_INDEX_BYTES:
            raise ValueError(
                "factor-v3 feature history snapshot index rejected"
            )
        digest = hashlib.sha256(raw_index).hexdigest()
        index_path = staging / "snapshot-index.json"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(index_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw_index)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(staging)
        relative_path = _snapshot_index_relative_path(digest)
        directory_parts = relative_path.split("/")[:-1]
        final_parent = root
        for part in directory_parts[:-1]:
            child = final_parent / part
            if not child.exists():
                child.mkdir()
                fsync_directory(final_parent)
            if (
                _is_reparse_point(child)
                or not child.is_dir()
                or not child.resolve(strict=True).is_relative_to(root)
            ):
                raise ValueError(
                    "factor-v3 feature history snapshot path rejected"
                )
            final_parent = child
        final_root = final_parent / directory_parts[-1]
        with _snapshot_directory_chain_guard(
            root,
            final_parent,
        ) as final_parent_anchor:
            if final_root.exists():
                _flush_snapshot_parent(final_parent_anchor)
                existing = _read_and_replay_collection_snapshot(
                    output_root=root,
                    relative_path=relative_path,
                    expected_sha256=digest,
                    sessions=sessions,
                    temporal_partition_contract=(
                        temporal_partition_contract
                    ),
                )
                _discard_successful_snapshot_staging(root=root, staging=staging)
                return existing
            try:
                _promote_snapshot_directory(
                    staging=staging,
                    final_root=final_root,
                    final_parent_anchor=final_parent_anchor,
                )
            except FileExistsError:
                _flush_snapshot_parent(final_parent_anchor)
                existing = _read_and_replay_collection_snapshot(
                    output_root=root,
                    relative_path=relative_path,
                    expected_sha256=digest,
                    sessions=sessions,
                    temporal_partition_contract=(
                        temporal_partition_contract
                    ),
                )
                _discard_successful_snapshot_staging(root=root, staging=staging)
                return existing
            return _read_and_replay_collection_snapshot(
                output_root=root,
                relative_path=relative_path,
                expected_sha256=digest,
                sessions=sessions,
                temporal_partition_contract=temporal_partition_contract,
            )
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "factor-v3 feature history"
        ):
            raise
        raise ValueError(
            "factor-v3 feature history snapshot capture rejected"
        ) from None


def _validated_trade_cal_publication(value: Any) -> dict[str, Any]:
    publication = _strict_mapping(
        value,
        fields=_TRADE_CAL_PUBLICATION_FIELDS,
        label="trade calendar publication",
    )
    if (
        publication.get("schema") != "jiaoch-trade-cal-authority-publication/v1"
        or publication.get("publication_status")
        != "DURABLE_POSTVERIFIED_AND_RETURNED"
        or publication.get("authority_manifest_created") is not True
    ):
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    digest = _strict_sha256(
        publication.get("authority_manifest_sha256"),
        label="trade calendar publication manifest sha256",
    )
    relative_path = publication.get("authority_manifest_relative_path")
    if type(relative_path) is not str:
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    match = _TRADE_CAL_MANIFEST_PATH_RE.fullmatch(relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("factor-v3 feature history trade calendar publication rejected")
    capability = _strict_uuid4(
        publication.get("publication_capability"),
        label="trade calendar publication capability",
    )
    return {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": relative_path,
        "authority_manifest_sha256": digest,
        "publication_capability": capability,
        "publication_status": publication["publication_status"],
        "schema": publication["schema"],
    }


def _validated_trade_cal_verification(
    value: Any,
    *,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or value.get("verified") is not True
        or value.get("calendar_authority_status") != "VERIFIED_SINGLE_SEALED_CALL"
        or value.get("authority_manifest_sha256")
        != publication["authority_manifest_sha256"]
        or value.get("exchange") != "SSE"
        or value.get("development_session_alignment_verified") is not False
        or value.get("embargo_consumed") is not False
        or value.get("final_oos_consumed") is not False
        or value.get("production_profile_registered") is not False
        or value.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("factor-v3 feature history trade calendar verification rejected")
    for field in (
        "authority_manifest_sha256",
        "is_open_normalization_root_sha256",
        "open_sessions_root_sha256",
    ):
        _strict_sha256(value.get(field), label=f"trade calendar verification {field}")
    _strict_positive_int(
        value.get("open_session_count"),
        label="trade calendar verification open session count",
    )
    _strict_iso_date(
        value.get("start_date"),
        label="trade calendar verification start",
    )
    _strict_iso_date(
        value.get("end_date"),
        label="trade calendar verification end",
    )
    return deepcopy(value)


def _load_verified_trade_cal_manifest(
    *,
    output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a candidate only while its returned publication capability verifies."""

    from app.jiaoch_trade_cal_authority import verify_jiaoch_trade_cal_authority

    publication = _validated_trade_cal_publication(trade_cal_publication)
    verifier_args = {
        "output_root": output_root,
        "authority_manifest_relative_path": publication[
            "authority_manifest_relative_path"
        ],
        "expected_authority_manifest_sha256": publication[
            "authority_manifest_sha256"
        ],
        "publication_capability": publication["publication_capability"],
    }
    first = _validated_trade_cal_verification(
        verify_jiaoch_trade_cal_authority(**verifier_args),
        publication=publication,
    )
    path = Path(output_root).joinpath(
        *publication["authority_manifest_relative_path"].split("/")
    )
    try:
        stat = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.st_size > _MAX_TRADE_CAL_MANIFEST_BYTES
        ):
            raise OSError
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected") from None
    if len(raw) != stat.st_size or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(),
        publication["authority_manifest_sha256"],
    ):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    payload = _strict_json_loads(raw, label="trade calendar manifest")
    if not hmac.compare_digest(raw, _canonical_bytes(payload)):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    second = _validated_trade_cal_verification(
        verify_jiaoch_trade_cal_authority(**verifier_args),
        publication=publication,
    )
    if second != first:
        raise ValueError("factor-v3 feature history trade calendar verification drifted")
    return payload, first


def _validated_development_sessions(
    development_session_refs: Sequence[Mapping[str, Any]],
) -> list[str]:
    refs = _strict_sequence(development_session_refs, label="development session refs")
    sessions: list[str] = []
    for raw in refs:
        row = _strict_mapping(
            raw,
            fields=frozenset({"trade_date"}),
            label="development session ref",
        )
        sessions.append(_strict_iso_date(row["trade_date"], label="development trade_date"))
    if (
        len(sessions) != _FROZEN_DEVELOPMENT_SESSION_COUNT
        or sessions[0] != _FROZEN_DEVELOPMENT_START
        or sessions[-1] != _FROZEN_DEVELOPMENT_END
        or sessions != sorted(sessions)
        or len(set(sessions)) != len(sessions)
        or not hmac.compare_digest(
            canonical_sha256(sessions),
            _FROZEN_DEVELOPMENT_SESSIONS_SHA256,
        )
    ):
        raise ValueError("factor-v3 feature history frozen development sessions rejected")
    return sessions


def _validated_partition_contract(
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    if type(temporal_partition_contract) is not dict:
        raise ValueError("factor-v3 feature history partition contract rejected")
    contract = deepcopy(temporal_partition_contract)
    try:
        if (
            contract.get("policy_version") != "contamination-aware-forward-oos/v1"
            or contract.get("contract_sha256") != _FROZEN_PARTITION_V1_SHA256
        ):
            raise PartitionContractError("wrong frozen contract")
        classify_date(contract, _FROZEN_DEVELOPMENT_START)
    except PartitionContractError:
        raise ValueError("factor-v3 feature history partition contract rejected") from None
    return contract


def _validated_trade_calendar_manifest(
    manifest: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    if (
        type(manifest) is not dict
        or manifest.get("schema") != "jiaoch-trade-cal-authority/v1"
        or manifest.get("authority_scope") != "SSE_TRADING_CALENDAR_WINDOW_ONLY"
        or manifest.get("calendar_authority_status")
        != "NOT_GRANTED_WITHOUT_RETURNED_PUBLICATION_CAPABILITY"
        or manifest.get("calendar_integrity_verified") is not True
        or manifest.get("natural_day_coverage_verified") is not True
        or manifest.get("pretrade_chain_verified") is not True
        or manifest.get("open_sessions_sorted") is not True
        or manifest.get("development_session_alignment_verified") is not False
        or manifest.get("development_session_count_claimed") is not False
        or manifest.get("embargo_consumed") is not False
        or manifest.get("experiment_launch_eligible") is not False
        or manifest.get("final_oos_consumed") is not False
        or manifest.get("formal_materialization_eligible") is not False
        or manifest.get("production_profile_registered") is not False
        or manifest.get("production_recommendation_eligible") is not False
        or manifest.get("rows_published") is not False
        or manifest.get("exchange") != "SSE"
    ):
        raise ValueError("factor-v3 feature history trade calendar manifest rejected")
    start = _strict_iso_date(manifest.get("start_date"), label="trade calendar start")
    end = _strict_iso_date(manifest.get("end_date"), label="trade calendar end")
    if end != _FROZEN_DEVELOPMENT_END or start > _FROZEN_DEVELOPMENT_START:
        raise ValueError("factor-v3 feature history trade calendar window rejected")
    raw_sessions = _strict_sequence(
        manifest.get("open_sessions"),
        label="trade calendar open sessions",
    )
    sessions = [
        _strict_wire_date(value, label="trade calendar open session") for value in raw_sessions
    ]
    count = _strict_positive_int(
        manifest.get("open_session_count"),
        label="trade calendar open session count",
    )
    if count != len(sessions) or sessions != sorted(sessions) or len(set(sessions)) != count:
        raise ValueError("factor-v3 feature history trade calendar sessions rejected")
    root = _strict_sha256(
        manifest.get("open_sessions_root_sha256"),
        label="trade calendar open sessions root",
    )
    expected_root = hashlib.sha256(
        _canonical_bytes(
            {
                "end_date": end.replace("-", ""),
                "exchange": "SSE",
                "open_sessions": [value.replace("-", "") for value in sessions],
                "schema": "jiaoch-trade-cal-open-sessions/v1",
                "start_date": start.replace("-", ""),
            }
        )
    ).hexdigest()
    if not hmac.compare_digest(root, expected_root):
        raise ValueError("factor-v3 feature history trade calendar root rejected")
    if (
        verification.get("calendar_authority_status")
        != "VERIFIED_SINGLE_SEALED_CALL"
        or verification.get("verified") is not True
        or verification.get("exchange") != "SSE"
        or verification.get("start_date") != start
        or verification.get("end_date") != end
        or verification.get("open_session_count") != count
        or verification.get("open_sessions_root_sha256") != root
    ):
        raise ValueError("factor-v3 feature history trade calendar grant rejected")
    return sessions, {
        "calendar_authority_status": verification["calendar_authority_status"],
        "candidate_calendar_authority_status": manifest[
            "calendar_authority_status"
        ],
        "end_date": end,
        "exchange": "SSE",
        "open_session_count": count,
        "open_sessions_root_sha256": root,
        "schema": manifest["schema"],
        "start_date": start,
    }


def _segment_prewindow(
    *,
    prewindow: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    grouped: list[tuple[str, list[str]]] = []
    try:
        for session in prewindow:
            role = classify_date(temporal_partition_contract, session)
            if role not in {"development", "contaminated_diagnostic"}:
                raise PartitionContractError("forbidden feature history role")
            if not grouped or grouped[-1][0] != role:
                grouped.append((role, []))
            grouped[-1][1].append(session)
    except PartitionContractError:
        raise ValueError("factor-v3 feature history partition segmentation rejected") from None
    if [role for role, _sessions in grouped] != [
        "development",
        "contaminated_diagnostic",
    ]:
        raise ValueError("factor-v3 feature history partition segmentation rejected")
    output = []
    for role, sessions in grouped:
        try:
            assert_range_allowed(
                temporal_partition_contract,
                role,
                sessions[0],
                sessions[-1],
                "collect",
            )
            if role == "contaminated_diagnostic":
                assert_range_allowed(
                    temporal_partition_contract,
                    role,
                    sessions[0],
                    sessions[-1],
                    "diagnose",
                )
        except PartitionContractError:
            raise ValueError("factor-v3 feature history partition segmentation rejected") from None
        operations = ["collect_feature_history_only"]
        bindings = {"collect_feature_history_only": "collect"}
        if role == "contaminated_diagnostic":
            operations.append("diagnose_feature_history_quality_only")
            bindings["diagnose_feature_history_quality_only"] = "diagnose"
        output.append(
            {
                "authorized_operations": operations,
                "candidate_rows_generated": False,
                "count": len(sessions),
                "end": sessions[-1],
                "experiment_launch_permitted": False,
                "label_rows_generated": False,
                "partition_operation_bindings": bindings,
                "sessions": list(sessions),
                "sessions_sha256": canonical_sha256(list(sessions)),
                "start": sessions[0],
                "target_rows_generated": False,
                "temporal_role": role,
                "train_backtest_validate_permitted": False,
            }
        )
    return output


def build_factor_v3_feature_history_collection_plan(
    *,
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only permitted 250-session feature-history collection plan."""

    development_sessions = _validated_development_sessions(development_session_refs)
    contract = _validated_partition_contract(temporal_partition_contract)
    publication = _validated_trade_cal_publication(trade_cal_publication)
    manifest, calendar_verification = _load_verified_trade_cal_manifest(
        output_root=trade_cal_output_root,
        trade_cal_publication=publication,
    )
    calendar_sessions, calendar_descriptor = _validated_trade_calendar_manifest(
        manifest,
        calendar_verification,
    )
    try:
        development_start_index = calendar_sessions.index(development_sessions[0])
        development_end_index = calendar_sessions.index(development_sessions[-1])
    except ValueError:
        raise ValueError(
            "factor-v3 feature history development session alignment rejected"
        ) from None
    if (
        calendar_sessions[development_start_index : development_end_index + 1]
        != development_sessions
    ):
        raise ValueError("factor-v3 feature history development session alignment rejected")
    if development_start_index < _REQUIRED_PRIOR_OPEN_SESSIONS:
        raise ValueError("factor-v3 feature history requires exact 250-session prewindow")
    prewindow = calendar_sessions[
        development_start_index - _REQUIRED_PRIOR_OPEN_SESSIONS : development_start_index
    ]
    if (
        len(prewindow) != _REQUIRED_PRIOR_OPEN_SESSIONS
        or not prewindow
        or prewindow[-1] >= development_sessions[0]
    ):
        raise ValueError("factor-v3 feature history requires exact 250-session prewindow")
    segments = _segment_prewindow(
        prewindow=prewindow,
        temporal_partition_contract=contract,
    )
    manifest_sha256 = publication["authority_manifest_sha256"]
    unsigned = {
        "schema_version": "audited-pit-factor-v3-feature-history-plan/v1",
        "purpose": "feature_history_only",
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "trade_calendar_authority": {
            **calendar_descriptor,
            "authority_manifest_created": publication["authority_manifest_created"],
            "authority_manifest_relative_path": publication[
                "authority_manifest_relative_path"
            ],
            "authority_manifest_sha256": manifest_sha256,
            "publication_capability_sha256": hashlib.sha256(
                publication["publication_capability"].encode("utf-8")
            ).hexdigest(),
            "publication_schema": publication["schema"],
            "publication_status": publication["publication_status"],
        },
        "development_sessions": {
            "count": len(development_sessions),
            "end": development_sessions[-1],
            "sessions": development_sessions,
            "sha256": canonical_sha256(development_sessions),
            "start": development_sessions[0],
        },
        "prewindow": {
            "count": len(prewindow),
            "end": prewindow[-1],
            "sessions": prewindow,
            "sha256": canonical_sha256(prewindow),
            "start": prewindow[0],
        },
        "segments": segments,
        "temporal_partition_contract_sha256": contract["contract_sha256"],
        "security_code_transition_contract_sha256": (SECURITY_CODE_TRANSITION_CONTRACT_SHA256),
        "feature_history_route_policy_sha256": (
            _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256
        ),
        "source_authority": {
            **deepcopy(_FROZEN_JIAOCH_SOURCE_AUTHORITY),
            "allowed_network_routes": ["direct", "loopback_http_proxy"],
            "interface_whitelist": list(_REQUIRED_FEATURE_HISTORY_APIS),
        },
        "collector_recipe": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["collector_recipe"]
        ),
        "upstream_scope_policy": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["upstream_scope_policy"]
        ),
        "forbidden_outputs_and_operations": deepcopy(
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT["forbidden_outputs_and_operations"]
        ),
        "safety": deepcopy(_SAFETY),
    }
    return {**unsigned, "plan_sha256": canonical_sha256(unsigned)}


def _plan_source_descriptor(collection_plan: Any) -> Mapping[str, Any]:
    if type(collection_plan) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    descriptor = collection_plan.get("trade_calendar_authority")
    if type(descriptor) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    for field in (
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability_sha256",
        "publication_schema",
        "publication_status",
    ):
        if field not in descriptor:
            raise ValueError("factor-v3 feature history plan rejected")
    return descriptor


def verify_factor_v3_feature_history_collection_plan(
    *,
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and compare the plan using only verified offline authorities."""

    descriptor = _plan_source_descriptor(collection_plan)
    publication = _validated_trade_cal_publication(trade_cal_publication)
    if (
        descriptor["authority_manifest_relative_path"]
        != publication["authority_manifest_relative_path"]
        or descriptor["authority_manifest_sha256"]
        != publication["authority_manifest_sha256"]
        or descriptor["publication_capability_sha256"]
        != hashlib.sha256(
            publication["publication_capability"].encode("utf-8")
        ).hexdigest()
        or descriptor["publication_schema"] != publication["schema"]
        or descriptor["publication_status"] != publication["publication_status"]
    ):
        raise ValueError("factor-v3 feature history trade calendar publication drifted")
    rebuilt = build_factor_v3_feature_history_collection_plan(
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=publication,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    if collection_plan != rebuilt:
        raise ValueError("factor-v3 feature history plan verification rejected")
    return {
        "schema_version": "audited-pit-factor-v3-feature-history-plan-verification/v1",
        "verified": True,
        "plan_sha256": rebuilt["plan_sha256"],
        "prewindow_session_count": rebuilt["prewindow"]["count"],
        "development_session_count": rebuilt["development_sessions"]["count"],
        "feature_history_only": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }


def _verify_plan_self_integrity(collection_plan: Mapping[str, Any]) -> list[str]:
    if (
        type(collection_plan) is dict
        and collection_plan.get("security_code_transition_contract_sha256")
        != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    ):
        raise ValueError("factor-v3 feature history transition binding rejected")
    if (
        type(collection_plan) is not dict
        or collection_plan.get("schema_version") != "audited-pit-factor-v3-feature-history-plan/v1"
        or collection_plan.get("purpose") != "feature_history_only"
        or collection_plan.get("factor_v3_points_contract_sha256")
        != FACTOR_V3_POINTS_CONTRACT_SHA256
        or collection_plan.get("factor_v3_feature_history_authority_contract_sha256")
        != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        or collection_plan.get("feature_history_route_policy_sha256")
        != _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256
        or collection_plan.get("source_authority")
        != {
            **_FROZEN_JIAOCH_SOURCE_AUTHORITY,
            "allowed_network_routes": ["direct", "loopback_http_proxy"],
            "interface_whitelist": list(_REQUIRED_FEATURE_HISTORY_APIS),
        }
        or collection_plan.get("safety") != _SAFETY
    ):
        raise ValueError("factor-v3 feature history plan rejected")
    plan_sha256 = _strict_sha256(
        collection_plan.get("plan_sha256"),
        label="plan sha256",
    )
    unsigned = {key: value for key, value in collection_plan.items() if key != "plan_sha256"}
    if not hmac.compare_digest(canonical_sha256(unsigned), plan_sha256):
        raise ValueError("factor-v3 feature history plan rejected")
    prewindow = collection_plan.get("prewindow")
    if type(prewindow) is not dict:
        raise ValueError("factor-v3 feature history plan rejected")
    sessions = prewindow.get("sessions")
    if (
        not isinstance(sessions, list)
        or len(sessions) != _REQUIRED_PRIOR_OPEN_SESSIONS
        or sessions != sorted(sessions)
        or len(set(sessions)) != len(sessions)
        or prewindow.get("count") != len(sessions)
        or prewindow.get("start") != sessions[0]
        or prewindow.get("end") != sessions[-1]
        or prewindow.get("sha256") != canonical_sha256(sessions)
    ):
        raise ValueError("factor-v3 feature history plan sessions rejected")
    for session in sessions:
        _strict_iso_date(session, label="plan session")
    return sessions


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ValueError("factor-v3 feature history PIT database rejected") from None
    return digest.hexdigest()


def _source_file_descriptor(path: Path, *, relative_path: str) -> dict[str, Any]:
    try:
        stat_before = path.stat()
        raw = path.read_bytes()
        stat_after = path.stat()
    except OSError:
        raise ValueError("factor-v3 feature history producer code rejected") from None
    if (
        path.is_symlink()
        or not path.is_file()
        or stat_before.st_size <= 0
        or stat_before.st_size != len(raw)
        or stat_after.st_size != stat_before.st_size
        or stat_after.st_mtime_ns != stat_before.st_mtime_ns
    ):
        raise ValueError("factor-v3 feature history producer code rejected")
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": stat_after.st_size,
    }


def _code_object_descriptor(code: types.CodeType) -> dict[str, Any]:
    constants = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            constants.append({"code": _code_object_descriptor(value)})
        elif value is None or type(value) in {bool, int, float, str, bytes}:
            constants.append(
                {
                    "type": type(value).__name__,
                    "value": value.hex() if type(value) is bytes else value,
                }
            )
        else:
            constants.append({"type": type(value).__name__})
    return {
        "argcount": code.co_argcount,
        "bytecode_sha256": hashlib.sha256(code.co_code).hexdigest(),
        "cellvars": list(code.co_cellvars),
        "constants": constants,
        "flags": code.co_flags,
        "freevars": list(code.co_freevars),
        "kwonlyargcount": code.co_kwonlyargcount,
        "names": list(code.co_names),
        "posonlyargcount": code.co_posonlyargcount,
        "varnames": list(code.co_varnames),
    }


def _canonical_loaded_module_name(
    value: str, *, canonical_module_name: str | None
) -> str:
    if canonical_module_name is not None and value == "__main__":
        return canonical_module_name
    return value


def _runtime_identity_descriptor(
    value: Any, *, canonical_module_name: str | None = None
) -> dict[str, Any]:
    if value is None or type(value) in {bool, int, float, str, bytes}:
        return {
            "type": type(value).__name__,
            "value": value.hex() if type(value) is bytes else value,
        }
    if type(value) in {list, tuple}:
        return {
            "items": [
                _runtime_identity_descriptor(
                    item, canonical_module_name=canonical_module_name
                )
                for item in value
            ],
            "type": type(value).__name__,
        }
    if type(value) is dict and all(type(key) is str for key in value):
        return {
            "items": {
                key: _runtime_identity_descriptor(
                    item, canonical_module_name=canonical_module_name
                )
                for key, item in sorted(value.items())
            },
            "type": "dict",
        }
    if type(value) in {set, frozenset}:
        return {
            "items": sorted(
                canonical_sha256(
                    _runtime_identity_descriptor(
                        item, canonical_module_name=canonical_module_name
                    )
                )
                for item in value
            ),
            "type": type(value).__name__,
        }
    if inspect.isfunction(value):
        return {
            "code": _code_object_descriptor(value.__code__),
            "module": _canonical_loaded_module_name(
                value.__module__, canonical_module_name=canonical_module_name
            ),
            "qualname": value.__qualname__,
            "type": "function",
        }
    if inspect.isclass(value):
        return {
            "module": _canonical_loaded_module_name(
                value.__module__, canonical_module_name=canonical_module_name
            ),
            "qualname": value.__qualname__,
            "type": "class",
        }
    return {
        "module": _canonical_loaded_module_name(
            type(value).__module__, canonical_module_name=canonical_module_name
        ),
        "qualname": type(value).__qualname__,
        "type": "opaque",
    }


def _function_identity_descriptor(
    value: Any, *, canonical_module_name: str | None = None
) -> dict[str, Any]:
    closure = value.__closure__
    return {
        "closure": (
            []
            if closure is None
            else [
                _runtime_identity_descriptor(
                    cell.cell_contents, canonical_module_name=canonical_module_name
                )
                for cell in closure
            ]
        ),
        "code": _code_object_descriptor(value.__code__),
        "defaults": _runtime_identity_descriptor(
            value.__defaults__, canonical_module_name=canonical_module_name
        ),
        "defined_in": _canonical_loaded_module_name(
            value.__module__, canonical_module_name=canonical_module_name
        ),
        "kwdefaults": _runtime_identity_descriptor(
            value.__kwdefaults__, canonical_module_name=canonical_module_name
        ),
        "qualname": value.__qualname__,
    }


def _loaded_module_descriptor(
    module: Any, *, canonical_module_name: str | None = None
) -> dict[str, Any]:
    bindings = []
    for name, value in sorted(vars(module).items()):
        if inspect.isfunction(value):
            bindings.append(
                {
                    "binding": name,
                    **_function_identity_descriptor(
                        value, canonical_module_name=canonical_module_name
                    ),
                }
            )
        elif inspect.isclass(value) and (
            str(value.__module__).startswith("app.") or value.__module__ == module.__name__
        ):
            methods = []
            for method_name, raw in sorted(vars(value).items()):
                candidate = raw
                if isinstance(raw, (classmethod, staticmethod)):
                    candidate = raw.__func__
                if inspect.isfunction(candidate):
                    methods.append(
                        {
                            "name": method_name,
                            **_function_identity_descriptor(
                                candidate, canonical_module_name=canonical_module_name
                            ),
                        }
                    )
            bindings.append(
                {
                    "binding": name,
                    "class_module": _canonical_loaded_module_name(
                        value.__module__, canonical_module_name=canonical_module_name
                    ),
                    "class_qualname": value.__qualname__,
                    "methods": methods,
                }
            )
    runtime_constants = {
        name: _runtime_identity_descriptor(
            value, canonical_module_name=canonical_module_name
        )
        for name, value in sorted(vars(module).items())
        if name.isupper()
    }
    module_path = Path(str(module.__file__)).resolve(strict=True)
    return {
        "bindings_root_sha256": canonical_sha256(bindings),
        "module": _canonical_loaded_module_name(
            module.__name__, canonical_module_name=canonical_module_name
        ),
        "module_file_sha256": _file_sha256(module_path),
        "module_file": str(module_path),
        "runtime_constants_root_sha256": canonical_sha256(runtime_constants),
    }


def _loaded_producer_module(*, path: Path) -> types.ModuleType:
    canonical_module_name = f"app.{path.stem}"
    module = importlib.import_module(canonical_module_name)
    active = sys.modules.get("__main__")
    active_file = getattr(active, "__file__", None)
    active_matches_producer = False
    if isinstance(active, types.ModuleType) and isinstance(active_file, str):
        try:
            active_matches_producer = (
                Path(active_file).resolve(strict=True) == path.resolve(strict=True)
            )
        except (OSError, RuntimeError, ValueError):
            pass
    if active_matches_producer:
        active_descriptor = _loaded_module_descriptor(
            active, canonical_module_name=canonical_module_name
        )
        module_descriptor = _loaded_module_descriptor(
            module, canonical_module_name=canonical_module_name
        )
        if not hmac.compare_digest(
            canonical_sha256(active_descriptor), canonical_sha256(module_descriptor)
        ):
            raise ValueError("factor-v3 feature history duplicate runner runtime rejected")
    return module


def _producer_binding() -> dict[str, Any]:
    source_root = Path(__file__).resolve().parent
    source_descriptors = []
    loaded_descriptors = []
    for filename in _PRODUCER_FILES:
        path = source_root / filename
        source_descriptors.append(
            _source_file_descriptor(path, relative_path=f"app/{filename}")
        )
        module = _loaded_producer_module(path=path)
        loaded_descriptors.append(
            _loaded_module_descriptor(
                module, canonical_module_name=f"app.{path.stem}"
            )
        )
    payload = {
        "critical_runtime_constants": {
            "factor_v3_feature_history_authority_contract_sha256": (
                FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
            ),
            "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
            "feature_history_route_policy_sha256": (
                _FROZEN_FEATURE_HISTORY_ROUTE_POLICY_SHA256
            ),
            "frozen_jiaoch_source_authority": deepcopy(
                _FROZEN_JIAOCH_SOURCE_AUTHORITY
            ),
            "security_code_transition_contract_sha256": (
                SECURITY_CODE_TRANSITION_CONTRACT_SHA256
            ),
        },
        "loaded_execution_root_sha256": canonical_sha256(loaded_descriptors),
        "source_manifest_root_sha256": canonical_sha256(source_descriptors),
        "schema_version": "factor-v3-feature-history-producer-binding/v2",
    }
    return {**payload, "root_sha256": canonical_sha256(payload)}


def _validated_expected_producer_binding(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "critical_runtime_constants",
        "loaded_execution_root_sha256",
        "root_sha256",
        "schema_version",
        "source_manifest_root_sha256",
    }:
        raise ValueError("factor-v3 feature history producer binding rejected")
    if value.get("schema_version") != "factor-v3-feature-history-producer-binding/v2":
        raise ValueError("factor-v3 feature history producer binding rejected")
    root_sha256 = _strict_sha256(
        value.get("root_sha256"),
        label="feature history producer binding root",
    )
    unsigned = {key: item for key, item in value.items() if key != "root_sha256"}
    if not hmac.compare_digest(root_sha256, canonical_sha256(unsigned)):
        raise ValueError("factor-v3 feature history producer binding rejected")
    return deepcopy(value)


def _producer_code_root_sha256() -> str:
    return _producer_binding()["root_sha256"]


def _factor_v3_feature_history_attestation_binding(
    *,
    collection_publication: Mapping[str, Any],
    collection_publication_output_root: str | Path,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    feature_history_run_spec_path: str | Path,
    feature_history_run_spec_sha256: str,
    feature_history_run_root: str | Path,
    manifest: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    publication = _validated_collection_publication(collection_publication)
    issuance = _collection_publication_issuance(publication)
    public_publication = {
        key: value
        for key, value in publication.items()
        if key != "publication_capability"
    }
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    publication_root = _safe_collection_output_root(collection_publication_output_root)
    trade_cal_root = Path(trade_cal_output_root).resolve(strict=True)
    spec_raw = spec_path.read_bytes()
    if len(spec_raw) > 4 * 1024 * 1024:
        raise ValueError("factor-v3 feature history run spec rejected")
    identity = {
        "collection_plan_sha256": collection_plan["plan_sha256"],
        "collection_publication": public_publication,
        "collection_publication_capability_sha256": issuance[
            "publication_capability_sha256"
        ],
        "collection_publication_issuance_relative_path": (
            _collection_issuance_relative_path(issuance)
        ),
        "collection_publication_issuance_sha256": hashlib.sha256(
            _canonical_bytes(issuance)
        ).hexdigest(),
        "collection_publication_output_root": str(publication_root),
        "development_session_refs_sha256": canonical_sha256(
            _validated_development_sessions(development_session_refs)
        ),
        "feature_history_run_root": str(run_root),
        "feature_history_run_spec_file_sha256": hashlib.sha256(spec_raw).hexdigest(),
        "feature_history_run_spec_path": str(spec_path),
        "feature_history_run_spec_sha256": _strict_sha256(
            feature_history_run_spec_sha256,
            label="feature run spec sha256",
        ),
        "manifest_identity_sha256": canonical_sha256(dict(manifest)),
        "manifest_relative_path": publication["authority_manifest_relative_path"],
        "manifest_sha256": publication["authority_manifest_sha256"],
        "pit_store_database_sha256": receipt["pit_store_database_sha256"],
        "receipt": dict(receipt),
        "receipt_sha256": receipt["receipt_sha256"],
        "schema": "factor-v3-feature-history-attestation-authority-binding/v1",
        "session_authority_refs_sha256": receipt[
            "session_authority_refs_sha256"
        ],
        "session_count": receipt["session_count"],
        "sessions_sha256": receipt["sessions_sha256"],
        "snapshot_index_sha256": receipt["snapshot_index_sha256"],
        "source_authority_root_sha256": receipt["source_authority_root_sha256"],
        "temporal_partition_contract_sha256": canonical_sha256(
            _validated_partition_contract(temporal_partition_contract)
        ),
        "trade_cal_output_root": str(trade_cal_root),
        "trade_cal_publication_sha256": canonical_sha256(dict(trade_cal_publication)),
    }
    return {**identity, "binding_sha256": canonical_sha256(identity)}


def _validated_session_authority_refs(
    value: Any,
    *,
    sessions: Sequence[str],
) -> list[dict[str, Any]]:
    refs = _strict_sequence(value, label="session authority refs")
    if len(refs) != len(sessions):
        raise ValueError("factor-v3 feature history session authority coverage rejected")
    output = []
    for expected_session, raw in zip(sessions, refs, strict=True):
        ref = _strict_mapping(
            raw,
            fields=_SESSION_AUTHORITY_REF_FIELDS,
            label="session authority ref",
        )
        trade_date = _strict_iso_date(
            ref.get("trade_date"),
            label="session authority trade_date",
        )
        if trade_date != expected_session:
            raise ValueError("factor-v3 feature history session authority order rejected")
        for field in (
            "bak_basic_normalized_rows_sha256",
            "bak_basic_raw_sha256",
            "bak_basic_receipt_sha256",
            "daily_rows_root_sha256",
            "market_generation_lineage_sha256",
            "market_generation_manifest_sha256",
            "suspend_d_rows_root_sha256",
        ):
            _strict_sha256(ref.get(field), label=f"session authority {field}")
        _strict_positive_int(
            ref.get("bak_basic_row_count"),
            label="session authority bak_basic row count",
        )
        generation_id = ref.get("market_generation_id")
        if type(generation_id) is not str or not generation_id:
            raise ValueError("factor-v3 feature history session authority generation rejected")
        output.append(deepcopy(ref))
    return output


def _board_for_membership_row(row: Mapping[str, Any]) -> str:
    code = str(row.get("ts_code") or "")
    exchange = str(row.get("exchange") or "")
    symbol, separator, suffix = code.partition(".")
    if len(symbol) != 6 or not symbol.isdigit() or separator != ".":
        raise ValueError("factor-v3 feature history upstream scope rejected")
    if exchange == "BSE" and suffix == "BJ":
        return "beijing"
    if exchange == "SSE" and suffix == "SH":
        if symbol.startswith(("688", "689")):
            return "science_technology"
        return "mainboard"
    if exchange == "SZSE" and suffix == "SZ":
        if symbol.startswith(("300", "301")):
            return "chinext"
        return "mainboard"
    raise ValueError("factor-v3 feature history upstream scope rejected")


def _receipt_semantics_sha256(receipt: Mapping[str, Any]) -> str:
    try:
        params = json.loads(receipt["params_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("factor-v3 feature history PIT receipt rejected") from None
    return canonical_sha256(
        {
            "dataset": receipt["dataset"],
            "partition_key": receipt["partition_key"],
            "endpoint": receipt["endpoint"],
            "params": params,
            "retrieved_at": receipt["retrieved_at"],
            "http_status": receipt["http_status"],
            "raw_path": receipt["raw_path"],
            "raw_sha256": receipt["raw_sha256"],
            "raw_bytes": receipt["raw_bytes"],
            "response_code": receipt["response_code"],
            "response_message": receipt["response_message"],
            "row_cap": receipt["row_cap"],
            "row_count": receipt["row_count"],
            "normalized_sha256": receipt["normalized_sha256"],
            "parser_version": receipt["parser_version"],
        }
    )


def _validated_feature_history_attempt_route(
    attempt: Mapping[str, Any],
    *,
    dataset: str,
    trade_date: str,
) -> str:
    try:
        semantics = json.loads(attempt["request_semantics_json"])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("factor-v3 feature history route policy rejected") from None
    if (
        type(semantics) is not dict
        or _canonical_bytes(semantics).decode("utf-8")
        != attempt.get("request_semantics_json")
        or semantics.get("dataset") != dataset
        or semantics.get("partition_key") != trade_date
        or semantics.get("api_name") != dataset
        or semantics.get("credential_slot_id") != "points-primary"
        or semantics.get("credential_route_purpose")
        != "factor-v3-feature-history"
        or semantics.get("credential_route_id")
        != f"feature-history:points-primary:{dataset}"
    ):
        raise ValueError("factor-v3 feature history route policy rejected")
    return _strict_uuid4(
        semantics.get("credential_generation_id"),
        label="feature history credential generation",
    )


def _exact_membership_authority_on_connection(
    *,
    store: Any,
    connection: sqlite3.Connection,
    trade_date: str,
    expected_temporal_role: str,
    expected_temporal_contract_sha256: str,
    receipt_ref_sha256: str,
) -> dict[str, Any]:
    receipt_rows = list(
        connection.execute(
            """
            SELECT * FROM receipts
            WHERE dataset='bak_basic' AND partition_key=?
            """,
            (trade_date,),
        )
    )
    if len(receipt_rows) != 1:
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic receipt rejected"
        )
    receipt = dict(receipt_rows[0])
    if (
        receipt.get("dataset") != "bak_basic"
        or receipt.get("partition_key") != trade_date
        or receipt.get("endpoint") != "bak_basic"
        or int(receipt.get("response_code", -1)) != 0
        or int(receipt.get("row_count", 0)) <= 0
        or not hmac.compare_digest(
            _receipt_semantics_sha256(receipt),
            receipt_ref_sha256,
        )
    ):
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic receipt rejected"
        )
    derived_membership = connection.execute(
        """
        SELECT 1 FROM membership_session_generations
        WHERE trade_date=? LIMIT 1
        """,
        (trade_date,),
    ).fetchone()
    membership_head = connection.execute(
        "SELECT 1 FROM membership_session_head WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    if derived_membership is not None or membership_head is not None:
        raise ValueError(
            "factor-v3 feature history carry or quarantine membership rejected"
        )
    normalized_rows = store._normalized_rows_from_db(
        connection,
        "bak_basic",
        trade_date,
    )
    if (
        len(normalized_rows) != int(receipt["row_count"])
        or not hmac.compare_digest(
            canonical_sha256(normalized_rows),
            str(receipt["normalized_sha256"]),
        )
        or any(row.get("trade_date") != trade_date for row in normalized_rows)
    ):
        raise ValueError(
            "factor-v3 feature history exact nonempty bak_basic rows rejected"
        )
    board_counts = {field: 0 for field in sorted(_BOARD_COUNT_FIELDS)}
    for row in normalized_rows:
        board_counts[_board_for_membership_row(row)] += 1
    if (
        sum(board_counts.values()) != len(normalized_rows)
        or board_counts["science_technology"] <= 0
        or board_counts["beijing"] <= 0
    ):
        raise ValueError("factor-v3 feature history upstream scope rejected")

    attempt_rows = list(
        connection.execute(
            """
            SELECT attempt.*, event.status AS terminal_status,
                   event.details_json AS terminal_details_json,
                   event.recorded_at AS terminal_recorded_at
            FROM fetch_attempts AS attempt
            LEFT JOIN fetch_promotion_events AS event
              ON event.attempt_id=attempt.attempt_id
            WHERE attempt.dataset='bak_basic' AND attempt.partition_key=?
            ORDER BY attempt.attempt_sequence
            """,
            (trade_date,),
        )
    )
    if not attempt_rows:
        raise ValueError("factor-v3 feature history PIT membership lineage rejected")
    linked_attempts = []
    source_authority = None
    credential_generation_id = None
    for stored in attempt_rows:
        attempt = dict(stored)
        attempt_generation_id = _validated_feature_history_attempt_route(
            attempt,
            dataset="bak_basic",
            trade_date=trade_date,
        )
        if credential_generation_id is None:
            credential_generation_id = attempt_generation_id
        elif credential_generation_id != attempt_generation_id:
            raise ValueError("factor-v3 feature history credential generation rejected")
        source, role, contract = store._membership_attempt_authority(
            attempt,
            dataset="bak_basic",
            partition_key=trade_date,
        )
        if (
            role != expected_temporal_role
            or contract != expected_temporal_contract_sha256
        ):
            raise ValueError(
                "factor-v3 feature history PIT membership temporal authority rejected"
            )
        if source_authority is None:
            source_authority = source
        elif source != source_authority:
            raise ValueError(
                "factor-v3 feature history PIT membership source authority rejected"
            )
        if attempt.get("terminal_status") == "invalid_json":
            raise ValueError(
                "factor-v3 feature history exact nonempty bak_basic semantic empty rejected"
            )
        details_raw = attempt.get("terminal_details_json")
        try:
            details = json.loads(details_raw) if details_raw is not None else None
        except (TypeError, json.JSONDecodeError):
            raise ValueError(
                "factor-v3 feature history PIT membership event rejected"
            ) from None
        if details is not None and _canonical_bytes(details).decode("utf-8") != details_raw:
            raise ValueError("factor-v3 feature history PIT membership event rejected")
        raw_link = attempt.get("raw_sha256") == receipt["raw_sha256"]
        event_link = (
            type(details) is dict
            and details.get("receipt_raw_sha256") == receipt["raw_sha256"]
        )
        if raw_link or event_link:
            if (
                attempt.get("terminal_status") not in {"stored", "reused"}
                or not raw_link
                or not event_link
                or attempt.get("error_kind") is not None
                or int(attempt.get("body_complete", 0)) != 1
                or attempt.get("http_status") is None
                or not 200 <= int(attempt["http_status"]) < 300
                or int(attempt.get("raw_bytes", -1)) != int(receipt["raw_bytes"])
                or store._verify_raw_file(attempt)
                != store._verify_raw_file(receipt)
            ):
                raise ValueError(
                    "factor-v3 feature history PIT membership lineage rejected"
                )
            linked_attempts.append(
                {
                    "attempt_id": str(attempt["attempt_id"]),
                    "raw_sha256": str(attempt["raw_sha256"]),
                    "request_semantics_sha256": str(
                        attempt["request_semantics_sha256"]
                    ),
                    "terminal_status": str(attempt["terminal_status"]),
                }
            )
    if (
        not linked_attempts
        or source_authority is None
        or credential_generation_id is None
    ):
        raise ValueError("factor-v3 feature history PIT membership lineage rejected")
    return {
        "attempt_refs": linked_attempts,
        "board_counts": board_counts,
        "normalized_rows_root_sha256": canonical_sha256(normalized_rows),
        "receipt": receipt,
        "receipt_ref_sha256": receipt_ref_sha256,
        "credential_generation_id": credential_generation_id,
        "source_authority": _validated_frozen_jiaoch_source_authority(
            source_authority
        ),
    }


def _validated_market_attempt_routes_on_connection(
    *,
    store: Any,
    connection: sqlite3.Connection,
    trade_date: str,
    expected_temporal_role: str,
    expected_temporal_contract_sha256: str,
) -> str:
    credential_generation_id = None
    for dataset in _REQUIRED_MARKET_SHARDS:
        attempts = list(
            connection.execute(
                """
                SELECT * FROM fetch_attempts
                WHERE dataset=? AND partition_key=?
                ORDER BY attempt_sequence
                """,
                (dataset, trade_date),
            )
        )
        if not attempts:
            raise ValueError("factor-v3 feature history route policy rejected")
        for stored in attempts:
            attempt = dict(stored)
            attempt_generation_id = _validated_feature_history_attempt_route(
                attempt,
                dataset=dataset,
                trade_date=trade_date,
            )
            if credential_generation_id is None:
                credential_generation_id = attempt_generation_id
            elif credential_generation_id != attempt_generation_id:
                raise ValueError("factor-v3 feature history credential generation rejected")
            source, role, contract = store._membership_attempt_authority(
                attempt,
                dataset=dataset,
                partition_key=trade_date,
            )
            if (
                role != expected_temporal_role
                or contract != expected_temporal_contract_sha256
            ):
                raise ValueError(
                    "factor-v3 feature history market temporal authority rejected"
                )
            _validated_frozen_jiaoch_source_authority(source)
    if credential_generation_id is None:
        raise ValueError("factor-v3 feature history credential generation rejected")
    return credential_generation_id


def _validate_closed_collection_store_scope(
    *,
    connection: sqlite3.Connection,
    sessions: Sequence[str],
) -> None:
    placeholders = ",".join("?" for _ in sessions)
    allowed_datasets = ("bak_basic", *_REQUIRED_MARKET_SHARDS)
    dataset_placeholders = ",".join("?" for _ in allowed_datasets)
    forbidden_attempt = connection.execute(
        f"""
        SELECT 1 FROM fetch_attempts
        WHERE dataset NOT IN ({dataset_placeholders})
           OR partition_key NOT IN ({placeholders})
        LIMIT 1
        """,
        (*allowed_datasets, *sessions),
    ).fetchone()
    forbidden_receipt = connection.execute(
        f"""
        SELECT 1 FROM receipts
        WHERE dataset != 'bak_basic' OR partition_key NOT IN ({placeholders})
        LIMIT 1
        """,
        tuple(sessions),
    ).fetchone()
    unexpected_generation = any(
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        for table in (
            "membership_session_generations",
            "membership_session_head",
            "stock_basic_generations",
            "etf_proxy_generations",
        )
    )
    market_generation_count = int(
        connection.execute("SELECT COUNT(*) FROM market_session_generations").fetchone()[0]
    )
    forbidden_market_generation = connection.execute(
        f"""
        SELECT 1 FROM market_session_generations
        WHERE trade_date NOT IN ({placeholders})
        LIMIT 1
        """,
        tuple(sessions),
    ).fetchone()
    if (
        forbidden_attempt is not None
        or forbidden_receipt is not None
        or unexpected_generation
        or market_generation_count != len(sessions)
        or forbidden_market_generation is not None
    ):
        raise ValueError("factor-v3 feature history PIT store market generation scope rejected")


def _market_authority_on_connection(
    *,
    store: Any,
    connection: sqlite3.Connection,
    trade_date: str,
    expected_temporal_role: str,
    expected_temporal_contract_sha256: str,
) -> dict[str, Any]:
    if (
        connection.execute(
            """
            SELECT 1 FROM market_session_generation_head
            WHERE trade_date=?
            """,
            (trade_date,),
        ).fetchone()
        is None
    ):
        raise ValueError(
            "factor-v3 feature history market generation coverage rejected"
        )
    market = store._membership_market_authority_on_connection(
        connection,
        trade_date,
    )
    generation, verified, source, role, contract = market
    manifest = verified["manifest"]
    shards = manifest.get("shards")
    if (
        generation.get("status") != "published"
        or generation.get("vintage") != "historical_backfill"
        or generation.get("final_oos_eligible") is not False
        or role != expected_temporal_role
        or contract != expected_temporal_contract_sha256
        or not hmac.compare_digest(
            str(generation.get("lineage_sha256") or ""),
            str(verified["lineage_sha256"]),
        )
        or manifest.get("final_oos_eligible") is not False
        or not isinstance(shards, list)
        or [shard.get("dataset") for shard in shards]
        != sorted(_REQUIRED_MARKET_SHARDS)
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    shard_counts = {
        str(shard["dataset"]): int(shard["row_count"]) for shard in shards
    }
    if (
        any(shard_counts[dataset] <= 0 for dataset in _NONEMPTY_MARKET_SHARDS)
        or shard_counts["suspend_d"] < 0
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    dataset_roots = manifest.get("dataset_roots")
    if type(dataset_roots) is not dict or set(dataset_roots) != set(
        _REQUIRED_MARKET_SHARDS
    ):
        raise ValueError("factor-v3 feature history market generation rejected")
    for dataset in _REQUIRED_MARKET_SHARDS:
        _strict_sha256(
            dataset_roots.get(dataset),
            label=f"{dataset} market generation rows root",
        )
    return {
        "dataset_roots": deepcopy(dataset_roots),
        "generation": generation,
        "manifest_sha256": verified["manifest_sha256"],
        "lineage_sha256": verified["lineage_sha256"],
        "shard_counts": shard_counts,
        "source_authority": _validated_frozen_jiaoch_source_authority(source),
    }


def _verified_raw_artifact_set(
    *,
    store: Any,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    from app.research_pit_store import PITReceiptError

    records_by_path: dict[str, dict[str, Any]] = {}
    for table in ("receipts", "fetch_attempts"):
        if table == "fetch_attempts":
            rows = connection.execute(
                """
                SELECT attempt.dataset, attempt.raw_path, attempt.raw_sha256,
                       attempt.raw_bytes, event.status AS terminal_status,
                       event.details_json AS terminal_details_json
                FROM fetch_attempts AS attempt
                LEFT JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                WHERE attempt.raw_path IS NOT NULL AND attempt.raw_path != ''
                ORDER BY attempt.raw_path
                """
            )
        else:
            rows = connection.execute(
                """
                SELECT dataset, raw_path, raw_sha256, raw_bytes
                FROM receipts
                WHERE raw_path IS NOT NULL AND raw_path != ''
                ORDER BY raw_path
                """
            )
        for stored in rows:
            record = dict(stored)
            if table == "fetch_attempts":
                try:
                    details = json.loads(record["terminal_details_json"])
                except (TypeError, json.JSONDecodeError):
                    raise ValueError(
                        "factor-v3 feature history raw artifact drifted"
                    ) from None
                if (
                    type(details) is not dict
                    or _canonical_bytes(details).decode("utf-8")
                    != record["terminal_details_json"]
                ):
                    raise ValueError("factor-v3 feature history raw artifact drifted")
                terminal_status = record.get("terminal_status")
                terminal_raw_sha256 = (
                    details.get("receipt_raw_sha256")
                    if terminal_status in {"stored", "reused"}
                    else details.get("raw_sha256")
                    if terminal_status == "market_session_staged"
                    else None
                )
                if terminal_status in {"stored", "reused", "market_session_staged"} and (
                    terminal_raw_sha256 != record.get("raw_sha256")
                ):
                    raise ValueError("factor-v3 feature history raw artifact drifted")
                if terminal_status in {
                    "transport_error",
                    "http_error",
                    "api_error",
                    "invalid_json",
                    "clock_error",
                } and (
                    set(details) != {"error_kind"}
                    or type(details["error_kind"]) is not str
                    or not details["error_kind"]
                ):
                    raise ValueError("factor-v3 feature history raw artifact drifted")
                if terminal_status not in {
                    "stored",
                    "reused",
                    "market_session_staged",
                    "transport_error",
                    "http_error",
                    "api_error",
                    "invalid_json",
                    "clock_error",
                }:
                    raise ValueError("factor-v3 feature history raw artifact drifted")
            raw_path = record.get("raw_path")
            if type(raw_path) is not str or not raw_path:
                raise ValueError("factor-v3 feature history raw artifact drifted")
            previous = records_by_path.setdefault(raw_path, record)
            if any(
                previous.get(field) != record.get(field)
                for field in ("dataset", "raw_sha256", "raw_bytes")
            ):
                raise ValueError("factor-v3 feature history raw artifact drifted")
    raw_root = Path(store.raw_root)
    try:
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise OSError
        actual_paths = set()
        for candidate in raw_root.rglob("*"):
            if candidate.is_symlink():
                raise OSError
            if candidate.is_file():
                actual_paths.add(candidate.relative_to(store.root).as_posix())
    except (OSError, ValueError):
        raise ValueError("factor-v3 feature history raw artifact drifted") from None
    if actual_paths != set(records_by_path):
        raise ValueError("factor-v3 feature history raw artifact drifted")
    descriptors = []
    for raw_path, record in sorted(records_by_path.items()):
        path = Path(store.root, raw_path)
        try:
            stat_before = path.stat()
            store._verify_raw_file(record)
            stat_after = path.stat()
        except (OSError, PITReceiptError, ValueError, KeyError, TypeError):
            raise ValueError("factor-v3 feature history raw artifact drifted") from None
        if (
            stat_before.st_size != stat_after.st_size
            or stat_before.st_mtime_ns != stat_after.st_mtime_ns
        ):
            raise ValueError("factor-v3 feature history raw artifact drifted")
        descriptors.append(
            {
                "raw_bytes": int(record["raw_bytes"]),
                "raw_path": raw_path,
                "raw_sha256": str(record["raw_sha256"]),
            }
        )
    return {
        "count": len(descriptors),
        "sha256": canonical_sha256(descriptors),
    }


def _replay_pit_store(
    *,
    pit_store_root: str | Path,
    expected_database_sha256: str,
    sessions: Sequence[str],
    temporal_partition_contract: Mapping[str, Any],
) -> dict[str, Any]:
    from app.research_pit_store import (
        AuditedPointInTimeUniverse,
        PITReceiptError,
        PITReceiptStore,
    )

    root = Path(pit_store_root)
    database_path = root / "metadata.sqlite3"
    if (
        root.is_symlink()
        or not root.is_dir()
        or database_path.is_symlink()
        or not database_path.is_file()
        or database_path.resolve().parent != root.resolve()
    ):
        raise ValueError("factor-v3 feature history PIT store rejected")
    sidecars = (Path(f"{database_path}-wal"), Path(f"{database_path}-shm"))
    if any(path.exists() for path in sidecars):
        raise ValueError(
            "factor-v3 feature history PIT store must be finalized without WAL or SHM"
        )
    expected = _strict_sha256(
        expected_database_sha256,
        label="PIT store database sha256",
    )
    stat_before = database_path.stat()
    if stat_before.st_size <= 0 or not hmac.compare_digest(
        _file_sha256(database_path),
        expected,
    ):
        raise ValueError("factor-v3 feature history PIT database anchor rejected")
    store = PITReceiptStore.__new__(PITReceiptStore)
    store.root = root.resolve()
    store.raw_root = store.root / "raw"
    store.database_path = database_path.resolve()
    connection = None
    try:
        connection = AuditedPointInTimeUniverse._readonly_connection(
            store.database_path
        )
        receipt_verification = store._verify_receipts_on_connection(connection)
        receipt_ref_map = {
            (row["dataset"], row["partition_key"]): row["receipt_sha256"]
            for row in receipt_verification["receipt_refs"]
        }
        _validate_closed_collection_store_scope(
            connection=connection,
            sessions=sessions,
        )
        raw_artifacts_before = _verified_raw_artifact_set(
            store=store,
            connection=connection,
        )
        session_refs = []
        source_descriptors = []
        board_descriptors = []
        collection_credential_generation_id = None
        for trade_date in sessions:
            role = classify_date(temporal_partition_contract, trade_date)
            if role not in {"development", "contaminated_diagnostic"}:
                raise ValueError(
                    "factor-v3 feature history PIT temporal authority rejected"
                )
            membership_receipt_ref = receipt_ref_map.get(("bak_basic", trade_date))
            if membership_receipt_ref is None:
                raise ValueError(
                    "factor-v3 feature history exact nonempty bak_basic receipt rejected"
                )
            membership = _exact_membership_authority_on_connection(
                store=store,
                connection=connection,
                trade_date=trade_date,
                expected_temporal_role=role,
                expected_temporal_contract_sha256=temporal_partition_contract[
                    "contract_sha256"
                ],
                receipt_ref_sha256=membership_receipt_ref,
            )
            market = _market_authority_on_connection(
                store=store,
                connection=connection,
                trade_date=trade_date,
                expected_temporal_role=role,
                expected_temporal_contract_sha256=temporal_partition_contract[
                    "contract_sha256"
                ],
            )
            market_credential_generation_id = _validated_market_attempt_routes_on_connection(
                store=store,
                connection=connection,
                trade_date=trade_date,
                expected_temporal_role=role,
                expected_temporal_contract_sha256=temporal_partition_contract[
                    "contract_sha256"
                ],
            )
            if (
                membership["source_authority"] != market["source_authority"]
                or membership["credential_generation_id"]
                != market_credential_generation_id
            ):
                raise ValueError(
                    "factor-v3 feature history PIT source authority rejected"
                )
            if collection_credential_generation_id is None:
                collection_credential_generation_id = market_credential_generation_id
            elif collection_credential_generation_id != market_credential_generation_id:
                raise ValueError("factor-v3 feature history credential generation rejected")
            receipt = membership["receipt"]
            session_ref = {
                "trade_date": trade_date,
                "bak_basic_receipt_sha256": membership["receipt_ref_sha256"],
                "bak_basic_raw_sha256": receipt["raw_sha256"],
                "bak_basic_normalized_rows_sha256": receipt["normalized_sha256"],
                "bak_basic_row_count": int(receipt["row_count"]),
                "market_generation_id": str(
                    market["generation"]["generation_id"]
                ),
                "market_generation_manifest_sha256": market["manifest_sha256"],
                "market_generation_lineage_sha256": market["lineage_sha256"],
                "daily_rows_root_sha256": market["dataset_roots"]["daily"],
                "suspend_d_rows_root_sha256": market["dataset_roots"]["suspend_d"],
            }
            session_refs.append(session_ref)
            source_descriptors.append(
                {
                    "trade_date": trade_date,
                    "source_authority": membership["source_authority"],
                    "credential_generation_id": market_credential_generation_id,
                    "temporal_role": role,
                    "temporal_contract_sha256": temporal_partition_contract[
                        "contract_sha256"
                    ],
                    "membership_attempt_refs": membership["attempt_refs"],
                    "session_authority_ref": session_ref,
                }
            )
            board_descriptors.append(
                {
                    "trade_date": trade_date,
                    "board_counts": membership["board_counts"],
                    "source_rows_sha256": membership[
                        "normalized_rows_root_sha256"
                    ],
                }
            )
        if collection_credential_generation_id is None:
            raise ValueError("factor-v3 feature history credential generation rejected")
        raw_artifacts_after = _verified_raw_artifact_set(
            store=store,
            connection=connection,
        )
        if raw_artifacts_after != raw_artifacts_before:
            raise ValueError("factor-v3 feature history raw artifact drifted")
    except (PITReceiptError, sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "factor-v3 feature history"
        ):
            raise
        raise ValueError(
            "factor-v3 feature history PIT store replay rejected"
        ) from None
    finally:
        if connection is not None:
            connection.close()
    if any(path.exists() for path in sidecars):
        raise ValueError(
            "factor-v3 feature history PIT store must be finalized without WAL or SHM"
        )
    stat_after = database_path.stat()
    if (
        stat_after.st_size != stat_before.st_size
        or stat_after.st_mtime_ns != stat_before.st_mtime_ns
        or not hmac.compare_digest(_file_sha256(database_path), expected)
    ):
        raise ValueError("factor-v3 feature history PIT database drifted")
    terminal = None
    try:
        terminal = AuditedPointInTimeUniverse._readonly_connection(store.database_path)
        terminal_receipt_verification = store._verify_receipts_on_connection(terminal)
        _validate_closed_collection_store_scope(
            connection=terminal,
            sessions=sessions,
        )
        raw_artifacts_terminal = _verified_raw_artifact_set(
            store=store,
            connection=terminal,
        )
    except (PITReceiptError, sqlite3.DatabaseError, OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "factor-v3 feature history"
        ):
            raise
        raise ValueError(
            "factor-v3 feature history PIT store replay rejected"
        ) from None
    finally:
        if terminal is not None:
            terminal.close()
    stat_terminal = database_path.stat()
    if (
        raw_artifacts_terminal != raw_artifacts_before
        or terminal_receipt_verification["receipt_manifest_sha256"]
        != receipt_verification["receipt_manifest_sha256"]
        or stat_terminal.st_size != stat_before.st_size
        or stat_terminal.st_mtime_ns != stat_before.st_mtime_ns
        or not hmac.compare_digest(_file_sha256(database_path), expected)
    ):
        raise ValueError("factor-v3 feature history raw artifact drifted")
    return {
        "database_bytes": stat_after.st_size,
        "database_sha256": expected,
        "receipt_manifest_sha256": receipt_verification[
            "receipt_manifest_sha256"
        ],
        "raw_artifact_count": raw_artifacts_after["count"],
        "raw_artifact_set_sha256": raw_artifacts_after["sha256"],
        "session_refs": session_refs,
        "source_authority_root_sha256": canonical_sha256(source_descriptors),
        "upstream_scope_root_sha256": canonical_sha256(board_descriptors),
    }


def _validated_collection_manifest(
    value: Any,
    *,
    publication: Mapping[str, Any],
    sessions: Sequence[str],
    expected_producer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = _strict_mapping(
        value,
        fields=_COLLECTION_MANIFEST_FIELDS,
        label="collection manifest",
    )
    if (
        manifest.get("schema")
        != "audited-pit-factor-v3-feature-history-collection-manifest/v2"
        or manifest.get("authority_status") != "UNGRANTED_FEATURE_HISTORY_ONLY"
        or manifest.get("feature_history_only") is not True
        or manifest.get("factor_v3_points_contract_sha256")
        != FACTOR_V3_POINTS_CONTRACT_SHA256
        or manifest.get("factor_v3_feature_history_authority_contract_sha256")
        != FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        or manifest.get("security_code_transition_contract_sha256")
        != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        or manifest.get("session_count") != len(sessions)
        or manifest.get("sessions_sha256") != canonical_sha256(sessions)
        or manifest.get("exact_nonempty_bak_basic_session_count") != len(sessions)
        or manifest.get("suspend_d_authority_session_count") != len(sessions)
        or manifest.get("upstream_star_preserved_session_count") != len(sessions)
        or manifest.get("upstream_beijing_preserved_session_count") != len(sessions)
        or manifest.get("embargo_consumed") is not False
        or manifest.get("experiment_launch_eligible") is not False
        or manifest.get("factor_materialization_eligible") is not False
        or manifest.get("final_oos_consumed") is not False
        or manifest.get("production_profile_registered") is not False
        or manifest.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    for field in (
        "collection_plan_sha256",
        "development_session_refs_sha256",
        "pit_store_database_sha256",
        "pit_store_raw_artifact_set_sha256",
        "pit_store_receipt_manifest_sha256",
        "session_authority_refs_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
        "upstream_scope_root_sha256",
    ):
        _strict_sha256(manifest.get(field), label=f"collection manifest {field}")
    if (
        _strict_positive_int(
            manifest.get("pit_store_database_bytes"),
            label="collection manifest database bytes",
        )
        <= 0
        or _strict_positive_int(
            manifest.get("pit_store_raw_artifact_count"),
            label="collection manifest raw artifact count",
        )
        <= 0
    ):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    snapshot_relative_path = manifest.get(
        "snapshot_index_relative_path"
    )
    snapshot_sha256 = manifest.get("snapshot_index_sha256")
    if (
        type(snapshot_relative_path) is not str
        or _COLLECTION_SNAPSHOT_INDEX_PATH_RE.fullmatch(
            snapshot_relative_path
        )
        is None
        or _snapshot_index_relative_path(snapshot_sha256)
        != snapshot_relative_path
    ):
        raise ValueError(
            "factor-v3 feature history collection snapshot rejected"
        )
    expected_capability_sha256 = hashlib.sha256(
        publication["publication_capability"].encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(
        _strict_sha256(
            manifest.get("publication_capability_sha256"),
            label="collection manifest publication capability sha256",
        ),
        expected_capability_sha256,
    ):
        raise ValueError("factor-v3 feature history collection publication capability rejected")
    expected_binding = (
        _producer_binding()
        if expected_producer_binding is None
        else _validated_expected_producer_binding(expected_producer_binding)
    )
    if manifest.get("producer_binding") != expected_binding:
        raise ValueError("factor-v3 feature history collection producer binding rejected")
    _validated_feature_history_route_policy_descriptor(
        manifest.get("feature_history_route_policy_descriptor")
    )
    refs = _validated_session_authority_refs(
        manifest.get("session_authority_refs"),
        sessions=sessions,
    )
    if not hmac.compare_digest(
        manifest["session_authority_refs_sha256"], canonical_sha256(refs)
    ):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    return deepcopy(manifest)


def _manifest_from_replay(
    *,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    feature_history_route_policy_descriptor: Mapping[str, Any],
    publication_capability: str,
    replay: Mapping[str, Any],
    snapshot_index_relative_path: str,
    snapshot_index_sha256: str,
    sessions: Sequence[str],
) -> dict[str, Any]:
    return {
        "authority_status": "UNGRANTED_FEATURE_HISTORY_ONLY",
        "collection_plan_sha256": collection_plan["plan_sha256"],
        "development_session_refs_sha256": canonical_sha256(
            _validated_development_sessions(development_session_refs)
        ),
        "embargo_consumed": False,
        "exact_nonempty_bak_basic_session_count": len(sessions),
        "experiment_launch_eligible": False,
        "factor_materialization_eligible": False,
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "feature_history_only": True,
        "feature_history_route_policy_descriptor": deepcopy(
            feature_history_route_policy_descriptor
        ),
        "final_oos_consumed": False,
        "pit_store_database_bytes": replay["database_bytes"],
        "pit_store_database_sha256": replay["database_sha256"],
        "pit_store_raw_artifact_set_sha256": replay["raw_artifact_set_sha256"],
        "pit_store_raw_artifact_count": replay["raw_artifact_count"],
        "pit_store_receipt_manifest_sha256": replay["receipt_manifest_sha256"],
        "producer_binding": _producer_binding(),
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "publication_capability_sha256": hashlib.sha256(
            publication_capability.encode("utf-8")
        ).hexdigest(),
        "schema": "audited-pit-factor-v3-feature-history-collection-manifest/v2",
        "security_code_transition_contract_sha256": (
            SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        ),
        "session_authority_refs": deepcopy(replay["session_refs"]),
        "session_authority_refs_sha256": canonical_sha256(replay["session_refs"]),
        "session_count": len(sessions),
        "sessions_sha256": canonical_sha256(sessions),
        "snapshot_index_relative_path": snapshot_index_relative_path,
        "snapshot_index_sha256": snapshot_index_sha256,
        "source_authority_root_sha256": replay["source_authority_root_sha256"],
        "suspend_d_authority_session_count": len(sessions),
        "upstream_beijing_preserved_session_count": len(sessions),
        "upstream_scope_root_sha256": replay["upstream_scope_root_sha256"],
        "upstream_star_preserved_session_count": len(sessions),
    }


def _publish_factor_v3_feature_history_collection_candidate(
    *,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    feature_history_route_policy_descriptor: Mapping[str, Any],
    pit_store_root: str | Path,
    publication_output_root: str | Path,
    temporal_partition_contract: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal one offline replay into a capability-bound collection publication."""

    sessions = _verify_plan_self_integrity(collection_plan)
    verify_factor_v3_feature_history_collection_plan(
        collection_plan=collection_plan,
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=trade_cal_publication,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    route_policy = _validated_feature_history_route_policy_descriptor(
        feature_history_route_policy_descriptor
    )
    contract = _validated_partition_contract(temporal_partition_contract)
    producer_before = _producer_binding()
    snapshot = _capture_feature_history_snapshot(
        pit_store_root=pit_store_root,
        output_root=publication_output_root,
        sessions=sessions,
        temporal_partition_contract=contract,
    )
    replay = snapshot["replay"]
    if _producer_binding() != producer_before:
        raise ValueError("factor-v3 feature history collection producer drift rejected")
    publication_capability = str(uuid.uuid4())
    manifest = _manifest_from_replay(
        collection_plan=collection_plan,
        development_session_refs=development_session_refs,
        feature_history_route_policy_descriptor=route_policy,
        publication_capability=publication_capability,
        replay=replay,
        snapshot_index_relative_path=snapshot["relative_path"],
        snapshot_index_sha256=snapshot["sha256"],
        sessions=sessions,
    )
    publication = {
        "authority_manifest_created": True,
        "authority_manifest_relative_path": "",
        "authority_manifest_sha256": "",
        "publication_capability": publication_capability,
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "schema": "audited-pit-factor-v3-feature-history-collection-publication/v1",
    }
    raw = _canonical_bytes(manifest)
    if len(raw) > _MAX_COLLECTION_MANIFEST_BYTES:
        raise ValueError("factor-v3 feature history collection manifest rejected")
    digest = hashlib.sha256(raw).hexdigest()
    relative_path = (
        "feature_history_collection_manifest_candidates/sha256/"
        f"{digest[:2]}/{digest}.json"
    )
    output_root = _safe_collection_output_root(publication_output_root)
    created = _write_collection_manifest_candidate(
        output_root=output_root,
        relative_path=relative_path,
        raw=raw,
    )
    if not created:
        raise ValueError("factor-v3 feature history collection publication rejected")
    publication.update(
        authority_manifest_relative_path=relative_path,
        authority_manifest_sha256=digest,
    )
    _write_collection_publication_issuance(
        output_root=output_root,
        publication=publication,
    )
    verify_factor_v3_feature_history_collection_authority(
        collection_publication=publication,
        collection_publication_output_root=output_root,
        collection_plan=collection_plan,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=trade_cal_publication,
    )
    return publication


def _factor_v3_feature_history_attestation_binding(
    *,
    collection_publication: Mapping[str, Any],
    collection_publication_output_root: str | Path,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    feature_history_run_spec_path: str | Path,
    feature_history_run_spec_sha256: str,
    feature_history_run_root: str | Path,
    manifest: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    publication = _validated_collection_publication(collection_publication)
    issuance = _collection_publication_issuance(publication)
    public_publication = {
        key: value
        for key, value in publication.items()
        if key != "publication_capability"
    }
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    publication_root = _safe_collection_output_root(
        collection_publication_output_root
    )
    trade_cal_root = Path(trade_cal_output_root).resolve(strict=True)
    identity = {
        "collection_plan_sha256": collection_plan["plan_sha256"],
        "collection_publication": public_publication,
        "collection_publication_capability_sha256": issuance[
            "publication_capability_sha256"
        ],
        "collection_publication_issuance_relative_path": (
            _collection_issuance_relative_path(issuance)
        ),
        "collection_publication_issuance_sha256": hashlib.sha256(
            _canonical_bytes(issuance)
        ).hexdigest(),
        "collection_publication_output_root": str(publication_root),
        "development_session_refs_sha256": canonical_sha256(
            _validated_development_sessions(development_session_refs)
        ),
        "feature_history_run_root": str(run_root),
        "feature_history_run_spec_file_sha256": hashlib.sha256(
            raw_authority._read_safe_file(
                spec_path,
                label="factor-v3 feature history run spec",
                max_bytes=4 * 1024 * 1024,
            )
        ).hexdigest(),
        "feature_history_run_spec_path": str(spec_path),
        "feature_history_run_spec_sha256": _strict_sha256(
            feature_history_run_spec_sha256,
            label="feature run spec sha256",
        ),
        "manifest_identity_sha256": canonical_sha256(dict(manifest)),
        "manifest_relative_path": publication[
            "authority_manifest_relative_path"
        ],
        "manifest_sha256": publication["authority_manifest_sha256"],
        "pit_store_database_sha256": receipt["pit_store_database_sha256"],
        "receipt": dict(receipt),
        "receipt_sha256": receipt["receipt_sha256"],
        "schema": "factor-v3-feature-history-attestation-authority-binding/v1",
        "session_authority_refs_sha256": receipt[
            "session_authority_refs_sha256"
        ],
        "session_count": receipt["session_count"],
        "sessions_sha256": receipt["sessions_sha256"],
        "snapshot_index_sha256": receipt["snapshot_index_sha256"],
        "source_authority_root_sha256": receipt[
            "source_authority_root_sha256"
        ],
        "temporal_partition_contract_sha256": canonical_sha256(
            _validated_partition_contract(temporal_partition_contract)
        ),
        "trade_cal_output_root": str(trade_cal_root),
        "trade_cal_publication_sha256": canonical_sha256(
            dict(trade_cal_publication)
        ),
    }
    return {
        **identity,
        "binding_sha256": canonical_sha256(identity),
    }


def _verify_factor_v3_feature_history_collection_authority(
    *,
    collection_publication: Mapping[str, Any],
    collection_publication_output_root: str | Path,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    attestation_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant feature-history-only authority from a sealed collection publication."""

    attested_context = None
    expected_producer_binding = None
    if attestation_verification is not None:
        if type(attestation_verification) is not dict:
            raise ValueError(
                "factor-v3 feature history attestation verification rejected"
            )
        from app import factor_v3_feature_history_frozen_source_attestation as frozen

        attested_context = frozen._validated_attested_replay_context(
            **attestation_verification
        )
        if type(attested_context.get("authority_binding")) is not dict:
            raise ValueError("factor-v3 feature history attestation binding rejected")
        expected_producer_binding = attested_context["producer_binding"]
    sessions = _verify_plan_self_integrity(collection_plan)
    verify_factor_v3_feature_history_collection_plan(
        collection_plan=collection_plan,
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=trade_cal_publication,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
    )
    publication = _validated_collection_publication(collection_publication)
    manifest = _validated_collection_manifest(
        _read_collection_manifest(
            output_root=collection_publication_output_root,
            publication=publication,
        ),
        publication=publication,
        sessions=sessions,
        expected_producer_binding=expected_producer_binding,
    )
    _validated_collection_publication_issuance(
        output_root=collection_publication_output_root,
        publication=publication,
    )
    if (
        manifest["collection_plan_sha256"] != collection_plan["plan_sha256"]
        or manifest["development_session_refs_sha256"]
        != canonical_sha256(_validated_development_sessions(development_session_refs))
        or manifest["feature_history_route_policy_descriptor"]["sha256"]
        != collection_plan["feature_history_route_policy_sha256"]
    ):
        raise ValueError("factor-v3 feature history collection manifest rejected")
    contract = _validated_partition_contract(temporal_partition_contract)
    producer_before = _producer_binding()
    snapshot = _read_and_replay_collection_snapshot(
        output_root=collection_publication_output_root,
        relative_path=manifest["snapshot_index_relative_path"],
        expected_sha256=manifest["snapshot_index_sha256"],
        sessions=sessions,
        temporal_partition_contract=contract,
    )
    replay = snapshot["replay"]
    if _producer_binding() != producer_before:
        raise ValueError("factor-v3 feature history collection producer drift rejected")
    if (
        replay["session_refs"] != manifest["session_authority_refs"]
        or replay["database_bytes"] != manifest["pit_store_database_bytes"]
        or replay["receipt_manifest_sha256"]
        != manifest["pit_store_receipt_manifest_sha256"]
        or replay["raw_artifact_count"] != manifest["pit_store_raw_artifact_count"]
        or replay["raw_artifact_set_sha256"]
        != manifest["pit_store_raw_artifact_set_sha256"]
        or replay["source_authority_root_sha256"]
        != manifest["source_authority_root_sha256"]
        or replay["upstream_scope_root_sha256"]
        != manifest["upstream_scope_root_sha256"]
    ):
        raise ValueError("factor-v3 feature history collection evidence rejected")
    terminal_manifest = _read_collection_manifest(
        output_root=collection_publication_output_root,
        publication=publication,
    )
    if terminal_manifest != manifest:
        raise ValueError("factor-v3 feature history collection manifest drifted")
    if attested_context is not None:
        from app import factor_v3_feature_history_frozen_source_attestation as frozen

        if (
            frozen._validated_attested_replay_context(**attestation_verification)
            != attested_context
        ):
            raise ValueError("factor-v3 feature history attestation source drift rejected")
    unsigned = {
        "schema_version": "audited-pit-factor-v3-feature-history-authority-receipt/v3",
        "verified": True,
        "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
        "feature_history_only": True,
        "factor_v3_points_contract_sha256": FACTOR_V3_POINTS_CONTRACT_SHA256,
        "factor_v3_feature_history_authority_contract_sha256": (
            FACTOR_V3_FEATURE_HISTORY_AUTHORITY_CONTRACT_SHA256
        ),
        "collection_publication_manifest_sha256": publication[
            "authority_manifest_sha256"
        ],
        "collection_plan_sha256": collection_plan["plan_sha256"],
        "session_count": len(sessions),
        "sessions_sha256": canonical_sha256(sessions),
        "session_authority_refs_sha256": manifest["session_authority_refs_sha256"],
        "snapshot_index_sha256": snapshot["sha256"],
        "pit_store_database_sha256": replay["database_sha256"],
        "pit_store_database_bytes": replay["database_bytes"],
        "pit_store_receipt_manifest_sha256": replay[
            "receipt_manifest_sha256"
        ],
        "pit_store_raw_artifact_set_sha256": replay["raw_artifact_set_sha256"],
        "source_authority_root_sha256": replay[
            "source_authority_root_sha256"
        ],
        "upstream_scope_root_sha256": replay["upstream_scope_root_sha256"],
        "producer_code_root_sha256": manifest["producer_binding"]["root_sha256"],
        "exact_nonempty_bak_basic_session_count": len(sessions),
        "daily_generation_session_count": len(sessions),
        "suspend_d_authority_session_count": len(sessions),
        "upstream_star_preserved_session_count": len(sessions),
        "upstream_beijing_preserved_session_count": len(sessions),
        "security_code_transition_contract_sha256": (
            SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        ),
        "factor_materialization_eligible": False,
        "experiment_launch_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
    }
    receipt = {**unsigned, "receipt_sha256": canonical_sha256(unsigned)}
    if attested_context is not None:
        actual_binding = _factor_v3_feature_history_attestation_binding(
            collection_publication=publication,
            collection_publication_output_root=collection_publication_output_root,
            collection_plan=collection_plan,
            development_session_refs=development_session_refs,
            temporal_partition_contract=temporal_partition_contract,
            trade_cal_output_root=trade_cal_output_root,
            trade_cal_publication=trade_cal_publication,
            feature_history_run_spec_path=attestation_verification[
                "feature_history_run_spec_path"
            ],
            feature_history_run_spec_sha256=attested_context["feature_history"][
                "feature_run_spec_sha256"
            ],
            feature_history_run_root=attestation_verification["feature_history_run_root"],
            manifest=terminal_manifest,
            receipt=receipt,
        )
        if actual_binding != attested_context["authority_binding"]:
            raise ValueError("factor-v3 feature history attestation binding mismatch")
        _validated_collection_publication_issuance(
            output_root=collection_publication_output_root,
            publication=publication,
        )
    return receipt


def verify_factor_v3_feature_history_collection_authority(
    *,
    collection_publication: Mapping[str, Any],
    collection_publication_output_root: str | Path,
    collection_plan: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
) -> dict[str, Any]:
    """Grant feature-history-only authority from a sealed collection publication."""

    return _verify_factor_v3_feature_history_collection_authority(
        collection_publication=collection_publication,
        collection_publication_output_root=collection_publication_output_root,
        collection_plan=collection_plan,
        development_session_refs=development_session_refs,
        temporal_partition_contract=temporal_partition_contract,
        trade_cal_output_root=trade_cal_output_root,
        trade_cal_publication=trade_cal_publication,
    )


def _verify_feature_history_with_attested_producer_binding(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    from app import factor_v3_feature_history_frozen_source_attestation as frozen

    with frozen._locked_physical_frozen_source_binding(frozen_source_root):
        return _verify_factor_v3_feature_history_collection_authority(
            **kwargs,
            attestation_verification={
                "attestation_path": attestation_path,
                "expected_attestation_sha256": expected_attestation_sha256,
                "frozen_source_root": frozen_source_root,
                "expected_frozen_source_commit": expected_frozen_source_commit,
                "feature_history_run_spec_path": feature_history_run_spec_path,
                "feature_history_run_root": feature_history_run_root,
            },
        )
