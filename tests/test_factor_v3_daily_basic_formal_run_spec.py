from __future__ import annotations

import ast
import base64
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace

import pytest

from app import factor_v3_formal_bootstrap_runtime as bootstrap_runtime
from scripts import build_factor_v3_daily_basic_formal_run_spec as formal
from scripts import run_factor_v3_daily_basic_formal as formal_shim


_OPENSSL = Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe")


def _test_rsa3072_key(tmp_path: Path, label: str) -> tuple[Path, bytes]:
    private_key = tmp_path / f"{label}-private.pem"
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


def _test_rsa_signature(
    tmp_path: Path,
    *,
    label: str,
    private_key: Path,
    payload: bytes,
) -> bytes:
    payload_path = tmp_path / f"{label}-payload.json"
    signature_path = tmp_path / f"{label}-signature.bin"
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


def _sessions(count: int = 733) -> list[str]:
    start = date(2023, 6, 26)
    sessions = [(start + timedelta(days=index)).isoformat() for index in range(count - 1)]
    sessions.append("2026-07-03")
    return sessions


def _candidate(*, sessions: list[str] | None = None) -> dict[str, object]:
    trade_dates = _sessions() if sessions is None else sessions
    return {
        "schema": "factor-v3-daily-basic-run-spec/v2",
        "run_spec_sha256": "a" * 64,
        "source_authority_root_sha256": "b" * 64,
        "session_count": len(trade_dates),
        "sessions": trade_dates,
        "sessions_sha256": "c" * 64,
        "collection_policy_descriptor": {
            "credential_proof_claimed": False,
            "document": {
                "routes": [
                    {
                        "api_name": "daily_basic",
                        "credential_slot_id": "points-primary",
                    }
                ]
            },
        },
        "collector": {
            "timeout_seconds": 30.0,
            "max_attempts": 3,
            "workers": 1,
        },
        "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
    }


class _FakeRunner:
    def __init__(self, candidate: dict[str, object]) -> None:
        self.candidate = candidate
        self.build_calls: list[dict[str, object]] = []
        self.validate_calls: list[bytes] = []

    def build_factor_v3_daily_basic_run_spec(self, **kwargs: object) -> dict[str, object]:
        self.build_calls.append(kwargs)
        return json.loads(json.dumps(self.candidate))

    def validate_factor_v3_daily_basic_run_spec_bytes(
        self,
        raw: bytes,
    ) -> dict[str, object]:
        self.validate_calls.append(raw)
        assert not raw.endswith(b"\n")
        return json.loads(raw)

    def load_factor_v3_daily_basic_run_spec(
        self,
        _path: str | Path,
    ) -> dict[str, object]:
        raise AssertionError("preflight must not create a temporary run spec")

    def run_factor_v3_daily_basic_collection(self, **_kwargs: object) -> object:
        raise AssertionError("preflight must not run collection")

    def verify_factor_v3_daily_basic_run(self, **_kwargs: object) -> object:
        raise AssertionError("preflight must not verify a persisted run")


class _FrozenActionConfig(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _TrustedBootstrapContext:
    def __init__(
        self,
        config: _FrozenActionConfig,
        *,
        ledger_overrides: Mapping[
            str,
            Mapping[str, object],
        ] | None = None,
        rejected_modules: set[str] | None = None,
    ) -> None:
        self._config = config
        self._ledger_overrides = dict(ledger_overrides or {})
        self._rejected_modules = set(rejected_modules or ())
        self.asserted_modules: list[tuple[str, str, str]] = []
        self.buffered: list[object] = []
        self.postverify_calls = 0
        self.guard_events: list[str] = []
        self.guard_terminal_attack: Callable[[], None] | None = None
        self.guard_descriptor_overrides: dict[str, object] = {}
        self.guard_initial_snapshot_override: object | None = None
        self._guard_descriptor: Mapping[str, object] | None = None
        self._runtime_guard_context: object | None = None
        self.guard = _SyntheticNativePreflightTerminalGuard(self)

    def validate_action_config(self, candidate: object) -> None:
        if candidate is not self._config:
            raise RuntimeError("untrusted frozen action config")

    def verified_ledger_entry(self, module_name: str) -> Mapping[str, object]:
        relative_path = (
            "app/__init__.py"
            if module_name == "app"
            else f"{module_name.replace('.', '/')}.py"
        )
        entry = {
            "absolute_path": str(
                formal.FORMAL_WORKTREE_ROOT
                / Path(*relative_path.split("/"))
            ),
            "byte_count": 1,
            "is_package": module_name == "app",
            "loader_identity": "external-verified-source-loader/v1",
            "module_name": module_name,
            "relative_path": relative_path,
            "source_sha256": hashlib.sha256(module_name.encode()).hexdigest(),
        }
        entry.update(self._ledger_overrides.get(module_name, {}))
        return entry

    def assert_verified_module(
        self,
        module_name: str,
        relative_path: str,
        expected_sha256: str,
    ) -> None:
        self.asserted_modules.append(
            (module_name, relative_path, expected_sha256)
        )
        if module_name in self._rejected_modules:
            raise RuntimeError("verified loader ledger rejected")

    def emit_json(self, value: object) -> None:
        self.buffered.append(value)

    def postverify(self) -> None:
        self.postverify_calls += 1

    def preflight_terminal_guard_descriptor(self) -> Mapping[str, object]:
        if self._guard_descriptor is not None:
            return self._guard_descriptor
        runtime_action_config = bootstrap_runtime._FrozenActionConfig(
            {
                "action": self._config["action"],
                "formal_input_root_sha256": self._config[
                    "formal_input_root"
                ],
                "formal_output_root": self._config["formal_output_root"],
                "run_root": self._config["run_root"],
                "run_spec_path": self._config["run_spec_path"],
            }
        )
        runtime_context = object.__new__(
            bootstrap_runtime._TrustedBootstrapContext
        )
        runtime_context._preflight_terminal_guard_descriptor = (  # type: ignore[attr-defined]
            bootstrap_runtime._preflight_terminal_guard_descriptor(
                runtime_action_config
            )
        )
        descriptor = dict(
            bootstrap_runtime._TrustedBootstrapContext.preflight_terminal_guard_descriptor(
                runtime_context
            )
        )
        self._runtime_guard_context = runtime_context
        descriptor.update(self.guard_descriptor_overrides)
        self._guard_descriptor = MappingProxyType(descriptor)
        return self._guard_descriptor

    def acquire_preflight_terminal_guard(
        self,
        descriptor: object,
    ) -> object:
        if descriptor is not self.guard.descriptor:
            raise RuntimeError("terminal guard descriptor identity rejected")
        self.guard_events.append("acquire")
        return self.guard


class _SyntheticNativePreflightTerminalGuard:
    def __init__(self, context: _TrustedBootstrapContext) -> None:
        self.context = context
        self.active = False

    @property
    def descriptor(self) -> Mapping[str, object]:
        return self.context.preflight_terminal_guard_descriptor()

    def __enter__(self) -> _SyntheticNativePreflightTerminalGuard:
        self.active = True
        self.context.guard_events.append("enter")
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        self.context.guard_events.append("release")
        self.active = False

    def bind_initial_snapshot(self, snapshot: object) -> None:
        expected = self.context.guard_initial_snapshot_override
        if expected is not None and snapshot is not expected:
            raise RuntimeError("terminal guard initial identity rejected")
        self.context.guard_events.append("bind-initial")

    def attempt_namespace_mutation(self, mutation: Callable[[], None]) -> None:
        if self.active:
            raise formal.FormalRunSpecError(
                "external native preflight terminal guard denied mutation"
            )
        mutation()

    def terminal_postverify_and_emit(
        self,
        snapshot: object,
        value: object,
    ) -> None:
        self.context.guard_events.append("terminal-atomic")
        attack = self.context.guard_terminal_attack
        if attack is not None:
            self.attempt_namespace_mutation(attack)
        formal._postverify_planned_run_root(snapshot)
        self.context.emit_json(value)


def _frozen_action_config(action: str = "build-spec") -> _FrozenActionConfig:
    return _FrozenActionConfig(
        {
            "action": action,
            "formal_input_root": formal.FORMAL_INPUT_ROOT_SHA256,
            "formal_output_root": str(formal.SPEC_OUTPUT_ROOT),
            "run_root": str(formal.PLANNED_RUN_ROOT),
            "run_spec_path": str(formal.SPEC_OUTPUT_ROOT / "planned.json"),
        }
    )


def _assert_preflight_rejects_postcheck_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mutation: Callable[[Path, Path], None],
    create_run_root: bool = True,
) -> None:
    parent = tmp_path / "planned-parent"
    parent.mkdir()
    run_root = parent / "planned-run"
    if create_run_root:
        run_root.mkdir()
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    events: list[str] = []
    original_build = formal.build_and_verify_candidate

    def raced_build(candidate_runner: object) -> tuple[dict[str, object], bytes]:
        candidate, content = original_build(candidate_runner)
        mutation(parent, run_root)
        return candidate, content

    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "build_and_verify_candidate", raced_build)
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: events.append("ledger"),
    )

    with pytest.raises(formal.FormalRunSpecError, match="planned run-root"):
        formal.trusted_dispatch(context, config)

    assert events == ["ledger"]
    assert context.buffered == []


