from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer


_GIT = Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
_OPENSSL = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            str(_GIT),
            "-c",
            "user.name=Factor V3 Bootstrap Test",
            "-c",
            "user.email=factor-v3-bootstrap@example.invalid",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
    )
    return completed.stdout.strip()


def _test_rsa_key(tmp_path: Path) -> tuple[Path, bytes]:
    private_key = tmp_path / "test-only-review-private.pem"
    subprocess.run(
        [
            str(_OPENSSL),
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:3072",
            "-out",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
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


def _sign(tmp_path: Path, private_key: Path, payload: bytes) -> bytes:
    payload_path = tmp_path / f"{_sha256(payload)}.payload"
    signature_path = tmp_path / f"{_sha256(payload)}.signature"
    payload_path.write_bytes(payload)
    subprocess.run(
        [
            str(_OPENSSL),
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature_path),
            str(payload_path),
        ],
        check=True,
        capture_output=True,
    )
    return signature_path.read_bytes()


def _source_entry(repo: Path, relative_path: str) -> dict[str, object]:
    raw = (repo / Path(*relative_path.split("/"))).read_bytes()
    return {
        "bytes": len(raw),
        "path": relative_path,
        "sha256": _sha256(raw),
    }


def _cas_write(root: Path, raw: bytes) -> tuple[Path, str]:
    digest = _sha256(raw)
    directory = root / "sha256" / digest[:2]
    directory.mkdir(parents=True)
    path = directory / f"{digest}.json"
    path.write_bytes(raw)
    return path.resolve(), digest


def _fixture_config(
    tmp_path: Path,
    *,
    dispatch_body: str | None = None,
    import_marker: Path | None = None,
) -> dict[str, object]:
    repo = (tmp_path / "reviewed-repo").resolve()
    (repo / "app").mkdir(parents=True)
    (repo / "scripts").mkdir()
    reviewed_app_relative_paths = (
        "app/factor_v3_daily_basic_runner.py",
        *(f"app/reviewed_unloaded_{index:02}.py" for index in range(33)),
    )
    reviewed_app_module_names = tuple(
        relative_path[:-3].replace("/", ".") for relative_path in reviewed_app_relative_paths
    )
    marker_statement = (
        ""
        if import_marker is None
        else (
            "from pathlib import Path\n"
            f"Path({str(import_marker)!r}).write_text('imported', encoding='utf-8')\n"
        )
    )
    builder_body = dispatch_body or (
        "    context.validate_action_config(frozen_action_config)\n"
        "    entry = context.verified_ledger_entry(__name__)\n"
        "    context.assert_verified_module(\n"
        "        __name__, entry['relative_path'], entry['source_sha256']\n"
        "    )\n"
        f"    unloaded_modules = {reviewed_app_module_names!r}\n"
        "    unloaded_entries = [\n"
        "        context.verified_ledger_entry(name)\n"
        "        for name in unloaded_modules\n"
        "    ]\n"
        "    unloaded_entry = unloaded_entries[0]\n"
        "    unloaded_ledger = (\n"
        "        len(unloaded_entries) == 34\n"
        "        and all(item['byte_count'] > 0 for item in unloaded_entries)\n"
        "        and unloaded_entry['relative_path']\n"
        "        == 'app/factor_v3_daily_basic_runner.py'\n"
        "        and unloaded_entry['loader_identity']\n"
        "        == 'factor-v3-verified-source-loader/v1'\n"
        "    )\n"
        "    try:\n"
        "        context.assert_verified_module(\n"
        "            unloaded_modules[0],\n"
        "            unloaded_entry['relative_path'],\n"
        "            unloaded_entry['source_sha256'],\n"
        "        )\n"
        "    except BaseException:\n"
        "        unloaded_assert_rejected = True\n"
        "    else:\n"
        "        unloaded_assert_rejected = False\n"
        "    real_file = globals()['__file__']\n"
        "    globals()['__file__'] = real_file + '.fake'\n"
        "    try:\n"
        "        context.assert_verified_module(\n"
        "            __name__, entry['relative_path'], entry['source_sha256']\n"
        "        )\n"
        "    except BaseException:\n"
        "        fake_file_rejected = True\n"
        "    else:\n"
        "        fake_file_rejected = False\n"
        "    finally:\n"
        "        globals()['__file__'] = real_file\n"
        "    real_loader = globals()['__loader__']\n"
        "    globals()['__loader__'] = object()\n"
        "    try:\n"
        "        context.assert_verified_module(\n"
        "            __name__, entry['relative_path'], entry['source_sha256']\n"
        "        )\n"
        "    except BaseException:\n"
        "        nonverified_loader_rejected = True\n"
        "    else:\n"
        "        nonverified_loader_rejected = False\n"
        "    finally:\n"
        "        globals()['__loader__'] = real_loader\n"
        "    try:\n"
        "        __import__('app.not_in_signed_manifest')\n"
        "    except ImportError:\n"
        "        rejected_unreviewed = True\n"
        "    else:\n"
        "        rejected_unreviewed = False\n"
        "    try:\n"
        "        Path(__file__).write_text('replacement', encoding='utf-8')\n"
        "    except PermissionError:\n"
        "        replacement_blocked = True\n"
        "    else:\n"
        "        replacement_blocked = False\n"
        "    context.emit_json({\n"
        "        'action': frozen_action_config['action'],\n"
        "        'formal_input_root': frozen_action_config['formal_input_root'],\n"
        "        'loader_identity': entry['loader_identity'],\n"
        "        'fake_file_rejected': fake_file_rejected,\n"
        "        'nonverified_loader_rejected': nonverified_loader_rejected,\n"
        "        'rejected_unreviewed': rejected_unreviewed,\n"
        "        'replacement_blocked': replacement_blocked,\n"
        "        'source_registry_entries': 3 + len(unloaded_entries),\n"
        "        'status': 'fixture-verified',\n"
        "        'unloaded_assert_rejected': unloaded_assert_rejected,\n"
        "        'unloaded_ledger': unloaded_ledger,\n"
        "    })\n"
        "    return 0\n"
    )
    builder = (
        "from pathlib import Path\n"
        f"{marker_statement}"
        "\n"
        "def trusted_dispatch(context, frozen_action_config):\n"
        f"{builder_body}"
    ).encode("utf-8")
    builder_path = repo / "scripts" / "build_factor_v3_daily_basic_formal_run_spec.py"
    builder_path.write_bytes(builder)
    shim = (
        "import sys\n"
        "\n"
        "def trusted_dispatch(context, frozen_action_config):\n"
        "    context.validate_action_config(frozen_action_config)\n"
        "    builder = sys.modules.get(\n"
        "        'scripts.build_factor_v3_daily_basic_formal_run_spec'\n"
        "    )\n"
        "    if builder is None:\n"
        "        raise RuntimeError('verified builder unavailable')\n"
        "    entry = context.verified_ledger_entry(builder.__name__)\n"
        "    context.assert_verified_module(\n"
        "        builder.__name__, entry['relative_path'], entry['source_sha256']\n"
        "    )\n"
        "    return builder.trusted_dispatch(context, frozen_action_config)\n"
    ).encode("utf-8")
    shim_path = repo / "scripts" / "run_factor_v3_daily_basic_formal.py"
    shim_path.write_bytes(shim)
    (repo / "app" / "__init__.py").write_text(
        '"""Signed fixture package."""\n',
        encoding="utf-8",
    )
    for relative_path in reviewed_app_relative_paths:
        (repo / Path(*relative_path.split("/"))).write_text(
            "raise RuntimeError('reviewed unloaded fixture must not execute')\n",
            encoding="utf-8",
        )
    _git(repo.parent, "init", str(repo))
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "branch", "--show-current")

    source_manifest = [
        _source_entry(repo, relative_path)
        for relative_path in (
            "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
            "scripts/run_factor_v3_daily_basic_formal.py",
            "app/__init__.py",
            *reviewed_app_relative_paths,
        )
    ]
    source_root_sha256 = _sha256(_canonical_bytes(source_manifest))
    private_key, public_der = _test_rsa_key(tmp_path)
    public_der_sha256 = _sha256(public_der)
    formal_input_root_sha256 = _sha256(b"fixture-formal-input-root")
    review_protocol_sha256 = _sha256(b"fixture-review-protocol")
    payload = {
        "branch": branch,
        "decision": "APPROVED_NO_P0_P1_P2",
        "feature_attestation_sha256": _sha256(b"fixture-feature-attestation"),
        "formal_input_root_sha256": formal_input_root_sha256,
        "formal_runner_sha256": _file_sha256(repo / "app" / "factor_v3_daily_basic_runner.py"),
        "issued_at_utc": "2026-07-30T00:00:00+00:00",
        "project_id": "quant-signal-lkj",
        "review_nonce_sha256": _sha256(b"fixture-review-nonce"),
        "review_protocol_sha256": review_protocol_sha256,
        "reviewed_commit": commit,
        "reviewed_source_manifest": source_manifest,
        "reviewed_source_root_sha256": source_root_sha256,
        "reviewer_key_id": f"sha256:{public_der_sha256}",
        "schema": "factor-v3-daily-basic-formal-review-signed-payload/v1",
        "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
    }
    payload_raw = _canonical_bytes(payload)
    receipt_raw = _canonical_bytes(
        {
            "payload": payload,
            "signature_base64": base64.b64encode(_sign(tmp_path, private_key, payload_raw)).decode(
                "ascii"
            ),
        }
    )
    receipt_path, receipt_sha256 = _cas_write(
        (tmp_path / "receipts").resolve(),
        receipt_raw,
    )
    python = Path(sys.executable).resolve()
    base_python = Path(sys._base_executable).resolve()
    git = _GIT.resolve()
    builder_sha256 = _file_sha256(builder_path)
    shim_sha256 = _file_sha256(shim_path)
    review_payload_sha256 = _sha256(payload_raw)
    claim = {
        "base_python_executable_path": str(base_python),
        "base_python_executable_sha256": _file_sha256(base_python),
        "branch": branch,
        "builder_sha256": builder_sha256,
        "formal_input_root_sha256": formal_input_root_sha256,
        "git_executable_path": str(git),
        "git_executable_sha256": _file_sha256(git),
        "project_id": "quant-signal-lkj",
        "python_executable_path": str(python),
        "python_executable_sha256": _file_sha256(python),
        "review_payload_sha256": review_payload_sha256,
        "review_public_key_spki_sha256": public_der_sha256,
        "review_receipt_sha256": receipt_sha256,
        "reviewed_commit": commit,
        "reviewed_source_root_sha256": source_root_sha256,
        "schema": "factor-v3-daily-basic-formal-bootstrap-claim/v1",
        "shim_sha256": shim_sha256,
    }
    claim_raw = _canonical_bytes(claim)
    claim_path, claim_sha256 = _cas_write(
        (tmp_path / "claims").resolve(),
        claim_raw,
    )
    formal_input = (tmp_path / "formal-input").resolve()
    formal_output = (tmp_path / "formal-output").resolve()
    run_root = (tmp_path / "run-root").resolve()
    for directory in (formal_input, formal_output, run_root):
        directory.mkdir()
    return {
        "action": "verify",
        "base_python_executable_path": str(base_python),
        "base_python_executable_sha256": _file_sha256(base_python),
        "bootstrap_claim_path": str(claim_path),
        "bootstrap_claim_sha256": claim_sha256,
        "builder_relative_path": ("scripts/build_factor_v3_daily_basic_formal_run_spec.py"),
        "builder_sha256": builder_sha256,
        "expected_branch": branch,
        "expected_commit": commit,
        "formal_input_root": str(formal_input),
        "formal_input_root_sha256": formal_input_root_sha256,
        "formal_output_root": str(formal_output),
        "git_executable_path": str(git),
        "git_executable_sha256": _file_sha256(git),
        "project_id": "quant-signal-lkj",
        "python_executable_path": str(python),
        "python_executable_sha256": _file_sha256(python),
        "repo_root": str(repo),
        "review_payload_sha256": review_payload_sha256,
        "review_protocol_sha256": review_protocol_sha256,
        "review_public_key_spki_der_base64": base64.b64encode(public_der).decode("ascii"),
        "review_public_key_spki_sha256": public_der_sha256,
        "review_receipt_path": str(receipt_path),
        "review_receipt_sha256": receipt_sha256,
        "run_root": str(run_root),
        "run_spec_path": str((tmp_path / "run-spec.json").resolve()),
        "schema": "factor-v3-formal-bootstrap-render-config/v1",
        "shim_relative_path": "scripts/run_factor_v3_daily_basic_formal.py",
        "shim_sha256": shim_sha256,
        "source_manifest": source_manifest,
        "source_root_sha256": source_root_sha256,
    }


