from dataclasses import dataclass, field
import ipaddress
import os
import socket
from urllib.parse import urlparse

JIAOCH_ROW_CAP_OVERRIDES = (("stk_limit", 10_000),)


@dataclass(frozen=True)
class TushareSource:
    name: str
    api_url: str
    allowed_hosts: tuple[str, ...]
    token: str = field(repr=False)
    request_protocol: str = "tushare-root-post/v1"
    row_cap_overrides: tuple[tuple[str, int], ...] = ()
    proxy_url: str | None = None
    network_route: str = "direct"

    def row_cap_for(self, dataset: str, default: int) -> int:
        return dict(self.row_cap_overrides).get(str(dataset), int(default))


def _validate_loopback_http_proxy(proxy_url: str) -> str:
    error = "JIAOCH_PROXY_URL must be an HTTP loopback URL with an explicit port"
    if any(ord(character) < 32 or ord(character) == 127 for character in proxy_url):
        raise ValueError(error)
    try:
        parsed = urlparse(proxy_url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError(error) from exc

    if (
        parsed.scheme != "http"
        or not hostname
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(error)

    if hostname.lower() == "localhost":
        try:
            addresses = socket.getaddrinfo(hostname, port)
        except socket.gaierror as exc:
            raise ValueError(error) from exc
        if not addresses or any(
            not ipaddress.ip_address(address[4][0]).is_loopback for address in addresses
        ):
            raise ValueError(error)
        normalized_host = "localhost"
    else:
        try:
            ip = ipaddress.ip_address(hostname)
            if not ip.is_loopback:
                raise ValueError(error)
        except ValueError as exc:
            raise ValueError(error) from exc
        normalized_host = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed

    return f"http://{normalized_host}:{port}"


def resolve_tushare_source(
    profile: str,
    *,
    api_url: str | None,
    allow_insecure_http: bool,
) -> TushareSource:
    name = str(profile).strip().lower()
    if name == "jiaoch":
        if api_url is not None:
            raise ValueError("jiaoch source does not allow --api-url")
        if allow_insecure_http:
            raise ValueError("jiaoch source does not allow insecure HTTP")
        token = str(os.getenv("JIAOCH_TOKEN") or "")
        if not token:
            raise ValueError("JIAOCH_TOKEN is required for controlled collection")
        configured_proxy = str(os.getenv("JIAOCH_PROXY_URL") or "")
        proxy_url = (
            _validate_loopback_http_proxy(configured_proxy) if configured_proxy else None
        )
        return TushareSource(
            name="jiaoch",
            api_url="http://jiaoch.site",
            allowed_hosts=("jiaoch.site",),
            token=token,
            request_protocol="tushare-path-per-interface/v1",
            row_cap_overrides=JIAOCH_ROW_CAP_OVERRIDES,
            proxy_url=proxy_url,
            network_route="loopback_http_proxy" if proxy_url else "direct",
        )
    if name != "official":
        raise ValueError(f"unsupported source profile: {name}")
    token = str(os.getenv("TUSHARE_TOKEN") or "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is required for controlled collection")
    return TushareSource(
        name="official",
        api_url=str(api_url or "http://api.tushare.pro"),
        allowed_hosts=("api.tushare.pro",),
        token=token,
    )