def _required_existing_package_initializers(
    root: Path,
    relative_paths: tuple[str, ...],
) -> set[str]:
    required: set[str] = set()
    for relative_path in relative_paths:
        parts = relative_path.split("/")
        for depth in range(1, len(parts)):
            candidate = "/".join((*parts[:depth], "__init__.py"))
            if (root / Path(*candidate.split("/"))).is_file():
                required.add(candidate)
    return required


def test_formal_paths_and_authority_inputs_are_frozen_exactly() -> None:
    main = Path(r"E:\AI workspace\quant-signal-lkj")
    assert formal.SPEC_OUTPUT_ROOT == (
        main
        / "data/research_runs/audited_pit_factor_v3_daily_basic_run_spec_v2"
        / "run_specs/sha256"
    )
    assert formal.PLANNED_RUN_ROOT == (
        main
        / "data/research_runs"
        / "audited_pit_factor_v3_daily_basic_collection_v3_development_733"
    )
    assert formal.EXACT_SET_AUTHORITY_INPUTS == {
        "feature_history_frozen_source_attestation_path": str(
            main
            / "data/research_artifacts"
            / "factor_v3_feature_history_frozen_source_attestation_v5"
            / "factor_v3_feature_history_frozen_source_attestations"
            / "sha256/83"
            / "830dfd19373accfca7358e09f2a2084a42ab517ca518f436713bb9926be07d11.json"
        ),
        "expected_feature_history_frozen_source_attestation_sha256": (
            "830dfd19373accfca7358e09f2a2084a42ab517ca518f436713bb9926be07d11"
        ),
        "feature_history_frozen_source_root": (
            r"E:\AI workspace\quant-signal-lkj-factor-v3-feature-history-formal-run"
        ),
        "expected_feature_history_frozen_source_commit": (
            "b8057962f7a9754848994a6cfda9c9bf85e3db89"
        ),
        "feature_history_run_spec_path": str(
            main
            / "data/research_runs/audited_pit_factor_v3_feature_history_run_spec_v1"
            / "run_specs/sha256/1d"
            / "1df06cd4fe351149596ae06326daa8f315e9e640979ad8031483cbbe3ab3f9e3.json"
        ),
        "feature_history_run_root": str(
            main
            / "data/research_runs"
            / "audited_pit_factor_v3_feature_history_collection_v1_development_prewindow_250"
        ),
        "audited_development_universe_sqlite_path": str(
            main
            / "data/research_artifacts/audited_pit_universe_v2"
            / "0f204f883429723a0015cdc36ae373a5157e4557fd94a9c52f50b94f47ba90f4"
            / "metadata.sqlite3"
        ),
        "expected_development_coverage_audit_sha256": (
            "eb999a28591f43cca2111bd609ff71d77eaae2ad0b3471bb2f8da5c1e6b6ceed"
        ),
        "expected_development_artifact_root_sha256": (
            "505400a945973df54b943e22d195ddc3c93734eecbc6e464bb39005049b92380"
        ),
        "expected_development_temporal_contract_sha256": (
            "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
        ),
        "expected_development_temporal_role": "development_4",
        "security_code_transition_evidence_root": str(
            main
            / "data/research_artifacts/security_code_transition_evidence_v1"
        ),
        "expected_security_code_transition_contract_sha256": (
            "685c5bb48f043534e94b7acb941d32dc06bb01e8ae92348f585cb063cffa6b0c"
        ),
    }


def test_formal_review_manifest_covers_the_complete_runtime_import_closure() -> None:
    relative_paths = formal.FORMAL_REVIEW_SOURCE_RELATIVE_PATHS

    assert len(relative_paths) == len(set(relative_paths))
    assert {
        "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
        "scripts/run_factor_v3_daily_basic_formal.py",
    } <= set(relative_paths)
    assert _required_existing_package_initializers(
        formal.FORMAL_WORKTREE_ROOT,
        relative_paths,
    ) <= set(relative_paths)
    assert "app/__init__.py" in relative_paths


def test_formal_bootstrap_modules_have_no_top_level_application_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
        "scripts/run_factor_v3_daily_basic_formal.py",
    ):
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_level_imports.append(node.module or "")
        assert not any(
            name == "app" or name.startswith("app.")
            for name in top_level_imports
        )


def test_shim_dispatch_uses_only_a_preloaded_verified_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _frozen_action_config()
    context = _TrustedBootstrapContext(config)
    calls: list[tuple[object, object]] = []
    builder = SimpleNamespace(
        trusted_dispatch=lambda received_context, received_config: (
            calls.append((received_context, received_config)) or 17
        )
    )
    builder_name = (
        "scripts.build_factor_v3_daily_basic_formal_run_spec"
    )
    monkeypatch.setitem(sys.modules, builder_name, builder)

    result = formal_shim.trusted_dispatch(context, config)

    assert result == 17
    assert calls == [(context, config)]
    assert context.asserted_modules == [
        (
            builder_name,
            "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
            hashlib.sha256(builder_name.encode()).hexdigest(),
        )
    ]
    dispatch_tree = ast.parse(inspect.getsource(formal_shim.trusted_dispatch))
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        for node in ast.walk(dispatch_tree)
    )


