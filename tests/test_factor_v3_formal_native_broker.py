from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BROKER_SOURCE = (
    REPO_ROOT
    / "native"
    / "factor_v3_formal_native_broker"
    / "factor_v3_formal_native_broker.c"
)
BROKER_INCLUDE = BROKER_SOURCE.parent


def _broker_module():
    from app import factor_v3_formal_native_broker

    return factor_v3_formal_native_broker


def _public_paths(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "authorization_path": tmp_path / "bootstrap authorization.json",
        "completion_marker_path": tmp_path / "bootstrap completion.json",
        "publication_receipt_path": tmp_path / "supervisor publication.json",
        "execution_ledger_root": tmp_path / "execution ledger",
    }
    paths["execution_ledger_root"].mkdir()
    for name, path in paths.items():
        if name != "execution_ledger_root":
            path.write_bytes(f"{name}\n".encode("ascii"))
    return paths


def _candidate(tmp_path: Path, *, action: str = "run") -> tuple[bytes, dict[str, Path]]:
    broker = _broker_module()
    paths = _public_paths(tmp_path)
    raw = broker.build_factor_v3_formal_native_broker_candidate(
        action=action,
        **paths,
    )
    return raw, paths


def _c_wide(value: Path) -> str:
    return str(value.resolve()).replace("\\", "/").replace('"', '\\"')


def _compile(
    *,
    source: Path,
    output: Path,
    extra: list[str] | None = None,
) -> None:
    completed = _compile_result(source=source, output=output, extra=extra)
    assert completed.returncode == 0, completed.stderr


