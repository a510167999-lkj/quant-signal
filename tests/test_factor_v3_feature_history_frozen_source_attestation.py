from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app import factor_v3_daily_basic_733_exact_set_authority as daily_authority
from app import factor_v3_feature_history_frozen_source_attestation as frozen
from app import jiaoch_points_raw_authority as raw_authority
from scripts import build_factor_v3_daily_basic_formal_run_spec as formal_spec


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _tree_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative_path = path.relative_to(root).as_posix()
        entry = {
            "ctime_ns": metadata.st_ctime_ns,
            "device": metadata.st_dev,
            "file_attributes": int(getattr(metadata, "st_file_attributes", 0)),
            "inode": metadata.st_ino,
            "links": metadata.st_nlink,
            "mode": metadata.st_mode,
            "mtime_ns": metadata.st_mtime_ns,
            "size": metadata.st_size,
            "type": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            entry["sha256"] = _sha(path.read_bytes())
        snapshot[relative_path] = entry
    return snapshot


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(frozen.GIT_EXECUTABLE), "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=frozen._git_environment(),
        stdin=subprocess.DEVNULL,
    )


def _install_real_git_frozen_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    source_root = (tmp_path / "git-frozen-source").resolve()
    source_root.mkdir()
    for index, relative_path in enumerate(frozen.FROZEN_PRODUCER_RELATIVE_PATHS):
        path = source_root / Path(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"committed-source-{index}\r\n".encode())
    _git(source_root, "init", "--quiet")
    _git(source_root, "config", "core.autocrlf", "true")
    _git(source_root, "-c", "core.autocrlf=true", "add", "--all")
    _git(
        source_root,
        "-c",
        "user.name=Frozen Test",
        "-c",
        "user.email=frozen-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "frozen source",
    )
    commit = _git(source_root, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    monkeypatch.setattr(frozen, "FROZEN_SOURCE_ROOT", source_root)
    monkeypatch.setattr(frozen, "FROZEN_SOURCE_COMMIT", commit)
    return source_root, commit


def _install_frozen_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    source_root = (tmp_path / "frozen-source").resolve()
    app_root = source_root / "app"
    app_root.mkdir(parents=True)
    for index, relative_path in enumerate(frozen.FROZEN_PRODUCER_RELATIVE_PATHS):
        path = source_root / Path(*relative_path.split("/"))
        path.write_bytes(f"source-{index}\r\n".encode())
    committed_sources = {
        relative_path: (source_root / Path(*relative_path.split("/")))
        .read_bytes()
        .replace(b"\r\n", b"\n")
        for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS
    }
    commit = "b8057962f7a9754848994a6cfda9c9bf85e3db89"
    monkeypatch.setattr(frozen, "FROZEN_SOURCE_ROOT", source_root)
    monkeypatch.setattr(frozen, "FROZEN_SOURCE_COMMIT", commit)
    monkeypatch.setattr(
        frozen,
        "_git_identity",
        lambda _root: {
            "commit": commit,
            "root": str(source_root),
            "status_clean": True,
        },
    )
    monkeypatch.setattr(frozen, "_validated_frozen_git_index", lambda _root: None)
    monkeypatch.setattr(
        frozen,
        "_git_blob_bytes",
        lambda _root, _commit, relative_path: committed_sources[relative_path],
    )
    return source_root, commit


def _safe_verifier_result(
    *,
    source_root: Path,
    spec_path: Path,
    run_root: Path,
) -> dict[str, Any]:
    producer = {
        "schema_version": "factor-v3-feature-history-producer-binding/v2",
    }
    producer["root_sha256"] = _canonical_sha(producer)
    feature = {
        "authority_manifest_relative_path": (
            f"feature_history_collection_manifest_candidates/sha256/aa/{'a' * 64}.json"
        ),
        "authority_manifest_sha256": "a" * 64,
        "feature_run_root": str(run_root),
        "feature_run_spec_file_sha256": _sha(spec_path.read_bytes()),
        "feature_run_spec_path": str(spec_path),
        "feature_run_spec_sha256": "b" * 64,
        "pit_store_database_sha256": "c" * 64,
        "publication_capability_sha256": "d" * 64,
        "publication_issuance_relative_path": (
            f"feature_history_collection_publication_receipts/sha256/ee/{'e' * 64}.json"
        ),
        "publication_issuance_sha256": "e" * 64,
        "receipt_sha256": "f" * 64,
        "session_count": 250,
        "sessions_sha256": "1" * 64,
        "snapshot_index_sha256": "2" * 64,
        "source_authority_root_sha256": "3" * 64,
    }
    return {
        "feature_history": feature,
        "producer_binding": {
            **producer,
        },
        "producer_relative_paths": list(frozen.FROZEN_PRODUCER_RELATIVE_PATHS),
    }


def _publish_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_root, commit = _install_frozen_source(tmp_path, monkeypatch)
    spec_path = (tmp_path / "feature-spec.json").resolve()
    spec_path.write_bytes(
        json.dumps(
            {"run_spec_sha256": "b" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    run_root = (tmp_path / "feature-run").resolve()
    run_root.mkdir()
    (run_root / ".factor-v3-feature-history-runner.lock").write_bytes(b"\0")
    monkeypatch.setattr(frozen, "FROZEN_FEATURE_RUN_SPEC_PATH", spec_path)
    monkeypatch.setattr(
        frozen,
        "FROZEN_FEATURE_RUN_SPEC_FILE_SHA256",
        _sha(spec_path.read_bytes()),
    )
    monkeypatch.setattr(
        frozen,
        "FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256",
        "b" * 64,
    )
    monkeypatch.setattr(frozen, "FROZEN_FEATURE_RUN_ROOT", run_root)
    safe_result = _safe_verifier_result(
        source_root=source_root,
        spec_path=spec_path,
        run_root=run_root,
    )
    monkeypatch.setattr(
        frozen,
        "_run_frozen_verifier",
        lambda **_kwargs: json.loads(json.dumps(safe_result)),
    )
    monkeypatch.setattr(
        frozen,
        "_validated_attested_authority_binding",
        lambda **_kwargs: {"binding_sha256": "4" * 64},
    )
    output_root = (tmp_path / "attestations").resolve()
    output_root.mkdir()
    publication = frozen.publish_factor_v3_feature_history_frozen_source_attestation(
        frozen_source_root=source_root,
        expected_frozen_source_commit=commit,
        feature_history_run_spec_path=spec_path,
        feature_history_run_root=run_root,
        output_root=output_root,
    )
    arguments = {
        "attestation_path": (
            output_root / Path(*publication["attestation_relative_path"].split("/"))
        ),
        "expected_attestation_sha256": publication["attestation_sha256"],
        "frozen_source_root": source_root,
        "expected_frozen_source_commit": commit,
        "feature_history_run_spec_path": spec_path,
        "feature_history_run_root": run_root,
    }
    return arguments, safe_result


def test_attestation_is_capability_free_content_addressed_and_replays_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: json.loads(json.dumps(safe_result["feature_history"])),
    )

    verified = frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)

    raw = Path(arguments["attestation_path"]).read_bytes()
    attestation = json.loads(raw)
    assert verified["verified"] is True
    assert verified["session_count"] == 250
    assert verified["authority_binding"] == {"binding_sha256": "4" * 64}
    assert _sha(raw) == arguments["expected_attestation_sha256"]
    assert Path(arguments["attestation_path"]).name == f"{_sha(raw)}.json"
    assert (
        attestation["frozen_source"]["checkout_policy"]
        == frozen.FROZEN_SOURCE_CHECKOUT_POLICY
    )
    assert "publication_capability" not in attestation
    assert b'"publication_capability":' not in raw
    assert "token" not in json.dumps(attestation).lower()


def test_public_replay_holds_existing_shared_run_lock_across_the_entire_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    run_root = Path(arguments["feature_history_run_root"])
    lock_path = run_root / ".factor-v3-feature-history-runner.lock"
    feature = json.loads(json.dumps(safe_result["feature_history"]))
    context = {
        "attestor_producer": {"root_sha256": "5" * 64},
        "authority_binding": {"binding_sha256": "4" * 64},
        "feature_history": feature,
        "frozen_source_root": str(arguments["frozen_source_root"]),
        "physical_binding": {"producer_binding_root_sha256": "6" * 64},
        "producer_binding": safe_result["producer_binding"],
        "run_root": str(run_root),
        "spec_path": str(arguments["feature_history_run_spec_path"]),
    }
    events: list[str] = []

    @contextmanager
    def tracked_lock(path: Path):
        assert path == lock_path
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def replay_context(**_kwargs: Any) -> dict[str, Any]:
        assert events == ["lock-enter"] or events == [
            "lock-enter",
            "context",
            "replay",
        ]
        events.append("context")
        return json.loads(json.dumps(context))

    def replay_feature(**_kwargs: Any) -> dict[str, Any]:
        assert events == ["lock-enter", "context"]
        events.append("replay")
        return json.loads(json.dumps(feature))

    monkeypatch.setattr(
        frozen,
        "_existing_read_only_run_lock",
        tracked_lock,
        raising=False,
    )
    monkeypatch.setattr(
        frozen,
        "_validated_attested_replay_context",
        replay_context,
    )
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        replay_feature,
    )

    verified = frozen.verify_factor_v3_feature_history_frozen_source_attestation(
        **arguments
    )

    assert verified["verified"] is True
    assert events == ["lock-enter", "context", "replay", "context", "lock-exit"]


@pytest.mark.parametrize(
    "forbidden",
    (
        "private_key",
        "private-key",
        "signing_private_key",
        "rsa_private_key_pem",
        "RSA Private Key PEM",
    ),
)
def test_attestation_rejects_private_key_shape_variants(
    forbidden: str,
) -> None:
    with pytest.raises(ValueError, match="credential"):
        frozen._reject_plaintext_credentials({forbidden: "not-a-real-secret"})


def test_attestation_rejects_missing_tampered_and_wrong_expected_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: safe_result["feature_history"],
    )
    path = Path(arguments["attestation_path"])
    raw = path.read_bytes()
    path.unlink()
    with pytest.raises(ValueError, match="attestation"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="attestation"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)
    path.write_bytes(raw)
    arguments["expected_attestation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="attestation"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)


def test_attestation_rejects_frozen_source_line_ending_or_git_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: safe_result["feature_history"],
    )
    source_file = Path(arguments["frozen_source_root"]) / Path(
        *frozen.FROZEN_PRODUCER_RELATIVE_PATHS[1].split("/")
    )
    source_file.write_bytes(source_file.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="source"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)

    source_file.write_bytes(source_file.read_bytes().replace(b"\r\n", b"\n"))
    monkeypatch.setattr(
        frozen,
        "_git_identity",
        lambda _root: {
            "commit": "0" * 40,
            "root": str(arguments["frozen_source_root"]),
            "status_clean": True,
        },
    )
    with pytest.raises(ValueError, match="source"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)


