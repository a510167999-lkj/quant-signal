import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import current_pool_source
from app.current_pool_source import (
    build_current_pool_descriptor,
    fetch_jiaoch_current_pool_descriptor,
    verify_current_pool_universe_descriptor,
)
from app.current_pool import classify_current_pool_item
from app import jobs


def _fetch(exchange: str, list_status: str, fields: tuple[str, ...]):
    assert exchange in {"SSE", "SZSE"}
    assert list_status in {"L", "D", "P", "G"}
    offset = {"L": 0, "D": 1, "P": 2, "G": 3}[list_status]
    symbol = f"{1 + offset:06d}" if exchange == "SZSE" else f"{600000 + offset:06d}"
    row = [
        f"{symbol}.SZ" if exchange == "SZSE" else f"{symbol}.SH",
        symbol,
        f"{exchange} {list_status}",
        exchange,
        "主板",
        list_status,
        "19910403",
        None,
    ]
    return {"code": 0, "msg": None, "data": {"fields": list(fields), "items": [row]}}


def _resign_descriptor(payload: dict) -> dict:
    candidate = json.loads(json.dumps(payload))
    for receipt in candidate["partition_receipts"]:
        params = receipt["params"]
        partition_items = sorted(
            (
                item
                for item in candidate["items"]
                if item["exchange"] == params["exchange"]
                and item["list_status"] == params["list_status"]
            ),
            key=lambda item: item["ts_code"],
        )
        receipt["normalized_row_count"] = len(partition_items)
        receipt["raw_row_count"] = len(partition_items) + receipt[
            "excluded_invalid_identity_count"
        ]
        receipt["rows_sha256"] = current_pool_source.hashlib.sha256(
            current_pool_source._canonical_json(partition_items)
        ).hexdigest()
    unsigned = {
        key: value for key, value in candidate.items() if key != "descriptor_sha256"
    }
    candidate["descriptor_sha256"] = current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(unsigned)
    ).hexdigest()
    return candidate


def _legacy_nonlisted_rows(fields: tuple[str, ...]) -> list[list[object]]:
    public_rows = [
        {
            "ts_code": "T600018.SH",
            "symbol": "T600018",
            "name": "上港集箱(退)",
            "exchange": "SSE",
            "market": None,
            "list_status": "D",
            "list_date": "20000719",
            "delist_date": "20061226",
        },
        {
            "ts_code": "TS0018.SH",
            "symbol": "TS0018",
            "name": "上港集箱(退)",
            "exchange": "SSE",
            "market": "主板",
            "list_status": "D",
            "list_date": "20000719",
            "delist_date": "20061226",
        },
    ]
    return [[row[field] for field in fields] for row in public_rows]


def _fetch_with_legacy_nonlisted_identities(
    exchange: str, list_status: str, fields: tuple[str, ...]
):
    response = _fetch(exchange, list_status, fields)
    if (exchange, list_status) == ("SSE", "D"):
        response["data"]["items"].extend(_legacy_nonlisted_rows(fields))
    return response


def _resign_only(payload: dict) -> dict:
    candidate = json.loads(json.dumps(payload))
    unsigned = {
        key: value for key, value in candidate.items() if key != "descriptor_sha256"
    }
    candidate["descriptor_sha256"] = current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(unsigned)
    ).hexdigest()
    return candidate


def test_audits_legacy_nonlisted_invalid_identities_without_universe_items(
    tmp_path: Path,
):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_with_legacy_nonlisted_identities,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["excluded_invalid_identity_count"] == 2
    assert not {
        "T600018.SH",
        "TS0018.SH",
    } & {item["ts_code"] for item in payload["items"]}
    receipt = next(
        item
        for item in payload["partition_receipts"]
        if item["params"] == {"exchange": "SSE", "list_status": "D"}
    )
    assert set(receipt) == {
        "api_name",
        "params",
        "raw_row_count",
        "normalized_row_count",
        "rows_sha256",
        "excluded_invalid_identity_count",
        "excluded_invalid_identity_rows",
        "excluded_rows_sha256",
    }
    assert receipt["raw_row_count"] == 3
    assert receipt["normalized_row_count"] == 1
    assert receipt["excluded_invalid_identity_count"] == 2
    assert receipt["excluded_invalid_identity_rows"] == sorted(
        [
            dict(zip(current_pool_source.STOCK_BASIC_FIELDS, row, strict=True))
            for row in _legacy_nonlisted_rows(current_pool_source.STOCK_BASIC_FIELDS)
        ],
        key=current_pool_source._canonical_json,
    )
    assert receipt["excluded_rows_sha256"] == current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(
            receipt["excluded_invalid_identity_rows"]
        )
    ).hexdigest()
    assert verify_current_pool_universe_descriptor(payload)["descriptor_sha256"] == payload[
        "descriptor_sha256"
    ]


