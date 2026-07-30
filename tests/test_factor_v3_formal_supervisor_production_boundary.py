from __future__ import annotations

from importlib import import_module

from app import factor_v3_formal_trusted_supervisor as supervisor


def test_production_supervisor_control_plane_exports_the_complete_chain() -> None:
    control = import_module("app.factor_v3_formal_supervisor_control")

    for name in (
        "publish_factor_v3_formal_supervisor",
        "validate_factor_v3_formal_supervisor_publication",
        "build_factor_v3_formal_supervisor_launch_authorization",
        "launch_factor_v3_formal_supervisor",
    ):
        assert callable(getattr(control, name))


def test_launch_authorization_v2_binds_the_executed_supervisor_artifact() -> None:
    assert supervisor.LAUNCH_AUTHORIZATION_SCHEMA == (
        "factor-v3-formal-supervisor-launch-authorization/v2"
    )
    assert {
        "executed_supervisor_path",
        "executed_supervisor_sha256",
        "supervisor_loader_sha256",
        "supervisor_publication_receipt_path",
        "supervisor_publication_receipt_sha256",
    }.issubset(supervisor._LAUNCH_FIELDS)


def test_external_loader_establishes_the_boundary_before_supervisor_imports() -> None:
    control = import_module("app.factor_v3_formal_supervisor_control")

    assert control.SUPERVISOR_LAUNCH_PYTHON_FLAGS == ("-I", "-B", "-S", "-P")
    source = control.SUPERVISOR_EXTERNAL_LOADER_TEMPLATE
    assert type(source) is str
    assert source.startswith("from __future__ import annotations\n")
    boundary = source.index("# EARLY_EXACT_IMPORT_BOUNDARY_COMPLETE")
    artifact_check = source.index("# EXECUTED_SUPERVISOR_IDENTITY_VERIFIED")
    artifact_exec = source.index("# EXECUTED_SUPERVISOR_BYTES_EXECUTED")
    assert boundary < artifact_check < artifact_exec
    assert "import pathlib" not in source[:boundary]
    assert "import json" not in source[:boundary]
    assert "import base64" not in source[:boundary]


def test_external_loader_exposes_a_pipe_only_native_credential_provider() -> None:
    control = import_module("app.factor_v3_formal_supervisor_control")

    source = control.SUPERVISOR_EXTERNAL_LOADER_TEMPLATE
    signature_check = source.index(
        '_verify_signature(_canonical_bytes(_payload), _authorization["signature_base64"])'
    )
    provider = source.index("_TRUSTED_NATIVE_BROKER_CREDENTIAL_PROVIDER")
    supervisor_exec = source.index("# EXECUTED_SUPERVISOR_BYTES_EXECUTED")

    assert '"--native-broker-v1"' in source
    assert provider > signature_check
    assert provider < supervisor_exec
    assert "sys.stdin.buffer.readline(" in source
    assert "sys.stdout.buffer.write(" in source
    assert '"HANDLE="' in source
    assert "NATIVE_BROKER_CREDENTIAL_HANDLE" not in source