def test_direct_formal_entrypoints_reject_untrusted_callers() -> None:
    with pytest.raises(formal.FormalRunSpecError, match="trusted bootstrap"):
        formal.trusted_dispatch({}, {})
    with pytest.raises(formal.FormalRunSpecError, match="trusted bootstrap"):
        formal.main([])
    with pytest.raises(formal.FormalRunSpecError, match="trusted bootstrap"):
        formal.run_locked_runner_cli(["verify"])


def test_external_bootstrap_claim_binds_every_runtime_trust_anchor() -> None:
    commit = "d" * 40
    builder_sha256 = "1" * 64
    shim_sha256 = "2" * 64
    source_manifest = [
        {
            "bytes": 1,
            "path": "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
            "sha256": builder_sha256,
        },
        {
            "bytes": 1,
            "path": "scripts/run_factor_v3_daily_basic_formal.py",
            "sha256": shim_sha256,
        },
    ]
    source_root = hashlib.sha256(
        formal._canonical_bytes(source_manifest)
    ).hexdigest()
    review_evidence = {
        "payload": {
            "formal_input_root_sha256": formal.FORMAL_INPUT_ROOT_SHA256,
            "reviewed_commit": commit,
            "reviewed_source_root_sha256": source_root,
        },
        "payload_sha256": "3" * 64,
        "receipt_sha256": "4" * 64,
    }
    claim = {
        "base_python_executable_path": str(formal.BASE_PYTHON_EXECUTABLE),
        "base_python_executable_sha256": (
            formal.BASE_PYTHON_EXECUTABLE_SHA256
        ),
        "branch": formal.EXPECTED_BRANCH,
        "builder_sha256": builder_sha256,
        "formal_input_root_sha256": formal.FORMAL_INPUT_ROOT_SHA256,
        "git_executable_path": str(formal.GIT_EXECUTABLE),
        "git_executable_sha256": formal.GIT_EXECUTABLE_SHA256,
        "project_id": "quant-signal-lkj",
        "python_executable_path": str(formal.PYTHON_EXECUTABLE),
        "python_executable_sha256": formal.PYTHON_EXECUTABLE_SHA256,
        "review_payload_sha256": review_evidence["payload_sha256"],
        "review_public_key_spki_sha256": (
            formal.FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256
        ),
        "review_receipt_sha256": review_evidence["receipt_sha256"],
        "reviewed_commit": commit,
        "reviewed_source_root_sha256": source_root,
        "schema": "factor-v3-daily-basic-formal-bootstrap-claim/v1",
        "shim_sha256": shim_sha256,
    }
    raw = formal._canonical_bytes(claim)
    digest = hashlib.sha256(raw).hexdigest()

    assert formal._validated_external_bootstrap_claim(
        raw,
        expected_claim_sha256=digest,
        expected_commit=commit,
        expected_source_manifest=source_manifest,
        review_evidence=review_evidence,
    ) == claim

    for rejected in (
        {key: value for key, value in claim.items() if key != "shim_sha256"},
        {**claim, "review_receipt_sha256": "5" * 64},
        {**claim, "python_executable_path": "C:\\attacker\\python.exe"},
    ):
        rejected_raw = formal._canonical_bytes(rejected)
        with pytest.raises(formal.FormalRunSpecError, match="bootstrap"):
            formal._validated_external_bootstrap_claim(
                rejected_raw,
                expected_claim_sha256=hashlib.sha256(
                    rejected_raw
                ).hexdigest(),
                expected_commit=commit,
                expected_source_manifest=source_manifest,
                review_evidence=review_evidence,
            )
    with pytest.raises(formal.FormalRunSpecError, match="bootstrap"):
        formal._validated_external_bootstrap_claim(
            raw,
            expected_claim_sha256="0" * 64,
            expected_commit=commit,
            expected_source_manifest=source_manifest,
            review_evidence=review_evidence,
        )


def test_offline_candidate_uses_exact_inputs_builds_twice_and_validates_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRunner(_candidate())

    monkeypatch.setattr(
        tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight must not create temporary files")
        ),
    )
    candidate, content = formal.build_and_verify_candidate(fake)

    assert candidate["session_count"] == 733
    assert candidate["sessions"][0] == "2023-06-26"
    assert candidate["sessions"][-1] == "2026-07-03"
    assert len(fake.build_calls) == 2
    assert fake.build_calls == [
        {
            "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
            "timeout_seconds": 30,
            "max_attempts": 3,
        },
        {
            "exact_set_authority_inputs": dict(formal.EXACT_SET_AUTHORITY_INPUTS),
            "timeout_seconds": 30,
            "max_attempts": 3,
        },
    ]
    assert fake.validate_calls == [content]
    assert content == json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert not content.endswith(b"\n")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(session_count=732), "733"),
        (lambda value: value["sessions"].pop(), "733"),
        (lambda value: value["sessions"].__setitem__(0, "2023-06-25"), "2023-06-26"),
        (lambda value: value["sessions"].__setitem__(-1, "2026-07-02"), "2026-07-03"),
        (lambda value: value["collector"].update(workers=2), "collector"),
        (lambda value: value.update(token="forbidden"), "credential"),
        (lambda value: value.update(publication_capability="forbidden"), "credential"),
    ],
)
def test_offline_candidate_rejects_nonformal_shape(
    mutation: object, match: str
) -> None:
    candidate = _candidate()
    mutation(candidate)

    with pytest.raises(formal.FormalRunSpecError, match=match):
        formal.build_and_verify_candidate(_FakeRunner(candidate))


def test_content_addressed_publish_has_no_trailing_lf_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(formal, "SPEC_OUTPUT_ROOT", tmp_path / "specs" / "sha256")
    content = b'{"safe":true}'
    expected_sha256 = hashlib.sha256(content).hexdigest()

    target = formal.publish_candidate(content)

    assert target == (
        tmp_path / "specs" / "sha256" / expected_sha256[:2] / f"{expected_sha256}.json"
    )
    assert target.read_bytes() == content
    assert formal.publish_candidate(content) == target

    target.write_bytes(b'{"safe":false}')
    with pytest.raises(formal.FormalRunSpecError, match="different bytes"):
        formal.publish_candidate(content)


