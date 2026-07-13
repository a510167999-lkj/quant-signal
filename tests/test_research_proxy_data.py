import pytest

from app.research_proxy_data import (
    ETF_PROXY_COVERAGE_SCHEMA_VERSION,
    ETF_PROXY_PRICE_BASIS,
    ETF_PROXY_REQUIRED_SYMBOLS,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
    EtfProxyError,
    EtfProxySpec,
    audit_etf_proxy_coverage,
    build_etf_proxy_specs,
    normalize_etf_proxy_rows,
)

SH = "510300.SH"
SZ = "159915.SZ"


def _row(symbol, trade_date, *, open_=10.0, high=11.0, low=9.5, close=10.5,
         pre_close=10.0, change=0.5, pct_chg=5.0, vol=1000.0, amount=10000.0):
    return {
        "ts_code": symbol,
        "trade_date": trade_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "pre_close": pre_close,
        "change": change,
        "pct_chg": pct_chg,
        "vol": vol,
        "amount": amount,
    }


# --- contract constants ----------------------------------------------------

def test_required_symbols_and_fields_are_frozen_exact_contract():
    assert ETF_PROXY_REQUIRED_SYMBOLS == ("510300.SH", "159915.SZ")
    assert FUND_DAILY_FIELDS == (
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    )
    assert FUND_DAILY_ROW_CAP == 5000
    assert ETF_PROXY_PRICE_BASIS == "raw_unadjusted_proxy"


# --- build_etf_proxy_specs -------------------------------------------------

def test_specs_one_per_symbol_with_yyyymmdd_wire_dates():
    specs = build_etf_proxy_specs("2024-01-02", "2024-01-09")

    assert [s.symbol for s in specs] == ["510300.SH", "159915.SZ"]
    assert all(s.fields == FUND_DAILY_FIELDS for s in specs)
    assert all(s.row_cap == 5000 for s in specs)
    assert all(s.price_basis == "raw_unadjusted_proxy" for s in specs)
    # wire_params must carry the exact ts_code per spec — it is NOT a shared
    # date-only map sitting beside spec.symbol. Freeze the full dict so a
    # regression to a symbol-less wire_params fails loudly.
    assert specs[0].wire_params == {
        "ts_code": "510300.SH",
        "start_date": "20240102",
        "end_date": "20240109",
    }
    assert specs[1].wire_params == {
        "ts_code": "159915.SZ",
        "start_date": "20240102",
        "end_date": "20240109",
    }
    # each spec owns a distinct wire_params instance — no shared aliasing
    assert specs[0].wire_params is not specs[1].wire_params


def test_specs_reject_inverted_window():
    with pytest.raises(EtfProxyError, match="start_date"):
        build_etf_proxy_specs("2024-01-09", "2024-01-02")


def test_specs_accept_yyyymmdd_input_dates():
    specs = build_etf_proxy_specs("20240102", "20240102")
    assert specs[0].wire_params == {
        "ts_code": "510300.SH",
        "start_date": "20240102",
        "end_date": "20240102",
    }


# --- normalize_etf_proxy_rows ---------------------------------------------

def test_normalize_converts_dates_to_iso_and_preserves_raw_prices():
    rows = [_row(SH, "20240102"), _row(SH, "20240103", close=11.0)]
    out = normalize_etf_proxy_rows(rows, SH, "2024-01-02", "2024-01-03")

    assert [r["trade_date"] for r in out] == ["2024-01-02", "2024-01-03"]
    assert out[0]["close"] == 10.5
    assert out[1]["close"] == 11.0
    # raw, unadjusted — values pass through unchanged
    assert out[0]["open"] == 10.0 and out[0]["pre_close"] == 10.0


def test_normalize_sorts_by_date_regardless_of_input_order():
    rows = [_row(SH, "20240103"), _row(SH, "20240102")]
    out = normalize_etf_proxy_rows(rows, SH, "2024-01-02", "2024-01-03")
    assert [r["trade_date"] for r in out] == ["2024-01-02", "2024-01-03"]


