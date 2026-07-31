from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

import pytest

from app import factor_v3_formal_trusted_supervisor as formal_supervisor
from app import factor_v3_formal_bootstrap_renderer as formal_bootstrap_renderer
from app import factor_v3_formal_supervisor_control as formal_control
from tests.test_factor_v3_formal_bootstrap_authorization import (
    _authorized_fixture,
    _execution_test_key,
    _write_completion_authorization,
)
from tests.test_factor_v3_formal_trusted_supervisor import (
    _canonical_bytes,
    _file_sha256,
    _fixture,
    _rewrite_authorization,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BROKER_ROOT = REPO_ROOT / "native" / "factor_v3_formal_native_broker"
BROKER_SOURCE = BROKER_ROOT / "factor_v3_formal_native_broker.c"
BROKER_MANIFEST = BROKER_ROOT / "factor_v3_formal_native_broker_manifest.h"
SERVICE_INSTALLER = (
    REPO_ROOT / "scripts" / "install_factor_v3_formal_native_broker_service.ps1"
)
HANDOFF_CHILD_SOURCE = (
    REPO_ROOT / "tests" / "native" / "factor_v3_credential_handoff_child.c"
)


def _compile_test_broker(tmp_path: Path) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("Win32 GCC is unavailable")
    output = tmp_path / "factor_v3_formal_native_broker-service-tcb-test.exe"
    completed = subprocess.run(
        [
            gcc,
            "-std=c11",
            "-DUNICODE",
            "-D_UNICODE",
            "-municode",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-static",
            "-DF3_BROKER_TESTING=1",
            "-DF3_BROKER_DISPOSABLE_TEST_MANIFEST=1",
            f"-I{BROKER_ROOT}",
            str(BROKER_SOURCE),
            "-o",
            str(output),
            "-ladvapi32",
            "-lbcrypt",
            "-lncrypt",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return output


def _run(binary: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _c_wide(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _compile_source(
    *,
    source: Path,
    output: Path,
    includes: list[Path],
    definitions: list[str],
    libraries: list[str],
) -> None:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("Win32 GCC is unavailable")
    completed = subprocess.run(
        [
            gcc,
            "-std=c11",
            "-DUNICODE",
            "-D_UNICODE",
            "-municode",
            "-Wall",
            "-Wextra",
            "-Werror",
            *definitions,
            *(f"-I{path}" for path in includes),
            str(source),
            "-o",
            str(output),
            "-ladvapi32",
            "-lbcrypt",
            *libraries,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


def _replace_acl(
    path: Path,
    *,
    builtin_users_rights: str | None,
    grant_current_user: bool = True,
    interactive_rights: str | None = None,
    service_writer_sid: str | None = None,
    owner_rights: str | None = None,
    exact_reader_sid: str | None = None,
    exact_reader_rights: str = "RX",
) -> None:
    account = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    inherit = "(OI)(CI)" if path.is_dir() else ""
    grants = [
        f"*S-1-5-18:{inherit}(F)",
        f"*S-1-5-32-544:{inherit}(F)",
    ]
    if grant_current_user:
        grants.append(f"{account}:{inherit}(F)")
    if builtin_users_rights is not None:
        grants.append(
            f"*S-1-5-32-545:{inherit}({builtin_users_rights})"
        )
    if interactive_rights is not None:
        grants.append(f"*S-1-5-4:{inherit}({interactive_rights})")
    if service_writer_sid is not None:
        grants.append(f"*{service_writer_sid}:{inherit}(F)")
    if owner_rights is not None:
        grants.append(f"*S-1-3-4:{inherit}({owner_rights})")
    if exact_reader_sid is not None:
        grants.append(
            f"*{exact_reader_sid}:{inherit}({exact_reader_rights})"
        )
    completed = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            *grants,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def _current_logon_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/logonid"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    sid = completed.stdout.strip()
    assert sid.startswith("S-1-5-5-")
    return sid


def _current_user_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/user"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    values = [
        value
        for value in completed.stdout.split()
        if value.startswith("S-1-")
    ]
    assert len(values) == 1
    return values[0]


def _current_cloud_identity_sid() -> str:
    completed = subprocess.run(
        ["whoami", "/groups"],
        check=False,
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    values = [
        value.decode("ascii")
        for value in completed.stdout.split()
        if value.startswith(b"S-1-11-")
    ]
    assert len(values) == 1
    return values[0]


def _copy_disposable_python_runtime(
    root: Path,
    *,
    worker_sid: str,
) -> Path:
    source = Path(sys._base_executable).resolve().parent
    target = root / "restricted-python"
    target.mkdir()
    _replace_acl(
        target,
        builtin_users_rights=None,
        exact_reader_sid=worker_sid,
    )
    for name in (
        "python.exe",
        "python3.dll",
        "python311.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ):
        shutil.copy2(source / name, target / name)
    shutil.copytree(
        source / "DLLs",
        target / "DLLs",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    shutil.copytree(
        source / "Lib",
        target / "Lib",
        ignore=shutil.ignore_patterns(
            "site-packages",
            "__pycache__",
            "*.pyc",
            "*.pyo",
        ),
    )
    (target / "python311._pth").write_text(
        ".\nLib\nDLLs\n",
        encoding="ascii",
        newline="\n",
    )
    return (target / "python.exe").resolve()


def _grant_cleanup_access(root: Path) -> None:
    account = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
    subprocess.run(
        [
            "icacls",
            str(root),
            "/grant",
            f"{account}:(OI)(CI)(F)",
            "/T",
            "/C",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _build_handoff_fixture(root: Path) -> tuple[Path, Path, Path, Path, bytes, str]:
    from app import factor_v3_formal_native_broker as broker

    public = root / "public"
    runtime_root = root / "runtime-readonly"
    source_root = root / "source-readonly"
    provisional_root = root / "worker-provisional"
    completed_root = root / "broker-completed"
    secret_root = root / "broker-secret"
    for path in (
        public,
        runtime_root,
        source_root,
        provisional_root,
        completed_root,
        secret_root,
    ):
        path.mkdir(parents=True)
    _replace_acl(provisional_root, builtin_users_rights="M")
    _replace_acl(
        completed_root,
        builtin_users_rights=None,
        grant_current_user=False,
        interactive_rights="F",
    )
    _replace_acl(
        secret_root,
        builtin_users_rights=None,
        grant_current_user=False,
        interactive_rights="F",
    )

    secret_value = f"disposable-credential-{uuid.uuid4()}".encode("ascii")
    credential = secret_root / "points-primary.slot"
    credential.write_bytes(secret_value)
    _replace_acl(
        credential,
        builtin_users_rights=None,
        grant_current_user=False,
        interactive_rights="F",
    )

    paths = {
        "authorization_path": public / "bootstrap-authorization.json",
        "completion_marker_path": public / "bootstrap-completion.json",
        "publication_receipt_path": public / "supervisor-publication.json",
        "execution_ledger_root": completed_root,
    }
    for name, path in paths.items():
        if name != "execution_ledger_root":
            path.write_bytes(f"{name}\n".encode("ascii"))
    candidate = broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        **paths,
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=public,
        candidate=candidate,
    )
    candidate_path = Path(publication["candidate_path"])
    candidate_sha256 = publication["candidate_sha256"]

    original_schema = "factor-v3-formal-supervisor-launch-authorization/v2"
    original_action = "run"
    original_envelope_sha256 = "1" * 64
    original_signature_sha256 = "2" * 64
    original_cas_sha256 = "3" * 64
    claim_fields = (
        f"original_schema={original_schema}\n"
        f"original_action={original_action}\n"
        f"original_envelope_sha256={original_envelope_sha256}\n"
        f"original_signature_sha256={original_signature_sha256}\n"
        f"original_cas_sha256={original_cas_sha256}\n"
        f"candidate_sha256={candidate_sha256}\n"
    ).encode("ascii")
    claim_sha256 = __import__("hashlib").sha256(claim_fields).hexdigest()

    provisional = provisional_root / "worker.provisional"
    completed = completed_root / "broker.completed"
    child_manifest = root / "handoff_child_manifest.h"
    child_manifest.write_text(
        "\n".join(
            (
                '#define F3_HANDOFF_SECRET_PATH L"' + _c_wide(credential) + '"',
                '#define F3_HANDOFF_COMPLETED_PATH L"' + _c_wide(completed) + '"',
                '#define F3_HANDOFF_RUNTIME_ROOT L"' + _c_wide(runtime_root) + '"',
                '#define F3_HANDOFF_DISABLED_SID L"S-1-5-4"',
                (
                    '#define F3_HANDOFF_EXPECTED_SECRET_SHA256 L"'
                    + __import__("hashlib").sha256(secret_value).hexdigest()
                    + '"'
                ),
                f'#define F3_HANDOFF_ORIGINAL_SCHEMA "{original_schema}"',
                f'#define F3_HANDOFF_ORIGINAL_ACTION "{original_action}"',
                (
                    '#define F3_HANDOFF_ORIGINAL_ENVELOPE_SHA256 "'
                    + original_envelope_sha256
                    + '"'
                ),
                (
                    '#define F3_HANDOFF_ORIGINAL_SIGNATURE_SHA256 "'
                    + original_signature_sha256
                    + '"'
                ),
                (
                    '#define F3_HANDOFF_ORIGINAL_CAS_SHA256 "'
                    + original_cas_sha256
                    + '"'
                ),
                f'#define F3_HANDOFF_CANDIDATE_SHA256 "{candidate_sha256}"',
                f'#define F3_HANDOFF_CLAIM_SHA256 "{claim_sha256}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    child = runtime_root / "factor_v3_credential_handoff_child.exe"
    _compile_source(
        source=HANDOFF_CHILD_SOURCE,
        output=child,
        includes=[root],
        definitions=[
            '-DF3_HANDOFF_MANIFEST_HEADER="handoff_child_manifest.h"',
        ],
        libraries=[],
    )
    _replace_acl(
        runtime_root,
        builtin_users_rights="RX",
        grant_current_user=False,
    )

    reviewed_source = source_root / "reviewed-source.txt"
    reviewed_source.write_bytes(b"reviewed disposable native handoff source\n")
    _replace_acl(
        source_root,
        builtin_users_rights="RX",
        grant_current_user=False,
    )
    broker_manifest = root / "handoff_broker_manifest.h"
    broker_manifest.write_text(
        "\n".join(
            (
                '#define F3_BROKER_RUNTIME_PATH L"' + _c_wide(child) + '"',
                (
                    '#define F3_BROKER_RUNTIME_SHA256 L"'
                    + __import__("hashlib").sha256(child.read_bytes()).hexdigest()
                    + '"'
                ),
                '#define F3_BROKER_SOURCE_PATH L"' + _c_wide(reviewed_source) + '"',
                (
                    '#define F3_BROKER_SOURCE_SHA256 L"'
                    + __import__("hashlib").sha256(
                        reviewed_source.read_bytes()
                    ).hexdigest()
                    + '"'
                ),
                '#define F3_BROKER_CREDENTIAL_SLOT_PATH L"'
                + _c_wide(credential)
                + '"',
                (
                    '#define F3_BROKER_CNG_PROVIDER '
                    'L"Microsoft Software Key Storage Provider"'
                ),
                '#define F3_BROKER_CNG_KEY_NAME L"unused-disposable-test-key"',
                "#define F3_BROKER_CNG_ALGORITHM NCRYPT_RSA_ALGORITHM",
                '#define F3_BROKER_SERVICE_NAME L"DisposableFixtureService"',
                '#define F3_BROKER_SERVICE_SID L"S-1-5-18"',
                '#define F3_BROKER_RESTRICTING_SID L"S-1-5-4"',
                f'#define F3_BROKER_HANDOFF_ORIGINAL_SCHEMA "{original_schema}"',
                f'#define F3_BROKER_HANDOFF_ORIGINAL_ACTION "{original_action}"',
                (
                    '#define F3_BROKER_HANDOFF_ORIGINAL_ENVELOPE_SHA256 "'
                    + original_envelope_sha256
                    + '"'
                ),
                (
                    '#define F3_BROKER_HANDOFF_ORIGINAL_SIGNATURE_SHA256 "'
                    + original_signature_sha256
                    + '"'
                ),
                (
                    '#define F3_BROKER_HANDOFF_ORIGINAL_CAS_SHA256 "'
                    + original_cas_sha256
                    + '"'
                ),
                "#define F3_BROKER_TESTING 1",
                "#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1",
                "",
            )
        ),
        encoding="utf-8",
    )
    native = public / "factor_v3_formal_native_broker-handoff-test.exe"
    _compile_source(
        source=BROKER_SOURCE,
        output=native,
        includes=[root, BROKER_ROOT],
        definitions=[
            '-DF3_BROKER_MANIFEST_HEADER="handoff_broker_manifest.h"',
        ],
        libraries=["-lncrypt"],
    )
    return native, candidate_path, provisional, completed, secret_value, claim_sha256


def _build_production_validation_fixture(
    root: Path,
) -> tuple[
    Path,
    Path,
    Path,
    dict[str, object],
    formal_supervisor._SupervisorPins,
]:
    from app import factor_v3_formal_native_broker as broker

    pins, payload, launch_authorization, _environment, _writes = _fixture(root)
    candidate = broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        authorization_path=payload["bootstrap_execution_authorization_path"],
        completion_marker_path=payload["publication_completion_marker_path"],
        publication_receipt_path=payload["supervisor_publication_receipt_path"],
        launch_authorization_path=launch_authorization,
        execution_ledger_root=payload["execution_ledger_root"],
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=root / "native-candidates",
        candidate=candidate,
    )
    public_der = base64.b64decode(pins.execution_public_key_spki_der_base64)
    modulus, exponent = formal_supervisor._parse_rsa3072_spki(public_der)
    source = Path(str(payload["supervisor_loader_path"]))
    credential = Path(str(payload["credential_path"]))
    manifest = root / "production_validation_manifest.h"
    manifest.write_text(
        "\n".join(
            (
                '#define F3_BROKER_RUNTIME_PATH L"' + _c_wide(Path(sys.executable)) + '"',
                '#define F3_BROKER_RUNTIME_SHA256 L"'
                + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
                + '"',
                '#define F3_BROKER_SOURCE_PATH L"' + _c_wide(source) + '"',
                '#define F3_BROKER_SOURCE_SHA256 L"'
                + hashlib.sha256(source.read_bytes()).hexdigest()
                + '"',
                '#define F3_BROKER_CREDENTIAL_SLOT_PATH L"'
                + _c_wide(credential)
                + '"',
                (
                    '#define F3_BROKER_CNG_PROVIDER '
                    'L"Microsoft Software Key Storage Provider"'
                ),
                '#define F3_BROKER_CNG_KEY_NAME L"unused-disposable-test-key"',
                "#define F3_BROKER_CNG_ALGORITHM NCRYPT_RSA_ALGORITHM",
                '#define F3_BROKER_SERVICE_NAME L"DisposableFixtureService"',
                '#define F3_BROKER_SERVICE_SID L"S-1-5-18"',
                '#define F3_BROKER_RESTRICTING_SID L"S-1-5-4"',
                '#define F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX "'
                + modulus.to_bytes(384, "big").hex()
                + '"',
                f"#define F3_BROKER_EXECUTION_PUBLIC_EXPONENT {exponent}u",
                "#define F3_BROKER_TESTING 1",
                "#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1",
                "",
            )
        ),
        encoding="utf-8",
    )
    native = root / "factor_v3_formal_native_broker-production-validation-test.exe"
    _compile_source(
        source=BROKER_SOURCE,
        output=native,
        includes=[root, BROKER_ROOT],
        definitions=[
            '-DF3_BROKER_MANIFEST_HEADER="production_validation_manifest.h"',
        ],
        libraries=["-lncrypt"],
    )
    return (
        native,
        Path(publication["candidate_path"]),
        launch_authorization,
        payload,
        pins,
    )


def _build_native_supervisor_e2e_fixture(
    root: Path,
) -> tuple[Path, Path, bytes, str]:
    from app import factor_v3_formal_native_broker as broker

    now = datetime.now(timezone.utc).replace(microsecond=0)
    service_sid = _current_cloud_identity_sid()
    worker_sid = "S-1-5-32-545"
    _replace_acl(
        root,
        builtin_users_rights="RX",
    )
    runtime_python = _copy_disposable_python_runtime(
        root,
        worker_sid=worker_sid,
    )

    def current_validity(payload: dict[str, object]) -> None:
        base_python = runtime_python
        payload["issued_at_utc"] = now.isoformat(timespec="seconds")
        payload["not_before_utc"] = now.isoformat(timespec="seconds")
        payload["expires_at_utc"] = (now + timedelta(minutes=30)).isoformat(
            timespec="seconds"
        )
        payload["python_executable_path"] = str(base_python)
        runtime_sha256 = hashlib.sha256(
            base_python.read_bytes()
        ).hexdigest()
        payload["python_executable_sha256"] = runtime_sha256
        payload["base_python_executable_path"] = str(base_python)
        payload["base_python_executable_sha256"] = runtime_sha256
        stdlib_policy = (
            formal_bootstrap_renderer._trusted_stdlib_policy_for_base_python(
                base_python,
                Path(str(payload["stdlib_policy"]["pycache_prefix"])),
            )
        )
        payload["stdlib_policy"] = stdlib_policy
        payload["stdlib_policy_root_sha256"] = (
            formal_bootstrap_renderer.stdlib_policy_root_sha256(
                stdlib_policy
            )
        )
        claim_path = Path(str(payload["bootstrap_claim_path"]))
        claim = json.loads(claim_path.read_bytes())
        claim["python_executable_path"] = str(base_python)
        claim["python_executable_sha256"] = runtime_sha256
        if "base_python_executable_path" in claim:
            claim["base_python_executable_path"] = str(base_python)
        if "base_python_executable_sha256" in claim:
            claim["base_python_executable_sha256"] = runtime_sha256
        if "stdlib_policy_root_sha256" in claim:
            claim["stdlib_policy_root_sha256"] = payload[
                "stdlib_policy_root_sha256"
            ]
        claim_raw = _canonical_bytes(claim)
        claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
        updated_claim_path = (
            claim_path.parent.parent
            / claim_sha256[:2]
            / f"{claim_sha256}.json"
        )
        updated_claim_path.parent.mkdir(parents=True)
        updated_claim_path.write_bytes(claim_raw)
        payload["bootstrap_claim_path"] = str(updated_claim_path.resolve())
        payload["bootstrap_claim_sha256"] = claim_sha256

    config, _payload, authorization_path, trusted_public_der = (
        _authorized_fixture(
            root,
            action="run",
            mutate_payload=current_validity,
        )
    )
    source_app = REPO_ROOT / "app"
    reviewed_app = Path(str(config["repo_root"])) / "app"
    for name in (
        "factor_v3_formal_control_contract.py",
        "factor_v3_formal_supervisor_loader_runtime.py",
        "factor_v3_formal_trusted_supervisor.py",
    ):
        shutil.copyfile(source_app / name, reviewed_app / name)
    exclude_path = Path(str(config["repo_root"])) / ".git" / "info" / "exclude"
    with exclude_path.open("a", encoding="utf-8", newline="\n") as stream:
        for name in (
            "factor_v3_formal_control_contract.py",
            "factor_v3_formal_supervisor_loader_runtime.py",
            "factor_v3_formal_trusted_supervisor.py",
        ):
            stream.write(f"/app/{name}\n")
    completion_payload = (
        formal_bootstrap_renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
            authorization_path=authorization_path,
            trusted_public_key_spki_der=trusted_public_der,
        )
    )
    completion_authorization_path = _write_completion_authorization(
        root,
        completion_payload,
    )
    bootstrap_publication = (
        formal_bootstrap_renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
            authorization_path=authorization_path,
            completion_authorization_path=completion_authorization_path,
            trusted_public_key_spki_der=trusted_public_der,
        )
    )
    publication = formal_control._publish_with_trust(
        authorization_path=authorization_path,
        completion_marker_path=bootstrap_publication["completion_marker_path"],
        trusted_public_key_spki_der=trusted_public_der,
    )
    ledger_root = (root / "execution-ledger").resolve()
    ledger_root.mkdir()
    credential = (root / "points-primary.token").resolve()
    secret = f"fixture-native-secret-{uuid.uuid4()}".encode("ascii")
    credential.write_bytes(secret)
    _replace_acl(
        credential,
        builtin_users_rights=None,
        grant_current_user=False,
        service_writer_sid=service_sid,
        owner_rights="RC",
    )
    _replace_acl(
        ledger_root,
        builtin_users_rights="M",
        grant_current_user=True,
    )
    private_key, _public_der = _execution_test_key(root)
    launch_publication = (
        formal_control._build_factor_v3_formal_supervisor_launch_authorization_with_trust(
            authorization_path=authorization_path,
            completion_marker_path=bootstrap_publication["completion_marker_path"],
            publication_receipt_path=publication[
                "supervisor_publication_receipt_path"
            ],
            private_key_path=private_key,
            trusted_public_key_spki_der=trusted_public_der,
            verify_reviewed_sources=False,
            action="run",
            execution_ledger_root=ledger_root,
            credential_path=credential,
            now_utc=now,
        )
    )
    launch_authorization = Path(
        str(launch_publication["launch_authorization_path"])
    )
    launch_sha256 = _file_sha256(launch_authorization)
    native_completed_root = ledger_root / "native_completed"
    native_completed_sha_root = native_completed_root / "sha256"
    native_completed_parent = native_completed_sha_root / launch_sha256[:2]
    native_completed_root.mkdir()
    _replace_acl(
        native_completed_root,
        builtin_users_rights=None,
        grant_current_user=False,
        service_writer_sid=service_sid,
        owner_rights="RC",
    )
    native_completed_sha_root.mkdir()
    native_completed_parent.mkdir()
    _replace_acl(
        native_completed_sha_root,
        builtin_users_rights=None,
        grant_current_user=False,
        service_writer_sid=service_sid,
        owner_rights="RC",
    )
    _replace_acl(
        native_completed_parent,
        builtin_users_rights=None,
        grant_current_user=False,
        service_writer_sid=service_sid,
        owner_rights="RC",
    )
    payload = json.loads(launch_authorization.read_bytes())["payload"]
    loader_path = Path(str(payload["supervisor_loader_path"]))
    loader_sha256 = str(payload["supervisor_loader_sha256"])
    runtime = Path(str(payload["python_executable_path"]))
    candidate = broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        authorization_path=payload["bootstrap_execution_authorization_path"],
        completion_marker_path=payload["publication_completion_marker_path"],
        publication_receipt_path=payload["supervisor_publication_receipt_path"],
        launch_authorization_path=launch_authorization,
        execution_ledger_root=payload["execution_ledger_root"],
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=root / "native-candidates",
        candidate=candidate,
    )
    modulus, exponent = formal_supervisor._parse_rsa3072_spki(
        trusted_public_der
    )
    key_name = f"quant-signal-lkj-disposable-test-{uuid.uuid4()}"
    manifest = root / "native_supervisor_e2e_manifest.h"
    manifest.write_text(
        "\n".join(
            (
                '#define F3_BROKER_RUNTIME_PATH L"' + _c_wide(runtime) + '"',
                '#define F3_BROKER_RUNTIME_SHA256 L"'
                + hashlib.sha256(runtime.read_bytes()).hexdigest()
                + '"',
                '#define F3_BROKER_SOURCE_PATH L"'
                + str(loader_path.resolve()).replace("\\", "\\\\")
                + '"',
                f'#define F3_BROKER_SOURCE_SHA256 L"{loader_sha256}"',
                '#define F3_BROKER_CREDENTIAL_SLOT_PATH L"'
                + _c_wide(credential)
                + '"',
                (
                    '#define F3_BROKER_CNG_PROVIDER '
                    'L"Microsoft Software Key Storage Provider"'
                ),
                f'#define F3_BROKER_CNG_KEY_NAME L"{key_name}"',
                "#define F3_BROKER_CNG_ALGORITHM NCRYPT_RSA_ALGORITHM",
                '#define F3_BROKER_SERVICE_NAME L"DisposableFixtureService"',
                f'#define F3_BROKER_SERVICE_SID L"{service_sid}"',
                f'#define F3_BROKER_RESTRICTING_SID L"{service_sid}"',
                f'#define F3_BROKER_WORKER_SID L"{worker_sid}"',
                "#define F3_BROKER_TESTING_SUPERVISOR_TOKEN_COMPATIBILITY 1",
                '#define F3_BROKER_EXECUTION_PUBLIC_MODULUS_HEX "'
                + modulus.to_bytes(384, "big").hex()
                + '"',
                f"#define F3_BROKER_EXECUTION_PUBLIC_EXPONENT {exponent}u",
                "#define F3_BROKER_TESTING 1",
                "#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1",
                "",
            )
        ),
        encoding="utf-8",
    )
    native = root / "factor_v3_formal_native_broker-supervisor-e2e.exe"
    _compile_source(
        source=BROKER_SOURCE,
        output=native,
        includes=[root, BROKER_ROOT],
        definitions=[
            '-DF3_BROKER_MANIFEST_HEADER="native_supervisor_e2e_manifest.h"',
        ],
        libraries=["-lncrypt"],
    )
    try:
        created = subprocess.run(
            [str(native), "--test-create-persistent-cng-key"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert created.returncode == 0, created.stderr.decode(
            "utf-8",
            errors="replace",
        )
        exported = subprocess.run(
            [str(native), "--test-export-persistent-cng-public"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert exported.returncode == 0, exported.stderr.decode(
            "utf-8",
            errors="replace",
        )
        public_fields = dict(
            line.split("=", 1)
            for line in exported.stdout.decode("ascii").splitlines()
        )
        public_blob_hex = public_fields["public_blob_hex"]
        public_blob_sha256 = public_fields["public_blob_sha256"]
        assert re.fullmatch(r"[0-9a-f]+", public_blob_hex)
        assert re.fullmatch(r"[0-9a-f]{64}", public_blob_sha256)
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\n".join(
                (
                    '#define F3_BROKER_COMPLETION_KEY_ID '
                    '"factor-v3-formal-native-completion"',
                    '#define F3_BROKER_COMPLETION_KEY_VERSION "v1"',
                    '#define F3_BROKER_COMPLETION_PUBLIC_BLOB_HEX '
                    f'"{public_blob_hex}"',
                    '#define F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256 '
                    f'"{public_blob_sha256}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        _compile_source(
            source=BROKER_SOURCE,
            output=native,
            includes=[root, BROKER_ROOT],
            definitions=[
                '-DF3_BROKER_MANIFEST_HEADER="native_supervisor_e2e_manifest.h"',
            ],
            libraries=["-lncrypt"],
        )
    except BaseException:
        subprocess.run(
            [str(native), "--test-delete-persistent-cng-key"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        raise
    return native, Path(publication["candidate_path"]), secret, key_name


def test_native_manifest_uses_fixed_cng_identity_and_no_private_key_file_slot() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    manifest = BROKER_MANIFEST.read_text(encoding="utf-8")

    assert "F3_BROKER_SIGNING_KEY_SLOT_PATH" not in source
    assert "F3_BROKER_SIGNING_KEY_SLOT_PATH" not in manifest
    assert '#define F3_BROKER_CNG_PROVIDER L"Microsoft Software Key Storage Provider"' in (
        manifest
    )
    assert '#define F3_BROKER_CNG_KEY_NAME L"quant-signal-lkj-factor-v3-formal"' in (
        manifest
    )
    assert '#define F3_BROKER_CNG_ALGORITHM NCRYPT_RSA_ALGORITHM' in manifest
    assert "NCryptSignHash" in source
    assert "NCRYPT_ALLOW_EXPORT_FLAG" not in source
    assert "NCRYPT_ALLOW_PLAINTEXT_EXPORT_FLAG" not in source


def test_production_worker_token_uses_one_fixed_restricted_sid_and_cannot_open_secret() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    manifest = BROKER_MANIFEST.read_text(encoding="utf-8")
    token = source[
        source.index("static int create_production_restricted_token"):
        source.index("static int read_exact_pipe_frame")
    ]

    assert "F3_BROKER_WORKER_SID" in manifest
    assert "F3_BROKER_WORKER_SID" in token
    assert "TokenRestrictedSids" in token
    assert "restricted->GroupCount != 1" in token
    assert "EqualSid" in token
    assert "restricted_token_cannot_read_path" in source
    assert source.index("restricted_token_cannot_read_path") < source.index(
        "F3_BROKER_CREDENTIAL_SLOT_PATH",
        source.index("f3_broker_launch_production_supervisor"),
    )


def test_production_completion_uses_persistent_cng_and_has_independent_verifier() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    completion_start = source.index(
        "static int open_persistent_cng_signing_key"
    )
    completion = source[
        completion_start:
        source.index("#ifdef F3_BROKER_TESTING", completion_start)
    ]

    assert "factor-v3-formal-native-broker-completed/v4" in source
    assert "NCryptOpenKey" in completion
    assert "NCryptVerifySignature" in completion
    assert "BCRYPT_RSAFULLPRIVATE_BLOB" in completion
    assert "NCryptDeleteKey" not in completion
    assert "verify_persistent_cng_completion" in source
    assert '--test-verify-persistent-cng-completion' in source


def test_production_completion_preholds_protected_namespace_and_writes_relative_to_handle() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    launch = source[
        source.index("int f3_broker_launch_production_supervisor"):
        source.index(
            "#ifdef F3_BROKER_TESTING\nstatic int test_protected_file_chain"
        )
    ]
    atomic = source[
        source.index("static int atomic_write_persistent_completion"):
        source.index("static int open_persistent_cng_signing_key")
    ]

    assert "hold_protected_completion_namespace" in source
    assert launch.index("hold_protected_completion_namespace") < launch.index(
        "CreateProcessAsUserW"
    )
    assert "held_directory_chain_unchanged" in launch
    assert "NtCreateFile" in atomic
    assert "RootDirectory" in atomic
    assert "NtSetInformationFile" in atomic
    assert "SetFileInformationByHandle" in atomic
    assert "MoveFileExW" not in atomic
    assert '--test-protected-completion-namespace' in source


def test_persistent_completion_signs_only_after_held_terminal_lineage_and_cleans_failed_publication() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    launch = source[
        source.index("int f3_broker_launch_production_supervisor"):
        source.index(
            "#ifdef F3_BROKER_TESTING\nstatic int test_protected_file_chain"
        )
    ]
    lineage = source[
        source.index("static int validate_completion_lineage_before_signing"):
        source.index("static int write_persistent_cng_completion")
    ]

    assert launch.index("read_production_completed") < launch.index(
        "validate_completion_lineage_before_signing"
    )
    assert launch.index("validate_completion_lineage_before_signing") < (
        launch.index("write_persistent_cng_completion")
    )
    assert '"claims"' in lineage
    assert '"completed"' in lineage
    assert '"worker_terminals"' in lineage
    assert "open_held_file" in lineage
    assert "read_candidate" in lineage
    assert "factor-v3-formal-supervisor-execution-claim/v2" in lineage
    assert "factor-v3-formal-supervisor-execution-completed/v2" in lineage
    assert '"status"' in lineage
    assert '"launch_authorization_sha256"' in lineage
    assert '"launch_authorization_schema"' in lineage
    assert '"launch_authorization_signature_sha256"' in lineage
    assert "launch_envelope->signature" in lineage
    assert '"claim_sha256"' in lineage
    assert '"worker_terminal_bytes"' in lineage
    assert '"worker_terminal_schema"' in lineage
    assert '"worker_terminal_sha256"' in lineage
    assert "held_unchanged" in lineage
    assert "worker_terminal_file" in launch
    assert "delete_failed_persistent_completion" in launch


def test_native_resume_claim_v2_binds_original_schema_and_signature_hash() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    validator = source[
        source.index("static int validate_resume_status"):
        source.index("static int validate_resume_lineage")
    ]

    assert "factor-v3-formal-supervisor-execution-claim/v2" in validator
    assert '"launch_authorization_schema"' in validator
    assert '"launch_authorization_signature_sha256"' in validator
    assert "original->signature" in validator
    assert "json_top_has_exact_keys" in validator


def test_persistent_completion_is_canonical_json_and_verifier_is_public_only() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    writer = source[
        source.index("static int write_persistent_cng_completion"):
        source.index("static int hex_signature")
    ]
    verifier = source[
        source.index("static int verify_persistent_cng_completion"):
        source.index("#if 0", source.index(
            "static int verify_persistent_cng_completion"
        ))
    ]
    public_import = source[
        source.index("static int import_completion_public_key"):
        source.index("static int persistent_cng_key_matches_public_pin")
    ]
    manifest = BROKER_MANIFEST.read_text(encoding="utf-8")

    assert "factor-v3-formal-native-broker-completed/v4" in source
    assert '\\"payload\\":' in writer
    assert '\\"signature_hex\\":\\"' in writer
    assert '\\"completion_key_id\\":' in writer
    assert '\\"completion_key_version\\":' in writer
    assert '\\"completion_public_blob_sha256\\":' in writer
    assert "hash_completion_signature_payload" in writer
    assert "import_completion_public_key" in verifier
    assert "BCRYPT_RSAPUBLIC_BLOB" in public_import
    assert "BCryptImportKeyPair" in public_import
    assert "NCryptOpenKey" not in verifier
    assert "open_persistent_cng_signing_key" not in verifier
    assert "F3_BROKER_COMPLETION_PUBLIC_BLOB_HEX" in manifest
    assert "F3_BROKER_COMPLETION_PUBLIC_BLOB_SHA256" in manifest
    assert "F3_BROKER_COMPLETION_KEY_ID" in manifest
    assert "F3_BROKER_COMPLETION_KEY_VERSION" in manifest
    assert 'L"--verify-completion"' in source


def test_native_resume_status_requires_exact_original_run_claim() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    validator = source[
        source.index("static int validate_resume_status"):
        source.index("static int validate_resume_lineage")
    ]

    assert "static const char *const status_keys[]" in validator
    assert "json_top_has_exact_keys" in validator
    assert validator.index("json_top_has_exact_keys") < validator.index(
        "json_top_string_matches"
    )
    assert '"action"' in validator
    assert '"authorization_id_sha256"' in validator
    assert '"authorization_nonce_sha256"' in validator
    assert '"bootstrap_execution_authorization_sha256"' in validator
    assert '"launch_authorization_sha256"' in validator
    assert '"replay_scope"' in validator
    assert '"schema"' in validator
    assert '"status"' in validator
    assert '"run"' in validator


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_interactive_process_is_rejected_as_formal_service_identity(
    tmp_path: Path,
) -> None:
    native = _compile_test_broker(tmp_path)
    completed = _run(native, "--test-require-service-identity")

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "service identity rejected" in completed.stderr.lower()


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_protected_namespace_requires_trusted_owner_and_no_effective_caller_write(
    tmp_path: Path,
) -> None:
    native = _compile_test_broker(tmp_path)

    mutable = _run(native, "--test-protected-namespace", str(tmp_path))
    assert mutable.returncode != 0
    assert "namespace rejected" in mutable.stderr.lower()

    protected_root = Path(os.environ["SystemRoot"]) / "System32"
    protected = _run(native, "--test-protected-namespace", str(protected_root))
    assert protected.returncode == 0, protected.stderr
    assert protected.stdout == ""
    assert protected.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_protected_file_chain_validates_and_holds_all_ancestor_namespaces(
    tmp_path: Path,
) -> None:
    native = _compile_test_broker(tmp_path)
    mutable_file = tmp_path / "mutable-runtime.exe"
    mutable_file.write_bytes(b"disposable\n")
    mutable = _run(
        native,
        "--test-protected-file-chain",
        str(mutable_file),
    )
    assert mutable.returncode != 0
    assert "namespace chain rejected" in mutable.stderr.lower()

    protected_file = Path(os.environ["SystemRoot"]) / "System32" / "kernel32.dll"
    protected = _run(
        native,
        "--test-protected-file-chain",
        str(protected_file),
    )
    assert protected.returncode == 0, protected.stderr
    assert protected.stdout == ""
    assert protected.stderr == ""


def test_production_launch_checks_service_and_fixed_namespace_chains_before_handoff() -> None:
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    launch = source.index('wcscmp(argv[1], L"--launch")')
    service_check = source.index(
        "f3_broker_current_process_is_expected_service",
        launch,
    )
    namespace_check = source.index("hold_production_namespace_chains", launch)
    candidate_check = source.index(
        "f3_broker_validate_production_candidate",
        launch,
    )
    handoff = source.index(
        "f3_broker_launch_production_supervisor",
        candidate_check,
    )
    assert launch < service_check < namespace_check < candidate_check < handoff
    assert "native broker credential handoff is unimplemented" not in source
    production_check = source[
        source.index("hold_production_namespace_chains"):launch
    ]
    assert "F3_BROKER_RUNTIME_PATH" in production_check
    assert "F3_BROKER_SOURCE_PATH" in production_check
    assert "F3_BROKER_CREDENTIAL_SLOT_PATH" in production_check
    assert "ACCESS_ALLOWED_ACE_TYPE" in source
    assert "AccessCheck" in source


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_production_candidate_verifies_signed_v2_launch_authorization(
    tmp_path: Path,
) -> None:
    native, candidate, _launch, _payload, _pins = (
        _build_production_validation_fixture(tmp_path)
    )

    completed = _run(native, "--test-validate-production-candidate", str(candidate))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_production_candidate_rejects_tampered_launch_signature(
    tmp_path: Path,
) -> None:
    from app import factor_v3_formal_native_broker as broker

    native, candidate, launch, _payload, _pins = (
        _build_production_validation_fixture(tmp_path)
    )
    fields = broker._validated_candidate(candidate.read_bytes())
    outer = json.loads(launch.read_bytes())
    signature = bytearray(base64.b64decode(outer["signature_base64"], validate=True))
    signature[0] ^= 1
    outer["signature_base64"] = base64.b64encode(signature).decode("ascii")
    tampered_raw = _canonical_bytes(outer)
    tampered_digest = hashlib.sha256(tampered_raw).hexdigest()
    tampered_launch = (
        tmp_path
        / "launch_authorizations"
        / "sha256"
        / tampered_digest[:2]
        / f"{tampered_digest}.json"
    )
    tampered_launch.parent.mkdir(parents=True)
    tampered_launch.write_bytes(tampered_raw)
    raw = broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        authorization_path=fields["authorization_path"],
        completion_marker_path=fields["completion_marker_path"],
        publication_receipt_path=fields["publication_receipt_path"],
        launch_authorization_path=tampered_launch,
        execution_ledger_root=fields["execution_ledger_root"],
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=tmp_path / "tampered-native-candidates",
        candidate=raw,
    )

    completed = _run(
        native,
        "--test-validate-production-candidate",
        publication["candidate_path"],
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "candidate rejected" in completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_production_candidate_rejects_signed_ledger_binding_drift(
    tmp_path: Path,
) -> None:
    from app import factor_v3_formal_native_broker as broker

    native, candidate, _launch, payload, _pins = (
        _build_production_validation_fixture(tmp_path)
    )
    fields = broker._validated_candidate(candidate.read_bytes())
    drifted = dict(payload)
    drifted_root = (tmp_path / "signed-drifted-ledger").resolve()
    drifted_root.mkdir()
    drifted["execution_ledger_root"] = str(drifted_root)
    drifted_launch = _rewrite_authorization(
        tmp_path,
        drifted,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    raw = broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        authorization_path=fields["authorization_path"],
        completion_marker_path=fields["completion_marker_path"],
        publication_receipt_path=fields["publication_receipt_path"],
        launch_authorization_path=drifted_launch,
        execution_ledger_root=fields["execution_ledger_root"],
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=tmp_path / "drifted-native-candidates",
        candidate=raw,
    )

    completed = _run(
        native,
        "--test-validate-production-candidate",
        publication["candidate_path"],
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "candidate rejected" in completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_production_resume_verifies_original_run_and_claim_lineage(
    tmp_path: Path,
) -> None:
    from app import factor_v3_formal_native_broker as broker

    native, _candidate, original_launch, original, _pins = (
        _build_production_validation_fixture(tmp_path)
    )
    original_sha256 = _file_sha256(original_launch)
    ledger_root = Path(str(original["execution_ledger_root"]))
    status_raw = _canonical_bytes(
        {
            "action": "run",
            "authorization_id_sha256": original["authorization_id_sha256"],
            "authorization_nonce_sha256": original["authorization_nonce_sha256"],
            "bootstrap_execution_authorization_sha256": original[
                "bootstrap_execution_authorization_sha256"
            ],
            "launch_authorization_sha256": original_sha256,
            "replay_scope": original["replay_scope"],
            "schema": formal_supervisor.CLAIM_SCHEMA,
            "status": "claimed",
        }
    )
    status_path = formal_supervisor.claim_path_for_authorization(
        ledger_root,
        original_sha256,
    )
    status_path.parent.mkdir(parents=True)
    status_path.write_bytes(status_raw)
    resume = dict(original)
    resume.update(
        {
            "action": "resume",
            "resume_of_authorization_id_sha256": original[
                "authorization_id_sha256"
            ],
            "resume_of_authorization_nonce_sha256": original[
                "authorization_nonce_sha256"
            ],
            "resume_of_authorization_sha256": original_sha256,
            "resume_of_bootstrap_execution_authorization_sha256": original[
                "bootstrap_execution_authorization_sha256"
            ],
            "resume_of_replay_scope": original["replay_scope"],
            "resume_status_path": str(status_path),
            "resume_status_sha256": hashlib.sha256(status_raw).hexdigest(),
        }
    )
    resume_launch = _rewrite_authorization(
        tmp_path,
        resume,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    raw = broker.build_factor_v3_formal_native_broker_candidate(
        action="resume",
        authorization_path=resume["bootstrap_execution_authorization_path"],
        completion_marker_path=resume["publication_completion_marker_path"],
        publication_receipt_path=resume["supervisor_publication_receipt_path"],
        launch_authorization_path=resume_launch,
        execution_ledger_root=resume["execution_ledger_root"],
        resume_authorization_path=original_launch,
        resume_status_path=status_path,
    )
    publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=tmp_path / "resume-native-candidates",
        candidate=raw,
    )

    completed = _run(
        native,
        "--test-validate-production-candidate",
        publication["candidate_path"],
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""

    first_resume_sha256 = _file_sha256(resume_launch)
    second_status_raw = _canonical_bytes(
        {
            "action": "resume",
            "authorization_id_sha256": resume["authorization_id_sha256"],
            "authorization_nonce_sha256": resume["authorization_nonce_sha256"],
            "bootstrap_execution_authorization_sha256": resume[
                "bootstrap_execution_authorization_sha256"
            ],
            "launch_authorization_sha256": first_resume_sha256,
            "replay_scope": resume["replay_scope"],
            "schema": formal_supervisor.CLAIM_SCHEMA,
            "status": "claimed",
        }
    )
    second_status_path = formal_supervisor.claim_path_for_authorization(
        ledger_root,
        first_resume_sha256,
    )
    second_status_path.parent.mkdir(parents=True, exist_ok=True)
    second_status_path.write_bytes(second_status_raw)
    resume_of_resume = dict(resume)
    resume_of_resume.update(
        {
            "resume_of_authorization_sha256": first_resume_sha256,
            "resume_status_path": str(second_status_path),
            "resume_status_sha256": hashlib.sha256(second_status_raw).hexdigest(),
        }
    )
    resume_of_resume_launch = _rewrite_authorization(
        tmp_path,
        resume_of_resume,
        tmp_path / "execution-key" / "execution-private.pem",
    )
    invalid_raw = broker.build_factor_v3_formal_native_broker_candidate(
        action="resume",
        authorization_path=resume_of_resume[
            "bootstrap_execution_authorization_path"
        ],
        completion_marker_path=resume_of_resume[
            "publication_completion_marker_path"
        ],
        publication_receipt_path=resume_of_resume[
            "supervisor_publication_receipt_path"
        ],
        launch_authorization_path=resume_of_resume_launch,
        execution_ledger_root=resume_of_resume["execution_ledger_root"],
        resume_authorization_path=resume_launch,
        resume_status_path=second_status_path,
    )
    invalid_publication = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=tmp_path / "resume-of-resume-native-candidates",
        candidate=invalid_raw,
    )

    rejected = _run(
        native,
        "--test-validate-production-candidate",
        invalid_publication["candidate_path"],
    )

    assert rejected.returncode != 0
    assert rejected.stdout == ""
    assert "candidate rejected" in rejected.stderr


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_disposable_cng_key_is_nonexportable_signs_and_is_deleted(
    tmp_path: Path,
) -> None:
    native = _compile_test_broker(tmp_path)
    key_name = f"quant-signal-lkj-disposable-test-{uuid.uuid4()}"

    first = _run(native, "--test-cng-disposable", key_name)
    assert first.returncode == 0, first.stderr
    assert first.stdout == "CNG_DISPOSABLE_SIGN_OK\n"
    assert first.stderr == ""

    absent = _run(native, "--test-cng-key-absent", key_name)
    assert absent.returncode == 0, absent.stderr
    assert absent.stdout == ""
    assert absent.stderr == ""

    second = _run(native, "--test-cng-disposable", key_name)
    assert second.returncode == 0, second.stderr


def test_service_installer_is_explicit_dry_run_and_documents_external_tcb() -> None:
    script = SERVICE_INSTALLER.read_text(encoding="utf-8")

    assert "SupportsShouldProcess" in script
    assert "DRY-RUN" in script
    assert "Windows administrator" in script
    assert "SCM" in script
    assert "service SID" in script
    assert "ACL" in script
    assert "CNG" in script
    assert "exit 1" in script


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_attribute_list_failed_initialization_is_never_deleted(
    tmp_path: Path,
) -> None:
    native = _compile_test_broker(tmp_path)
    completed = _run(native, "--test-attribute-init-failure-cleanup")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    assert source.count("attributes_initialized") >= 2


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_restricted_child_gets_credential_only_after_bound_ready_and_broker_completes(
    tmp_path: Path,
) -> None:
    program_data = Path(os.environ["ProgramData"])
    root = Path(
        tempfile.mkdtemp(
            prefix="quant-signal-lkj-disposable-handoff-",
            dir=program_data,
        )
    )
    try:
        native, candidate, provisional, completed, secret, claim_sha256 = (
            _build_handoff_fixture(root)
        )
        credential = root / "broker-secret" / "points-primary.slot"
        hardlink = root / "broker-secret" / "points-primary-hardlink.slot"
        os.link(credential, hardlink)
        rejected_key = f"quant-signal-lkj-disposable-test-{uuid.uuid4()}"
        rejected = _run(
            native,
            "--test-credential-handoff",
            str(candidate),
            str(provisional),
            str(completed),
            rejected_key,
        )
        assert rejected.returncode != 0
        assert not provisional.exists()
        assert not completed.exists()
        hardlink.unlink()

        key_name = f"quant-signal-lkj-disposable-test-{uuid.uuid4()}"
        launched = _run(
            native,
            "--test-credential-handoff",
            str(candidate),
            str(provisional),
            str(completed),
            key_name,
        )
        assert launched.returncode == 0, launched.stderr
        assert launched.stdout == ""
        assert launched.stderr == ""
        assert provisional.read_text(encoding="ascii") == (
            "schema=factor-v3-formal-native-broker-provisional/v1\n"
            "restricted_token=1\n"
            "pre_claim_secret_open_denied=1\n"
            "credential_handle_read_ok=1\n"
            "post_claim_secret_reopen_denied=1\n"
            "runtime_namespace_write_denied=1\n"
            "completed_path_write_denied=1\n"
            "status=provisional\n"
        )
        ledger = completed.read_bytes()
        assert b"schema=factor-v3-formal-native-broker-completed/v1\n" in ledger
        assert b"status=completed\n" in ledger
        assert f"claim_sha256={claim_sha256}\n".encode("ascii") in ledger
        assert b"signature_algorithm=RSA-PKCS1-SHA256\n" in ledger
        assert b"signature_verified_before_key_delete=1\n" in ledger
        assert secret not in provisional.read_bytes()
        assert secret not in ledger
        assert secret not in launched.stdout.encode()
        assert secret not in launched.stderr.encode()

        absent = _run(native, "--test-cng-key-absent", key_name)
        assert absent.returncode == 0, absent.stderr
        source = BROKER_SOURCE.read_text(encoding="utf-8")
        assert "CreateRestrictedToken" in source
        assert "CreateProcessAsUserW" in source
        assert "PROC_THREAD_ATTRIBUTE_HANDLE_LIST" in source
        assert "DuplicateHandle" in source
        assert source.index("validate_bound_ready") < source.index(
            "F3_BROKER_CREDENTIAL_SLOT_PATH",
            source.index("validate_bound_ready"),
        )
    finally:
        _grant_cleanup_access(root)
        shutil.rmtree(root)


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_launches_rendered_supervisor_and_synthetic_worker_e2e(
    tmp_path: Path,
) -> None:
    del tmp_path
    root = Path(
        tempfile.mkdtemp(
            prefix="quant-signal-lkj-native-supervisor-e2e-",
            dir=Path(os.environ["ProgramData"]),
        )
    )
    try:
        native, candidate, secret, key_name = (
            _build_native_supervisor_e2e_fixture(root)
        )
        credential = root / "points-primary.token"
        token_check = subprocess.run(
            [
                str(native),
                "--test-fixed-worker-token-denies-secret",
                str(credential),
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert token_check.returncode == 0, token_check.stderr.decode(
            "utf-8",
            errors="replace",
        )
        runtime_check = subprocess.run(
            [str(native), "--test-isolated-supervisor-runtime-startup"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert runtime_check.returncode == 0, runtime_check.stderr.decode(
            "utf-8",
            errors="replace",
        )
        namespace_check = subprocess.run(
            [
                str(native),
                "--test-protected-completion-namespace",
                str(candidate),
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert namespace_check.returncode == 0, namespace_check.stderr.decode(
            "utf-8",
            errors="replace",
        )

        completed = subprocess.run(
            [
                str(native),
                "--test-production-supervisor-launch",
                str(candidate),
            ],
            check=False,
            capture_output=True,
            timeout=180,
        )

        assert completed.returncode == 0, completed.stderr.decode(
            "utf-8",
            errors="replace",
        )
        assert completed.stderr == b""
        assert completed.stdout.startswith(
            b"COMPLETED factor-v3-formal-native-broker/v2\n"
        )
        assert b"launch_authorization_sha256=" in completed.stdout
        assert b"claim_sha256=" in completed.stdout
        assert b"supervisor_completed_sha256=" in completed.stdout
        assert b"worker_terminal_sha256=" in completed.stdout
        assert b"native_completed_receipt_sha256=" in completed.stdout
        verified = subprocess.run(
            [
                str(native),
                "--test-verify-persistent-cng-completion",
                str(candidate),
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert verified.returncode == 0, verified.stderr.decode(
            "utf-8",
            errors="replace",
        )
        present = subprocess.run(
            [str(native), "--test-persistent-cng-key-present"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert present.returncode == 0, present.stderr.decode(
            "utf-8",
            errors="replace",
        )
        deleted = subprocess.run(
            [str(native), "--test-delete-persistent-cng-key"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert deleted.returncode == 0, deleted.stderr.decode(
            "utf-8",
            errors="replace",
        )
        public_only = subprocess.run(
            [str(native), "--verify-completion", str(candidate)],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert public_only.returncode == 0, public_only.stderr.decode(
            "utf-8",
            errors="replace",
        )
        from app import factor_v3_formal_native_broker as broker

        fields = broker._validated_candidate(candidate.read_bytes())
        launch_path = Path(fields["launch_authorization_path"])
        launch_sha256 = _file_sha256(launch_path)
        receipt_path = (
            Path(fields["execution_ledger_root"])
            / "native_completed"
            / "sha256"
            / launch_sha256[:2]
            / f"{launch_sha256}.json"
        )
        receipt_raw = receipt_path.read_bytes()
        receipt_value = json.loads(receipt_raw)
        assert receipt_raw == _canonical_bytes(receipt_value)
        assert set(receipt_value) == {"payload", "signature_hex"}
        assert receipt_value["payload"]["schema"] == (
            "factor-v3-formal-native-broker-completed/v4"
        )
        assert receipt_value["payload"]["completion_key_id"] == (
            "factor-v3-formal-native-completion"
        )
        assert receipt_value["payload"]["completion_key_version"] == "v1"
        assert re.fullmatch(
            r"[0-9a-f]{64}",
            receipt_value["payload"]["completion_public_blob_sha256"],
        )
        worker_terminal_path = (
            Path(fields["execution_ledger_root"])
            / "worker_terminals"
            / "sha256"
            / launch_sha256[:2]
            / f"{launch_sha256}.json"
        )
        worker_terminal_raw = worker_terminal_path.read_bytes()
        assert worker_terminal_raw.endswith(b"\n")
        assert worker_terminal_raw[:-1].find(b"\n") == -1
        assert receipt_value["payload"]["worker_terminal_bytes"] == len(
            worker_terminal_raw
        )
        assert receipt_value["payload"]["worker_terminal_schema"] == (
            "factor-v3-formal-bootstrap-worker-terminal/v2"
        )
        assert receipt_value["payload"]["worker_terminal_sha256"] == (
            hashlib.sha256(worker_terminal_raw).hexdigest()
        )
        assert json.loads(worker_terminal_raw)["schema"] == (
            receipt_value["payload"]["worker_terminal_schema"]
        )
        receipt = bytearray(receipt_raw)
        signature_offset = receipt.index(b'"signature_hex":"') + len(
            b'"signature_hex":"'
        )
        receipt[signature_offset] = (
            ord("0") if receipt[signature_offset] != ord("0") else ord("1")
        )
        receipt_path.write_bytes(receipt)
        tampered = subprocess.run(
            [
                str(native),
                "--verify-completion",
                str(candidate),
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
        assert tampered.returncode != 0
        assert tampered.stdout == b""
        assert key_name.encode("utf-8") not in completed.stdout
        assert secret not in completed.stdout
        assert secret not in completed.stderr
    finally:
        if "native" in locals() and native.exists():
            subprocess.run(
                [str(native), "--test-delete-persistent-cng-key"],
                check=False,
                capture_output=True,
                timeout=30,
            )
        _grant_cleanup_access(root)
        def remove_readonly(
            function: object,
            path: str,
            _error: object,
        ) -> None:
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(root, onerror=remove_readonly)
