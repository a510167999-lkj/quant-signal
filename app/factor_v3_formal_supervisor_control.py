from __future__ import annotations

import base64
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from app import factor_v3_formal_bootstrap_renderer as bootstrap_renderer
from app import factor_v3_formal_control_contract as contract
from app import factor_v3_formal_trusted_supervisor as supervisor


class FormalSupervisorControlError(RuntimeError):
    pass


SUPERVISOR_LAUNCH_PYTHON_FLAGS = ("-I", "-B", "-S", "-P")
SUPERVISOR_PUBLICATION_RECEIPT_SCHEMA = "factor-v3-formal-supervisor-publication-receipt/v1"
_SUPERVISOR_SOURCE_RELATIVE_PATH = "app/factor_v3_formal_trusted_supervisor.py"
_CONTROL_CONTRACT_RELATIVE_PATH = "app/factor_v3_formal_control_contract.py"
_LOADER_TEMPLATE_RELATIVE_PATH = "app/factor_v3_formal_supervisor_loader_runtime.py"
_LOADER_TEMPLATE_PATH = (
    Path(__file__).resolve().with_name("factor_v3_formal_supervisor_loader_runtime.py")
)
SUPERVISOR_EXTERNAL_LOADER_TEMPLATE = (
    _LOADER_TEMPLATE_PATH.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_AUTHORIZATION_BYTES = 4 * 1024 * 1024
_DEFAULT_LAUNCH_LIFETIME = timedelta(minutes=30)
_DEFAULT_WORKER_TIMEOUT_SECONDS = 7 * 24 * 60 * 60
_OPENSSL_PATH = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")
_OPENSSL_SHA256 = "3fbfe9fa1fdfbae35980ce3b1e6f5a2db843a3d4d69b7d1c13168ec2f1b538a6"
_PRODUCTION_EXECUTION_AUTHORIZATION_PRIVATE_KEY_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\.secrets"
    r"\factor_v3_execution_authorization_rsa3072_private.pem"
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
        raise FormalSupervisorControlError("canonical JSON rejected") from exc


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in items:
            if key in output:
                raise FormalSupervisorControlError(f"{label} rejected")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                FormalSupervisorControlError(f"{label} rejected")
            ),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        FormalSupervisorControlError,
    ) as exc:
        raise FormalSupervisorControlError(f"{label} rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        raise FormalSupervisorControlError(f"{label} rejected")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _absolute(value: Any, *, label: str) -> Path:
    if type(value) is not str or not value:
        raise FormalSupervisorControlError(f"{label} rejected")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise FormalSupervisorControlError(f"{label} rejected")
    return path


def _public_environment(snapshot: Mapping[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for name in contract.PUBLIC_ENVIRONMENT:
        value = snapshot.get(name)
        if value is not None:
            if type(value) is not str or not value:
                raise FormalSupervisorControlError("public environment rejected")
            output[name] = value
    return output


def _completion_with_trust(
    *,
    completion_marker_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    return (
        bootstrap_renderer._validate_factor_v3_formal_bootstrap_completion_marker_with_test_trust(
            completion_marker_path=completion_marker_path,
            trusted_public_key_spki_der=trusted_public_key_spki_der,
        )
    )


def _config_with_trust(
    *,
    authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    return bootstrap_renderer._authorized_config(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )


def _held_source(
    stack: ExitStack,
    *,
    path: Path,
    expected_sha256: str | None,
    label: str,
) -> supervisor._HeldFile:
    handle = supervisor._HeldFile(
        path,
        expected_sha256=expected_sha256,
        label=label,
        max_bytes=_MAX_SOURCE_BYTES,
    )
    stack.callback(handle.close)
    return handle


def _validated_inputs(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _config_with_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    completion = _completion_with_trust(
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    marker_path = _absolute(
        str(completion_marker_path),
        label="bootstrap completion marker path",
    )
    if (
        completion["execution_authorization_sha256"] != config["execution_authorization_sha256"]
        or completion["bootstrap_output_root"] != config["bootstrap_output_root"]
        or completion["stdlib_inventory_root_sha256"] != config["stdlib_policy_root_sha256"]
        or completion["completion_marker_path"] != str(marker_path)
    ):
        raise FormalSupervisorControlError("bootstrap publication binding rejected")
    return config, completion


def _supervisor_pins(
    *,
    config: Mapping[str, Any],
    completion: Mapping[str, Any],
    supervisor_source_sha256: str,
) -> supervisor._SupervisorPins:
    return supervisor._SupervisorPins(
        base_python_executable_path=str(config["base_python_executable_path"]),
        base_python_executable_sha256=str(config["base_python_executable_sha256"]),
        bootstrap_completion_marker_path=str(completion["completion_marker_path"]),
        bootstrap_completion_marker_sha256=str(completion["completion_marker_sha256"]),
        bootstrap_completion_schema=contract.PUBLICATION_COMPLETION_SCHEMA,
        control_contract_source_sha256=supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
        execution_public_key_spki_der_base64=str(
            config["execution_authorization_public_key_spki_der_base64"]
        ),
        execution_public_key_spki_sha256=str(
            config["execution_authorization_public_key_spki_sha256"]
        ),
        git_executable_path=str(config["git_executable_path"]),
        git_executable_sha256=str(config["git_executable_sha256"]),
        python_executable_path=str(config["python_executable_path"]),
        python_executable_sha256=str(config["python_executable_sha256"]),
        repo_root=str(config["repo_root"]),
        supervisor_expected_commit=str(config["expected_commit"]),
        supervisor_source_relative_path=_SUPERVISOR_SOURCE_RELATIVE_PATH,
        supervisor_source_sha256=supervisor_source_sha256,
        worker_protocol=contract.WORKER_PROTOCOL,
        worker_terminal_schema=contract.WORKER_TERMINAL_SCHEMA,
    )


def _loader_source(
    *,
    artifact_path: Path,
    artifact_sha256: str,
    public_der: bytes,
    stdlib_policy: Mapping[str, Any],
    template_raw: bytes,
) -> bytes:
    entries = tuple(
        dict(entry)
        for entry in stdlib_policy["entries"]
        if type(entry) is dict
        and entry.get("module") is not None
        and entry.get("kind") in {"source", "extension"}
    )
    if not entries:
        raise FormalSupervisorControlError("supervisor loader stdlib rejected")
    modulus, exponent = supervisor._parse_rsa3072_spki(public_der)
    raw = template_raw.replace(b"\r\n", b"\n")
    replacements = {
        b"_EARLY_STDLIB_ENTRIES: tuple[dict[str, object], ...] | None = None": (
            b"_EARLY_STDLIB_ENTRIES: tuple[dict[str, object], ...] = "
            + ascii(entries).encode("ascii")
        ),
        b"_EXECUTED_SUPERVISOR_PATH: str | None = None": (
            f"_EXECUTED_SUPERVISOR_PATH: str = {str(artifact_path)!r}".encode("utf-8")
        ),
        b"_EXECUTED_SUPERVISOR_SHA256: str | None = None": (
            f"_EXECUTED_SUPERVISOR_SHA256: str = {artifact_sha256!r}".encode("ascii")
        ),
        b"_EXECUTION_PUBLIC_KEY_N: int | None = None": (
            f"_EXECUTION_PUBLIC_KEY_N: int = {modulus}".encode("ascii")
        ),
        b"_EXECUTION_PUBLIC_KEY_E: int | None = None": (
            f"_EXECUTION_PUBLIC_KEY_E: int = {exponent}".encode("ascii")
        ),
    }
    for marker, replacement in replacements.items():
        if raw.count(marker) != 1:
            raise FormalSupervisorControlError("supervisor loader template rejected")
        raw = raw.replace(marker, replacement)
    if (
        not raw
        or b"# EARLY_EXACT_IMPORT_BOUNDARY_COMPLETE" not in raw
        or b"# EXECUTED_SUPERVISOR_IDENTITY_VERIFIED" not in raw
        or b"# EXECUTED_SUPERVISOR_BYTES_EXECUTED" not in raw
    ):
        raise FormalSupervisorControlError("supervisor loader rejected")
    try:
        compile(raw, "<factor-v3-formal-supervisor-loader>", "exec")
    except (SyntaxError, ValueError) as exc:
        raise FormalSupervisorControlError("supervisor loader rejected") from exc
    return raw


def _publication_materials(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> tuple[dict[str, Any], bytes, bytes, bytes, supervisor._SupervisorPins]:
    config, completion = _validated_inputs(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    repo_root = _absolute(str(config["repo_root"]), label="formal repo root")
    with ExitStack() as stack:
        template = _held_source(
            stack,
            path=repo_root / _SUPERVISOR_SOURCE_RELATIVE_PATH,
            expected_sha256=None,
            label="supervisor template",
        )
        control_source = _held_source(
            stack,
            path=repo_root / _CONTROL_CONTRACT_RELATIVE_PATH,
            expected_sha256=supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
            label="supervisor control contract",
        )
        loader_template = _held_source(
            stack,
            path=repo_root / _LOADER_TEMPLATE_RELATIVE_PATH,
            expected_sha256=None,
            label="supervisor loader template",
        )
        template_sha256 = _sha256(template.raw)
        pins = _supervisor_pins(
            config=config,
            completion=completion,
            supervisor_source_sha256=template_sha256,
        )
        rendered = supervisor._render_supervisor_with_pins(
            pins,
            source_raw=template.raw,
            control_contract_raw=control_source.raw,
        )
        artifact_sha256 = _sha256(rendered)
        root = _absolute(
            str(config["bootstrap_output_root"]),
            label="bootstrap output root",
        )
        artifact_relative_path = f"supervisors/sha256/{artifact_sha256[:2]}/{artifact_sha256}.py"
        artifact_path = root / Path(*artifact_relative_path.split("/"))
        loader_raw = _loader_source(
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            public_der=trusted_public_key_spki_der,
            stdlib_policy=config["stdlib_policy"],
            template_raw=loader_template.raw,
        )
        loader_sha256 = _sha256(loader_raw)
        loader_relative_path = f"supervisor_loaders/sha256/{loader_sha256[:2]}/{loader_sha256}.py"
        receipt = {
            "bootstrap_completion_marker_sha256": completion["completion_marker_sha256"],
            "bootstrap_execution_authorization_sha256": config["execution_authorization_sha256"],
            "control_contract_source_sha256": supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
            "executed_supervisor_bytes": len(rendered),
            "executed_supervisor_relative_path": artifact_relative_path,
            "executed_supervisor_sha256": artifact_sha256,
            "reviewed_commit": config["expected_commit"],
            "schema": SUPERVISOR_PUBLICATION_RECEIPT_SCHEMA,
            "stdlib_inventory_root_sha256": config["stdlib_policy_root_sha256"],
            "supervisor_loader_bytes": len(loader_raw),
            "supervisor_loader_relative_path": loader_relative_path,
            "supervisor_loader_sha256": loader_sha256,
            "supervisor_source_sha256": template_sha256,
        }
        receipt_raw = _canonical_bytes(receipt)
        for handle in (template, control_source, loader_template):
            handle.postverify()
        return config, rendered, loader_raw, receipt_raw, pins


def _publish_with_trust(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    config, rendered, loader_raw, receipt_raw, _pins = _publication_materials(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    root = _absolute(str(config["bootstrap_output_root"]), label="bootstrap output root")
    artifact_sha256 = _sha256(rendered)
    loader_sha256 = _sha256(loader_raw)
    receipt_sha256 = _sha256(receipt_raw)
    held_files: list[tuple[Any, bytes]] = []
    with bootstrap_renderer._held_publication_directory_tree(
        root=root,
        targets=(
            ("supervisors", artifact_sha256),
            ("supervisor_loaders", loader_sha256),
            ("supervisor_publication_receipts", receipt_sha256),
        ),
    ) as directories:
        try:
            artifact_path, _artifact_relative = bootstrap_renderer._safe_cas_publish(
                root=root,
                category="supervisors",
                digest=artifact_sha256,
                suffix=".py",
                raw=rendered,
                held_files=held_files,
            )
            loader_path, _loader_relative = bootstrap_renderer._safe_cas_publish(
                root=root,
                category="supervisor_loaders",
                digest=loader_sha256,
                suffix=".py",
                raw=loader_raw,
                held_files=held_files,
            )
            receipt_path, _receipt_relative = bootstrap_renderer._safe_cas_publish(
                root=root,
                category="supervisor_publication_receipts",
                digest=receipt_sha256,
                suffix=".json",
                raw=receipt_raw,
                held_files=held_files,
            )
            for held, expected_raw in held_files:
                bootstrap_renderer._flush_held_win32_directory(
                    path=held.path.parent,
                    handle=directories.handle_for(held.path.parent),
                )
                held.terminal_verify(expected_raw)
            return {
                "bootstrap_execution_authorization_path": str(Path(authorization_path)),
                "bootstrap_completion_marker_path": str(Path(completion_marker_path)),
                "executed_supervisor_path": str(artifact_path),
                "executed_supervisor_sha256": artifact_sha256,
                "supervisor_loader_path": str(loader_path),
                "supervisor_loader_sha256": loader_sha256,
                "supervisor_publication_receipt_path": str(receipt_path),
                "supervisor_publication_receipt_sha256": receipt_sha256,
                "status": "published",
            }
        finally:
            for held, _expected_raw in reversed(held_files):
                held.close()


def publish_factor_v3_formal_supervisor(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
) -> dict[str, Any]:
    return _publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=(
            bootstrap_renderer._production_execution_authorization_public_key_der()
        ),
    )


def _validate_publication_with_trust(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    trusted_public_key_spki_der: bytes,
) -> dict[str, Any]:
    config, rendered, loader_raw, expected_receipt_raw, pins = _publication_materials(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    root = _absolute(str(config["bootstrap_output_root"]), label="bootstrap output root")
    receipt_path = _absolute(
        str(publication_receipt_path),
        label="supervisor publication receipt",
    )
    receipt_sha256 = receipt_path.stem
    if (
        _SHA256_RE.fullmatch(receipt_sha256) is None
        or receipt_path
        != root
        / "supervisor_publication_receipts"
        / "sha256"
        / receipt_sha256[:2]
        / f"{receipt_sha256}.json"
    ):
        raise FormalSupervisorControlError("supervisor publication receipt CAS rejected")
    receipt_handle = supervisor._HeldFile(
        receipt_path,
        expected_sha256=receipt_sha256,
        label="supervisor publication receipt",
        max_bytes=_MAX_AUTHORIZATION_BYTES,
    )
    artifact_sha256 = _sha256(rendered)
    artifact_path = root / "supervisors" / "sha256" / artifact_sha256[:2] / f"{artifact_sha256}.py"
    artifact_handle = supervisor._HeldFile(
        artifact_path,
        expected_sha256=artifact_sha256,
        label="executed supervisor",
        max_bytes=_MAX_SOURCE_BYTES,
    )
    loader_sha256 = _sha256(loader_raw)
    loader_path = root / "supervisor_loaders" / "sha256" / loader_sha256[:2] / f"{loader_sha256}.py"
    loader_handle = supervisor._HeldFile(
        loader_path,
        expected_sha256=loader_sha256,
        label="supervisor loader",
        max_bytes=_MAX_SOURCE_BYTES,
    )
    try:
        if (
            receipt_handle.raw != expected_receipt_raw
            or artifact_handle.raw != rendered
            or loader_handle.raw != loader_raw
        ):
            raise FormalSupervisorControlError("supervisor publication drifted")
        receipt_handle.postverify()
        artifact_handle.postverify()
        loader_handle.postverify()
        return {
            "bootstrap_completion_marker_path": str(completion_marker_path),
            "bootstrap_execution_authorization_path": str(authorization_path),
            "config": config,
            "executed_supervisor_path": str(artifact_path),
            "executed_supervisor_sha256": artifact_sha256,
            "pins": pins,
            "receipt": _strict_json(
                receipt_handle.raw,
                label="supervisor publication receipt",
            ),
            "supervisor_loader_bytes": len(loader_raw),
            "supervisor_loader_path": str(loader_path),
            "supervisor_loader_source": loader_raw.decode("utf-8"),
            "supervisor_loader_sha256": loader_sha256,
            "supervisor_publication_receipt_path": str(receipt_path),
            "supervisor_publication_receipt_sha256": receipt_sha256,
        }
    finally:
        loader_handle.close()
        artifact_handle.close()
        receipt_handle.close()


def validate_factor_v3_formal_supervisor_publication(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
) -> dict[str, Any]:
    return _validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        publication_receipt_path=publication_receipt_path,
        trusted_public_key_spki_der=(
            bootstrap_renderer._production_execution_authorization_public_key_der()
        ),
    )


def _git_output(
    argv: list[str],
    *,
    config: Mapping[str, Any],
) -> bytes:
    executable = supervisor._HeldFile(
        Path(str(config["git_executable_path"])),
        expected_sha256=str(config["git_executable_sha256"]),
        label="fixed Git executable",
        max_bytes=64 * 1024 * 1024,
        allow_hardlinks=True,
    )
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    startupinfo.lpAttributeList = {"handle_list": []}
    try:
        completed = subprocess.run(
            [str(config["git_executable_path"]), *argv],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=_public_environment(os.environ),
            timeout=30,
        )
        executable.postverify()
    except (OSError, subprocess.SubprocessError):
        raise FormalSupervisorControlError("formal source review rejected") from None
    finally:
        executable.close()
    if completed.returncode != 0 or completed.stderr:
        raise FormalSupervisorControlError("formal source review rejected")
    return completed.stdout


def _verify_reviewed_sources(
    publication: Mapping[str, Any],
) -> None:
    config = publication["config"]
    repo_root = str(config["repo_root"])
    expected_commit = str(config["expected_commit"])
    if _COMMIT_RE.fullmatch(expected_commit) is None:
        raise FormalSupervisorControlError("reviewed commit rejected")
    head = (
        _git_output(
            ["-C", repo_root, "rev-parse", "HEAD"],
            config=config,
        )
        .decode("ascii")
        .strip()
    )
    if head != expected_commit:
        raise FormalSupervisorControlError("reviewed commit rejected")
    for relative_path in (
        _SUPERVISOR_SOURCE_RELATIVE_PATH,
        _CONTROL_CONTRACT_RELATIVE_PATH,
        _LOADER_TEMPLATE_RELATIVE_PATH,
    ):
        blob = _git_output(
            ["-C", repo_root, "show", f"{expected_commit}:{relative_path}"],
            config=config,
        )
        path = Path(repo_root) / relative_path
        handle = supervisor._HeldFile(
            path,
            expected_sha256=None,
            label="reviewed supervisor source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        try:
            if handle.raw.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
                raise FormalSupervisorControlError("reviewed supervisor source drifted")
            handle.postverify()
        finally:
            handle.close()


def _openssl(
    argv: list[str],
    *,
    input_raw: bytes | None,
) -> bytes:
    executable = supervisor._HeldFile(
        _OPENSSL_PATH,
        expected_sha256=_OPENSSL_SHA256,
        label="fixed OpenSSL executable",
        max_bytes=64 * 1024 * 1024,
        allow_hardlinks=True,
    )
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    startupinfo.lpAttributeList = {"handle_list": []}
    try:
        completed = subprocess.run(
            [str(_OPENSSL_PATH), *argv],
            check=False,
            input=input_raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=_public_environment(os.environ),
            timeout=30,
        )
        executable.postverify()
    except (OSError, subprocess.SubprocessError):
        raise FormalSupervisorControlError("launch authorization signing rejected") from None
    finally:
        executable.close()
    if completed.returncode != 0 or completed.stderr or not completed.stdout:
        raise FormalSupervisorControlError("launch authorization signing rejected")
    return completed.stdout


def _validated_resume_original(
    *,
    publication: Mapping[str, Any],
    original_path: Path,
    status_path: Path,
    ledger_root: Path,
) -> tuple[dict[str, Any], str, str, str]:
    handles: list[supervisor._HeldFile] = []
    try:
        authorization_sha256 = original_path.stem
        if _SHA256_RE.fullmatch(authorization_sha256) is None:
            raise FormalSupervisorControlError("resume launch rejected")
        supervisor._validate_cas_path(
            original_path,
            authorization_sha256,
            category="launch_authorizations",
            suffix=".json",
            label="resume authorization",
        )
        bootstrap_root = _absolute(
            str(publication["config"]["bootstrap_output_root"]),
            label="bootstrap output root",
        )
        expected_original_path = (
            bootstrap_root
            / "launch_authorizations"
            / "sha256"
            / authorization_sha256[:2]
            / f"{authorization_sha256}.json"
        )
        if os.path.normcase(str(original_path)) != os.path.normcase(
            str(expected_original_path)
        ):
            raise FormalSupervisorControlError("resume launch rejected")
        original_handle = supervisor._HeldFile(
            original_path,
            expected_sha256=authorization_sha256,
            label="resume authorization",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        handles.append(original_handle)
        outer = _strict_json(original_handle.raw, label="resume authorization")
        if set(outer) != {"payload", "signature_base64"}:
            raise FormalSupervisorControlError("resume launch rejected")
        original_value = outer.get("payload")
        if type(original_value) is not dict:
            raise FormalSupervisorControlError("resume launch rejected")
        original_validation_time = supervisor._parse_utc(
            original_value.get("not_before_utc"),
            label="resume original not-before time",
        )
        original_payload = supervisor._validate_launch_payload(
            original_value,
            pins=publication["pins"],
            now_utc=original_validation_time,
            trusted_executed_supervisor_path=publication["executed_supervisor_path"],
            trusted_executed_supervisor_sha256=publication["executed_supervisor_sha256"],
            trusted_supervisor_loader_path=publication["supervisor_loader_path"],
            trusted_supervisor_loader_sha256=publication["supervisor_loader_sha256"],
        )
        signature = supervisor._decoded_signature(outer.get("signature_base64"))
        public_der = base64.b64decode(
            publication["pins"].execution_public_key_spki_der_base64.encode("ascii"),
            validate=True,
        )
        supervisor._verify_signature(
            _canonical_bytes(original_payload),
            signature,
            public_der=public_der,
        )
        if (
            original_payload["schema"] != supervisor.LAUNCH_AUTHORIZATION_SCHEMA
            or original_payload["action"] != "run"
            or original_payload["worker_action"] != "run"
            or original_payload["bootstrap_execution_authorization_sha256"]
            != publication["config"]["execution_authorization_sha256"]
        ):
            raise FormalSupervisorControlError("resume launch rejected")
        expected_status_path = supervisor.claim_path_for_authorization(
            ledger_root,
            authorization_sha256,
        )
        if os.path.normcase(str(status_path)) != os.path.normcase(
            str(expected_status_path)
        ):
            raise FormalSupervisorControlError("resume launch rejected")
        status_handle = supervisor._HeldFile(
            status_path,
            expected_sha256=None,
            label="resume status",
            max_bytes=_MAX_AUTHORIZATION_BYTES,
        )
        handles.append(status_handle)
        status = _strict_json(status_handle.raw, label="resume status")
        signature_sha256 = _sha256(signature)
        if (
            set(status) != supervisor._CLAIM_FIELDS
            or status.get("schema") != supervisor.CLAIM_SCHEMA
            or status.get("status") != "claimed"
            or status.get("action") != "run"
            or status.get("launch_authorization_sha256") != authorization_sha256
            or status.get("launch_authorization_schema")
            != supervisor.LAUNCH_AUTHORIZATION_SCHEMA
            or status.get("launch_authorization_signature_sha256") != signature_sha256
            or status.get("authorization_id_sha256")
            != original_payload["authorization_id_sha256"]
            or status.get("authorization_nonce_sha256")
            != original_payload["authorization_nonce_sha256"]
            or status.get("bootstrap_execution_authorization_sha256")
            != original_payload["bootstrap_execution_authorization_sha256"]
            or status.get("replay_scope") != original_payload["replay_scope"]
        ):
            raise FormalSupervisorControlError("resume launch rejected")
        status_sha256 = _sha256(status_handle.raw)
        nonce_replay_sha256 = _sha256(
            _canonical_bytes(
                {
                    "authorization_nonce_sha256": original_payload[
                        "authorization_nonce_sha256"
                    ],
                    "replay_scope": original_payload["replay_scope"],
                }
            )
        )
        for category, identity in (
            (
                "bootstrap_authorizations",
                original_payload["bootstrap_execution_authorization_sha256"],
            ),
            ("authorization_ids", original_payload["authorization_id_sha256"]),
            ("authorization_nonces", nonce_replay_sha256),
        ):
            tuple_path = (
                ledger_root
                / category
                / "sha256"
                / str(identity)[:2]
                / f"{identity}.json"
            )
            tuple_handle = supervisor._HeldFile(
                tuple_path,
                expected_sha256=status_sha256,
                label="resume replay tuple",
                max_bytes=_MAX_AUTHORIZATION_BYTES,
            )
            handles.append(tuple_handle)
            if tuple_handle.raw != status_handle.raw:
                raise FormalSupervisorControlError("resume launch rejected")
        for handle in handles:
            handle.postverify()
        return (
            original_payload,
            authorization_sha256,
            signature_sha256,
            status_sha256,
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        supervisor.FormalSupervisorError,
    ):
        raise FormalSupervisorControlError("resume launch rejected") from None
    finally:
        for handle in reversed(handles):
            handle.close()


def _launch_payload(
    *,
    publication: Mapping[str, Any],
    action: str,
    credential_path: Path | str | None,
    execution_ledger_root: Path | str,
    now_utc: datetime,
    resume_authorization_path: Path | str | None,
    resume_status_path: Path | str | None,
) -> dict[str, Any]:
    config = publication["config"]
    receipt = publication["receipt"]
    completion = _completion_with_trust(
        completion_marker_path=publication["bootstrap_completion_marker_path"],
        trusted_public_key_spki_der=base64.b64decode(
            config["execution_authorization_public_key_spki_der_base64"].encode("ascii"),
            validate=True,
        ),
    )
    if action not in contract.WORKER_ACTION_BY_LAUNCH_ACTION:
        raise FormalSupervisorControlError("launch action rejected")
    terminal_guard_request = contract.preflight_terminal_guard_request(
        action=contract.WORKER_ACTION_BY_LAUNCH_ACTION[action],
        run_root=str(config["run_root"]),
    )
    if (
        action in {"build-spec", "preflight"}
    ) != (terminal_guard_request is not None):
        raise FormalSupervisorControlError(
            "preflight terminal guard request rejected"
        )
    if now_utc.tzinfo != timezone.utc or now_utc.microsecond != 0:
        raise FormalSupervisorControlError("launch time rejected")
    launch_id = _sha256(
        _canonical_bytes(
            {
                "action": action,
                "bootstrap_authorization_id_sha256": config["authorization_id_sha256"],
                "domain": "factor-v3-formal-supervisor-launch-id/v2",
                "executed_supervisor_sha256": publication["executed_supervisor_sha256"],
            }
        )
    )
    launch_nonce = _sha256(
        _canonical_bytes(
            {
                "bootstrap_authorization_nonce_sha256": config["authorization_nonce_sha256"],
                "domain": "factor-v3-formal-supervisor-launch-nonce/v2",
                "supervisor_loader_sha256": publication["supervisor_loader_sha256"],
            }
        )
    )
    credential = (
        None
        if credential_path is None
        else str(_absolute(str(credential_path), label="credential path"))
    )
    ledger_root = _absolute(
        str(execution_ledger_root),
        label="execution ledger root",
    )
    if not ledger_root.is_dir():
        raise FormalSupervisorControlError("execution ledger root rejected")
    resume_values = {
        "resume_of_authorization_id_sha256": None,
        "resume_of_authorization_nonce_sha256": None,
        "resume_of_authorization_sha256": None,
        "resume_of_action": None,
        "resume_of_bootstrap_execution_authorization_sha256": None,
        "resume_of_launch_authorization_schema": None,
        "resume_of_launch_authorization_signature_sha256": None,
        "resume_of_replay_scope": None,
        "resume_status_path": None,
        "resume_status_sha256": None,
    }
    if action == "resume":
        if resume_authorization_path is None or resume_status_path is None:
            raise FormalSupervisorControlError("resume launch rejected")
        original_path = _absolute(
            str(resume_authorization_path),
            label="resume authorization",
        )
        status_path = _absolute(str(resume_status_path), label="resume status")
        (
            original_payload,
            original_authorization_sha256,
            original_signature_sha256,
            status_sha256,
        ) = _validated_resume_original(
            publication=publication,
            original_path=original_path,
            status_path=status_path,
            ledger_root=ledger_root,
        )
        resume_values = {
            "resume_of_authorization_id_sha256": original_payload["authorization_id_sha256"],
            "resume_of_authorization_nonce_sha256": original_payload["authorization_nonce_sha256"],
            "resume_of_authorization_sha256": original_authorization_sha256,
            "resume_of_action": original_payload["action"],
            "resume_of_bootstrap_execution_authorization_sha256": config[
                "execution_authorization_sha256"
            ],
            "resume_of_launch_authorization_schema": original_payload["schema"],
            "resume_of_launch_authorization_signature_sha256": (
                original_signature_sha256
            ),
            "resume_of_replay_scope": contract.EXECUTION_REPLAY_SCOPE,
            "resume_status_path": str(status_path),
            "resume_status_sha256": status_sha256,
        }
        launch_id = str(resume_values["resume_of_authorization_id_sha256"])
        launch_nonce = str(resume_values["resume_of_authorization_nonce_sha256"])
    payload = {
        "action": action,
        "authorization_id_sha256": launch_id,
        "authorization_nonce_sha256": launch_nonce,
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "bootstrap_authorization_id_sha256": config["authorization_id_sha256"],
        "bootstrap_authorization_nonce_sha256": config["authorization_nonce_sha256"],
        "bootstrap_execution_authorization_path": str(
            publication["bootstrap_execution_authorization_path"]
        ),
        "bootstrap_execution_authorization_sha256": config["execution_authorization_sha256"],
        "bootstrap_output_root": config["bootstrap_output_root"],
        "bootstrap_worker_path": completion["bootstrap_path"],
        "bootstrap_worker_sha256": completion["bootstrap_sha256"],
        "control_contract_descriptor_sha256": contract.control_contract_descriptor_sha256(),
        "control_contract_source_sha256": supervisor._CONTROL_CONTRACT_SOURCE_SHA256,
        "credential_path": credential if action in {"run", "resume"} else None,
        "credential_slot_id": "points-primary" if action in {"run", "resume"} else None,
        "environment_policy": contract.worker_environment_policy(),
        "executed_supervisor_path": publication["executed_supervisor_path"],
        "executed_supervisor_sha256": publication["executed_supervisor_sha256"],
        "execution_key_id": config["execution_authorization_key_id"],
        "execution_key_role": supervisor.BOOTSTRAP_EXECUTION_AUTHORIZATION_KEY_ROLE,
        "execution_ledger_root": str(ledger_root),
        "expires_at_utc": (now_utc + _DEFAULT_LAUNCH_LIFETIME).isoformat(timespec="seconds"),
        "formal_input_root_path": config["formal_input_root"],
        "formal_input_root_sha256": config["formal_input_root_sha256"],
        "formal_output_root": config["formal_output_root"],
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "issued_at_utc": now_utc.isoformat(timespec="seconds"),
        "not_before_utc": now_utc.isoformat(timespec="seconds"),
        "project_id": "quant-signal-lkj",
        "publication_completion_marker_path": completion["completion_marker_path"],
        "publication_completion_marker_sha256": completion["completion_marker_sha256"],
        "publication_completion_schema": contract.PUBLICATION_COMPLETION_SCHEMA,
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "repo_root": config["repo_root"],
        "replay_scope": contract.EXECUTION_REPLAY_SCOPE,
        "review_public_key_spki_sha256": config["review_public_key_spki_sha256"],
        "reviewed_commit": config["expected_commit"],
        "run_root": config["run_root"],
        "run_spec_path": config["run_spec_path"],
        "run_spec_sha256": config["run_spec_sha256"],
        "schema": supervisor.LAUNCH_AUTHORIZATION_SCHEMA,
        "source_root_sha256": config["source_root_sha256"],
        "stdlib_inventory_root_sha256": config["stdlib_policy_root_sha256"],
        "stdlib_policy_path": completion["stdlib_policy_path"],
        "stdlib_policy_sha256": completion["stdlib_policy_sha256"],
        "supervisor_expected_commit": config["expected_commit"],
        "supervisor_loader_bytes": publication["supervisor_loader_bytes"],
        "supervisor_loader_path": publication["supervisor_loader_path"],
        "supervisor_loader_sha256": receipt["supervisor_loader_sha256"],
        "supervisor_publication_receipt_path": publication["supervisor_publication_receipt_path"],
        "supervisor_publication_receipt_sha256": publication[
            "supervisor_publication_receipt_sha256"
        ],
        "supervisor_source_sha256": receipt["supervisor_source_sha256"],
        "worker_action": contract.WORKER_ACTION_BY_LAUNCH_ACTION[action],
        "worker_argv": contract.exact_worker_argv(
            python_executable_path=config["python_executable_path"],
            bootstrap_worker_path=completion["bootstrap_path"],
            pycache_prefix=config["stdlib_policy"]["pycache_prefix"],
        ),
        "worker_protocol": contract.WORKER_PROTOCOL,
        "worker_pycache_prefix": config["stdlib_policy"]["pycache_prefix"],
        "worker_terminal_schema": contract.WORKER_TERMINAL_SCHEMA,
        "worker_timeout_seconds": _DEFAULT_WORKER_TIMEOUT_SECONDS,
        **resume_values,
    }
    if set(payload) != supervisor._LAUNCH_FIELDS:
        raise FormalSupervisorControlError("launch payload field set rejected")
    return payload


def _build_factor_v3_formal_supervisor_launch_authorization_with_trust(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    private_key_path: Path | str,
    trusted_public_key_spki_der: bytes,
    verify_reviewed_sources: bool,
    action: str,
    execution_ledger_root: Path | str,
    credential_path: Path | str | None = None,
    resume_authorization_path: Path | str | None = None,
    resume_status_path: Path | str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    publication = _validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        publication_receipt_path=publication_receipt_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    if verify_reviewed_sources:
        _verify_reviewed_sources(publication)
    current = datetime.now(timezone.utc).replace(microsecond=0) if now_utc is None else now_utc
    payload = _launch_payload(
        publication=publication,
        action=action,
        credential_path=credential_path,
        execution_ledger_root=execution_ledger_root,
        now_utc=current,
        resume_authorization_path=resume_authorization_path,
        resume_status_path=resume_status_path,
    )
    private_path = _absolute(str(private_key_path), label="execution private key")
    private_handle = supervisor._HeldFile(
        private_path,
        expected_sha256=None,
        label="execution private key",
        max_bytes=64 * 1024,
    )
    try:
        derived_der = _openssl(
            ["pkey", "-in", str(private_path), "-pubout", "-outform", "DER"],
            input_raw=None,
        )
        if _sha256(derived_der) != _sha256(trusted_public_key_spki_der):
            raise FormalSupervisorControlError("execution private key role rejected")
        signature = _openssl(
            ["dgst", "-sha256", "-sign", str(private_path)],
            input_raw=_canonical_bytes(payload),
        )
        private_handle.postverify()
    finally:
        private_handle.close()
    outer_raw = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }
    )
    root = _absolute(
        str(publication["config"]["bootstrap_output_root"]),
        label="bootstrap output root",
    )
    digest = _sha256(outer_raw)
    held_files: list[tuple[Any, bytes]] = []
    with bootstrap_renderer._held_publication_directory_tree(
        root=root,
        targets=(("launch_authorizations", digest),),
    ) as directories:
        try:
            path, _relative = bootstrap_renderer._safe_cas_publish(
                root=root,
                category="launch_authorizations",
                digest=digest,
                suffix=".json",
                raw=outer_raw,
                held_files=held_files,
            )
            for held, expected_raw in held_files:
                bootstrap_renderer._flush_held_win32_directory(
                    path=held.path.parent,
                    handle=directories.handle_for(held.path.parent),
                )
                held.terminal_verify(expected_raw)
            return {
                "launch_authorization_path": str(path),
                "launch_authorization_sha256": digest,
                "status": "authorized",
            }
        finally:
            for held, _expected_raw in reversed(held_files):
                held.close()


def build_factor_v3_formal_supervisor_launch_authorization(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    action: str,
    execution_ledger_root: Path | str,
    credential_path: Path | str | None = None,
    resume_authorization_path: Path | str | None = None,
    resume_status_path: Path | str | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    return _build_factor_v3_formal_supervisor_launch_authorization_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        publication_receipt_path=publication_receipt_path,
        private_key_path=_PRODUCTION_EXECUTION_AUTHORIZATION_PRIVATE_KEY_PATH,
        trusted_public_key_spki_der=(
            bootstrap_renderer._production_execution_authorization_public_key_der()
        ),
        verify_reviewed_sources=True,
        action=action,
        execution_ledger_root=execution_ledger_root,
        credential_path=credential_path,
        resume_authorization_path=resume_authorization_path,
        resume_status_path=resume_status_path,
        now_utc=now_utc,
    )


def _launch_factor_v3_formal_supervisor_with_trust(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    launch_authorization_path: Path | str,
    trusted_public_key_spki_der: bytes,
    environment_snapshot: Mapping[str, str],
) -> dict[str, Any]:
    publication = _validate_publication_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        publication_receipt_path=publication_receipt_path,
        trusted_public_key_spki_der=trusted_public_key_spki_der,
    )
    launch_path = _absolute(
        str(launch_authorization_path),
        label="launch authorization",
    )
    launch_sha256 = launch_path.stem
    launch_handle = supervisor._HeldFile(
        launch_path,
        expected_sha256=launch_sha256,
        label="launch authorization",
        max_bytes=_MAX_AUTHORIZATION_BYTES,
    )
    artifact_handle = supervisor._HeldFile(
        Path(publication["executed_supervisor_path"]),
        expected_sha256=publication["executed_supervisor_sha256"],
        label="executed supervisor",
        max_bytes=_MAX_SOURCE_BYTES,
    )
    loader_handle = supervisor._HeldFile(
        Path(publication["supervisor_loader_path"]),
        expected_sha256=publication["supervisor_loader_sha256"],
        label="supervisor loader",
        max_bytes=_MAX_SOURCE_BYTES,
    )
    receipt_handle = supervisor._HeldFile(
        Path(publication["supervisor_publication_receipt_path"]),
        expected_sha256=publication["supervisor_publication_receipt_sha256"],
        label="supervisor publication receipt",
        max_bytes=_MAX_AUTHORIZATION_BYTES,
    )
    try:
        outer = _strict_json(launch_handle.raw, label="launch authorization")
        if set(outer) != {"payload", "signature_base64"}:
            raise FormalSupervisorControlError("launch authorization rejected")
        payload = supervisor._validate_launch_payload(
            outer["payload"],
            pins=publication["pins"],
            now_utc=datetime.now(timezone.utc).replace(microsecond=0),
            trusted_executed_supervisor_path=publication["executed_supervisor_path"],
            trusted_executed_supervisor_sha256=publication["executed_supervisor_sha256"],
            trusted_supervisor_loader_path=publication["supervisor_loader_path"],
            trusted_supervisor_loader_sha256=publication["supervisor_loader_sha256"],
        )
        supervisor._verify_signature(
            _canonical_bytes(payload),
            supervisor._decoded_signature(outer["signature_base64"]),
            public_der=trusted_public_key_spki_der,
        )
        loader_source = publication["supervisor_loader_source"]
        if _sha256(loader_source.encode("utf-8")) != payload["supervisor_loader_sha256"]:
            raise FormalSupervisorControlError("supervisor loader drifted")
        environment = _public_environment(environment_snapshot)
        if any(
            name in environment_snapshot
            for name in contract.ACTION_SECRET_ENVIRONMENT[payload["action"]]
        ):
            raise FormalSupervisorControlError("credential present before trusted boundary")
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        startupinfo.lpAttributeList = {"handle_list": []}
        completed = subprocess.run(
            [
                str(payload["python_executable_path"]),
                *SUPERVISOR_LAUNCH_PYTHON_FLAGS,
                str(payload["supervisor_loader_path"]),
                str(launch_path),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(payload["repo_root"]),
            env=environment,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=int(payload["worker_timeout_seconds"]) + 300,
        )
        for handle in (
            launch_handle,
            artifact_handle,
            loader_handle,
            receipt_handle,
        ):
            handle.postverify()
        if completed.returncode != 0 or completed.stderr:
            raise FormalSupervisorControlError("trusted supervisor execution rejected")
        return {
            "launch_authorization_sha256": launch_sha256,
            "status": "completed",
            "worker_terminal_sha256": _sha256(completed.stdout),
        }
    except (OSError, subprocess.SubprocessError):
        raise FormalSupervisorControlError("trusted supervisor execution rejected") from None
    finally:
        receipt_handle.close()
        loader_handle.close()
        artifact_handle.close()
        launch_handle.close()


def launch_factor_v3_formal_supervisor(
    *,
    authorization_path: Path | str,
    completion_marker_path: Path | str,
    publication_receipt_path: Path | str,
    launch_authorization_path: Path | str,
) -> dict[str, Any]:
    return _launch_factor_v3_formal_supervisor_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=completion_marker_path,
        publication_receipt_path=publication_receipt_path,
        launch_authorization_path=launch_authorization_path,
        trusted_public_key_spki_der=(
            bootstrap_renderer._production_execution_authorization_public_key_der()
        ),
        environment_snapshot=os.environ,
    )
