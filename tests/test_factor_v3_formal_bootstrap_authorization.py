from __future__ import annotations

import base64
import errno
import json
import os
from pathlib import Path
import subprocess
from typing import Callable

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer
from app import factor_v3_formal_bootstrap_runtime as runtime
from tests.test_factor_v3_formal_bootstrap_renderer import (
    _GIT,
    _OPENSSL,
    _canonical_bytes,
    _cas_write,
    _file_sha256,
    _fixture_config,
    _git,
    _run_rendered,
    _sha256,
    _sign,
    _source_entry,
    _test_rsa_key,
)


AUTHORIZATION_SCHEMA = "factor-v3-formal-bootstrap-execution-authorization/v2"
BE3_FORMAL_COMMIT = "be3f985a0cd52f4092441e45056c3ec9da94668e"
BE3_REVIEWED_PATHS = (
    "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
    "scripts/run_factor_v3_daily_basic_formal.py",
    "app/__init__.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/current_pool.py",
    "app/current_pool_gate.py",
    "app/durable_io.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_daily_basic_runner.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
    "app/jiaoch_credential_slots.py",
    "app/jiaoch_daily_basic_collection_set.py",
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


def _review_public_der(tmp_path: Path) -> bytes:
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


def _execution_test_key(tmp_path: Path) -> tuple[Path, bytes]:
    key_root = tmp_path / "test-only-execution-authorization-key"
    private_key = key_root / "test-only-review-private.pem"
    if not private_key.exists():
        key_root.mkdir()
        return _test_rsa_key(key_root)
    public_der = subprocess.run(
        [
            str(_OPENSSL),
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-outform",
            "DER",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return private_key, public_der


def _trusted_public_der(tmp_path: Path) -> bytes:
    return _execution_test_key(tmp_path)[1]


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
    _execution_private_key, execution_public_der = _execution_test_key(tmp_path)
    execution_key_sha256 = _sha256(execution_public_der)
    authorization_nonce_sha256 = _sha256(f"authorization:{config['action']}".encode())
    stdlib_policy = renderer._trusted_stdlib_policy_for_base_python(
        Path(str(config["base_python_executable_path"]))
    )
    config["_test_stdlib_inventory_root_sha256"] = stdlib_policy["inventory_root_sha256"]
    return {
        "action": config["action"],
        "authorization_id_sha256": _sha256(
            f"authorization-id:{authorization_nonce_sha256}".encode()
        ),
        "authorization_nonce_sha256": authorization_nonce_sha256,
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "bootstrap_claim_path": config["bootstrap_claim_path"],
        "bootstrap_claim_sha256": config["bootstrap_claim_sha256"],
        "bootstrap_output_root": str(bootstrap_output_root),
        "builder_relative_path": config["builder_relative_path"],
        "builder_sha256": config["builder_sha256"],
        "expected_branch": config["expected_branch"],
        "expected_commit": config["expected_commit"],
        "execution_authorization_key_id": (f"sha256:{execution_key_sha256}"),
        "execution_authorization_key_role": ("factor-v3-bootstrap-execution-authorization"),
        "expires_at_utc": "2026-07-31T00:00:00+00:00",
        "feature_attestation_sha256": review_payload["feature_attestation_sha256"],
        "formal_input_root_path": config["formal_input_root"],
        "formal_input_root_sha256": config["formal_input_root_sha256"],
        "formal_output_root": config["formal_output_root"],
        "formal_runner_sha256": review_payload["formal_runner_sha256"],
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "issued_at_utc": "2026-07-30T12:00:00+00:00",
        "not_before_utc": "2026-07-30T12:00:00+00:00",
        "project_id": "quant-signal-lkj",
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "repo_root": config["repo_root"],
        "replay_scope": "factor-v3-formal-bootstrap-execution/v1",
        "review_payload_sha256": config["review_payload_sha256"],
        "review_protocol_sha256": config["review_protocol_sha256"],
        "review_public_key_spki_der_base64": config["review_public_key_spki_der_base64"],
        "review_public_key_spki_sha256": config["review_public_key_spki_sha256"],
        "review_receipt_path": config["review_receipt_path"],
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
        "stdlib_policy": stdlib_policy,
        "supervisor_protocol": renderer._supervisor_protocol_descriptor(),
    }


def _write_authorization(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[Path, str]:
    private_key, _public_der = _execution_test_key(tmp_path)
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


def _write_completion_authorization(
    tmp_path: Path,
    payload: dict[str, object],
) -> Path:
    private_key, _public_der = _execution_test_key(tmp_path)
    payload_raw = _canonical_bytes(payload)
    envelope_raw = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(_sign(tmp_path, private_key, payload_raw)).decode(
                "ascii"
            ),
        }
    )
    path, _digest = _cas_write(
        (tmp_path / "completion-authorizations").resolve(),
        envelope_raw,
    )
    return path


def _authorized_fixture(
    tmp_path: Path,
    *,
    action: str = "verify",
    dispatch_body: str | None = None,
    import_marker: Path | None = None,
    mutate_payload: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[str, object], dict[str, object], Path, bytes]:
    config = _fixture_config(
        tmp_path,
        dispatch_body=dispatch_body,
        import_marker=import_marker,
    )
    config["action"] = action
    payload = _authorization_payload(tmp_path, config)
    if mutate_payload is not None:
        mutate_payload(payload)
    authorization_path, _authorization_sha256 = _write_authorization(
        tmp_path,
        payload,
    )
    return config, payload, authorization_path, _trusted_public_der(tmp_path)


def _be3_source(relative_path: str) -> bytes:
    project_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [
            str(_GIT),
            "-C",
            str(project_root),
            "show",
            f"{BE3_FORMAL_COMMIT}:{relative_path}",
        ],
        check=True,
        capture_output=True,
    ).stdout


def _insert_before_trusted_dispatch(source: bytes, override: str) -> bytes:
    text = source.decode("utf-8")
    marker = "\ndef trusted_dispatch("
    assert text.count(marker) == 1
    return text.replace(marker, f"\n{override}\n\ndef trusted_dispatch(", 1).encode()


def _be3_crossline_authorized_fixture(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], Path, bytes]:
    config = _fixture_config(tmp_path)
    repo = Path(str(config["repo_root"]))
    for directory in (repo / "app", repo / "scripts"):
        for path in directory.glob("*.py"):
            path.unlink()
    for relative_path in BE3_REVIEWED_PATHS:
        destination = repo / Path(*relative_path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_be3_source(relative_path))

    formal_input_root_sha256 = _sha256(b"be3-crossline-formal-input")
    builder_path = repo / "scripts" / "build_factor_v3_daily_basic_formal_run_spec.py"
    builder_override = (
        f"FORMAL_WORKTREE_ROOT = Path({str(repo)!r})\n"
        f"EXPECTED_BRANCH = {config['expected_branch']!r}\n"
        f"SPEC_OUTPUT_ROOT = Path({config['formal_output_root']!r})\n"
        f"PLANNED_RUN_ROOT = Path({config['run_root']!r})\n"
        f"FORMAL_INPUT_ROOT_SHA256 = {formal_input_root_sha256!r}"
    )
    builder_path.write_bytes(
        _insert_before_trusted_dispatch(
            builder_path.read_bytes(),
            builder_override,
        )
    )
    shim_path = repo / "scripts" / "run_factor_v3_daily_basic_formal.py"
    shim_path.write_bytes(
        _insert_before_trusted_dispatch(
            shim_path.read_bytes(),
            f"_WORKTREE_ROOT = Path({str(repo)!r})",
        )
    )
    runner_path = repo / "app" / "factor_v3_daily_basic_runner.py"
    runner_path.write_text(
        "from __future__ import annotations\n\n"
        "def verify_factor_v3_daily_basic_run(*, run_spec_path, run_root):\n"
        "    return {\n"
        "        'run_root': run_root,\n"
        "        'run_spec_path': run_spec_path,\n"
        "        'source': 'be3-crossline-lightweight-runner',\n"
        "        'status': 'verified',\n"
        "    }\n\n"
        "def run_factor_v3_daily_basic_collection(*, run_spec_path, run_root):\n"
        "    return verify_factor_v3_daily_basic_run(\n"
        "        run_spec_path=run_spec_path,\n"
        "        run_root=run_root,\n"
        "    )\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "--all")
    commit = _git(repo, "commit", "-m", "be3 crossline fixture")
    assert commit
    reviewed_commit = _git(repo, "rev-parse", "HEAD")
    source_manifest = [_source_entry(repo, relative_path) for relative_path in BE3_REVIEWED_PATHS]
    assert len(source_manifest) == 37
    source_root_sha256 = _sha256(_canonical_bytes(source_manifest))
    public_der = _review_public_der(tmp_path)
    public_der_sha256 = _sha256(public_der)
    feature_attestation_sha256 = _sha256(b"be3-crossline-feature-attestation")
    formal_runner_sha256 = _file_sha256(runner_path)
    review_payload = {
        "branch": config["expected_branch"],
        "decision": "APPROVED_NO_P0_P1_P2",
        "feature_attestation_sha256": feature_attestation_sha256,
        "formal_input_root_sha256": formal_input_root_sha256,
        "formal_runner_sha256": formal_runner_sha256,
        "issued_at_utc": "2026-07-30T00:00:00+00:00",
        "project_id": "quant-signal-lkj",
        "review_nonce_sha256": _sha256(b"be3-crossline-review-nonce"),
        "review_protocol_sha256": config["review_protocol_sha256"],
        "reviewed_commit": reviewed_commit,
        "reviewed_source_manifest": source_manifest,
        "reviewed_source_root_sha256": source_root_sha256,
        "reviewer_key_id": f"sha256:{public_der_sha256}",
        "schema": "factor-v3-daily-basic-formal-review-signed-payload/v1",
        "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
    }
    review_payload_raw = _canonical_bytes(review_payload)
    receipt_raw = _canonical_bytes(
        {
            "payload": review_payload,
            "signature_base64": base64.b64encode(
                _sign(
                    tmp_path,
                    tmp_path / "test-only-review-private.pem",
                    review_payload_raw,
                )
            ).decode("ascii"),
        }
    )
    receipt_path, receipt_sha256 = _cas_write(
        (tmp_path / "be3-crossline-receipts").resolve(),
        receipt_raw,
    )
    claim = {
        "base_python_executable_path": config["base_python_executable_path"],
        "base_python_executable_sha256": config["base_python_executable_sha256"],
        "branch": config["expected_branch"],
        "builder_sha256": _file_sha256(builder_path),
        "formal_input_root_sha256": formal_input_root_sha256,
        "git_executable_path": config["git_executable_path"],
        "git_executable_sha256": config["git_executable_sha256"],
        "project_id": "quant-signal-lkj",
        "python_executable_path": config["python_executable_path"],
        "python_executable_sha256": config["python_executable_sha256"],
        "review_payload_sha256": _sha256(review_payload_raw),
        "review_public_key_spki_sha256": public_der_sha256,
        "review_receipt_sha256": receipt_sha256,
        "reviewed_commit": reviewed_commit,
        "reviewed_source_root_sha256": source_root_sha256,
        "schema": "factor-v3-daily-basic-formal-bootstrap-claim/v1",
        "shim_sha256": _file_sha256(shim_path),
    }
    claim_path, claim_sha256 = _cas_write(
        (tmp_path / "be3-crossline-claims").resolve(),
        _canonical_bytes(claim),
    )
    config.update(
        {
            "bootstrap_claim_path": str(claim_path),
            "bootstrap_claim_sha256": claim_sha256,
            "builder_sha256": claim["builder_sha256"],
            "expected_commit": reviewed_commit,
            "formal_input_root_sha256": formal_input_root_sha256,
            "review_payload_sha256": claim["review_payload_sha256"],
            "review_public_key_spki_der_base64": base64.b64encode(public_der).decode("ascii"),
            "review_public_key_spki_sha256": public_der_sha256,
            "review_receipt_path": str(receipt_path),
            "review_receipt_sha256": receipt_sha256,
            "shim_sha256": claim["shim_sha256"],
            "source_manifest": source_manifest,
            "source_root_sha256": source_root_sha256,
        }
    )
    payload = _authorization_payload(tmp_path, config)
    authorization_path, _authorization_sha256 = _write_authorization(
        tmp_path,
        payload,
    )
    return (
        config,
        payload,
        authorization_path,
        _trusted_public_der(tmp_path),
    )


def _render_authorized(
    authorization_path: Path,
    trusted_public_der: bytes,
) -> bytes:
    return renderer._render_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )


