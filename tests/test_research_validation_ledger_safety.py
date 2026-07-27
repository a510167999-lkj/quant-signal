import hashlib
import json
import os
import threading
from collections import UserDict
from contextlib import contextmanager
from pathlib import Path

import pytest

from app import research_validation as rv


def _event(experiment_id: str, event_type: str, **extra):
    return {
        "event_id": f"{experiment_id}:{event_type}",
        "experiment_id": experiment_id,
        "event_type": event_type,
        **extra,
    }


def test_preregistered_experiment_cannot_skip_atomic_claim(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(
        str(ledger),
        _event(
            "pre",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v1"},
        ),
    )

    with pytest.raises(ValueError, match="registered -> completed"):
        rv.append_experiment_event(str(ledger), _event("pre", "completed"))

    assert [row["event_type"] for row in rv.read_experiment_ledger(str(ledger))] == [
        "registered"
    ]


def test_legacy_registered_experiment_can_still_complete(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("legacy", "registered"))
    rv.append_experiment_event(str(ledger), _event("legacy", "completed"))

    assert [row["event_type"] for row in rv.read_experiment_ledger(str(ledger))] == [
        "registered",
        "completed",
    ]


def test_orphan_ledger_lock_is_rejected_without_bootstrap_repair(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lock = Path(f"{ledger}.lock")
    lock.write_bytes(b"\0")

    with pytest.raises(ValueError, match="bootstrap state is inconsistent"):
        rv.read_experiment_ledger(str(ledger))

    assert ledger.exists() is False
    assert lock.read_bytes() == b"\0"


def test_reading_a_missing_ledger_does_not_create_an_orphan_lock(tmp_path):
    ledger = tmp_path / "ledger.jsonl"

    assert rv.read_experiment_ledger(str(ledger)) == []

    assert ledger.exists() is False
    assert Path(f"{ledger}.lock").exists() is False


def test_posix_bootstrap_writes_a_nonempty_lock_marker(tmp_path, monkeypatch):
    class FakeFcntl:
        LOCK_EX = 1
        LOCK_SH = 2
        LOCK_UN = 8

        @staticmethod
        def flock(_fd, _operation):
            return None

    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(rv, "_fcntl", FakeFcntl)
    monkeypatch.setattr(rv, "_msvcrt", None)

    rv.append_experiment_event(str(ledger), _event("bootstrap", "registered"))

    assert Path(f"{ledger}.lock").read_bytes() == b"\0"


def test_existing_ledger_rejects_a_tampered_lock_marker_without_writing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("first", "registered"))
    lock = Path(f"{ledger}.lock")
    lock.write_bytes(b"tampered")
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="ledger lock marker is invalid"):
        rv.append_experiment_event(str(ledger), _event("second", "registered"))

    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"event_id": "missing-experiment", "event_type": "registered"}, "experiment_id"),
        (
            {
                "event_id": "first-completed",
                "experiment_id": "first",
                "event_type": "completed",
            },
            "experiment must be registered first",
        ),
    ],
)
def test_invalid_first_event_does_not_leave_an_orphan_lock(tmp_path, event, message):
    ledger = tmp_path / "ledger.jsonl"

    with pytest.raises(ValueError, match=message):
        rv.append_experiment_event(str(ledger), event)

    assert ledger.exists() is False
    assert Path(f"{ledger}.lock").exists() is False