def _run_rendered(
    source: bytes,
    config: dict[str, object],
    *caller_args: str,
) -> subprocess.CompletedProcess[str]:
    bootstrap_path = Path(str(config["run_root"])) / "direct-rendered-bootstrap.py"
    bootstrap_path.write_bytes(source)
    return subprocess.run(
        [
            str(config["python_executable_path"]),
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={config['_test_stdlib_pycache_prefix']}",
            str(bootstrap_path),
            *caller_args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
        timeout=60,
    )


def test_renderer_is_deterministic_self_contained_and_has_no_placeholder(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _authorized_fixture,
        _render_authorized,
    )

    _config, _payload, authorization_path, trusted_public_der = _authorized_fixture(tmp_path)
    first = _render_authorized(authorization_path, trusted_public_der)
    second = _render_authorized(authorization_path, trusted_public_der)

    assert first == second
    assert first == renderer.validate_rendered_factor_v3_formal_bootstrap(first)
    assert not first.endswith(b"\n")
    assert renderer._CONFIG_MARKER not in first
    assert renderer._CONTROL_CONTRACT_MARKER not in first
    assert renderer._EARLY_STDLIB_ENTRIES_MARKER not in first
    early_prefix = first.split(b"# EARLY_EXACT_IMPORT_BOUNDARY_COMPLETE", 1)[0]
    assert b"import _frozen_importlib as _early_importlib" in early_prefix
    assert b"\nimport base64\n" not in early_prefix
    assert b"\nimport os\n" not in early_prefix
    assert b"\nfrom pathlib import Path\n" not in early_prefix
    assert len(first) <= 8 * 1024 * 1024
    compile(first, "<factor-v3-formal-bootstrap>", "exec")


def test_rendered_bootstrap_executes_only_verified_held_source_bytes(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _authorized_fixture,
        _render_authorized,
        _run_as_synthetic_supervisor,
    )

    config, _payload, authorization_path, trusted_public_der = _authorized_fixture(tmp_path)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_as_synthetic_supervisor(
        rendered,
        config,
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["result"] == {
        "action": "verify",
        "fake_file_rejected": True,
        "formal_input_root": str(config["formal_input_root_sha256"]),
        "loader_identity": "factor-v3-verified-source-loader/v1",
        "nonverified_loader_rejected": True,
        "rejected_unreviewed": True,
        "replacement_blocked": True,
        "source_registry_entries": 37,
        "status": "fixture-verified",
        "unloaded_assert_rejected": True,
        "unloaded_ledger": True,
    }


def test_rendered_bootstrap_rejects_caller_arguments_before_repo_import(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _authorized_fixture,
        _render_authorized,
    )

    marker = tmp_path / "repository-imported.marker"
    config, _payload, authorization_path, trusted_public_der = _authorized_fixture(
        tmp_path,
        import_marker=marker,
    )
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_rendered(rendered, config, "--action=run")

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "bootstrap rejected" in completed.stderr.lower()
    assert not marker.exists()


def test_rendered_bootstrap_never_flushes_buffered_success_after_failure(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _authorized_fixture,
        _render_authorized,
    )

    config, _payload, authorization_path, trusted_public_der = _authorized_fixture(
        tmp_path,
        dispatch_body=(
            "    context.validate_action_config(frozen_action_config)\n"
            "    context.emit_json({'status': 'must-not-escape'})\n"
            "    raise RuntimeError('fixture failure after buffered success')\n"
        ),
    )
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "must-not-escape" not in completed.stderr
    assert "bootstrap rejected" in completed.stderr.lower()


@pytest.mark.parametrize(
    "forbidden",
    (
        "token",
        "publication_capability",
        "private_key",
        "private-key",
        "signing_private_key",
        "RSA Private Key PEM",
    ),
)
def test_renderer_rejects_credentials_and_private_key_shapes(
    tmp_path: Path,
    forbidden: str,
) -> None:
    config = _fixture_config(tmp_path)
    config[forbidden] = "forbidden"

    with pytest.raises(renderer.FormalBootstrapRenderError, match="credential"):
        renderer._validated_config(config)


def test_renderer_rejects_manifest_outside_app_and_scripts(
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path)
    manifest = list(config["source_manifest"])
    manifest.append(
        {
            "bytes": 1,
            "path": "unreviewed.py",
            "sha256": _sha256(b"x"),
        }
    )
    config["source_manifest"] = manifest
    config["source_root_sha256"] = _sha256(_canonical_bytes(manifest))

    with pytest.raises(renderer.FormalBootstrapRenderError, match="manifest"):
        renderer._validated_config(config)


def test_renderer_requires_signed_app_package_initializer(
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path)
    manifest = [item for item in config["source_manifest"] if item["path"] != "app/__init__.py"]
    config["source_manifest"] = manifest
    config["source_root_sha256"] = _sha256(_canonical_bytes(manifest))

    with pytest.raises(renderer.FormalBootstrapRenderError, match="app/__init__"):
        renderer._validated_config(config)


def test_renderer_rejects_mismatched_executable_identity(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    config["python_executable_sha256"] = "0" * 64

    with pytest.raises(renderer.FormalBootstrapRenderError, match="identity"):
        renderer._validated_config(config)


def test_renderer_rejects_noncanonical_public_key_encoding(
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path)
    config["review_public_key_spki_der_base64"] = "!"

    with pytest.raises(renderer.FormalBootstrapRenderError, match="public key"):
        renderer._validated_config(config)


def test_renderer_rejects_mismatched_public_key_identity(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    config["review_public_key_spki_sha256"] = "0" * 64

    with pytest.raises(renderer.FormalBootstrapRenderError, match="public key"):
        renderer._validated_config(config)


def test_renderer_rejects_traversing_trusted_entrypoint(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path)
    config["builder_relative_path"] = "scripts/../unreviewed.py"

    with pytest.raises(renderer.FormalBootstrapRenderError, match="builder"):
        renderer._validated_config(config)


def test_rendered_validator_rejects_corrupt_embedded_payload(
    tmp_path: Path,
) -> None:
    from tests.test_factor_v3_formal_bootstrap_authorization import (
        _authorized_fixture,
        _render_authorized,
    )

    _config, _payload, authorization_path, trusted_public_der = _authorized_fixture(tmp_path)
    rendered = _render_authorized(authorization_path, trusted_public_der)
    marker = b"_EMBEDDED_CONFIG_JSON: bytes = b'"
    assert rendered.count(marker) == 1
    corrupted = rendered.replace(marker, marker + b"!", 1)

    with pytest.raises(renderer.FormalBootstrapRenderError, match="rejected"):
        renderer.validate_rendered_factor_v3_formal_bootstrap(corrupted)

    early_module = b"'module': 'base64'"
    assert rendered.count(early_module) >= 1
    drifted_early_inventory = rendered.replace(
        early_module,
        b"'module': 'base6X'",
        1,
    )
    with pytest.raises(renderer.FormalBootstrapRenderError, match="drifted"):
        renderer.validate_rendered_factor_v3_formal_bootstrap(drifted_early_inventory)
