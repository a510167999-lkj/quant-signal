import pytest

from app.akshare_client import akshare_call
from app.storage import read_json


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
