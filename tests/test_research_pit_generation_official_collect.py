import json
import sqlite3
from datetime import datetime, timezone

from app.research_pit_collector import (
    ControlledTushareCollector,
    HttpEntityResponse,
)
from app.research_pit_store import PITReceiptStore
from app.research_partitions import load_temporal_partition_contract


TOKEN = "official-generation-collector-token"
COLLECTION_TIME = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
STOCK_PARTITIONS = tuple(
    f"{exchange}:{status}"
    for exchange in ("SSE", "SZSE")
    for status in ("L", "D", "P", "G")
)


def _response_body(request, call_number):
    api_name = request["api_name"]
    params = request["params"]
    fields = request["fields"].split(",")
    if api_name == "trade_cal":
        rows = [[params["exchange"], "20240102", 1, "20240101"]]
    elif api_name == "stock_basic":
        rows = []
    elif api_name == "daily":
        rows = [
            [
                "600001.SH",
                params["trade_date"],
                10.0,
                10.5,
                9.8,
                10.2,
                9.9,
                0.3,
                3.03,
                1000,
                10100,
            ]
        ]
    elif api_name == "adj_factor":
        rows = [["600001.SH", params["trade_date"], 1.5]]
    elif api_name == "stk_limit":
        rows = [[params["trade_date"], "600001.SH", 10.0, 11.0, 9.0]]
    elif api_name == "suspend_d":
        rows = []
    else:
        rows = [[params["trade_date"], "600001.SH", "A", "银行", "20100101"]]
    return json.dumps(
        {
            "request_id": f"official-collect-{call_number}",
            "code": 0,
            "msg": "",
            "data": {"fields": fields, "items": rows},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class FakeTrustedClock:
    def __init__(self):
        self.monotonic = 0

    def assert_synchronized(self):
        return {"source": "official-collect-test", "synchronized": True}

    def now_utc(self):
        return COLLECTION_TIME

    def monotonic_ns(self):
        self.monotonic += 1
        return self.monotonic


class RecordingTransport:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def post(self, **kwargs):
        request = json.loads(kwargs["body"])
        self.calls.append(request)
        api_name = request["api_name"]
        params = request["params"]
        if api_name == "trade_cal":
            identity = params["exchange"]
        elif api_name == "stock_basic":
            identity = f'{params["exchange"]}:{params["list_status"]}'
        else:
            identity = params["trade_date"]
        self.events.append(f"fetch:{api_name}:{identity}")
        return HttpEntityResponse(
            status=200,
            headers={},
            body=_response_body(request, len(self.calls)),
            body_complete=True,
        )


class GenerationObservedCollector(ControlledTushareCollector):
    def __init__(self, *, events, **kwargs):
        super().__init__(**kwargs)
        self.events = events
        self.generation_call_count = 0

    def collect_stock_basic_generation(self, **kwargs):
        self.generation_call_count += 1
        self.events.append("collect:stock_basic_generation")
        return super().collect_stock_basic_generation(**kwargs)


def test_official_collect_routes_stock_basic_through_one_audited_generation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    events = []
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = RecordingTransport(events)
    contract = load_temporal_partition_contract(
        "data/research_partitions/frozen-v1.json"
    )
    collector = GenerationObservedCollector(
        events=events,
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=FakeTrustedClock(),
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )
    promoted_datasets = []
    real_promote = store.promote_fetch_attempt

    def record_legacy_promotion(attempt_id):
        attempt = store.fetch_attempts(attempt_id=attempt_id)[0]
        promoted_datasets.append(attempt["dataset"])
        return real_promote(attempt_id)

    monkeypatch.setattr(store, "promote_fetch_attempt", record_legacy_promotion)

    report = collector.collect(
        start_date="2024-01-02",
        end_date="2024-01-02",
        resume=True,
    )

    assert collector.generation_call_count == 1
    assert events == [
        "fetch:trade_cal:SSE",
        "fetch:trade_cal:SZSE",
        "collect:stock_basic_generation",
        *[f"fetch:stock_basic:{partition}" for partition in STOCK_PARTITIONS],
        "fetch:bak_basic:20240102",
        # WHY: collect now drives the four-shard market session generation for
        # the single common open session (2024-01-02); these shards stage into
        # the market generation and never promote as legacy receipts.
        "fetch:daily:20240102",
        "fetch:adj_factor:20240102",
        "fetch:stk_limit:20240102",
        "fetch:suspend_d:20240102",
    ]
    assert promoted_datasets == ["trade_cal", "trade_cal", "bak_basic"]
    with sqlite3.connect(store.database_path) as connection:
        legacy_stock_receipts = connection.execute(
            "SELECT COUNT(*) FROM receipts WHERE dataset = 'stock_basic'"
        ).fetchone()[0]
    assert legacy_stock_receipts == 0
    stock_attempts = [
        attempt
        for attempt in store.fetch_attempts()
        if attempt["dataset"] == "stock_basic"
    ]
    assert len(stock_attempts) == 8
    assert {attempt["terminal_status"] for attempt in stock_attempts} == {
        "generation_staged"
    }

    verified = store.verify_stock_basic_generation(report["generation_id"])
    assert report["manifest"] == verified["manifest"]
    assert report["manifest_sha256"] == verified["manifest_sha256"]
    assert report["audit_identity_sha256"] == verified["audit_identity_sha256"]
