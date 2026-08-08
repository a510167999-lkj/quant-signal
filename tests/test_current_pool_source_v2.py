import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import current_pool_source, jobs
from app.current_pool import build_current_pool_coverage
from app.current_pool_risk_source import _load_universe
from app.current_pool_source import (
    CURRENT_POOL_UNIVERSE_SCHEMA_V2,
    build_current_pool_descriptor,
    build_current_pool_descriptor_v2,
    current_pool_universe_partition_coverage,
    fetch_jiaoch_current_pool_descriptor_v2,
    verify_current_pool_universe_descriptor,
)


_STATUSES = ("L", "D", "P", "G")


def _fetch_all_exchanges(exchange: str, list_status: str, fields: tuple[str, ...]) -> dict:
    assert exchange in {"SSE", "SZSE", "BSE"}
    assert list_status in _STATUSES
    index = _STATUSES.index(list_status) + 1
    if exchange == "SSE":
        symbol = "688001" if list_status == "P" else f"60000{index}"
        suffix = "SH"
        market = "科创板" if symbol.startswith("688") else "主板"
    elif exchange == "SZSE":
        symbol = f"00000{index}"
        suffix = "SZ"
        market = "主板"
    else:
        symbol = f"83000{index}"
        suffix = "BJ"
        market = "北交所"
    row = [
        f"{symbol}.{suffix}",
        symbol,
        f"{exchange}-{list_status}",
        exchange,
        market,
        list_status,
        "19910403",
        None,
    ]
    return {"code": 0, "msg": None, "data": {"fields": list(fields), "items": [row]}}


def _resign(payload: dict) -> dict:
    candidate = json.loads(json.dumps(payload))
    unsigned = {key: value for key, value in candidate.items() if key != "descriptor_sha256"}
    candidate["descriptor_sha256"] = current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(unsigned)
    ).hexdigest()
    return candidate


def test_v1_legacy_contract_remains_two_exchange_and_never_fetches_bse(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def fetch(exchange: str, list_status: str, fields: tuple[str, ...]) -> dict:
        seen.append((exchange, list_status))
        return _fetch_all_exchanges(exchange, list_status, fields)

    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=fetch,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["schema"] == "current-pool-universe-input/v1"
    assert payload["partition_coverage"] == {
        "exchanges": ["SSE", "SZSE"],
        "list_statuses": list(_STATUSES),
        "partition_count": 8,
    }
    assert len(seen) == 8
    assert all(exchange != "BSE" for exchange, _ in seen)
    assert all(not item["ts_code"].endswith(".BJ") for item in payload["items"])


def test_v2_retains_bse_and_star_upstream_but_marks_them_ineligible_downstream(
    tmp_path: Path,
) -> None:
    result = build_current_pool_descriptor_v2(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_all_exchanges,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["schema"] == CURRENT_POOL_UNIVERSE_SCHEMA_V2
    assert payload["partition_coverage"] == {
        "exchanges": ["SSE", "SZSE", "BSE"],
        "list_statuses": list(_STATUSES),
        "partition_count": 12,
    }
    assert len(payload["partition_receipts"]) == 12
    assert {item["ts_code"] for item in payload["items"]} >= {"830001.BJ", "688001.SH"}
    assert verify_current_pool_universe_descriptor(payload)["symbols"] >= {
        "830001.BJ",
        "688001.SH",
    }

    coverage = build_current_pool_coverage(payload["items"], histories={})
    status = {item["symbol"]: item for item in coverage["item_history_status"]}
    assert status["830001"]["eligible"] is False
    assert status["688001"]["eligible"] is False


def test_v1_verifier_rejects_a_resigned_bse_descriptor_with_a_v1_label(tmp_path: Path) -> None:
    result = build_current_pool_descriptor_v2(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_all_exchanges,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    payload["schema"] = "current-pool-universe-input/v1"

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(_resign(payload))


@pytest.mark.parametrize("schema", [[], {}])
def test_schema_helpers_and_verifier_reject_non_string_schema_fail_closed(schema: object) -> None:
    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        current_pool_universe_partition_coverage(schema)
    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor({"schema": schema})


@pytest.mark.parametrize("mutation", ["missing_bse_partition", "tampered_bse_row"])
def test_v2_verifier_fails_closed_when_bse_evidence_is_missing_or_tampered(
    tmp_path: Path, mutation: str
) -> None:
    result = build_current_pool_descriptor_v2(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_all_exchanges,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    if mutation == "missing_bse_partition":
        payload["partition_receipts"] = [
            receipt
            for receipt in payload["partition_receipts"]
            if receipt["params"]["exchange"] != "BSE"
        ]
    else:
        next(item for item in payload["items"] if item["ts_code"] == "830001.BJ")["name"] = "changed"

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(_resign(payload))


def test_v2_descriptor_is_accepted_by_risk_loader_and_jobs_loader(tmp_path: Path) -> None:
    result = build_current_pool_descriptor_v2(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_all_exchanges,
    )

    digest, symbols, _ = _load_universe(result["path"], "2026-07-13")
    loaded = jobs._load_current_pool_descriptor(result["path"], CURRENT_POOL_UNIVERSE_SCHEMA_V2)
    assert len(digest) == 64
    assert "830001.BJ" in symbols
    assert loaded["descriptor_sha256"] == digest


def test_v2_fetch_is_jiaoch_only_and_requests_all_bse_partitions(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    class Transport:
        def __init__(self, **_kwargs) -> None:
            pass

        def post(self, **kwargs):
            request = json.loads(kwargs["body"])
            calls.append(request)
            response = _fetch_all_exchanges(
                request["params"]["exchange"],
                request["params"]["list_status"],
                tuple(request["fields"].split(",")),
            )
            return SimpleNamespace(status=200, body_complete=True, body=json.dumps(response).encode())

    monkeypatch.setattr(
        current_pool_source,
        "resolve_tushare_source",
        lambda *args, **kwargs: SimpleNamespace(
            token="test-only", proxy_url=None, api_url="https://jiaoch.test", allowed_hosts=("jiaoch.test",)
        ),
    )
    monkeypatch.setattr(current_pool_source, "UrllibTushareTransport", Transport)
    now = datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    fetch_jiaoch_current_pool_descriptor_v2(
        as_of="2026-07-13", output_dir=tmp_path, now_provider=lambda: now
    )

    assert len(calls) == 12
    assert {call["api_name"] for call in calls} == {"stock_basic"}
    assert {(call["params"]["exchange"], call["params"]["list_status"]) for call in calls} == {
        (exchange, status) for exchange in ("SSE", "SZSE", "BSE") for status in _STATUSES
    }


def test_current_pool_fetch_command_uses_v2_jiaoch_builder(monkeypatch, capsys, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_v2_fetch(**kwargs):
        calls.append(kwargs)
        return {"path": str(tmp_path / "artifact.json"), "created": True}

    def legacy_fetch(**_kwargs):
        raise AssertionError("legacy v1 fetch must not be selected")

    monkeypatch.setattr(jobs, "fetch_jiaoch_current_pool_descriptor_v2", fake_v2_fetch, raising=False)
    monkeypatch.setattr(jobs, "fetch_jiaoch_current_pool_descriptor", legacy_fetch, raising=False)

    assert (
        jobs.main(
            [
                "research-current-pool-fetch-jiaoch",
                "--as-of",
                "2026-07-13",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert calls == [
        {
            "as_of": "2026-07-13",
            "output_dir": str(tmp_path),
            "timeout_seconds": 30.0,
        }
    ]
    assert "artifact.json" in capsys.readouterr().out
