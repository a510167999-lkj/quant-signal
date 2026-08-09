import pytest

from app.research_pit_sources import resolve_tushare_source


def test_jiaoch_profile_is_http_pinned_and_uses_its_own_environment_token(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert source.name == "jiaoch"
    assert source.api_url == "http://jiaoch.site"
    assert source.allowed_hosts == ("jiaoch.site",)
    assert source.token == "compatible-source-token"
    assert source.request_protocol == "tushare-path-per-interface/v1"
    assert source.row_cap_for("stk_limit", 5800) == 10000


def test_jiaoch_profile_accepts_explicit_loopback_http_proxy(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://127.0.0.1:7897")

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert source.proxy_url == "http://127.0.0.1:7897"
    assert source.network_route == "loopback_http_proxy"


def test_tushare_source_repr_does_not_expose_token(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "secret-token-must-not-appear")

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert "secret-token-must-not-appear" not in repr(source)


def test_jiaoch_profile_normalizes_proxy_root_path(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://127.0.0.1:7897/")

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert source.proxy_url == "http://127.0.0.1:7897"


def test_jiaoch_profile_defaults_to_direct_route_without_proxy(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.delenv("JIAOCH_PROXY_URL", raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert source.proxy_url is None
    assert source.network_route == "direct"


def test_official_profile_remains_direct_when_jiaoch_proxy_is_configured(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "official-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://127.0.0.1:7897")

    source = resolve_tushare_source(
        "official", api_url=None, allow_insecure_http=False
    )

    assert source.proxy_url is None
    assert source.network_route == "direct"


@pytest.mark.parametrize(
    "proxy_url",
    [
        "https://127.0.0.1:7897",
        "http://192.0.2.1:7897",
        "http://user:password@127.0.0.1:7897",
        "http://127.0.0.1",
        "http://127.0.0.1:7897/path",
        "http://127.0.0.1:7897/;params",
        "http://127.0.0.1:7897?mode=test",
        "http://127.0.0.1:7897#fragment",
        "not-a-url",
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:7897\r\n",
        "http://127.0.0.1:\t7897",
        "http://127.0.0.1:7897\x7f",
    ],
)
def test_jiaoch_profile_rejects_invalid_proxy_urls(monkeypatch, proxy_url):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", proxy_url)

    with pytest.raises(ValueError, match="JIAOCH_PROXY_URL"):
        resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=False)


def test_jiaoch_profile_accepts_localhost_only_when_all_addresses_are_loopback(
    monkeypatch,
):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://localhost:7897")
    monkeypatch.setattr(
        "app.research_pit_sources.socket.getaddrinfo",
        lambda host, port: [
            (2, 1, 6, "", ("127.0.0.1", port)),
            (10, 1, 6, "", ("::1", port, 0, 0)),
        ],
    )

    source = resolve_tushare_source(
        "jiaoch", api_url=None, allow_insecure_http=False
    )

    assert source.network_route == "loopback_http_proxy"


def test_jiaoch_profile_rejects_localhost_with_any_non_loopback_address(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")
    monkeypatch.setenv("JIAOCH_PROXY_URL", "http://localhost:7897")
    monkeypatch.setattr(
        "app.research_pit_sources.socket.getaddrinfo",
        lambda host, port: [
            (2, 1, 6, "", ("127.0.0.1", port)),
            (2, 1, 6, "", ("192.0.2.1", port)),
        ],
    )

    with pytest.raises(ValueError, match="JIAOCH_PROXY_URL"):
        resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=False)


def test_jiaoch_profile_rejects_url_override(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")

    with pytest.raises(ValueError, match="does not allow --api-url"):
        resolve_tushare_source(
            "jiaoch", api_url="https://example.invalid", allow_insecure_http=False
        )


def test_jiaoch_profile_rejects_insecure_switch(monkeypatch):
    monkeypatch.setenv("JIAOCH_TOKEN", "compatible-source-token")

    with pytest.raises(ValueError, match="does not allow insecure HTTP"):
        resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=True)


def test_jiaoch_profile_requires_dedicated_environment_token(monkeypatch):
    monkeypatch.delenv("JIAOCH_TOKEN", raising=False)
    monkeypatch.setenv("TUSHARE_TOKEN", "official-token")

    with pytest.raises(ValueError, match="JIAOCH_TOKEN is required"):
        resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=False)


def test_official_profile_preserves_existing_url_override(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "official-token")

    source = resolve_tushare_source(
        "official",
        api_url="https://api.tushare.pro",
        allow_insecure_http=False,
    )

    assert source.name == "official"
    assert source.api_url == "https://api.tushare.pro"
    assert source.allowed_hosts == ("api.tushare.pro",)
    assert source.token == "official-token"
    assert source.request_protocol == "tushare-root-post/v1"
    assert source.row_cap_for("stk_limit", 5800) == 5800
