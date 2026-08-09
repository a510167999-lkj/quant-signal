from __future__ import annotations

import ctypes
import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app import factor_v3_formal_trusted_supervisor as supervisor
from tests.test_factor_v3_formal_native_service_tcb import (
    BROKER_ROOT,
    _compile_source,
    _run,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SOURCE = (
    REPO_ROOT
    / "tests"
    / "native"
    / "factor_v3_parent_source_root_lease_harness.c"
)
ABI_SOURCE = (
    REPO_ROOT
    / "tests"
    / "native"
    / "factor_v3_parent_source_root_lease_abi.c"
)
RUN_SPEC_SHA256 = "0123456789abcdef" * 4
SEMANTIC_INPUT_ROOT_SHA256 = "89abcdef01234567" * 4
ATTEMPT_KEY_SCHEMA = "factor-v3-parent-source-development-authority-attempt-key/v1"
ATTEMPT_KEY_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "schema": ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": SEMANTIC_INPUT_ROOT_SHA256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
GLOBAL_ATTEMPT_SCHEMA = "factor-v3-parent-source-global-attempt-identity/v1"
GLOBAL_ATTEMPT_IDENTITY_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "attempt_key_sha256": ATTEMPT_KEY_SHA256,
            "schema": GLOBAL_ATTEMPT_SCHEMA,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_CONTRACT_COMMANDS = (
    ("--identity-binding",),
    (
        "--epoch-matrix",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
    ),
    (
        "--epoch-probe",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
        "1",
    ),
    (
        "--epoch-stage-hold",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
        "C:\\disposable-parent-source-ledger\\ready",
        "C:\\disposable-parent-source-ledger\\release",
    ),
    (
        "--lease-hold",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
        "C:\\disposable-parent-source-ledger\\ready",
        "C:\\disposable-parent-source-ledger\\release",
    ),
    (
        "--lease-try",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
    ),
    (
        "--lease-tamper",
        "C:\\disposable-parent-source-ledger",
        "C:\\disposable-parent-source-ledger\\run-claim.json",
        "C:\\disposable-parent-source-ledger\\verify-claim.json",
        "C:\\alternate-parent-source-ledger",
        "C:\\outside-parent-source-ledger\\run-claim.json",
        "C:\\outside-parent-source-ledger\\verify-claim.json",
    ),
)
_MISSING_CONTRACT_MARKERS = {
    "persistent root epoch authority": (
        "factor_v3_parent_source_lookup_root_epoch_transition",
        "factor_v3_parent_source_publish_run_receipt",
        "factor_v3_parent_source_publish_terminal_receipt",
    ),
}
_UNRELATED_FAILURE_MARKERS = (
    "no such file or directory",
    "requires a disposable test manifest",
    "cannot find -l",
    "unrecognized command-line option",
    "fatal error:",
)


@pytest.fixture(scope="module")
def parser_only_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("parent-source-root-lease-harness") / (
        "factor_v3_parent_source_root_lease_harness-parser.exe"
    )
    _compile_source(
        source=HARNESS_SOURCE,
        output=output,
        includes=[BROKER_ROOT],
        definitions=["-DF3_PARENT_SOURCE_ROOT_LEASE_HARNESS_PARSER_ONLY=1"],
        libraries=["-lncrypt"],
    )
    return output


@pytest.fixture(scope="module")
def parent_source_abi(tmp_path_factory: pytest.TempPathFactory) -> ctypes.CDLL:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("Win32 GCC is unavailable")
    output = tmp_path_factory.mktemp("parent-source-root-lease-abi") / (
        "factor_v3_parent_source_root_lease_abi.dll"
    )
    completed = subprocess.run(
        [
            gcc,
            "-std=c11",
            "-DUNICODE",
            "-D_UNICODE",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            f"-I{BROKER_ROOT}",
            str(ABI_SOURCE),
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
    library = ctypes.CDLL(str(output))
    library.f3_parent_source_abi_derive_attempt_key.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    library.f3_parent_source_abi_derive_attempt_key.restype = ctypes.c_int
    library.f3_parent_source_abi_derive_global_identity.argtypes = (
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    library.f3_parent_source_abi_derive_global_identity.restype = ctypes.c_int
    library.f3_parent_source_abi_derive_root_policy.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    library.f3_parent_source_abi_derive_root_policy.restype = ctypes.c_int
    acquire_arguments = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
    )
    library.f3_parent_source_abi_acquire_status.argtypes = (
        *acquire_arguments,
        ctypes.c_uint,
    )
    library.f3_parent_source_abi_acquire_status.restype = ctypes.c_int
    library.f3_parent_source_abi_wrong_thread_release_rejected.argtypes = (
        acquire_arguments
    )
    library.f3_parent_source_abi_wrong_thread_release_rejected.restype = ctypes.c_int
    library.f3_parent_source_abi_root_epoch_transition.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    )
    library.f3_parent_source_abi_root_epoch_transition.restype = ctypes.c_int
    receipt_arguments = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
    )
    library.f3_parent_source_abi_publish_run_receipt.argtypes = receipt_arguments
    library.f3_parent_source_abi_publish_run_receipt.restype = ctypes.c_int
    library.f3_parent_source_abi_publish_terminal_receipt.argtypes = receipt_arguments
    library.f3_parent_source_abi_publish_terminal_receipt.restype = ctypes.c_int
    return library


def test_disposable_harness_freezes_parent_source_attempt_identity() -> None:
    source = HARNESS_SOURCE.read_text(encoding="utf-8")
    path_variants = (
        {
            "attempt_key_sha256": ATTEMPT_KEY_SHA256,
            "global_run_claim_path": "C:\\first\\run.claim.json",
            "global_verify_claim_path": "C:\\first\\verify.claim.json",
            "run_root": "C:\\first\\run",
            "run_spec_sha256": "1" * 64,
            "verification_root": "C:\\first\\verify",
        },
        {
            "attempt_key_sha256": ATTEMPT_KEY_SHA256,
            "global_run_claim_path": "D:\\second\\run.claim.json",
            "global_verify_claim_path": "D:\\second\\verify.claim.json",
            "run_root": "D:\\second\\run",
            "run_spec_sha256": "2" * 64,
            "verification_root": "D:\\second\\verify",
        },
    )
    observed_global_identities = {
        hashlib.sha256(
            json.dumps(
                {
                    "attempt_key_sha256": variant["attempt_key_sha256"],
                    "schema": GLOBAL_ATTEMPT_SCHEMA,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for variant in path_variants
    }

    assert ATTEMPT_KEY_SHA256 == (
        "d7b05c7a8eedfe55931520f6840c0e8bfbe883311e3d50f74f9071b4494eb93f"
    )
    assert GLOBAL_ATTEMPT_IDENTITY_SHA256 == (
        "0d9a4e8c499eb74dc375427c9e9438bf9a3bc1a910f4df6a04ac9b3df6827fa0"
    )
    assert observed_global_identities == {GLOBAL_ATTEMPT_IDENTITY_SHA256}
    assert RUN_SPEC_SHA256 in source
    assert SEMANTIC_INPUT_ROOT_SHA256 in source
    assert ATTEMPT_KEY_SCHEMA in source
    assert ATTEMPT_KEY_SHA256 in source
    assert GLOBAL_ATTEMPT_SCHEMA in source
    assert GLOBAL_ATTEMPT_IDENTITY_SHA256 in source
    assert "F3_BROKER_TESTING 1" in source
    assert "F3_BROKER_DISPOSABLE_TEST_MANIFEST 1" in source
    assert "DISPOSABLE_" in source


def test_parent_source_root_lease_compiled_contract(
    parser_only_harness: Path,
    tmp_path: Path,
) -> None:
    self_check = _run(parser_only_harness, "--self-check")
    assert self_check.returncode == 0, self_check.stderr
    assert self_check.stdout == "DISPOSABLE_PARENT_SOURCE_HARNESS_SELF_CHECK_OK\n"

    for arguments in _CONTRACT_COMMANDS:
        parsed = _run(parser_only_harness, *arguments)
        assert parsed.returncode == 90, (arguments, parsed.stderr)
        assert parsed.stdout.startswith("DISPOSABLE_CONTRACT_UNAVAILABLE=")

    rejected = _run(parser_only_harness, "--lease-hold")
    assert rejected.returncode == 64
    assert rejected.stderr == "disposable parent-source harness command rejected\n"

    output = tmp_path / "factor_v3_parent_source_root_lease_harness.exe"
    try:
        _compile_source(
            source=HARNESS_SOURCE,
            output=output,
            includes=[BROKER_ROOT],
            definitions=[],
            libraries=["-lncrypt"],
        )
    except AssertionError as exc:
        compiler_stderr = str(exc)
        folded_stderr = compiler_stderr.casefold()
        for contract, markers in _MISSING_CONTRACT_MARKERS.items():
            assert all(marker in compiler_stderr for marker in markers), (
                contract,
                compiler_stderr,
            )
        assert not any(
            marker in folded_stderr for marker in _UNRELATED_FAILURE_MARKERS
        ), compiler_stderr
        pytest.fail(
            "compiled parent-source root lease contract remains RED:\n"
            f"{compiler_stderr}",
            pytrace=False,
        )

    compiled_self_check = _run(output, "--self-check")
    assert compiled_self_check.returncode == 0, compiled_self_check.stderr
    assert (
        compiled_self_check.stdout
        == "DISPOSABLE_PARENT_SOURCE_HARNESS_SELF_CHECK_OK\n"
    )

    identity = _run(output, "--identity-binding")
    assert identity.returncode == 0, identity.stderr
    assert identity.stdout == (
        "DISPOSABLE_IDENTITY_BOUND="
        f"{RUN_SPEC_SHA256}:{ATTEMPT_KEY_SHA256}:"
        f"{GLOBAL_ATTEMPT_IDENTITY_SHA256}\n"
    )

    global_attempt_ledger_root = tmp_path / "global-attempt-ledger"
    global_attempt_ledger_root.mkdir()
    claim_root = (
        global_attempt_ledger_root
        / "attempts"
        / "sha256"
        / ATTEMPT_KEY_SHA256[:2]
        / ATTEMPT_KEY_SHA256
    )
    claim_root.mkdir(parents=True)
    global_run_claim = claim_root / "run.claim.json"
    global_verify_claim = claim_root / "verify.claim.json"
    alternate_global_attempt_ledger_root = tmp_path / "alternate-global-ledger"
    alternate_global_attempt_ledger_root.mkdir()
    outside_run_claim = tmp_path / "outside-run.claim.json"
    outside_verify_claim = tmp_path / "outside-verify.claim.json"

    epoch = _run(
        output,
        "--epoch-matrix",
        str(global_attempt_ledger_root),
        str(global_run_claim),
        str(global_verify_claim),
    )
    assert epoch.returncode == 0, epoch.stderr
    assert epoch.stdout == "DISPOSABLE_ROOT_EPOCH_MATRIX=verified\n"

    restarted_run_probe = _run(
        output,
        "--epoch-probe",
        str(global_attempt_ledger_root),
        str(global_run_claim),
        str(global_verify_claim),
        "1",
    )
    assert restarted_run_probe.returncode == 0, restarted_run_probe.stderr
    assert restarted_run_probe.stdout == "DISPOSABLE_ROOT_EPOCH_OBSERVED=4:2\n"
    restarted_verify_probe = _run(
        output,
        "--epoch-probe",
        str(global_attempt_ledger_root),
        str(global_run_claim),
        str(global_verify_claim),
        "2",
    )
    assert restarted_verify_probe.returncode == 0, restarted_verify_probe.stderr
    assert restarted_verify_probe.stdout == "DISPOSABLE_ROOT_EPOCH_OBSERVED=4:2\n"

    for extra_name in (
        "fifth-artifact.json",
        "epoch.db-wal",
        "epoch.db-shm",
        "epoch.partial",
        ".parent-source-epoch-staging-interrupted.tmp",
    ):
        extra_path = claim_root / extra_name
        extra_path.write_bytes(b"unexpected")
        polluted_probe = _run(
            output,
            "--epoch-probe",
            str(global_attempt_ledger_root),
            str(global_run_claim),
            str(global_verify_claim),
            "1",
        )
        assert polluted_probe.returncode == 77, (
            extra_name,
            polluted_probe.stdout,
            polluted_probe.stderr,
        )
        extra_path.unlink()

    crash_root = tmp_path / "staging-crash-global-ledger"
    crash_root.mkdir()
    crash_run, crash_verify = supervisor.factor_v3_parent_source_global_claim_paths(
        crash_root,
        ATTEMPT_KEY_SHA256,
    )
    crash_run.parent.mkdir(parents=True)
    stage_ready = tmp_path / "epoch-stage-holder.ready"
    stage_release = tmp_path / "epoch-stage-holder.release"
    stage_holder = subprocess.Popen(
        [
            str(output),
            "--epoch-stage-hold",
            str(crash_root),
            str(crash_run),
            str(crash_verify),
            str(stage_ready),
            str(stage_release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not stage_ready.exists() and stage_holder.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert stage_ready.exists(), stage_holder.communicate(timeout=1)
        stage_holder.kill()
        stage_holder.communicate(timeout=10)
    finally:
        if stage_holder.poll() is None:
            stage_holder.kill()
            stage_holder.communicate(timeout=2)
    assert tuple(crash_run.parent.glob(".parent-source-epoch-staging-*.tmp"))
    crashed_stage_probe = _run(
        output,
        "--epoch-probe",
        str(crash_root),
        str(crash_run),
        str(crash_verify),
        "1",
    )
    assert crashed_stage_probe.returncode == 77, crashed_stage_probe.stderr

    tamper = _run(
        output,
        "--lease-tamper",
        str(global_attempt_ledger_root),
        str(global_run_claim),
        str(global_verify_claim),
        str(alternate_global_attempt_ledger_root),
        str(outside_run_claim),
        str(outside_verify_claim),
    )
    assert tamper.returncode == 0, tamper.stderr
    assert tamper.stdout == "DISPOSABLE_ROOT_LEASE_TAMPER_REJECTED=3\n"

    ready_path = tmp_path / "lease-holder.ready"
    release_path = tmp_path / "lease-holder.release"
    holder = subprocess.Popen(
        [
            str(output),
            "--lease-hold",
            str(global_attempt_ledger_root),
            str(global_run_claim),
            str(global_verify_claim),
            str(ready_path),
            str(release_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    holder_stdout = ""
    holder_stderr = ""
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and holder.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert ready_path.exists(), holder.communicate(timeout=1)

        contended = _run(
            output,
            "--lease-try",
            str(global_attempt_ledger_root),
            str(global_run_claim),
            str(global_verify_claim),
        )
        assert contended.returncode == 75, contended.stderr
        assert contended.stdout == "DISPOSABLE_ROOT_LEASE_CONTENDED=1\n"

        release_path.write_bytes(b"")
        holder_stdout, holder_stderr = holder.communicate(timeout=10)
    finally:
        if holder.poll() is None:
            release_path.write_bytes(b"")
            try:
                holder_stdout, holder_stderr = holder.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder_stdout, holder_stderr = holder.communicate(timeout=2)

    assert holder.returncode == 0, holder_stderr
    assert holder_stdout == "DISPOSABLE_ROOT_LEASE_HELD=1\n"

    after_release = _run(
        output,
        "--lease-try",
        str(global_attempt_ledger_root),
        str(global_run_claim),
        str(global_verify_claim),
    )
    assert after_release.returncode == 0, after_release.stderr
    assert after_release.stdout == "DISPOSABLE_ROOT_LEASE_ACQUIRED=1\n"


def test_python_and_compiled_c_abi_share_identity_and_epoch_contract(
    parent_source_abi: ctypes.CDLL,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempt_output = ctypes.create_string_buffer(65)
    assert parent_source_abi.f3_parent_source_abi_derive_attempt_key(
        SEMANTIC_INPUT_ROOT_SHA256.encode("ascii"),
        attempt_output,
        ctypes.sizeof(attempt_output),
    ) == 1
    assert (
        attempt_output.value.decode("ascii")
        == supervisor.factor_v3_parent_source_derive_attempt_key_sha256(
            SEMANTIC_INPUT_ROOT_SHA256
        )
        == ATTEMPT_KEY_SHA256
    )

    global_output = ctypes.create_string_buffer(65)
    assert parent_source_abi.f3_parent_source_abi_derive_global_identity(
        attempt_output.value,
        global_output,
        ctypes.sizeof(global_output),
    ) == 1
    assert (
        global_output.value.decode("ascii")
        == supervisor.factor_v3_parent_source_derive_global_attempt_identity_sha256(
            attempt_output.value.decode("ascii")
        )
        == GLOBAL_ATTEMPT_IDENTITY_SHA256
    )

    rejected_output = ctypes.create_string_buffer(65)
    assert parent_source_abi.f3_parent_source_abi_derive_attempt_key(
        SEMANTIC_INPUT_ROOT_SHA256.upper().encode("ascii"),
        rejected_output,
        ctypes.sizeof(rejected_output),
    ) == 0
    with pytest.raises(supervisor.FormalSupervisorError):
        supervisor.factor_v3_parent_source_derive_attempt_key_sha256(
            SEMANTIC_INPUT_ROOT_SHA256.upper()
        )

    global_root = tmp_path / "abi-global-attempt-ledger"
    global_root.mkdir()
    run_claim, verify_claim = supervisor.factor_v3_parent_source_global_claim_paths(
        global_root,
        ATTEMPT_KEY_SHA256,
    )
    run_claim.parent.mkdir(parents=True)
    root_policy_output = ctypes.create_string_buffer(65)
    assert parent_source_abi.f3_parent_source_abi_derive_root_policy(
        str(global_root),
        root_policy_output,
        ctypes.sizeof(root_policy_output),
    ) == 1
    assert (
        root_policy_output.value.decode("ascii")
        == supervisor.factor_v3_parent_source_root_policy_sha256(global_root)
    )
    def c_transition(action: int) -> tuple[int, int]:
        observed = ctypes.c_uint(999)
        transition = ctypes.c_uint(999)
        accepted = parent_source_abi.f3_parent_source_abi_root_epoch_transition(
            str(global_root),
            str(run_claim),
            str(verify_claim),
            RUN_SPEC_SHA256.encode("ascii"),
            ATTEMPT_KEY_SHA256.encode("ascii"),
            GLOBAL_ATTEMPT_IDENTITY_SHA256.encode("ascii"),
            action,
            ctypes.byref(observed),
            ctypes.byref(transition),
        )
        assert accepted == 1
        return observed.value, transition.value

    receipt_args = (
        str(global_root),
        str(run_claim),
        str(verify_claim),
        RUN_SPEC_SHA256.encode("ascii"),
        ATTEMPT_KEY_SHA256.encode("ascii"),
        GLOBAL_ATTEMPT_IDENTITY_SHA256.encode("ascii"),
    )
    assert c_transition(supervisor.PARENT_SOURCE_ROOT_ACTION_RUN) == (
        supervisor.PARENT_SOURCE_ROOT_STATE_EMPTY,
        supervisor.PARENT_SOURCE_ROOT_TRANSITION_START_RUN,
    )
    assert c_transition(supervisor.PARENT_SOURCE_ROOT_ACTION_RUN) == (
        supervisor.PARENT_SOURCE_ROOT_STATE_CLAIMED,
        supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
    )
    assert parent_source_abi.f3_parent_source_abi_publish_run_receipt(*receipt_args) == 1
    assert c_transition(supervisor.PARENT_SOURCE_ROOT_ACTION_VERIFY) == (
        supervisor.PARENT_SOURCE_ROOT_STATE_COMPLETED,
        supervisor.PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY,
    )
    assert c_transition(supervisor.PARENT_SOURCE_ROOT_ACTION_RUN) == (
        supervisor.PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED,
        supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
    )
    assert (
        parent_source_abi.f3_parent_source_abi_publish_terminal_receipt(*receipt_args)
        == 1
    )
    for action in (
        supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
        supervisor.PARENT_SOURCE_ROOT_ACTION_VERIFY,
    ):
        assert c_transition(action) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
        )

    acquire_args = (
        str(global_root),
        str(run_claim),
        str(verify_claim),
        RUN_SPEC_SHA256.encode("ascii"),
        ATTEMPT_KEY_SHA256.encode("ascii"),
        GLOBAL_ATTEMPT_IDENTITY_SHA256.encode("ascii"),
    )
    assert parent_source_abi.f3_parent_source_abi_acquire_status(
        *acquire_args,
        60_001,
    ) == supervisor.PARENT_SOURCE_ROOT_LEASE_BINDING_REJECTED
    assert parent_source_abi.f3_parent_source_abi_acquire_status(
        *acquire_args,
        0xFFFFFFFF,
    ) == supervisor.PARENT_SOURCE_ROOT_LEASE_BINDING_REJECTED
    assert parent_source_abi.f3_parent_source_abi_acquire_status(
        *acquire_args,
        60_000,
    ) == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert (
        parent_source_abi.f3_parent_source_abi_wrong_thread_release_rejected(
            *acquire_args
        )
        == 1
    )

    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        global_root,
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        supervisor.factor_v3_parent_source_root_policy_sha256(global_root),
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        supervisor._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
        supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
            GLOBAL_ATTEMPT_IDENTITY_SHA256,
            None,
        )
        supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
            GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
    status, python_lease = supervisor._ParentSourceRootLease.acquire(
        global_attempt_ledger_root=global_root,
        global_run_claim_path=run_claim,
        global_verify_claim_path=verify_claim,
        run_spec_sha256=RUN_SPEC_SHA256,
        attempt_key_sha256=ATTEMPT_KEY_SHA256,
        global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        timeout_milliseconds=2000,
    )
    assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert python_lease is not None
    try:
        assert supervisor.factor_v3_parent_source_lookup_root_epoch_transition(
            python_lease,
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
        ) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
        )
    finally:
        python_lease.close()
        with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
                GLOBAL_ATTEMPT_IDENTITY_SHA256,
                None,
            )
            supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
                GLOBAL_ATTEMPT_IDENTITY_SHA256
            )


def test_python_and_compiled_c_contend_on_one_machine_global_lease_and_recover(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "factor_v3_parent_source_cross_abi_harness.exe"
    _compile_source(
        source=HARNESS_SOURCE,
        output=output,
        includes=[BROKER_ROOT],
        definitions=[],
        libraries=["-lncrypt"],
    )
    global_root = tmp_path / "cross-abi-global-attempt-ledger"
    global_root.mkdir()
    run_claim, verify_claim = supervisor.factor_v3_parent_source_global_claim_paths(
        global_root,
        ATTEMPT_KEY_SHA256,
    )
    run_claim.parent.mkdir(parents=True)
    alternate_root = tmp_path / "cross-abi-alternate-ledger"
    alternate_root.mkdir()
    alternate_run, alternate_verify = (
        supervisor.factor_v3_parent_source_global_claim_paths(
            alternate_root,
            ATTEMPT_KEY_SHA256,
        )
    )
    alternate_run.parent.mkdir(parents=True)
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        global_root,
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        supervisor.factor_v3_parent_source_root_policy_sha256(global_root),
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        supervisor._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
        supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
            GLOBAL_ATTEMPT_IDENTITY_SHA256,
            None,
        )
        supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
            GLOBAL_ATTEMPT_IDENTITY_SHA256
        )

    ready_path = tmp_path / "cross-abi-holder.ready"
    release_path = tmp_path / "cross-abi-holder.release"
    holder = subprocess.Popen(
        [
            str(output),
            "--lease-hold",
            str(global_root),
            str(run_claim),
            str(verify_claim),
            str(ready_path),
            str(release_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    python_lease: supervisor._ParentSourceRootLease | None = None
    abandoned_holder: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and holder.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert ready_path.exists(), holder.communicate(timeout=1)

        status, contended_lease = supervisor._ParentSourceRootLease.acquire(
            global_attempt_ledger_root=global_root,
            global_run_claim_path=run_claim,
            global_verify_claim_path=verify_claim,
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            timeout_milliseconds=200,
        )
        assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_CONTENDED
        assert contended_lease is None

        release_path.write_bytes(b"")
        holder_stdout, holder_stderr = holder.communicate(timeout=10)
        assert holder.returncode == 0, holder_stderr
        assert holder_stdout == "DISPOSABLE_ROOT_LEASE_HELD=1\n"

        status, python_lease = supervisor._ParentSourceRootLease.acquire(
            global_attempt_ledger_root=global_root,
            global_run_claim_path=run_claim,
            global_verify_claim_path=verify_claim,
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            timeout_milliseconds=2000,
        )
        assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
        assert python_lease is not None
        assert python_lease.unchanged(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        )
        assert not python_lease.unchanged(
            run_spec_sha256="f" * 64,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        )
        assert python_lease.root_epoch_transition(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
        ) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_EMPTY,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_START_RUN,
        )
        assert python_lease.root_epoch_transition(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
        ) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_CLAIMED,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
        )
        python_lease.publish_run_receipt(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        )
        assert python_lease.root_epoch_transition(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_VERIFY,
        ) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_COMPLETED,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY,
        )
        python_lease.publish_terminal_receipt(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        )
        assert python_lease.root_epoch_transition(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
        ) == (
            supervisor.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            supervisor.PARENT_SOURCE_ROOT_TRANSITION_REJECT,
        )

        native_contender = _run(
            output,
            "--lease-try",
            str(global_root),
            str(run_claim),
            str(verify_claim),
        )
        assert native_contender.returncode == 75, native_contender.stderr
        assert native_contender.stdout == "DISPOSABLE_ROOT_LEASE_CONTENDED=1\n"
        python_lease.close()
        python_lease = None

        persistent_probe = _run(
            output,
            "--epoch-probe",
            str(global_root),
            str(run_claim),
            str(verify_claim),
            str(supervisor.PARENT_SOURCE_ROOT_ACTION_RUN),
        )
        assert persistent_probe.returncode == 0, persistent_probe.stderr
        assert persistent_probe.stdout == "DISPOSABLE_ROOT_EPOCH_OBSERVED=4:2\n"

        status, rejected = supervisor._ParentSourceRootLease.acquire(
            global_attempt_ledger_root=global_root,
            global_run_claim_path=run_claim,
            global_verify_claim_path=verify_claim,
            run_spec_sha256="f" * 64,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            timeout_milliseconds=200,
        )
        assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_BINDING_REJECTED
        assert rejected is None

        status, rejected = supervisor._ParentSourceRootLease.acquire(
            global_attempt_ledger_root=alternate_root,
            global_run_claim_path=alternate_run,
            global_verify_claim_path=alternate_verify,
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
            timeout_milliseconds=200,
        )
        assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_BINDING_REJECTED
        assert rejected is None

        abandoned_ready = tmp_path / "abandoned-holder.ready"
        abandoned_release = tmp_path / "abandoned-holder.release"
        abandoned_holder = subprocess.Popen(
            [
                str(output),
                "--lease-hold",
                str(global_root),
                str(run_claim),
                str(verify_claim),
                str(abandoned_ready),
                str(abandoned_release),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 10
        while not abandoned_ready.exists() and abandoned_holder.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert abandoned_ready.exists(), abandoned_holder.communicate(timeout=1)
        abandoned_holder.kill()
        abandoned_holder.communicate(timeout=10)
        recovered = _run(
            output,
            "--lease-try",
            str(global_root),
            str(run_claim),
            str(verify_claim),
        )
        assert recovered.returncode == 0, recovered.stderr
        assert recovered.stdout == "DISPOSABLE_ROOT_LEASE_ACQUIRED=1\n"
    finally:
        if python_lease is not None:
            python_lease.close()
        if holder.poll() is None:
            release_path.write_bytes(b"")
            try:
                holder.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate(timeout=2)
        if abandoned_holder is not None and abandoned_holder.poll() is None:
            abandoned_holder.kill()
            abandoned_holder.communicate(timeout=2)
        with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
                GLOBAL_ATTEMPT_IDENTITY_SHA256,
                None,
            )
            supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
                GLOBAL_ATTEMPT_IDENTITY_SHA256
            )


def test_python_epoch_rejects_caller_state_and_partial_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "partial-chain-global-attempt-ledger"
    global_root.mkdir()
    run_claim, verify_claim = supervisor.factor_v3_parent_source_global_claim_paths(
        global_root,
        ATTEMPT_KEY_SHA256,
    )
    run_claim.parent.mkdir(parents=True)
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        global_root,
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        supervisor.factor_v3_parent_source_root_policy_sha256(global_root),
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        supervisor._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
        supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
            GLOBAL_ATTEMPT_IDENTITY_SHA256,
            None,
        )
        supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
            GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
    status, lease = supervisor._ParentSourceRootLease.acquire(
        global_attempt_ledger_root=global_root,
        global_run_claim_path=run_claim,
        global_verify_claim_path=verify_claim,
        run_spec_sha256=RUN_SPEC_SHA256,
        attempt_key_sha256=ATTEMPT_KEY_SHA256,
        global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        timeout_milliseconds=2000,
    )
    assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert lease is not None
    try:
        with pytest.raises(TypeError):
            supervisor.factor_v3_parent_source_lookup_root_epoch_transition(
                lease,
                run_spec_sha256=RUN_SPEC_SHA256,
                attempt_key_sha256=ATTEMPT_KEY_SHA256,
                global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
                root_state=supervisor.PARENT_SOURCE_ROOT_STATE_EMPTY,
                requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
            )
        for extra_name in (
            "fifth-artifact.json",
            "epoch.db-wal",
            "epoch.db-shm",
            "epoch.partial",
            ".parent-source-epoch-staging-interrupted.tmp",
        ):
            extra_path = run_claim.parent / extra_name
            extra_path.write_bytes(b"unexpected")
            with pytest.raises(supervisor.FormalSupervisorError):
                supervisor.factor_v3_parent_source_lookup_root_epoch_transition(
                    lease,
                    run_spec_sha256=RUN_SPEC_SHA256,
                    attempt_key_sha256=ATTEMPT_KEY_SHA256,
                    global_attempt_identity_sha256=(
                        GLOBAL_ATTEMPT_IDENTITY_SHA256
                    ),
                    requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_VERIFY,
                )
            extra_path.unlink()
        terminal_path, terminal_raw = lease._expected_epoch_files()[3]
        terminal_path.write_bytes(terminal_raw)
        with pytest.raises(supervisor.FormalSupervisorError):
            supervisor.factor_v3_parent_source_lookup_root_epoch_transition(
                lease,
                run_spec_sha256=RUN_SPEC_SHA256,
                attempt_key_sha256=ATTEMPT_KEY_SHA256,
                global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
                requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
            )
    finally:
        lease.close()
        with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
                GLOBAL_ATTEMPT_IDENTITY_SHA256,
                None,
            )
            supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
                GLOBAL_ATTEMPT_IDENTITY_SHA256
            )


def test_python_restart_rejects_native_interrupted_epoch_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "factor_v3_parent_source_stage_crash_harness.exe"
    _compile_source(
        source=HARNESS_SOURCE,
        output=output,
        includes=[BROKER_ROOT],
        definitions=[],
        libraries=["-lncrypt"],
    )
    global_root = tmp_path / "native-stage-crash-ledger"
    global_root.mkdir()
    run_claim, verify_claim = supervisor.factor_v3_parent_source_global_claim_paths(
        global_root,
        ATTEMPT_KEY_SHA256,
    )
    run_claim.parent.mkdir(parents=True)
    ready_path = tmp_path / "native-stage-crash.ready"
    release_path = tmp_path / "native-stage-crash.release"
    holder = subprocess.Popen(
        [
            str(output),
            "--epoch-stage-hold",
            str(global_root),
            str(run_claim),
            str(verify_claim),
            str(ready_path),
            str(release_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and holder.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert ready_path.exists(), holder.communicate(timeout=1)
        holder.kill()
        holder.communicate(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=2)
    assert tuple(run_claim.parent.glob(".parent-source-epoch-staging-*.tmp"))

    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        global_root,
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        supervisor.factor_v3_parent_source_root_policy_sha256(global_root),
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        supervisor._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
        supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
            GLOBAL_ATTEMPT_IDENTITY_SHA256,
            None,
        )
        supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
            GLOBAL_ATTEMPT_IDENTITY_SHA256
        )
    status, lease = supervisor._ParentSourceRootLease.acquire(
        global_attempt_ledger_root=global_root,
        global_run_claim_path=run_claim,
        global_verify_claim_path=verify_claim,
        run_spec_sha256=RUN_SPEC_SHA256,
        attempt_key_sha256=ATTEMPT_KEY_SHA256,
        global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        timeout_milliseconds=2000,
    )
    assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert lease is not None
    try:
        with pytest.raises(supervisor.FormalSupervisorError):
            supervisor.factor_v3_parent_source_lookup_root_epoch_transition(
                lease,
                run_spec_sha256=RUN_SPEC_SHA256,
                attempt_key_sha256=ATTEMPT_KEY_SHA256,
                global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
                requested_action=supervisor.PARENT_SOURCE_ROOT_ACTION_RUN,
            )
    finally:
        lease.close()
        with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
                GLOBAL_ATTEMPT_IDENTITY_SHA256,
                None,
            )
            supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
                GLOBAL_ATTEMPT_IDENTITY_SHA256
            )


def test_python_mirror_requires_private_disposable_native_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "native-authority-gated-ledger"
    global_root.mkdir()
    run_claim, verify_claim = supervisor.factor_v3_parent_source_global_claim_paths(
        global_root,
        ATTEMPT_KEY_SHA256,
    )
    run_claim.parent.mkdir(parents=True)
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT",
        global_root,
    )
    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256",
        supervisor.factor_v3_parent_source_root_policy_sha256(global_root),
    )
    acquire_kwargs = {
        "global_attempt_ledger_root": global_root,
        "global_run_claim_path": run_claim,
        "global_verify_claim_path": verify_claim,
        "run_spec_sha256": RUN_SPEC_SHA256,
        "attempt_key_sha256": ATTEMPT_KEY_SHA256,
        "global_attempt_identity_sha256": GLOBAL_ATTEMPT_IDENTITY_SHA256,
        "timeout_milliseconds": 2000,
    }
    for unauthorized in ({}, "f" * 64, object()):
        monkeypatch.setattr(
            supervisor,
            "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
            unauthorized,
        )
        status, lease = supervisor._ParentSourceRootLease.acquire(**acquire_kwargs)
        assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_BINDING_REJECTED
        assert lease is None
    with pytest.raises(TypeError):
        supervisor._ParentSourceRootLease.acquire(
            **acquire_kwargs,
            native_root_policy_authority={},
        )

    monkeypatch.setattr(
        supervisor,
        "_PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY",
        supervisor._PARENT_SOURCE_DISPOSABLE_F3_BROKER_TESTING_ROOT_POLICY_AUTHORITY,
    )
    status, lease = supervisor._ParentSourceRootLease.acquire(**acquire_kwargs)
    assert status == supervisor.PARENT_SOURCE_ROOT_LEASE_ACQUIRED
    assert lease is not None
    try:
        assert all(
            b"native_root_policy_authority" not in raw
            and b"F3_BROKER_TESTING" not in raw
            for _path, raw in lease._expected_epoch_files()
        )
        release_errors: list[BaseException] = []

        def release_from_wrong_thread() -> None:
            try:
                lease.close()
            except BaseException as exc:
                release_errors.append(exc)

        worker = threading.Thread(target=release_from_wrong_thread)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(release_errors) == 1
        assert isinstance(release_errors[0], supervisor.FormalSupervisorError)
        assert lease.unchanged(
            run_spec_sha256=RUN_SPEC_SHA256,
            attempt_key_sha256=ATTEMPT_KEY_SHA256,
            global_attempt_identity_sha256=GLOBAL_ATTEMPT_IDENTITY_SHA256,
        )
    finally:
        lease.close()
        with supervisor._PARENT_SOURCE_ROOT_LEASE_STATE_LOCK:
            supervisor._PARENT_SOURCE_ROOT_BINDINGS.pop(
                GLOBAL_ATTEMPT_IDENTITY_SHA256,
                None,
            )
            supervisor._PARENT_SOURCE_ACTIVE_ROOT_LEASES.discard(
                GLOBAL_ATTEMPT_IDENTITY_SHA256
            )


def test_parent_source_root_lease_keeps_formal_false_gates_closed() -> None:
    native_source = (
        BROKER_ROOT / "factor_v3_formal_native_broker.c"
    ).read_text(encoding="utf-8")
    runner_source = (
        REPO_ROOT / "app" / "factor_v3_parent_source_authority_runner.py"
    ).read_text(encoding="utf-8")

    assert supervisor._PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT is None
    assert (
        supervisor._PARENT_SOURCE_FIXED_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256
        is None
    )
    assert supervisor._PARENT_SOURCE_REGISTERED_NATIVE_ROOT_POLICY_AUTHORITY is None
    assert (
        '#define F3_BROKER_PARENT_SOURCE_GLOBAL_ATTEMPT_LEDGER_ROOT L""'
        in native_source
    )
    assert (
        '#define F3_BROKER_PARENT_SOURCE_GLOBAL_ATTEMPT_LEDGER_ROOT_POLICY_SHA256 ""'
        in native_source
    )
    for field in (
        "formal_materialization_eligible",
        "producer_binding_verified",
        "runtime_dependency_authority_verified",
        "source_authority_complete",
    ):
        assert f'"{field}": False' in runner_source