def test_planned_run_root_must_be_absent_or_strictly_empty_without_sidecars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_root = tmp_path / "formal-run"
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)

    formal.verify_planned_run_root()
    absent_snapshot = formal._planned_run_root_snapshot()
    formal._postverify_planned_run_root(absent_snapshot)
    run_root.mkdir()
    formal.verify_planned_run_root()
    empty_snapshot = formal._planned_run_root_snapshot()
    formal._postverify_planned_run_root(empty_snapshot)

    (run_root / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(formal.FormalRunSpecError, match="not empty"):
        formal.verify_planned_run_root()
    (run_root / "state.json").unlink()

    sidecar = run_root.with_name(f"{run_root.name}.stdout.log")
    sidecar.write_text("", encoding="utf-8")
    with pytest.raises(formal.FormalRunSpecError, match="sidecar"):
        formal.verify_planned_run_root()


def test_formal_worktree_requires_exact_branch_and_clean_status(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_commit = "d" * 40
    responses = {
        ("rev-parse", "--show-toplevel"): str(formal.FORMAL_WORKTREE_ROOT),
        ("rev-parse", "HEAD"): reviewed_commit,
        ("branch", "--show-current"): formal.EXPECTED_BRANCH,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    monkeypatch.setattr(formal, "_git_output", lambda *args: responses[args])
    monkeypatch.setattr(
        formal,
        "_script_worktree_root",
        lambda: formal.FORMAL_WORKTREE_ROOT,
    )
    source_manifest = [
        {
            "bytes": 1,
            "path": "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
            "sha256": "f" * 64,
        }
    ]
    reviewed_source = hashlib.sha256(
        formal._canonical_bytes(source_manifest)
    ).hexdigest()
    receipt_sha256 = "b" * 64
    claim_raw = formal._canonical_bytes(
        {"review_receipt_sha256": receipt_sha256}
    )
    claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
    monkeypatch.setattr(
        formal,
        "_validated_formal_review_receipt",
        lambda **_kwargs: {
            "payload": {
                "reviewed_commit": reviewed_commit,
                "reviewed_source_root_sha256": reviewed_source,
            },
            "payload_sha256": "c" * 64,
            "receipt_sha256": receipt_sha256,
        },
    )
    monkeypatch.setattr(
        formal,
        "_validated_external_bootstrap_claim",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        formal,
        "_formal_review_source_manifest",
        lambda: source_manifest,
    )
    monkeypatch.setattr(
        formal,
        "_bootstrap_claim_path",
        lambda _digest: Path("ignored-bootstrap-claim.json"),
    )
    monkeypatch.setattr(
        formal,
        "_read_safe_file",
        lambda *_args, **_kwargs: claim_raw,
    )
    formal.verify_formal_worktree(
        expected_bootstrap_claim_sha256=claim_sha256
    )

    responses[("rev-parse", "HEAD")] = "not-a-commit"
    with pytest.raises(formal.FormalRunSpecError, match="commit"):
        formal.verify_formal_worktree(
            expected_bootstrap_claim_sha256=claim_sha256
        )
    responses[("rev-parse", "HEAD")] = reviewed_commit

    responses[("status", "--porcelain=v1", "--untracked-files=all")] = " M unsafe.py"
    with pytest.raises(formal.FormalRunSpecError, match="dirty"):
        formal.verify_formal_worktree(
            expected_bootstrap_claim_sha256=claim_sha256
        )


def test_formal_worktree_requires_external_content_addressed_anchor() -> None:
    parameter = inspect.signature(
        formal.verify_formal_worktree
    ).parameters["expected_bootstrap_claim_sha256"]
    assert parameter.default is inspect.Parameter.empty
    assert not hasattr(formal, "FORMAL_REVIEW_RECEIPT_SHA256")
    assert formal.FORMAL_REVIEW_RECEIPT_ROOT == (
        formal.MAIN_REPO_ROOT
        / "data/research_artifacts/factor_v3_daily_basic_formal_review_v3"
        / "review_receipts/sha256"
    )
    assert formal.FORMAL_REVIEW_PUBLIC_KEY_PATH == (
        formal.MAIN_REPO_ROOT
        / ".secrets/factor_v3_formal_review_rsa3072_public.pem"
    )
    assert formal.FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256 == (
        "552852331cd6c7b0b08483b21c85fcc63b6ea9787c0c5ae8e146b246b46daaee"
    )


def test_rsa3072_pkcs1_v1_5_sha256_verifier_accepts_only_exact_signature(
    tmp_path: Path,
) -> None:
    payload = b'{"review":"test-only-rsa3072-vector"}'
    private_key, public_der = _test_rsa3072_key(
        tmp_path,
        "pure-verifier",
    )
    signature = _test_rsa_signature(
        tmp_path,
        label="pure-verifier",
        private_key=private_key,
        payload=payload,
    )
    modulus, exponent = formal._parse_rsa3072_spki_der(public_der)

    assert formal._verify_rsa3072_pkcs1_v1_5_sha256(
        payload,
        signature,
        modulus=modulus,
        exponent=exponent,
    )
    for rejected in (
        bytes([signature[0] ^ 1]) + signature[1:],
        signature[:-1],
        b"\x00" * 384,
    ):
        with pytest.raises(formal.FormalRunSpecError, match="signature"):
            formal._verify_rsa3072_pkcs1_v1_5_sha256(
                payload,
                rejected,
                modulus=modulus,
                exponent=exponent,
            )
    with pytest.raises(formal.FormalRunSpecError, match="signature"):
        formal._verify_rsa3072_pkcs1_v1_5_sha256(
            payload + b" ",
            signature,
            modulus=modulus,
            exponent=exponent,
        )


def test_signed_review_receipt_contract_rejects_attacker_controlled_shapes() -> None:
    source = inspect.getsource(
        formal._validated_formal_review_receipt
    ) + inspect.getsource(formal._validated_signed_review_receipt)

    assert "signature_base64" in source
    assert "reviewed_commit" in source
    assert "reviewed_source_manifest" in source
    assert "reviewed_source_root_sha256" in source
    assert "APPROVED_NO_P0_P1_P2" in source
    assert "reviewer_key_id" in source
    assert "review_protocol_sha256" in source
    assert "base64.b64decode" in source
    assert "validate=True" in source
    assert "FORMAL_REVIEW_PUBLIC_KEY_PATH" in source
    assert "FORMAL_REVIEW_PUBLIC_KEY_SPKI_SHA256" in source
    assert "FORMAL_REVIEW_RECEIPT_SHA256" not in source


def test_signed_review_receipt_rejects_replay_mutation_and_attacker_key(
    tmp_path: Path,
) -> None:
    trusted_private, trusted_der = _test_rsa3072_key(
        tmp_path,
        "trusted-reviewer",
    )
    attacker_private, _attacker_der = _test_rsa3072_key(
        tmp_path,
        "attacker",
    )
    source_manifest = [
        {
            "bytes": 7,
            "path": "scripts/reviewed.py",
            "sha256": hashlib.sha256(b"reviewed").hexdigest(),
        }
    ]
    source_root = hashlib.sha256(
        formal._canonical_bytes(source_manifest)
    ).hexdigest()
    commit = "d" * 40
    input_root = "e" * 64
    key_id = f"sha256:{hashlib.sha256(trusted_der).hexdigest()}"
    payload = {
        "branch": formal.EXPECTED_BRANCH,
        "decision": "APPROVED_NO_P0_P1_P2",
        "feature_attestation_sha256": formal.FEATURE_HISTORY_ATTESTATION_SHA256,
        "formal_input_root_sha256": input_root,
        "formal_runner_sha256": formal.FACTOR_V3_DAILY_BASIC_RUNNER_SHA256,
        "issued_at_utc": "2026-07-30T00:00:00+00:00",
        "project_id": "quant-signal-lkj",
        "review_nonce_sha256": "f" * 64,
        "review_protocol_sha256": formal.FORMAL_REVIEW_PROTOCOL_SHA256,
        "reviewed_commit": commit,
        "reviewed_source_manifest": source_manifest,
        "reviewed_source_root_sha256": source_root,
        "reviewer_key_id": key_id,
        "schema": "factor-v3-daily-basic-formal-review-signed-payload/v1",
        "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
    }

    def receipt_raw(
        value: dict[str, object],
        *,
        private_key: Path = trusted_private,
    ) -> bytes:
        payload_raw = formal._canonical_bytes(value)
        signature = _test_rsa_signature(
            tmp_path,
            label=hashlib.sha256(payload_raw).hexdigest(),
            private_key=private_key,
            payload=payload_raw,
        )
        return formal._canonical_bytes(
            {
                "payload": value,
                "signature_base64": base64.b64encode(signature).decode(
                    "ascii"
                ),
            }
        )

    assert formal._validated_signed_review_receipt(
        receipt_raw(payload),
        expected_commit=commit,
        expected_source_manifest=source_manifest,
        expected_formal_input_root_sha256=input_root,
        trusted_public_key_der=trusted_der,
    ) == payload

    mutations = []
    for field, replacement in (
        ("decision", "REJECTED"),
        ("reviewed_commit", "a" * 40),
        ("reviewed_source_root_sha256", "b" * 64),
        ("formal_input_root_sha256", "c" * 64),
    ):
        mutated = deepcopy(payload)
        mutated[field] = replacement
        mutations.append(receipt_raw(mutated))
    mutated_manifest = deepcopy(payload)
    mutated_manifest["reviewed_source_manifest"][0]["path"] = (
        "scripts/unreviewed.py"
    )
    mutations.append(receipt_raw(mutated_manifest))
    signed_secret = deepcopy(payload)
    signed_secret["token"] = "forbidden-even-when-signed"
    mutations.append(receipt_raw(signed_secret))
    mutations.append(receipt_raw(payload, private_key=attacker_private))
    valid_raw = receipt_raw(payload)
    mutations.extend(
        (
            valid_raw + b"\n",
            valid_raw.replace(
                b'{"payload":',
                b'{"payload":{},"payload":',
                1,
            ),
            valid_raw.replace(b'"signature_base64":"', b'"signature_base64":" ', 1),
        )
    )
    for rejected in mutations:
        with pytest.raises(
            formal.FormalRunSpecError,
            match="review|signature|credential",
        ):
            formal._validated_signed_review_receipt(
                rejected,
                expected_commit=commit,
                expected_source_manifest=source_manifest,
                expected_formal_input_root_sha256=input_root,
                trusted_public_key_der=trusted_der,
            )


def test_formal_git_executable_sha_is_frozen() -> None:
    assert formal.GIT_EXECUTABLE_SHA256 == (
        "c39b1b4f7a57935bbeadf246dc2466316619453a6a9da77c4a9c6bd6d8fb21d3"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink executable contract")
def test_formal_git_executable_allows_a_normal_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    raw = b"formal-git-hardlink"
    executable.write_bytes(raw)
    os.link(executable, tmp_path / "git-copy.exe")
    monkeypatch.setattr(formal, "GIT_EXECUTABLE", executable)
    monkeypatch.setattr(
        formal,
        "GIT_EXECUTABLE_SHA256",
        hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        formal.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="ok\n",
            stderr="",
            returncode=0,
        ),
    )

    assert formal._git_output("rev-parse", "HEAD") == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write/delete contract")
def test_formal_git_handle_denies_transient_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (tmp_path / "git.exe").resolve()
    original = b"pinned-git"
    executable.write_bytes(original)
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"transient-git")
    monkeypatch.setattr(formal, "GIT_EXECUTABLE", executable)
    monkeypatch.setattr(
        formal,
        "GIT_EXECUTABLE_SHA256",
        hashlib.sha256(original).hexdigest(),
        raising=False,
    )
    blocked = False

    def transient_replace(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal blocked
        try:
            os.replace(replacement, executable)
        except PermissionError:
            blocked = True
        else:
            executable.write_bytes(original)
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(formal.subprocess, "run", transient_replace)

    assert formal._git_output("rev-parse", "HEAD") == "ok"
    assert blocked is True
    assert executable.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows formal lock contract")
def test_formal_runtime_holds_source_claim_receipt_key_and_executables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = (tmp_path / "worktree").resolve()
    scripts = worktree / "scripts"
    scripts.mkdir(parents=True)
    source = scripts / "bootstrap.py"
    source_raw = b"BOOTSTRAP = True\n"
    source.write_bytes(source_raw)
    public_key = (tmp_path / "review-public.pem").resolve()
    public_raw = b"test-only-public-key"
    public_key.write_bytes(public_raw)
    git = (tmp_path / "git.exe").resolve()
    python = (tmp_path / "python.exe").resolve()
    base_python = (tmp_path / "base-python.exe").resolve()
    executable_bytes = {
        git: b"git",
        python: b"python",
        base_python: b"base-python",
    }
    for path, raw in executable_bytes.items():
        path.write_bytes(raw)
        os.link(path, path.with_name(f"{path.stem}-copy{path.suffix}"))
    receipt_raw = b'{"test":"receipt"}'
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    receipt_root = (tmp_path / "receipts" / "sha256").resolve()
    receipt_shard = receipt_root / receipt_sha256[:2]
    receipt_shard.mkdir(parents=True)
    receipt = receipt_shard / f"{receipt_sha256}.json"
    receipt.write_bytes(receipt_raw)
    claim_raw = formal._canonical_bytes(
        {"review_receipt_sha256": receipt_sha256}
    )
    claim_sha256 = hashlib.sha256(claim_raw).hexdigest()
    claim_root = (tmp_path / "claims" / "sha256").resolve()
    claim_shard = claim_root / claim_sha256[:2]
    claim_shard.mkdir(parents=True)
    claim = claim_shard / f"{claim_sha256}.json"
    claim.write_bytes(claim_raw)
    monkeypatch.setattr(formal, "FORMAL_WORKTREE_ROOT", worktree)
    monkeypatch.setattr(
        formal,
        "FORMAL_REVIEW_SOURCE_RELATIVE_PATHS",
        ("scripts/bootstrap.py",),
    )
    monkeypatch.setattr(formal, "FORMAL_REVIEW_PUBLIC_KEY_PATH", public_key)
    monkeypatch.setattr(formal, "FORMAL_REVIEW_RECEIPT_ROOT", receipt_root)
    monkeypatch.setattr(formal, "FORMAL_BOOTSTRAP_CLAIM_ROOT", claim_root)
    monkeypatch.setattr(formal, "GIT_EXECUTABLE", git)
    monkeypatch.setattr(
        formal,
        "GIT_EXECUTABLE_SHA256",
        hashlib.sha256(executable_bytes[git]).hexdigest(),
    )
    monkeypatch.setattr(formal, "PYTHON_EXECUTABLE", python)
    monkeypatch.setattr(
        formal,
        "PYTHON_EXECUTABLE_SHA256",
        hashlib.sha256(executable_bytes[python]).hexdigest(),
    )
    monkeypatch.setattr(formal, "BASE_PYTHON_EXECUTABLE", base_python)
    monkeypatch.setattr(
        formal,
        "BASE_PYTHON_EXECUTABLE_SHA256",
        hashlib.sha256(executable_bytes[base_python]).hexdigest(),
    )

    with formal._locked_formal_review_sources(
        expected_bootstrap_claim_sha256=claim_sha256
    ) as locked:
        assert locked["source_manifest"] == [
            {
                "bytes": len(source_raw),
                "path": "scripts/bootstrap.py",
                "sha256": hashlib.sha256(source_raw).hexdigest(),
            }
        ]
        for path in (
            source,
            public_key,
            claim,
            receipt,
            git,
            python,
            base_python,
        ):
            with pytest.raises(PermissionError):
                path.write_bytes(b"transient replacement")

    assert source.read_bytes() == source_raw
    assert claim.read_bytes() == claim_raw
    assert receipt.read_bytes() == receipt_raw


def test_formal_runner_load_requires_external_verified_loader_ledger() -> None:
    source = (
        inspect.getsource(formal._load_runner)
        + inspect.getsource(formal._validated_verified_ledger_entry)
    )

    assert "assert_verified_module" in source
    assert "verified_ledger_entry" in source
    assert "FACTOR_V3_DAILY_BASIC_RUNNER_SHA256" not in source


def test_formal_dispatch_requires_exact_trusted_context_api() -> None:
    signature = inspect.signature(formal.trusted_dispatch)

    assert tuple(signature.parameters) == (
        "context",
        "frozen_action_config",
    )
    source = (
        inspect.getsource(formal.trusted_dispatch)
        + inspect.getsource(formal._guarded_preflight_or_build)
        + inspect.getsource(formal._validated_preflight_terminal_guard)
        + inspect.getsource(formal._emit_trusted_json)
    )
    assert "_validated_trusted_action_config" in source
    assert "_postverify_verified_module_ledger" in source
    assert "acquire_preflight_terminal_guard" in source
    assert "preflight_terminal_guard_descriptor" in source
    assert "terminal_postverify_and_emit" in source
    assert "emit_json" in source
    assert "print(" not in source
    assert "runner.main" not in source


def test_success_output_is_buffered_until_external_terminal_postverify(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _frozen_action_config()
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    events: list[str] = []
    original_emit = context.emit_json

    def emit_json(value: object) -> None:
        events.append("emit")
        original_emit(value)

    context.emit_json = emit_json
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    planned_snapshot = object()
    monkeypatch.setattr(
        formal,
        "_planned_run_root_snapshot",
        lambda: planned_snapshot,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda snapshot: (
            events.append("run-root-terminal")
            if snapshot is planned_snapshot
            else (_ for _ in ()).throw(AssertionError("unexpected snapshot"))
        ),
    )
    monkeypatch.setattr(
        formal,
        "publish_candidate",
        lambda _content: formal.SPEC_OUTPUT_ROOT / "candidate.json",
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: events.append("ledger"),
    )

    assert formal.trusted_dispatch(context, config) == 0

    assert events == ["ledger", "run-root-terminal", "emit"]
    assert context.guard_events == [
        "acquire",
        "enter",
        "bind-initial",
        "terminal-atomic",
        "release",
    ]
    assert len(context.buffered) == 1
    assert context.postverify_calls == 0
    assert capsys.readouterr() == ("", "")


def test_preflight_dispatch_never_publishes_runs_or_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    events: list[str] = []
    planned_snapshot = object()
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)

    def postverify_planned_run_root(snapshot: object) -> None:
        assert snapshot is planned_snapshot
        events.append("run-root-terminal-verified")

    monkeypatch.setattr(
        formal,
        "_planned_run_root_snapshot",
        lambda: (
            events.append("run-root-readonly-snapshotted")
            or planned_snapshot
        ),
    )
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        postverify_planned_run_root,
    )
    monkeypatch.setattr(
        formal,
        "publish_candidate",
        lambda _content: (_ for _ in ()).throw(
            AssertionError("preflight must not publish")
        ),
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: events.append("ledger"),
    )

    assert formal.trusted_dispatch(context, config) == 0

    assert events == [
        "run-root-readonly-snapshotted",
        "ledger",
        "run-root-terminal-verified",
    ]
    assert context.guard_events == [
        "acquire",
        "enter",
        "bind-initial",
        "terminal-atomic",
        "release",
    ]
    assert runner.validate_calls
    assert context.buffered == [
        formal.safe_summary(
            _candidate(),
            runner.validate_calls[0],
            published=False,
        )
    ]


def test_runtime_context_descriptor_enters_builder_guard_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    snapshot = object()
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(
        formal,
        "_planned_run_root_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda candidate: (
            None
            if candidate is snapshot
            else (_ for _ in ()).throw(AssertionError("unexpected snapshot"))
        ),
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    descriptor = context.preflight_terminal_guard_descriptor()
    assert set(descriptor) == {
        "acquire_before",
        "action",
        "atomic_terminal_operation",
        "hold_until",
        "parent_path",
        "protected_actions",
        "protected_path_field",
        "provider_identity",
        "run_root",
        "schema",
        "worker_binding_environment",
        "write_policy",
    }
    assert context._runtime_guard_context is not None
    assert formal.trusted_dispatch(context, config) == 0
    assert context.guard_events == [
        "acquire",
        "enter",
        "bind-initial",
        "terminal-atomic",
        "release",
    ]


def test_preflight_rejects_run_root_file_created_after_initial_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(_parent: Path, run_root: Path) -> None:
        (run_root / "hostile-after-check.json").write_text(
            "{}",
            encoding="utf-8",
        )

    _assert_preflight_rejects_postcheck_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_preflight_rejects_sidecar_created_after_initial_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(_parent: Path, run_root: Path) -> None:
        run_root.with_name(f"{run_root.name}.stdout.log").write_text(
            "",
            encoding="utf-8",
        )

    _assert_preflight_rejects_postcheck_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_preflight_rejects_case_variant_win32_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "planned-parent"
    parent.mkdir()
    run_root = parent / "planned-run"
    run_root.mkdir()
    run_root.with_name("PLANNED-RUN.stdout.log").write_bytes(b"")
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)

    with pytest.raises(formal.FormalRunSpecError, match="sidecar"):
        formal.verify_planned_run_root()


def test_preflight_rejects_missing_external_native_terminal_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    context.acquire_preflight_terminal_guard = None  # type: ignore[method-assign]
    runner = _FakeRunner(_candidate())
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "_planned_run_root_snapshot", object)
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda _snapshot: None,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    with pytest.raises(
        formal.FormalRunSpecError,
        match="trusted bootstrap context",
    ):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider_identity", "python-fake-guard/v1"),
        ("run_root", r"E:\hostile\other-run-root"),
        ("parent_path", r"E:\hostile"),
        ("hold_until", "worker-buffer-only"),
    ),
)
def test_preflight_rejects_untrusted_or_misbound_terminal_guard_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    context.guard_descriptor_overrides[field] = value
    runner = _FakeRunner(_candidate())
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "_planned_run_root_snapshot", object)
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda _snapshot: None,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    with pytest.raises(formal.FormalRunSpecError, match="terminal guard"):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []


