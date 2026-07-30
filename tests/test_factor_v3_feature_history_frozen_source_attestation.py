from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app import factor_v3_daily_basic_733_exact_set_authority as daily_authority
from app import factor_v3_feature_history_frozen_source_attestation as frozen


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


def _install_frozen_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str]:
    source_root = (tmp_path / "frozen-source").resolve()
    app_root = source_root / "app"
    app_root.mkdir(parents=True)
    for index, relative_path in enumerate(frozen.FROZEN_PRODUCER_RELATIVE_PATHS):
        path = source_root / Path(*relative_path.split("/"))
        path.write_bytes(f"source-{index}\n".encode())
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
    return source_root, commit


def _safe_verifier_result(
    *,
    source_root: Path,
    spec_path: Path,
    run_root: Path,
) -> dict[str, Any]:
    producer = frozen._physical_frozen_source_binding(source_root)
    feature = {
        "authority_manifest_relative_path": (
            "feature_history_collection_manifest_candidates/sha256/aa/"
            f"{'a' * 64}.json"
        ),
        "authority_manifest_sha256": "a" * 64,
        "feature_run_root": str(run_root),
        "feature_run_spec_file_sha256": _sha(spec_path.read_bytes()),
        "feature_run_spec_path": str(spec_path),
        "feature_run_spec_sha256": "b" * 64,
        "pit_store_database_sha256": "c" * 64,
        "publication_capability_sha256": "d" * 64,
        "publication_issuance_relative_path": (
            "feature_history_collection_publication_receipts/sha256/ee/"
            f"{'e' * 64}.json"
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
            "root_sha256": producer["producer_binding_root_sha256"],
            "schema_version": "factor-v3-feature-history-producer-binding/v2",
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
    output_root = (tmp_path / "attestations").resolve()
    output_root.mkdir()
    publication = (
        frozen.publish_factor_v3_feature_history_frozen_source_attestation(
            frozen_source_root=source_root,
            expected_frozen_source_commit=commit,
            feature_history_run_spec_path=spec_path,
            feature_history_run_root=run_root,
            output_root=output_root,
        )
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
        lambda **_kwargs: json.loads(
            json.dumps(safe_result["feature_history"])
        ),
    )

    verified = (
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )
    )

    raw = Path(arguments["attestation_path"]).read_bytes()
    attestation = json.loads(raw)
    assert verified["verified"] is True
    assert verified["session_count"] == 250
    assert _sha(raw) == arguments["expected_attestation_sha256"]
    assert Path(arguments["attestation_path"]).name == f"{_sha(raw)}.json"
    assert "publication_capability" not in attestation
    assert b'"publication_capability":' not in raw
    assert "token" not in json.dumps(attestation).lower()


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
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="attestation"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )
    path.write_bytes(raw)
    arguments["expected_attestation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="attestation"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )


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
    source_file = (
        Path(arguments["frozen_source_root"])
        / Path(*frozen.FROZEN_PRODUCER_RELATIVE_PATHS[1].split("/"))
    )
    source_file.write_bytes(source_file.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="source"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )

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
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )


def test_attestation_rejects_manifest_replacement_and_current_consumer_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, safe_result = _publish_fixture(tmp_path, monkeypatch)
    replaced = json.loads(json.dumps(safe_result["feature_history"]))
    replaced["authority_manifest_sha256"] = "9" * 64
    replaced["authority_manifest_relative_path"] = (
        "feature_history_collection_manifest_candidates/sha256/99/"
        f"{'9' * 64}.json"
    )
    monkeypatch.setattr(
        frozen,
        "_replay_current_feature_history",
        lambda **_kwargs: replaced,
    )
    with pytest.raises(ValueError, match="replay"):
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )

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
        frozen.verify_factor_v3_feature_history_frozen_source_attestation(
            **arguments
        )


def test_only_private_attestation_gate_can_replace_manifest_producer_binding() -> None:
    assert "expected_producer_binding" not in inspect.signature(
        history_authority.verify_factor_v3_feature_history_collection_authority
    ).parameters
    with pytest.raises(ValueError, match="attestation"):
        history_authority._verify_feature_history_with_attested_producer_binding(
            expected_producer_binding={"root_sha256": "manifest-self-report"},
            attestation_capability=object(),
        )


def test_daily_authority_binds_attestation_helper_as_producer_dependency() -> None:
    assert (
        "factor_v3_feature_history_frozen_source_attestation.py"
        in daily_authority._PRODUCER_FILES
    )
