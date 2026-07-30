from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess

import pytest

from app import factor_v3_formal_bootstrap_renderer as renderer
from app import factor_v3_formal_bootstrap_runtime as runtime
from tests.test_factor_v3_formal_bootstrap_authorization import (
    _authorized_fixture,
    _render_authorized,
)
from tests.test_factor_v3_formal_bootstrap_renderer import _run_rendered


PRODUCTION_EXECUTION_PUBLIC_KEY_PATH = Path(
    r"E:\AI workspace\quant-signal-lkj\.secrets"
    r"\factor_v3_execution_authorization_rsa3072_public.pem"
)
PRODUCTION_EXECUTION_PUBLIC_KEY_SPKI_SHA256 = (
    "3776d27e0553f315fbfbe24e70975efeafe9e84ed4bedd2cc54ea67f597339c2"
)
SUPERVISOR_PROTOCOL = "factor-v3-formal-supervisor-worker/v1"
WORKER_TERMINAL_SCHEMA = "factor-v3-formal-bootstrap-worker-terminal/v1"


def _run_as_synthetic_supervisor(
    rendered: bytes,
    config: dict[str, object],
    tmp_path: Path,
    *,
    launch_action: str = "verify",
) -> subprocess.CompletedProcess[str]:
    bootstrap_sha256 = hashlib.sha256(rendered).hexdigest()
    bootstrap_directory = tmp_path / "synthetic-bootstrap-cas" / "sha256" / bootstrap_sha256[:2]
    bootstrap_directory.mkdir(parents=True)
    bootstrap_path = bootstrap_directory / f"{bootstrap_sha256}.py"
    bootstrap_path.write_bytes(rendered)
    launch_authorization_sha256 = hashlib.sha256(
        b"synthetic-supervisor-launch-envelope"
    ).hexdigest()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    environment.update(
        {
            "FACTOR_V3_FORMAL_LAUNCH_ACTION": launch_action,
            "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256": (launch_authorization_sha256),
            "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL": SUPERVISOR_PROTOCOL,
        }
    )
    if launch_action in {"run", "resume"}:
        environment["JIAOCH_TOKEN"] = "test-only-never-log"
    return subprocess.run(
        [
            str(config["python_executable_path"]),
            "-I",
            "-B",
            "-S",
            str(bootstrap_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
    )


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


def test_stdlib_policy_uses_exact_roots_and_content_inventory() -> None:
    policy = renderer._trusted_stdlib_policy_for_base_python(
        Path(__import__("sys")._base_executable)
    )

    assert policy["schema"] == "factor-v3-bootstrap-stdlib-policy/v1"
    assert set(policy) == {
        "importer_policy_sha256",
        "inventory_root_sha256",
        "roots",
        "schema",
    }
    assert [item["kind"] for item in policy["roots"]] == [
        "stdlib",
        "platstdlib",
        "stdlib_zip",
    ]
    assert all(
        set(item)
        == {
            "inventory_sha256",
            "kind",
            "path",
        }
        for item in policy["roots"]
    )
    assert all(len(item["inventory_sha256"]) == 64 for item in policy["roots"])


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
        assert len(identities) == 4
        assert all(item["reparse"] is False for item in identities)
        with pytest.raises(OSError):
            shard.rename(replacement)


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
    def fake_chain(**_kwargs: object):
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
        "_held_win32_directory_chain",
        fake_chain,
    )
    monkeypatch.setattr(renderer, "_safe_cas_publish", fake_publish)

    renderer._publish_prevalidated_completion_for_test(
        root=Path(r"C:\synthetic-publication-root"),
        bootstrap_raw=b"bootstrap",
        receipt_raw=b"receipt",
        completion_raw=b"completion",
    )

    assert categories == [
        "bootstraps",
        "publication_receipts",
        "completion_markers",
    ]


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

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "saved-fd-escape" not in completed.stderr


def test_supervisor_protocol_and_terminal_frame_are_exact() -> None:
    assert runtime._SUPERVISOR_PROTOCOL == SUPERVISOR_PROTOCOL
    assert runtime._WORKER_TERMINAL_SCHEMA == WORKER_TERMINAL_SCHEMA
    assert runtime._SUPERVISOR_PUBLIC_ENVIRONMENT == frozenset(
        {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    )
    assert runtime._SUPERVISOR_FIXED_ENVIRONMENT == frozenset(
        {
            "FACTOR_V3_FORMAL_LAUNCH_ACTION",
            "FACTOR_V3_FORMAL_LAUNCH_AUTHORIZATION_SHA256",
            "FACTOR_V3_FORMAL_LAUNCH_PROTOCOL",
        }
    )
    assert runtime._SUPERVISOR_ACTION_SECRET_ENVIRONMENT == {
        "build-spec": frozenset(),
        "resume": frozenset({"JIAOCH_TOKEN"}),
        "run": frozenset({"JIAOCH_TOKEN"}),
        "verify": frozenset(),
    }
    assert runtime._WORKER_TERMINAL_FIELDS == frozenset(
        {
            "artifacts",
            "authorization_nonce_sha256",
            "bootstrap_execution_authorization_sha256",
            "launch_action",
            "launch_authorization_sha256",
            "result",
            "schema",
            "status",
            "worker_action",
        }
    )


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
    assert set(frame) == runtime._WORKER_TERMINAL_FIELDS
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

    completed = _run_rendered(rendered, config)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "factor-v3 formal bootstrap rejected\n"
