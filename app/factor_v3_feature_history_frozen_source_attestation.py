"""One-artifact frozen-source attestation for the formal Factor V3 prewindow."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


FROZEN_SOURCE_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run"
)
FROZEN_SOURCE_COMMIT = "b8057962f7a9754848994a6cfda9c9bf85e3db89"
FROZEN_FEATURE_RUN_SPEC_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\data\research_runs"
    r"\audited_pit_factor_v3_feature_history_run_spec_v1\run_specs\sha256\1d"
    r"\1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
)
FROZEN_FEATURE_RUN_SPEC_FILE_SHA256 = (
    "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3"
)
FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256 = (
    "561df1ecf28112544c28b318787581ffaf2edc64f8b53cfd45787d0206cf9e29"
)
FROZEN_FEATURE_RUN_ROOT = Path(
    r"E:\AI workspace\quant-signal-lkj\data\research_runs"
    r"\audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
)
FROZEN_PRODUCER_RELATIVE_PATHS = (
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/durable_io.py",
    "app/jiaoch_credential_slots.py",
    "app/jiaoch_trade_cal_authority.py",
    "app/research_partitions.py",
    "app/research_pit_collector.py",
    "app/research_pit_sources.py",
    "app/research_pit_store.py",
    "app/research_security_code_transition.py",
)
ATTESTATION_SCHEMA = "factor-v3-feature-history-frozen-source-attestation/v1"
ATTESTATION_PUBLICATION_SCHEMA = (
    "factor-v3-feature-history-frozen-source-attestation-publication/v1"
)
_ATTESTATION_KIND = "factor_v3_feature_history_frozen_source_attestations"
_ATTESTOR_VERSION = (
    "app.factor_v3_feature_history_frozen_source_attestation/1"
)
_ATTESTOR_RELATIVE_PATHS = (
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_feature_history_runner.py",
)
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_ATTESTATION_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_ATTESTED_REPLAY_CAPABILITY = object()
_FROZEN_RESULT_FIELDS = frozenset(
    {"feature_history", "producer_binding", "producer_relative_paths"}
)
_FEATURE_FIELDS = frozenset(
    {
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "feature_run_root",
        "feature_run_spec_file_sha256",
        "feature_run_spec_path",
        "feature_run_spec_sha256",
        "pit_store_database_sha256",
        "publication_capability_sha256",
        "publication_issuance_relative_path",
        "publication_issuance_sha256",
        "receipt_sha256",
        "session_count",
        "sessions_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
    }
)
_FROZEN_SOURCE_FIELDS = frozenset(
    {
        "commit",
        "physical_files",
        "physical_files_root_sha256",
        "producer_binding",
        "root",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {"attestor_producer", "feature_history", "frozen_source", "schema", "verified"}
)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("factor-v3 frozen-source canonical JSON rejected") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & flag
    )


def _read_direct_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    try:
        before = path.stat()
        if (
            _is_reparse(path)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise OSError
        raw = path.read_bytes()
        after = path.stat()
    except OSError:
        raise ValueError(f"factor-v3 frozen-source {label} rejected") from None
    if (
        before.st_size != len(raw)
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ValueError(f"factor-v3 frozen-source {label} drifted")
    return raw


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in items:
            if key in output:
                raise ValueError(f"factor-v3 frozen-source {label} rejected")
            output[key] = item
        return output

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _value: None,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"factor-v3 frozen-source {label} rejected") from exc
    if type(value) is not dict or not hmac.compare_digest(raw, _canonical_bytes(value)):
        raise ValueError(f"factor-v3 frozen-source {label} rejected")
    return value


def _git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("factor-v3 frozen-source git identity rejected") from exc
    return result.stdout.strip()


def _git_identity(root: Path) -> dict[str, Any]:
    return {
        "commit": _git_output(root, "rev-parse", "HEAD"),
        "root": str(
            Path(
                _git_output(root, "rev-parse", "--show-toplevel")
            ).resolve(strict=True)
        ),
        "status_clean": not bool(
            _git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
        ),
    }


def _validated_frozen_root(
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
) -> Path:
    root = Path(frozen_source_root)
    if (
        not root.is_absolute()
        or _is_reparse(root)
        or not root.is_dir()
        or root.resolve(strict=True) != Path(FROZEN_SOURCE_ROOT).resolve(strict=True)
        or expected_frozen_source_commit != FROZEN_SOURCE_COMMIT
        or _COMMIT_RE.fullmatch(expected_frozen_source_commit) is None
    ):
        raise ValueError("factor-v3 frozen-source identity rejected")
    identity = _git_identity(root)
    if identity != {
        "commit": expected_frozen_source_commit,
        "root": str(root.resolve(strict=True)),
        "status_clean": True,
    }:
        raise ValueError("factor-v3 frozen-source identity rejected")
    return root.resolve(strict=True)


def _physical_frozen_source_binding(source_root: str | Path) -> dict[str, Any]:
    root = Path(source_root).resolve(strict=True)
    entries = []
    for relative_path in FROZEN_PRODUCER_RELATIVE_PATHS:
        raw = _read_direct_file(
            root / Path(*relative_path.split("/")),
            label="producer source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        entries.append(
            {
                "physical_bytes": len(raw),
                "physical_sha256": _sha256(raw),
                "relative_path": relative_path,
            }
        )
    identity = {
        "physical_files": entries,
        "schema": "factor-v3-feature-history-frozen-physical-source/v1",
    }
    return {
        **identity,
        "producer_binding_root_sha256": _canonical_sha256(identity),
    }


def _attestor_producer_binding() -> dict[str, Any]:
    source_root = Path(__file__).resolve(strict=True).parents[1]
    entries = []
    for relative_path in _ATTESTOR_RELATIVE_PATHS:
        raw = _read_direct_file(
            source_root / Path(*relative_path.split("/")),
            label="attestor source",
            max_bytes=_MAX_SOURCE_BYTES,
        )
        entries.append(
            {
                "path": relative_path,
                "physical_bytes": len(raw),
                "physical_sha256": _sha256(raw),
            }
        )
    identity = {
        "entries": entries,
        "producer_version": _ATTESTOR_VERSION,
        "schema": "factor-v3-feature-history-frozen-attestor-producer/v1",
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


_FROZEN_VERIFIER_CODE = r"""
import hashlib
import json
from pathlib import Path
import sys

