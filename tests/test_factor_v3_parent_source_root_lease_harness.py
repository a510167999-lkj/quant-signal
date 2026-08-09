from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

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
    "semantic/global-attempt identity": (
        "factor_v3_parent_source_derive_attempt_key_sha256",
        "factor_v3_parent_source_derive_global_attempt_identity_sha256",
    ),
    "root-wide lease": (
        "factor_v3_parent_source_acquire_root_lease",
        "factor_v3_parent_source_root_lease_unchanged",
        "factor_v3_parent_source_release_root_lease",
    ),
    "root epoch authority": (
        "factor_v3_parent_source_lookup_root_epoch_transition",
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
