import pytest

import app.akshare_client as akshare_client_module
from app.akshare_client import akshare_call
from app.storage import read_json


class _ControlledClock:
    def __init__(self, now=0.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def test_akshare_call_retries_and_records_recovery(tmp_path, monkeypatch):
    status_path = tmp_path / "akshare_status.json"
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(status_path))
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary disconnect")
        return "ok"

    result = akshare_call(
        "fake_endpoint",
        flaky,
        attempts=2,
        base_delay_seconds=0,
        jitter_seconds=0,
    )

    status = read_json(str(status_path), {})
    assert result == "ok"
    assert calls["count"] == 2
    assert status["endpoints"]["fake_endpoint"]["status"] == "recovered"
    assert status["endpoints"]["fake_endpoint"]["attempts"] == 2


def test_akshare_call_records_final_failure(tmp_path, monkeypatch):
    status_path = tmp_path / "akshare_status.json"
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(status_path))

    with pytest.raises(RuntimeError):
        akshare_call(
            "broken_endpoint",
            lambda: (_ for _ in ()).throw(RuntimeError("remote closed")),
            attempts=2,
            base_delay_seconds=0,
            jitter_seconds=0,
        )

    status = read_json(str(status_path), {})
    endpoint = status["endpoints"]["broken_endpoint"]
    assert endpoint["status"] == "failed"
    assert endpoint["attempts"] == 2
    assert endpoint["error_type"] == "RuntimeError"


def test_low_retry_endpoint_caps_env_retry_count(tmp_path, monkeypatch):
    status_path = tmp_path / "akshare_status.json"
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(status_path))
    monkeypatch.setenv("AKSHARE_MAX_RETRIES", "5")
    calls = {"count": 0}

    def broken():
        calls["count"] += 1
        raise RuntimeError("still closed")

    with pytest.raises(RuntimeError):
        akshare_call(
            "stock_board_industry_cons_em",
            broken,
            base_delay_seconds=0,
            jitter_seconds=0,
        )

    status = read_json(str(status_path), {})
    assert calls["count"] == 2
    assert status["endpoints"]["stock_board_industry_cons_em"]["attempts"] == 2


def test_akshare_call_honors_elapsed_budget(tmp_path, monkeypatch):
    status_path = tmp_path / "akshare_status.json"
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(status_path))
    calls = {"count": 0}

    def slow_broken():
        calls["count"] += 1
        raise RuntimeError("slow failure")

    with pytest.raises(RuntimeError):
        akshare_call(
            "stock_zh_a_hist",
            slow_broken,
            attempts=5,
            base_delay_seconds=1,
            jitter_seconds=0,
            max_elapsed_seconds=0.01,
        )

    assert calls["count"] <= 2


def test_akshare_call_never_starts_retry_at_absolute_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(tmp_path / "akshare_status.json"))
    clock = _ControlledClock()

    def oversleep_to_deadline(delay):
        clock.sleeps.append(delay)
        clock.now = 10.0

    clock.sleep = oversleep_to_deadline
    monkeypatch.setattr(akshare_client_module, "time", clock)
    calls = {"count": 0}
    failure = RuntimeError("deadline reached")

    def broken():
        calls["count"] += 1
        if calls["count"] == 1:
            clock.now = 4.0
        raise failure

    with pytest.raises(RuntimeError, match="deadline reached") as caught:
        akshare_call(
            "stock_zh_a_hist",
            broken,
            attempts=5,
            base_delay_seconds=5,
            jitter_seconds=0,
            max_elapsed_seconds=10,
        )

    assert caught.value is failure
    assert calls["count"] == 1
    assert clock.sleeps == [5]


def test_akshare_call_does_not_sleep_when_backoff_consumes_remaining_budget(
    tmp_path, monkeypatch
):
    status_path = tmp_path / "akshare_status.json"
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(status_path))
    clock = _ControlledClock()
    monkeypatch.setattr(akshare_client_module, "time", clock)
    calls = {"count": 0}

    def broken():
        calls["count"] += 1
        if calls["count"] == 1:
            clock.now = 4.0
        raise RuntimeError("budget exhausted")

    with pytest.raises(RuntimeError, match="budget exhausted"):
        akshare_call(
            "stock_zh_a_hist",
            broken,
            attempts=5,
            base_delay_seconds=6,
            jitter_seconds=0,
            max_elapsed_seconds=10,
        )

    status = read_json(str(status_path), {})
    assert calls["count"] == 1
    assert clock.sleeps == []
    assert status["endpoints"]["stock_zh_a_hist"]["attempts"] == 1


def test_akshare_call_zero_elapsed_budget_disables_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(tmp_path / "akshare_status.json"))
    clock = _ControlledClock()
    monkeypatch.setattr(akshare_client_module, "time", clock)
    calls = {"count": 0}

    def recovers():
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    result = akshare_call(
        "stock_zh_a_hist",
        recovers,
        attempts=3,
        base_delay_seconds=0,
        jitter_seconds=0,
        max_elapsed_seconds=0,
    )

    assert result == "ok"
    assert calls["count"] == 3


@pytest.mark.parametrize("budget", [-1.0, float("nan"), float("inf")])
def test_akshare_call_rejects_invalid_elapsed_budget_before_operation(budget):
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        return "unexpected"

    with pytest.raises(ValueError, match="max_elapsed_seconds"):
        akshare_call(
            "stock_zh_a_hist",
            operation,
            attempts=2,
            base_delay_seconds=0,
            jitter_seconds=0,
            max_elapsed_seconds=budget,
        )

    assert calls["count"] == 0


def test_akshare_call_fails_closed_if_monotonic_clock_moves_backwards(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AKSHARE_STATUS_PATH", str(tmp_path / "akshare_status.json"))
    clock = _ControlledClock(now=10.0)
    monkeypatch.setattr(akshare_client_module, "time", clock)
    calls = {"count": 0}
    failure = RuntimeError("clock regression")

    def broken():
        calls["count"] += 1
        clock.now = 9.0
        raise failure

    with pytest.raises(RuntimeError, match="clock regression") as caught:
        akshare_call(
            "stock_zh_a_hist",
            broken,
            attempts=5,
            base_delay_seconds=0,
            jitter_seconds=0,
            max_elapsed_seconds=5,
        )

    assert caught.value is failure
    assert calls["count"] == 1
