"""One-artifact frozen-source attestation for the formal Factor V3 prewindow."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any

from app import jiaoch_points_raw_authority as raw_authority


FROZEN_SOURCE_ROOT = Path(r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run")
FROZEN_SOURCE_COMMIT = "b8057962f7a9754848994a6cfda9c9bf85e3db89"
FROZEN_FEATURE_RUN_SPEC_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\data\research_runs"
    r"\audited_pit_factor_v3_feature_history_run_spec_v1\run_specs\sha256\1d"
    r"\1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
)
FROZEN_FEATURE_RUN_SPEC_FILE_SHA256 = (
    "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3"
)
FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256 = (
    "561df1ecf28112544c28b318787581ffaf2edc64f8b53cfd45787d0206cf9e29"
)
FROZEN_FEATURE_RUN_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj\data\research_runs"
    r"\audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
)
_FROZEN_PRODUCER_SOURCE_MANIFEST = tuple(
    {"relative_path": relative_path}
    for relative_path in (
        "app/__init__.py",
        "app/audited_pit_factor_v3_feature_history_authority.py",
        "app/audited_pit_factor_v3_points_contract.py",
        "app/current_pool.py",
        "app/current_pool_gate.py",
        "app/durable_io.py",
        "app/factor_v3_feature_history_runner.py",
        "app/jiaoch_credential_slots.py",
        "app/jiaoch_daily_basic_exact_set_authority.py",
        "app/jiaoch_minute_collection_set.py",
        "app/jiaoch_minute_raw_authority.py",
        "app/jiaoch_minute_reconciliation.py",
        "app/jiaoch_points_collection_set.py",
        "app/jiaoch_points_raw_authority.py",
        "app/jiaoch_points_response_normalization.py",
        "app/jiaoch_trade_cal_authority.py",
        "app/research_market_data.py",
        "app/research_membership.py",
        "app/research_partitions.py",
        "app/research_pit_collector.py",
        "app/research_pit_contracts.py",
        "app/research_pit_sources.py",
        "app/research_pit_store.py",
        "app/research_pit_transport.py",
        "app/research_provider_evidence_partitions.py",
        "app/research_provider_pit_tail.py",
        "app/research_provider_pit_tail_v2.py",
        "app/research_proxy_data.py",
        "app/research_scope.py",
        "app/research_security_code_transition.py",
        "app/research_suspension_evidence.py",
    )
)
FROZEN_PRODUCER_RELATIVE_PATHS = tuple(
    item["relative_path"] for item in _FROZEN_PRODUCER_SOURCE_MANIFEST
)
ATTESTATION_SCHEMA = "factor-v3-feature-history-frozen-source-attestation/v2"
ATTESTATION_PUBLICATION_SCHEMA = (
    "factor-v3-feature-history-frozen-source-attestation-publication/v1"
)
_ATTESTATION_KIND = "factor_v3_feature_history_frozen_source_attestations"
_ATTESTOR_VERSION = "app.factor_v3_feature_history_frozen_source_attestation/2"
_ATTESTOR_RELATIVE_PATHS = (
    "app/__init__.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/jiaoch_points_raw_authority.py",
)
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_ATTESTATION_BYTES = 4 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_VERIFIER_OUTPUT_BYTES = 4 * 1024 * 1024
_FROZEN_VERIFIER_TIMEOUT_SECONDS = 3600
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
FROZEN_SOURCE_CHECKOUT_POLICY = {
    "blob_encoding": "utf-8-strict",
    "blob_eol": "lf-only-no-cr",
    "blob_final_newline_required": True,
    "blob_forbidden": [
        "utf-8-bom",
        "nul",
        "c0-except-horizontal-tab-line-feed-form-feed",
        "delete",
    ],
    "physical_eol": "crlf",
    "platform": "windows",
    "schema": "factor-v3-feature-history-frozen-checkout-policy/v1",
    "transform": "replace-each-lf-with-crlf-byte-exact",
}
GIT_EXECUTABLE = Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
GIT_EXECUTABLE_SHA256 = "c39b1b4f7a57935bbeadf246dc2466316619453a6a9da77c4a9c6bd6d8fb21d3"
PYTHON_EXECUTABLE = Path(r"E:\AI workspace\quant-signal-lkj\.venv\Scripts\python.exe")
PYTHON_EXECUTABLE_SHA256 = "5fec912cd3c47c125754cfbcb9b21ce0b415f860cfa8e2a3b98ceb9cd73bd30f"
_FROZEN_RESULT_FIELDS = frozenset(
    {"feature_history", "producer_binding", "producer_relative_paths"}
)
_FEATURE_FIELDS = frozenset(
    {
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "feature_run_root",
        "feature_run_spec_file_sha256",
        "feature_run_spec_path",
        "feature_run_spec_sha256",
        "pit_store_database_sha256",
        "publication_capability_sha256",
        "publication_issuance_relative_path",
        "publication_issuance_sha256",
        "receipt_sha256",
        "session_count",
        "sessions_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
    }
)
_FROZEN_SOURCE_FIELDS = frozenset(
    {
        "commit",
        "checkout_policy",
        "physical_files",
        "physical_files_root_sha256",
        "producer_binding",
        "root",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {"attestor_producer", "feature_history", "frozen_source", "schema", "verified"}
)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("factor-v3 frozen-source canonical JSON rejected") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    return value


def _validated_frozen_checkout_policy(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not hmac.compare_digest(
        _canonical_bytes(value),
        _canonical_bytes(FROZEN_SOURCE_CHECKOUT_POLICY),
    ):
        raise ValueError("factor-v3 frozen-source checkout policy rejected")
    return json.loads(_canonical_bytes(FROZEN_SOURCE_CHECKOUT_POLICY))


def _derive_frozen_checkout_bytes(raw_blob: bytes) -> bytes:
    if type(raw_blob) is not bytes or not raw_blob or len(raw_blob) > _MAX_SOURCE_BYTES:
        raise ValueError("factor-v3 frozen-source commit blob checkout rejected")
    try:
        text = raw_blob.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("factor-v3 frozen-source commit blob checkout rejected") from None
    if (
        raw_blob.startswith(b"\xef\xbb\xbf")
        or b"\r" in raw_blob
        or b"\0" in raw_blob
        or not raw_blob.endswith(b"\n")
        or any(
            (ord(character) < 32 and character not in {"\t", "\n", "\f"})
            or ord(character) == 127
            for character in text
        )
    ):
        raise ValueError("factor-v3 frozen-source commit blob checkout rejected")
    return raw_blob.replace(b"\n", b"\r\n")


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & flag)


def _read_direct_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    raw = raw_authority._read_safe_file(
        path,
        label=f"factor-v3 frozen-source {label}",
        max_bytes=max_bytes,
    )
    if not raw:
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    return raw


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in items:
            if key in output:
                raise ValueError(f"factor-v3 frozen-source {label} rejected")
            output[key] = item
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: None,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"factor-v3 frozen-source {label} rejected") from exc
    if type(value) is not dict or not hmac.compare_digest(raw, _canonical_bytes(value)):
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    return value


@contextmanager
def _open_pinned_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    allow_hardlinks: bool = False,
):
    parent = raw_authority._safe_existing_directory(
        path.parent,
        f"factor-v3 frozen-source {label} parent",
    )
    candidate = parent / path.name
    try:
        before = candidate.lstat()
        if (
            _is_reparse(candidate)
            or not stat.S_ISREG(before.st_mode)
            or (
                int(getattr(before, "st_nlink", 1)) != 1
                if not allow_hardlinks
                else int(getattr(before, "st_nlink", 1)) < 1
            )
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise OSError
    except OSError:
        raise ValueError(f"factor-v3 frozen-source {label} rejected") from None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

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
        handle = create_file(
            str(candidate),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise ValueError(f"factor-v3 frozen-source {label} safe open rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
    else:
        try:
            descriptor = os.open(
                candidate,
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            raise ValueError(f"factor-v3 frozen-source {label} safe open rejected") from None
    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(getattr(opened, "st_file_attributes", 0)) & 0x00000400
            or not os.path.samestat(before, opened)
        ):
            raise ValueError(f"factor-v3 frozen-source {label} identity rejected")
        yield candidate.resolve(strict=True), stream
    finally:
        stream.close()


def _locked_file_bytes(
    handle: Any,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    before = os.fstat(handle.fileno())
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > max_bytes:
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    handle.seek(0)
    raw = handle.read(max_bytes + 1)
    handle.seek(0)
    after = os.fstat(handle.fileno())
    if (
        len(raw) != before.st_size
        or not os.path.samestat(before, after)
        or before.st_size != after.st_size
    ):
        raise ValueError(f"factor-v3 frozen-source {label} drifted")
    return raw


def _postverify_pinned_file(
    path: Path,
    handle: Any,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int,
    allow_hardlinks: bool = False,
) -> None:
    opened = os.fstat(handle.fileno())
    try:
        parent = raw_authority._safe_existing_directory(
            path.parent,
            f"factor-v3 frozen-source {label} parent",
        )
        candidate = parent / path.name
        terminal = candidate.lstat()
    except (OSError, ValueError):
        raise ValueError(f"factor-v3 frozen-source {label} identity rejected") from None
    raw = _locked_file_bytes(
        handle,
        label=label,
        max_bytes=max_bytes,
    )
    if (
        not os.path.samestat(opened, terminal)
        or (
            int(getattr(terminal, "st_nlink", 1)) != 1
            if not allow_hardlinks
            else int(getattr(terminal, "st_nlink", 1)) < 1
        )
        or _is_reparse(candidate)
        or not hmac.compare_digest(_sha256(raw), expected_sha256)
    ):
        raise ValueError(f"factor-v3 frozen-source {label} identity rejected")


@contextmanager
def _pinned_executable(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
):
    with _open_pinned_file(
        path,
        label=f"{label} executable",
        max_bytes=_MAX_EXECUTABLE_BYTES,
        allow_hardlinks=True,
    ) as (candidate, handle):
        raw = _locked_file_bytes(
            handle,
            label=f"{label} executable",
            max_bytes=_MAX_EXECUTABLE_BYTES,
        )
        if not hmac.compare_digest(_sha256(raw), expected_sha256):
            raise ValueError(f"factor-v3 frozen-source {label} executable identity rejected")
        yield candidate
        _postverify_pinned_file(
            candidate,
            handle,
            expected_sha256=expected_sha256,
            label=f"{label} executable",
            max_bytes=_MAX_EXECUTABLE_BYTES,
            allow_hardlinks=True,
        )


def _read_pinned_executable(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> Path:
    with _pinned_executable(
        path,
        expected_sha256=expected_sha256,
        label=label,
    ) as candidate:
        return candidate


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _git_output(root: Path, *args: str) -> str:
    with _pinned_executable(
        GIT_EXECUTABLE,
        expected_sha256=GIT_EXECUTABLE_SHA256,
        label="git",
    ) as executable:
        try:
            result = subprocess.run(
                [str(executable), "--no-replace-objects", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=_git_environment(),
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise ValueError("factor-v3 frozen-source git identity rejected") from exc
    if (
        len(result.stdout.encode("utf-8")) > 1024 * 1024
        or len(result.stderr.encode("utf-8")) > 1024 * 1024
    ):
        raise ValueError("factor-v3 frozen-source git identity rejected")
    return result.stdout.strip()


def _git_bytes(root: Path, *args: str, max_bytes: int) -> bytes:
    with _pinned_executable(
        GIT_EXECUTABLE,
        expected_sha256=GIT_EXECUTABLE_SHA256,
        label="git",
    ) as executable:
        try:
            result = subprocess.run(
                [str(executable), "--no-replace-objects", "-C", str(root), *args],
                check=True,
                capture_output=True,
                env=_git_environment(),
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise ValueError("factor-v3 frozen-source git identity rejected") from exc
    if (
        len(result.stdout) > max_bytes
        or result.stderr
        or not result.stdout
    ):
        raise ValueError("factor-v3 frozen-source git identity rejected")
    return result.stdout


def _git_blob_bytes(root: Path, commit: str, relative_path: str) -> bytes:
    if (
        _COMMIT_RE.fullmatch(commit) is None
        or relative_path not in FROZEN_PRODUCER_RELATIVE_PATHS
    ):
        raise ValueError("factor-v3 frozen-source commit blob rejected")
    return _git_bytes(
        root,
        "cat-file",
        "blob",
        f"{commit}:{relative_path}",
        max_bytes=_MAX_SOURCE_BYTES,
    )


def _validated_frozen_git_index(root: Path) -> None:
    output = _git_output(
        root,
        "-c",
        "core.quotePath=false",
        "ls-files",
        "-v",
        "--",
        *FROZEN_PRODUCER_RELATIVE_PATHS,
    )
    entries: dict[str, str] = {}
    for line in output.splitlines():
        if len(line) < 3 or line[1] != " " or line[2:] in entries:
            raise ValueError("factor-v3 frozen-source git index rejected")
        entries[line[2:]] = line[0]
    if entries != {
        relative_path: "H" for relative_path in FROZEN_PRODUCER_RELATIVE_PATHS
    }:
        raise ValueError("factor-v3 frozen-source git index rejected")


def _git_identity(root: Path) -> dict[str, Any]:
    return {
        "commit": _git_output(root, "rev-parse", "HEAD"),
        "root": str(Path(_git_output(root, "rev-parse", "--show-toplevel")).resolve(strict=True)),
        "status_clean": not bool(
            _git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
        ),
    }


def _validated_frozen_root(
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
) -> Path:
    root = Path(frozen_source_root)
    if (
        not root.is_absolute()
        or _is_reparse(root)
        or not root.is_dir()
        or root.resolve(strict=True) != Path(FROZEN_SOURCE_ROOT).resolve(strict=True)
        or expected_frozen_source_commit != FROZEN_SOURCE_COMMIT
        or _COMMIT_RE.fullmatch(expected_frozen_source_commit) is None
    ):
        raise ValueError("factor-v3 frozen-source identity rejected")
    identity = _git_identity(root)
    if identity != {
        "commit": expected_frozen_source_commit,
        "root": str(root.resolve(strict=True)),
        "status_clean": True,
    }:
        raise ValueError("factor-v3 frozen-source identity rejected")
    _validated_frozen_git_index(root)
    return root.resolve(strict=True)


def _frozen_physical_source_entry(
    *,
    root: Path,
    relative_path: str,
    physical_raw: bytes,
) -> dict[str, Any]:
    commit_raw = _git_blob_bytes(
        root,
        FROZEN_SOURCE_COMMIT,
        relative_path,
    )
    expected_physical = _derive_frozen_checkout_bytes(commit_raw)
    if not hmac.compare_digest(physical_raw, expected_physical):
        raise ValueError("factor-v3 frozen-source commit checkout binding rejected")
    return {
        "commit_blob_bytes": len(commit_raw),
        "commit_blob_sha256": _sha256(commit_raw),
        "physical_bytes": len(physical_raw),
        "physical_sha256": _sha256(physical_raw),
        "relative_path": relative_path,
    }


def _frozen_physical_binding(entries: list[dict[str, Any]]) -> dict[str, Any]:
    identity = {
        "checkout_policy": _validated_frozen_checkout_policy(
            FROZEN_SOURCE_CHECKOUT_POLICY
        ),
        "physical_files": entries,
        "schema": "factor-v3-feature-history-frozen-physical-source/v2",
    }
    return {
        **identity,
        "producer_binding_root_sha256": _canonical_sha256(identity),
    }


def _physical_frozen_source_binding(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve(strict=True)
    entries = []
    for relative_path in FROZEN_PRODUCER_RELATIVE_PATHS:
        raw = _read_direct_file(
            root / Path(*relative_path.split("/")),
            label="producer source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        entries.append(
            _frozen_physical_source_entry(
                root=root,
                relative_path=relative_path,
                physical_raw=raw,
            )
        )
    return _frozen_physical_binding(entries)


@contextmanager
def _locked_physical_frozen_source_binding(source_root: str | Path):
    root = Path(source_root).resolve(strict=True)
    with ExitStack() as stack:
        locked: list[tuple[Path, Any, str, int]] = []
        entries = []
        for relative_path in FROZEN_PRODUCER_RELATIVE_PATHS:
            path = root / Path(*relative_path.split("/"))
            candidate, handle = stack.enter_context(
                _open_pinned_file(
                    path,
                    label="producer source",
                    max_bytes=_MAX_SOURCE_BYTES,
                )
            )
            raw = _locked_file_bytes(
                handle,
                label="producer source",
                max_bytes=_MAX_SOURCE_BYTES,
            )
            digest = _sha256(raw)
            entries.append(
                _frozen_physical_source_entry(
                    root=root,
                    relative_path=relative_path,
                    physical_raw=raw,
                )
            )
            locked.append((candidate, handle, digest, len(raw)))
        binding = _frozen_physical_binding(entries)
        yield binding
        for candidate, handle, digest, size in locked:
            _postverify_pinned_file(
                candidate,
                handle,
                expected_sha256=digest,
                label="producer source",
                max_bytes=max(_MAX_SOURCE_BYTES, size),
            )


@contextmanager
def _existing_read_only_run_lock(path: str | Path):
    from app import factor_v3_feature_history_runner as runner

    candidate = Path(path)
    try:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ValueError("factor-v3 frozen-source existing run lock rejected") from exc
    locked = False
    lock_api: Any = None
    try:
        try:
            runner._validate_open_lock_identity(candidate, descriptor)
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size < 1:
                    raise OSError
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBRLCK, 1)
                lock_api = msvcrt
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                lock_api = fcntl
            locked = True
            runner._validate_open_lock_identity(candidate, descriptor)
        except (ImportError, OSError, runner.FactorV3FeatureHistoryRunnerError) as exc:
            raise ValueError("factor-v3 frozen-source existing run lock rejected") from exc
        try:
            yield
            runner._validate_open_lock_identity(candidate, descriptor)
        except runner.FactorV3FeatureHistoryRunnerError as exc:
            raise ValueError("factor-v3 frozen-source existing run lock rejected") from exc
        finally:
            if locked:
                try:
                    if os.name == "nt":
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        lock_api.locking(descriptor, lock_api.LK_UNLCK, 1)
                    else:
                        lock_api.flock(descriptor, lock_api.LOCK_UN)
                except OSError as exc:
                    raise ValueError(
                        "factor-v3 frozen-source existing run lock cleanup rejected"
                    ) from exc
    finally:
        os.close(descriptor)


def _attestor_producer_binding() -> dict[str, Any]:
    source_root = Path(__file__).resolve(strict=True).parents[1]
    entries = []
    for relative_path in _ATTESTOR_RELATIVE_PATHS:
        raw = _read_direct_file(
            source_root / Path(*relative_path.split("/")),
            label="attestor source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        entries.append(
            {
                "path": relative_path,
                "physical_bytes": len(raw),
                "physical_sha256": _sha256(raw),
            }
        )
    identity = {
        "entries": entries,
        "producer_version": _ATTESTOR_VERSION,
        "schema": "factor-v3-feature-history-frozen-attestor-producer/v1",
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


_FROZEN_VERIFIER_CODE = r"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
    raise RuntimeError("frozen verifier requires Python -I -B")
source_root = Path(sys.argv[1]).resolve(strict=True)
spec_path = Path(sys.argv[2]).resolve(strict=True)
run_root = Path(sys.argv[3]).resolve(strict=True)
pycache_root = Path(sys.argv[4]).resolve(strict=True)
if (
    sys.pycache_prefix is None
    or Path(sys.pycache_prefix).resolve(strict=True) != pycache_root
    or next(pycache_root.iterdir(), None) is not None
):
    raise RuntimeError("frozen verifier pycache isolation rejected")
expected_physical = json.loads(sys.argv[5])
expected_physical_count = int(sys.argv[6])
if (
    type(expected_physical) is not list
    or expected_physical_count <= 0
    or len(expected_physical) != expected_physical_count
):
    raise RuntimeError("frozen verifier physical source binding rejected")

def direct_source(relative_path, expected_bytes, expected_sha256):
    path = source_root / Path(*relative_path.split("/"))
    current = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        current /= part
        metadata = current.lstat()
        is_reparse = current.is_symlink() or bool(
            int(getattr(metadata, "st_file_attributes", 0)) & 0x400
        )
        if is_reparse:
            raise RuntimeError("frozen verifier source reparse rejected")
        if index < len(path.parts[1:]) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("frozen verifier source parent rejected")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or int(getattr(before, "st_nlink", 1)) != 1
    ):
        raise RuntimeError("frozen verifier source file rejected")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if not os.path.samestat(before, opened):
            raise RuntimeError("frozen verifier source identity rejected")
        raw = bytearray()
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise RuntimeError("frozen verifier source size rejected")
            raw.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError("frozen verifier source size rejected")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = path.lstat()
    if (
        not os.path.samestat(opened, after)
        or not os.path.samestat(after, path_after)
        or len(raw) != expected_bytes
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise RuntimeError("frozen verifier source binding rejected")
    return path

source_paths = {
    item["relative_path"]: direct_source(
        item["relative_path"],
        item["physical_bytes"],
        item["physical_sha256"],
    )
    for item in expected_physical
}
sys.path.insert(0, str(source_root))
from app import audited_pit_factor_v3_feature_history_authority as authority
from app import factor_v3_feature_history_runner as runner

if (
    Path(authority.__file__).resolve(strict=True)
    != source_paths["app/audited_pit_factor_v3_feature_history_authority.py"]
    or Path(runner.__file__).resolve(strict=True)
    != source_root / "app" / "factor_v3_feature_history_runner.py"
):
    raise RuntimeError("frozen verifier execution source rejected")
paths = runner._run_paths(run_root, create=False)

@contextmanager
def existing_read_only_run_lock(path):
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    locked = False
    lock_api = None
    try:
        runner._validate_open_lock_identity(path, descriptor)
        try:
            import fcntl
        except ImportError:
            fcntl = None
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        if os.name != "nt" and fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            lock_api = fcntl
        elif msvcrt is not None:
            if os.fstat(descriptor).st_size < 1:
                raise RuntimeError("frozen verifier existing lock rejected")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBRLCK, 1)
            lock_api = msvcrt
        else:
            raise RuntimeError("frozen verifier read-only locking unavailable")
        locked = True
        runner._validate_open_lock_identity(path, descriptor)
        yield
        runner._validate_open_lock_identity(path, descriptor)
    finally:
        try:
            if locked and lock_api is not None:
                if getattr(lock_api, "flock", None) is not None:
                    lock_api.flock(descriptor, lock_api.LOCK_UN)
                else:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    lock_api.locking(descriptor, lock_api.LK_UNLCK, 1)
        finally:
            os.close(descriptor)

with existing_read_only_run_lock(paths["lock"]):
    spec = runner.load_factor_v3_feature_history_run_spec(spec_path)
    runner._verify_plan(spec)
    state = runner._validated_state(
        runner._read_json_file(
            paths["state"],
            label="run state",
            max_bytes=runner._MAX_STATE_BYTES,
        ),
        run_spec_sha256=spec["run_spec_sha256"],
    )
    if (
        state.get("status") != "verified"
        or state.get("completed_session_count") != 250
        or type(state.get("receipt")) is not dict
    ):
        raise RuntimeError("frozen verifier existing state rejected")
    publication = runner._validated_publication(state["collection_publication"])
    receipt = runner._verify_collection_authority(
        publication_root=paths["publication_root"],
        publication=publication,
        spec=spec,
    )
    if receipt != state["receipt"] or receipt.get("session_count") != 250:
        raise RuntimeError("frozen verifier existing receipt rejected")
    manifest = authority._read_collection_manifest(
        output_root=paths["publication_root"],
        publication=publication,
    )
    issuance = authority._collection_publication_issuance(publication)
    issuance_raw = authority._canonical_bytes(issuance)
    feature = {
        "authority_manifest_relative_path": publication["authority_manifest_relative_path"],
        "authority_manifest_sha256": publication["authority_manifest_sha256"],
        "feature_run_root": str(run_root),
        "feature_run_spec_file_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "feature_run_spec_path": str(spec_path),
        "feature_run_spec_sha256": spec["run_spec_sha256"],
        "pit_store_database_sha256": receipt["pit_store_database_sha256"],
        "publication_capability_sha256": issuance["publication_capability_sha256"],
        "publication_issuance_relative_path": authority._collection_issuance_relative_path(issuance),
        "publication_issuance_sha256": hashlib.sha256(issuance_raw).hexdigest(),
        "receipt_sha256": receipt["receipt_sha256"],
        "session_count": receipt["session_count"],
        "sessions_sha256": receipt["sessions_sha256"],
        "snapshot_index_sha256": receipt["snapshot_index_sha256"],
        "source_authority_root_sha256": receipt["source_authority_root_sha256"],
    }
    if manifest["producer_binding"] != authority._producer_binding():
        raise RuntimeError("frozen producer binding rejected")
for item in expected_physical:
    direct_source(
        item["relative_path"],
        item["physical_bytes"],
        item["physical_sha256"],
    )
if next(pycache_root.iterdir(), None) is not None:
    raise RuntimeError("frozen verifier pycache emission rejected")
print(json.dumps(
    {
        "feature_history": feature,
        "producer_binding": manifest["producer_binding"],
        "producer_relative_paths": [
            item["relative_path"] for item in expected_physical
        ],
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
))
"""