def test_listed_partition_never_omits_invalid_identity(tmp_path: Path):
    def listed_legacy_identity(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if (exchange, list_status) == ("SSE", "L"):
            row = dict(
                zip(fields, response["data"]["items"][0], strict=True)
            )
            row.update(ts_code="T600018.SH", symbol="T600018")
            response["data"]["items"][0] = [row[field] for field in fields]
        return response

    with pytest.raises(ValueError, match="identity is inconsistent"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=listed_legacy_identity,
        )


def test_nonlisted_identity_exclusion_does_not_hide_other_identity_conflicts(
    tmp_path: Path,
):
    def mismatched_legacy_identity(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if (exchange, list_status) == ("SSE", "D"):
            row = dict(
                zip(fields, response["data"]["items"][0], strict=True)
            )
            row.update(ts_code="T600018.SZ", symbol="T600018")
            response["data"]["items"][0] = [row[field] for field in fields]
        return response

    with pytest.raises(ValueError, match="identity is inconsistent"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=mismatched_legacy_identity,
        )


@pytest.mark.parametrize(
    ("exchange", "list_status", "ts_code", "symbol"),
    [
        ("SSE", "D", "ABC.SH", "ABC"),
        ("SSE", "P", "12345.SH", "12345"),
        ("SSE", "G", "600018X.SH", "600018X"),
        ("SZSE", "D", "!!!.SZ", "!!!"),
        ("SSE", "D", "T600019.SH", "T600019"),
        ("SSE", "D", "TS0019.SH", "TS0019"),
        ("SSE", "P", "T600018.SH", "T600018"),
        ("SSE", "G", "TS0018.SH", "TS0018"),
    ],
)
def test_unknown_nonlisted_invalid_identities_remain_fail_closed(
    tmp_path: Path,
    exchange: str,
    list_status: str,
    ts_code: str,
    symbol: str,
):
    def unknown_identity(
        shard_exchange: str, shard_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(shard_exchange, shard_status, fields)
        if (shard_exchange, shard_status) == (exchange, list_status):
            row = dict(
                zip(fields, response["data"]["items"][0], strict=True)
            )
            row.update(
                ts_code=ts_code,
                symbol=symbol,
                name="上港集箱(退)",
                market="主板",
            )
            response["data"]["items"][0] = [row[field] for field in fields]
        return response

    with pytest.raises(ValueError, match="identity is inconsistent"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=unknown_identity,
        )


@pytest.mark.parametrize(
    ("ts_code", "symbol", "market"),
    [
        ("T600018.SH", "T600018", "主板"),
        ("T600018.SH", "T600018", ""),
        ("T600018.SH", "T600018", 0),
        ("T600018.SH", "T600018", ["主板"]),
        ("TS0018.SH", "TS0018", None),
        ("TS0018.SH", "TS0018", "创业板"),
        ("TS0018.SH", "TS0018", {"value": "主板"}),
    ],
)
def test_observed_identity_exception_requires_exact_market_value_and_type(
    tmp_path: Path, ts_code: str, symbol: str, market
):
    def wrong_market(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if (exchange, list_status) == ("SSE", "D"):
            row = dict(
                zip(fields, response["data"]["items"][0], strict=True)
            )
            row.update(
                ts_code=ts_code,
                symbol=symbol,
                name="上港集箱(退)",
                market=market,
            )
            response["data"]["items"][0] = [row[field] for field in fields]
        return response

    with pytest.raises(ValueError, match="identity is inconsistent"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=wrong_market,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "其他退市股"),
        ("name", 600018),
        ("list_date", 20000719),
        ("list_date", "20001340"),
        ("delist_date", ["20061226"]),
        ("delist_date", "20060230"),
    ],
)
def test_observed_identity_exception_still_validates_exact_name_and_dates(
    tmp_path: Path, field: str, value
):
    def malformed_observed_row(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if (exchange, list_status) == ("SSE", "D"):
            row = dict(
                zip(fields, _legacy_nonlisted_rows(fields)[0], strict=True)
            )
            row[field] = value
            response["data"]["items"][0] = [row[item] for item in fields]
        return response

    with pytest.raises((TypeError, ValueError)):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=malformed_observed_row,
        )


def test_verifier_requires_canonical_top_level_item_order(tmp_path: Path):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    payload["items"].reverse()

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(_resign_descriptor(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload, receipt: payload.update(unexpected="not-in-contract"),
        lambda payload, receipt: receipt.update(unexpected="not-in-contract"),
        lambda payload, receipt: payload.update(excluded_invalid_identity_count=1),
        lambda payload, receipt: receipt.update(raw_row_count=2),
        lambda payload, receipt: receipt.update(normalized_row_count=2),
        lambda payload, receipt: receipt.update(excluded_invalid_identity_count=1),
        lambda payload, receipt: receipt.update(excluded_rows_sha256="0" * 64),
        lambda payload, receipt: receipt["excluded_invalid_identity_rows"][0].update(
            symbol="600018", ts_code="600018.SH"
        ),
        lambda payload, receipt: receipt["excluded_invalid_identity_rows"].reverse(),
    ],
)
def test_verifier_recomputes_excluded_identity_receipts(
    tmp_path: Path, mutate
):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_with_legacy_nonlisted_identities,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    receipt = next(
        item
        for item in payload["partition_receipts"]
        if item["params"] == {"exchange": "SSE", "list_status": "D"}
    )
    mutate(payload, receipt)

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(_resign_only(payload))


def test_verifier_forbids_an_excluded_identity_from_reappearing_as_an_item(
    tmp_path: Path,
):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch_with_legacy_nonlisted_identities,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    receipt = next(
        item
        for item in payload["partition_receipts"]
        if item["params"] == {"exchange": "SSE", "list_status": "D"}
    )
    raw = receipt["excluded_invalid_identity_rows"][0]
    payload["items"].append({**raw, "market_evidence": "provider"})
    receipt["normalized_row_count"] += 1
    receipt["rows_sha256"] = current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(
            sorted(
                (
                    item
                    for item in payload["items"]
                    if item["exchange"] == "SSE" and item["list_status"] == "D"
                ),
                key=lambda item: item["ts_code"],
            )
        )
    ).hexdigest()
    receipt["raw_row_count"] += 1

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(_resign_only(payload))


def test_builds_content_addressed_stock_master_with_incomplete_risk_snapshot(tmp_path: Path):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch,
    )

    path = Path(result["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == f"{payload['descriptor_sha256']}.json"
    assert payload["schema"] == "current-pool-universe-input/v1"
    assert payload["source_id"] == "jiaoch"
    assert payload["as_of"] == "2026-07-13"
    assert payload["retrieved_at"] == "2026-07-13T09:30:00+08:00"
    assert payload["risk_snapshot_complete"] is False
    assert payload["production_recommendation_eligible"] is False
    assert payload["risk_coverage"] == {
        "namechange": "not_collected",
        "stock_st": "not_collected",
        "suspend_d": "not_collected",
    }
    assert len(payload["items"]) == 8
    assert len(payload["partition_receipts"]) == 8
    assert payload["excluded_invalid_identity_count"] == 0
    assert all(
        set(receipt)
        == {
            "api_name",
            "params",
            "raw_row_count",
            "normalized_row_count",
            "rows_sha256",
            "excluded_invalid_identity_count",
            "excluded_invalid_identity_rows",
            "excluded_rows_sha256",
        }
        for receipt in payload["partition_receipts"]
    )
    assert verify_current_pool_universe_descriptor(payload)["descriptor_sha256"] == payload["descriptor_sha256"]
    assert set(payload["items"][0]) == {
        "ts_code",
        "symbol",
        "name",
        "market",
        "exchange",
        "list_status",
        "list_date",
        "delist_date",
        "market_evidence",
    }
    assert {item["market_evidence"] for item in payload["items"]} == {"provider"}
    assert result["created"] is True
    loaded = jobs._load_current_pool_descriptor(str(path), "current-pool-universe-input/v1")
    assert loaded["source_as_of"] == "2026-07-13"
    assert loaded["descriptor_sha256"] == payload["descriptor_sha256"]

    repeated = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch,
    )
    assert repeated["path"] == result["path"]
    assert repeated["created"] is False


@pytest.mark.parametrize(
    ("exchange", "list_status", "symbol", "expected_market", "expected_reason"),
    [
        ("SSE", "D", "600001", "主板", "excluded_list_status_delisted"),
        ("SZSE", "P", "300001", "创业板", "excluded_list_status_paused"),
        ("SSE", "G", "688001", "科创板", "excluded_list_status_not_listed"),
    ],
)
def test_infers_missing_market_only_for_nonlisted_rows_and_binds_receipt(
    tmp_path: Path,
    exchange: str,
    list_status: str,
    symbol: str,
    expected_market: str,
    expected_reason: str,
):
    def missing_nonlisted_market(
        shard_exchange: str, shard_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(shard_exchange, shard_status, fields)
        if shard_exchange == exchange and shard_status == list_status:
            response["data"]["items"][0][fields.index("ts_code")] = (
                f"{symbol}.SH" if exchange == "SSE" else f"{symbol}.SZ"
            )
            response["data"]["items"][0][fields.index("symbol")] = symbol
            response["data"]["items"][0][fields.index("market")] = None
        return response

    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=missing_nonlisted_market,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    item = next(row for row in payload["items"] if row["ts_code"].startswith(symbol))
    assert item["market"] == expected_market
    assert item["market_evidence"] == "inferred_nonlisted_code"
    classification = classify_current_pool_item(item)
    assert classification["eligible"] is False
    assert expected_reason in classification["reason_codes"]
    assert any(reason.startswith("excluded_list_status_") for reason in classification["reason_codes"])

    receipt = next(
        row
        for row in payload["partition_receipts"]
        if row["params"] == {"exchange": exchange, "list_status": list_status}
    )
    partition_items = sorted(
        (
            row
            for row in payload["items"]
            if row["exchange"] == exchange and row["list_status"] == list_status
        ),
        key=lambda row: row["ts_code"],
    )
    expected_hash = current_pool_source.hashlib.sha256(
        current_pool_source._canonical_json(partition_items)
    ).hexdigest()
    assert receipt["rows_sha256"] == expected_hash


def test_accepts_observed_2026_chinext_302_prefix(tmp_path: Path) -> None:
    def fetch_with_302(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if (exchange, list_status) == ("SZSE", "L"):
            values = response["data"]["items"][0]
            values[fields.index("ts_code")] = "302132.SZ"
            values[fields.index("symbol")] = "302132"
            values[fields.index("name")] = "中航成飞"
            values[fields.index("market")] = "创业板"
        return response

    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=fetch_with_302,
    )

    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    item = next(row for row in payload["items"] if row["ts_code"] == "302132.SZ")
    assert classify_current_pool_item(item) == {
        "symbol": "302132",
        "eligible": True,
        "board": "chinext",
        "reason_codes": [],
        "signal_allowed_today": True,
        "daily_entry_blocked": False,
        "daily_reason_codes": [],
    }
    assert "302132.SZ" in verify_current_pool_universe_descriptor(payload)["symbols"]


def test_missing_market_for_listed_row_remains_fail_closed(tmp_path: Path):
    def missing_listed_market(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        if exchange == "SSE" and list_status == "L":
            response["data"]["items"][0][fields.index("market")] = None
        return response

    with pytest.raises(ValueError, match="missing identity or status fields"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=missing_listed_market,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.update(list_status="L"),
        lambda item: item.update(market="创业板"),
        lambda item: item.update(market_evidence="unknown"),
        lambda item: item.pop("market_evidence"),
        lambda item: item.update(exchange="SZSE", ts_code="600001.SZ"),
    ],
)
def test_verifier_recomputes_inferred_market_and_rejects_conflicts(
    tmp_path: Path, mutation
):
    def missing_nonlisted_market(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if exchange == "SSE" and list_status == "D":
            response["data"]["items"][0][fields.index("market")] = None
        return response

    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=missing_nonlisted_market,
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    inferred = next(
        item for item in payload["items"] if item["market_evidence"] != "provider"
    )
    mutation(inferred)
    candidate = _resign_descriptor(payload)

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        verify_current_pool_universe_descriptor(candidate)


def test_provider_market_conflict_is_not_recast_as_inferred(tmp_path: Path):
    def conflicting_provider_market(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ):
        response = _fetch(exchange, list_status, fields)
        if exchange == "SSE" and list_status == "D":
            response["data"]["items"][0][fields.index("market")] = "创业板"
        return response

    with pytest.raises(ValueError, match="current-pool universe descriptor rejected"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=conflicting_provider_market,
        )


def test_rejects_incomplete_or_conflicting_stock_basic_partitions(tmp_path: Path):
    def incomplete(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        response["data"]["fields"] = list(fields[:-1])
        return response

    with pytest.raises(ValueError, match="missing requested fields"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=incomplete,
        )


@pytest.mark.parametrize("code", [False, "0"])
def test_rejects_non_integer_success_code(tmp_path: Path, code):
    def invalid_code(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        response["code"] = code
        return response

    with pytest.raises(ValueError, match="non-success envelope"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=invalid_code,
        )


def test_rejects_duplicate_fields_and_inconsistent_stock_identity(tmp_path: Path):
    def duplicate_fields(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        response["data"]["fields"].append("name")
        response["data"]["items"][0].append("duplicate")
        return response

    with pytest.raises(ValueError, match="duplicate fields"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=duplicate_fields,
        )

    def wrong_suffix(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        response["data"]["items"][0][0] = "600000.SZ"
        return response

    with pytest.raises(ValueError, match="identity is inconsistent"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=wrong_suffix,
        )


def test_audit_loader_rejects_tampering_and_false_risk_completeness(tmp_path: Path):
    result = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:30:00+08:00",
        output_dir=tmp_path,
        fetch_partition=_fetch,
    )
    path = Path(result["path"])
    original = json.loads(path.read_text(encoding="utf-8"))

    for mutation in (
        lambda value: value.update(as_of="2026-07-14"),
        lambda value: value.update(retrieved_at="2026-07-12T23:59:00+08:00"),
        lambda value: value["items"][0].update(name="tampered"),
        lambda value: value.update(risk_snapshot_complete=True),
        lambda value: value["partition_coverage"].update(partition_count=7),
        lambda value: value["partition_receipts"][0]["params"].update(exchange="SZSE"),
        lambda value: value["partition_receipts"][0].update(rows_sha256="z" * 64),
        lambda value: value["items"][0].update(market="创业板"),
    ):
        tampered = json.loads(json.dumps(original))
        mutation(tampered)
        candidate = tmp_path / "candidate.json"
        candidate.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
            jobs._load_current_pool_descriptor(str(candidate), "current-pool-universe-input/v1")

    wrong_name = tmp_path / f"{'0' * 64}.json"
    wrong_name.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs._load_current_pool_descriptor(str(wrong_name), "current-pool-universe-input/v1")


def test_jiaoch_fetch_adapter_uses_fixed_path_and_rejects_unsafe_responses(
    monkeypatch, tmp_path: Path
):
    token = "secret/test"
    calls = []
    mode = {"value": "ok"}

    class Transport:
        def __init__(self, *, proxy_url):
            assert proxy_url is None

        def post(self, **kwargs):
            calls.append(kwargs)
            exchange = json.loads(kwargs["body"])["params"]["exchange"]
            status = json.loads(kwargs["body"])["params"]["list_status"]
            fields = tuple(json.loads(kwargs["body"])["fields"].split(","))
            body = json.dumps(_fetch(exchange, status, fields)).encode()
            if mode["value"] == "reflection":
                body = token.encode()
            if mode["value"] == "invalid_json":
                body = b"not-json"
            if mode["value"] == "api_error":
                body = json.dumps({"code": 40203, "msg": "denied", "data": None}).encode()
            if mode["value"] == "semantic_reflection":
                body = b'{"code":0,"msg":"secret\\/test","data":{"fields":[],"items":[]}}'
            return SimpleNamespace(
                status=500 if mode["value"] == "http" else 200,
                body_complete=mode["value"] != "incomplete",
                body=body,
            )

    monkeypatch.setattr(
        current_pool_source,
        "resolve_tushare_source",
        lambda *args, **kwargs: SimpleNamespace(
            token=token,
            proxy_url=None,
            api_url="http://jiaoch.site",
            allowed_hosts=("jiaoch.site",),
        ),
    )
    monkeypatch.setattr(current_pool_source, "UrllibTushareTransport", Transport)

    def now():
        return datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    fetch_jiaoch_current_pool_descriptor(
        as_of="2026-07-13", output_dir=tmp_path / "ok", now_provider=now
    )
    assert len(calls) == 8
    assert {call["url"] for call in calls} == {"http://jiaoch.site/stock_basic"}
    assert all(call["max_body_bytes"] == 5 * 1024 * 1024 for call in calls)

    for unsafe in (
        "http",
        "incomplete",
        "reflection",
        "semantic_reflection",
        "invalid_json",
        "api_error",
    ):
        mode["value"] = unsafe
        with pytest.raises(ValueError):
            fetch_jiaoch_current_pool_descriptor(
                as_of="2026-07-13", output_dir=tmp_path / unsafe, now_provider=now
            )


def test_rejects_noncanonical_as_of_without_fetching(tmp_path: Path):
    called = False

    def fetch(*_args):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="canonical ISO"):
        build_current_pool_descriptor(
            as_of="20260713",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=fetch,
        )
    assert called is False


def test_jobs_exposes_jiaoch_current_pool_fetch_without_printing_credentials(
    monkeypatch, capsys, tmp_path: Path
):
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"path": str(tmp_path / "artifact.json"), "created": True}

    monkeypatch.setattr(jobs, "fetch_jiaoch_current_pool_descriptor_v2", fake_fetch)
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


def test_fetch_binds_as_of_to_shanghai_collection_day(monkeypatch, tmp_path: Path):
    class Transport:
        def __init__(self, **_kwargs):
            pass

        def post(self, **kwargs):
            request = json.loads(kwargs["body"])
            body = _fetch(
                request["params"]["exchange"],
                request["params"]["list_status"],
                tuple(request["fields"].split(",")),
            )
            return SimpleNamespace(status=200, body_complete=True, body=json.dumps(body).encode())

    monkeypatch.setattr(
        current_pool_source,
        "resolve_tushare_source",
        lambda *args, **kwargs: SimpleNamespace(
            token="secret", proxy_url=None, api_url="http://jiaoch.site"
        ),
    )
    monkeypatch.setattr(current_pool_source, "UrllibTushareTransport", Transport)
    now = datetime(2026, 7, 13, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = fetch_jiaoch_current_pool_descriptor(
        as_of="2026-07-13", output_dir=tmp_path, now_provider=lambda: now
    )
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["as_of"] == "2026-07-13"
    assert payload["retrieved_at"] == "2026-07-13T23:59:00+08:00"
    with pytest.raises(ValueError, match="collection day"):
        fetch_jiaoch_current_pool_descriptor(
            as_of="2026-07-12", output_dir=tmp_path, now_provider=lambda: now
        )


def test_rejects_partition_row_cap(tmp_path: Path):
    def too_many(exchange: str, list_status: str, fields: tuple[str, ...]):
        response = _fetch(exchange, list_status, fields)
        if exchange == "SSE" and list_status == "L":
            response["data"]["items"] = response["data"]["items"] * 6001
        return response

    with pytest.raises(ValueError, match="row cap"):
        build_current_pool_descriptor(
            as_of="2026-07-13",
            retrieved_at="2026-07-13T09:30:00+08:00",
            output_dir=tmp_path,
            fetch_partition=too_many,
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":0,"code":0,"data":{"fields":[],"items":[]}}',
        b'{"code":0,"msg":NaN,"data":{"fields":[],"items":[]}}',
    ],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(raw):
    with pytest.raises(ValueError):
        current_pool_source._strict_json_loads(raw)


def test_semantic_token_scan_catches_escaped_token():
    token = "secret/path"
    raw = b'{"code":0,"msg":"secret\\/path","data":{"fields":[],"items":[]}}'
    parsed = current_pool_source._strict_json_loads(raw)
    assert token.encode() not in raw
    assert current_pool_source._contains_semantic_token(parsed, token) is True


def test_semantic_token_scan_catches_token_inside_longer_decoded_string():
    token = "secret/path"
    raw = b'{"msg":"prefix-secret\\/path-suffix"}'
    parsed = current_pool_source._strict_json_loads(raw)
    assert token.encode() not in raw
    assert current_pool_source._contains_semantic_token(parsed, token) is True


@pytest.mark.parametrize(
    "malformed_fragment",
    [
        '"source_id":"jiaoch","source_id":"jiaoch"',
        '"unexpected":NaN',
        '"unexpected":Infinity',
    ],
)
def test_audit_loader_strictly_rejects_duplicate_keys_and_nonfinite_json(
    tmp_path: Path, malformed_fragment: str
):
    path = tmp_path / "external.json"
    path.write_text(
        "{"
        '"schema":"current-pool-history-summary/v1",'
        f"{malformed_fragment},"
        '"source_id":"jiaoch","as_of":"2026-07-13","items":[]'
        "}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs._load_current_pool_descriptor(str(path), "current-pool-history-summary/v1")