def test_attestation_rejects_manifest_replacement_and_current_consumer_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    replaced = json.loads(json.dumps(safe_result["feature_history"]))
    replaced["authority_manifest_sha256"] = "9" * 64
    replaced["authority_manifest_relative_path"] = (
        f"feature_history_collection_manifest_candidates/sha256/99/{'9' * 64}.json"
    )
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: replaced,
    )
    with pytest.raises(ValueError, match="replay"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)

    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: safe_result["feature_history"],
    )
    original = frozen._attestor_producer_binding
    monkeypatch.setattr(
        frozen,
        "_attestor_producer_binding",
        lambda: {**original(), "root_sha256": "8" * 64},
    )
    with pytest.raises(ValueError, match="attestor"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(**arguments)


def test_attested_replay_surface_never_accepts_binding_or_capability_from_caller() -> None:
    assert (
        "expected_producer_binding"
        not in inspect.signature(
            history_authority.verify_factor_v3_feature_history_collection_authority
        ).parameters
    )
    assert (
        "expected_producer_binding"
        not in inspect.signature(
            history_authority._verify_factor_v3_feature_history_collection_authority
        ).parameters
    )
    private_parameters = inspect.signature(
        history_authority._verify_feature_history_with_attested_producer_binding
    ).parameters
    assert "expected_producer_binding" not in private_parameters
    assert "attestation_capability" not in private_parameters


def test_frozen_git_identity_uses_pinned_executable_and_scrubbed_environment() -> None:
    assert frozen.GIT_EXECUTABLE.is_absolute()
    assert frozen.GIT_EXECUTABLE_SHA256 == (
        "c39b1b4f7a57935bbeadf246dc2466316619453a6a9da77c4a9c6bd6d8fb21d3"
    )
    environment = frozen._git_environment()
    assert environment["GIT_CONFIG_GLOBAL"] == frozen.os.devnull
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert all(
        not key.upper().startswith("GIT_")
        for key in environment
        if key
        not in {
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_NO_REPLACE_OBJECTS",
            "GIT_OPTIONAL_LOCKS",
            "GIT_TERMINAL_PROMPT",
        }
    )


def test_frozen_subprocess_contract_binds_python_flags_pycache_and_physical_files() -> None:
    assert frozen.PYTHON_EXECUTABLE.is_absolute()
    assert frozen.PYTHON_EXECUTABLE_SHA256 == (
        "5fec912cd3c47c125754cfbcb9b21ce0b415f860cfa8e2a3b98ceb9cd73bd30f"
    )
    assert "sys.flags.isolated" in frozen._FROZEN_VERIFIER_CODE
    assert "sys.dont_write_bytecode" in frozen._FROZEN_VERIFIER_CODE
    assert "sys.pycache_prefix" in frozen._FROZEN_VERIFIER_CODE
    assert "physical_sha256" in frozen._FROZEN_VERIFIER_CODE
    source = inspect.getsource(frozen._run_frozen_verifier)
    assert "timeout=" in source
    assert "_MAX_VERIFIER_OUTPUT_BYTES" in source


def test_frozen_physical_binding_includes_runner_and_all_authority_dependencies() -> None:
    relative_paths = frozen.FROZEN_PRODUCER_RELATIVE_PATHS

    assert len(relative_paths) == len(set(relative_paths))
    assert "app/factor_v3_feature_history_runner.py" in relative_paths
    required_initializers = {
        "/".join((*parts[:depth], "__init__.py"))
        for relative_path in relative_paths
        for parts in (relative_path.split("/"),)
        for depth in range(1, len(parts))
        if (
            Path(__file__).resolve().parents[1]
            / Path(*"/".join((*parts[:depth], "__init__.py")).split("/"))
        ).is_file()
    }
    assert required_initializers <= set(relative_paths)
    assert "app/__init__.py" in relative_paths
    assert "app/__init__.py" in frozen._ATTESTOR_RELATIVE_PATHS


def test_frozen_producer_manifest_is_a_subset_of_the_pinned_b805_git_tree() -> None:
    tree_entries = {}
    for line in frozen._git_output(
        frozen.FROZEN_SOURCE_ROOT,
        "ls-tree",
        "-r",
        frozen.FROZEN_SOURCE_COMMIT,
    ).splitlines():
        descriptor, relative_path = line.split("\t", 1)
        tree_entries[relative_path] = descriptor.split()[2]
    index_entries = {}
    for line in frozen._git_output(
        frozen.FROZEN_SOURCE_ROOT,
        "ls-files",
        "--stage",
    ).splitlines():
        descriptor, relative_path = line.split("\t", 1)
        index_entries[relative_path] = descriptor.split()[1]

    assert set(frozen.FROZEN_PRODUCER_RELATIVE_PATHS) <= set(tree_entries)
    for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS:
        source_path = frozen.FROZEN_SOURCE_ROOT / Path(*relative_path.split("/"))
        assert index_entries[relative_path] == tree_entries[relative_path]
        assert source_path.stat().st_nlink == 1


@pytest.mark.parametrize("index_flag", ("--assume-unchanged", "--skip-worktree"))
def test_frozen_root_rejects_git_index_visibility_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    index_flag: str,
) -> None:
    source_root, commit = _install_real_git_frozen_source(tmp_path, monkeypatch)
    relative_path = frozen.FROZEN_PRODUCER_RELATIVE_PATHS[0]
    _git(source_root, "update-index", index_flag, "--", relative_path)
    assert frozen._git_identity(source_root)["status_clean"] is True

    with pytest.raises(ValueError, match="identity|index"):
        frozen._validated_frozen_root(source_root, commit)