def test_preflight_rejects_terminal_guard_initial_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    context.guard_initial_snapshot_override = object()
    runner = _FakeRunner(_candidate())
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "_planned_run_root_snapshot", object)
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda _snapshot: None,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    with pytest.raises(formal.FormalRunSpecError, match="terminal guard"):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []


def _assert_preflight_guard_rejects_transient_namespace_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    mutation: Callable[[Path, Path], None],
    create_run_root: bool = True,
) -> None:
    parent = tmp_path / "planned-parent"
    parent.mkdir()
    run_root = parent / "planned-run"
    if create_run_root:
        run_root.mkdir()
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    original_build = formal.build_and_verify_candidate

    def raced_build(candidate_runner: object) -> tuple[dict[str, object], bytes]:
        candidate, content = original_build(candidate_runner)
        context.guard.attempt_namespace_mutation(
            lambda: mutation(parent, run_root)
        )
        return candidate, content

    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "build_and_verify_candidate", raced_build)
    stable_snapshot = object()
    monkeypatch.setattr(
        formal,
        "_planned_run_root_snapshot",
        lambda: stable_snapshot,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda snapshot: (
            None
            if snapshot is stable_snapshot
            else (_ for _ in ()).throw(AssertionError("unexpected snapshot"))
        ),
    )
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    with pytest.raises(formal.FormalRunSpecError, match="guard"):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []


