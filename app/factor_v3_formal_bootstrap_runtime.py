from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import ctypes
from ctypes import wintypes
import errno
import hashlib
import hmac
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import msvcrt
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType, ModuleType
from typing import Any


_EMBEDDED_CONFIG_JSON: bytes | None = None
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_LOADER_IDENTITY = "factor-v3-verified-source-loader/v1"
_CONFIG_SCHEMA = "factor-v3-formal-bootstrap-render-config/v1"
_AUTHORIZATION_SCHEMA = "factor-v3-formal-bootstrap-execution-authorization/v1"
_CLAIM_SCHEMA = "factor-v3-daily-basic-formal-bootstrap-claim/v1"
_RECEIPT_SCHEMA = "factor-v3-daily-basic-formal-review-signed-payload/v1"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_CAPTURE_CHARS = 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_REPARSE_ATTRIBUTE = 0x00000400
_OS_WRITE = os.write
_OS_CLOSE = os.close
_OS_DUP = os.dup
_OS_DUP2 = os.dup2


class _BootstrapError(RuntimeError):
    pass


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
        raise _BootstrapError("canonical JSON rejected") from exc


def _strict_canonical_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise _BootstrapError(f"{label} rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                _BootstrapError(f"{label} rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _BootstrapError,
    ) as exc:
        raise _BootstrapError(f"{label} rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise _BootstrapError(f"{label} rejected")
    return value


def _reject_credential_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            compact = re.sub(r"[^a-z]", "", str(key).casefold())
            if (
                normalized
                in {
                    "api_key",
                    "credential",
                    "credential_material",
                    "password",
                    "publication_capability",
                    "route_credential",
                    "secret",
                    "token",
                }
                or "capability" in normalized
                or "privatekey" in compact
                or normalized.endswith(("_api_key", "_password", "_secret", "_token"))
            ):
                raise _BootstrapError("credential shape rejected")
            _reject_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _reject_credential_shape(nested)


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _BootstrapError(f"{label} rejected")
    return value


def _write_all(
    descriptor: int,
    raw: bytes,
    *,
    writer: Any = _OS_WRITE,
) -> None:
    view = memoryview(raw)
    offset = 0
    while offset < len(view):
        try:
            written = writer(descriptor, view[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if type(written) is not int or isinstance(written, bool) or written <= 0:
            raise _BootstrapError("terminal output write rejected")
        offset += written


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
    )


def _safe_existing_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise _BootstrapError(f"{label} rejected")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise _BootstrapError(f"{label} rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(current):
            raise _BootstrapError(f"{label} rejected")
    try:
        return path.resolve(strict=True)
    except OSError:
        raise _BootstrapError(f"{label} rejected") from None


def _safe_absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str:
        raise _BootstrapError(f"{label} rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise _BootstrapError(f"{label} rejected")
    return path


def _der_length(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise _BootstrapError("public key rejected")
    first = raw[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(raw) or raw[offset + 1] == 0:
        raise _BootstrapError("public key rejected")
    length = int.from_bytes(raw[offset + 1 : offset + 1 + count], "big")
    if length < 0x80:
        raise _BootstrapError("public key rejected")
    return length, offset + 1 + count


def _der_value(
    raw: bytes,
    offset: int,
    *,
    tag: int,
) -> tuple[bytes, int]:
    if offset >= len(raw) or raw[offset] != tag:
        raise _BootstrapError("public key rejected")
    length, value_offset = _der_length(raw, offset + 1)
    end = value_offset + length
    if end > len(raw):
        raise _BootstrapError("public key rejected")
    return raw[value_offset:end], end


def _positive_der_integer(raw: bytes, offset: int) -> tuple[int, int]:
    value, end = _der_value(raw, offset, tag=0x02)
    if not value or value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
        raise _BootstrapError("public key rejected")
    return int.from_bytes(value, "big"), end


def _parse_rsa3072_spki_der(raw: bytes) -> tuple[int, int]:
    if type(raw) is not bytes:
        raise _BootstrapError("public key rejected")
    outer, end = _der_value(raw, 0, tag=0x30)
    if end != len(raw):
        raise _BootstrapError("public key rejected")
    algorithm, offset = _der_value(outer, 0, tag=0x30)
    if algorithm != bytes.fromhex("06092a864886f70d0101010500"):
        raise _BootstrapError("public key rejected")
    bit_string, end = _der_value(outer, offset, tag=0x03)
    if end != len(outer) or not bit_string or bit_string[0] != 0:
        raise _BootstrapError("public key rejected")
    sequence, end = _der_value(bit_string[1:], 0, tag=0x30)
    if end != len(bit_string) - 1:
        raise _BootstrapError("public key rejected")
    modulus, offset = _positive_der_integer(sequence, 0)
    exponent, end = _positive_der_integer(sequence, offset)
    if (
        end != len(sequence)
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise _BootstrapError("public key rejected")
    return modulus, exponent


def _verify_rsa3072_signature(
    payload: bytes,
    signature: bytes,
    *,
    modulus: int,
    exponent: int,
) -> None:
    if (
        type(payload) is not bytes
        or type(signature) is not bytes
        or len(signature) != 384
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise _BootstrapError("review signature rejected")
    signature_int = int.from_bytes(signature, "big")
    if not 0 < signature_int < modulus:
        raise _BootstrapError("review signature rejected")
    encoded = pow(signature_int, exponent, modulus).to_bytes(384, "big")
    digest_info = (
        bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
    )
    expected = b"\x00\x01" + b"\xff" * (384 - len(digest_info) - 3) + b"\x00" + digest_info
    if not hmac.compare_digest(encoded, expected):
        raise _BootstrapError("review signature rejected")


class _HeldFile:
    def __init__(
        self,
        path: Path,
        *,
        expected_sha256: str,
        label: str,
        max_bytes: int,
        allow_hardlinks: bool = False,
    ) -> None:
        parent = _safe_existing_directory(path.parent, label=f"{label} parent")
        candidate = parent / path.name
        try:
            before = candidate.lstat()
        except OSError:
            raise _BootstrapError(f"{label} unavailable") from None
        link_count = int(getattr(before, "st_nlink", 1))
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_reparse(candidate)
            or before.st_size <= 0
            or before.st_size > max_bytes
            or (link_count < 1 if allow_hardlinks else link_count != 1)
        ):
            raise _BootstrapError(f"{label} rejected")
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
            raise _BootstrapError(f"{label} safe open rejected")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
        stream = os.fdopen(descriptor, "rb")
        try:
            opened = os.fstat(stream.fileno())
            stream.seek(0)
            raw = stream.read(max_bytes + 1)
            stream.seek(0)
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
                or not os.path.samestat(before, opened)
                or len(raw) != opened.st_size
                or hashlib.sha256(raw).hexdigest() != expected_sha256
            ):
                raise _BootstrapError(f"{label} identity rejected")
        except BaseException:
            stream.close()
            raise
        self.path = candidate.resolve(strict=True)
        self.raw = raw
        self._stream = stream
        self._expected_sha256 = expected_sha256
        self._label = label
        self._max_bytes = max_bytes
        self._allow_hardlinks = allow_hardlinks

    def postverify(self) -> None:
        opened = os.fstat(self._stream.fileno())
        parent = _safe_existing_directory(
            self.path.parent,
            label=f"{self._label} parent",
        )
        candidate = parent / self.path.name
        try:
            terminal = candidate.lstat()
        except OSError:
            raise _BootstrapError(f"{self._label} drifted") from None
        self._stream.seek(0)
        raw = self._stream.read(self._max_bytes + 1)
        self._stream.seek(0)
        link_count = int(getattr(terminal, "st_nlink", 1))
        if (
            not os.path.samestat(opened, terminal)
            or _is_reparse(candidate)
            or (link_count < 1 if self._allow_hardlinks else link_count != 1)
            or len(raw) != opened.st_size
            or hashlib.sha256(raw).hexdigest() != self._expected_sha256
        ):
            raise _BootstrapError(f"{self._label} drifted")

    def close(self) -> None:
        self._stream.close()


class _BoundedTextSink(io.TextIOBase):
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._chars = 0

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        if type(value) is not str:
            raise _BootstrapError("trusted text output rejected")
        if self._chars + len(value) > _MAX_CAPTURE_CHARS:
            raise _BootstrapError("trusted text output rejected")
        self._parts.append(value)
        self._chars += len(value)
        return len(value)

    def flush(self) -> None:
        return None

    def value(self) -> str:
        return "".join(self._parts)


class _FrozenActionConfig(Mapping[str, str]):
    def __init__(self, config: Mapping[str, Any]) -> None:
        self._values = MappingProxyType(
            {
                "action": str(config["action"]),
                "formal_input_root": str(config["formal_input_root_sha256"]),
                "formal_output_root": str(config["formal_output_root"]),
                "run_root": str(config["run_root"]),
                "run_spec_path": str(config["run_spec_path"]),
            }
        )
        self._sha256 = hashlib.sha256(_canonical_bytes(dict(self._values))).hexdigest()

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _VerifiedSourceLoader(importlib.abc.Loader):
    def __init__(
        self,
        *,
        fullname: str,
        entry: Mapping[str, Any],
        raw: bytes,
        loaded_ledger: dict[str, dict[str, Any]],
        source_registry: Mapping[str, Mapping[str, Any]],
        nonce: object,
    ) -> None:
        self.fullname = fullname
        self.entry = dict(entry)
        self.raw = raw
        self.loaded_ledger = loaded_ledger
        self.source_registry = source_registry
        self.nonce = nonce
        self.loader_identity = _LOADER_IDENTITY

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        if module.__name__ != self.fullname:
            raise _BootstrapError("verified module identity rejected")
        absolute_path = str(self.entry["absolute_path"])
        expected_ledger = {
            "absolute_path": absolute_path,
            "byte_count": len(self.raw),
            "is_package": bool(self.entry["is_package"]),
            "loader_identity": self.loader_identity,
            "module_name": self.fullname,
            "relative_path": str(self.entry["relative_path"]),
            "source_sha256": str(self.entry["source_sha256"]),
        }
        if (
            self.source_registry.get(self.fullname) != expected_ledger
            or len(self.raw) != self.entry["byte_count"]
            or hashlib.sha256(self.raw).hexdigest() != self.entry["source_sha256"]
        ):
            raise _BootstrapError("verified module source rejected")
        code = compile(
            self.raw,
            absolute_path,
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        module.__file__ = absolute_path
        exec(code, module.__dict__)
        self.loaded_ledger[self.fullname] = expected_ledger


class _VerifiedNamespaceLoader(importlib.abc.Loader):
    loader_identity = _LOADER_IDENTITY

    def __init__(
        self,
        *,
        loaded_ledger: dict[str, dict[str, Any]],
        nonce: object,
    ) -> None:
        self.loaded_ledger = loaded_ledger
        self.nonce = nonce

    def create_module(self, spec: Any) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        expected_ledger = {
            "absolute_path": "<synthetic:scripts>",
            "byte_count": 0,
            "is_package": True,
            "loader_identity": self.loader_identity,
            "module_name": "scripts",
            "relative_path": "<synthetic:scripts>",
            "source_sha256": hashlib.sha256(b"").hexdigest(),
        }
        self.loaded_ledger["scripts"] = expected_ledger


class _VerifiedSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(
        self,
        *,
        entries: Mapping[str, Mapping[str, Any]],
        source_bytes: Mapping[str, bytes],
        loaded_ledger: dict[str, dict[str, Any]],
        source_registry: Mapping[str, Mapping[str, Any]],
        nonce: object,
    ) -> None:
        self.entries = {key: dict(value) for key, value in entries.items()}
        self.source_bytes = MappingProxyType(dict(source_bytes))
        self.loaded_ledger = loaded_ledger
        self.source_registry = MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in source_registry.items()}
        )
        self.nonce = nonce
        self.namespace_loader = _VerifiedNamespaceLoader(
            loaded_ledger=loaded_ledger,
            nonce=nonce,
        )

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> Any:
        del path, target
        if fullname == "scripts":
            return importlib.machinery.ModuleSpec(
                fullname,
                self.namespace_loader,
                is_package=True,
            )
        entry = self.entries.get(fullname)
        if entry is not None:
            loader = _VerifiedSourceLoader(
                fullname=fullname,
                entry=entry,
                raw=self.source_bytes[fullname],
                loaded_ledger=self.loaded_ledger,
                source_registry=self.source_registry,
                nonce=self.nonce,
            )
            return importlib.util.spec_from_loader(
                fullname,
                loader,
                origin=str(entry["absolute_path"]),
                is_package=bool(entry["is_package"]),
            )
        if fullname == "app" or fullname.startswith("app.") or fullname.startswith("scripts."):
            raise ImportError("module is outside verified source manifest")
        return None


class _TrustedBootstrapContext:
    def __init__(
        self,
        *,
        action_config: _FrozenActionConfig,
        finder: _VerifiedSourceFinder,
        handles: list[_HeldFile],
        loaded_ledger: dict[str, dict[str, Any]],
        module_entries: Mapping[str, Mapping[str, Any]],
        nonce: object,
        import_path: tuple[str, ...],
        path_hooks: tuple[Any, ...],
        importer_cache: Mapping[str, Any],
        source_registry: Mapping[str, Mapping[str, Any]],
        stdout_sink: _BoundedTextSink,
        stderr_sink: _BoundedTextSink,
    ) -> None:
        self._action_config = action_config
        self._action_config_sha256 = action_config._sha256
        self._finder = finder
        self._handles = handles
        self._loaded_ledger = loaded_ledger
        self._module_entries = {key: dict(value) for key, value in module_entries.items()}
        self._nonce = nonce
        self._import_path = import_path
        self._path_hooks = path_hooks
        self._importer_cache = dict(importer_cache)
        self._source_registry = MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in source_registry.items()}
        )
        self._stdout_sink = stdout_sink
        self._stderr_sink = stderr_sink
        self._output: bytes | None = None
        self._postverified = False

    def validate_action_config(self, candidate: Any) -> None:
        if (
            candidate is not self._action_config
            or hashlib.sha256(_canonical_bytes(dict(self._action_config))).hexdigest()
            != self._action_config_sha256
        ):
            raise _BootstrapError("frozen action configuration rejected")

    def verified_ledger_entry(self, module_name: str) -> Mapping[str, object]:
        if type(module_name) is not str or module_name not in self._source_registry:
            raise _BootstrapError("verified module ledger rejected")
        return MappingProxyType(dict(self._source_registry[module_name]))

    def assert_verified_module(
        self,
        module_name: str,
        relative_path: str,
        expected_sha256: str,
    ) -> None:
        entry = self.verified_ledger_entry(module_name)
        module = sys.modules.get(module_name)
        loader = getattr(module, "__loader__", None)
        spec = getattr(module, "__spec__", None)
        loaded_entry = self._loaded_ledger.get(module_name)
        if (
            entry.get("relative_path") != relative_path
            or entry.get("source_sha256") != expected_sha256
            or entry.get("loader_identity") != _LOADER_IDENTITY
            or loaded_entry != dict(entry)
            or module is None
            or getattr(loader, "loader_identity", None) != _LOADER_IDENTITY
            or getattr(loader, "nonce", None) is not self._nonce
            or spec is None
            or spec.loader is not loader
        ):
            raise _BootstrapError("verified module assertion rejected")
        expected = self._module_entries.get(module_name)
        if (
            expected is None
            or getattr(module, "__file__", None) != str(expected["absolute_path"])
            or entry.get("byte_count") != expected["byte_count"]
            or entry.get("is_package") != expected["is_package"]
        ):
            raise _BootstrapError("verified module assertion rejected")

    def emit_json(self, value: Any) -> None:
        if self._output is not None:
            raise _BootstrapError("trusted output already emitted")
        _reject_credential_shape(value)
        raw = _canonical_bytes(value)
        if not raw or len(raw) > _MAX_OUTPUT_BYTES:
            raise _BootstrapError("trusted output rejected")
        self._output = raw + b"\n"

    def postverify(self) -> None:
        if self._postverified:
            raise _BootstrapError("terminal postverify already completed")
        self.validate_action_config(self._action_config)
        expected_meta_path = (
            self._finder,
            importlib.machinery.BuiltinImporter,
            importlib.machinery.FrozenImporter,
            importlib.machinery.PathFinder,
        )
        if tuple(sys.meta_path) != expected_meta_path:
            raise _BootstrapError("verified import policy drifted")
        if (
            tuple(sys.path) != self._import_path
            or tuple(sys.path_hooks) != self._path_hooks
            or set(sys.path_importer_cache) != set(self._importer_cache)
            or any(
                sys.path_importer_cache[key] is not value
                for key, value in self._importer_cache.items()
            )
        ):
            raise _BootstrapError("verified import boundary drifted")
        for handle in self._handles:
            handle.postverify()
        loaded_names = {
            name
            for name, module in sys.modules.items()
            if (
                module is not None
                and (
                    name == "app"
                    or name.startswith("app.")
                    or name == "scripts"
                    or name.startswith("scripts.")
                )
            )
        }
        if (
            not {
                "app",
                "scripts",
                "scripts.build_factor_v3_daily_basic_formal_run_spec",
                "scripts.run_factor_v3_daily_basic_formal",
            }.issubset(loaded_names)
            or loaded_names != set(self._loaded_ledger)
            or not (loaded_names - {"scripts"}).issubset(self._source_registry)
            or set(self._source_registry) != set(self._module_entries)
        ):
            raise _BootstrapError("verified loaded module closure rejected")
        for name in sorted(loaded_names):
            module = sys.modules[name]
            loaded_entry = self._loaded_ledger.get(name)
            loader = getattr(module, "__loader__", None)
            spec = getattr(module, "__spec__", None)
            if (
                loaded_entry is None
                or getattr(loader, "loader_identity", None) != _LOADER_IDENTITY
                or getattr(loader, "nonce", None) is not self._nonce
                or spec is None
                or spec.loader is not loader
            ):
                raise _BootstrapError("verified loaded module identity rejected")
            if name == "scripts":
                continue
            expected = self._module_entries.get(name)
            module_file = getattr(module, "__file__", None)
            if (
                expected is None
                or type(module_file) is not str
                or Path(module_file) != Path(str(expected["absolute_path"]))
                or loaded_entry != self._source_registry.get(name)
                or loaded_entry["source_sha256"] != expected["source_sha256"]
                or loaded_entry["byte_count"] != expected["byte_count"]
                or loaded_entry["relative_path"] != expected["relative_path"]
            ):
                raise _BootstrapError("verified loaded module source rejected")
        if self._stdout_sink.value() or self._stderr_sink.value():
            raise _BootstrapError("direct trusted output rejected")
        if self._output is None:
            raise _BootstrapError("trusted output missing")
        self._postverified = True

    def buffered_output(self) -> bytes:
        if not self._postverified or self._output is None:
            raise _BootstrapError("trusted output unavailable")
        return self._output


def _validated_manifest(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = config.get("source_manifest")
    if type(value) is not list or not value:
        raise _BootstrapError("source manifest rejected")
    output = []
    seen_paths: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != {"bytes", "path", "sha256"}:
            raise _BootstrapError("source manifest rejected")
        relative_path = item.get("path")
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        if (
            type(relative_path) is not str
            or relative_path in seen_paths
            or "\\" in relative_path
            or relative_path.startswith("/")
            or ".." in relative_path.split("/")
            or not relative_path.endswith(".py")
            or not (
                relative_path.startswith("app/")
                or relative_path
                in {
                    config.get("builder_relative_path"),
                    config.get("shim_relative_path"),
                }
            )
            or type(byte_count) is not int
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= _MAX_SOURCE_BYTES
            or type(digest) is not str
            or _SHA256_RE.fullmatch(digest) is None
        ):
            raise _BootstrapError("source manifest rejected")
        seen_paths.add(relative_path)
        output.append(
            {
                "bytes": byte_count,
                "path": relative_path,
                "sha256": digest,
            }
        )
    if (
        "app/__init__.py" not in seen_paths
        or config.get("builder_relative_path") not in seen_paths
        or config.get("shim_relative_path") not in seen_paths
        or hashlib.sha256(_canonical_bytes(output)).hexdigest() != config.get("source_root_sha256")
    ):
        raise _BootstrapError("source manifest rejected")
    return output


def _module_name(relative_path: str) -> tuple[str, bool]:
    if relative_path == "app/__init__.py":
        return "app", True
    if relative_path.startswith("app/"):
        nested = relative_path[4:-3].split("/")
        if "__init__" in nested:
            raise _BootstrapError("nested package manifest rejected")
        return "app." + ".".join(nested), False
    if relative_path.startswith("scripts/"):
        return "scripts." + relative_path[8:-3].replace("/", "."), False
    raise _BootstrapError("source module rejected")


def _validated_config() -> dict[str, Any]:
    if type(_EMBEDDED_CONFIG_JSON) is not bytes:
        raise _BootstrapError("embedded configuration unavailable")
    if len(_EMBEDDED_CONFIG_JSON) > _MAX_JSON_BYTES:
        raise _BootstrapError("embedded configuration rejected")
    config = _strict_canonical_json(
        _EMBEDDED_CONFIG_JSON,
        label="embedded configuration",
    )
    fields = {
        "action",
        "authorization_issued_at_utc",
        "authorization_nonce_sha256",
        "base_python_executable_path",
        "base_python_executable_sha256",
        "bootstrap_claim_path",
        "bootstrap_claim_sha256",
        "bootstrap_output_root",
        "builder_relative_path",
        "builder_sha256",
        "expected_branch",
        "expected_commit",
        "execution_authorization_path",
        "execution_authorization_public_key_spki_der_base64",
        "execution_authorization_public_key_spki_sha256",
        "execution_authorization_sha256",
        "feature_attestation_sha256",
        "formal_input_root",
        "formal_input_root_sha256",
        "formal_output_root",
        "formal_runner_sha256",
        "git_executable_path",
        "git_executable_sha256",
        "project_id",
        "python_executable_path",
        "python_executable_sha256",
        "repo_root",
        "review_payload_sha256",
        "review_protocol_sha256",
        "review_public_key_spki_der_base64",
        "review_public_key_spki_sha256",
        "review_receipt_path",
        "review_receipt_sha256",
        "run_root",
        "run_spec_path",
        "run_spec_sha256",
        "runtime_template_sha256",
        "schema",
        "shim_relative_path",
        "shim_sha256",
        "source_manifest",
        "source_root_sha256",
    }
    _reject_credential_shape(config)
    if (
        set(config) != fields
        or config.get("schema") != _CONFIG_SCHEMA
        or config.get("project_id") != "quant-signal-lkj"
        or config.get("action") not in {"build-spec", "run", "verify"}
        or type(config.get("expected_branch")) is not str
        or not config["expected_branch"]
        or _COMMIT_RE.fullmatch(str(config.get("expected_commit"))) is None
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00",
            str(config.get("authorization_issued_at_utc")),
        )
        is None
    ):
        raise _BootstrapError("embedded configuration rejected")
    for field in (
        "base_python_executable_sha256",
        "authorization_nonce_sha256",
        "bootstrap_claim_sha256",
        "builder_sha256",
        "execution_authorization_public_key_spki_sha256",
        "execution_authorization_sha256",
        "feature_attestation_sha256",
        "formal_input_root_sha256",
        "formal_runner_sha256",
        "git_executable_sha256",
        "python_executable_sha256",
        "review_payload_sha256",
        "review_protocol_sha256",
        "review_public_key_spki_sha256",
        "review_receipt_sha256",
        "run_spec_sha256",
        "runtime_template_sha256",
        "shim_sha256",
        "source_root_sha256",
    ):
        _require_sha256(config.get(field), label=field)
    for field in (
        "base_python_executable_path",
        "bootstrap_claim_path",
        "bootstrap_output_root",
        "execution_authorization_path",
        "formal_input_root",
        "formal_output_root",
        "git_executable_path",
        "python_executable_path",
        "repo_root",
        "review_receipt_path",
        "run_root",
        "run_spec_path",
    ):
        _safe_absolute_path(config.get(field), label=field)
    manifest = _validated_manifest(config)
    config["source_manifest"] = manifest
    for base64_field, sha_field in (
        (
            "review_public_key_spki_der_base64",
            "review_public_key_spki_sha256",
        ),
        (
            "execution_authorization_public_key_spki_der_base64",
            "execution_authorization_public_key_spki_sha256",
        ),
    ):
        try:
            public_der = base64.b64decode(
                str(config[base64_field]).encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, ValueError):
            raise _BootstrapError("embedded public key rejected") from None
        if (
            base64.b64encode(public_der).decode("ascii") != config[base64_field]
            or hashlib.sha256(public_der).hexdigest() != config[sha_field]
        ):
            raise _BootstrapError("embedded public key rejected")
        _parse_rsa3072_spki_der(public_der)
    return config


def _validate_cas_path(path: Path, digest: str, *, label: str) -> None:
    if (
        path.name != f"{digest}.json"
        or path.parent.name != digest[:2]
        or path.parent.parent.name != "sha256"
    ):
        raise _BootstrapError(f"{label} CAS rejected")


def _open_held(
    stack: ExitStack,
    handles: list[_HeldFile],
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    max_bytes: int,
    allow_hardlinks: bool = False,
) -> _HeldFile:
    handle = _HeldFile(
        path,
        expected_sha256=expected_sha256,
        label=label,
        max_bytes=max_bytes,
        allow_hardlinks=allow_hardlinks,
    )
    handles.append(handle)
    stack.callback(handle.close)
    return handle


def _git_output(git_path: Path, repo_root: Path, *args: str) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            [str(git_path), "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise _BootstrapError("repository identity rejected") from exc
    if (
        len(completed.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8")) > _MAX_OUTPUT_BYTES
        or completed.stderr
    ):
        raise _BootstrapError("repository identity rejected")
    return completed.stdout.strip()


def _validate_claim_and_receipt(
    config: Mapping[str, Any],
    *,
    claim_raw: bytes,
    receipt_raw: bytes,
    public_der: bytes,
) -> None:
    claim = _strict_canonical_json(claim_raw, label="bootstrap claim")
    expected_claim = {
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "branch": config["expected_branch"],
        "builder_sha256": config["builder_sha256"],
        "formal_input_root_sha256": config["formal_input_root_sha256"],
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "project_id": config["project_id"],
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "review_payload_sha256": config["review_payload_sha256"],
        "review_public_key_spki_sha256": config["review_public_key_spki_sha256"],
        "review_receipt_sha256": config["review_receipt_sha256"],
        "reviewed_commit": config["expected_commit"],
        "reviewed_source_root_sha256": config["source_root_sha256"],
        "schema": _CLAIM_SCHEMA,
        "shim_sha256": config["shim_sha256"],
    }
    if claim != expected_claim:
        raise _BootstrapError("bootstrap claim rejected")
    outer = _strict_canonical_json(receipt_raw, label="review receipt")
    if set(outer) != {"payload", "signature_base64"}:
        raise _BootstrapError("review receipt rejected")
    payload = outer.get("payload")
    signature_text = outer.get("signature_base64")
    fields = {
        "branch",
        "decision",
        "feature_attestation_sha256",
        "formal_input_root_sha256",
        "formal_runner_sha256",
        "issued_at_utc",
        "project_id",
        "review_nonce_sha256",
        "review_protocol_sha256",
        "reviewed_commit",
        "reviewed_source_manifest",
        "reviewed_source_root_sha256",
        "reviewer_key_id",
        "schema",
        "signature_scheme",
    }
    if (
        type(payload) is not dict
        or set(payload) != fields
        or type(signature_text) is not str
        or payload.get("schema") != _RECEIPT_SCHEMA
        or payload.get("project_id") != config["project_id"]
        or payload.get("branch") != config["expected_branch"]
        or payload.get("reviewed_commit") != config["expected_commit"]
        or payload.get("decision") != "APPROVED_NO_P0_P1_P2"
        or payload.get("reviewed_source_manifest") != config["source_manifest"]
        or payload.get("reviewed_source_root_sha256") != config["source_root_sha256"]
        or payload.get("formal_input_root_sha256") != config["formal_input_root_sha256"]
        or payload.get("review_protocol_sha256") != config["review_protocol_sha256"]
        or payload.get("feature_attestation_sha256") != config["feature_attestation_sha256"]
        or payload.get("formal_runner_sha256") != config["formal_runner_sha256"]
        or payload.get("reviewer_key_id") != f"sha256:{config['review_public_key_spki_sha256']}"
        or payload.get("signature_scheme") != "RSASSA-PKCS1-v1_5-SHA256"
        or _SHA256_RE.fullmatch(str(payload.get("review_nonce_sha256"))) is None
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
            r"[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00",
            str(payload.get("issued_at_utc")),
        )
        is None
    ):
        raise _BootstrapError("review receipt payload rejected")
    _reject_credential_shape(payload)
    payload_raw = _canonical_bytes(payload)
    if hashlib.sha256(payload_raw).hexdigest() != config["review_payload_sha256"]:
        raise _BootstrapError("review receipt payload rejected")
    try:
        signature = base64.b64decode(
            signature_text.encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise _BootstrapError("review signature rejected") from None
    if base64.b64encode(signature).decode("ascii") != signature_text:
        raise _BootstrapError("review signature rejected")
    modulus, exponent = _parse_rsa3072_spki_der(public_der)
    _verify_rsa3072_signature(
        payload_raw,
        signature,
        modulus=modulus,
        exponent=exponent,
    )
    runner_entry = next(
        (
            item
            for item in config["source_manifest"]
            if item.get("path") == "app/factor_v3_daily_basic_runner.py"
        ),
        None,
    )
    if runner_entry is None or runner_entry.get("sha256") != config["formal_runner_sha256"]:
        raise _BootstrapError("formal runner sha256 semantic field rejected")


def _validate_execution_authorization(
    config: Mapping[str, Any],
    *,
    authorization_raw: bytes,
    public_der: bytes,
) -> None:
    outer = _strict_canonical_json(
        authorization_raw,
        label="execution authorization",
    )
    if set(outer) != {"payload", "signature_base64"}:
        raise _BootstrapError("execution authorization rejected")
    expected_payload = {
        "action": config["action"],
        "authorization_nonce_sha256": config["authorization_nonce_sha256"],
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "bootstrap_claim_path": config["bootstrap_claim_path"],
        "bootstrap_claim_sha256": config["bootstrap_claim_sha256"],
        "bootstrap_output_root": config["bootstrap_output_root"],
        "builder_relative_path": config["builder_relative_path"],
        "builder_sha256": config["builder_sha256"],
        "expected_branch": config["expected_branch"],
        "expected_commit": config["expected_commit"],
        "feature_attestation_sha256": config["feature_attestation_sha256"],
        "formal_input_root_path": config["formal_input_root"],
        "formal_input_root_sha256": config["formal_input_root_sha256"],
        "formal_output_root": config["formal_output_root"],
        "formal_runner_sha256": config["formal_runner_sha256"],
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "issued_at_utc": config["authorization_issued_at_utc"],
        "project_id": config["project_id"],
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "repo_root": config["repo_root"],
        "review_payload_sha256": config["review_payload_sha256"],
        "review_protocol_sha256": config["review_protocol_sha256"],
        "review_public_key_spki_der_base64": config["review_public_key_spki_der_base64"],
        "review_public_key_spki_sha256": config["review_public_key_spki_sha256"],
        "review_receipt_path": config["review_receipt_path"],
        "review_receipt_sha256": config["review_receipt_sha256"],
        "run_root": config["run_root"],
        "run_spec_path": config["run_spec_path"],
        "run_spec_sha256": config["run_spec_sha256"],
        "runtime_template_sha256": config["runtime_template_sha256"],
        "schema": _AUTHORIZATION_SCHEMA,
        "shim_relative_path": config["shim_relative_path"],
        "shim_sha256": config["shim_sha256"],
        "source_manifest": config["source_manifest"],
        "source_root_sha256": config["source_root_sha256"],
    }
    if outer.get("payload") != expected_payload:
        raise _BootstrapError("execution authorization payload rejected")
    signature_text = outer.get("signature_base64")
    if type(signature_text) is not str:
        raise _BootstrapError("execution authorization signature rejected")
    try:
        signature = base64.b64decode(
            signature_text.encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise _BootstrapError("execution authorization signature rejected") from None
    if base64.b64encode(signature).decode("ascii") != signature_text:
        raise _BootstrapError("execution authorization signature rejected")
    modulus, exponent = _parse_rsa3072_spki_der(public_der)
    try:
        _verify_rsa3072_signature(
            _canonical_bytes(expected_payload),
            signature,
            modulus=modulus,
            exponent=exponent,
        )
    except _BootstrapError as exc:
        raise _BootstrapError("execution authorization signature rejected") from exc


def _module_entries(
    config: Mapping[str, Any],
    *,
    repo_root: Path,
    source_handles: Mapping[str, _HeldFile],
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    entries: dict[str, dict[str, Any]] = {}
    source_bytes: dict[str, bytes] = {}
    for item in config["source_manifest"]:
        relative_path = str(item["path"])
        module_name, is_package = _module_name(relative_path)
        if module_name in entries:
            raise _BootstrapError("source module collision rejected")
        absolute_path = repo_root / Path(*relative_path.split("/"))
        handle = source_handles[relative_path]
        entries[module_name] = {
            "absolute_path": str(absolute_path),
            "byte_count": item["bytes"],
            "is_package": is_package,
            "relative_path": relative_path,
            "source_sha256": item["sha256"],
        }
        source_bytes[module_name] = handle.raw
    return entries, source_bytes


def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = os.path.normcase(str(path.resolve(strict=False)))
    for root in roots:
        root_text = os.path.normcase(str(root))
        try:
            if os.path.commonpath((candidate, root_text)) == root_text:
                return True
        except ValueError:
            continue
    return False


def _freeze_stdlib_import_boundary(
    *,
    repo_root: Path,
) -> tuple[tuple[str, ...], tuple[Any, ...], dict[str, Any]]:
    roots = tuple(
        dict.fromkeys(
            Path(value).resolve(strict=True)
            for value in (sys.base_prefix, sys.base_exec_prefix)
            if type(value) is str and value
        )
    )
    if not roots:
        raise _BootstrapError("stdlib import roots rejected")
    import_path: list[str] = []
    for value in sys.path:
        if type(value) is not str or not value:
            raise _BootstrapError("stdlib import path rejected")
        candidate = Path(value)
        if (
            not candidate.is_absolute()
            or candidate.resolve(strict=False) == repo_root
            or not _is_within(candidate, roots)
        ):
            raise _BootstrapError("stdlib import path rejected")
        import_path.append(value)
    if len(import_path) != len(set(map(os.path.normcase, import_path))):
        raise _BootstrapError("stdlib import path rejected")
    cache: dict[str, Any] = {}
    for key, value in sys.path_importer_cache.items():
        if (
            type(key) is not str
            or not key
            or not Path(key).is_absolute()
            or not _is_within(Path(key), roots)
        ):
            raise _BootstrapError("stdlib importer cache rejected")
        cache[key] = value
    hooks = tuple(sys.path_hooks)
    if not hooks:
        raise _BootstrapError("stdlib path hooks rejected")
    sys.path = list(import_path)
    return tuple(import_path), hooks, cache


def _capture_fd_output(callback: Any) -> Any:
    duplicate = _OS_DUP
    duplicate_to = _OS_DUP2
    close_descriptor = _OS_CLOSE
    saved_stdout = duplicate(1)
    try:
        saved_stderr = duplicate(2)
    except BaseException:
        close_descriptor(saved_stdout)
        raise
    try:
        with tempfile.TemporaryFile(mode="w+b") as stdout_capture:
            with tempfile.TemporaryFile(mode="w+b") as stderr_capture:
                sys.stdout.flush()
                sys.stderr.flush()
                duplicate_to(stdout_capture.fileno(), 1)
                duplicate_to(stderr_capture.fileno(), 2)
                try:
                    value = callback()
                    sys.stdout.flush()
                    sys.stderr.flush()
                finally:
                    duplicate_to(saved_stdout, 1)
                    duplicate_to(saved_stderr, 2)
                stdout_capture.seek(0)
                stderr_capture.seek(0)
                captured_stdout = stdout_capture.read(_MAX_OUTPUT_BYTES + 1)
                captured_stderr = stderr_capture.read(_MAX_OUTPUT_BYTES + 1)
                if captured_stdout or captured_stderr:
                    raise _BootstrapError("direct descriptor output rejected")
                return value
    finally:
        close_descriptor(saved_stdout)
        close_descriptor(saved_stderr)


def _trusted_run() -> bytes:
    if (
        os.name != "nt"
        or sys.argv != ["-c"]
        or type(sys.orig_argv) is not list
        or len(sys.orig_argv) != 6
        or sys.orig_argv[1:5] != ["-I", "-B", "-S", "-c"]
        or type(sys.orig_argv[5]) is not str
        or not sys.orig_argv[5]
        or sys.flags.isolated != 1
        or sys.flags.ignore_environment != 1
        or sys.flags.no_user_site != 1
        or sys.flags.safe_path is not True
        or sys.flags.optimize != 0
        or sys.flags.debug != 0
        or sys.flags.verbose != 0
        or sys.flags.inspect != 0
        or sys.flags.interactive != 0
        or sys.flags.bytes_warning != 0
        or sys.flags.quiet != 0
        or sys.flags.dev_mode is not False
        or sys.flags.utf8_mode != 0
        or sys.flags.warn_default_encoding != 0
        or sys.flags.dont_write_bytecode != 1
        or sys.dont_write_bytecode is not True
        or sys.flags.no_site != 1
        or sys._xoptions != {}
        or sys.warnoptions != []
        or any(
            name == "app"
            or name.startswith("app.")
            or name == "scripts"
            or name.startswith("scripts.")
            for name in sys.modules
        )
    ):
        raise _BootstrapError("fixed bootstrap runtime rejected")
    config = _validated_config()
    python_path = Path(str(config["python_executable_path"]))
    base_python_path = Path(str(config["base_python_executable_path"]))
    if (
        Path(sys.executable) != python_path
        or Path(sys.orig_argv[0]) != base_python_path
        or type(sys._base_executable) is not str
        or Path(sys._base_executable) != base_python_path
    ):
        raise _BootstrapError("fixed bootstrap runtime rejected")
    repo_root = _safe_existing_directory(
        Path(str(config["repo_root"])),
        label="repository root",
    )
    for field in (
        "bootstrap_output_root",
        "formal_input_root",
        "formal_output_root",
        "run_root",
    ):
        resolved = _safe_existing_directory(
            Path(str(config[field])),
            label=field,
        )
        if str(resolved) != config[field]:
            raise _BootstrapError(f"{field} rejected")
    claim_path = Path(str(config["bootstrap_claim_path"]))
    authorization_path = Path(str(config["execution_authorization_path"]))
    receipt_path = Path(str(config["review_receipt_path"]))
    run_spec_path = Path(str(config["run_spec_path"]))
    _validate_cas_path(
        claim_path,
        str(config["bootstrap_claim_sha256"]),
        label="bootstrap claim",
    )
    _validate_cas_path(
        authorization_path,
        str(config["execution_authorization_sha256"]),
        label="execution authorization",
    )
    _validate_cas_path(
        receipt_path,
        str(config["review_receipt_sha256"]),
        label="review receipt",
    )
    try:
        review_public_der = base64.b64decode(
            str(config["review_public_key_spki_der_base64"]).encode("ascii"),
            validate=True,
        )
        authorization_public_der = base64.b64decode(
            str(config["execution_authorization_public_key_spki_der_base64"]).encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise _BootstrapError("embedded public key rejected") from None
    handles: list[_HeldFile] = []
    result: bytes | None = None
    with tempfile.TemporaryDirectory(prefix="factor-v3-external-bootstrap-pycache-") as temporary:
        cache_root = _safe_existing_directory(
            Path(temporary),
            label="isolated pycache",
        )
        if next(cache_root.iterdir(), None) is not None:
            raise _BootstrapError("isolated pycache rejected")
        sys.pycache_prefix = str(cache_root)
        with ExitStack() as stack:
            _open_held(
                stack,
                handles,
                python_path,
                expected_sha256=str(config["python_executable_sha256"]),
                label="python executable",
                max_bytes=_MAX_EXECUTABLE_BYTES,
                allow_hardlinks=True,
            )
            _open_held(
                stack,
                handles,
                base_python_path,
                expected_sha256=str(config["base_python_executable_sha256"]),
                label="base python executable",
                max_bytes=_MAX_EXECUTABLE_BYTES,
                allow_hardlinks=True,
            )
            git_handle = _open_held(
                stack,
                handles,
                Path(str(config["git_executable_path"])),
                expected_sha256=str(config["git_executable_sha256"]),
                label="git executable",
                max_bytes=_MAX_EXECUTABLE_BYTES,
                allow_hardlinks=True,
            )
            authorization_handle = _open_held(
                stack,
                handles,
                authorization_path,
                expected_sha256=str(config["execution_authorization_sha256"]),
                label="execution authorization",
                max_bytes=_MAX_JSON_BYTES,
            )
            claim_handle = _open_held(
                stack,
                handles,
                claim_path,
                expected_sha256=str(config["bootstrap_claim_sha256"]),
                label="bootstrap claim",
                max_bytes=_MAX_JSON_BYTES,
            )
            _open_held(
                stack,
                handles,
                run_spec_path,
                expected_sha256=str(config["run_spec_sha256"]),
                label="run spec",
                max_bytes=_MAX_JSON_BYTES,
            )
            receipt_handle = _open_held(
                stack,
                handles,
                receipt_path,
                expected_sha256=str(config["review_receipt_sha256"]),
                label="review receipt",
                max_bytes=_MAX_JSON_BYTES,
            )
            source_handles: dict[str, _HeldFile] = {}
            for item in config["source_manifest"]:
                relative_path = str(item["path"])
                source_handles[relative_path] = _open_held(
                    stack,
                    handles,
                    repo_root / Path(*relative_path.split("/")),
                    expected_sha256=str(item["sha256"]),
                    label="reviewed source",
                    max_bytes=_MAX_SOURCE_BYTES,
                )
                if len(source_handles[relative_path].raw) != item["bytes"]:
                    raise _BootstrapError("reviewed source size rejected")
            if (
                hashlib.sha256(_canonical_bytes(config["source_manifest"])).hexdigest()
                != config["source_root_sha256"]
            ):
                raise _BootstrapError("reviewed source root rejected")
            _validate_claim_and_receipt(
                config,
                claim_raw=claim_handle.raw,
                receipt_raw=receipt_handle.raw,
                public_der=review_public_der,
            )
            _validate_execution_authorization(
                config,
                authorization_raw=authorization_handle.raw,
                public_der=authorization_public_der,
            )
            git_path = git_handle.path
            identity = {
                "branch": _git_output(
                    git_path,
                    repo_root,
                    "branch",
                    "--show-current",
                ),
                "commit": _git_output(
                    git_path,
                    repo_root,
                    "rev-parse",
                    "HEAD",
                ),
                "root": str(
                    Path(
                        _git_output(
                            git_path,
                            repo_root,
                            "rev-parse",
                            "--show-toplevel",
                        )
                    ).resolve(strict=True)
                ),
                "status": _git_output(
                    git_path,
                    repo_root,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ),
            }
            if identity != {
                "branch": config["expected_branch"],
                "commit": config["expected_commit"],
                "root": str(repo_root),
                "status": "",
            }:
                raise _BootstrapError("repository identity rejected")
            entries, source_bytes = _module_entries(
                config,
                repo_root=repo_root,
                source_handles=source_handles,
            )
            source_registry: dict[str, dict[str, Any]] = {
                name: {
                    "absolute_path": str(entry["absolute_path"]),
                    "byte_count": int(entry["byte_count"]),
                    "is_package": bool(entry["is_package"]),
                    "loader_identity": _LOADER_IDENTITY,
                    "module_name": name,
                    "relative_path": str(entry["relative_path"]),
                    "source_sha256": str(entry["source_sha256"]),
                }
                for name, entry in entries.items()
            }
            loaded_ledger: dict[str, dict[str, Any]] = {}
            nonce = object()
            finder = _VerifiedSourceFinder(
                entries=entries,
                source_bytes=source_bytes,
                loaded_ledger=loaded_ledger,
                nonce=nonce,
                source_registry=source_registry,
            )
            sys.meta_path = [
                finder,
                importlib.machinery.BuiltinImporter,
                importlib.machinery.FrozenImporter,
                importlib.machinery.PathFinder,
            ]
            import_path, path_hooks, importer_cache = _freeze_stdlib_import_boundary(
                repo_root=repo_root
            )
            stdout_sink = _BoundedTextSink()
            stderr_sink = _BoundedTextSink()
            action_config = _FrozenActionConfig(config)
            context = _TrustedBootstrapContext(
                action_config=action_config,
                finder=finder,
                handles=handles,
                loaded_ledger=loaded_ledger,
                module_entries=entries,
                nonce=nonce,
                import_path=import_path,
                path_hooks=path_hooks,
                importer_cache=importer_cache,
                source_registry=source_registry,
                stdout_sink=stdout_sink,
                stderr_sink=stderr_sink,
            )

            def dispatch_reviewed_source() -> None:
                with redirect_stdout(stdout_sink), redirect_stderr(stderr_sink):
                    importlib.import_module("app")
                    builder_name = "scripts." + str(config["builder_relative_path"])[8:-3].replace(
                        "/",
                        ".",
                    )
                    shim_name = "scripts." + str(config["shim_relative_path"])[8:-3].replace(
                        "/",
                        ".",
                    )
                    importlib.import_module(builder_name)
                    shim = importlib.import_module(shim_name)
                    context.assert_verified_module(
                        builder_name,
                        str(config["builder_relative_path"]),
                        str(config["builder_sha256"]),
                    )
                    context.assert_verified_module(
                        shim_name,
                        str(config["shim_relative_path"]),
                        str(config["shim_sha256"]),
                    )
                    dispatch = getattr(shim, "trusted_dispatch", None)
                    if not callable(dispatch):
                        raise _BootstrapError("trusted dispatch unavailable")
                    dispatch_result = dispatch(context, action_config)
                    if (
                        type(dispatch_result) is not int
                        or isinstance(dispatch_result, bool)
                        or dispatch_result != 0
                    ):
                        raise _BootstrapError("trusted dispatch rejected")
                    context.postverify()

            _capture_fd_output(dispatch_reviewed_source)
            result = context.buffered_output()
    if result is None:
        raise _BootstrapError("trusted output unavailable")
    return result


def _main() -> int:
    try:
        output = _trusted_run()
    except BaseException:
        try:
            _write_all(2, b"factor-v3 formal bootstrap rejected\n")
        except BaseException:
            pass
        return 2
    try:
        _write_all(1, output)
    except BaseException:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
