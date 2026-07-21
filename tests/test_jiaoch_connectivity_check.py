"""Tests for the jiaoch-connectivity-check diagnostic probe.

The probe is a pure read-only function `_probe_jiaoch_connectivity` that walks
five stages (clock gate -> source profile -> DNS -> TLS -> HTTPS POST) and
returns a structured payload. Tests monkeypatch each stage to avoid real
network access; production_callers never hit the network from the test suite.
"""

from __future__ import annotations

import socket

from app import jobs
from app.jobs import _probe_jiaoch_connectivity


def _last_step(payload: dict) -> dict:
    return payload["steps"][-1]


def test_probe_reports_clock_gate_failure_without_touching_network(monkeypatch):
    """Windows 上历史性 fail-closed:clock gate 阻塞时必须立即返回,不调 DNS/TLS。"""
    from app.research_pit_collector import PITCollectionError

    class FailingClock:
        def assert_synchronized(self):
            raise PITCollectionError("system clock synchronization could not be proven")

    dns_calls: list[str] = []

    def _no_dns(*args, **kwargs):
        dns_calls.append("should-not-be-called")
        raise AssertionError("DNS must not be queried when clock gate fails")

    monkeypatch.setattr("app.research_pit_collector.SystemTrustedClock", FailingClock)
    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)

    payload = _probe_jiaoch_connectivity(timeout_seconds=5.0)

    assert payload["overall_status"] == "blocked_clock_gate"
    assert _last_step(payload)["step"] == "clock_gate"
    assert _last_step(payload)["status"] == "fail_closed"
    assert _last_step(payload)["error_type"] == "PITCollectionError"
    assert dns_calls == [], "DNS must not be queried when clock gate fails"
    assert "elapsed_ms" in payload


def test_probe_reports_source_profile_failure_when_proxy_misconfigured(monkeypatch):
    """Clock gate 通过但 JIAOCH_PROXY_URL 配错(非 loopback)时,报告 source_config 阻塞。"""
    class PassingClock:
        def assert_synchronized(self):
            return {"source": "test", "synchronized": True}

    monkeypatch.setattr("app.research_pit_collector.SystemTrustedClock", PassingClock)
    monkeypatch.setenv("JIAOCH_TOKEN", "fake-token-for-probe")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "https://not-loopback.example.com")

    payload = _probe_jiaoch_connectivity(timeout_seconds=5.0)

    assert payload["overall_status"] == "blocked_source_config"
    assert _last_step(payload)["step"] == "source_profile"
    assert _last_step(payload)["status"] == "fail"


def test_probe_reports_dns_failure_and_does_not_attempt_tls(monkeypatch, tmp_path):
    """DNS 失败必须立即返回,不打开 TLS 握手。"""
    class PassingClock:
        def assert_synchronized(self):
            return {"source": "test", "synchronized": True}

    monkeypatch.setattr("app.research_pit_collector.SystemTrustedClock", PassingClock)
    monkeypatch.setenv("JIAOCH_TOKEN", "fake-token-for-probe")

    tls_calls: list[str] = []

    def _no_tls(*args, **kwargs):
        tls_calls.append("should-not-be-called")
        raise AssertionError("TLS must not be attempted when DNS fails")

    def _gaierror(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _gaierror)
    monkeypatch.setattr(socket, "create_connection", _no_tls)

    payload = _probe_jiaoch_connectivity(timeout_seconds=5.0)

    assert payload["overall_status"] == "blocked_dns"
    assert _last_step(payload)["step"] == "dns_resolution"
    assert _last_step(payload)["status"] == "fail"
    assert _last_step(payload)["error_type"] == "gaierror"
    assert tls_calls == []


