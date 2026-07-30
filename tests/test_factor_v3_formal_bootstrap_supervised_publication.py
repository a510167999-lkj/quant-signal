from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
from pathlib import Path
import subprocess

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer
from app import factor_v3_formal_bootstrap_runtime as runtime
from app import factor_v3_formal_control_contract as contract
from tests.test_factor_v3_formal_bootstrap_authorization import (
    _authorized_fixture,
    _render_authorized,
    _run_as_synthetic_supervisor,
    _write_completion_authorization,
)


PRODUCTION_EXECUTION_PUBLIC_KEY_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\.secrets"
    r"\factor_v3_execution_authorization_rsa3072_public.pem"
)
PRODUCTION_EXECUTION_PUBLIC_KEY_SPKI_SHA256 = (
    "70c8ad8f74cddfe363175d76c433cb145aadcd7cbd8af669768e697d99cccce8"
)
SUPERVISOR_PROTOCOL = contract.WORKER_PROTOCOL
WORKER_TERMINAL_SCHEMA = contract.WORKER_TERMINAL_SCHEMA


def test_production_renderer_has_a_fixed_execution_authorization_key() -> None:
    parameters = inspect.signature(
        renderer.render_factor_v3_formal_bootstrap_from_authorization
    ).parameters

    assert set(parameters) == {"authorization_path"}
    assert (
        renderer.PRODUCTION_EXECUTION_AUTHORIZATION_PUBLIC_KEY_PATH
        == PRODUCTION_EXECUTION_PUBLIC_KEY_PATH
    )
    assert (
        renderer.PRODUCTION_EXECUTION_AUTHORIZATION_SPKI_SHA256
        == PRODUCTION_EXECUTION_PUBLIC_KEY_SPKI_SHA256
    )
    public_der = renderer._production_execution_authorization_public_key_der()
    assert hashlib.sha256(public_der).hexdigest() == (PRODUCTION_EXECUTION_PUBLIC_KEY_SPKI_SHA256)


def test_execution_authorization_v2_freezes_replay_and_key_roles() -> None:
    assert renderer.AUTHORIZATION_SCHEMA == (
        "factor-v3-formal-bootstrap-execution-authorization/v2"
    )
    assert {
        "authorization_id_sha256",
        "authorization_nonce_sha256",
        "execution_authorization_key_id",
        "execution_authorization_key_role",
        "expires_at_utc",
        "issued_at_utc",
        "not_before_utc",
        "replay_scope",
        "stdlib_policy",
        "supervisor_protocol",
    }.issubset(renderer._AUTHORIZATION_FIELDS)


def test_review_and_execution_authorization_key_roles_must_be_distinct() -> None:
    digest = "1" * 64

    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="key roles",
    ):
        renderer._validate_distinct_authorization_key_roles(
            review_public_key_spki_sha256=digest,
            execution_public_key_spki_sha256=digest,
        )


def test_execution_authorization_replay_window_is_fail_closed() -> None:
    window = {
        "authorization_id_sha256": "1" * 64,
        "authorization_nonce_sha256": "2" * 64,
        "expires_at_utc": "2026-07-30T12:10:00+00:00",
        "issued_at_utc": "2026-07-30T12:00:00+00:00",
        "not_before_utc": "2026-07-30T12:00:00+00:00",
        "replay_scope": "factor-v3-formal-bootstrap-execution/v1",
    }

    assert (
        renderer._validated_replay_claim(
            window,
            now_utc="2026-07-30T12:05:00+00:00",
        )
        == window
    )
    for now_utc in (
        "2026-07-30T11:59:59+00:00",
        "2026-07-30T12:10:01+00:00",
    ):
        with pytest.raises(
            renderer.FormalBootstrapRenderError,
            match="replay",
        ):
            renderer._validated_replay_claim(window, now_utc=now_utc)