def _run_frozen_verifier(
    *,
    frozen_source_root: Path,
    feature_history_run_spec_path: Path,
    feature_history_run_root: Path,
    expected_physical_binding: Mapping[str, Any],
) -> dict[str, Any]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    }
    physical_files = expected_physical_binding.get("physical_files")
    if type(physical_files) is not list or len(physical_files) != len(
        FROZEN_PRODUCER_RELATIVE_PATHS
    ):
        raise ValueError("factor-v3 frozen-source physical binding rejected")
    with tempfile.TemporaryDirectory(prefix="factor-v3-frozen-pycache-") as temporary:
        pycache_root = Path(temporary) / "pycache"
        pycache_root.mkdir()
        with _pinned_executable(
            PYTHON_EXECUTABLE,
            expected_sha256=PYTHON_EXECUTABLE_SHA256,
            label="python",
        ) as python_executable:
            try:
                result = subprocess.run(
                    [
                        str(python_executable),
                        "-X",
                        f"pycache_prefix={pycache_root}",
                        "-I",
                        "-B",
                        "-c",
                        _FROZEN_VERIFIER_CODE,
                        str(frozen_source_root),
                        str(feature_history_run_spec_path),
                        str(feature_history_run_root),
                        str(pycache_root),
                        _canonical_bytes(physical_files).decode("utf-8"),
                        str(len(physical_files)),
                    ],
                    cwd=frozen_source_root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    timeout=_FROZEN_VERIFIER_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("factor-v3 frozen-source verifier unavailable") from exc
    if (
        len(result.stdout) > _MAX_VERIFIER_OUTPUT_BYTES
        or len(result.stderr) > _MAX_VERIFIER_OUTPUT_BYTES
        or result.returncode != 0
        or result.stderr
        or not result.stdout
    ):
        raise ValueError("factor-v3 frozen-source verifier rejected")
    raw = result.stdout.rstrip(b"\r\n")
    if result.stdout not in {raw + b"\n", raw + b"\r\n"}:
        raise ValueError("factor-v3 frozen-source verifier output rejected")
    return _strict_json(raw, label="verifier output")


def _reject_plaintext_credentials(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = re.sub(r"[^a-z]", "", str(key).casefold())
            if (
                normalized
                in {
                    "api_key",
                    "credential",
                    "password",
                    "publication_capability",
                    "secret",
                    "token",
                }
                or "privatekey" in compact
            ):
                raise ValueError("factor-v3 frozen-source plaintext credential rejected")
            _reject_plaintext_credentials(nested)
    elif type(value) is list:
        for nested in value:
            _reject_plaintext_credentials(nested)


def _validated_feature_identity(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FEATURE_FIELDS:
        raise ValueError("factor-v3 frozen-source feature identity rejected")
    output = json.loads(_canonical_bytes(value))
    for field in (
        "authority_manifest_sha256",
        "feature_run_spec_file_sha256",
        "feature_run_spec_sha256",
        "pit_store_database_sha256",
        "publication_capability_sha256",
        "publication_issuance_sha256",
        "receipt_sha256",
        "sessions_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
    ):
        _require_sha256(output[field], label=field)
    if (
        output["session_count"] != 250
        or type(output["feature_run_root"]) is not str
        or type(output["feature_run_spec_path"]) is not str
        or not Path(output["feature_run_root"]).is_absolute()
        or not Path(output["feature_run_spec_path"]).is_absolute()
        or type(output["authority_manifest_relative_path"]) is not str
        or type(output["publication_issuance_relative_path"]) is not str
    ):
        raise ValueError("factor-v3 frozen-source feature identity rejected")
    for path_field, sha_field in (
        ("authority_manifest_relative_path", "authority_manifest_sha256"),
        ("publication_issuance_relative_path", "publication_issuance_sha256"),
    ):
        parts = output[path_field].split("/")
        digest = output[sha_field]
        if (
            len(parts) != 4
            or parts[-3] != "sha256"
            or parts[-2] != digest[:2]
            or parts[-1] != f"{digest}.json"
        ):
            raise ValueError("factor-v3 frozen-source feature CAS identity rejected")
    return output


def _validated_frozen_result(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FROZEN_RESULT_FIELDS:
        raise ValueError("factor-v3 frozen-source verifier result rejected")
    if value.get("producer_relative_paths") != list(FROZEN_PRODUCER_RELATIVE_PATHS):
        raise ValueError("factor-v3 frozen-source producer set rejected")
    producer = value.get("producer_binding")
    if (
        type(producer) is not dict
        or producer.get("schema_version") != "factor-v3-feature-history-producer-binding/v2"
        or type(producer.get("root_sha256")) is not str
        or producer.get("root_sha256")
        != _canonical_sha256(
            {key: nested for key, nested in producer.items() if key != "root_sha256"}
        )
    ):
        raise ValueError("factor-v3 frozen-source producer binding rejected")
    _require_sha256(producer["root_sha256"], label="producer binding root")
    output = {
        "feature_history": _validated_feature_identity(value["feature_history"]),
        "producer_binding": json.loads(_canonical_bytes(producer)),
        "producer_relative_paths": list(FROZEN_PRODUCER_RELATIVE_PATHS),
    }
    _reject_plaintext_credentials(output)
    return output


def _attestation_payload(
    *,
    frozen_source_root: Path,
    frozen_source_commit: str,
    physical_binding: Mapping[str, Any],
    verifier_result: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "attestor_producer": _attestor_producer_binding(),
        "feature_history": dict(verifier_result["feature_history"]),
        "frozen_source": {
            "commit": frozen_source_commit,
            "checkout_policy": physical_binding["checkout_policy"],
            "physical_files": physical_binding["physical_files"],
            "physical_files_root_sha256": physical_binding["producer_binding_root_sha256"],
            "producer_binding": verifier_result["producer_binding"],
            "root": str(frozen_source_root),
        },
        "schema": ATTESTATION_SCHEMA,
        "verified": True,
    }
    _reject_plaintext_credentials(payload)
    return payload


def _safe_output_root(output_root: str | Path) -> Path:
    root = Path(output_root)
    if not root.is_absolute():
        raise ValueError("factor-v3 frozen-source output root rejected")
    return raw_authority._safe_existing_directory(
        root,
        "factor-v3 frozen-source output root",
    )


def _write_attestation(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _canonical_bytes(dict(payload))
    if len(raw) > _MAX_ATTESTATION_BYTES:
        raise ValueError("factor-v3 frozen-source attestation rejected")
    digest = _sha256(raw)
    root = _safe_output_root(root)
    directory = raw_authority._content_addressed_directory(
        root,
        _ATTESTATION_KIND,
        digest,
    )
    path = directory / f"{digest}.json"
    raw_authority._write_create_only(
        path,
        raw,
        label="factor-v3 frozen-source attestation",
        reuse_identical=True,
    )
    if not hmac.compare_digest(
        _read_direct_file(
            path,
            label="attestation",
            max_bytes=_MAX_ATTESTATION_BYTES,
        ),
        raw,
    ):
        raise ValueError("factor-v3 frozen-source attestation postverify rejected")
    return {
        "attestation_relative_path": path.relative_to(root).as_posix(),
        "attestation_sha256": digest,
        "schema": ATTESTATION_PUBLICATION_SCHEMA,
    }


def publish_factor_v3_feature_history_frozen_source_attestation(
    *,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    root = _validated_frozen_root(
        frozen_source_root,
        expected_frozen_source_commit,
    )
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    if (
        spec_path != Path(FROZEN_FEATURE_RUN_SPEC_PATH).resolve(strict=True)
        or run_root != Path(FROZEN_FEATURE_RUN_ROOT).resolve(strict=True)
        or _sha256(
            _read_direct_file(
                spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        )
        != FROZEN_FEATURE_RUN_SPEC_FILE_SHA256
    ):
        raise ValueError("factor-v3 frozen-source feature input rejected")
    with _locked_physical_frozen_source_binding(root) as before:
        result = _validated_frozen_result(
            _run_frozen_verifier(
                frozen_source_root=root,
                feature_history_run_spec_path=spec_path,
                feature_history_run_root=run_root,
                expected_physical_binding=before,
            )
        )
        if (
            result["feature_history"]["feature_run_spec_sha256"]
            != FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256
        ):
            raise ValueError("factor-v3 frozen-source feature input rejected")
        after = _physical_frozen_source_binding(root)
        if before != after or _validated_frozen_root(root, expected_frozen_source_commit) != root:
            raise ValueError("factor-v3 frozen-source source drifted")
        payload = _attestation_payload(
            frozen_source_root=root,
            frozen_source_commit=expected_frozen_source_commit,
            physical_binding=after,
            verifier_result=result,
        )
    return _write_attestation(_safe_output_root(output_root), payload)


def _attestation_path(
    attestation_path: str | Path,
    expected_attestation_sha256: str,
) -> Path:
    digest = _require_sha256(
        expected_attestation_sha256,
        label="attestation sha256",
    )
    path = Path(attestation_path)
    expected_tail = (
        _ATTESTATION_KIND,
        "sha256",
        digest[:2],
        f"{digest}.json",
    )
    if not path.is_absolute() or tuple(path.parts[-4:]) != expected_tail:
        raise ValueError("factor-v3 frozen-source attestation path rejected")
    return path


def _validated_attestation(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
) -> dict[str, Any]:
    path = _attestation_path(attestation_path, expected_attestation_sha256)
    raw = _read_direct_file(
        path,
        label="attestation",
        max_bytes=_MAX_ATTESTATION_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_attestation_sha256):
        raise ValueError("factor-v3 frozen-source attestation content rejected")
    value = _strict_json(raw, label="attestation")
    if (
        set(value) != _ATTESTATION_FIELDS
        or value.get("schema") != ATTESTATION_SCHEMA
        or value.get("verified") is not True
        or type(value.get("attestor_producer")) is not dict
        or type(value.get("frozen_source")) is not dict
        or set(value["frozen_source"]) != _FROZEN_SOURCE_FIELDS
    ):
        raise ValueError("factor-v3 frozen-source attestation rejected")
    _validated_frozen_checkout_policy(value["frozen_source"]["checkout_policy"])
    _validated_feature_identity(value.get("feature_history"))
    _reject_plaintext_credentials(value)
    return value


def _validated_attested_authority_binding(
    *,
    feature_history_run_spec_path: Path,
    feature_history_run_root: Path,
    feature: Mapping[str, Any],
    producer_binding: Mapping[str, Any],
) -> dict[str, Any]:
    from app import audited_pit_factor_v3_feature_history_authority as authority
    from app import factor_v3_feature_history_runner as runner

    spec = runner.load_factor_v3_feature_history_run_spec(feature_history_run_spec_path)
    paths = runner._run_paths(feature_history_run_root, create=False)
    state = runner._validated_state(
        runner._read_json_file(
            paths["state"],
            label="run state",
            max_bytes=runner._MAX_STATE_BYTES,
        ),
        run_spec_sha256=spec["run_spec_sha256"],
    )
    if (
        state["status"] != "verified"
        or state["completed_session_count"] != 250
        or type(state["receipt"]) is not dict
    ):
        raise ValueError("factor-v3 frozen-source run state rejected")
    publication = runner._validated_publication(state["collection_publication"])
    manifest = authority._validated_collection_manifest(
        authority._read_collection_manifest(
            output_root=paths["publication_root"],
            publication=publication,
        ),
        publication=publication,
        sessions=authority._verify_plan_self_integrity(spec["collection_plan"]),
        expected_producer_binding=producer_binding,
    )
    authority._validated_collection_publication_issuance(
        output_root=paths["publication_root"],
        publication=publication,
    )
    binding = authority._factor_v3_feature_history_attestation_binding(
        collection_publication=publication,
        collection_publication_output_root=paths["publication_root"],
        collection_plan=spec["collection_plan"],
        development_session_refs=spec["development_session_refs"],
        temporal_partition_contract=spec["temporal_partition_contract"],
        trade_cal_output_root=spec["trade_cal_output_root"],
        trade_cal_publication=spec["trade_cal_publication"],
        feature_history_run_spec_path=feature_history_run_spec_path,
        feature_history_run_spec_sha256=spec["run_spec_sha256"],
        feature_history_run_root=feature_history_run_root,
        manifest=manifest,
        receipt=state["receipt"],
    )
    publication_issuance = authority._collection_publication_issuance(publication)
    expected_feature = {
        "authority_manifest_relative_path": publication["authority_manifest_relative_path"],
        "authority_manifest_sha256": publication["authority_manifest_sha256"],
        "feature_run_root": str(feature_history_run_root),
        "feature_run_spec_file_sha256": _sha256(
            _read_direct_file(
                feature_history_run_spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        ),
        "feature_run_spec_path": str(feature_history_run_spec_path),
        "feature_run_spec_sha256": spec["run_spec_sha256"],
        "pit_store_database_sha256": state["receipt"]["pit_store_database_sha256"],
        "publication_capability_sha256": publication_issuance["publication_capability_sha256"],
        "publication_issuance_relative_path": (
            authority._collection_issuance_relative_path(publication_issuance)
        ),
        "publication_issuance_sha256": _sha256(authority._canonical_bytes(publication_issuance)),
        "receipt_sha256": state["receipt"]["receipt_sha256"],
        "session_count": state["receipt"]["session_count"],
        "sessions_sha256": state["receipt"]["sessions_sha256"],
        "snapshot_index_sha256": state["receipt"]["snapshot_index_sha256"],
        "source_authority_root_sha256": state["receipt"]["source_authority_root_sha256"],
    }
    if _validated_feature_identity(expected_feature) != feature:
        raise ValueError("factor-v3 frozen-source attested authority binding rejected")
    return binding


def _validated_attested_replay_context(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
) -> dict[str, Any]:
    attestation = _validated_attestation(
        attestation_path=attestation_path,
        expected_attestation_sha256=expected_attestation_sha256,
    )
    root = _validated_frozen_root(
        frozen_source_root,
        expected_frozen_source_commit,
    )
    frozen_identity = attestation["frozen_source"]
    producer_binding = frozen_identity.get("producer_binding")
    if (
        type(producer_binding) is not dict
        or producer_binding.get("schema_version") != "factor-v3-feature-history-producer-binding/v2"
        or producer_binding.get("root_sha256")
        != _canonical_sha256(
            {key: value for key, value in producer_binding.items() if key != "root_sha256"}
        )
    ):
        raise ValueError("factor-v3 frozen-source producer binding rejected")
    physical = _physical_frozen_source_binding(root)
    if (
        frozen_identity["root"] != str(root)
        or frozen_identity["commit"] != expected_frozen_source_commit
        or frozen_identity["checkout_policy"] != physical["checkout_policy"]
        or frozen_identity["physical_files"] != physical["physical_files"]
        or frozen_identity["physical_files_root_sha256"] != physical["producer_binding_root_sha256"]
    ):
        raise ValueError("factor-v3 frozen-source source identity rejected")
    attestor = _attestor_producer_binding()
    if attestation["attestor_producer"] != attestor:
        raise ValueError("factor-v3 frozen-source attestor producer rejected")
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    feature = _validated_feature_identity(attestation["feature_history"])
    if (
        feature["feature_run_spec_path"] != str(spec_path)
        or feature["feature_run_root"] != str(run_root)
        or spec_path != Path(FROZEN_FEATURE_RUN_SPEC_PATH).resolve(strict=True)
        or run_root != Path(FROZEN_FEATURE_RUN_ROOT).resolve(strict=True)
        or feature["feature_run_spec_sha256"] != FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256
        or feature["feature_run_spec_file_sha256"]
        != _sha256(
            _read_direct_file(
                spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        )
    ):
        raise ValueError("factor-v3 frozen-source feature input rejected")
    authority_binding = _validated_attested_authority_binding(
        feature_history_run_spec_path=spec_path,
        feature_history_run_root=run_root,
        feature=feature,
        producer_binding=producer_binding,
    )
    return {
        "attestation": json.loads(_canonical_bytes(attestation)),
        "attestor_producer": attestor,
        "authority_binding": authority_binding,
        "feature_history": feature,
        "frozen_source_root": str(root),
        "physical_binding": physical,
        "producer_binding": json.loads(_canonical_bytes(producer_binding)),
        "run_root": str(run_root),
        "spec_path": str(spec_path),
    }


def _replay_current_feature_history(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: Path,
    feature_history_run_root: Path,
) -> dict[str, Any]:
    from app import audited_pit_factor_v3_feature_history_authority as authority
    from app import factor_v3_feature_history_runner as runner

    spec = runner.load_factor_v3_feature_history_run_spec(feature_history_run_spec_path)
    paths = runner._run_paths(feature_history_run_root, create=False)
    state = runner._validated_state(
        runner._read_json_file(
            paths["state"],
            label="run state",
            max_bytes=runner._MAX_STATE_BYTES,
        ),
        run_spec_sha256=spec["run_spec_sha256"],
    )
    publication = runner._validated_publication(state["collection_publication"])
    receipt = authority._verify_feature_history_with_attested_producer_binding(
        attestation_path=attestation_path,
        expected_attestation_sha256=expected_attestation_sha256,
        frozen_source_root=frozen_source_root,
        expected_frozen_source_commit=expected_frozen_source_commit,
        feature_history_run_spec_path=feature_history_run_spec_path,
        feature_history_run_root=feature_history_run_root,
        collection_plan=spec["collection_plan"],
        trade_cal_output_root=spec["trade_cal_output_root"],
        trade_cal_publication=spec["trade_cal_publication"],
        development_session_refs=spec["development_session_refs"],
        temporal_partition_contract=spec["temporal_partition_contract"],
        collection_publication_output_root=paths["publication_root"],
        collection_publication=publication,
    )
    if state.get("receipt") != receipt:
        raise ValueError("factor-v3 frozen-source current receipt replay rejected")
    issuance = authority._collection_publication_issuance(publication)
    issuance_raw = authority._canonical_bytes(issuance)
    return {
        "authority_manifest_relative_path": publication["authority_manifest_relative_path"],
        "authority_manifest_sha256": publication["authority_manifest_sha256"],
        "feature_run_root": str(feature_history_run_root),
        "feature_run_spec_file_sha256": _sha256(
            _read_direct_file(
                feature_history_run_spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        ),
        "feature_run_spec_path": str(feature_history_run_spec_path),
        "feature_run_spec_sha256": spec["run_spec_sha256"],
        "pit_store_database_sha256": receipt["pit_store_database_sha256"],
        "publication_capability_sha256": issuance["publication_capability_sha256"],
        "publication_issuance_relative_path": authority._collection_issuance_relative_path(
            issuance
        ),
        "publication_issuance_sha256": _sha256(issuance_raw),
        "receipt_sha256": receipt["receipt_sha256"],
        "session_count": receipt["session_count"],
        "sessions_sha256": receipt["sessions_sha256"],
        "snapshot_index_sha256": receipt["snapshot_index_sha256"],
        "source_authority_root_sha256": receipt["source_authority_root_sha256"],
    }


def _verify_factor_v3_feature_history_frozen_source_attestation_locked(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
) -> dict[str, Any]:
    context_before = _validated_attested_replay_context(
        attestation_path=attestation_path,
        expected_attestation_sha256=expected_attestation_sha256,
        frozen_source_root=frozen_source_root,
        expected_frozen_source_commit=expected_frozen_source_commit,
        feature_history_run_spec_path=feature_history_run_spec_path,
        feature_history_run_root=feature_history_run_root,
    )
    spec_path = Path(context_before["spec_path"])
    run_root = Path(context_before["run_root"])
    feature = context_before["feature_history"]
    replay = _validated_feature_identity(
        _replay_current_feature_history(
            attestation_path=attestation_path,
            expected_attestation_sha256=expected_attestation_sha256,
            frozen_source_root=frozen_source_root,
            expected_frozen_source_commit=expected_frozen_source_commit,
            feature_history_run_spec_path=spec_path,
            feature_history_run_root=run_root,
        )
    )
    if replay != feature:
        raise ValueError("factor-v3 frozen-source current replay rejected")
    context_after = _validated_attested_replay_context(
        attestation_path=attestation_path,
        expected_attestation_sha256=expected_attestation_sha256,
        frozen_source_root=frozen_source_root,
        expected_frozen_source_commit=expected_frozen_source_commit,
        feature_history_run_spec_path=feature_history_run_spec_path,
        feature_history_run_root=feature_history_run_root,
    )
    if context_after != context_before:
        raise ValueError("factor-v3 frozen-source source drifted")
    return {
        "authority_binding": context_before["authority_binding"],
        "receipt_sha256": feature["receipt_sha256"],
        "session_count": 250,
        "sessions_sha256": feature["sessions_sha256"],
        "verified": True,
    }


def verify_factor_v3_feature_history_frozen_source_attestation(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
) -> dict[str, Any]:
    from app import factor_v3_feature_history_runner as runner

    with _locked_physical_frozen_source_binding(frozen_source_root):
        paths = runner._run_paths(feature_history_run_root, create=False)
        with _existing_read_only_run_lock(paths["lock"]):
            return _verify_factor_v3_feature_history_frozen_source_attestation_locked(
                attestation_path=attestation_path,
                expected_attestation_sha256=expected_attestation_sha256,
                frozen_source_root=frozen_source_root,
                expected_frozen_source_commit=expected_frozen_source_commit,
                feature_history_run_spec_path=feature_history_run_spec_path,
                feature_history_run_root=feature_history_run_root,
            )
