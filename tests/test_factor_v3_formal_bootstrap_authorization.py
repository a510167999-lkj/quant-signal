from __future__ import annotations

import base64
import errno
import json
from pathlib import Path
import subprocess
from typing import Callable

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer
from app import factor_v3_formal_bootstrap_runtime as runtime
from tests.test_factor_v3_formal_bootstrap_renderer import (
    _OPENSSL,
    _canonical_bytes,
    _cas_write,
    _fixture_config,
    _run_rendered,
    _sha256,
    _sign,
)


AUTHORIZATION_SCHEMA = "factor-v3-formal-bootstrap-execution-authorization/v1"


def _trusted_public_der(tmp_path: Path) -> bytes:
    return subprocess.run(
        [
            str(_OPENSSL),
            "pkey",
            "-in",
            str(tmp_path / "test-only-review-private.pem"),
            "-pubout",
            "-outform",
            "DER",
        ],
        check=True,
        capture_output=True,
    ).stdout


def _authorization_payload(
    tmp_path: Path,
    config: dict[str, object],
) -> dict[str, object]:
    run_spec_path = Path(str(config["run_spec_path"]))
    run_spec_raw = _canonical_bytes(
        {
            "schema": "factor-v3-test-authorized-run-spec/v1",
            "session_count": 37,
        }
    )
    run_spec_path.write_bytes(run_spec_raw)
    bootstrap_output_root = (tmp_path / "bootstrap-output").resolve()
    bootstrap_output_root.mkdir(exist_ok=True)
    receipt_outer = json.loads(Path(str(config["review_receipt_path"])).read_text(encoding="utf-8"))
    review_payload = receipt_outer["payload"]
    return {
        "action": config["action"],
        "authorization_nonce_sha256": _sha256(f"authorization:{config['action']}".encode()),
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "bootstrap_claim_path": config["bootstrap_claim_path"],
        "bootstrap_claim_sha256": config["bootstrap_claim_sha256"],
        "bootstrap_output_root": str(bootstrap_output_root),
        "builder_relative_path": config["builder_relative_path"],
        "builder_sha256": config["builder_sha256"],
        "expected_branch": config["expected_branch"],
        "expected_commit": config["expected_commit"],
        "feature_attestation_sha256": review_payload["feature_attestation_sha256"],
        "formal_input_root_path": config["formal_input_root"],
        "formal_input_root_sha256": config["formal_input_root_sha256"],
        "formal_output_root": config["formal_output_root"],
        "formal_runner_sha256": review_payload["formal_runner_sha256"],
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "issued_at_utc": "2026-07-30T12:00:00+00:00",
        "project_id": "quant-signal-lkj",
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "repo_root": config["repo_root"],
        "review_payload_sha256": config["review_payload_sha256"],
        "review_protocol_sha256": config["review_protocol_sha256"],
        "review_public_key_spki_der_base64": config["review_public_key_spki_der_base64"],
        "review_public_key_spki_sha256": config["review_public_key_spki_sha256"],
        "review_receipt_sha256": config["review_receipt_sha256"],
        "run_root": config["run_root"],
        "run_spec_path": str(run_spec_path),
        "run_spec_sha256": _sha256(run_spec_raw),
        "runtime_template_sha256": renderer.RUNTIME_TEMPLATE_SHA256,
        "schema": AUTHORIZATION_SCHEMA,
        "shim_relative_path": config["shim_relative_path"],
        "shim_sha256": config["shim_sha256"],
        "source_manifest": config["source_manifest"],
        "source_root_sha256": config["source_root_sha256"],
    }


def _write_authorization(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[Path, str]:
    private_key = tmp_path / "test-only-review-private.pem"
    payload_raw = _canonical_bytes(payload)
    envelope_raw = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(_sign(tmp_path, private_key, payload_raw)).decode(
                "ascii"
            ),
        }
    )
    return _cas_write(
        (tmp_path / "execution-authorizations").resolve(),
        envelope_raw,
    )


def _authorized_fixture(
    tmp_path: Path,
    *,
    action: str = "verify",
    dispatch_body: str | None = None,
    mutate_payload: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[str, object], dict[str, object], Path, bytes]:
    config = _fixture_config(tmp_path, dispatch_body=dispatch_body)
    config["action"] = action
    payload = _authorization_payload(tmp_path, config)
    if mutate_payload is not None:
        mutate_payload(payload)
    authorization_path, _authorization_sha256 = _write_authorization(
        tmp_path,
        payload,
    )
    return config, payload, authorization_path, _trusted_public_der(tmp_path)


def _render_authorized(
    authorization_path: Path,
    trusted_public_der: bytes,
) -> bytes:
    return renderer.render_factor_v3_formal_bootstrap_from_authorization(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )


def test_renderer_rejects_naked_unsigned_configuration(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)

    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="authorization",
    ):
        renderer.render_factor_v3_formal_bootstrap(config)


