from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app import jiaoch_historical_minute_source
from app.jiaoch_historical_minute_source import (
    collect_jiaoch_historical_minute_diagnostic,
    load_jiaoch_historical_minute_diagnostic,
)
from app.research_pit_sources import TushareSource
from app.research_pit_transport import HttpEntityResponse, PITCollectionError


TOKEN = "unit-secret/path"
RETRIEVED_AT = "2026-07-29T16:30:00+08:00"


class RecordingTransport:
    def __init__(
        self,
        response: HttpEntityResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _source(token: str = TOKEN) -> TushareSource:
    return TushareSource(
        name="jiaoch",
        api_url="https://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        token=token,
        request_protocol="tushare-path-per-interface/v1",
    )


def _response(raw: bytes, *, status: int = 200, complete: bool = True) -> HttpEntityResponse:
    return HttpEntityResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=raw,
        body_complete=complete,
    )


def _collect(
    tmp_path: Path,
    transport: RecordingTransport,
    monkeypatch,
    *,
    token: str = TOKEN,
) -> tuple[dict, dict]:
    monkeypatch.setattr(
        jiaoch_historical_minute_source,
        "UrllibTushareTransport",
        lambda *, proxy_url: transport,
    )
    publication = collect_jiaoch_historical_minute_diagnostic(
        source=_source(token),
        ts_code="600000.SH",
        start_date="20260728",
        end_date="20260728",
        retrieved_at=RETRIEVED_AT,
        output_dir=tmp_path,
        timeout_seconds=7.5,
    )
    manifest = load_jiaoch_historical_minute_diagnostic(publication["path"])
    return publication, manifest


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for nested in value.values() for key in _all_keys(nested)}
    if isinstance(value, list):
        return {key for nested in value for key in _all_keys(nested)}
    return set()


def _assert_unbound(manifest: dict) -> None:
    assert manifest["authority_status"] == "UNBOUND"
    assert manifest["evidence_complete"] is False
    assert manifest["verified"] is False
    assert manifest["rows_published"] is False
    assert manifest["minute_amount_authority_status"] == "UNBOUND"
    assert manifest["production_recommendation_eligible"] is False
    assert manifest["raw_response_persisted"] is False
    assert "rows" not in _all_keys(manifest)
    assert "MinuteAmount" not in _all_keys(manifest)


def test_public_collector_binds_the_audited_transport_internally() -> None:
    assert (
        "transport" not in inspect.signature(collect_jiaoch_historical_minute_diagnostic).parameters
    )


def test_unsafe_proxy_source_is_rejected_before_transport_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    constructed: list[str | None] = []

    def transport_factory(*, proxy_url):
        constructed.append(proxy_url)
        return RecordingTransport(_response(b'{"code":-1,"msg":"permission denied","data":null}'))

    monkeypatch.setattr(
        jiaoch_historical_minute_source,
        "UrllibTushareTransport",
        transport_factory,
    )
    unsafe = TushareSource(
        name="jiaoch",
        api_url="https://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        token=TOKEN,
        request_protocol="tushare-path-per-interface/v1",
        proxy_url="http://attacker.example:8080",
        network_route="loopback_http_proxy",
    )

    with pytest.raises(ValueError, match="source binding rejected"):
        collect_jiaoch_historical_minute_diagnostic(
            source=unsafe,
            ts_code="600000.SH",
            start_date="20260728",
            end_date="20260728",
            retrieved_at=RETRIEVED_AT,
            output_dir=tmp_path,
        )
    assert constructed == []