def test_probe_reports_tls_failure_and_does_not_attempt_post(monkeypatch):
    """TLS 握手失败必须报告具体类型(SSL/timeout/OSError),不调 POST。"""
    class PassingClock:
        def assert_synchronized(self):
            return {"source": "test", "synchronized": True}

    monkeypatch.setattr("app.research_pit_collector.SystemTrustedClock", PassingClock)
    monkeypatch.setenv("JIAOCH_TOKEN", "fake-token-for-probe")

    def _resolve(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    # create_connection 必须返回能进 with 的 fake socket,TLS 失败发生在 wrap_socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: _FakeSocket())

    import ssl as _ssl

    def _tls_fail(*args, **kwargs):
        raise ssl.SSLError("TLS handshake failed")

    monkeypatch.setattr(_ssl, "create_default_context", lambda: _DummySslContext(_tls_fail))

    post_calls: list[str] = []
    monkeypatch.setattr(
        "app.research_pit_collector.UrllibTushareTransport",
        lambda **kw: _FailingTransport(post_calls),
    )

    payload = _probe_jiaoch_connectivity(timeout_seconds=5.0)

    assert payload["overall_status"] == "blocked_tls"
    assert _last_step(payload)["step"] == "tls_handshake"
    assert _last_step(payload)["status"] == "fail"
    assert _last_step(payload)["error_type"] == "SSLError"
    assert post_calls == []


def test_probe_reports_ok_without_token_when_reachable(monkeypatch):
    """Clock/DNS/TLS 全过 + 无 token 时,POST 步骤 skipped,整体 ok_without_token。"""
    class PassingClock:
        def assert_synchronized(self):
            return {"source": "test", "synchronized": True}

    monkeypatch.setattr("app.research_pit_collector.SystemTrustedClock", PassingClock)
    monkeypatch.delenv("JIAOCH_TOKEN", raising=False)

    def _resolve(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: _FakeSocket())

    import ssl as _ssl

    monkeypatch.setattr(_ssl, "create_default_context", lambda: _DummySslContext(lambda *a, **kw: None))

    payload = _probe_jiaoch_connectivity(timeout_seconds=5.0)

    assert payload["overall_status"] == "ok_without_token"
    assert _last_step(payload)["step"] == "https_post"
    assert _last_step(payload)["status"] == "skipped"


def test_cli_jiaoch_connectivity_check_wires_probe_and_exit_code(monkeypatch, capsys):
    """CLI subparser 必须调 _probe_jiaoch_connectivity 并按 overall_status 映射 exit code。"""
    captured: dict = {}

    def _fake_probe(timeout_seconds: float = 10.0) -> dict:
        captured["timeout"] = timeout_seconds
        return {
            "probe": "jiaoch-connectivity/v1",
            "overall_status": "ok",
            "elapsed_ms": 1,
            "steps": [{"step": "clock_gate", "status": "pass"}],
        }

    monkeypatch.setattr(jobs, "_probe_jiaoch_connectivity", _fake_probe)

    exit_code = jobs.main(["jiaoch-connectivity-check", "--timeout-seconds", "7"])

    assert exit_code == 0
    assert captured["timeout"] == 7.0
    out = capsys.readouterr().out
    assert '"overall_status": "ok"' in out


def test_cli_returns_nonzero_when_probe_fails(monkeypatch, capsys):
    """探测失败时 CLI 必须返回非零 exit code,便于脚本和生产健康检查消费。"""
    monkeypatch.setattr(
        jobs,
        "_probe_jiaoch_connectivity",
        lambda timeout_seconds=10.0: {
            "probe": "jiaoch-connectivity/v1",
            "overall_status": "blocked_clock_gate",
            "elapsed_ms": 1,
            "steps": [{"step": "clock_gate", "status": "fail_closed"}],
        },
    )

    exit_code = jobs.main(["jiaoch-connectivity-check"])

    assert exit_code == 1


# ---------- helpers ----------

class _FakeSocket:
    """Minimal context-manager socket so create_connection + wrap_socket flows work in tests."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _DummySslContext:
    def __init__(self, wrap_side_effect):
        self._wrap_side_effect = wrap_side_effect

    def wrap_socket(self, sock, server_hostname=None):
        self._wrap_side_effect()
        return _WrappedFakeSocket()


class _WrappedFakeSocket:
    """fake SSL-wrapped socket with the methods the probe reads."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getpeercert(self):
        return {
            "subject": ((("commonName", "jiaoch.site"),),),
            "issuer": ((("commonName", "Test CA"),),),
            "notAfter": "Jan  1 00:00:00 2027 GMT",
        }

    def version(self):
        return "TLSv1.2"


class _FailingTransport:
    def __init__(self, post_calls):
        self._post_calls = post_calls

    def post(self, **kwargs):
        self._post_calls.append("should-not-be-called")
        raise AssertionError("POST must not be attempted when TLS fails")


# Late import so the parametrized test bodies above stay readable.
import ssl  # noqa: E402