def test_ledger_lock_replacement_between_identity_check_and_open_is_rejected(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    lock = Path(f"{ledger}.lock")
    lock.write_bytes(b"\0")
    before = ledger.read_bytes()
    original_open = rv._safe_open_fd
    replacement = tmp_path / "replacement.lock"
    replaced = False

    def replace_before_open(path, flags, *, label, mode=0o600):
        nonlocal replaced
        if Path(path) == lock and not replaced:
            replacement.write_bytes(b"\0")
            os.replace(replacement, lock)
            replaced = True
        return original_open(path, flags, label=label, mode=mode)

    monkeypatch.setattr(rv, "_safe_open_fd", replace_before_open)

    with pytest.raises(ValueError, match="ledger lock path identity was replaced"):
        with rv._ledger_lock(ledger, exclusive=True):
            pass

    assert replaced is True
    assert ledger.read_bytes() == before


def test_ledger_replacement_between_identity_check_and_lock_acquisition_is_rejected(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("frozen\n", encoding="utf-8")
    lock = Path(f"{ledger}.lock")
    lock.write_bytes(b"\0")
    replacement = tmp_path / "replacement.jsonl"
    original_open = rv._safe_open_fd
    replaced = False

    def replace_before_lock_open(path, flags, *, label, mode=0o600):
        nonlocal replaced
        if Path(path) == lock and not replaced:
            replacement.write_bytes(ledger.read_bytes())
            os.replace(replacement, ledger)
            replaced = True
        return original_open(path, flags, label=label, mode=mode)

    monkeypatch.setattr(rv, "_safe_open_fd", replace_before_lock_open)

    with pytest.raises(ValueError, match="ledger path identity was replaced"):
        with rv._ledger_lock(ledger, exclusive=True):
            pass

    assert replaced is True


def test_controlled_registration_compares_tip_and_exact_nonterminal_allowlist(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    frozen = rv.append_experiment_event(
        str(ledger),
        _event(
            "frozen-old",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v1"},
        ),
    )
    allowed = [
        {
            "experiment_id": frozen["experiment_id"],
            "sequence": frozen["sequence"],
            "record_hash": frozen["record_hash"],
            "event_type": frozen["event_type"],
        }
    ]

    registered = rv.register_experiment_if_tip_matches(
        str(ledger),
        _event(
            "new-control",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v2"},
        ),
        expected_sequence=frozen["sequence"],
        expected_record_hash=frozen["record_hash"],
        allowed_nonterminal_records=allowed,
        minimum_sequence_exclusive=frozen["sequence"],
    )

    assert registered["sequence"] == frozen["sequence"] + 1
    assert registered["previous_record_hash"] == frozen["record_hash"]


def test_controlled_registration_rejects_stale_tip_without_writing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    tip = rv.append_experiment_event(str(ledger), _event("done", "registered"))
    rv.append_experiment_event(str(ledger), _event("done", "failed"))
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="ledger tip"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            _event("new-control", "registered"),
            expected_sequence=tip["sequence"],
            expected_record_hash=tip["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=tip["sequence"],
        )

    assert ledger.read_bytes() == before


def test_controlled_registration_rejects_same_inode_ledger_mutation_before_append(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("sealed", "registered"))
    tip = rv.append_experiment_event(str(ledger), _event("sealed", "failed"))
    before = ledger.read_bytes()
    original_append = rv._append_experiment_event_locked
    mutated = False

    def mutate_then_append(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            with ledger.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            mutated = True
        return original_append(*args, **kwargs)

    monkeypatch.setattr(rv, "_append_experiment_event_locked", mutate_then_append)

    with pytest.raises(ValueError, match="ledger file contents changed before append"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            _event("new-control", "registered"),
            expected_sequence=tip["sequence"],
            expected_record_hash=tip["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=tip["sequence"],
        )

    assert mutated is True
    assert ledger.read_bytes() == before + b"\n"
    assert [row["experiment_id"] for row in rv.read_experiment_ledger(str(ledger))] == [
        "sealed",
        "sealed",
    ]


def test_controlled_v4_ledger_binding_rejects_a_replaced_guard_identity(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("sealed", "registered"))
    lock = Path(f"{ledger}.lock")
    ledger_stat = ledger.stat()
    lock_stat = lock.stat()
    ledger_path = str(ledger.resolve())
    lock_path = str(lock.resolve())
    registered = {
        "registration_contract": {
            "precompute_ledger": {
                "schema_version": "research-precompute-ledger-binding/v4",
                "ledger_path": ledger_path,
                "ledger_path_sha256": hashlib.sha256(
                    ledger_path.encode("utf-8")
                ).hexdigest(),
                "ledger_device": ledger_stat.st_dev,
                "ledger_inode": ledger_stat.st_ino,
                "lock_path": lock_path,
                "lock_path_sha256": hashlib.sha256(
                    lock_path.encode("utf-8")
                ).hexdigest(),
                "lock_device": lock_stat.st_dev,
                "lock_inode": lock_stat.st_ino,
                "lock_file_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "pre_registration_ledger_file_sha256": hashlib.sha256(
                    ledger.read_bytes()
                ).hexdigest(),
                "expected_tip_sequence": 1,
                "expected_tip_record_hash": "a" * 64,
            }
        }
    }

    with rv._ledger_lock(ledger, exclusive=True) as guard:
        replaced_guard = {
            **guard,
            "ledger_identity": (ledger_stat.st_dev, ledger_stat.st_ino + 1),
        }
        with pytest.raises(ValueError, match="controlled ledger path identity was replaced"):
            rv._assert_controlled_ledger_identity_locked(registered, replaced_guard)


def test_controlled_registration_rejects_ledger_byte_drift_inside_lock(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("sealed", "registered"))
    tip = rv.append_experiment_event(str(ledger), _event("sealed", "failed"))
    frozen = ledger.read_bytes()
    original_lock = rv._ledger_lock
    mutated = False

    @contextmanager
    def mutate_after_lock(*args, **kwargs):
        nonlocal mutated
        with original_lock(*args, **kwargs) as guard:
            if not mutated:
                ledger.write_bytes(frozen + b"\n")
                mutated = True
            yield guard

    monkeypatch.setattr(rv, "_ledger_lock", mutate_after_lock)

    with pytest.raises(ValueError, match="ledger file contents changed"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            _event("new-control", "registered"),
            expected_sequence=tip["sequence"],
            expected_record_hash=tip["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=tip["sequence"],
            expected_ledger_file_sha256=hashlib.sha256(frozen).hexdigest(),
        )

    assert mutated is True
    assert ledger.read_bytes() == frozen + b"\n"
    assert [row["event_type"] for row in rv.read_experiment_ledger(str(ledger))] == [
        "registered",
        "failed",
    ]


def test_controlled_registration_rejects_unexpected_nonterminal_without_writing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    tip = rv.append_experiment_event(
        str(ledger),
        _event(
            "unexpected-open",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v1"},
        ),
    )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="nonterminal"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            _event("new-control", "registered"),
            expected_sequence=tip["sequence"],
            expected_record_hash=tip["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=tip["sequence"],
        )

    assert ledger.read_bytes() == before


def test_controlled_registration_rejects_reused_experiment_id_without_writing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("reused", "registered"))
    tip = rv.append_experiment_event(str(ledger), _event("reused", "failed"))
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="experiment_id"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            _event("reused", "registered"),
            expected_sequence=tip["sequence"],
            expected_record_hash=tip["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=tip["sequence"],
        )

    assert ledger.read_bytes() == before


def test_fail_registered_experiment_is_atomic_and_single_use(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    registered = rv.append_experiment_event(
        str(ledger),
        _event(
            "new-control",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v2"},
        ),
    )

    failed = rv.fail_registered_experiment_if_current(
        str(ledger),
        experiment_id="new-control",
        registered_record_hash=registered["record_hash"],
        failure_code="PRECOMPUTE_LAUNCH_FAILED",
        failure_phase="precompute_launch",
        error_type="LauncherError",
        run_claim_file_sha256="5" * 64,
    )

    assert failed["event_type"] == "failed"
    assert failed["registered_record_hash"] == registered["record_hash"]
    assert failed["run_claim_file_sha256"] == "5" * 64
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="no longer registered"):
        rv.fail_registered_experiment_if_current(
            str(ledger),
            experiment_id="new-control",
            registered_record_hash=registered["record_hash"],
            failure_code="PRECOMPUTE_LAUNCH_FAILED",
            failure_phase="precompute_launch",
            error_type="LauncherError",
            run_claim_file_sha256="5" * 64,
        )
    assert ledger.read_bytes() == before


def test_fail_registered_experiment_rejects_wrong_hash_without_writing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(
        str(ledger),
        _event(
            "new-control",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v2"},
        ),
    )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="registered record"):
        rv.fail_registered_experiment_if_current(
            str(ledger),
            experiment_id="new-control",
            registered_record_hash="0" * 64,
            failure_code="PRECOMPUTE_LAUNCH_FAILED",
            failure_phase="precompute_launch",
            error_type="LauncherError",
            run_claim_file_sha256=None,
        )

    assert ledger.read_bytes() == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_ledger_symlink_is_rejected(tmp_path):
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.symlink_to(target)

    with pytest.raises(ValueError, match="symlink|unsafe"):
        rv.append_experiment_event(str(ledger), _event("unsafe", "registered"))

    assert target.read_text(encoding="utf-8") == ""


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_lock_symlink_is_rejected(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lock_target = tmp_path / "lock-target"
    lock_target.write_text("", encoding="utf-8")
    Path(f"{ledger}.lock").symlink_to(lock_target)

    with pytest.raises(ValueError, match="symlink|unsafe"):
        rv.append_experiment_event(str(ledger), _event("unsafe-lock", "registered"))


def test_windows_lock_backend_is_available_without_fcntl(tmp_path, monkeypatch):
    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_RLCK = 2
        LK_UNLCK = 3

        @staticmethod
        def locking(fd, mode, size):
            calls.append((fd, mode, size))

    monkeypatch.setattr(rv, "_fcntl", None)
    monkeypatch.setattr(rv, "_msvcrt", FakeMsvcrt)

    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("windows", "registered"))

    assert [mode for _fd, mode, size in calls if size == 1] == [
        FakeMsvcrt.LK_LOCK,
        FakeMsvcrt.LK_UNLCK,
    ]


def test_path_identity_change_is_rejected(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("first", "registered"))
    real_read = rv._read_ledger
    replaced = False

    def replace_before_read(path, guard=None):
        nonlocal replaced
        if guard is not None and not replaced:
            replacement = path.with_suffix(".replacement")
            replacement.write_text("", encoding="utf-8")
            os.replace(replacement, path)
            replaced = True
        return real_read(path, guard)

    monkeypatch.setattr(rv, "_read_ledger", replace_before_read)

    with pytest.raises(ValueError, match="replaced|identity|unsafe"):
        rv.append_experiment_event(str(ledger), _event("second", "registered"))

    assert replaced is True
    assert ledger.read_bytes() == b""


class _SchemaSmugglingEvent(dict):
    """Expose a benign contract to routing while retaining a controlled one."""

    def get(self, key, default=None):
        if key == "registration_contract":
            return {"schema_version": "research-validation-registration/v1"}
        return super().get(key, default)


def test_generic_append_freezes_mapping_before_controlled_schema_routing(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    event = _SchemaSmugglingEvent(
        _event(
            "smuggled",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v4"},
        )
    )

    with pytest.raises(ValueError, match="controlled registration"):
        rv.append_experiment_event(str(ledger), event)

    assert ledger.exists() is False
    assert Path(f"{ledger}.lock").exists() is False


def test_generic_append_snapshots_a_recursive_mapping_into_plain_json(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    event = UserDict(
        _event(
            "mapping",
            "registered",
            metadata=UserDict({"state": "frozen"}),
        )
    )

    recorded = rv.append_experiment_event(str(ledger), event)
    event["metadata"]["state"] = "changed"

    assert type(recorded) is dict
    assert type(recorded["metadata"]) is dict
    assert recorded["metadata"] == {"state": "frozen"}
    assert rv.read_experiment_ledger(str(ledger))[0]["metadata"] == {"state": "frozen"}


def test_controlled_registration_freezes_mapping_before_v4_binding(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("prior", "registered"))
    prior = rv.append_experiment_event(str(ledger), _event("prior", "failed"))
    event = _SchemaSmugglingEvent(
        _event(
            "smuggled",
            "registered",
            registration_contract={"schema_version": "research-validation-registration/v4"},
        )
    )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="v4|precompute|registration"):
        rv.register_experiment_if_tip_matches(
            str(ledger),
            event,
            expected_sequence=prior["sequence"],
            expected_record_hash=prior["record_hash"],
            allowed_nonterminal_records=[],
            minimum_sequence_exclusive=prior["sequence"],
        )

    assert ledger.read_bytes() == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_ledger_reader_rejects_non_json_numeric_constants(tmp_path, constant):
    ledger = tmp_path / "ledger.jsonl"
    rv.append_experiment_event(str(ledger), _event("valid", "registered"))
    with ledger.open("ab") as handle:
        encoded = json.dumps({"bad": constant}).replace(f'"{constant}"', constant)
        handle.write(encoded.encode())
        handle.write(b"\n")

    with pytest.raises(ValueError, match="invalid experiment ledger"):
        rv.read_experiment_ledger(str(ledger))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_ledger_json_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError):
        rv._canonical_json({"value": value})


def test_report_artifact_rejects_non_finite_numbers(tmp_path):
    with pytest.raises(ValueError):
        rv.write_report_artifact(str(tmp_path / "reports"), {"value": float("nan")})


def test_concurrent_generic_appends_keep_a_single_valid_hash_chain(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    gate = threading.Barrier(3)
    results = []
    errors = []

    def append(index):
        try:
            gate.wait(timeout=5)
            results.append(
                rv.append_experiment_event(
                    str(ledger), _event(f"parallel-{index}", "registered")
                )
            )
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=append, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    gate.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert sorted(item["sequence"] for item in results) == [1, 2]
    rows = rv.read_experiment_ledger(str(ledger))
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[1]["previous_record_hash"] == rows[0]["record_hash"]