def _compile_result(
    *,
    source: Path,
    output: Path,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("Win32 GCC is unavailable")
    return subprocess.run(
        [
            gcc,
            "-std=c11",
            "-DUNICODE",
            "-D_UNICODE",
            "-municode",
            "-Wall",
            "-Wextra",
            "-Werror",
            *(extra or []),
            str(source),
            "-o",
            str(output),
            "-ladvapi32",
            "-lbcrypt",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _helper_source() -> str:
    return r"""
#include <windows.h>
#include <stdio.h>

static int present(const wchar_t *name) {
    wchar_t value[2];
    SetLastError(ERROR_SUCCESS);
    DWORD size = GetEnvironmentVariableW(name, value, 2);
    return size != 0 || GetLastError() != ERROR_ENVVAR_NOT_FOUND;
}

static int current_directory_is_module_directory(void) {
    wchar_t current[32768];
    wchar_t module[32768];
    wchar_t *separator;
    DWORD current_length = GetCurrentDirectoryW(
        (DWORD)(sizeof(current) / sizeof(current[0])),
        current
    );
    DWORD module_length = GetModuleFileNameW(
        NULL,
        module,
        (DWORD)(sizeof(module) / sizeof(module[0]))
    );
    if (current_length == 0
        || current_length >= sizeof(current) / sizeof(current[0])
        || module_length == 0
        || module_length >= sizeof(module) / sizeof(module[0])) {
        return 0;
    }
    separator = wcsrchr(module, L'\\');
    if (separator == NULL) {
        return 0;
    }
    *separator = L'\0';
    return _wcsicmp(current, module) == 0;
}

static int system_root_is_os_directory(void) {
    wchar_t actual[32768];
    wchar_t observed[32768];
    UINT actual_length = GetSystemWindowsDirectoryW(
        actual,
        (UINT)(sizeof(actual) / sizeof(actual[0]))
    );
    DWORD observed_length = GetEnvironmentVariableW(
        L"SYSTEMROOT",
        observed,
        (DWORD)(sizeof(observed) / sizeof(observed[0]))
    );
    return actual_length != 0
        && actual_length < sizeof(actual) / sizeof(actual[0])
        && observed_length != 0
        && observed_length < sizeof(observed) / sizeof(observed[0])
        && _wcsicmp(actual, observed) == 0;
}

int wmain(int argc, wchar_t **argv) {
    BOOL in_job = FALSE;
    FILE *output;
    wchar_t protocol[128] = L"";
    int hold_for_job_kill;
    if (argc != 2 || !IsProcessInJob(GetCurrentProcess(), NULL, &in_job)) {
        return 40;
    }
    hold_for_job_kill = wcsstr(argv[1], L"job-kill-child") != NULL;
    if (_wfopen_s(&output, argv[1], L"wb") != 0 || output == NULL) {
        return 41;
    }
    GetEnvironmentVariableW(
        L"FACTOR_V3_FORMAL_NATIVE_BROKER_PROTOCOL",
        protocol,
        (DWORD)(sizeof(protocol) / sizeof(protocol[0]))
    );
    fprintf(output, "IN_JOB=%d\n", in_job ? 1 : 0);
    fprintf(output, "HAS_JIAOCH_TOKEN=%d\n", present(L"JIAOCH_TOKEN"));
    fprintf(output, "HAS_PATH=%d\n", present(L"PATH"));
    fprintf(output, "HAS_PYTHONPATH=%d\n", present(L"PYTHONPATH"));
    fprintf(output, "HAS_TEMP=%d\n", present(L"TEMP"));
    fprintf(output, "HAS_TMP=%d\n", present(L"TMP"));
    fprintf(
        output,
        "CWD_IS_RUNTIME_DIR=%d\n",
        current_directory_is_module_directory()
    );
    fprintf(output, "SYSTEMROOT_IS_OS=%d\n", system_root_is_os_directory());
    fprintf(
        output,
        "PROTOCOL_OK=%d\n",
        wcscmp(protocol, L"factor-v3-formal-native-broker-child/v1") == 0
    );
    if (hold_for_job_kill) {
        fprintf(output, "PID=%lu\n", GetCurrentProcessId());
    }
    fclose(output);
    if (hold_for_job_kill) {
        Sleep(30000);
    }
    return 0;
}
"""


def _compile_fixture_broker(tmp_path: Path) -> tuple[Path, Path]:
    helper_source = tmp_path / "broker_child.c"
    helper_source.write_text(_helper_source(), encoding="utf-8")
    helper = tmp_path / "broker_child.exe"
    _compile(source=helper_source, output=helper)

    reviewed_source = tmp_path / "reviewed-source.txt"
    reviewed_source.write_bytes(b"reviewed native boundary fixture\n")
    signing_key = tmp_path / "dummy-signing-key.slot"
    signing_key.write_bytes(b"not-a-real-private-key\n")
    credential = tmp_path / "dummy-credential.slot"
    credential.write_bytes(b"not-a-real-token\n")
    manifest = tmp_path / "fixture_manifest.h"
    manifest.write_text(
        "\n".join(
            (
                '#define F3_BROKER_RUNTIME_PATH L"' + _c_wide(helper) + '"',
                '#define F3_BROKER_RUNTIME_SHA256 L"'
                + hashlib.sha256(helper.read_bytes()).hexdigest()
                + '"',
                '#define F3_BROKER_SOURCE_PATH L"'
                + _c_wide(reviewed_source)
                + '"',
                '#define F3_BROKER_SOURCE_SHA256 L"'
                + hashlib.sha256(reviewed_source.read_bytes()).hexdigest()
                + '"',
                '#define F3_BROKER_SIGNING_KEY_SLOT_PATH L"'
                + _c_wide(signing_key)
                + '"',
                '#define F3_BROKER_CREDENTIAL_SLOT_PATH L"'
                + _c_wide(credential)
                + '"',
                "#define F3_BROKER_TESTING 1",
                "#define F3_BROKER_DISPOSABLE_TEST_MANIFEST 1",
                "",
            )
        ),
        encoding="utf-8",
    )
    broker = tmp_path / "factor_v3_formal_native_broker.exe"
    _compile(
        source=BROKER_SOURCE,
        output=broker,
        extra=[
            '-DF3_BROKER_MANIFEST_HEADER="fixture_manifest.h"',
            f"-I{tmp_path}",
            f"-I{BROKER_INCLUDE}",
        ],
    )
    return broker, helper


def _publish_candidate(tmp_path: Path, raw: bytes) -> Path:
    broker = _broker_module()
    result = broker.publish_factor_v3_formal_native_broker_candidate(
        candidate_output_root=tmp_path / "candidate output",
        candidate=raw,
    )
    path = Path(result["candidate_path"])
    assert result == {
        "candidate_path": str(path),
        "candidate_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "published",
    }
    return path


def _write_raw_candidate(root: Path, raw: bytes) -> Path:
    digest = hashlib.sha256(raw).hexdigest()
    path = (
        root
        / "candidates"
        / "sha256"
        / digest[:2]
        / f"{digest}.candidate"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return path


def test_candidate_api_has_no_secret_path_parameters_and_is_deterministic(
    tmp_path: Path,
) -> None:
    broker = _broker_module()
    signature = inspect.signature(
        broker.build_factor_v3_formal_native_broker_candidate
    )
    assert "credential_path" not in signature.parameters
    assert "private_key_path" not in signature.parameters

    raw, paths = _candidate(tmp_path)
    assert raw == broker.build_factor_v3_formal_native_broker_candidate(
        action="run",
        **paths,
    )
    text = raw.decode("utf-8")
    assert text.startswith(
        "schema=factor-v3-formal-native-broker-candidate/v1\n"
        "action=run\n"
    )
    assert "signing_key_slot_id=factor-v3-execution-authorization\n" in text
    assert "credential_slot_id=points-primary\n" in text
    assert "credential_path" not in text
    assert "private_key_path" not in text
    assert "\r" not in text


def test_nonsecret_actions_bind_no_credential_slot(tmp_path: Path) -> None:
    raw, _paths = _candidate(tmp_path, action="verify")
    assert b"credential_slot_id=none\n" in raw


def test_candidate_builder_rejects_invalid_action_paths_and_resume_shape(
    tmp_path: Path,
) -> None:
    broker = _broker_module()
    paths = _public_paths(tmp_path)
    with pytest.raises(Exception, match="action"):
        broker.build_factor_v3_formal_native_broker_candidate(
            action="trade",
            **paths,
        )
    with pytest.raises(Exception, match="path"):
        broker.build_factor_v3_formal_native_broker_candidate(
            action="verify",
            **{**paths, "authorization_path": Path("relative.json")},
        )
    with pytest.raises(Exception, match="path"):
        broker.build_factor_v3_formal_native_broker_candidate(
            action="verify",
            **{**paths, "authorization_path": str(tmp_path / "bad\npath")},
        )
    with pytest.raises(Exception, match="resume"):
        broker.build_factor_v3_formal_native_broker_candidate(
            action="resume",
            **paths,
        )
    with pytest.raises(Exception, match="resume"):
        broker.build_factor_v3_formal_native_broker_candidate(
            action="run",
            resume_authorization_path=tmp_path / "resume.json",
            **paths,
        )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"\\server\share\authorization.json",
        r"\\?\C:\frozen\authorization.json",
        r"C:\frozen\authorization.json:stream",
        "C:\\frozen\\control\x01authorization.json",
        r"C:\frozen\trailing.\authorization.json",
        "C:\\frozen\\trailing \\authorization.json",
        r"C:\frozen\..\authorization.json",
        r"C:\PROGRA~1\authorization.json",
        r"C:\frozen\NUL.txt",
    ],
)
def test_candidate_builder_rejects_windows_path_aliases(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    paths = _public_paths(tmp_path)
    with pytest.raises(Exception, match="path"):
        _broker_module().build_factor_v3_formal_native_broker_candidate(
            action="verify",
            **{**paths, "authorization_path": unsafe_path},
        )


def test_resume_candidate_binds_only_public_resume_paths(tmp_path: Path) -> None:
    broker = _broker_module()
    paths = _public_paths(tmp_path)
    resume_authorization = tmp_path / "resume authorization.json"
    resume_status = tmp_path / "resume status.json"
    raw = broker.build_factor_v3_formal_native_broker_candidate(
        action="resume",
        resume_authorization_path=resume_authorization,
        resume_status_path=resume_status,
        **paths,
    )
    text = raw.decode("utf-8")
    assert f"resume_authorization_path={resume_authorization}\n" in text
    assert f"resume_status_path={resume_status}\n" in text
    assert "credential_slot_id=points-primary\n" in text


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: b"",
        lambda raw: raw.replace(b"schema=", b"wrong=", 1),
        lambda raw: raw.replace(b"\n", b"\r\n", 1),
        lambda raw: raw[:-1],
        lambda raw: raw.replace(b"credential_slot_id=points-primary", b"credential_slot_id=none"),
        lambda raw: raw.replace(
            b"runtime_manifest_schema=factor-v3-formal-native-broker-runtime-manifest/v1",
            b"runtime_manifest_schema=wrong",
        ),
        lambda raw: b"\xff" + raw[1:],
    ],
)
def test_candidate_parser_rejects_noncanonical_or_drifted_bytes(
    tmp_path: Path,
    mutator,
) -> None:
    broker = _broker_module()
    raw, _paths = _candidate(tmp_path)
    with pytest.raises(Exception, match="candidate"):
        broker.publish_factor_v3_formal_native_broker_candidate(
            candidate_output_root=tmp_path / "candidate output",
            candidate=mutator(raw),
        )


def test_candidate_publication_is_content_addressed_and_immutable(
    tmp_path: Path,
) -> None:
    raw, _paths = _candidate(tmp_path)
    path = _publish_candidate(tmp_path, raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert path == (
        tmp_path
        / "candidate output"
        / "candidates"
        / "sha256"
        / digest[:2]
        / f"{digest}.candidate"
    )
    assert path.read_bytes() == raw
    assert _publish_candidate(tmp_path, raw) == path
    with pytest.raises(Exception, match="candidate"):
        _broker_module().publish_factor_v3_formal_native_broker_candidate(
            candidate_output_root=tmp_path / "candidate output",
            candidate=raw + b"tampered\n",
            expected_candidate_path=path,
        )


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_compiles_and_launches_with_sanitized_environment_and_job(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_path = _publish_candidate(tmp_path, raw)
    output = tmp_path / "child-environment.txt"
    environment = dict(os.environ)
    environment.update(
        {
            "JIAOCH_TOKEN": "must-not-cross-native-boundary",
            "PYTHONPATH": str(tmp_path / "attacker"),
            "FACTOR_V3_ATTACKER_MARKER": "must-not-cross-native-boundary",
            "SYSTEMROOT": str(tmp_path / "attacker-systemroot"),
            "WINDIR": str(tmp_path / "attacker-windir"),
            "TEMP": str(tmp_path / "attacker-temp"),
            "TMP": str(tmp_path / "attacker-tmp"),
        }
    )
    completed = subprocess.run(
        [str(native), "--test-launch", str(candidate_path), str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert output.read_text(encoding="ascii") == (
        "IN_JOB=1\n"
        "HAS_JIAOCH_TOKEN=0\n"
        "HAS_PATH=0\n"
        "HAS_PYTHONPATH=0\n"
        "HAS_TEMP=0\n"
        "HAS_TMP=0\n"
        "CWD_IS_RUNTIME_DIR=1\n"
        "SYSTEMROOT_IS_OS=1\n"
        "PROTOCOL_OK=1\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_job_kills_child_when_last_job_handle_closes(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_path = _publish_candidate(tmp_path, raw)
    output = tmp_path / "job-kill-child.txt"
    completed = subprocess.run(
        [str(native), "--test-job-kill", str(candidate_path), str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    child_pid = int(
        next(
            line.removeprefix("PID=")
            for line in output.read_text(encoding="ascii").splitlines()
            if line.startswith("PID=")
        )
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue) {{ exit 1 }}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode == 0:
            break
        time.sleep(0.05)
    assert probe.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_assigns_job_atomically_before_child_resume(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_path = _publish_candidate(tmp_path, raw)
    output = tmp_path / "pre-resume-child-must-not-run.txt"
    completed = subprocess.run(
        [
            str(native),
            "--test-job-pre-resume-kill",
            str(candidate_path),
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert not output.exists()
    source = BROKER_SOURCE.read_text(encoding="utf-8")
    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST" in source
    assert "AssignProcessToJobObject" not in source


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_holds_candidate_ancestor_identity_through_child_lifetime(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_root = tmp_path / "held-candidate-root"
    candidate_path = _publish_candidate(candidate_root, raw)
    output = tmp_path / "job-kill-child-held-boundary.txt"
    process = subprocess.Popen(
        [str(native), "--test-launch", str(candidate_path), str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert output.exists()
        with pytest.raises(OSError):
            candidate_root.rename(tmp_path / "replaced-candidate-root")
    finally:
        process.terminate()
        process.wait(timeout=10)
    assert not (tmp_path / "replaced-candidate-root").exists()


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_rejects_hardlinked_candidate(tmp_path: Path) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_path = _publish_candidate(tmp_path, raw)
    hardlink = tmp_path / "candidate-hardlink.candidate"
    os.link(candidate_path, hardlink)
    completed = subprocess.run(
        [str(native), "--test-launch", str(candidate_path), str(tmp_path / "out")],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_rejects_relative_public_path_in_untrusted_candidate(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, paths = _candidate(tmp_path)
    drifted = raw.replace(
        f"authorization_path={paths['authorization_path']}\n".encode("utf-8"),
        b"authorization_path=relative.json\n",
    )
    assert drifted != raw
    candidate_path = _write_raw_candidate(tmp_path / "untrusted", drifted)
    completed = subprocess.run(
        [str(native), "--validate-candidate", str(candidate_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"C:\frozen\authorization.json:stream",
        r"C:\frozen\..\authorization.json",
        r"C:\PROGRA~1\authorization.json",
        r"C:\frozen\NUL.txt",
    ],
)
def test_native_candidate_parser_rejects_windows_path_aliases(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, paths = _candidate(tmp_path, action="verify")
    drifted = raw.replace(
        f"authorization_path={paths['authorization_path']}\n".encode("utf-8"),
        f"authorization_path={unsafe_path}\n".encode("utf-8"),
    )
    assert drifted != raw
    candidate_path = _write_raw_candidate(tmp_path / "untrusted", drifted)
    completed = subprocess.run(
        [str(native), "--validate-candidate", str(candidate_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_rejects_hardlinked_fixed_source_and_credential_slots(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    candidate_path = _publish_candidate(tmp_path, raw)
    source_hardlink = tmp_path / "reviewed-source-hardlink.txt"
    os.link(tmp_path / "reviewed-source.txt", source_hardlink)
    rejected_source = subprocess.run(
        [str(native), "--test-launch", str(candidate_path), str(tmp_path / "source-out")],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected_source.returncode != 0
    source_hardlink.unlink()

    credential_hardlink = tmp_path / "credential-hardlink.slot"
    os.link(tmp_path / "dummy-credential.slot", credential_hardlink)
    rejected_credential = subprocess.run(
        [
            str(native),
            "--test-launch",
            str(candidate_path),
            str(tmp_path / "credential-out"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected_credential.returncode != 0
    assert not (tmp_path / "credential-out").exists()


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_broker_rejects_reparse_directory_in_candidate_chain(
    tmp_path: Path,
) -> None:
    native, _helper = _compile_fixture_broker(tmp_path)
    raw, _paths = _candidate(tmp_path)
    real_root = tmp_path / "real-candidate-root"
    candidate_path = _publish_candidate(real_root, raw)
    junction = tmp_path / "candidate-junction"
    completed_link = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed_link.returncode != 0:
        pytest.fail(f"failed to create test junction: {completed_link.stderr}")
    through_junction = junction / candidate_path.relative_to(real_root)
    output = tmp_path / "junction-out"
    completed = subprocess.run(
        [str(native), "--test-launch", str(through_junction), str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_native_acl_probe_distinguishes_mutable_user_tree_from_system_protected_tree(
    tmp_path: Path,
) -> None:
    native = tmp_path / "factor_v3_formal_native_broker-acl-probe.exe"
    _compile(
        source=BROKER_SOURCE,
        output=native,
        extra=[
            "-DF3_BROKER_TESTING=1",
            "-DF3_BROKER_DISPOSABLE_TEST_MANIFEST=1",
            f"-I{BROKER_INCLUDE}",
        ],
    )

    mutable = subprocess.run(
        [str(native), "--test-current-token-readonly-root", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert mutable.returncode == 25
    assert "mutable" in mutable.stderr.lower()

    protected_root = Path(os.environ["SystemRoot"]) / "System32"
    protected = subprocess.run(
        [str(native), "--test-current-token-readonly-root", str(protected_root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert protected.returncode == 0, protected.stderr
    assert protected.stdout == ""
    assert protected.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
@pytest.mark.parametrize(
    ("disposable_marker", "production_ready"),
    [(0, 0), (1, 1)],
)
def test_native_build_rejects_unsealed_test_production_combinations(
    tmp_path: Path,
    disposable_marker: int,
    production_ready: int,
) -> None:
    manifest = tmp_path / "unsafe_test_manifest.h"
    manifest.write_text(
        "\n".join(
            (
                '#define F3_BROKER_RUNTIME_PATH L""',
                '#define F3_BROKER_RUNTIME_SHA256 L""',
                '#define F3_BROKER_SOURCE_PATH L""',
                '#define F3_BROKER_SOURCE_SHA256 L""',
                '#define F3_BROKER_SIGNING_KEY_SLOT_PATH L""',
                '#define F3_BROKER_CREDENTIAL_SLOT_PATH L""',
                "#define F3_BROKER_TESTING 1",
                (
                    "#define F3_BROKER_DISPOSABLE_TEST_MANIFEST "
                    f"{disposable_marker}"
                ),
                (
                    "#define F3_BROKER_PRODUCTION_HANDOFF_READY "
                    f"{production_ready}"
                ),
                "",
            )
        ),
        encoding="utf-8",
    )
    completed = _compile_result(
        source=BROKER_SOURCE,
        output=tmp_path / "unsafe-test-broker.exe",
        extra=[
            '-DF3_BROKER_MANIFEST_HEADER="unsafe_test_manifest.h"',
            f"-I{tmp_path}",
            f"-I{BROKER_INCLUDE}",
        ],
    )
    assert completed.returncode != 0
    assert "F3_BROKER_TESTING" in completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="native broker is Windows-only")
def test_default_binary_is_compileable_but_production_launch_fails_closed(
    tmp_path: Path,
) -> None:
    native = tmp_path / "factor_v3_formal_native_broker-unprovisioned.exe"
    _compile(
        source=BROKER_SOURCE,
        output=native,
        extra=[f"-I{BROKER_INCLUDE}"],
    )
    completed = subprocess.run(
        [str(native), "--launch", str(tmp_path / "missing.candidate")],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "unprovisioned" in completed.stderr.lower()
    test_mode = subprocess.run(
        [
            str(native),
            "--test-launch",
            str(tmp_path / "missing.candidate"),
            str(tmp_path / "missing.out"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert test_mode.returncode != 0
    assert "invocation rejected" in test_mode.stderr.lower()