def test_stdlib_policy_uses_exact_roots_and_content_inventory(tmp_path: Path) -> None:
    pycache_prefix = (tmp_path / "pycache").resolve()
    pycache_prefix.mkdir()
    policy = renderer._trusted_stdlib_policy_for_base_python(
        Path(__import__("sys")._base_executable),
        pycache_prefix,
    )

    assert policy["schema"] == contract.STDLIB_POLICY_SCHEMA
    assert set(policy) == {
        "absent_paths",
        "entries",
        "pycache_prefix",
        "roots",
        "schema",
    }
    assert [item["role"] for item in policy["roots"]] == [
        "platstdlib",
        "stdlib",
    ]
    assert all(set(item) == {"path", "role"} for item in policy["roots"])
    entries = policy["entries"]
    assert entries
    assert all(
        set(entry)
        == {
            "bytes",
            "is_package",
            "kind",
            "module",
            "path",
            "relative_path",
            "root",
            "sha256",
        }
        for entry in entries
    )
    assert all(
        (entry["kind"] == "dll" or isinstance(entry["module"], str))
        and entry["kind"] in {"dll", "extension", "source"}
        and len(entry["sha256"]) == 64
        for entry in entries
    )
    assert policy["absent_paths"]
    assert policy["pycache_prefix"] == str(pycache_prefix)


def _overlong_execution_replay_claim() -> dict[str, str]:
    return {
        "authorization_id_sha256": "1" * 64,
        "authorization_nonce_sha256": "2" * 64,
        "expires_at_utc": "2026-07-31T12:00:01+00:00",
        "issued_at_utc": "2026-07-30T12:00:00+00:00",
        "not_before_utc": "2026-07-30T12:00:00+00:00",
        "replay_scope": "factor-v3-formal-bootstrap-execution/v1",
    }


def test_execution_authorization_lifetime_is_capped_in_renderer() -> None:
    with pytest.raises(
        renderer.FormalBootstrapRenderError,
        match="replay",
    ):
        renderer._validated_replay_claim(
            _overlong_execution_replay_claim(),
            now_utc="2026-07-30T12:05:00+00:00",
        )


def test_execution_authorization_lifetime_is_capped_in_worker() -> None:
    with pytest.raises(runtime._BootstrapError, match="replay"):
        runtime._validated_replay_claim(
            _overlong_execution_replay_claim(),
            now_utc=__import__("datetime").datetime.fromisoformat("2026-07-30T12:05:00+00:00"),
        )


def test_runtime_template_identity_is_canonical_lf_and_rejects_mixed_eol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_lf = (
        Path(renderer.__file__)
        .resolve()
        .with_name("factor_v3_formal_bootstrap_runtime.py")
        .read_bytes()
        .replace(b"\r\n", b"\n")
    )
    runtime_crlf = runtime_lf.replace(b"\n", b"\r\n")
    expected_sha256 = hashlib.sha256(runtime_lf).hexdigest()
    monkeypatch.setattr(renderer, "RUNTIME_TEMPLATE_SHA256", expected_sha256)

    assert renderer._canonical_runtime_template_bytes(runtime_lf) == runtime_lf
    assert renderer._canonical_runtime_template_bytes(runtime_crlf) == runtime_lf
    for invalid in (
        b"first\rsecond\r",
        b"first\r\nsecond\n",
        b"first\nsecond\r\n",
    ):
        with pytest.raises(
            renderer.FormalBootstrapRenderError,
            match="line endings",
        ):
            renderer._canonical_runtime_template_bytes(invalid)

    monkeypatch.setattr(
        renderer,
        "_read_safe_file",
        lambda *_args, **_kwargs: runtime_crlf,
    )
    assert renderer._runtime_template_bytes() == runtime_lf


def test_win32_directory_handle_chain_blocks_parent_replacement(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "publication-root").resolve()
    shard = root / "bootstraps" / "sha256" / "ab"
    shard.mkdir(parents=True)
    replacement = root / "replacement"

    with renderer._held_win32_directory_chain(
        root=root,
        directories=(root, shard.parent.parent, shard.parent, shard),
    ) as identities:
        assert identities[-1]["path"] == str(shard)
        assert all(item["reparse"] is False for item in identities)
        with pytest.raises(OSError):
            shard.rename(replacement)


