"""Freeze a reviewed configuration into a self-contained Windows bootstrap."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
import zlib


class FormalBootstrapRenderError(RuntimeError):
    pass


CONFIG_SCHEMA = "factor-v3-formal-bootstrap-render-config/v1"
RUNTIME_TEMPLATE_SHA256 = "7ecc8d48202e70f5be882c87174e259ecc41d7a30dca9409ea118b0afcd11f08"
_RUNTIME_TEMPLATE_NAME = "factor_v3_formal_bootstrap_runtime.py"
_CONFIG_MARKER = b"_EMBEDDED_CONFIG_JSON: bytes | None = None"
_RENDERED_CONFIG_PREFIX = b"_EMBEDDED_CONFIG_JSON: bytes = "
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_RUNTIME_BYTES = 8 * 1024 * 1024
_MAX_WINDOWS_C_COMMAND_BYTES = 30_000
_REPARSE_ATTRIBUTE = 0x00000400
_WRAPPER_PREFIX = b"import base64,zlib;exec(compile(zlib.decompress(base64.b64decode(b'"
_WRAPPER_SUFFIX = b"')),'<factor-v3-formal-bootstrap>','exec'))"
_CONFIG_FIELDS = {
    "action",
    "base_python_executable_path",
    "base_python_executable_sha256",
    "bootstrap_claim_path",
    "bootstrap_claim_sha256",
    "builder_relative_path",
    "builder_sha256",
    "expected_branch",
    "expected_commit",
    "formal_input_root",
    "formal_input_root_sha256",
    "formal_output_root",
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
    "schema",
    "shim_relative_path",
    "shim_sha256",
    "source_manifest",
    "source_root_sha256",
}


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
        raise FormalBootstrapRenderError("bootstrap configuration is not canonical JSON") from exc


def _strict_canonical_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise FormalBootstrapRenderError("rendered bootstrap configuration rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                FormalBootstrapRenderError("rendered bootstrap configuration rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        FormalBootstrapRenderError,
    ) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap configuration rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FormalBootstrapRenderError("rendered bootstrap configuration rejected")
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
                raise FormalBootstrapRenderError(
                    "bootstrap configuration contains a credential shape"
                )
            _reject_credential_shape(nested)
    elif type(value) is list:
        for nested in value:
            _reject_credential_shape(nested)


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise FormalBootstrapRenderError(f"{label} rejected")
    return value


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
        raise FormalBootstrapRenderError(f"{label} rejected")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise FormalBootstrapRenderError(f"{label} rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(current):
            raise FormalBootstrapRenderError(f"{label} rejected")
    try:
        return path.resolve(strict=True)
    except OSError:
        raise FormalBootstrapRenderError(f"{label} rejected") from None


def _absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str:
        raise FormalBootstrapRenderError(f"{label} rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalBootstrapRenderError(f"{label} rejected")
    return path


def _read_safe_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    allow_hardlinks: bool = False,
) -> bytes:
    parent = _safe_existing_directory(path.parent, label=f"{label} parent")
    candidate = parent / path.name
    try:
        before = candidate.lstat()
    except OSError:
        raise FormalBootstrapRenderError(f"{label} unavailable") from None
    link_count = int(getattr(before, "st_nlink", 1))
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(candidate)
        or before.st_size <= 0
        or before.st_size > max_bytes
        or (link_count < 1 if allow_hardlinks else link_count != 1)
    ):
        raise FormalBootstrapRenderError(f"{label} rejected")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise FormalBootstrapRenderError(f"{label} unavailable") from None
    try:
        opened = os.fstat(descriptor)
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise FormalBootstrapRenderError(f"{label} rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FormalBootstrapRenderError(f"{label} rejected")
        after = os.fstat(descriptor)
        terminal = candidate.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
            or not os.path.samestat(opened, terminal)
            or _is_reparse(candidate)
            or after.st_size != opened.st_size
        ):
            raise FormalBootstrapRenderError(f"{label} drifted")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _der_length(raw: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    first = raw[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(raw) or raw[offset + 1] == 0:
        raise FormalBootstrapRenderError("embedded public key rejected")
    length = int.from_bytes(raw[offset + 1 : offset + 1 + count], "big")
    if length < 0x80:
        raise FormalBootstrapRenderError("embedded public key rejected")
    return length, offset + 1 + count


def _der_value(
    raw: bytes,
    offset: int,
    *,
    tag: int,
) -> tuple[bytes, int]:
    if offset >= len(raw) or raw[offset] != tag:
        raise FormalBootstrapRenderError("embedded public key rejected")
    length, value_offset = _der_length(raw, offset + 1)
    end = value_offset + length
    if end > len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    return raw[value_offset:end], end


def _positive_der_integer(raw: bytes, offset: int) -> tuple[int, int]:
    value, end = _der_value(raw, offset, tag=0x02)
    if not value or value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
        raise FormalBootstrapRenderError("embedded public key rejected")
    return int.from_bytes(value, "big"), end


def _validate_rsa3072_spki(raw: bytes) -> None:
    outer, end = _der_value(raw, 0, tag=0x30)
    if end != len(raw):
        raise FormalBootstrapRenderError("embedded public key rejected")
    algorithm, offset = _der_value(outer, 0, tag=0x30)
    if algorithm != bytes.fromhex("06092a864886f70d0101010500"):
        raise FormalBootstrapRenderError("embedded public key rejected")
    bit_string, end = _der_value(outer, offset, tag=0x03)
    if end != len(outer) or not bit_string or bit_string[0] != 0:
        raise FormalBootstrapRenderError("embedded public key rejected")
    sequence, end = _der_value(bit_string[1:], 0, tag=0x30)
    if end != len(bit_string) - 1:
        raise FormalBootstrapRenderError("embedded public key rejected")
    modulus, offset = _positive_der_integer(sequence, 0)
    exponent, end = _positive_der_integer(sequence, offset)
    if (
        end != len(sequence)
        or modulus.bit_length() != 3072
        or modulus % 2 != 1
        or exponent != 65537
    ):
        raise FormalBootstrapRenderError("embedded public key rejected")


def _validate_cas_path(path: Path, digest: str, *, label: str) -> None:
    if (
        path.name != f"{digest}.json"
        or path.parent.name != digest[:2]
        or path.parent.parent.name != "sha256"
    ):
        raise FormalBootstrapRenderError(f"{label} CAS rejected")


def _validated_manifest(
    value: Any,
    *,
    repo_root: Path,
    builder_relative_path: str,
    builder_sha256: str,
    shim_relative_path: str,
    shim_sha256: str,
    expected_root_sha256: str,
) -> list[dict[str, Any]]:
    if type(value) is not list or not value:
        raise FormalBootstrapRenderError("source manifest rejected")
    output = []
    seen: set[str] = set()
    for item in value:
        if type(item) is not dict or set(item) != {"bytes", "path", "sha256"}:
            raise FormalBootstrapRenderError("source manifest rejected")
        relative_path = item.get("path")
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        if (
            type(relative_path) is not str
            or relative_path in seen
            or "\\" in relative_path
            or relative_path.startswith("/")
            or ".." in relative_path.split("/")
            or not relative_path.endswith(".py")
            or not (
                relative_path.startswith("app/")
                or relative_path in {builder_relative_path, shim_relative_path}
            )
            or type(byte_count) is not int
            or isinstance(byte_count, bool)
            or not 0 < byte_count <= _MAX_SOURCE_BYTES
        ):
            raise FormalBootstrapRenderError("source manifest rejected")
        digest = _require_sha256(digest, label="source manifest SHA")
        raw = _read_safe_file(
            repo_root / Path(*relative_path.split("/")),
            label="reviewed source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
            raise FormalBootstrapRenderError("source manifest rejected")
        seen.add(relative_path)
        output.append(
            {
                "bytes": byte_count,
                "path": relative_path,
                "sha256": digest,
            }
        )
    by_path = {item["path"]: item for item in output}
    if (
        "app/__init__.py" not in by_path
        or by_path.get(builder_relative_path, {}).get("sha256") != builder_sha256
        or by_path.get(shim_relative_path, {}).get("sha256") != shim_sha256
        or hashlib.sha256(_canonical_bytes(output)).hexdigest() != expected_root_sha256
    ):
        raise FormalBootstrapRenderError(
            "source manifest must bind app/__init__.py, builder, and shim"
        )
    return output


def _validated_config(value: Mapping[str, Any]) -> dict[str, Any]:
    if os.name != "nt" or type(value) is not dict:
        raise FormalBootstrapRenderError(
            "bootstrap renderer requires an exact Windows configuration"
        )
    config = json.loads(_canonical_bytes(value))
    _reject_credential_shape(config)
    if (
        set(config) != _CONFIG_FIELDS
        or config.get("schema") != CONFIG_SCHEMA
        or config.get("project_id") != "quant-signal-lkj"
        or config.get("action") not in {"build-spec", "run", "verify"}
        or type(config.get("expected_branch")) is not str
        or not config["expected_branch"]
        or _COMMIT_RE.fullmatch(str(config.get("expected_commit"))) is None
    ):
        raise FormalBootstrapRenderError("bootstrap configuration rejected")
    for field in (
        "base_python_executable_sha256",
        "bootstrap_claim_sha256",
        "builder_sha256",
        "formal_input_root_sha256",
        "git_executable_sha256",
        "python_executable_sha256",
        "review_payload_sha256",
        "review_protocol_sha256",
        "review_public_key_spki_sha256",
        "review_receipt_sha256",
        "shim_sha256",
        "source_root_sha256",
    ):
        _require_sha256(config.get(field), label=field)
    paths = {
        field: _absolute_path(config.get(field), label=field)
        for field in (
            "base_python_executable_path",
            "bootstrap_claim_path",
            "formal_input_root",
            "formal_output_root",
            "git_executable_path",
            "python_executable_path",
            "repo_root",
            "review_receipt_path",
            "run_root",
            "run_spec_path",
        )
    }
    repo_root = _safe_existing_directory(
        paths["repo_root"],
        label="repository root",
    )
    for field in (
        "formal_input_root",
        "formal_output_root",
        "run_root",
    ):
        _safe_existing_directory(paths[field], label=field)
    for field in (
        "builder_relative_path",
        "shim_relative_path",
    ):
        relative_path = config.get(field)
        if (
            type(relative_path) is not str
            or not relative_path.startswith("scripts/")
            or not relative_path.endswith(".py")
            or "\\" in relative_path
            or ".." in relative_path.split("/")
        ):
            raise FormalBootstrapRenderError(f"{field} rejected")
    config["source_manifest"] = _validated_manifest(
        config["source_manifest"],
        repo_root=repo_root,
        builder_relative_path=config["builder_relative_path"],
        builder_sha256=config["builder_sha256"],
        shim_relative_path=config["shim_relative_path"],
        shim_sha256=config["shim_sha256"],
        expected_root_sha256=config["source_root_sha256"],
    )
    for path_field, sha_field, max_bytes, allow_hardlinks in (
        (
            "python_executable_path",
            "python_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "base_python_executable_path",
            "base_python_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "git_executable_path",
            "git_executable_sha256",
            _MAX_EXECUTABLE_BYTES,
            True,
        ),
        (
            "bootstrap_claim_path",
            "bootstrap_claim_sha256",
            _MAX_CONFIG_BYTES,
            False,
        ),
        (
            "review_receipt_path",
            "review_receipt_sha256",
            _MAX_CONFIG_BYTES,
            False,
        ),
    ):
        raw = _read_safe_file(
            paths[path_field],
            label=path_field,
            max_bytes=max_bytes,
            allow_hardlinks=allow_hardlinks,
        )
        if hashlib.sha256(raw).hexdigest() != config[sha_field]:
            raise FormalBootstrapRenderError(f"{path_field} identity rejected")
    _validate_cas_path(
        paths["bootstrap_claim_path"],
        config["bootstrap_claim_sha256"],
        label="bootstrap claim",
    )
    _validate_cas_path(
        paths["review_receipt_path"],
        config["review_receipt_sha256"],
        label="review receipt",
    )
    try:
        public_der = base64.b64decode(
            config["review_public_key_spki_der_base64"].encode("ascii"),
            validate=True,
        )
    except (AttributeError, UnicodeEncodeError, ValueError):
        raise FormalBootstrapRenderError("embedded public key rejected") from None
    if (
        base64.b64encode(public_der).decode("ascii") != config["review_public_key_spki_der_base64"]
        or hashlib.sha256(public_der).hexdigest() != config["review_public_key_spki_sha256"]
    ):
        raise FormalBootstrapRenderError("embedded public key rejected")
    _validate_rsa3072_spki(public_der)
    return config


def _runtime_template_bytes() -> bytes:
    path = Path(__file__).resolve().with_name(_RUNTIME_TEMPLATE_NAME)
    raw = _read_safe_file(
        path,
        label="bootstrap runtime template",
        max_bytes=1024 * 1024,
    )
    if hashlib.sha256(raw).hexdigest() != RUNTIME_TEMPLATE_SHA256 or raw.count(_CONFIG_MARKER) != 1:
        raise FormalBootstrapRenderError("bootstrap runtime template rejected")
    return raw


def render_factor_v3_formal_bootstrap(
    configuration: Mapping[str, Any],
) -> bytes:
    config = _validated_config(configuration)
    config_raw = _canonical_bytes(config)
    if not config_raw or len(config_raw) > _MAX_CONFIG_BYTES:
        raise FormalBootstrapRenderError("bootstrap configuration rejected")
    replacement = _RENDERED_CONFIG_PREFIX + repr(config_raw).encode("ascii")
    runtime = _runtime_template_bytes().replace(_CONFIG_MARKER, replacement)
    runtime = runtime.rstrip(b"\r\n")
    rendered = _WRAPPER_PREFIX + base64.b64encode(zlib.compress(runtime, level=9)) + _WRAPPER_SUFFIX
    if not rendered or rendered.endswith(b"\n") or len(rendered) > _MAX_WINDOWS_C_COMMAND_BYTES:
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    try:
        compile(rendered, "<factor-v3-formal-bootstrap>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap rejected") from exc
    return rendered


def validate_rendered_factor_v3_formal_bootstrap(raw: bytes) -> bytes:
    if (
        type(raw) is not bytes
        or not raw
        or raw.endswith(b"\n")
        or len(raw) > _MAX_WINDOWS_C_COMMAND_BYTES
        or not raw.startswith(_WRAPPER_PREFIX)
        or not raw.endswith(_WRAPPER_SUFFIX)
    ):
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    try:
        encoded = raw[len(_WRAPPER_PREFIX) : -len(_WRAPPER_SUFFIX)]
        compressed = base64.b64decode(encoded, validate=True)
        decompressor = zlib.decompressobj()
        runtime_raw = decompressor.decompress(
            compressed,
            _MAX_RUNTIME_BYTES + 1,
        )
        runtime_raw += decompressor.flush()
        if (
            not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
            or len(runtime_raw) > _MAX_RUNTIME_BYTES
        ):
            raise FormalBootstrapRenderError("rendered bootstrap rejected")
        source = runtime_raw.decode("utf-8")
        tree = ast.parse(source, filename="<factor-v3-formal-bootstrap>")
    except (
        UnicodeDecodeError,
        SyntaxError,
        ValueError,
        zlib.error,
    ) as exc:
        raise FormalBootstrapRenderError("rendered bootstrap rejected") from exc
    values = []
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_EMBEDDED_CONFIG_JSON"
            and node.value is not None
        ):
            try:
                values.append(ast.literal_eval(node.value))
            except (ValueError, SyntaxError):
                raise FormalBootstrapRenderError("rendered bootstrap rejected") from None
    if len(values) != 1 or type(values[0]) is not bytes:
        raise FormalBootstrapRenderError("rendered bootstrap rejected")
    config = _strict_canonical_json(values[0])
    if render_factor_v3_formal_bootstrap(config) != raw:
        raise FormalBootstrapRenderError("rendered bootstrap drifted")
    return raw
