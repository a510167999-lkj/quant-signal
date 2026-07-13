import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.research_pit_collector import (
    ControlledTushareCollector,
    FetchSpec,
    HttpEntityResponse,
    PITCollectionError,
    UrllibTushareTransport,
)
from app.research_pit_store import PITReceiptStore
from app.research_partitions import load_temporal_partition_contract


TOKEN = "entity-evidence-secret-token"
TEMPORAL_CONTRACT = load_temporal_partition_contract(
    "data/research_partitions/frozen-v1.json"
)
STOCK_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
)


class Headers:
    def __init__(self, *, content_lengths=()):
        self.content_lengths = [str(value) for value in content_lengths]

    def get(self, name, default=None):
        if str(name).lower() == "content-length" and self.content_lengths:
            return self.content_lengths[0]
        return default

    def get_all(self, name, default=None):
        if str(name).lower() == "content-length":
            return list(self.content_lengths) or default
        return default

    def items(self):
        return [("Content-Length", value) for value in self.content_lengths]


class ChunkedResponse:
    def __init__(self, chunks, *, content_lengths=()):
        self.headers = Headers(content_lengths=content_lengths)
        self.chunks = list(chunks)
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        return self.chunks.pop(0) if self.chunks else b""


def _body():
    return json.dumps(
        {
            "request_id": "entity-evidence-test",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": []},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _spec():
    return FetchSpec(
        dataset="stock_basic",
        partition_key="SSE:L",
        api_name="stock_basic",
        wire_params={"exchange": "SSE", "list_status": "L"},
        receipt_params={"exchange": "SSE", "list_status": "L"},
        fields=STOCK_FIELDS,
        row_cap=6000,
    )


def test_chunked_entity_without_content_length_reads_repeatedly_until_eof():
    response = ChunkedResponse([b"ab", b"cd", b""])

    body, complete = UrllibTushareTransport._read_bounded(response, 4)

    assert body == b"abcd"
    assert complete is True
    assert len(response.read_sizes) == 3
    assert response.chunks == []


def test_chunked_entity_without_content_length_reads_limit_plus_one_tail():
    response = ChunkedResponse([b"ab", b"cd", b"e", b""])

    body, complete = UrllibTushareTransport._read_bounded(response, 4)

    assert body == b"abcd"
    assert complete is False
    assert len(response.read_sizes) >= 3
    assert b"e" not in body


@pytest.mark.parametrize("content_lengths", [(4, 4), (4, 5)])
def test_repeated_or_conflicting_content_length_is_always_incomplete(content_lengths):
    response = ChunkedResponse([b"abcd", b""], content_lengths=content_lengths)

    body, complete = UrllibTushareTransport._read_bounded(response, 8)

    assert body == b"abcd"
    assert complete is False


class ExpiringClock:
    def __init__(self):
        self.monotonic = 0
        self.attestations = 0
        self.now_calls = 0

    def assert_synchronized(self):
        self.attestations += 1
        if self.attestations == 1:
            return {
                "source": "entity-evidence-clock",
                "synchronized": True,
                "checked_at": "2024-01-02T08:00:00+00:00",
            }
        return {
            "source": "entity-evidence-clock",
            "synchronized": False,
            "detail": TOKEN,
        }

    def monotonic_ns(self):
        return self.monotonic

    def now_utc(self):
        self.now_calls += 1
        return datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)


class ResponseThenExpireTransport:
    def __init__(self, clock, raw):
        self.clock = clock
        self.raw = raw
        self.calls = 0

    def post(self, **_kwargs):
        self.calls += 1
        self.clock.monotonic = 1_000_000_001
        return HttpEntityResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=self.raw,
            body_complete=True,
        )


def test_complete_entity_is_recorded_but_never_promotable_when_clock_ttl_recheck_fails(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    raw = _body()
    clock = ExpiringClock()
    transport = ResponseThenExpireTransport(clock, raw)
    store = PITReceiptStore(str(tmp_path / "store"))
    collector = ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=clock,
        max_attempts=1,
        clock_attestation_ttl_s=1,
        sleeper=lambda _seconds: None,
        temporal_contract=TEMPORAL_CONTRACT,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=TEMPORAL_CONTRACT["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )

    with pytest.raises(PITCollectionError, match="clock|synchronization") as raised:
        collector.fetch_partition(_spec())

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["retrieved_at"] is None
    assert "clock" in str(attempt["error_kind"]).lower()
    assert attempt["http_status"] == 200
    assert attempt["body_complete"] is True
    assert attempt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (store.root / attempt["raw_path"]).read_bytes() == raw
    assert attempt["terminal_status"] not in {None, "stored", "reused"}
    promotion = store.promote_fetch_attempt(attempt["attempt_id"])
    assert promotion["status"] not in {"stored", "reused"}
    assert store.receipt_count() == 0
    assert clock.now_calls == 0
    assert transport.calls == 1
    assert TOKEN not in str(raised.value)
    assert TOKEN not in repr(attempts)


class BrokenAfterResponseClock:
    def __init__(self, mode):
        self.mode = mode
        self.monotonic = 0
        self.response_seen = False

    def assert_synchronized(self):
        if self.response_seen and self.mode == "attestation_raises":
            raise RuntimeError("clock backend failed")
        return {"source": "broken-test-clock", "synchronized": True}

    def monotonic_ns(self):
        if self.response_seen and self.mode == "monotonic_raises":
            raise RuntimeError("monotonic backend failed")
        return self.monotonic

    def now_utc(self):
        if self.mode == "now_raises":
            raise RuntimeError("wall clock backend failed")
        if self.mode == "naive_now":
            return datetime(2024, 1, 2, 8, 0)
        return datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)


class MarkResponseTransport:
    def __init__(self, clock, raw):
        self.clock = clock
        self.raw = raw

    def post(self, **_kwargs):
        self.clock.monotonic = 2_000_000_000
        self.clock.response_seen = True
        return HttpEntityResponse(200, {}, self.raw, True)


@pytest.mark.parametrize(
    "mode",
    ["attestation_raises", "monotonic_raises", "now_raises", "naive_now"],
)
def test_any_post_response_clock_failure_still_records_terminal_attempt(
    tmp_path, monkeypatch, mode
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    raw = _body()
    clock = BrokenAfterResponseClock(mode)
    store = PITReceiptStore(str(tmp_path / mode))
    collector = ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=MarkResponseTransport(clock, raw),
        clock=clock,
        max_attempts=1,
        clock_attestation_ttl_s=1,
        sleeper=lambda _seconds: None,
        temporal_contract=TEMPORAL_CONTRACT,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=TEMPORAL_CONTRACT["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )

    with pytest.raises(PITCollectionError, match="clock|synchronization"):
        collector.fetch_partition(_spec())

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    assert attempts[0]["retrieved_at"] is None
    assert "clock" in str(attempts[0]["error_kind"]).lower()
    assert attempts[0]["terminal_status"] not in {None, "stored", "reused"}
    assert (store.root / attempts[0]["raw_path"]).read_bytes() == raw
    assert store.receipt_count() == 0
