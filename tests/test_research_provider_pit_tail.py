import copy
import json

import pytest

from app.research_provider_evidence_partitions import (
    PROVIDER_EVIDENCE_CONTRACT_PATH,
    load_provider_evidence_temporal_contract,
)
from app.research_provider_pit_tail import (
    ProviderPITTailError,
    build_provider_pit_tail_plan,
    validate_calendar_pair,
    validate_provider_envelope,
    validate_provider_pit_tail_plan,
)


def _authority():
    return load_provider_evidence_temporal_contract(
        PROVIDER_EVIDENCE_CONTRACT_PATH,
        repo_root=PROVIDER_EVIDENCE_CONTRACT_PATH.parents[4],
    )


def _envelope(fields, rows):
    return json.dumps(
        {"code": 0, "msg": None, "data": {"fields": fields, "items": rows}},
        separators=(",", ":"),
    ).encode()


def test_plan_freezes_exact_six_calls_and_legacy_lineage_without_adopting_old_run():
    plan = build_provider_pit_tail_plan(_authority())
    validate_provider_pit_tail_plan(plan, _authority())
    assert plan["schema"] == "provider-pit-tail-plan/v1"
    assert plan["data_cutoff"] == "2026-07-10"
    assert plan["planned_calls"] == 6
    assert [call["endpoint"] for call in plan["calls"]] == [
        "trade_cal",
        "trade_cal",
        "bak_basic",
        "stock_st",
        "suspend_d",
        "namechange",
    ]
    assert [call["row_cap"] for call in plan["calls"]] == [8, 8, 7000, 10000, 10000, 10000]
    assert plan["endpoint_counts"] == {
        "adj_factor": 0,
        "bak_basic": 1,
        "daily": 0,
        "namechange": 1,
        "stock_basic": 0,
        "stock_st": 1,
        "stk_limit": 0,
        "suspend_d": 1,
        "trade_cal": 2,
    }
    assert plan["legacy_risk_lineage"]["run_id"].startswith("daaf5206")
    assert plan["legacy_risk_lineage"]["historical_run_success_adopted"] is False
    assert plan["legacy_risk_lineage"]["status"] == "invalid_fail_closed"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda plan: plan["calls"].pop(),
        lambda plan: plan["calls"].append(copy.deepcopy(plan["calls"][0])),
        lambda plan: plan["calls"].reverse(),
        lambda plan: plan["calls"][0]["params"].update({"end_date": "20260711"}),
        lambda plan: plan["calls"][2].update({"row_cap": 10000}),
        lambda plan: plan["calls"][5]["fields"].reverse(),
        lambda plan: plan["legacy_risk_lineage"].update({"historical_run_success_adopted": True}),
        lambda plan: plan["authority"].update({"contract_sha256": "0" * 64}),
    ],
)
def test_plan_full_resign_cannot_change_call_or_legacy_contract(mutator):
    plan = build_provider_pit_tail_plan(_authority())
    mutator(plan)
    plan["plan_canonical_sha256"] = "0" * 64
    with pytest.raises(ProviderPITTailError):
        validate_provider_pit_tail_plan(plan, _authority())


def test_trade_cal_envelope_requires_exact_fields_full_window_and_nonempty_rows():
    call = build_provider_pit_tail_plan(_authority())["calls"][0]
    rows = [
        ["SSE", f"202607{day:02d}", 1 if day in {6, 7, 8, 9, 10} else 0, "20260703"]
        for day in range(4, 11)
    ]
    result = validate_provider_envelope(call, _envelope(call["fields"], rows))
    assert result["row_count"] == 7
    assert result["rows"][0]["cal_date"] == "20260704"

    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, _envelope(call["fields"], []))
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, _envelope(list(reversed(call["fields"])), rows))
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, _envelope(call["fields"], rows[:-1]))


def test_calendar_pair_requires_identical_dates_and_open_sessions():
    plan = build_provider_pit_tail_plan(_authority())
    rows = [
        [exchange, f"202607{day:02d}", 1 if day in {6, 7, 8, 9, 10} else 0, "20260703"]
        for day in range(4, 11)
        for exchange in ["SSE"]
    ]
    sse = validate_provider_envelope(plan["calls"][0], _envelope(plan["calls"][0]["fields"], rows))
    szse_rows = [["SZSE", *row[1:]] for row in rows]
    szse = validate_provider_envelope(
        plan["calls"][1], _envelope(plan["calls"][1]["fields"], szse_rows)
    )
    assert validate_calendar_pair(sse, szse) == "2026-07-10"
    szse["rows"][-1]["is_open"] = 0
    with pytest.raises(ProviderPITTailError):
        validate_calendar_pair(sse, szse)


def test_bak_basic_is_cutoff_anchor_with_unique_supported_codes_and_no_future_list_date():
    call = build_provider_pit_tail_plan(_authority())["calls"][2]
    valid = [["20260710", "600000.SH", "浦发银行", "银行", "19991110"]]
    assert validate_provider_envelope(call, _envelope(call["fields"], valid))["row_count"] == 1
    for invalid in (
        [["20260709", "600000.SH", "浦发银行", "银行", "19991110"]],
        [["20260710", "600000.SH", "A", "银行", "20260711"]],
        valid + valid,
        [["20260710", "12345.X", "未知", "", "20200101"]],
    ):
        with pytest.raises(ProviderPITTailError):
            validate_provider_envelope(call, _envelope(call["fields"], invalid))


@pytest.mark.parametrize("call_index", [3, 4])
def test_same_day_risk_rows_require_cutoff_trade_date_and_unique_symbols(call_index):
    call = build_provider_pit_tail_plan(_authority())["calls"][call_index]
    if call["endpoint"] == "stock_st":
        valid = [["000001.SZ", "平安银行", "S", "特别处理", "20260710"]]
    else:
        valid = [["000001.SZ", "20260710", "09:30", "全天停牌"]]
    assert validate_provider_envelope(call, _envelope(call["fields"], valid))["row_count"] == 1
    invalid = copy.deepcopy(valid)
    invalid[0][call["fields"].index("trade_date")] = "20260709"
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, _envelope(call["fields"], invalid))
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, _envelope(call["fields"], valid + valid))


def test_namechange_tail_requires_effective_start_in_tail_and_announcement_by_cutoff():
    call = build_provider_pit_tail_plan(_authority())["calls"][5]
    valid = [["000001.SZ", "新名称", "20260706", None, "20260705", "更名"]]
    assert validate_provider_envelope(call, _envelope(call["fields"], valid))["row_count"] == 1
    for invalid in (
        [["000001.SZ", "新名称", "20260703", None, "20260703", "更名"]],
        [["000001.SZ", "新名称", "20260706", None, "20260711", "更名"]],
        [["000001.SZ", "新名称", "20260706", "20260705", "20260705", "更名"]],
        [["000001.SZ", "新名称", "20260706", None, None, "更名"]],
    ):
        with pytest.raises(ProviderPITTailError):
            validate_provider_envelope(call, _envelope(call["fields"], invalid))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b'{"code":1,"msg":"failed","data":{"fields":[],"items":[]}}',
        b'{"code":0,"msg":null,"data":{"fields":[],"items":null}}',
        b'{"code":0,"code":0,"data":{"fields":[],"items":[]}}',
        b'{"code":0,"msg":null,"data":{"fields":[],"items":[],"value":NaN}}',
    ],
)
def test_provider_envelope_rejects_empty_malformed_error_and_noncanonical_json(payload):
    call = build_provider_pit_tail_plan(_authority())["calls"][5]
    with pytest.raises(ProviderPITTailError):
        validate_provider_envelope(call, payload)