def test_authorized_execution_passes_formal_input_semantic_sha(
    tmp_path: Path,
) -> None:
    (
        config,
        payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)

    rendered = _render_authorized(authorization_path, trusted_public_der)
    completed = _run_rendered(rendered, config)

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["formal_input_root"] == payload["formal_input_root_sha256"]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("action", "run"),
        ("run_spec_path", r"C:\tampered\run-spec.json"),
        ("formal_input_root_path", r"C:\tampered\formal-input"),
    ),
)
def test_authorization_tampering_requires_a_new_signature(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    (
        _config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    envelope = json.loads(authorization_path.read_text(encoding="utf-8"))
    envelope["payload"][field] = replacement
    tampered_raw = _canonical_bytes(envelope)
    tampered_path, _digest = _cas_write(
        (tmp_path / f"tampered-{field}").resolve(),
        tampered_raw,
    )

    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="signature",
    ):
        _render_authorized(tampered_path, trusted_public_der)


def test_each_action_requires_an_independent_authorization(
    tmp_path: Path,
) -> None:
    authorization_hashes = set()
    rendered_hashes = set()
    for action in ("build-spec", "run", "verify"):
        action_root = tmp_path / action
        action_root.mkdir()
        (
            _config,
            _payload,
            authorization_path,
            trusted_public_der,
        ) = _authorized_fixture(action_root, action=action)
        authorization_hashes.add(_sha256(authorization_path.read_bytes()))
        rendered_hashes.add(_sha256(_render_authorized(authorization_path, trusted_public_der)))

    assert len(authorization_hashes) == 3
    assert len(rendered_hashes) == 3


@pytest.mark.parametrize(
    "semantic_field",
    ("feature_attestation_sha256", "formal_runner_sha256"),
)
def test_resigned_authorization_rejects_semantic_sha_drift(
    tmp_path: Path,
    semantic_field: str,
) -> None:
    (
        _config,
        payload,
        _authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    payload[semantic_field] = "0" * 64
    authorization_path, _digest = _write_authorization(tmp_path, payload)

    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match=semantic_field.replace("_", " "),
    ):
        _render_authorized(authorization_path, trusted_public_der)


def test_renderer_publishes_bootstrap_and_receipt_with_safe_cas(
    tmp_path: Path,
) -> None:
    (
        _config,
        payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)

    first = renderer.publish_factor_v3_formal_bootstrap(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    second = renderer.publish_factor_v3_formal_bootstrap(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )

    assert first == second
    assert first["schema"] == ("factor-v3-formal-bootstrap-publication-receipt/v1")
    root = Path(str(payload["bootstrap_output_root"]))
    bootstrap_path = root / Path(*str(first["bootstrap_relative_path"]).split("/"))
    receipt_path = root / Path(*str(first["receipt_relative_path"]).split("/"))
    assert _sha256(bootstrap_path.read_bytes()) == first["bootstrap_sha256"]
    assert _sha256(receipt_path.read_bytes()) == first["receipt_sha256"]
    renderer.validate_rendered_factor_v3_formal_bootstrap(bootstrap_path.read_bytes())

    bootstrap_path.write_bytes(b"tampered")
    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="content-addressed",
    ):
        renderer.publish_factor_v3_formal_bootstrap(
            authorization_path=authorization_path,
            trusted_public_key_spki_der=trusted_public_der,
        )


def test_fd_level_stdout_and_subprocess_stderr_bypass_is_rejected(
    tmp_path: Path,
) -> None:
    dispatch_body = (
        "    context.validate_action_config(frozen_action_config)\n"
        "    os = __import__('os')\n"
        "    subprocess = __import__('subprocess')\n"
        "    sys = __import__('sys')\n"
        "    os.write(1, b'direct-fd-stdout')\n"
        "    subprocess.run(\n"
        "        [sys.executable, '-I', '-B', '-S', '-c',\n"
        "         \"import os;os.write(2,b'child-stderr')\"],\n"
        "        check=True,\n"
        "    )\n"
        "    context.emit_json({'status': 'must-not-escape'})\n"
        "    return 0\n"
    )
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path, dispatch_body=dispatch_body)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "must-not-escape" not in completed.stderr
    assert "direct-fd-stdout" not in completed.stderr
    assert "child-stderr" not in completed.stderr


def test_runtime_rejects_xoptions_before_repository_import(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "repository-imported.marker"
    config = _fixture_config(tmp_path, import_marker=marker)
    payload = _authorization_payload(tmp_path, config)
    authorization_path, _digest = _write_authorization(tmp_path, payload)
    rendered = _render_authorized(
        authorization_path,
        _trusted_public_der(tmp_path),
    )

    completed = subprocess.run(
        [
            str(config["python_executable_path"]),
            "-I",
            "-B",
            "-S",
            "-X",
            "dev",
            "-c",
            rendered.decode("utf-8"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
        timeout=60,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert not marker.exists()


def test_run_spec_drift_after_render_is_rejected_before_repository_import(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "repository-imported.marker"
    config = _fixture_config(tmp_path, import_marker=marker)
    payload = _authorization_payload(tmp_path, config)
    authorization_path, _digest = _write_authorization(tmp_path, payload)
    rendered = _render_authorized(
        authorization_path,
        _trusted_public_der(tmp_path),
    )
    Path(str(config["run_spec_path"])).write_bytes(b"tampered")

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert not marker.exists()


def test_runtime_rejects_import_boundary_mutation(tmp_path: Path) -> None:
    dispatch_body = (
        "    context.validate_action_config(frozen_action_config)\n"
        "    sys = __import__('sys')\n"
        "    sys.path_hooks.append(lambda _path: None)\n"
        "    sys.path.append(r'C:\\\\untrusted-import-root')\n"
        "    context.emit_json({'status': 'must-not-escape'})\n"
        "    return 0\n"
    )
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path, dispatch_body=dispatch_body)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "must-not-escape" not in completed.stderr


def test_write_all_retries_eintr_and_short_writes() -> None:
    writes: list[bytes] = []
    outcomes: list[int | BaseException] = [
        InterruptedError(errno.EINTR, "interrupted"),
        2,
        1,
    ]

    def writer(_descriptor: int, value: bytes | memoryview) -> int:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        writes.append(bytes(value[:outcome]))
        return outcome

    runtime._write_all(7, b"abc", writer=writer)

    assert writes == [b"ab", b"c"]
    assert outcomes == []