def test_preflight_guard_rejects_transient_file_and_sidecar_create_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(parent: Path, run_root: Path) -> None:
        transient = run_root / "transient.json"
        transient.write_bytes(b"{}")
        transient.unlink()
        sidecar = parent / f".{run_root.name}.TRANSIENT"
        sidecar.write_bytes(b"")
        sidecar.unlink()

    _assert_preflight_guard_rejects_transient_namespace_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_preflight_guard_rejects_transient_run_root_aba(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(_parent: Path, run_root: Path) -> None:
        run_root.rmdir()
        run_root.mkdir()

    _assert_preflight_guard_rejects_transient_namespace_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_preflight_guard_rejects_transient_parent_aba(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(parent: Path, _run_root: Path) -> None:
        parent.rmdir()
        parent.mkdir()

    _assert_preflight_guard_rejects_transient_namespace_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
        create_run_root=False,
    )


def test_preflight_guard_rejects_final_check_to_success_emit_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "planned-parent"
    parent.mkdir()
    run_root = parent / "planned-run"
    run_root.mkdir()
    monkeypatch.setattr(formal, "PLANNED_RUN_ROOT", run_root)
    config = _frozen_action_config("preflight")
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    original_emit = formal._emit_trusted_json

    def attack() -> None:
        (run_root / "after-terminal-before-emit.json").write_bytes(b"{}")

    def raced_emit(candidate_context: object, value: object) -> None:
        attack()
        original_emit(candidate_context, value)

    context.guard_terminal_attack = attack
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(formal, "_emit_trusted_json", raced_emit)
    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        lambda _context, _manifest: None,
    )

    with pytest.raises(formal.FormalRunSpecError, match="guard"):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []
    assert list(run_root.iterdir()) == []