def test_win32_directory_chain_holds_every_ancestor_from_volume_root(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "ancestor" / "publication-root").resolve()
    shard = root / "bootstraps" / "sha256" / "ab"
    shard.mkdir(parents=True)
    expected_paths: list[str] = []
    current = Path(root.anchor)
    expected_paths.append(str(current))
    for part in root.parts[1:]:
        current /= part
        expected_paths.append(str(current))
    for part in ("bootstraps", "sha256", "ab"):
        current /= part
        expected_paths.append(str(current))

    with renderer._held_win32_directory_chain(
        root=root,
        directories=(root, shard),
    ) as identities:
        assert [item["path"] for item in identities] == expected_paths
        with pytest.raises(OSError):
            (tmp_path / "ancestor").rename(tmp_path / "replacement")


def test_publish_requires_a_signed_completion_marker() -> None:
    parameters = inspect.signature(renderer.publish_factor_v3_formal_bootstrap).parameters

    assert set(parameters) == {
        "authorization_path",
        "completion_authorization_path",
    }
    assert "trusted_public_key_spki_der" not in parameters
    assert hasattr(
        renderer,
        "plan_factor_v3_formal_bootstrap_publication",
    )
    selection_parameters = inspect.signature(
        renderer.validate_factor_v3_formal_bootstrap_completion_marker
    ).parameters
    assert set(selection_parameters) == {"completion_marker_path"}


def test_invalid_completion_leaves_no_selectable_bootstrap(
    tmp_path: Path,
) -> None:
    (
        _config,
        payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    invalid_completion = tmp_path / "invalid-completion.json"
    invalid_completion.write_text("{}", encoding="utf-8")

    with pytest.raises(renderer.FormalBootstrapRenderError):
        renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
            authorization_path=authorization_path,
            completion_authorization_path=invalid_completion,
            trusted_public_key_spki_der=trusted_public_der,
        )

    output_root = Path(str(payload["bootstrap_output_root"]))
    assert list(output_root.iterdir()) == []


def test_completion_marker_is_the_last_and_only_selection_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    categories: list[str] = []

    @contextmanager
    def fake_tree(**_kwargs: object):
        yield ()

    def fake_publish(
        *,
        category: str,
        raw: bytes,
        **_kwargs: object,
    ) -> tuple[Path, str]:
        categories.append(category)
        return Path(f"{category}.json"), f"{category}.json"

    monkeypatch.setattr(
        renderer,
        "_held_publication_directory_tree",
        fake_tree,
    )
    monkeypatch.setattr(renderer, "_safe_cas_publish", fake_publish)

    renderer._publish_prevalidated_completion_for_test(
        root=Path(r"C:\synthetic-publication-root"),
        bootstrap_raw=b"bootstrap",
        receipt_raw=b"receipt",
        stdlib_policy_raw=b"stdlib-policy",
        completion_raw=b"completion",
    )

    assert categories == [
        "bootstraps",
        "publication_receipts",
        "stdlib_policies",
        "completion_markers",
    ]


def test_publication_holds_all_files_until_marker_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    completion_payload = renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    completion_path = _write_completion_authorization(
        tmp_path,
        completion_payload,
    )
    original_publish = renderer._safe_cas_publish
    published_paths: dict[str, Path] = {}
    mutation_blocked: dict[str, bool] = {}
    flushed: list[Path] = []

    def observed_publish(**kwargs: object) -> tuple[Path, str]:
        category = str(kwargs["category"])
        result = original_publish(**kwargs)
        published_paths[category] = result[0]
        if category == "completion_markers":
            for name, path in published_paths.items():
                try:
                    path.write_bytes(f"tampered-{name}".encode())
                except OSError:
                    mutation_blocked[name] = True
                else:
                    mutation_blocked[name] = False
        return result

    def observed_flush(*, path: Path, handle: object) -> None:
        del handle
        flushed.append(path)

    monkeypatch.setattr(renderer, "_safe_cas_publish", observed_publish)
    monkeypatch.setattr(
        renderer,
        "_flush_held_win32_directory",
        observed_flush,
        raising=False,
    )

    publication = renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )

    assert mutation_blocked == {
        "bootstraps": True,
        "completion_markers": True,
        "publication_receipts": True,
        "stdlib_policies": True,
    }
    assert {
        Path(publication["bootstrap_path"]).parent,
        Path(publication["completion_marker_path"]).parent,
        Path(publication["receipt_path"]).parent,
        Path(publication["stdlib_policy_path"]).parent,
    }.issubset(set(flushed))


