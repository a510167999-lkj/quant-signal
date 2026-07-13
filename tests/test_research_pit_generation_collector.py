import json
from datetime import datetime, timedelta, timezone

from app.research_pit_collector import (
    ControlledTushareCollector,
    HttpEntityResponse,
)
from app.research_pit_store import PITReceiptStore


TOKEN = "generation-collector-token"
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
STOCK_PARTITIONS = tuple(
    f"{exchange}:{status}"
    for exchange in ("SSE", "SZSE")
    for status in ("L", "D", "P", "G")
)


def _success_body(partition_key):
    return json.dumps(
        {
            "request_id": f"generation-{partition_key}",
            "code": 0,
            "msg": "",
            "data": {"fields": list(STOCK_FIELDS), "items": []},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TrustedClock:
    def __init__(self, now):
        self.now = now
        self.monotonic = 0

    def assert_synchronized(self):
        return {"source": "generation-test-clock", "synchronized": True}

    def now_utc(self):
        return self.now

    def monotonic_ns(self):
        return self.monotonic


class PartitionRecordingTransport:
    def __init__(self):
        self.calls = []

    def post(self, **kwargs):
        request = json.loads(kwargs["body"])
        params = request["params"]
        partition_key = f'{params["exchange"]}:{params["list_status"]}'
        self.calls.append({"partition_key": partition_key, **kwargs})
        return HttpEntityResponse(200, {}, _success_body(partition_key))


class GenerationContractStore:
    """In-memory contract double; the collector still executes real fetch logic."""

    def __init__(self):
        self.generations = {}
        self.active_generation_id = None
        self.begin_calls = []
        self.stage_calls = []
        self.publish_calls = []
        self.active_calls = 0
        self.attempts = []
        self.promotions = []
        self._next_generation = 1

    def _create_generation(self, started_at):
        generation_id = f"generation-{self._next_generation}"
        self._next_generation += 1
        self.generations[generation_id] = {
            "generation_id": generation_id,
            "status": "staging",
            "started_at": started_at,
            "staged_attempts": {},
        }
        return generation_id

    def seed_staging_generation(self, *, started_at, staged_partitions):
        generation_id = self._create_generation(started_at)
        generation = self.generations[generation_id]
        generation["staged_attempts"] = {
            partition_key: f"seed-{partition_key}"
            for partition_key in staged_partitions
        }
        return generation_id

    def _snapshot(self, generation_id):
        generation = self.generations[generation_id]
        return {
            "generation_id": generation_id,
            "status": generation["status"],
            "started_at": generation["started_at"].isoformat(),
            "staged_partitions": [
                partition_key
                for partition_key in STOCK_PARTITIONS
                if partition_key in generation["staged_attempts"]
            ],
        }

    def begin_or_resume_stock_basic_generation(self, now, force_new=False):
        self.begin_calls.append({"now": now, "force_new": force_new})
        staging = next(
            (
                generation
                for generation in self.generations.values()
                if generation["status"] == "staging"
            ),
            None,
        )
        if force_new and staging is not None:
            staging["status"] = "abandoned"
            staging = None
        if staging is not None and now - staging["started_at"] > timedelta(hours=1):
            staging["status"] = "abandoned"
            staging = None
        if staging is None:
            generation_id = self._create_generation(now)
        else:
            generation_id = staging["generation_id"]
        return self._snapshot(generation_id)

    def record_fetch_attempt(self, **metadata):
        attempt_id = f"attempt-{len(self.attempts) + 1}"
        self.attempts.append({"attempt_id": attempt_id, **metadata})
        return attempt_id

    def promote_fetch_attempt(self, attempt_id):
        self.promotions.append(attempt_id)
        return {"status": "stored", "attempt_id": attempt_id}

    def stage_stock_basic_attempt(
        self, generation_id, logical_partition_key, attempt_id
    ):
        assert logical_partition_key in STOCK_PARTITIONS
        assert attempt_id in {attempt["attempt_id"] for attempt in self.attempts}
        generation = self.generations[generation_id]
        assert generation["status"] == "staging"
        existing = generation["staged_attempts"].get(logical_partition_key)
        status = "reused" if existing == attempt_id else "staged"
        if existing is not None and existing != attempt_id:
            raise AssertionError("collector replaced an already staged partition")
        generation["staged_attempts"][logical_partition_key] = attempt_id
        self.stage_calls.append(
            {
                "generation_id": generation_id,
                "logical_partition_key": logical_partition_key,
                "attempt_id": attempt_id,
                "status": status,
            }
        )
        return {
            "status": status,
            "generation_id": generation_id,
            "logical_partition_key": logical_partition_key,
            "attempt_id": attempt_id,
        }

    def publish_stock_basic_generation(self, generation_id):
        generation = self.generations[generation_id]
        assert generation["status"] == "staging"
        assert set(generation["staged_attempts"]) == set(STOCK_PARTITIONS)
        generation["status"] = "published"
        self.active_generation_id = generation_id
        self.publish_calls.append(generation_id)
        return self._snapshot(generation_id)

    def active_stock_basic_generation(self):
        self.active_calls += 1
        if self.active_generation_id is None:
            return None
        return self._snapshot(self.active_generation_id)


def _collector(*, store, transport, now):
    from app.research_partitions import load_temporal_partition_contract
    contract = load_temporal_partition_contract("data/research_partitions/frozen-v1.json")
    return ControlledTushareCollector(
        store=store,
        token=TOKEN,
        api_url="https://example.invalid/tushare",
        allowed_hosts=("example.invalid",),
        transport=transport,
        clock=TrustedClock(now),
        max_attempts=1,
        sleeper=lambda _seconds: None,
        temporal_contract=contract,
        temporal_role="contaminated_diagnostic",
        temporal_contract_sha256=contract["contract_sha256"],
        temporal_start_date="2024-01-01",
        temporal_end_date="2026-07-03",
    )


def _assert_published(store, generation_id):
    assert store.publish_calls == [generation_id]
    assert store.active_generation_id == generation_id
    assert store.generations[generation_id]["status"] == "published"
    assert store.active_calls >= 1
    assert store.promotions == []


def test_new_generation_fetches_and_stages_all_eight_partitions_before_publish():
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    store = GenerationContractStore()
    transport = PartitionRecordingTransport()
    collector = _collector(store=store, transport=transport, now=now)

    collector.collect_stock_basic_generation(now=now, resume=True)

    generation_id = store.active_generation_id
    assert store.begin_calls == [{"now": now, "force_new": False}]
    assert [call["partition_key"] for call in transport.calls] == list(
        STOCK_PARTITIONS
    )
    assert [call["logical_partition_key"] for call in store.stage_calls] == list(
        STOCK_PARTITIONS
    )
    assert len(store.attempts) == 8
    assert set(store.generations[generation_id]["staged_attempts"]) == set(
        STOCK_PARTITIONS
    )
    _assert_published(store, generation_id)


def test_fresh_partial_generation_fetches_only_missing_partitions():
    started_at = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(minutes=59)
    staged = STOCK_PARTITIONS[:3]
    missing = STOCK_PARTITIONS[3:]
    store = GenerationContractStore()
    generation_id = store.seed_staging_generation(
        started_at=started_at,
        staged_partitions=staged,
    )
    transport = PartitionRecordingTransport()
    collector = _collector(store=store, transport=transport, now=now)

    collector.collect_stock_basic_generation(now=now, resume=True)

    assert [call["partition_key"] for call in transport.calls] == list(missing)
    assert [call["logical_partition_key"] for call in store.stage_calls] == list(
        missing
    )
    assert len(store.attempts) == len(missing)
    assert set(store.generations[generation_id]["staged_attempts"]) == set(
        STOCK_PARTITIONS
    )
    _assert_published(store, generation_id)


def test_expired_partial_generation_is_abandoned_and_refetched_from_zero():
    started_at = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(hours=1, milliseconds=1)
    store = GenerationContractStore()
    expired_generation_id = store.seed_staging_generation(
        started_at=started_at,
        staged_partitions=STOCK_PARTITIONS[:6],
    )
    transport = PartitionRecordingTransport()
    collector = _collector(store=store, transport=transport, now=now)

    collector.collect_stock_basic_generation(now=now, resume=True)

    assert store.generations[expired_generation_id]["status"] == "abandoned"
    published_generation_id = store.active_generation_id
    assert published_generation_id != expired_generation_id
    assert [call["partition_key"] for call in transport.calls] == list(
        STOCK_PARTITIONS
    )
    assert [call["logical_partition_key"] for call in store.stage_calls] == list(
        STOCK_PARTITIONS
    )
    assert len(store.attempts) == 8
    _assert_published(store, published_generation_id)


def test_complete_staging_generation_is_published_after_crash_without_network():
    started_at = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(minutes=30)
    store = GenerationContractStore()
    generation_id = store.seed_staging_generation(
        started_at=started_at,
        staged_partitions=STOCK_PARTITIONS,
    )
    transport = PartitionRecordingTransport()
    collector = _collector(store=store, transport=transport, now=now)

    collector.collect_stock_basic_generation(now=now, resume=True)

    assert transport.calls == []
    assert store.attempts == []
    assert store.stage_calls == []
    _assert_published(store, generation_id)


def test_real_receipt_store_stages_generation_without_legacy_receipt_promotion(tmp_path):
    now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
    store = PITReceiptStore(str(tmp_path / "store"))
    transport = PartitionRecordingTransport()
    collector = _collector(store=store, transport=transport, now=now)

    report = collector.collect_stock_basic_generation(now=now, resume=True)

    assert report["status"] == "published"
    assert report["fetched_partition_count"] == 8
    assert store.receipt_count() == 0
    active = store.active_stock_basic_generation()
    assert active["generation_id"] == report["generation_id"]
    assert store.verify_stock_basic_generation()["verification_status"] == "passed"
    attempts = store.fetch_attempts()
    assert len(attempts) == 8
    assert {row["terminal_status"] for row in attempts} == {"generation_staged"}