source_root = Path(sys.argv[1]).resolve(strict=True)
spec_path = Path(sys.argv[2]).resolve(strict=True)
run_root = Path(sys.argv[3]).resolve(strict=True)
sys.path.insert(0, str(source_root))
from app import audited_pit_factor_v3_feature_history_authority as authority
from app import factor_v3_feature_history_runner as runner

verified = runner.verify_factor_v3_feature_history_run(
    run_spec_path=spec_path,
    run_root=run_root,
)
if verified.get("status") != "verified" or type(verified.get("receipt")) is not dict:
    raise RuntimeError("frozen verifier rejected")
receipt = verified["receipt"]
spec = runner.load_factor_v3_feature_history_run_spec(spec_path)
paths = runner._run_paths(run_root, create=False)
state = runner._validated_state(
    runner._read_json_file(
        paths["state"],
        label="run state",
        max_bytes=runner._MAX_STATE_BYTES,
    ),
    run_spec_sha256=spec["run_spec_sha256"],
)
publication = runner._validated_publication(state["collection_publication"])
manifest = authority._read_collection_manifest(
    output_root=paths["publication_root"],
    publication=publication,
)
issuance = authority._collection_publication_issuance(publication)
issuance_raw = authority._canonical_bytes(issuance)
feature = {
    "authority_manifest_relative_path": publication["authority_manifest_relative_path"],
    "authority_manifest_sha256": publication["authority_manifest_sha256"],
    "feature_run_root": str(run_root),
    "feature_run_spec_file_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
    "feature_run_spec_path": str(spec_path),
    "feature_run_spec_sha256": spec["run_spec_sha256"],
    "pit_store_database_sha256": receipt["pit_store_database_sha256"],
    "publication_capability_sha256": issuance["publication_capability_sha256"],
    "publication_issuance_relative_path": authority._collection_issuance_relative_path(issuance),
    "publication_issuance_sha256": hashlib.sha256(issuance_raw).hexdigest(),
    "receipt_sha256": receipt["receipt_sha256"],
    "session_count": receipt["session_count"],
    "sessions_sha256": receipt["sessions_sha256"],
    "snapshot_index_sha256": receipt["snapshot_index_sha256"],
    "source_authority_root_sha256": receipt["source_authority_root_sha256"],
}
if manifest["producer_binding"] != authority._producer_binding():
    raise RuntimeError("frozen producer binding rejected")