def test_only_a_signed_completion_marker_can_select_a_bootstrap(
    tmp_path: Path,
) -> None:
    (
        _config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    completion_payload = renderer._plan_factor_v3_formal_bootstrap_publication_with_test_trust(
        authorization_path=authorization_path,
        trusted_public_key_spki_der=trusted_public_der,
    )
    completion_path = _write_completion_authorization(
        tmp_path,
        completion_payload,
    )
    publication = renderer._publish_factor_v3_formal_bootstrap_with_test_trust(
        authorization_path=authorization_path,
        completion_authorization_path=completion_path,
        trusted_public_key_spki_der=trusted_public_der,
    )

    selection = renderer._validate_factor_v3_formal_bootstrap_completion_marker_with_test_trust(
        completion_marker_path=publication["completion_marker_path"],
        trusted_public_key_spki_der=trusted_public_der,
    )

    assert selection["bootstrap_path"] == publication["bootstrap_path"]
    assert selection["receipt_path"] == publication["receipt_path"]
    assert selection["completion_marker_path"] == publication["completion_marker_path"]
    for unselectable in (
        publication["bootstrap_path"],
        publication["receipt_path"],
    ):
        with pytest.raises(
            renderer.FormalBootstrapRenderError,
            match="completion",
        ):
            renderer._validate_factor_v3_formal_bootstrap_completion_marker_with_test_trust(
                completion_marker_path=unselectable,
                trusted_public_key_spki_der=trusted_public_der,
            )


def test_saved_stdout_descriptors_cannot_escape_worker_capture(
    tmp_path: Path,
) -> None:
    dispatch_body = (
        "    context.validate_action_config(frozen_action_config)\n"
        "    os = __import__('os')\n"
        "    for descriptor in range(3, 128):\n"
        "        try:\n"
        "            os.write(descriptor, b'saved-fd-escape')\n"
        "        except OSError:\n"
        "            pass\n"
        "    context.emit_json({'status': 'must-not-escape'})\n"
        "    return 0\n"
    )
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path, dispatch_body=dispatch_body)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_as_synthetic_supervisor(rendered, config, tmp_path)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "saved-fd-escape" not in completed.stderr


def test_supervisor_protocol_and_terminal_frame_are_exact() -> None:
    descriptor = runtime._supervisor_protocol_descriptor()
    assert descriptor == contract.worker_protocol_descriptor()
    assert descriptor["protocol"] == SUPERVISOR_PROTOCOL
    assert descriptor["terminal_schema"] == WORKER_TERMINAL_SCHEMA
    assert descriptor["fixed_environment"] == contract.FIXED_ENVIRONMENT
    assert descriptor["terminal_fields"] == contract.WORKER_TERMINAL_FIELDS


