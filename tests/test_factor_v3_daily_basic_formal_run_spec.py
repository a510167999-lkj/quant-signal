from __future__ import annotations

import ast
import base64
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import build_factor_v3_daily_basic_formal_run_spec as formal


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
        self.load_calls: list[Path] = []

    def build_factor_v3_daily_basic_run_spec(self, **kwargs: object) -> dict[str, object]:
        self.build_calls.append(kwargs)
        return json.loads(json.dumps(self.candidate))

    def load_factor_v3_daily_basic_run_spec(self, path: str | Path) -> dict[str, object]:
        candidate_path = Path(path)
        self.load_calls.append(candidate_path)
        assert not candidate_path.read_bytes().endswith(b"\n")
        return json.loads(candidate_path.read_text(encoding="utf-8"))


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
        / "audited_pit_factor_v3_daily_basic_collection_v2_development_733"
    )
    assert formal.EXACT_SET_AUTHORITY_INPUTS == {
        "feature_history_frozen_source_attestation_path": str(
            main
            / "data/research_artifacts"
            / "factor_v3_feature_history_frozen_source_attestation_v3"
            / "factor_v3_feature_history_frozen_source_attestations"
            / "sha256/68"
            / "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675.json"
        ),
        "expected_feature_history_frozen_source_attestation_sha256": (
            "68d08661ee0f7a1e216c5ec4c9cbb49ea35193488b1cd50b276e9d72eb9ef675"
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
    assert formal.FORMAL_REVIEW_SOURCE_RELATIVE_PATHS == (
        "scripts/build_factor_v3_daily_basic_formal_run_spec.py",
        "scripts/run_factor_v3_daily_basic_formal.py",
        "app/audited_pit_factor_v3_feature_history_authority.py",
        "app/audited_pit_factor_v3_points_contract.py",
        "app/current_pool.py",
        "app/current_pool_gate.py",
        "app/durable_io.py",
        "app/factor_v3_daily_basic_733_exact_set_authority.py",
        "app/factor_v3_daily_basic_runner.py",
        "app/factor_v3_feature_history_frozen_source_attestation.py",
        "app/factor_v3_feature_history_runner.py",
        "app/jiaoch_credential_slots.py",
        "app/jiaoch_daily_basic_collection_set.py",
        "app/jiaoch_daily_basic_exact_set_authority.py",
        "app/jiaoch_minute_collection_set.py",
        "app/jiaoch_minute_raw_authority.py",
        "app/jiaoch_minute_reconciliation.py",
        "app/jiaoch_points_collection_set.py",
        "app/jiaoch_points_raw_authority.py",
        "app/jiaoch_points_response_normalization.py",
        "app/jiaoch_trade_cal_authority.py",
        "app/research_market_data.py",
        "app/research_membership.py",
        "app/research_partitions.py",
        "app/research_pit_collector.py",
        "app/research_pit_contracts.py",
        "app/research_pit_sources.py",
        "app/research_pit_store.py",
        "app/research_pit_transport.py",
        "app/research_provider_evidence_partitions.py",
        "app/research_provider_pit_tail.py",
        "app/research_provider_pit_tail_v2.py",
        "app/research_proxy_data.py",
        "app/research_scope.py",
        "app/research_security_code_transition.py",
        "app/research_suspension_evidence.py",
    )


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


def test_offline_candidate_uses_exact_inputs_builds_twice_and_loads_once() -> None:
    fake = _FakeRunner(_candidate())

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
    assert len(fake.load_calls) == 1
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
    run_root.mkdir()
    formal.verify_planned_run_root()

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
    reviewed_source = "f" * 64
    monkeypatch.setattr(
        formal,
        "_validated_formal_review_receipt",
        lambda: {
            "reviewed_commit": reviewed_commit,
            "reviewed_source_root_sha256": reviewed_source,
        },
    )
    monkeypatch.setattr(
        formal,
        "_formal_review_source_root",
        lambda: reviewed_source,
    )
    formal.verify_formal_worktree()

    responses[("rev-parse", "HEAD")] = "not-a-commit"
    with pytest.raises(formal.FormalRunSpecError, match="commit"):
        formal.verify_formal_worktree()
    responses[("rev-parse", "HEAD")] = reviewed_commit

    responses[("status", "--porcelain=v1", "--untracked-files=all")] = " M unsafe.py"
    with pytest.raises(formal.FormalRunSpecError, match="dirty"):
        formal.verify_formal_worktree()


def test_formal_worktree_review_anchor_is_not_caller_supplied() -> None:
    assert inspect.signature(formal.verify_formal_worktree).parameters == {}
    assert not hasattr(formal, "FORMAL_REVIEW_RECEIPT_SHA256")
    assert formal.FORMAL_REVIEW_RECEIPT_ROOT == (
        formal.MAIN_REPO_ROOT
        / "data/research_artifacts/factor_v3_daily_basic_formal_review_v1"
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


def test_formal_runner_load_binds_physical_source_through_import_and_postverify() -> None:
    source = inspect.getsource(formal._load_runner)

    assert "FACTOR_V3_DAILY_BASIC_RUNNER_SHA256" in source
    assert "_open_pinned_file" in source
    assert "_postverify_pinned_file" in source
    assert "_postverify_loaded_review_modules" in inspect.getsource(
        formal.run_locked_runner_cli
    )


def test_formal_build_and_run_require_external_anchor_and_hold_complete_review() -> None:
    source = inspect.getsource(formal.main)
    signature = inspect.signature(formal.main)

    assert signature.parameters[
        "expected_bootstrap_claim_sha256"
    ].default is inspect.Parameter.empty
    assert "_validated_fixed_runtime_identity" in source
    assert "_locked_formal_review_sources" in source
    assert source.index("_locked_formal_review_sources") < source.index(
        "verify_formal_worktree"
    )
    assert source.index("build_and_verify_candidate") < source.index(
        "publish_candidate"
    )
    runner_source = inspect.getsource(formal.run_locked_runner_cli)
    assert "_validated_fixed_runtime_identity" in runner_source
    assert "_locked_formal_review_sources" in runner_source
    assert runner_source.index("_locked_formal_review_sources") < (
        runner_source.index("verify_formal_worktree")
    )
    assert runner_source.index("verify_formal_worktree") < runner_source.index(
        "_load_runner"
    )
    assert runner_source.index("_load_runner") < runner_source.index(
        "runner.main"
    )


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
            sys.executable,
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