print(json.dumps(
    {
        "feature_history": feature,
        "producer_binding": manifest["producer_binding"],
        "producer_relative_paths": [
            f"app/{name}" for name in authority._PRODUCER_FILES
        ],
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
))
"""


def _run_frozen_verifier(
    *,
    frozen_source_root: Path,
    feature_history_run_spec_path: Path,
    feature_history_run_root: Path,
) -> dict[str, Any]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    }
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                _FROZEN_VERIFIER_CODE,
                str(frozen_source_root),
                str(feature_history_run_spec_path),
                str(feature_history_run_root),
            ],
            cwd=frozen_source_root,
            env=environment,
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError("factor-v3 frozen-source verifier unavailable") from exc
    if result.returncode != 0 or result.stderr or not result.stdout:
        raise ValueError("factor-v3 frozen-source verifier rejected")
    raw = result.stdout.rstrip(b"\r\n")
    if result.stdout not in {raw + b"\n", raw + b"\r\n"}:
        raise ValueError("factor-v3 frozen-source verifier output rejected")
    return _strict_json(raw, label="verifier output")


def _reject_plaintext_credentials(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {
                "api_key",
                "credential",
                "password",
                "publication_capability",
                "secret",
                "token",
            }:
                raise ValueError(
                    "factor-v3 frozen-source plaintext credential rejected"
                )
            _reject_plaintext_credentials(nested)
    elif type(value) is list:
        for nested in value:
            _reject_plaintext_credentials(nested)


def _validated_feature_identity(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FEATURE_FIELDS:
        raise ValueError("factor-v3 frozen-source feature identity rejected")
    output = json.loads(_canonical_bytes(value))
    for field in (
        "authority_manifest_sha256",
        "feature_run_spec_file_sha256",
        "feature_run_spec_sha256",
        "pit_store_database_sha256",
        "publication_capability_sha256",
        "publication_issuance_sha256",
        "receipt_sha256",
        "sessions_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
    ):
        _require_sha256(output[field], label=field)
    if (
        output["session_count"] != 250
        or type(output["feature_run_root"]) is not str
        or type(output["feature_run_spec_path"]) is not str
        or not Path(output["feature_run_root"]).is_absolute()
        or not Path(output["feature_run_spec_path"]).is_absolute()
        or type(output["authority_manifest_relative_path"]) is not str
        or type(output["publication_issuance_relative_path"]) is not str
    ):
        raise ValueError("factor-v3 frozen-source feature identity rejected")
    for path_field, sha_field in (
        ("authority_manifest_relative_path", "authority_manifest_sha256"),
        ("publication_issuance_relative_path", "publication_issuance_sha256"),
    ):
        parts = output[path_field].split("/")
        digest = output[sha_field]
        if (
            len(parts) != 4
            or parts[-3] != "sha256"
            or parts[-2] != digest[:2]
            or parts[-1] != f"{digest}.json"
        ):
            raise ValueError("factor-v3 frozen-source feature CAS identity rejected")
    return output


def _validated_frozen_result(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FROZEN_RESULT_FIELDS:
        raise ValueError("factor-v3 frozen-source verifier result rejected")
    if value.get("producer_relative_paths") != list(
        FROZEN_PRODUCER_RELATIVE_PATHS
    ):
        raise ValueError("factor-v3 frozen-source producer set rejected")
    producer = value.get("producer_binding")
    if type(producer) is not dict or type(producer.get("root_sha256")) is not str:
        raise ValueError("factor-v3 frozen-source producer binding rejected")
    _require_sha256(producer["root_sha256"], label="producer binding root")
    output = {
        "feature_history": _validated_feature_identity(value["feature_history"]),
        "producer_binding": json.loads(_canonical_bytes(producer)),
        "producer_relative_paths": list(FROZEN_PRODUCER_RELATIVE_PATHS),
    }
    _reject_plaintext_credentials(output)
    return output


def _attestation_payload(
    *,
    frozen_source_root: Path,
    frozen_source_commit: str,
    physical_binding: Mapping[str, Any],
    verifier_result: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "attestor_producer": _attestor_producer_binding(),
        "feature_history": dict(verifier_result["feature_history"]),
        "frozen_source": {
            "commit": frozen_source_commit,
            "physical_files": physical_binding["physical_files"],
            "physical_files_root_sha256": physical_binding[
                "producer_binding_root_sha256"
            ],
            "producer_binding": verifier_result["producer_binding"],
            "root": str(frozen_source_root),
        },
        "schema": ATTESTATION_SCHEMA,
        "verified": True,
    }
    _reject_plaintext_credentials(payload)
    return payload


def _safe_output_root(output_root: str | Path) -> Path:
    root = Path(output_root)
    if (
        not root.is_absolute()
        or _is_reparse(root)
        or not root.is_dir()
    ):
        raise ValueError("factor-v3 frozen-source output root rejected")
    return root.resolve(strict=True)


def _write_attestation(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _canonical_bytes(dict(payload))
    if len(raw) > _MAX_ATTESTATION_BYTES:
        raise ValueError("factor-v3 frozen-source attestation rejected")
    digest = _sha256(raw)
    directory = root / _ATTESTATION_KIND / "sha256" / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    if _is_reparse(directory) or not directory.is_dir():
        raise ValueError("factor-v3 frozen-source attestation root rejected")
    path = directory / f"{digest}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError:
        if _read_direct_file(
            path,
            label="attestation",
            max_bytes=_MAX_ATTESTATION_BYTES,
        ) != raw:
            raise ValueError("factor-v3 frozen-source attestation collision")
    else:
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return {
        "attestation_relative_path": path.relative_to(root).as_posix(),
        "attestation_sha256": digest,
        "schema": ATTESTATION_PUBLICATION_SCHEMA,
    }


def publish_factor_v3_feature_history_frozen_source_attestation(
    *,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    root = _validated_frozen_root(
        frozen_source_root,
        expected_frozen_source_commit,
    )
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    if (
        spec_path != Path(FROZEN_FEATURE_RUN_SPEC_PATH).resolve(strict=True)
        or run_root != Path(FROZEN_FEATURE_RUN_ROOT).resolve(strict=True)
        or _sha256(
            _read_direct_file(
                spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        )
        != FROZEN_FEATURE_RUN_SPEC_FILE_SHA256
    ):
        raise ValueError("factor-v3 frozen-source feature input rejected")
    before = _physical_frozen_source_binding(root)
    result = _validated_frozen_result(
        _run_frozen_verifier(
            frozen_source_root=root,
            feature_history_run_spec_path=spec_path,
            feature_history_run_root=run_root,
        )
    )
    if (
        result["feature_history"]["feature_run_spec_sha256"]
        != FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256
    ):
        raise ValueError("factor-v3 frozen-source feature input rejected")
    after = _physical_frozen_source_binding(root)
    if (
        before != after
        or _validated_frozen_root(root, expected_frozen_source_commit) != root
    ):
        raise ValueError("factor-v3 frozen-source source drifted")
    payload = _attestation_payload(
        frozen_source_root=root,
        frozen_source_commit=expected_frozen_source_commit,
        physical_binding=after,
        verifier_result=result,
    )
    return _write_attestation(_safe_output_root(output_root), payload)


def _attestation_path(
    attestation_path: str | Path,
    expected_attestation_sha256: str,
) -> Path:
    digest = _require_sha256(
        expected_attestation_sha256,
        label="attestation sha256",
    )
    path = Path(attestation_path)
    expected_tail = (
        _ATTESTATION_KIND,
        "sha256",
        digest[:2],
        f"{digest}.json",
    )
    if (
        not path.is_absolute()
        or tuple(path.parts[-4:]) != expected_tail
    ):
        raise ValueError("factor-v3 frozen-source attestation path rejected")
    return path


def _validated_attestation(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
) -> dict[str, Any]:
    path = _attestation_path(attestation_path, expected_attestation_sha256)
    raw = _read_direct_file(
        path,
        label="attestation",
        max_bytes=_MAX_ATTESTATION_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_attestation_sha256):
        raise ValueError("factor-v3 frozen-source attestation content rejected")
    value = _strict_json(raw, label="attestation")
    if (
        set(value) != _ATTESTATION_FIELDS
        or value.get("schema") != ATTESTATION_SCHEMA
        or value.get("verified") is not True
        or type(value.get("attestor_producer")) is not dict
        or type(value.get("frozen_source")) is not dict
        or set(value["frozen_source"]) != _FROZEN_SOURCE_FIELDS
    ):
        raise ValueError("factor-v3 frozen-source attestation rejected")
    _validated_feature_identity(value.get("feature_history"))
    _reject_plaintext_credentials(value)
    return value


def _replay_current_feature_history(
    *,
    feature_history_run_spec_path: Path,
    feature_history_run_root: Path,
    expected_producer_binding: Mapping[str, Any],
) -> dict[str, Any]:
    from app import audited_pit_factor_v3_feature_history_authority as authority
    from app import factor_v3_feature_history_runner as runner

    spec = runner.load_factor_v3_feature_history_run_spec(
        feature_history_run_spec_path
    )
    paths = runner._run_paths(feature_history_run_root, create=False)
    state = runner._validated_state(
        runner._read_json_file(
            paths["state"],
            label="run state",
            max_bytes=runner._MAX_STATE_BYTES,
        ),
        run_spec_sha256=spec["run_spec_sha256"],
    )
    publication = runner._validated_publication(
        state["collection_publication"]
    )
    receipt = authority._verify_feature_history_with_attested_producer_binding(
        expected_producer_binding=expected_producer_binding,
        attestation_capability=_ATTESTED_REPLAY_CAPABILITY,
        collection_plan=spec["collection_plan"],
        trade_cal_output_root=spec["trade_cal_output_root"],
        trade_cal_publication=spec["trade_cal_publication"],
        development_session_refs=spec["development_session_refs"],
        temporal_partition_contract=spec["temporal_partition_contract"],
        collection_publication_output_root=paths["publication_root"],
        collection_publication=publication,
    )
    if state.get("receipt") != receipt:
        raise ValueError("factor-v3 frozen-source current receipt replay rejected")
    issuance = authority._collection_publication_issuance(publication)
    issuance_raw = authority._canonical_bytes(issuance)
    return {
        "authority_manifest_relative_path": publication[
            "authority_manifest_relative_path"
        ],
        "authority_manifest_sha256": publication[
            "authority_manifest_sha256"
        ],
        "feature_run_root": str(feature_history_run_root),
        "feature_run_spec_file_sha256": _sha256(
            _read_direct_file(
                feature_history_run_spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        ),
        "feature_run_spec_path": str(feature_history_run_spec_path),
        "feature_run_spec_sha256": spec["run_spec_sha256"],
        "pit_store_database_sha256": receipt["pit_store_database_sha256"],
        "publication_capability_sha256": issuance[
            "publication_capability_sha256"
        ],
        "publication_issuance_relative_path": authority._collection_issuance_relative_path(
            issuance
        ),
        "publication_issuance_sha256": _sha256(issuance_raw),
        "receipt_sha256": receipt["receipt_sha256"],
        "session_count": receipt["session_count"],
        "sessions_sha256": receipt["sessions_sha256"],
        "snapshot_index_sha256": receipt["snapshot_index_sha256"],
        "source_authority_root_sha256": receipt[
            "source_authority_root_sha256"
        ],
    }


def verify_factor_v3_feature_history_frozen_source_attestation(
    *,
    attestation_path: str | Path,
    expected_attestation_sha256: str,
    frozen_source_root: str | Path,
    expected_frozen_source_commit: str,
    feature_history_run_spec_path: str | Path,
    feature_history_run_root: str | Path,
) -> dict[str, Any]:
    attestation = _validated_attestation(
        attestation_path=attestation_path,
        expected_attestation_sha256=expected_attestation_sha256,
    )
    root = _validated_frozen_root(
        frozen_source_root,
        expected_frozen_source_commit,
    )
    frozen_identity = attestation["frozen_source"]
    physical = _physical_frozen_source_binding(root)
    if (
        frozen_identity["root"] != str(root)
        or frozen_identity["commit"] != expected_frozen_source_commit
        or frozen_identity["physical_files"] != physical["physical_files"]
        or frozen_identity["physical_files_root_sha256"]
        != physical["producer_binding_root_sha256"]
    ):
        raise ValueError("factor-v3 frozen-source source identity rejected")
    if attestation["attestor_producer"] != _attestor_producer_binding():
        raise ValueError("factor-v3 frozen-source attestor producer rejected")
    attestor_before = _attestor_producer_binding()
    spec_path = Path(feature_history_run_spec_path).resolve(strict=True)
    run_root = Path(feature_history_run_root).resolve(strict=True)
    feature = _validated_feature_identity(attestation["feature_history"])
    if (
        feature["feature_run_spec_path"] != str(spec_path)
        or feature["feature_run_root"] != str(run_root)
        or spec_path != Path(FROZEN_FEATURE_RUN_SPEC_PATH).resolve(strict=True)
        or run_root != Path(FROZEN_FEATURE_RUN_ROOT).resolve(strict=True)
        or feature["feature_run_spec_sha256"]
        != FROZEN_FEATURE_RUN_SPEC_LOGICAL_SHA256
        or feature["feature_run_spec_file_sha256"]
        != _sha256(
            _read_direct_file(
                spec_path,
                label="feature run spec",
                max_bytes=_MAX_ATTESTATION_BYTES,
            )
        )
    ):
        raise ValueError("factor-v3 frozen-source feature input rejected")
    replay = _validated_feature_identity(
        _replay_current_feature_history(
            feature_history_run_spec_path=spec_path,
            feature_history_run_root=run_root,
            expected_producer_binding=frozen_identity["producer_binding"],
        )
    )
    if replay != feature:
        raise ValueError("factor-v3 frozen-source current replay rejected")
    if (
        _validated_frozen_root(root, expected_frozen_source_commit) != root
        or _physical_frozen_source_binding(root)["physical_files"]
        != frozen_identity["physical_files"]
        or _attestor_producer_binding() != attestor_before
    ):
        raise ValueError("factor-v3 frozen-source source drifted")
    return {
        "receipt_sha256": feature["receipt_sha256"],
        "session_count": 250,
        "sessions_sha256": feature["sessions_sha256"],
        "verified": True,
    }