def test_preflight_rejects_empty_run_root_replaced_after_initial_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(_parent: Path, run_root: Path) -> None:
        run_root.rmdir()
        run_root.mkdir()

    _assert_preflight_rejects_postcheck_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_preflight_rejects_empty_parent_replaced_after_initial_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def mutate(parent: Path, _run_root: Path) -> None:
        parent.rmdir()
        parent.mkdir()

    _assert_preflight_rejects_postcheck_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
        create_run_root=False,
    )


def test_preflight_rejects_run_root_reparse_after_initial_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_is_reparse = formal._is_reparse

    def mutate(_parent: Path, run_root: Path) -> None:
        monkeypatch.setattr(
            formal,
            "_is_reparse",
            lambda path: (
                Path(path) == run_root
                or original_is_reparse(Path(path))
            ),
        )

    _assert_preflight_rejects_postcheck_mutation(
        monkeypatch,
        tmp_path,
        mutation=mutate,
    )


def test_failed_terminal_ledger_proof_emits_no_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _frozen_action_config()
    context = _TrustedBootstrapContext(config)
    runner = _FakeRunner(_candidate())
    monkeypatch.setattr(formal, "_load_runner", lambda _context: runner)
    monkeypatch.setattr(
        formal,
        "_planned_run_root_snapshot",
        object,
    )
    monkeypatch.setattr(
        formal,
        "_postverify_planned_run_root",
        lambda _snapshot: (_ for _ in ()).throw(
            AssertionError("run-root proof must follow the ledger proof")
        ),
    )
    monkeypatch.setattr(
        formal,
        "publish_candidate",
        lambda _content: formal.SPEC_OUTPUT_ROOT / "candidate.json",
    )

    def reject_ledger(_context: object, _manifest: object) -> None:
        raise formal.FormalRunSpecError("formal verified loader ledger rejected")

    monkeypatch.setattr(
        formal,
        "_postverify_verified_module_ledger",
        reject_ledger,
    )

    with pytest.raises(formal.FormalRunSpecError, match="ledger"):
        formal.trusted_dispatch(context, config)

    assert context.buffered == []
    assert capsys.readouterr() == ("", "")


def test_formal_spec_publication_reuses_safe_cas_primitives() -> None:
    source = inspect.getsource(formal.publish_candidate)

    assert "raw_authority._content_addressed_directory" in source
    assert "raw_authority._write_create_only" in source
    assert "raw_authority._read_safe_file" in source
    assert "fsync_directory" in source


def test_fixed_runtime_identity_requires_python_base_python_isolation_and_pycache() -> None:
    source = inspect.getsource(formal._validated_fixed_runtime_identity)

    assert "sys.executable" in source
    assert "sys._base_executable" in source
    assert "PYTHON_EXECUTABLE_SHA256" in source
    assert "BASE_PYTHON_EXECUTABLE_SHA256" in source
    assert "sys.flags.isolated" in source
    assert "sys.dont_write_bytecode" in source
    assert "sys.pycache_prefix" in source