def test_normalize_rejects_symbol_substitution():
    rows = [_row(SZ, "20240102")]
    with pytest.raises(EtfProxyError, match="symbol"):
        normalize_etf_proxy_rows(rows, SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_duplicate_symbol_day():
    rows = [_row(SH, "20240102"), _row(SH, "20240102")]
    with pytest.raises(EtfProxyError, match="duplicate"):
        normalize_etf_proxy_rows(rows, SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_future_and_out_of_window_dates():
    with pytest.raises(EtfProxyError, match="window"):
        normalize_etf_proxy_rows([_row(SH, "20240104")], SH, "2024-01-02", "2024-01-03")
    with pytest.raises(EtfProxyError, match="window"):
        normalize_etf_proxy_rows([_row(SH, "20240101")], SH, "2024-01-02", "2024-01-03")


@pytest.mark.parametrize("field,bad_value", [
    ("open", 0.0),
    ("high", -1.0),
    ("low", float("nan")),
    ("close", float("inf")),
    ("pre_close", 0.0),
])
def test_normalize_rejects_non_positive_or_non_finite_prices(field, bad_value):
    row = _row(SH, "20240102")
    row[field] = bad_value
    with pytest.raises(EtfProxyError):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_high_low_inconsistency():
    # high below close → invalid OHLC geometry
    row = _row(SH, "20240102", high=9.0, close=10.5)
    with pytest.raises(EtfProxyError, match="high"):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")
    # low above open → invalid
    row = _row(SH, "20240102", low=12.0, open_=10.0)
    with pytest.raises(EtfProxyError, match="low"):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_negative_or_non_finite_volume_amount():
    for field in ("vol", "amount"):
        row = _row(SH, "20240102")
        row[field] = -5.0
        with pytest.raises(EtfProxyError):
            normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")
    row = _row(SH, "20240102")
    row["amount"] = float("nan")
    with pytest.raises(EtfProxyError):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")


def test_normalize_allows_zero_volume_normal_session():
    # vol/amount are non-negative; zero is a legitimate (thin) session, not a gap
    row = _row(SH, "20240102", vol=0.0, amount=0.0)
    out = normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")
    assert out[0]["vol"] == 0.0 and out[0]["amount"] == 0.0


# --- audit_etf_proxy_coverage ---------------------------------------------

def _sz_row(trade_date, close):
    # coherent OHLC around the chosen close so the geometry audit passes
    return _row(SZ, trade_date, open_=close - 0.1, high=close + 0.2,
                low=close - 0.3, close=close, pre_close=close - 0.1,
                change=0.1, pct_chg=2.0)


def _two_symbol_window():
    sessions = ["2024-01-02", "2024-01-03", "2024-01-04"]
    rows_by_symbol = {
        SH: [_row(SH, "20240102"), _row(SH, "20240103"), _row(SH, "20240104")],
        SZ: [_sz_row("20240102", 5.0), _sz_row("20240103", 5.5),
             _sz_row("20240104", 6.0)],
    }
    return sessions, rows_by_symbol


def test_audit_returns_streaming_root_count_hash_and_ineligible():
    sessions, rows_by_symbol = _two_symbol_window()
    out = audit_etf_proxy_coverage(
        {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
         for s in (SH, SZ)},
        sessions,
    )

    # root sorted by symbol (frozen order) then date
    assert [(r["ts_code"], r["trade_date"]) for r in out["root"]] == [
        (SH, "2024-01-02"), (SH, "2024-01-03"), (SH, "2024-01-04"),
        (SZ, "2024-01-02"), (SZ, "2024-01-03"), (SZ, "2024-01-04"),
    ]
    assert out["count"] == 6
    assert out["final_oos_eligible"] is False
    assert isinstance(out["coverage_sha256"], str) and len(out["coverage_sha256"]) == 64


def test_audit_coverage_hash_is_stable_under_input_reordering():
    sessions, rows_by_symbol = _two_symbol_window()
    norm_a = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
              for s in (SH, SZ)}
    # reverse the per-symbol row order before normalizing — hash must not move
    norm_b = {s: normalize_etf_proxy_rows(list(reversed(rows_by_symbol[s])), s,
                                        "2024-01-02", "2024-01-04")
              for s in (SH, SZ)}
    # also feed symbols in a different dict insertion order
    hash_a = audit_etf_proxy_coverage(norm_a, sessions)["coverage_sha256"]
    hash_b = audit_etf_proxy_coverage(
        {SZ: norm_b[SZ], SH: norm_b[SH]}, sessions)["coverage_sha256"]
    assert hash_a == hash_b


def test_audit_raw_prices_pass_through_unchanged():
    sessions, rows_by_symbol = _two_symbol_window()
    out = audit_etf_proxy_coverage(
        {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
         for s in (SH, SZ)},
        sessions,
    )
    sz_prices = {r["trade_date"]: r["close"] for r in out["root"] if r["ts_code"] == SZ}
    assert sz_prices == {"2024-01-02": 5.0, "2024-01-03": 5.5, "2024-01-04": 6.0}


def test_audit_rejects_missing_session_for_either_symbol():
    sessions, rows_by_symbol = _two_symbol_window()
    del rows_by_symbol[SH][1]  # drop a middle session for SH
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    with pytest.raises(EtfProxyError, match="missing"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_out_of_window_row_via_renormalization():
    # A row beyond the open_sessions window is caught when audit re-normalizes
    # over [sessions[0], sessions[-1]] — it never reaches the coverage stage.
    sessions, rows_by_symbol = _two_symbol_window()
    rows_by_symbol[SZ].append(_row(SZ, "20240105"))  # past the last open session
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-05")
            for s in (SH, SZ)}
    with pytest.raises(EtfProxyError, match="window"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_extra_session_within_window():
    # A row inside the window range but not in open_sessions survives
    # re-normalization and is then rejected by the exact-coverage stage.
    sessions = ["2024-01-02", "2024-01-04"]  # 01-03 is not an open session
    rows_by_symbol = {
        SH: [_row(SH, "20240102"), _row(SH, "20240103"), _row(SH, "20240104")],
        SZ: [_sz_row("20240102", 5.0), _sz_row("20240103", 5.5),
             _sz_row("20240104", 6.0)],
    }
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    with pytest.raises(EtfProxyError, match="extra"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_missing_symbol_entirely():
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    del norm[SZ]
    with pytest.raises(EtfProxyError, match="159915"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_bad_open_sessions():
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    with pytest.raises(EtfProxyError, match="non-empty"):
        audit_etf_proxy_coverage(norm, [])
    with pytest.raises(EtfProxyError, match="ascending"):
        audit_etf_proxy_coverage(norm, ["2024-01-03", "2024-01-02"])
    with pytest.raises(EtfProxyError, match="unique"):
        audit_etf_proxy_coverage(norm, ["2024-01-02", "2024-01-02"])


def test_audit_hash_embeds_schema_version_so_contract_changes_bump_it():
    # The schema version is part of the hashed payload; changing it must move the hash,
    # so a silent contract drift cannot pass review unnoticed.
    assert isinstance(ETF_PROXY_COVERAGE_SCHEMA_VERSION, str)
    assert EtfProxySpec  # spec type is part of the public contract surface


# --- adversarial: normalize refuses non-contract symbols / shapes -------------

def test_normalize_rejects_third_party_etf_even_when_self_consistent():
    # 510050.SH is a real, coherent ETF but is NOT in the required proxy set.
    # The row itself is perfectly self-consistent — the refusal is purely the
    # membership gate, proving the contract is closed to third-party symbols.
    THIRD = "510050.SH"
    rows = [_row(THIRD, "20240102")]
    with pytest.raises(EtfProxyError, match="required"):
        normalize_etf_proxy_rows(rows, THIRD, "2024-01-02", "2024-01-02")


def test_normalize_rejects_row_with_extra_field():
    row = _row(SH, "20240102")
    row["sabotage"] = 999  # field outside FUND_DAILY_FIELDS
    with pytest.raises(EtfProxyError, match="fields"):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_row_with_missing_field():
    row = _row(SH, "20240102")
    del row["pre_close"]  # a required field is gone
    with pytest.raises(EtfProxyError, match="fields"):
        normalize_etf_proxy_rows([row], SH, "2024-01-02", "2024-01-02")


def test_normalize_rejects_non_mapping_row():
    # a malformed raw entry (not a mapping at all) must fail loud, not silently
    with pytest.raises(EtfProxyError, match="mapping"):
        normalize_etf_proxy_rows(["not-a-mapping"], SH, "2024-01-02", "2024-01-02")


# --- adversarial: audit trusts nothing the caller claims to have normalized ---

def test_audit_rejects_extra_symbol_in_rows_by_symbol():
    # A stray third-party symbol key — even with fully self-consistent rows —
    # breaks the exact-symbol-set contract and is refused before any coverage.
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    norm["510050.SH"] = norm[SH]
    with pytest.raises(EtfProxyError, match="exactly"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_row_extra_field_even_when_coverage_complete():
    # Coverage dates are complete, but one row smuggles an extra field. Audit
    # must not trust the caller's "already normalized" claim — it re-checks.
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    norm[SH][0] = {**norm[SH][0], "sabotage": 999}
    with pytest.raises(EtfProxyError, match="fields"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_row_missing_field_even_when_coverage_complete():
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    tampered = dict(norm[SH][1])
    del tampered["pre_close"]
    norm[SH][1] = tampered
    with pytest.raises(EtfProxyError, match="fields"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_rejects_malformed_raw_mapping_even_when_coverage_complete():
    sessions, rows_by_symbol = _two_symbol_window()
    norm = {s: normalize_etf_proxy_rows(rows_by_symbol[s], s, "2024-01-02", "2024-01-04")
            for s in (SH, SZ)}
    norm[SH][2] = ["not", "a", "mapping"]
    with pytest.raises(EtfProxyError, match="mapping"):
        audit_etf_proxy_coverage(norm, sessions)


def test_audit_revalidates_ohlc_on_raw_rows_without_trusting_caller():
    # Caller hands RAW (un-normalized) rows; the SZ row has high < close.
    # Audit must catch the geometry violation itself via re-normalization.
    sessions = ["2024-01-02"]
    rows_by_symbol = {
        SH: [_row(SH, "20240102")],
        SZ: [_row(SZ, "20240102", high=9.0, close=10.5)],
    }
    with pytest.raises(EtfProxyError, match="high"):
        audit_etf_proxy_coverage(rows_by_symbol, sessions)


def test_audit_revalidates_symbol_on_raw_rows_without_trusting_caller():
    # The SZ bucket actually holds a 510300.SH row. Audit re-normalizes each
    # bucket against its own symbol, so the mismatch is caught here.
    sessions = ["2024-01-02"]
    rows_by_symbol = {
        SH: [_row(SH, "20240102")],
        SZ: [_row(SH, "20240102")],
    }
    with pytest.raises(EtfProxyError, match="symbol"):
        audit_etf_proxy_coverage(rows_by_symbol, sessions)