def test_permission_denied_is_terminal_single_request_and_fixed_stk_mins_post(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = json.dumps(
        {
            "code": -1,
            "msg": "权限不足: 对不起，您没有 stk_mins 接口的访问权限",
            "data": {
                "fields": ["trade_time", "amount"],
                "items": [["2026-07-28 09:31:00", 999_999_999]],
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    transport = RecordingTransport(_response(raw))

    publication, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "PERMISSION_DENIED"
    assert manifest["provider_code"] == -1
    assert manifest["request_count"] == 1
    assert manifest["retry_count"] == 0
    assert manifest["fallback_used"] is False
    assert manifest["transport_binding"] == {
        "implementation": "app.research_pit_transport.UrllibTushareTransport",
        "collector_post_invocations": 1,
        "collector_http_retries": 0,
        "collector_fallbacks": 0,
        "redirect_policy": "refuse",
        "network_route": "direct",
    }
    _assert_unbound(manifest)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://jiaoch.site/stk_mins"
    assert call["timeout_s"] == 7.5
    assert call["max_body_bytes"] == 1024 * 1024
    assert call["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "quant-jiaoch-stk-mins-probe/1",
    }
    assert call["body"] == json.dumps(
        {
            "api_name": "stk_mins",
            "fields": "",
            "params": {
                "end_date": "20260728",
                "freq": "1min",
                "start_date": "20260728",
                "ts_code": "600000.SH",
            },
            "token": TOKEN,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert b"999999999" not in Path(publication["path"]).read_bytes()


def test_any_nonzero_code_rejects_fake_rows(tmp_path: Path, monkeypatch) -> None:
    transport = RecordingTransport(
        _response(
            b'{"code":40203,"msg":"denied","data":'
            b'{"fields":["trade_time","amount"],"items":[["fake",123]]}}'
        )
    )

    publication, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "SOURCE_ERROR"
    assert manifest["provider_code"] == 40203
    _assert_unbound(manifest)
    assert b'"fake"' not in Path(publication["path"]).read_bytes()


def test_code_zero_remains_schema_unbound_without_real_success_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(
        _response(
            b'{"code":0,"msg":"ok","data":{"fields":["trade_time","amount"],'
            b'"items":[["2026-07-28 09:31:00",123456.7]]}}'
        )
    )

    publication, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "SCHEMA_UNBOUND"
    assert manifest["provider_code"] == 0
    _assert_unbound(manifest)
    assert b"123456.7" not in Path(publication["path"]).read_bytes()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":0,"code":0,"data":{}}',
        b'{"code":0,"msg":NaN,"data":{}}',
        b'{"code":0,"msg":1e999,"data":{}}',
        b'{"code":true,"data":{}}',
        b'{"code":"0","data":{}}',
        b'["not-an-envelope"]',
    ],
)
def test_strict_response_rejects_duplicate_nonfinite_and_non_integer_code(
    tmp_path: Path,
    raw: bytes,
    monkeypatch,
) -> None:
    transport = RecordingTransport(_response(raw))

    _, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "RESPONSE_REJECTED"
    assert manifest["provider_code"] is None
    _assert_unbound(manifest)
    assert len(transport.calls) == 1


def test_deep_bounded_response_is_stably_rejected_without_rows_or_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    depth = 600
    raw = (
        b'{"code":0,"msg":'
        + b"[" * depth
        + b'"safe"'
        + b"]" * depth
        + b',"data":{}}'
    )
    assert len(raw) < 1024 * 1024
    transport = RecordingTransport(_response(raw))

    publication, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "RESPONSE_REJECTED"
    assert manifest["provider_code"] is None
    _assert_unbound(manifest)
    assert len(transport.calls) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert Path(publication["path"]).is_file()


def test_loader_maps_deep_descriptor_to_stable_public_value_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(
        _response(
            '{"code":-1,"msg":"权限不足: stk_mins 未授权","data":null}'.encode("utf-8")
        )
    )
    _, manifest = _collect(tmp_path / "seed", transport, monkeypatch)
    manifest["request_semantics"] = "__DEEP__"
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    depth = 1_200
    serialized = serialized.replace(
        '"__DEEP__"',
        "[" * depth + "null" + "]" * depth,
    )
    path = tmp_path / "deep.json"
    path.write_text(serialized, encoding="utf-8")
    assert path.stat().st_size < 1024 * 1024

    with pytest.raises(
        ValueError,
        match="Jiaoch historical-minute diagnostic descriptor rejected",
    ):
        load_jiaoch_historical_minute_diagnostic(path)


@pytest.mark.parametrize(
    ("token", "raw"),
    [
        ("unit-secret", b'{"code":-1,"msg":"unit-secret"}'),
        ("unit-secret/path", b'{"code":-1,"msg":"prefix-unit-secret\\/path-suffix"}'),
    ],
)
def test_token_byte_or_semantic_echo_discards_response_body(
    tmp_path: Path,
    token: str,
    raw: bytes,
    monkeypatch,
) -> None:
    transport = RecordingTransport(_response(raw))

    publication, manifest = _collect(tmp_path, transport, monkeypatch, token=token)

    assert manifest["classification"] == "CREDENTIAL_ECHO_REJECTED"
    assert manifest["response_body_bytes"] is None
    assert manifest["response_body_sha256"] is None
    _assert_unbound(manifest)
    persisted = Path(publication["path"]).read_bytes()
    assert token.encode("utf-8") not in persisted
    assert raw not in persisted


@pytest.mark.parametrize(
    "response",
    [
        _response(b'{"code":0}', status=302),
        _response(b'{"code":0}', complete=False),
        _response(b"x" * (1024 * 1024 + 1)),
        _response(b"x" * (1024 * 1024 + 2)),
    ],
)
def test_http_incomplete_and_oversize_entities_are_rejected(
    tmp_path: Path,
    response: HttpEntityResponse,
    monkeypatch,
) -> None:
    transport = RecordingTransport(response)

    _, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "TRANSPORT_REJECTED"
    _assert_unbound(manifest)
    assert len(transport.calls) == 1


def test_transport_redirect_error_is_terminal_without_retry_or_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(error=PITCollectionError("redirect refused"))

    _, manifest = _collect(tmp_path, transport, monkeypatch)

    assert manifest["classification"] == "TRANSPORT_ERROR"
    assert manifest["request_count"] == 1
    assert manifest["retry_count"] == 0
    assert manifest["fallback_used"] is False
    _assert_unbound(manifest)
    assert len(transport.calls) == 1


def test_failure_diagnostic_is_content_addressed_and_tamper_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = RecordingTransport(
        _response('{"code":-1,"msg":"权限不足: stk_mins 未授权","data":null}'.encode("utf-8"))
    )

    publication, manifest = _collect(tmp_path, transport, monkeypatch)

    path = Path(publication["path"])
    assert path.name == f"{manifest['descriptor_sha256']}.json"
    unsigned = {key: value for key, value in manifest.items() if key != "descriptor_sha256"}
    expected = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert expected == manifest["descriptor_sha256"]

    tampered = dict(manifest)
    tampered["classification"] = "SCHEMA_UNBOUND"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="diagnostic descriptor rejected"):
        load_jiaoch_historical_minute_diagnostic(path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(authority_status="BOUND"),
        lambda value: value.update(evidence_complete=True),
        lambda value: value.update(verified=True),
        lambda value: value.update(rows_published=True),
        lambda value: value.update(minute_amount_authority_status="BOUND"),
        lambda value: value.update(rows=[{"MinuteAmount": 123.0}]),
    ],
)
def test_resigned_and_renamed_manifest_cannot_forge_bound_evidence_or_rows(
    tmp_path: Path,
    monkeypatch,
    mutation,
) -> None:
    transport = RecordingTransport(
        _response('{"code":-1,"msg":"权限不足: stk_mins 未授权","data":null}'.encode("utf-8"))
    )
    _, manifest = _collect(tmp_path, transport, monkeypatch)
    forged = dict(manifest)
    mutation(forged)
    forged.pop("descriptor_sha256")
    digest = hashlib.sha256(
        json.dumps(
            forged,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    forged["descriptor_sha256"] = digest
    path = tmp_path / f"{digest}.json"
    path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(ValueError, match="diagnostic descriptor rejected"):
        load_jiaoch_historical_minute_diagnostic(path)