def test_synthetic_supervisor_receives_one_canonical_terminal_frame(
    tmp_path: Path,
) -> None:
    (
        config,
        payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_as_synthetic_supervisor(
        rendered,
        config,
        tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.endswith("\n")
    assert completed.stdout.count("\n") == 1
    frame = json.loads(completed.stdout)
    assert set(frame) == set(contract.WORKER_TERMINAL_FIELDS)
    assert frame == {
        "artifacts": [],
        "authorization_nonce_sha256": payload["authorization_nonce_sha256"],
        "bootstrap_execution_authorization_sha256": hashlib.sha256(
            authorization_path.read_bytes()
        ).hexdigest(),
        "launch_action": "verify",
        "launch_authorization_sha256": hashlib.sha256(
            b"synthetic-supervisor-launch-envelope"
        ).hexdigest(),
        "result": {
            "action": "verify",
            "fake_file_rejected": True,
            "formal_input_root": payload["formal_input_root_sha256"],
            "loader_identity": "factor-v3-verified-source-loader/v1",
            "nonverified_loader_rejected": True,
            "rejected_unreviewed": True,
            "replacement_blocked": True,
            "source_registry_entries": 37,
            "status": "fixture-verified",
            "unloaded_assert_rejected": True,
            "unloaded_ledger": True,
        },
        "schema": WORKER_TERMINAL_SCHEMA,
        "status": "completed",
        "stdlib_inventory_root_sha256": payload["stdlib_policy_root_sha256"],
        "worker_action": "verify",
    }
    assert completed.stdout.encode("utf-8") == (
        json.dumps(
            frame,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


@pytest.mark.parametrize(
    ("launch_action", "extra_environment"),
    (
        ("verify", {"JIAOCH_TOKEN": "must-not-be-present"}),
        ("verify", {"UNREVIEWED": "rejected"}),
        ("run", {}),
    ),
)
def test_supervisor_environment_policy_is_action_exact(
    launch_action: str,
    extra_environment: dict[str, str],
) -> None:
    environment = {
        "FACTOR_V3_FORMAL_LAUNCH_ACTION": launch_action,
        "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256": "1" * 64,
        "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL": SUPERVISOR_PROTOCOL,
        "SYSTEMROOT": r"C:\Windows",
        **extra_environment,
    }

    with pytest.raises(runtime._BootstrapError, match="environment"):
        runtime._validated_supervisor_environment(
            environment,
            worker_action="run" if launch_action == "resume" else launch_action,
        )


def test_worker_rejects_missing_stdlib_prelock_proof(
    tmp_path: Path,
) -> None:
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    rendered = _render_authorized(authorization_path, trusted_public_der)

    completed = _run_as_synthetic_supervisor(
        rendered,
        config,
        tmp_path,
        include_stdlib_prelock=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_terminal_artifacts_are_exact_sorted_and_root_bounded(
    tmp_path: Path,
) -> None:
    formal_output_root = (tmp_path / "formal-output").resolve()
    run_root = (tmp_path / "run-root").resolve()
    formal_output_root.mkdir()
    run_root.mkdir()
    first = formal_output_root / "a.json"
    second = run_root / "z.json"
    first.write_bytes(b"a")
    second.write_bytes(b"zz")

    artifacts = runtime._validated_terminal_artifacts(
        [str(second), str(first)],
        formal_output_root=formal_output_root,
        run_root=run_root,
    )

    assert artifacts == [
        {
            "bytes": 1,
            "path": str(first),
            "sha256": hashlib.sha256(b"a").hexdigest(),
        },
        {
            "bytes": 2,
            "path": str(second),
            "sha256": hashlib.sha256(b"zz").hexdigest(),
        },
    ]
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"x")
    with pytest.raises(runtime._BootstrapError, match="artifact"):
        runtime._validated_terminal_artifacts(
            [str(outside)],
            formal_output_root=formal_output_root,
            run_root=run_root,
        )


def test_direct_worker_execution_requires_supervisor_context(
    tmp_path: Path,
) -> None:
    (
        config,
        _payload,
        authorization_path,
        trusted_public_der,
    ) = _authorized_fixture(tmp_path)
    rendered = _render_authorized(authorization_path, trusted_public_der)
    bootstrap_path = (tmp_path / "direct-bootstrap.py").resolve()
    bootstrap_path.write_bytes(rendered)

    completed = subprocess.run(
        [
            str(config["python_executable_path"]),
            "-I",
            "-B",
            "-S",
            "-X",
            f"pycache_prefix={config['_test_stdlib_pycache_prefix']}",
            str(bootstrap_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={},
        timeout=60,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "factor-v3 formal bootstrap rejected\n"