def test_frozen_git_identity_ignores_local_replacement_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, original_commit = _install_real_git_frozen_source(
        tmp_path,
        monkeypatch,
    )
    target_relative = frozen.FROZEN_PRODUCER_RELATIVE_PATHS[0]
    target = source_root / Path(*target_relative.split("/"))
    original_blob = frozen._git_blob_bytes(
        source_root,
        original_commit,
        target_relative,
    )
    target.write_bytes(b"replacement-source\r\n")
    _git(source_root, "-c", "core.autocrlf=true", "add", "--all")
    _git(
        source_root,
        "-c",
        "user.name=Frozen Test",
        "-c",
        "user.email=frozen-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "replacement source",
    )
    replacement_commit = (
        _git(source_root, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    )
    hostile_environment = frozen._git_environment()
    hostile_environment.pop("GIT_NO_REPLACE_OBJECTS", None)

    def hostile_git(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(frozen.GIT_EXECUTABLE), "-C", str(source_root), *args],
            check=True,
            capture_output=True,
            env=hostile_environment,
            stdin=subprocess.DEVNULL,
        )

    hostile_git("replace", original_commit, replacement_commit)
    hostile_git("reset", "--hard", original_commit)
    assert hostile_git(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout == b""
    assert (
        hostile_git(
            "cat-file",
            "blob",
            f"{original_commit}:{target_relative}",
        ).stdout
        == b"replacement-source\n"
    )

    assert (
        frozen._git_blob_bytes(
            source_root,
            original_commit,
            target_relative,
        )
        == original_blob
    )
    with pytest.raises(ValueError, match="identity|source"):
        frozen._validated_frozen_root(source_root, original_commit)


def test_locked_physical_sources_must_equal_the_pinned_commit_blobs_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _commit = _install_real_git_frozen_source(tmp_path, monkeypatch)
    target = source_root / Path(*frozen.FROZEN_PRODUCER_RELATIVE_PATHS[0].split("/"))
    target.write_bytes(b"uncommitted physical source\n")

    with pytest.raises(ValueError, match="commit|source binding"):
        with frozen._locked_physical_frozen_source_binding(source_root):
            pass


def test_frozen_checkout_policy_binds_raw_blobs_and_derived_crlf_physical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, commit = _install_real_git_frozen_source(tmp_path, monkeypatch)

    with frozen._locked_physical_frozen_source_binding(source_root) as binding:
        assert binding["checkout_policy"] == frozen.FROZEN_SOURCE_CHECKOUT_POLICY
        for entry in binding["physical_files"]:
            relative_path = entry["relative_path"]
            raw_blob = frozen._git_blob_bytes(source_root, commit, relative_path)
            physical = (source_root / Path(*relative_path.split("/"))).read_bytes()
            assert b"\r" not in raw_blob
            assert physical == raw_blob.replace(b"\n", b"\r\n")
            assert entry["commit_blob_bytes"] == len(raw_blob)
            assert entry["commit_blob_sha256"] == _sha(raw_blob)
            assert entry["physical_bytes"] == len(physical)
            assert entry["physical_sha256"] == _sha(physical)


@pytest.mark.parametrize(
    "raw_blob",
    (
        b"mixed\r\nline\n",
        b"binary\x00source\n",
        b"\xffinvalid-utf8\n",
        b"\xef\xbb\xbfbom-source\n",
    ),
)
def test_frozen_checkout_policy_rejects_mixed_eol_and_non_text_blobs(
    raw_blob: bytes,
) -> None:
    with pytest.raises(ValueError, match="checkout|blob"):
        frozen._derive_frozen_checkout_bytes(raw_blob)


def test_frozen_checkout_policy_rejects_tampering() -> None:
    tampered = dict(frozen.FROZEN_SOURCE_CHECKOUT_POLICY)
    tampered["physical_eol"] = "lf"

    with pytest.raises(ValueError, match="checkout policy"):
        frozen._validated_frozen_checkout_policy(tampered)


def test_attestation_rejects_readdressed_checkout_policy_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, _safe_result = _publish_fixture(tmp_path, monkeypatch)
    tampered = json.loads(Path(arguments["attestation_path"]).read_bytes())
    tampered["frozen_source"]["checkout_policy"]["physical_eol"] = "lf"
    output_root = (tmp_path / "tampered-attestation").resolve()
    output_root.mkdir()
    publication = frozen._write_attestation(output_root, tampered)
    tampered_path = output_root / Path(
        *publication["attestation_relative_path"].split("/")
    )

    with pytest.raises(ValueError, match="checkout policy"):
        frozen._validated_attestation(
            attestation_path=tampered_path,
            expected_attestation_sha256=publication["attestation_sha256"],
        )


def test_global_git_config_cannot_change_the_frozen_checkout_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _commit = _install_real_git_frozen_source(tmp_path, monkeypatch)
    with frozen._locked_physical_frozen_source_binding(source_root) as baseline:
        pass
    hostile_global = tmp_path / "hostile-gitconfig"
    hostile_global.write_text(
        "[core]\n\tautocrlf = false\n[eol]\n\tcheckout = lf\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile_global))

    with frozen._locked_physical_frozen_source_binding(source_root) as replay:
        pass

    assert replay == baseline
    assert frozen._git_environment()["GIT_CONFIG_GLOBAL"] == os.devnull


def test_post_b805_sources_are_bound_only_by_current_attestor_or_formal_closures() -> None:
    post_b805_sources = {
        "app/factor_v3_daily_basic_733_exact_set_authority.py",
        "app/factor_v3_daily_basic_runner.py",
        "app/factor_v3_feature_history_frozen_source_attestation.py",
        "app/jiaoch_daily_basic_collection_set.py",
    }
    attestor_sources = {
        "app/factor_v3_daily_basic_733_exact_set_authority.py",
        "app/factor_v3_feature_history_frozen_source_attestation.py",
    }

    assert post_b805_sources.isdisjoint(frozen.FROZEN_PRODUCER_RELATIVE_PATHS)
    assert attestor_sources <= set(frozen._ATTESTOR_RELATIVE_PATHS)
    assert post_b805_sources <= set(formal_spec.FORMAL_REVIEW_SOURCE_RELATIVE_PATHS)


def test_frozen_child_uses_the_bound_manifest_length_not_a_literal_ten() -> None:
    assert "len(expected_physical) != 10" not in frozen._FROZEN_VERIFIER_CODE
    assert "expected_physical_count" in frozen._FROZEN_VERIFIER_CODE


def test_frozen_child_is_existing_only_read_only_and_preserves_the_entire_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = (tmp_path / "source").resolve()
    app_root = source_root / "app"
    app_root.mkdir(parents=True)
    (app_root / "__init__.py").write_bytes(b"")
    for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS:
        path = source_root / Path(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\r\n")
    producer_files = [
        Path(relative_path).name
        for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS
        if relative_path != "app/factor_v3_feature_history_runner.py"
    ]
    authority_path = app_root / "audited_pit_factor_v3_feature_history_authority.py"
    authority_path.write_text(
        "\n".join(
            [
                "import hashlib, json",
                f"_PRODUCER_FILES = {producer_files!r}",
                "def _canonical_bytes(value):",
                "    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()",
                "def _producer_binding():",
                "    base={'schema_version':'factor-v3-feature-history-producer-binding/v2'}",
                "    base['root_sha256']=hashlib.sha256(_canonical_bytes(base)).hexdigest()",
                "    return base",
                "def _read_collection_manifest(**_kwargs):",
                "    return {'producer_binding':_producer_binding()}",
                "def _collection_publication_issuance(_publication):",
                "    return {'publication_capability_sha256':'d'*64}",
                "def _collection_issuance_relative_path(_issuance):",
                "    return 'feature_history_collection_publication_receipts/sha256/ee/'+'e'*64+'.json'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner_path = app_root / "factor_v3_feature_history_runner.py"
    runner_path.write_text(
        "\n".join(
            [
                "import json",
                "_MAX_STATE_BYTES=1024",
                "RECEIPT={'pit_store_database_sha256':'c'*64,'receipt_sha256':'f'*64,"
                "'session_count':250,'sessions_sha256':'1'*64,"
                "'snapshot_index_sha256':'2'*64,'source_authority_root_sha256':'3'*64}",
                "PUBLICATION={'authority_manifest_relative_path':"
                "'feature_history_collection_manifest_candidates/sha256/aa/'+'a'*64+'.json',"
                "'authority_manifest_sha256':'a'*64}",
                "def verify_factor_v3_feature_history_run(**_kwargs):",
                "    raise RuntimeError('mutating public verify forbidden')",
                "def _atomic_json(*_args,**_kwargs):",
                "    raise RuntimeError('state mutation forbidden')",
                "def load_factor_v3_feature_history_run_spec(_path):",
                "    return {'run_spec_sha256':'b'*64,'collection_plan':{}}",
                "def _verify_plan(_spec): return None",
                "def _run_paths(_root,create=False):",
                "    if create: raise RuntimeError('creation forbidden')",
                "    return {'lock':_root/'.factor-v3-feature-history-runner.lock',"
                "'state':_root/'state.json','publication_root':_root}",
                "def _validate_open_lock_identity(_path,_descriptor): return None",
                "def _read_json_file(path,**_kwargs):",
                "    return json.loads(path.read_text(encoding='utf-8'))",
                "def _validated_state(value,**_kwargs): return value",
                "def _validated_publication(value): return value",
                "def _verify_collection_authority(**_kwargs): return RECEIPT",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    commit_sources = {
        relative_path: (source_root / Path(*relative_path.split("/")))
        .read_bytes()
        .replace(b"\r\n", b"\n")
        for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS
    }
    monkeypatch.setattr(
        frozen,
        "_git_blob_bytes",
        lambda _root, _commit, relative_path: commit_sources[relative_path],
    )
    spec_path = (tmp_path / "spec.json").resolve()
    spec_path.write_bytes(b"{}")
    run_root = (tmp_path / "run").resolve()
    run_root.mkdir()
    (run_root / ".factor-v3-feature-history-runner.lock").write_bytes(b"\0")
    (run_root / "state.json").write_text(
        json.dumps(
            {
                "collection_publication": {
                    "authority_manifest_relative_path": (
                        "feature_history_collection_manifest_candidates/sha256/aa/"
                        + "a" * 64
                        + ".json"
                    ),
                    "authority_manifest_sha256": "a" * 64,
                },
                "completed_session_count": 250,
                "receipt": {
                    "pit_store_database_sha256": "c" * 64,
                    "receipt_sha256": "f" * 64,
                    "session_count": 250,
                    "sessions_sha256": "1" * 64,
                    "snapshot_index_sha256": "2" * 64,
                    "source_authority_root_sha256": "3" * 64,
                },
                "status": "verified",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    physical = frozen._physical_frozen_source_binding(source_root)
    python_path = Path(sys.executable).resolve()
    monkeypatch.setattr(frozen, "PYTHON_EXECUTABLE", python_path)
    monkeypatch.setattr(
        frozen,
        "PYTHON_EXECUTABLE_SHA256",
        _sha(python_path.read_bytes()),
    )

    before = _tree_snapshot(run_root)
    result = frozen._run_frozen_verifier(
        frozen_source_root=source_root,
        feature_history_run_spec_path=spec_path,
        feature_history_run_root=run_root,
        expected_physical_binding=physical,
    )
    after = _tree_snapshot(run_root)

    assert result["feature_history"]["session_count"] == 250
    assert result["producer_relative_paths"] == list(frozen.FROZEN_PRODUCER_RELATIVE_PATHS)
    assert after == before
    assert not tuple(run_root.rglob("*.tmp"))
    assert not tuple(run_root.rglob("*-wal"))
    assert not tuple(run_root.rglob("*-shm"))
    assert "verify_factor_v3_feature_history_run(" not in frozen._FROZEN_VERIFIER_CODE
    assert "_atomic_json(" not in frozen._FROZEN_VERIFIER_CODE
    assert "os.replace(" not in frozen._FROZEN_VERIFIER_CODE


def test_existing_read_only_run_lock_does_not_create_a_missing_lock(
    tmp_path: Path,
) -> None:
    missing = (tmp_path / "missing.lock").resolve()

    with pytest.raises(ValueError, match="lock"):
        with frozen._existing_read_only_run_lock(missing):
            pass

    assert not missing.exists()


def test_existing_read_only_run_lock_is_mutually_exclusive_with_writer_process(
    tmp_path: Path,
) -> None:
    lock_path = (tmp_path / "runner.lock").resolve()
    lock_path.write_bytes(b"\0")
    writer_code = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "from app import factor_v3_feature_history_runner as runner",
            "try:",
            "    with runner._run_lock(Path(sys.argv[2])):",
            "        print('locked' if sys.argv[1] == 'hold' else 'acquired', flush=True)",
            "        if sys.argv[1] == 'hold':",
            "            sys.stdin.readline()",
            "except Exception:",
            "    print('blocked', flush=True)",
        ]
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    writer = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            writer_code,
            "hold",
            str(lock_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert writer.stdout is not None
        assert writer.stdout.readline().strip() == "locked"
        with pytest.raises(ValueError, match="lock"):
            with frozen._existing_read_only_run_lock(lock_path):
                pass
    finally:
        if writer.poll() is None:
            assert writer.stdin is not None
            writer.stdin.write("\n")
            writer.stdin.flush()
            writer.wait(timeout=10)
    assert writer.returncode == 0

    with frozen._existing_read_only_run_lock(lock_path):
        contender = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                writer_code,
                "contend",
                str(lock_path),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert contender.stdout.strip() == "blocked"
    assert contender.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink executable contract")
def test_frozen_git_executable_allows_a_normal_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    raw = b"pinned-git-hardlink"
    executable.write_bytes(raw)
    os.link(executable, tmp_path / "git-copy.exe")
    monkeypatch.setattr(frozen, "GIT_EXECUTABLE", executable)
    monkeypatch.setattr(frozen, "GIT_EXECUTABLE_SHA256", _sha(raw))
    monkeypatch.setattr(
        frozen.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="ok\n",
            stderr="",
            returncode=0,
        ),
    )

    assert frozen._git_output(tmp_path, "rev-parse", "HEAD") == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink executable contract")
def test_frozen_python_executable_allows_a_normal_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "python.exe").resolve()
    raw = b"pinned-python-hardlink"
    executable.write_bytes(raw)
    os.link(executable, tmp_path / "python-copy.exe")
    monkeypatch.setattr(frozen, "PYTHON_EXECUTABLE", executable)
    monkeypatch.setattr(frozen, "PYTHON_EXECUTABLE_SHA256", _sha(raw))
    monkeypatch.setattr(
        frozen.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr=b"",
            stdout=b"{}\n",
        ),
    )
    source_root = (tmp_path / "source").resolve()
    source_root.mkdir()
    spec_path = (tmp_path / "spec.json").resolve()
    spec_path.write_bytes(b"{}")
    run_root = (tmp_path / "run").resolve()
    run_root.mkdir()

    assert (
        frozen._run_frozen_verifier(
            frozen_source_root=source_root,
            feature_history_run_spec_path=spec_path,
            feature_history_run_root=run_root,
            expected_physical_binding={
                "physical_files": [
                    {
                        "physical_bytes": 1,
                        "physical_sha256": "a" * 64,
                        "relative_path": relative_path,
                    }
                    for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS
                ]
            },
        )
        == {}
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write/delete contract")
def test_frozen_git_executable_is_locked_against_transient_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    original = b"pinned-git"
    executable.write_bytes(original)
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"transient-git")
    monkeypatch.setattr(frozen, "GIT_EXECUTABLE", executable)
    monkeypatch.setattr(
        frozen,
        "GIT_EXECUTABLE_SHA256",
        _sha(original),
    )
    blocked = False

    def transient_replace(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal blocked
        try:
            os.replace(replacement, executable)
        except PermissionError:
            blocked = True
        else:
            executable.write_bytes(original)
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(frozen.subprocess, "run", transient_replace)

    assert frozen._git_output(tmp_path, "rev-parse", "HEAD") == "ok"
    assert blocked is True
    assert executable.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write/delete contract")
def test_python_executable_is_locked_against_transient_replace_during_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "python.exe").resolve()
    original = b"pinned-python"
    executable.write_bytes(original)
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"transient-python")
    monkeypatch.setattr(frozen, "PYTHON_EXECUTABLE", executable)
    monkeypatch.setattr(
        frozen,
        "PYTHON_EXECUTABLE_SHA256",
        _sha(original),
    )
    blocked = False

    def transient_replace(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal blocked
        try:
            os.replace(replacement, executable)
        except PermissionError:
            blocked = True
        else:
            executable.write_bytes(original)
        return SimpleNamespace(
            returncode=0,
            stderr=b"",
            stdout=b"{}\n",
        )

    monkeypatch.setattr(frozen.subprocess, "run", transient_replace)
    source_root = (tmp_path / "source").resolve()
    source_root.mkdir()
    spec_path = (tmp_path / "spec.json").resolve()
    spec_path.write_bytes(b"{}")
    run_root = (tmp_path / "run").resolve()
    run_root.mkdir()

    frozen._run_frozen_verifier(
        frozen_source_root=source_root,
        feature_history_run_spec_path=spec_path,
        feature_history_run_root=run_root,
        expected_physical_binding={
            "physical_files": [
                {
                    "physical_bytes": 1,
                    "physical_sha256": "a" * 64,
                    "relative_path": relative_path,
                }
                for relative_path in frozen.FROZEN_PRODUCER_RELATIVE_PATHS
            ]
        },
    )

    assert blocked is True
    assert executable.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write/delete contract")
def test_frozen_sources_are_locked_against_transient_write_during_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, commit = _install_frozen_source(tmp_path, monkeypatch)
    spec_path = (tmp_path / "feature-spec.json").resolve()
    spec_path.write_bytes(
        json.dumps(
            {"run_spec_sha256": "b" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    run_root = (tmp_path / "feature-run").resolve()
    run_root.mkdir()
    monkeypatch.setattr(frozen, "FROZEN_FEATURE_RUN_SPEC_PATH", spec_path)
    monkeypatch.setattr(
        frozen,
        "FROZEN_FEATURE_RUN_SPEC_FILE_SHA256",
        _sha(spec_path.read_bytes()),
    )
    monkeypatch.setattr(
        frozen,
        "FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256",
        "b" * 64,
    )
    monkeypatch.setattr(frozen, "FROZEN_FEATURE_RUN_ROOT", run_root)
    safe_result = _safe_verifier_result(
        source_root=source_root,
        spec_path=spec_path,
        run_root=run_root,
    )
    target = source_root / Path(*frozen.FROZEN_PRODUCER_RELATIVE_PATHS[0].split("/"))
    original = target.read_bytes()
    blocked = False

    def transient_write(**_kwargs: Any) -> dict[str, Any]:
        nonlocal blocked
        try:
            target.write_bytes(b"transient-source")
        except PermissionError:
            blocked = True
        else:
            target.write_bytes(original)
        return json.loads(json.dumps(safe_result))

    monkeypatch.setattr(
        frozen,
        "_run_frozen_verifier",
        transient_write,
    )
    output_root = (tmp_path / "attestations").resolve()
    output_root.mkdir()

    frozen.publish_factor_v3_feature_history_frozen_source_attestation(
        frozen_source_root=source_root,
        expected_frozen_source_commit=commit,
        feature_history_run_spec_path=spec_path,
        feature_history_run_root=run_root,
        output_root=output_root,
    )

    assert blocked is True
    assert target.read_bytes() == original


def test_pinned_executable_rejects_reparse_in_any_parent_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "blocked-parent"
    parent.mkdir()
    executable = parent / "python.exe"
    executable.write_bytes(b"fixed executable")
    original = raw_authority._path_is_link_or_reparse
    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        lambda candidate: Path(candidate) == parent or original(Path(candidate)),
    )

    with pytest.raises(ValueError, match="link|reparse|executable"):
        frozen._read_pinned_executable(
            executable,
            expected_sha256=_sha(executable.read_bytes()),
            label="python",
        )


def test_frozen_result_rejects_noncanonical_producer_binding(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "feature-spec.json"
    spec_path.write_bytes(b"{}")
    run_root = tmp_path / "feature-run"
    run_root.mkdir()
    result = _safe_verifier_result(
        source_root=tmp_path,
        spec_path=spec_path,
        run_root=run_root,
    )
    result["producer_binding"]["extra"] = "unbound"

    with pytest.raises(ValueError, match="producer binding"):
        frozen._validated_frozen_result(result)


def test_direct_file_read_rejects_reparse_in_any_parent_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "blocked-parent"
    parent.mkdir()
    path = parent / "attestation.json"
    path.write_bytes(b"{}")
    original = raw_authority._path_is_link_or_reparse
    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        lambda candidate: Path(candidate) == parent or original(Path(candidate)),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        frozen._read_direct_file(
            path,
            label="attestation",
            max_bytes=1024,
        )


def test_attestation_cas_rejects_intermediate_reparse_and_postverifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = (tmp_path / "attestations").resolve()
    output_root.mkdir()
    kind_root = output_root / frozen._ATTESTATION_KIND
    original_reparse = raw_authority._path_is_link_or_reparse
    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        lambda candidate: Path(candidate) == kind_root or original_reparse(Path(candidate)),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        frozen._write_attestation(output_root, {"safe": True})

    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        original_reparse,
    )
    reads = 0
    original_read = raw_authority._read_safe_file

    def counted_read(*args: Any, **kwargs: Any) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(raw_authority, "_read_safe_file", counted_read)
    frozen._write_attestation(output_root, {"safe": True})
    assert reads >= 1


def test_attestation_cas_rejects_intermediate_reparse_and_postverifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = (tmp_path / "attestations").resolve()
    output_root.mkdir()
    kind_root = output_root / frozen._ATTESTATION_KIND
    original_reparse = raw_authority._path_is_link_or_reparse
    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        lambda candidate: (
            Path(candidate) == kind_root
            or original_reparse(Path(candidate))
        ),
    )

    with pytest.raises(ValueError, match="link|reparse"):
        frozen._write_attestation(output_root, {"safe": True})

    monkeypatch.setattr(
        raw_authority,
        "_path_is_link_or_reparse",
        original_reparse,
    )
    reads = 0
    original_read = raw_authority._read_safe_file

    def counted_read(*args: Any, **kwargs: Any) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(raw_authority, "_read_safe_file", counted_read)
    frozen._write_attestation(output_root, {"safe": True})
    assert reads >= 1


def test_daily_authority_binds_attestation_helper_as_producer_dependency() -> None:
    assert (
        "factor_v3_feature_history_frozen_source_attestation.py" in daily_authority._PRODUCER_FILES
    )