@pytest.mark.skipif(os.name != "nt", reason="Windows executable identity contract")
def test_fixed_runtime_identity_allows_normal_python_hardlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = (tmp_path / "python.exe").resolve()
    base_python = (tmp_path / "base-python.exe").resolve()
    python_raw = b"fixed-python"
    base_raw = b"fixed-base-python"
    python.write_bytes(python_raw)
    base_python.write_bytes(base_raw)
    os.link(python, tmp_path / "python-copy.exe")
    os.link(base_python, tmp_path / "base-python-copy.exe")
    pycache = (tmp_path / "isolated-pycache").resolve()
    pycache.mkdir()
    monkeypatch.setattr(formal, "PYTHON_EXECUTABLE", python)
    monkeypatch.setattr(
        formal,
        "PYTHON_EXECUTABLE_SHA256",
        hashlib.sha256(python_raw).hexdigest(),
    )
    monkeypatch.setattr(formal, "BASE_PYTHON_EXECUTABLE", base_python)
    monkeypatch.setattr(
        formal,
        "BASE_PYTHON_EXECUTABLE_SHA256",
        hashlib.sha256(base_raw).hexdigest(),
    )
    monkeypatch.setattr(
        formal,
        "sys",
        SimpleNamespace(
            _base_executable=str(base_python),
            dont_write_bytecode=True,
            executable=str(python),
            flags=SimpleNamespace(isolated=1),
            modules={},
            pycache_prefix=str(pycache),
        ),
    )

    identity = formal._validated_fixed_runtime_identity()

    assert identity["python_executable_path"] == str(python)
    assert identity["base_python_executable_path"] == str(base_python)


def test_fixed_runtime_identity_accepts_the_real_isolated_interpreter() -> None:
    root = Path(__file__).resolve().parents[1]
    program = (
        "import sys,tempfile;"
        "temporary=tempfile.TemporaryDirectory("
        "prefix='factor-v3-runtime-test-');"
        "sys.pycache_prefix=temporary.name;"
        f"sys.path.insert(0,{str(root)!r});"
        "from scripts import "
        "build_factor_v3_daily_basic_formal_run_spec as formal;"
        "formal._validated_fixed_runtime_identity();"
        "print('verified')"
    )
    completed = subprocess.run(
        [
            str(formal.PYTHON_EXECUTABLE),
            "-I",
            "-B",
            "-c",
            program,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "verified\n"
    assert completed.stderr == ""


def test_loaded_application_modules_require_external_ledger_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve()
    app_root = root / "app"
    app_root.mkdir()
    package_source = app_root / "__init__.py"
    package_raw = b"PACKAGE = 1\n"
    package_source.write_bytes(package_raw)
    source = app_root / "reviewed.py"
    raw = b"VALUE = 1\n"
    source.write_bytes(raw)
    manifest = [
        {
            "bytes": len(package_raw),
            "path": "app/__init__.py",
            "sha256": hashlib.sha256(package_raw).hexdigest(),
        },
        {
            "bytes": len(raw),
            "path": "app/reviewed.py",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    ]
    config = _frozen_action_config()
    context = _TrustedBootstrapContext(
        config,
        ledger_overrides={
            "app": {
                "byte_count": len(package_raw),
                "source_sha256": hashlib.sha256(package_raw).hexdigest(),
            },
            "app.reviewed": {
                "byte_count": len(raw),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            },
        },
        rejected_modules={"app.reviewed"},
    )
    monkeypatch.setitem(
        formal.sys.modules,
        "app",
        SimpleNamespace(__file__=str(package_source)),
    )
    monkeypatch.setitem(
        formal.sys.modules,
        "app.reviewed",
        SimpleNamespace(__file__=str(source)),
    )

    with pytest.raises(formal.FormalRunSpecError, match="ledger"):
        formal._postverify_verified_module_ledger(context, manifest)
    assert {item[0] for item in context.asserted_modules} == {
        "app",
        "app.reviewed",
    }


def test_custom_meta_path_and_forged_file_cannot_replace_ledger_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (tmp_path / "reviewed.py").resolve()
    raw = b"VALUE = 1\n"
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = [{"bytes": len(raw), "path": "app/reviewed.py", "sha256": digest}]
    forged = SimpleNamespace(
        __file__=str(source),
        __loader__=SimpleNamespace(name="attacker"),
        __spec__=SimpleNamespace(origin=str(source)),
    )
    finder = SimpleNamespace(find_spec=lambda *_args: forged.__spec__)
    monkeypatch.setitem(formal.sys.modules, "app.reviewed", forged)
    monkeypatch.setattr(formal.sys, "meta_path", [finder])
    config = _frozen_action_config()
    context = _TrustedBootstrapContext(
        config,
        ledger_overrides={
            "app.reviewed": {
                "byte_count": len(raw),
                "source_sha256": digest,
            },
        },
        rejected_modules={"app.reviewed"},
    )

    with pytest.raises(formal.FormalRunSpecError, match="ledger"):
        formal._postverify_verified_module_ledger(context, manifest)


def test_cli_shim_rejects_a_nonfrozen_python_interpreter(
    tmp_path: Path,
) -> None:
    shim = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_factor_v3_daily_basic_formal.py"
    )
    completed = subprocess.run(
        [
            str(formal.BASE_PYTHON_EXECUTABLE),
            "-I",
            "-B",
            str(shim),
            "--bootstrap-claim-sha256",
            "a" * 64,
            "verify",
            "--run-spec",
            str(tmp_path / "absent-spec.json"),
            "--run-root",
            str(tmp_path / "absent-run"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
    )

    assert completed.returncode == 2
    assert "fixed Python -I -B" in completed.stderr
    assert "run spec unavailable" not in completed.stderr


def test_isolated_cli_shim_fails_closed_without_external_bootstrap_anchor(
    tmp_path: Path,
) -> None:
    shim = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_factor_v3_daily_basic_formal.py"
    )
    completed = subprocess.run(
        [
            str(formal.PYTHON_EXECUTABLE),
            "-I",
            "-B",
            str(shim),
            "verify",
            "--run-spec",
            str(tmp_path / "absent-spec.json"),
            "--run-root",
            str(tmp_path / "absent-run"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
    )

    assert completed.returncode == 2
    assert "No module named" not in completed.stderr
    assert completed.stderr in {
        "formal bootstrap requires fixed Python -I -B\n",
        "formal bootstrap claim unavailable; external trusted bootstrap context required\n",
    }
    assert completed.stdout == ""


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
def test_formal_credential_shape_rejects_private_key_variants(
    forbidden: str,
) -> None:
    with pytest.raises(formal.FormalRunSpecError, match="credential"):
        formal._assert_no_credential_shape({forbidden: "not-a-real-secret"})


def test_safe_summary_excludes_inputs_credentials_and_capabilities() -> None:
    summary = formal.safe_summary(
        _candidate(),
        b'{"safe":true}',
        published=False,
    )
    encoded = json.dumps(summary, sort_keys=True).lower()

    assert summary["status"] == "preflight-verified"
    assert summary["session_count"] == 733
    assert "token" not in encoded
    assert "credential" not in encoded
    assert "capability" not in encoded
    assert "exact_set_authority_inputs" not in encoded
