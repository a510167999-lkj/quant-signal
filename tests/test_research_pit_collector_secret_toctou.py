import json
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

from app.research_pit_collector import (
    ControlledTushareCollector,
    FetchSpec,
    HttpEntityResponse,
    PITCollectionError,
)
from app.research_pit_store import PITReceiptStore


TOKEN = "unicode-secret-token"
FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
)


class Clock:
    def assert_synchronized(self):
        return {"source": "test", "synchronized": True}

    def monotonic_ns(self):
        return 1

    def now_utc(self):
        return datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _collector(store, transport):
    from app.research_partitions import load_temporal_partition_contract
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=Clock(),
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )


def _spec(wire_params):
    return FetchSpec(
        dataset="stock_basic",
        partition_key="SSE:L",
        api_name="stock_basic",
        wire_params=wire_params,
        receipt_params={"exchange": "SSE", "list_status": "L"},
        fields=FIELDS,
        row_cap=6000,
    )


def _success_body():
    return json.dumps(
        {
            "code": 0,
            "msg": "",
            "data": {"fields": list(FIELDS), "items": []},
        },
        separators=(",", ":"),
    ).encode()


def test_json_unicode_escaped_credential_echo_is_discarded_before_disk(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    escaped = TOKEN.replace("s", r"\u0073")
    raw = ('{"error":"' + escaped + '"}').encode()
    assert json.loads(raw)["error"] == TOKEN
    store = PITReceiptStore(str(tmp_path / "store"))
    collector = _collector(store, Transport(HttpEntityResponse(503, {}, raw)))

    with pytest.raises(PITCollectionError) as raised:
        collector.fetch_partition(_spec({"exchange": "SSE", "list_status": "L"}))

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    assert "credential" in str(attempts[0]["error_kind"]).lower()
    assert attempts[0]["raw_path"] is None
    assert TOKEN not in str(raised.value)
    for path in store.root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert TOKEN.encode() not in content
            assert escaped.encode() not in content


class StatefulWireParams(Mapping):
    def __init__(self):
        self.snapshots = 0
        self.current = {"exchange": "SSE", "list_status": "L"}

    def __iter__(self):
        self.snapshots += 1
        self.current = {"exchange": "SSE", "list_status": "L"}
        if self.snapshots >= 3:
            self.current["ts_code"] = "600001.SH"
        return iter(self.current)

    def __len__(self):
        return len(self.current)

    def __getitem__(self, key):
        return self.current[key]


class RecordingStore:
    def __init__(self):
        self.attempts = []

    def record_fetch_attempt(self, **metadata):
        self.attempts.append(metadata)
        return "attempt-1"

    def promote_fetch_attempt(self, attempt_id):
        return {"status": "stored", "attempt_id": attempt_id}


def test_wire_mapping_is_frozen_once_before_validation_and_serialization():
    mapping = StatefulWireParams()
    transport = Transport(HttpEntityResponse(200, {}, _success_body()))
    store = RecordingStore()
    collector = _collector(store, transport)

    result = collector.fetch_partition(_spec(mapping))

    assert result["status"] == "stored"
    sent = json.loads(transport.calls[0]["body"])
    assert sent["params"] == {"exchange": "SSE", "list_status": "L"}
    assert "ts_code" not in sent["params"]
    assert store.attempts[0]["request_semantics"]["wire_params"] == sent["params"]