def _run_as_synthetic_supervisor(
    rendered: bytes,
    config: dict[str, object],
    tmp_path: Path,
    *,
    launch_action: str | None = None,
    include_stdlib_prelock: bool = True,
) -> subprocess.CompletedProcess[str]:
    selected_action = str(config["action"]) if launch_action is None else launch_action
    bootstrap_sha256 = _sha256(rendered)
    bootstrap_directory = tmp_path / "synthetic-bootstrap-cas" / "sha256" / bootstrap_sha256[:2]
    bootstrap_directory.mkdir(parents=True)
    bootstrap_path = bootstrap_directory / f"{bootstrap_sha256}.py"
    bootstrap_path.write_bytes(rendered)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    environment.update(
        {
            "FACTOR_V3_FORMAL_LAUNCH_ACTION": selected_action,
            "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256": _sha256(
                b"synthetic-supervisor-launch-envelope"
            ),
            "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL": ("factor-v3-formal-supervisor-worker/v1"),
        }
    )
    if include_stdlib_prelock:
        environment["FACTOR_V3_FORMAL_STDLIB_PRELOCKED_ROOT_SHA256"] = str(
            config["_test_stdlib_inventory_root_sha256"]
        )
    if selected_action in {"run", "resume"}:
        environment["JIAOCH_TOKEN"] = "test-only-never-log"
    return subprocess.run(
        [
            str(config["python_executable_path"]),
            "-I",
            "-B",
            "-S",
            str(bootstrap_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
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
    completed = _run_as_synthetic_supervisor(
        rendered,
        config,
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert (
        json.loads(completed.stdout)["result"]["formal_input_root"]
        == (payload["formal_input_root_sha256"])
    )


def test_actual_be3_control_line_runs_through_b2_authorized_bootstrap(
    tmp_path: Path,
) -> None:
    (
        config,
        payload,
        authorization_path,
        trusted_public_der,
    ) = _be3_crossline_authorized_fixture(tmp_path)

    rendered = _render_authorized(authorization_path, trusted_public_der)
    completed = _run_as_synthetic_supervisor(
        rendered,
        config,
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["result"] == {
        "run_root": payload["run_root"],
        "run_spec_path": payload["run_spec_path"],
        "source": "be3-crossline-lightweight-runner",
        "status": "verified",
    }


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


def test_authorization_rejects_an_untrusted_signing_key(tmp_path: Path) -> None:
    (
        _config,
        _payload,
        authorization_path,
        _trusted_public_der_value,
    ) = _authorized_fixture(tmp_path)
    wrong_key_root = tmp_path / "wrong-authorization-key"
    wrong_key_root.mkdir()
    _private_key, wrong_public_der = _test_rsa_key(wrong_key_root)

    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="authorization (signature|key role)",
    ):
        _render_authorized(authorization_path, wrong_public_der)


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
    completion_payload = renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    completion_authorization_path = _write_completion_authorization(
        tmp_path,
        completion_payload,
    )

    first = renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    second = renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )

    assert first == second
    assert first["schema"] == ("factor-v3-formal-bootstrap-publication-completion/v1")
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
        renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
            authorization_path=authorization_path,
            completion_authorization_path=completion_authorization_path,
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


def test_runtime_rejects_additional_python_switch_before_repository_import(
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
            "-u",
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
