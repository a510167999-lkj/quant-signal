"""Lightweight bounded HTTP transport for Tushare-compatible sources."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class PITCollectionError(RuntimeError):
    """Stable public error shared with the controlled PIT collector."""


@dataclass(frozen=True)
class HttpEntityResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    body_complete: bool = True


class TushareTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpEntityResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise PITCollectionError(f"Tushare transport refused HTTP redirect {int(code)}")


class UrllibTushareTransport:
    """Bounded POST transport that returns exact response entity bytes."""

    def __init__(
        self,
        opener: Any = None,
        *,
        proxy_url: str | None = None,
        _opener_factory: Callable[..., Any] = build_opener,
        _proxy_handler_factory: Callable[..., Any] = ProxyHandler,
        _redirect_handler_factory: Callable[[], Any] = _NoRedirectHandler,
    ) -> None:
        proxy_handler = _proxy_handler_factory({"https": proxy_url} if proxy_url else {})
        self._opener = (
            opener
            if opener is not None
            else _opener_factory(proxy_handler, _redirect_handler_factory())
        )

    @staticmethod
    def _read_bounded(response: Any, limit: int) -> tuple[bytes, bool]:
        get_all = getattr(response.headers, "get_all", None)
        if callable(get_all):
            declared_values = list(get_all("Content-Length", []) or [])
        else:
            declared = response.headers.get("Content-Length")
            declared_values = [] if declared is None else str(declared).split(",")
        declared_values = [str(value) for value in declared_values]
        declared_size = None
        declared_invalid = len(declared_values) > 1
        if len(declared_values) == 1:
            value = declared_values[0]
            if value and value.isascii() and value.isdigit():
                declared_size = int(declared_values[0])
                declared_invalid = declared_size < 0 or declared_size > limit
            else:
                declared_invalid = True

        if declared_size is not None and not declared_invalid:
            chunks = []
            remaining = declared_size
            while remaining:
                chunk = response.read(remaining)
                if not chunk or not isinstance(chunk, bytes):
                    return b"".join(chunks), False
                chunks.append(chunk)
                if len(chunk) > remaining:
                    return b"".join(chunks)[:limit], False
                remaining -= len(chunk)
            return b"".join(chunks), True

        chunks = []
        total = 0
        reached_eof = False
        while total <= limit:
            chunk = response.read(max(1, limit + 1 - total))
            if isinstance(chunk, bytes) and chunk == b"":
                reached_eof = True
                break
            if not isinstance(chunk, bytes):
                declared_invalid = True
                break
            chunks.append(chunk)
            total += len(chunk)
        body = b"".join(chunks)
        complete = (
            not declared_invalid
            and reached_eof
            and len(body) <= limit
            and (declared_size is None or len(body) == declared_size)
        )
        return body[:limit], complete

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_body_bytes: int,
    ) -> HttpEntityResponse:
        request = Request(
            url=str(url),
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            response = self._opener.open(request, timeout=float(timeout_s))
        except HTTPError as exc:
            response = exc
        try:
            entity, complete = self._read_bounded(response, int(max_body_bytes))
            return HttpEntityResponse(
                status=int(response.getcode()),
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=entity,
                body_complete=complete,
            )
        finally:
            response.close()
