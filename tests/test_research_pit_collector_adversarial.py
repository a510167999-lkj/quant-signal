import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.research_pit_collector import (
    ControlledTushareCollector,
    FetchSpec,
    HttpEntityResponse,
    PITCollectionError,
    SystemTrustedClock,
    UrllibTushareTransport,
)
from app.research_pit_store import PITReceiptStore


TOKEN = "adversarial-collector-token"
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


def _body(*, request_id="test", items=None):
    return json.dumps(
        {
            "request_id": request_id,
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": items or []},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _spec(*, status="L", wire_params=None):
    return FetchSpec(
        dataset="stock_basic",
        partition_key=f"SSE:{status}",
        api_name="stock_basic",
        wire_params=wire_params
        or {"exchange": "SSE", "list_status": status},
        receipt_params={"exchange": "SSE", "list_status": status},
        fields=STOCK_FIELDS,
        row_cap=6000,
    )


class ManualClock:
    def __init__(self):
        self.wall = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.monotonic = 0
        self.attestation_count = 0

    def assert_synchronized(self):
        self.attestation_count += 1
        return {
            "source": "manual-test-clock",
            "synchronized": True,
            "sequence": self.attestation_count,
            "checked_at": self.wall.isoformat(),
        }

    def now_utc(self):
        return self.wall

    def monotonic_ns(self):
        return self.monotonic

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.monotonic += int(seconds * 1_000_000_000)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class RecordingStore:
    def __init__(self):
        self.attempts = []
        self.promotions = []

    def record_fetch_attempt(self, **metadata):
        self.attempts.append(metadata)
        return f"attempt-{len(self.attempts)}"

    def promote_fetch_attempt(self, attempt_id):
        self.promotions.append(attempt_id)
        return {"status": "stored", "attempt_id": attempt_id}


def _collector(*, store, transport, clock, **kwargs):
    from app.research_partitions import load_temporal_partition_contract
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=clock,
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
        **kwargs,
    )


def test_noncanonical_wire_params_are_rejected_before_network_or_attempt_recording():
    transport = ScriptedTransport([HttpEntityResponse(200, {}, _body())])
    store = RecordingStore()
    collector = _collector(store=store, transport=transport, clock=ManualClock())
    narrowed = _spec(
        wire_params={
            "exchange": "SSE",
            "list_status": "L",
            "ts_code": "600001.SH",
        }
    )

    with pytest.raises(PITCollectionError, match="canonical|wire|params|specification"):
        collector.fetch_partition(narrowed)

    assert transport.calls == []
    assert store.attempts == []
    assert store.promotions == []


def test_reflected_collector_token_never_reaches_disk_and_leaves_diagnostic_attempt(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    store = PITReceiptStore(str(tmp_path / "store"))
    collector = _collector(
        store=store,
        transport=ScriptedTransport(
            [HttpEntityResponse(503, {}, b"upstream echoed " + TOKEN.encode("utf-8"))]
        ),
        clock=ManualClock(),
    )

    with pytest.raises(PITCollectionError) as raised:
        collector.fetch_partition(_spec())

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    diagnostic = " ".join(
        str(attempts[0].get(key) or "")
        for key in ("error_kind", "error_message", "terminal_status")
    ).lower()
    assert "token" in diagnostic or "credential" in diagnostic
    assert TOKEN not in str(raised.value)
    assert TOKEN not in repr(attempts)
    for path in store.root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode("utf-8") not in path.read_bytes(), path


def test_short_content_length_entity_is_incomplete_and_cannot_be_promoted(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    raw = _body()

    class Response:
        headers = {
            "Content-Length": str(len(raw) + 100),
            "Content-Type": "application/json",
        }

        def __init__(self):
            self.closed = False

        def read(self, _limit):
            return raw

        def getcode(self):
            return 200

        def close(self):
            self.closed = True

    response = Response()

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 30.0
            return response

    store = PITReceiptStore(str(tmp_path / "store"))
    collector = _collector(
        store=store,
        transport=UrllibTushareTransport(opener=Opener()),
        clock=ManualClock(),
    )

    with pytest.raises(PITCollectionError, match="incomplete|failed"):
        collector.fetch_partition(_spec())

    attempts = store.fetch_attempts(partition_key="SSE:L")
    assert len(attempts) == 1
    assert attempts[0]["body_complete"] is False
    assert attempts[0]["error_kind"] == "incomplete_response"
    assert attempts[0]["terminal_status"] is not None
    assert store.receipt_count() == 0
    assert response.closed is True


def test_macos_network_time_on_requires_a_measured_offset():
    systemsetup_on = SimpleNamespace(
        returncode=0,
        stdout="Network Time: On\n",
        stderr="",
    )

    with (
        patch("app.research_pit_collector.platform.system", return_value="Darwin"),
        patch("app.research_pit_collector.Path.exists", return_value=True),
        patch("app.research_pit_collector.shutil.which", return_value=None),
        patch("app.research_pit_collector.subprocess.run", return_value=systemsetup_on),
    ):
        with pytest.raises(PITCollectionError, match="synchronization"):
            SystemTrustedClock().assert_synchronized()

    measured = SimpleNamespace(
        returncode=0,
        stdout=(
            "selected:\n"
            "sntp_exchange {\n"
            "result: 0 (Success)\n"
            "offset: review (0.25)\n"
            "}\n"
            "+0.250000 +/- 0.070000 time.apple.com\n"
        ),
        stderr="",
    )

    def which(name):
        return "/usr/bin/sntp" if name == "sntp" else None

    def run(command, **_kwargs):
        return systemsetup_on if command[0] == "/usr/sbin/systemsetup" else measured

    with (
        patch("app.research_pit_collector.platform.system", return_value="Darwin"),
        patch("app.research_pit_collector.Path.exists", return_value=True),
        patch("app.research_pit_collector.shutil.which", side_effect=which),
        patch("app.research_pit_collector.subprocess.run", side_effect=run),
    ):
        evidence = SystemTrustedClock().assert_synchronized()

    assert evidence["source"] == "sntp"
    assert evidence["offset_seconds"] == 0.25


def test_expired_clock_attestation_is_refreshed_before_next_partition():
    clock = ManualClock()
    store = RecordingStore()
    collector = _collector(
        store=store,
        transport=ScriptedTransport(
            [
                HttpEntityResponse(200, {}, _body(request_id="first")),
                HttpEntityResponse(200, {}, _body(request_id="second")),
            ]
        ),
        clock=clock,
        clock_attestation_ttl_s=10,
    )

    collector.fetch_partition(_spec(status="L"))
    clock.advance(11)
    collector.fetch_partition(_spec(status="D"))

    assert clock.attestation_count == 2
    assert [attempt["clock_attestation"]["sequence"] for attempt in store.attempts] == [
        1,
        2,
    ]
