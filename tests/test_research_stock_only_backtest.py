"""historical resolved path 切到 stock-only 市场上下文的回归测试。

本切片只改 `_run_historical_universe_research_backtest_resolved`：
- 不再加载 MARKET_PROXY_SYMBOLS 的 ETF proxy frame；
- 不再调用 `_historical_market_context` / `_historical_proxy_returns` /
  `_relative_strength_context` / `_benchmark_return`；
- 每 signal_date 用 `_stock_breadth_market_context` / `_stock_market_returns` /
  `_stock_relative_strength_context`；
- allowed_actions 显式尊重 allow_buy / allow_watch / buy_only；
- summary 走 stock benchmark，不再触发 ETF benchmark。

provider/cache spy 允许 market='a'，任何 market='etf' 立即失败。
"""
import json
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest

import app.research_backtest as research_backtest_module
from app.artifact_outcome_evidence import replay_trade_outcome
from app.config import Settings
from app.research_backtest import (
    ArtifactNativeUnresolvedExit,
    _artifact_native_time_exit_trade,
    _research_payload_from_trades,
    _run_historical_universe_research_backtest_resolved,
    run_historical_universe_research_backtest,
)
from app.research_context import (
    STOCK_MARKET_CONTEXT_SCHEMA_VERSION,
    _stock_universe_equal_weight_benchmark,
)
from app.research_composite_universe import CompositeAuditedUniverse
from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptStore
from tests.test_research_composite_universe import _annual_segments, _bar
from tests.test_research_pit_store import (
    _daily_response,
    _ingest_complete_two_day_fixture,
    _promote_controlled_receipt,
    _publish_market_session_for_audit,
)
import tests.test_research_pit_store as _pit_store_test_module
from tests.test_signals import sample_frame


def test_managed_backtest_rejects_cross_role_before_artifact_io(monkeypatch):
    contract = {"contract_sha256": "a" * 64, "roles": []}
    monkeypatch.setattr(
        research_backtest_module,
        "load_temporal_partition_contract",
        lambda path: contract,
    )
    monkeypatch.setattr(
        research_backtest_module,
        "assert_range_allowed",
        lambda *args: (_ for _ in ()).throw(ValueError("range crosses temporal role")),
    )
    monkeypatch.setattr(
        research_backtest_module,
        "_resolve_historical_universe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("artifact I/O")),
    )

    with pytest.raises(ValueError, match="crosses temporal role"):
        run_historical_universe_research_backtest(
            settings=object(), provider=object(), start_date="2023-12-20",
            end_date="2024-01-05", max_deep=1, top_n=1, hold_days=1,
            lookback_days=10, max_universe_symbols=0,
            audited_pit_universe_path="must-not-open",
            expected_coverage_audit_sha256="b" * 64,
            expected_artifact_root_sha256="c" * 64,
            temporal_contract_path="contract.json",
            expected_temporal_contract_sha256="a" * 64,
            expected_temporal_role="development",
        )


def test_managed_backtest_passes_requested_end_date_to_seed_resolution(monkeypatch):
    seen = {}
    contract = {"contract_sha256": "a" * 64, "roles": []}
    monkeypatch.setattr(
        research_backtest_module,
        "load_temporal_partition_contract",
        lambda _path: contract,
    )
    monkeypatch.setattr(
        research_backtest_module,
        "assert_range_allowed",
        lambda *args: None,
    )

    def stop_after_resolution_args(*_args, **kwargs):
        seen["end_date"] = kwargs.get("end_date")
        raise RuntimeError("resolution arguments captured")

    monkeypatch.setattr(
        research_backtest_module,
        "_resolve_historical_universe",
        stop_after_resolution_args,
    )

    with pytest.raises(RuntimeError, match="resolution arguments captured"):
        run_historical_universe_research_backtest(
            settings=object(),
            provider=object(),
            start_date="2023-01-03",
            end_date="2023-06-30",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=10,
            max_universe_symbols=0,
            audited_pit_universe_path="must-not-open",
            expected_coverage_audit_sha256="b" * 64,
            expected_artifact_root_sha256="c" * 64,
            temporal_contract_path="contract.json",
            expected_temporal_contract_sha256="a" * 64,
            expected_temporal_role="development",
        )

    assert seen["end_date"] == "2023-06-30"


def test_composite_resolver_loads_descriptor_and_seeds_full_requested_range(monkeypatch):
    class Universe:
        start_date = "2022-01-04"
        end_date = "2023-12-29"
        temporal_contract_sha256 = "c" * 64
        temporal_role = "development"

        def __init__(self):
            self.seed_calls = []

        def seed_items(self, start_date, end_date):
            self.seed_calls.append((start_date, end_date))
            return [{"symbol": "600001", "name": "composite"}]

        def close(self):
            raise AssertionError("successful resolution must leave the universe open")

    universe = Universe()
    seen = {}

    def fake_loader(
        path,
        *,
        expected_composite_root_sha256,
        expected_descriptor_file_sha256=None,
    ):
        seen.update(
            path=path,
            root=expected_composite_root_sha256,
            descriptor=expected_descriptor_file_sha256,
        )
        return universe

    monkeypatch.setattr(
        research_backtest_module,
        "load_composite_universe_descriptor",
        fake_loader,
    )

    items, resolved = research_backtest_module._resolve_historical_universe(
        settings=object(),
        use_live_snapshot=False,
        max_universe_symbols=0,
        start_date="2022-06-01",
        end_date="2023-06-30",
        composite_pit_descriptor_path="/frozen/composite.json",
        expected_composite_root_sha256="d" * 64,
        expected_temporal_contract_sha256="c" * 64,
        expected_temporal_role="development",
    )

    assert items == [{"symbol": "600001", "name": "composite"}]
    assert resolved is universe
    assert seen["descriptor"] is None
    assert seen == {
        "path": "/frozen/composite.json",
        "root": "d" * 64,
        "descriptor": None,
    }
    assert universe.seed_calls == [("2022-06-01", "2023-06-30")]


def test_managed_backtest_rejects_single_and_composite_artifacts_together():
    with pytest.raises(ValueError, match="only one"):
        run_historical_universe_research_backtest(
            settings=object(),
            provider=object(),
            start_date="2022-01-04",
            end_date="2023-12-29",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=10,
            max_universe_symbols=0,
            audited_pit_universe_path="single.sqlite3",
            composite_pit_descriptor_path="composite.json",
            expected_artifact_root_sha256="a" * 64,
            expected_composite_root_sha256="b" * 64,
            expected_coverage_audit_sha256="d" * 64,
            temporal_contract_path="contract.json",
            expected_temporal_contract_sha256="c" * 64,
            expected_temporal_role="development",
        )


def test_managed_backtest_rejects_legacy_pit_before_file_io(monkeypatch):
    monkeypatch.setattr(
        research_backtest_module,
        "_resolve_historical_universe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("file I/O")),
    )
    with pytest.raises(
        ValueError, match="legacy PIT universe is not allowed for frozen development"
    ):
        run_historical_universe_research_backtest(
            settings=object(), provider=object(), start_date="2020-01-01",
            max_deep=1, top_n=1, hold_days=1, lookback_days=10,
            max_universe_symbols=0, pit_universe_path="must-not-open",
        )


class StockOnlyProvider:
    """股票 history 正常返回；任何 market='etf' 调用立即失败（spy）。

    direction='up' 产生强势宽度（above_ma20 ~100），'down' 产生防御性宽度。
    """

    def __init__(self, direction="up", periods=100):
        self.direction = direction
        self.periods = periods
        self.etf_calls = 0
        self.stock_calls = 0
        self.frame = sample_frame(self.direction, periods=self.periods)
        self.frame["volume"] = 2_000_000
        self.frame["amount"] = self.frame["close"] * self.frame["volume"]

    def history(self, symbol, market, lookback_days=620, adjust="qfq"):
        if market == "etf":
            self.etf_calls += 1
            raise AssertionError("unexpected etf history call for %s" % symbol)
        self.stock_calls += 1
        return self.frame.copy(), "fake-stock-provider"


class FakePITUniverse:
    universe_sha256 = "a" * 64
    calendar_sha256 = "b" * 64
    source_manifest_sha256 = "c" * 64
    coverage_audit_sha256 = "d" * 64
    is_audited_store_artifact = False

    def __init__(self, items, sessions):
        self._items = {item["symbol"]: dict(item) for item in items}
        self._sessions = tuple(sorted(sessions))
        self.start_date = self._sessions[0]
        self.end_date = self._sessions[-1]

    def item_as_of(self, symbol, signal_date):
        if signal_date not in self._sessions:
            return None
        item = self._items.get(str(symbol))
        return dict(item) if item else None

    def items_as_of(self, signal_date):
        if signal_date not in self._sessions:
            raise ValueError("signal date is not covered")
        return [dict(self._items[symbol]) for symbol in sorted(self._items)]

    def open_sessions(self, start_date, end_date):
        return [date for date in self._sessions if start_date <= date <= end_date]


class FakeAuditedPITUniverse(FakePITUniverse):
    is_audited_store_artifact = True
    artifact_root_sha256 = "e" * 64
    final_oos_eligible = False


class NoHistoryProvider:
    def __init__(self):
        self.calls = 0

    def history(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("artifact-native replay must not call history provider")


class FakeArtifactReplayAdapter:
    artifact_root_sha256 = "e" * 64
    contract_sha256 = "f" * 64

    def __init__(self, universe, frame, blocked_buys=None, blocked_sells=None):
        self.universe = universe
        self.frame = frame.copy()
        self.blocked_buys = set(blocked_buys or ())
        self.blocked_sells = set(blocked_sells or ())
        self.signal_calls = []
        self.open_calls = []

    def signal_frame(self, symbol, start_date, as_of_date):
        self.signal_calls.append((str(symbol), start_date, as_of_date))
        return self.frame[
            (self.frame["date"] >= start_date) & (self.frame["date"] <= as_of_date)
        ].copy()

    def next_open(self, symbol, trade_date, side="buy"):
        self.open_calls.append((str(symbol), trade_date, side))
        proof = {"trade_date": trade_date, "generation_id": f"g-{trade_date}"}
        if side == "buy" and trade_date in self.blocked_buys:
            return {
                "fillable": False,
                "reason": "buy_open_locked_limit",
                "raw_price": None,
                "generation_proof": proof,
            }
        if side == "sell" and trade_date in self.blocked_sells:
            return {
                "fillable": False,
                "reason": "sell_open_locked_limit",
                "raw_price": None,
                "generation_proof": proof,
            }
        row = self.frame[self.frame["date"] == trade_date]
        if row.empty:
            return {
                "fillable": False,
                "reason": "missing_raw_bar",
                "raw_price": None,
                "generation_proof": proof,
            }
        return {
            "fillable": True,
            "reason": "raw_open",
            "raw_price": float(row.iloc[0]["raw_open"]),
            "generation_proof": proof,
        }


def _artifact_frame(periods=100):
    frame = sample_frame("up", periods=periods)
    frame["volume"] = 2_000_000.0
    frame["amount"] = frame["close"] * frame["volume"]
    frame["change_pct"] = frame["close"].pct_change().fillna(0) * 100
    for field in ("open", "high", "low", "close"):
        frame[f"raw_{field}"] = frame[field]
    frame["bar_adj_factor"] = 1.0
    frame["as_of_adj_factor"] = 1.0
    frame["adjustment_as_of_date"] = frame["date"].iloc[-1]
    frame["raw_pre_close"] = frame["raw_close"].shift(1).fillna(frame["raw_close"])
    frame["raw_volume_lots"] = frame["volume"] / 100
    frame["raw_amount_thousand_yuan"] = frame["amount"] / 1000
    frame["generation_proof"] = [
        {"trade_date": date, "generation_id": f"g-{date}"}
        for date in frame["date"]
    ]
    return frame


def _publish_long_real_artifact(
    tmp_path,
    periods=96,
    *,
    temporal_contract_sha256=None,
    temporal_role=None,
    breakout_position=None,
):
    sessions = [date.strftime("%Y-%m-%d") for date in pd.bdate_range("2024-01-02", periods=periods)]
    position_by_date = {date: index for index, date in enumerate(sessions)}
    original_defaults = _pit_store_test_module._market_default_rows
    if breakout_position is not None and not (
        21 <= int(breakout_position) <= periods - 3
    ):
        raise ValueError("breakout_position must leave 20 lookback and 2 exit sessions")
    closes = [10.0 + index * 0.05 for index in range(periods)]
    if breakout_position is not None:
        breakout_position = int(breakout_position)
        baseline_highs = []
        for index, close in enumerate(closes):
            pre_close = closes[index - 1] if index else close
            baseline_highs.append(max(pre_close * 1.001, close) * 1.01)
        closes[breakout_position] = (
            max(baseline_highs[breakout_position - 20 : breakout_position])
            * 1.01
        )
        for index in range(breakout_position + 1, periods):
            closes[index] = closes[index - 1] + 0.05

    def trending_market_rows(dataset, trade_date):
        position = position_by_date[trade_date]
        wire = trade_date.replace("-", "")
        close = closes[position]
        pre_close = closes[position - 1] if position else close
        open_price = pre_close * 1.001
        high = max(open_price, close) * 1.01
        low = min(open_price, close) * 0.99
        volume = 2_000_000 if position == breakout_position else 1_000_000
        if dataset == "daily":
            return [
                [
                    "600001.SH",
                    wire,
                    open_price,
                    high,
                    low,
                    close,
                    pre_close,
                    close - pre_close,
                    (close / pre_close - 1) * 100,
                    volume,
                    close * volume,
                ]
            ]
        if dataset == "adj_factor":
            return [["600001.SH", wire, 1.0]]
        if dataset == "stk_limit":
            return [[wire, "600001.SH", pre_close, pre_close * 1.1, pre_close * 0.9]]
        if dataset == "suspend_d" and trade_date == sessions[0]:
            return [["000002.SZ", wire, "09:30:00", "S"]]
        return []

    store = PITReceiptStore(str(tmp_path / "long-store"))
    controlled_authority = temporal_contract_sha256 is not None
    if controlled_authority != (temporal_role is not None):
        raise ValueError("temporal artifact fixture authority must be atomic")
    _pit_store_test_module._market_default_rows = trending_market_rows
    try:
        _ingest_complete_two_day_fixture(
            store,
            temporal_role=temporal_role,
            temporal_contract_sha256=temporal_contract_sha256,
            calendar_sessions=sessions,
            calendar_exchanges=("SSE",) if controlled_authority else ("SSE", "SZSE"),
        )
        extra_sessions = sessions[2:]
        for session in extra_sessions:
            params = {"trade_date": session.replace("-", "")}
            raw = _daily_response(
                session.replace("-", ""), [["600001.SH", "A", "银行"]]
            )
            if controlled_authority:
                _promote_controlled_receipt(
                    store,
                    dataset="bak_basic",
                    partition_key=session,
                    params=params,
                    raw_bytes=raw,
                    retrieved_at=f"{session}T16:00:00+08:00",
                    row_cap=7000,
                    temporal_role=temporal_role,
                    temporal_contract_sha256=temporal_contract_sha256,
                )
            else:
                store.ingest_tushare_response(
                    dataset="bak_basic",
                    partition_key=session,
                    endpoint="bak_basic",
                    params=params,
                    raw_bytes=raw,
                    http_status=200,
                    retrieved_at=f"{session}T16:00:00+08:00",
                    row_cap=7000,
                )
            _publish_market_session_for_audit(
                store,
                session,
                datetime.fromisoformat(f"{session}T16:00:00+00:00").replace(
                    tzinfo=timezone.utc
                ),
                source_profile="jiaoch" if controlled_authority else None,
                temporal_role=temporal_role,
                temporal_contract_sha256=temporal_contract_sha256,
            )
    finally:
        _pit_store_test_module._market_default_rows = original_defaults

    audit = store.audit_coverage(start_date=sessions[0], end_date=sessions[-1])
    artifact = store.publish_universe_artifact(
        str(tmp_path / "long-artifact"),
        start_date=sessions[0],
        end_date=sessions[-1],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        temporal_contract_sha256=temporal_contract_sha256,
        temporal_role=temporal_role,
    )
    return artifact, audit, sessions


def test_artifact_entry_gap_uses_same_asof_adjusted_prices_across_corporate_action():
    sessions = ["2025-01-02", "2025-01-03", "2025-01-06"]

    class CorporateActionAdapter:
        def next_open(self, symbol, trade_date, side="buy"):
            raw_prices = {"2025-01-03": 50.0, "2025-01-06": 55.0}
            return {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": raw_prices[trade_date],
                "generation_proof": {"trade_date": trade_date},
            }

        def signal_frame(self, symbol, start_date, as_of_date):
            rows = [
                {
                    "date": "2025-01-02",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "raw_open": 100.0,
                    "raw_high": 101.0,
                    "raw_low": 99.0,
                    "raw_close": 100.0,
                    "bar_adj_factor": 1.0,
                },
                {
                    # 2-for-1 corporate action: raw 50, but on the entry-date
                    # causal adjustment basis both signal close and entry open
                    # are 100, so the economic gap is 0%, not -50%.
                    "date": "2025-01-03",
                    "open": 100.0,
                    "high": 104.0,
                    "low": 98.0,
                    "close": 102.0,
                    "raw_open": 50.0,
                    "raw_high": 52.0,
                    "raw_low": 49.0,
                    "raw_close": 51.0,
                    "bar_adj_factor": 2.0,
                },
                {
                    "date": "2025-01-06",
                    "open": 110.0,
                    "high": 110.0,
                    "low": 110.0,
                    "close": 110.0,
                    "raw_open": 55.0,
                    "raw_high": 55.0,
                    "raw_low": 55.0,
                    "raw_close": 55.0,
                    "bar_adj_factor": 2.0,
                },
            ]
            return pd.DataFrame(
                row for row in rows if start_date <= row["date"] <= as_of_date
            )

    outcome = _artifact_native_time_exit_trade(
        adapter=CorporateActionAdapter(),
        verdict_cache={},
        symbol="600001",
        signal_date=sessions[0],
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        artifact_start_date=sessions[0],
        hold_days=1,
        settings=Settings(),
    )

    assert outcome is not None
    realized, entry_evidence = outcome
    assert entry_evidence["gap_pct"] == pytest.approx(0.0)
    assert realized["entry_raw_price"] == pytest.approx(50.0)
    assert realized["return_pct"] == pytest.approx(10.0)


def test_artifact_producer_matches_strict_replay_at_pct4_half_boundary():
    """Producer and verifier must share one exact outcome arithmetic contract."""

    sessions = [
        "2025-01-02",
        "2025-01-03",
        "2025-01-06",
        "2025-01-07",
        "2025-01-08",
    ]
    analysis_end_factor = 1.9673950175832966
    exit_factor = 1.3726677763186674
    entry_factor = 0.8982917896085058
    intermediate_factor = 0.9394327477178013
    entry_raw = 49.83642340716134
    factors = [
        entry_factor,
        entry_factor,
        intermediate_factor,
        exit_factor,
        analysis_end_factor,
    ]
    raw_ohlc = [
        (entry_raw, entry_raw, entry_raw, entry_raw),
        (entry_raw, entry_raw, entry_raw, entry_raw),
        (47.8, 50.0, 45.0, 47.70226166787021),
        (32.28072976306658,) * 4,
        (31.0,) * 4,
    ]
    rows = []
    for trade_date, factor, values in zip(sessions, factors, raw_ohlc):
        row = {
            "date": trade_date,
            "bar_adj_factor": factor,
            "as_of_adj_factor": analysis_end_factor,
            "adjustment_as_of_date": sessions[-1],
        }
        for field, raw_value in zip(("open", "high", "low", "close"), values):
            row[field] = raw_value * factor / analysis_end_factor
            row[f"raw_{field}"] = raw_value
        rows.append(row)
    analysis_frame = pd.DataFrame(rows)

    class Adapter:
        def signal_frame(self, symbol, start_date, as_of_date):
            return analysis_frame[
                (analysis_frame["date"] >= start_date)
                & (analysis_frame["date"] <= as_of_date)
            ].copy()

        def next_open(self, symbol, trade_date, side="buy"):
            raw_price = float(
                analysis_frame.loc[
                    analysis_frame["date"] == trade_date, "raw_open"
                ].iloc[0]
            )
            return {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": raw_price,
                "generation_proof": {"trade_date": trade_date, "side": side},
            }

    outcome = _artifact_native_time_exit_trade(
        adapter=Adapter(),
        verdict_cache={},
        symbol="600001",
        signal_date=sessions[0],
        sessions=sessions,
        session_positions={value: index for index, value in enumerate(sessions)},
        artifact_start_date=sessions[0],
        hold_days=2,
        settings=Settings(),
        analysis_frame=analysis_frame,
        analysis_date_positions={
            value: index for index, value in enumerate(sessions)
        },
    )
    assert outcome is not None
    realized, _entry_evidence = outcome

    class Audited:
        start_date = sessions[0]

        def open_sessions(self, start_date, end_date):
            return [
                value for value in sessions if start_date <= value <= end_date
            ]

        def causal_signal_bars(self, symbol, start_date, as_of_date):
            bars = []
            for row in rows:
                if not start_date <= row["date"] <= as_of_date:
                    continue
                bar = {
                    "trade_date": row["date"],
                    "bar_adj_factor": row["bar_adj_factor"],
                    "as_of_adj_factor": exit_factor,
                    "adjustment_as_of_date": sessions[3],
                }
                for field in ("open", "high", "low", "close"):
                    bar[f"raw_{field}"] = row[f"raw_{field}"]
                    bar[f"signal_{field}"] = (
                        row[f"raw_{field}"]
                        * row["bar_adj_factor"]
                        / exit_factor
                    )
                bars.append(bar)
            return bars

        def next_open_execution_evidence(self, symbol, trade_date, side):
            raw_price = float(
                analysis_frame.loc[
                    analysis_frame["date"] == trade_date, "raw_open"
                ].iloc[0]
            )
            return {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": raw_price,
                "generation_proof": {"trade_date": trade_date, "side": side},
            }

    trade = {"symbol": "600001", "signal_date": sessions[0], **realized}
    replay_trade_outcome(Audited(), trade, compare_claim=True)


def _universe_items(n):
    items = []
    for i in range(1, n + 1):
        symbol = "%06d" % i
        items.append(
            {
                "symbol": symbol,
                "market": "a",
                "name": "股票%s" % symbol,
                "latest": 100,
                "amount": 1_000_000_000,
                "change_pct": 2,
            }
        )
    return items


def _stock_only_settings(tmp_path):
    universe_path = tmp_path / "universe.json"
    universe_path.write_text("{}")
    return replace(
        Settings(),
        universe_cache_path=str(universe_path),
        min_backtest_trades=1,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )


def _run_stock_only(provider, settings, tmp_path, **overrides):
    items = _universe_items(overrides.pop("universe_n", 100))
    sessions = provider.frame["date"].astype(str).tolist()
    pit_universe = FakePITUniverse(items, sessions)
    kwargs = dict(
        settings=settings,
        provider=provider,
        start_date="2025-04-01",
        max_deep=100,
        top_n=100,
        hold_days=1,
        lookback_days=620,
        cache_dir=str(tmp_path / "cache"),
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=pit_universe,
    )
    kwargs.update(overrides)
    return _run_historical_universe_research_backtest_resolved(**kwargs)


def test_historical_resolved_rejects_missing_pit_before_history(tmp_path):
    provider = StockOnlyProvider()
    with pytest.raises(ValueError, match="PIT universe"):
        _run_historical_universe_research_backtest_resolved(
            settings=_stock_only_settings(tmp_path),
            provider=provider,
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=100,
            _resolved_universe_items=_universe_items(1),
            _resolved_pit_universe=None,
        )
    assert provider.stock_calls == 0
    assert provider.etf_calls == 0


def _force_signal(monkeypatch, action="BUY", score=5.0):
    monkeypatch.setattr(
        "app.research_backtest.evaluate_signal",
        lambda history: {
            "action": action,
            "score": score,
            "confidence": 90,
            "reasons": [],
            "confirmations": [],
            "risks": [],
            "indicators": {},
        },
    )


# ---------------------------------------------------------------------------
# 1. historical resolved 全程不触碰 ETF；可完成到 summary
# ---------------------------------------------------------------------------


def test_historical_resolved_makes_zero_etf_calls(tmp_path, monkeypatch):
    _force_signal(monkeypatch)
    provider = StockOnlyProvider(direction="up")
    result = _run_stock_only(provider, _stock_only_settings(tmp_path), tmp_path)

    # 完成 to summary
    assert "summary" in result
    # 全程零 ETF history 调用（proxy frame + benchmark 都被移除）
    assert provider.etf_calls == 0


def test_audited_artifact_path_makes_zero_provider_calls_and_uses_raw_open(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    universe.item_as_of = lambda *args: (_ for _ in ()).throw(
        AssertionError("artifact replay must batch historical membership")
    )
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    provider = NoHistoryProvider()

    result = _run_historical_universe_research_backtest_resolved(
        settings=_stock_only_settings(tmp_path),
        provider=provider,
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        cache_dir=str(tmp_path / "must-not-be-read"),
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    assert provider.calls == 0
    assert adapter.signal_calls[0] == (
        items[0]["symbol"],
        universe.start_date,
        universe.end_date,
    )
    # 全年帧只读取一次；信号日、入场日和退出日都通过可证明的常数尺度
    # 消去 analysis_end 复权分母，不再为每个历史日期重复读取 SQLite。
    assert adapter.signal_calls == [
        (items[0]["symbol"], universe.start_date, universe.end_date)
    ]
    assert result["summary"]["artifact_native_replay"] is True
    assert result["summary"]["market_data_source"] == "audited_artifact"
    assert result["summary"]["artifact_root_sha256"] == adapter.artifact_root_sha256
    evaluation_window = result["summary"]["artifact_evaluation_window"]
    assert evaluation_window == {
        "schema_version": "artifact-evaluation-window/v1",
        "requested_start_date": "2025-04-01",
        "artifact_history_start_date": universe.start_date,
        "analysis_end_date": universe.end_date,
        "required_warmup_sessions": 90,
        "warmup_cutoff_date": frame.iloc[90]["date"],
        "effective_evaluation_start_date": frame.iloc[90]["date"],
        "available_pre_evaluation_sessions": 90,
        "expected_session_count": 10,
    }
    assert result["summary"]["stock_universe_benchmark_eligible"] is True
    assert result["summary"]["stock_universe_benchmark_days"] == 10
    assert result["summary"]["stock_universe_benchmark_expected_days"] == 10
    assert (
        result["summary"]["stock_universe_benchmark_start_date"]
        == frame.iloc[90]["date"]
    )
    contract = result["summary"]["research_data_contract"]
    assert contract["point_in_time"] is False
    assert contract["universe_point_in_time"] is False
    assert contract["development_integrity"] is False
    assert contract["eligible_for_development_validation"] is False
    assert contract["audited_authority"] is None
    assert result["qualified_trades"]
    trade = result["qualified_trades"][0]
    evidence = trade["entry_executability"]
    assert evidence["evidence_source"] == "audited_artifact_next_open"
    assert evidence["raw_price"] == pytest.approx(
        float(frame.loc[frame["date"] == trade["entry_date"], "raw_open"].iloc[0])
    )
    assert evidence["generation_proof"]["trade_date"] == trade["entry_date"]
    assert trade["price_basis"] == "raw_unadjusted_execution"


def test_artifact_benchmark_missing_post_warmup_session_stays_fail_closed(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    expected_sessions = frame.iloc[90:]["date"].tolist()
    missing_session = expected_sessions[4]
    monkeypatch.setattr(
        research_backtest_module,
        "_historical_market_breadth",
        lambda *_args, **_kwargs: {
            session: {
                "eligible_count": 1,
                "coverage_pct": 100.0,
                "equal_weight_daily_return_pct": 0.1,
            }
            for session in expected_sessions
            if session != missing_session
        },
    )

    result = _run_historical_universe_research_backtest_resolved(
        settings=_stock_only_settings(tmp_path),
        provider=NoHistoryProvider(),
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    summary = result["summary"]
    assert summary["stock_universe_benchmark_eligible"] is False
    assert summary["stock_universe_benchmark_expected_days"] == 10
    assert summary["stock_universe_benchmark_days"] == 9
    assert missing_session in summary["stock_universe_benchmark_reasons"][0]


def test_artifact_signal_evaluation_reuses_preloaded_causal_prefix(
    tmp_path, monkeypatch
):
    evaluated_lengths = []

    def capture_signal_window(history):
        evaluated_lengths.append(len(history))
        return {"action": "BUY", "score": 5.0, "confidence": 90}

    monkeypatch.setattr(
        "app.research_backtest.evaluate_signal", capture_signal_window
    )
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    monkeypatch.setattr(
        "app.research_backtest._artifact_native_time_exit_trade",
        lambda **kwargs: None,
    )
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    _run_historical_universe_research_backtest_resolved(
        settings=_stock_only_settings(tmp_path),
        provider=NoHistoryProvider(),
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        cache_dir=str(tmp_path / "must-not-be-read"),
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    assert adapter.signal_calls == [
        (items[0]["symbol"], universe.start_date, universe.end_date)
    ]
    assert evaluated_lengths
    assert set(evaluated_lengths) == {2}


def test_artifact_unresolved_exit_is_audited_and_does_not_abort_universe(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.research_backtest.evaluate_signal",
        lambda history: {"action": "BUY", "score": 5.0, "confidence": 90},
    )
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)

    def unresolved(**kwargs):
        raise ArtifactNativeUnresolvedExit(
            kwargs["symbol"], kwargs["signal_date"], "2025-04-08"
        )

    monkeypatch.setattr(
        "app.research_backtest._artifact_native_time_exit_trade", unresolved
    )
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    result = _run_historical_universe_research_backtest_resolved(
        settings=_stock_only_settings(tmp_path),
        provider=NoHistoryProvider(),
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        cache_dir=str(tmp_path / "must-not-be-read"),
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    assert result["qualified_trades"] == []
    assert result["summary"]["artifact_unresolved_exit_count"] > 0
    assert {
        row["reason"] for row in result["summary"]["artifact_unresolved_exits"]
    } == {"unfillable_through_coverage_end"}


def test_artifact_signal_evaluation_rejects_missing_precomputed_indicator(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    real_add_indicators = research_backtest_module.add_indicators
    monkeypatch.setattr(
        "app.research_backtest.add_indicators",
        lambda value: real_add_indicators(value).drop(columns=["rsi14"]),
    )
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    with pytest.raises(
        ValueError,
        match="artifact analysis frame is missing precomputed signal indicators: rsi14",
    ):
        _run_historical_universe_research_backtest_resolved(
            settings=_stock_only_settings(tmp_path),
            provider=NoHistoryProvider(),
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=620,
            cache_dir=str(tmp_path / "must-not-be-read"),
            include_qualified_trades=True,
            _resolved_universe_items=items,
            _resolved_pit_universe=universe,
        )


@pytest.mark.parametrize("signal_index", [90, 95, 99])
def test_artifact_two_row_signal_window_is_evaluator_equivalent_to_full_prefix(
    signal_index,
):
    frame = research_backtest_module.add_indicators(_artifact_frame())

    full_prefix = research_backtest_module._artifact_causal_indicator_prefix(
        frame, signal_index
    )
    two_row_window = research_backtest_module._artifact_causal_signal_window(
        frame, signal_index
    )

    assert research_backtest_module.evaluate_signal(
        two_row_window
    ) == research_backtest_module.evaluate_signal(full_prefix)


def test_real_temporally_bound_artifact_declares_pit_without_premature_eligibility(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    temporal_contract_sha256 = "a" * 64
    artifact, audit, sessions = _publish_long_real_artifact(
        tmp_path,
        temporal_contract_sha256=temporal_contract_sha256,
        temporal_role="development",
    )
    universe = AuditedPointInTimeUniverse.from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        expected_artifact_root_sha256=artifact["artifact_root_sha256"],
        expected_temporal_contract_sha256=temporal_contract_sha256,
        expected_temporal_role="development",
    )
    try:
        result = _run_historical_universe_research_backtest_resolved(
            settings=replace(_stock_only_settings(tmp_path), min_backtest_trades=0),
            provider=NoHistoryProvider(),
            start_date=sessions[60],
            end_date=sessions[-1],
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=620,
            max_universe_symbols=0,
            include_qualified_trades=True,
            expected_temporal_contract_sha256=temporal_contract_sha256,
            expected_temporal_role="development",
            _resolved_universe_items=universe.seed_items(sessions[60], sessions[-1]),
            _resolved_pit_universe=universe,
        )
    finally:
        universe.close()

    contract = result["summary"]["research_data_contract"]
    assert contract["schema_version"] == "research_data_contract/v1"
    assert contract["artifact_role"] == "development_only"
    assert contract["point_in_time"] is True
    assert contract["universe_point_in_time"] is True
    assert contract["development_integrity"] is True
    assert contract["eligible_for_development_validation"] is False
    assert contract["eligible_for_final_validation"] is False
    assert contract["final_oos_eligible"] is False
    assert contract["development_eligibility_reasons"] == [
        "artifact_native_evidence_not_compiled",
        "qualified_trades_sha256_not_bound",
        "qualified_trade_lineage_not_bound",
        "strict_evidence_bundle_not_compiled",
    ]
    assert contract["external_temporal_authority_verified"] is True
    assert contract["temporal_contract_sha256"] == temporal_contract_sha256
    assert contract["temporal_role"] == "development"
    assert contract["artifact_root_sha256"] == artifact["artifact_root_sha256"]
    assert contract["artifact_replay_contract_sha256"] == result["summary"][
        "artifact_replay_contract_sha256"
    ]
    assert contract["universe_sha256"] == universe.universe_sha256
    assert contract["calendar_sha256"] == universe.calendar_sha256
    assert contract["coverage_audit_sha256"] == audit["coverage_audit_sha256"]
    assert contract["artifact_manifest_sha256"] == universe.manifest["manifest_sha256"]
    assert contract["market_generation_root_sha256"] == universe.manifest[
        "market_generations"
    ]["root_sha256"]
    assert contract["stock_generation_lineage_sha256"] == universe.manifest[
        "stock_generation"
    ]["lineage_sha256"]
    assert contract["source_manifest_sha256"] == universe.source_manifest_sha256
    assert contract["audited_authority"] == {
        "artifact_root_sha256": artifact["artifact_root_sha256"],
        "coverage_audit_sha256": audit["coverage_audit_sha256"],
        "temporal_contract_sha256": temporal_contract_sha256,
        "temporal_role": "development",
        "artifact_manifest_sha256": universe.manifest["manifest_sha256"],
        "market_generation_root_sha256": universe.manifest["market_generations"][
            "root_sha256"
        ],
        "stock_generation_lineage_sha256": universe.manifest["stock_generation"][
            "lineage_sha256"
        ],
    }
    assert contract["known_biases"] == [
        "controlled_direct_transport_not_verified",
        "independent_exchange_master_not_bound",
        "historical_backfill_not_contemporaneously_observed",
        "artifact_not_final_oos_eligible",
        "intraday_exit_models_not_supported",
    ]
    assert "qualified_trades_sha256" not in contract
    assert "qualified_trade_lineage_sha256" not in contract
    assert "artifact_native_evidence_path" not in contract
    assert "artifact_native_evidence_sha256" not in contract
    assert "evidence_bundle_path" not in contract
    assert "evidence_bundle_sha256" not in contract


def test_verified_composite_declares_pit_but_not_strict_or_live_eligibility(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    # This compact fixture verifies authority classification, not indicator
    # warmup.  Production warmup behavior is covered by the dedicated
    # artifact-evaluation-window tests below.
    monkeypatch.setattr(
        research_backtest_module, "STRATEGY_SIGNAL_WARMUP_SESSIONS", 0
    )
    first, second = _annual_segments()
    first.bars = [
        _bar(
            "2022-12-29",
            raw_open=10.0,
            bar_factor=1.0,
            local_asof_factor=1.0,
            local_adjustment_date="2022-12-30",
            segment="2022",
        )
    ]
    second.bars = [
        _bar(
            "2023-06-01",
            raw_open=6.0,
            bar_factor=2.0,
            local_asof_factor=2.0,
            local_adjustment_date="2023-06-01",
            segment="2023",
        )
    ]
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )
    compact_sessions = ["2022-12-29", "2023-06-01"]
    monkeypatch.setattr(
        composite,
        "open_sessions",
        lambda start, end: [
            session for session in compact_sessions if start <= session <= end
        ],
    )

    result = _run_historical_universe_research_backtest_resolved(
        settings=replace(_stock_only_settings(tmp_path), min_backtest_trades=0),
        provider=NoHistoryProvider(),
        start_date="2022-12-29",
        end_date="2023-06-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        max_universe_symbols=0,
        include_qualified_trades=True,
        expected_temporal_contract_sha256="c" * 64,
        expected_temporal_role="development",
        _resolved_universe_items=[
            {"symbol": "600001", "market": "a", "name": "composite"}
        ],
        _resolved_pit_universe=composite,
    )

    contract = result["summary"]["research_data_contract"]
    assert contract["schema_version"] == "research_data_contract/v1"
    assert contract["point_in_time"] is True
    assert contract["development_integrity"] is True
    assert contract["eligible_for_development_validation"] is False
    assert contract["eligible_for_final_validation"] is False
    assert contract["final_oos_eligible"] is False
    assert contract["live_proof"] is False
    assert contract["composite_authority_schema_version"] == (
        "research-composite-universe/v1"
    )
    assert contract["composite_authority"] == composite.composite_authority
    assert contract["artifact_root_sha256"] == composite.composite_root_sha256
    assert contract["coverage_audit_sha256"] == composite.coverage_audit_sha256
    assert contract["calendar_sha256"] == composite.calendar_sha256
    assert contract["source_manifest_sha256"] == composite.source_manifest_sha256
    assert contract["audited_authority"] is None
    assert contract["artifact_manifest_sha256"] is None
    assert contract["development_eligibility_reasons"] == [
        "composite_artifact_native_evidence_not_compiled",
        "qualified_trades_sha256_not_bound",
        "qualified_trade_lineage_not_bound",
        "strict_evidence_bundle_not_compiled",
    ]


def test_legacy_unbound_v4_artifact_is_rejected_before_provider_or_cache(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)

    def forbidden_external_read(*args, **kwargs):
        raise AssertionError("audited v4 replay touched a mutable cache/snapshot reader")

    for target in (
        "app.research_backtest.read_json",
        "app.research_backtest._history_with_file_cache",
        "app.research_backtest._announcements_with_file_cache",
    ):
        monkeypatch.setattr(target, forbidden_external_read)
    artifact, audit, sessions = _publish_long_real_artifact(tmp_path)
    # This historical fixture intentionally predates temporal authority binding.
    # Opt in to the isolated legacy loader; production bound loading must never
    # accept coverage alone.
    monkeypatch.setattr(
        "app.research_backtest.AuditedPointInTimeUniverse.from_file",
        AuditedPointInTimeUniverse.from_legacy_unbound_file,
    )
    provider = NoHistoryProvider()
    settings = replace(
        _stock_only_settings(tmp_path),
        min_backtest_trades=0,
        min_backtest_win_rate=0,
        min_backtest_avg_return=-100,
        max_backtest_avg_adverse=100,
    )

    with pytest.raises(ValueError, match="expected_artifact_root_sha256"):
        run_historical_universe_research_backtest(
            settings=settings,
            provider=provider,
            start_date=sessions[60],
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=620,
            max_universe_symbols=0,
            cache_dir=str(tmp_path / "must-not-exist"),
            include_qualified_trades=True,
            audited_pit_universe_path=artifact["path"],
            expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
        )

    assert provider.calls == 0
    assert not (tmp_path / "must-not-exist").exists()


def test_audited_artifact_locked_next_open_is_not_a_trade(tmp_path, monkeypatch):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    # 所有可能的次日买入都锁涨停，必须逐日拒绝，不能退回启发式成交。
    adapter = FakeArtifactReplayAdapter(
        universe, frame, blocked_buys=set(frame["date"].tolist())
    )
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    provider = NoHistoryProvider()

    result = _run_historical_universe_research_backtest_resolved(
        settings=_stock_only_settings(tmp_path),
        provider=provider,
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    assert provider.calls == 0
    assert result["summary"]["selected_trade_count"] == 0
    assert result["qualified_trades"] == []
    assert any(side == "buy" for _symbol, _date, side in adapter.open_calls)


def test_audited_artifact_blocked_sell_retries_next_market_open(tmp_path, monkeypatch):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    first_planned_exit = str(frame.iloc[92]["date"])
    adapter = FakeArtifactReplayAdapter(
        universe, frame, blocked_sells={first_planned_exit}
    )
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    settings = replace(_stock_only_settings(tmp_path), min_backtest_trades=0)

    result = _run_historical_universe_research_backtest_resolved(
        settings=settings,
        provider=NoHistoryProvider(),
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    delayed = [
        trade
        for trade in result["qualified_trades"]
        if trade["planned_exit_date"] == first_planned_exit
    ]
    assert delayed
    assert delayed[0]["exit_date"] > delayed[0]["planned_exit_date"]
    assert delayed[0]["exit_execution_evidence"]["fillable"] is True
    sell_calls = [call for call in adapter.open_calls if call[2] == "sell"]
    blocked_index = sell_calls.index((items[0]["symbol"], first_planned_exit, "sell"))
    assert sell_calls[blocked_index + 1][1] > first_planned_exit


def test_audited_artifact_missing_held_session_fails_closed_across_suspension(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    full_frame = _artifact_frame(periods=100)
    sessions = full_frame["date"].astype(str).tolist()
    # The stock is suspended after the signal and resumes only on the final
    # market session. Its sparse frame has fewer than hold_days future rows,
    # although the audited market calendar contains the planned exit session.
    sparse_frame = pd.concat(
        [full_frame.iloc[:92], full_frame.iloc[[99]]], ignore_index=True
    )
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, sessions)
    adapter = FakeArtifactReplayAdapter(universe, sparse_frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    with pytest.raises(
        ValueError, match="artifact outcome frame is missing a held market session"
    ):
        _run_historical_universe_research_backtest_resolved(
            settings=replace(_stock_only_settings(tmp_path), min_backtest_trades=0),
            provider=NoHistoryProvider(),
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=5,
            lookback_days=620,
            include_qualified_trades=True,
            _resolved_universe_items=items,
            _resolved_pit_universe=universe,
        )

    symbol = items[0]["symbol"]
    assert (symbol, sessions[91], "buy") in adapter.open_calls
    assert (symbol, sessions[96], "sell") in adapter.open_calls
    assert (symbol, sessions[99], "sell") in adapter.open_calls


def test_audited_artifact_unresolved_sell_fails_closed_per_trade(
    tmp_path, monkeypatch
):
    _force_signal(monkeypatch)
    monkeypatch.setattr("app.research_context.STOCK_MARKET_MIN_ELIGIBLE", 1)
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(
        universe,
        frame,
        blocked_sells=set(frame.iloc[92:]["date"].astype(str)),
    )
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    result = _run_historical_universe_research_backtest_resolved(
        settings=replace(_stock_only_settings(tmp_path), min_backtest_trades=0),
        provider=NoHistoryProvider(),
        start_date="2025-04-01",
        max_deep=1,
        top_n=1,
        hold_days=1,
        lookback_days=620,
        include_qualified_trades=True,
        _resolved_universe_items=items,
        _resolved_pit_universe=universe,
    )

    unresolved = result["summary"]["artifact_unresolved_exits"]
    assert result["summary"]["artifact_unresolved_exit_count"] == len(unresolved)
    assert unresolved
    assert {row["reason"] for row in unresolved} == {
        "unfillable_through_coverage_end"
    }


@pytest.mark.parametrize("field", ["stop_loss_pct", "take_profit_pct", "trailing_stop_pct"])
def test_audited_artifact_rejects_unproven_intraday_exit_models(
    tmp_path, monkeypatch, field
):
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )

    with pytest.raises(ValueError, match="intraday exit"):
        _run_historical_universe_research_backtest_resolved(
            settings=_stock_only_settings(tmp_path),
            provider=NoHistoryProvider(),
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=620,
            _resolved_universe_items=items,
            _resolved_pit_universe=universe,
            **{field: 5},
        )


@pytest.mark.parametrize("hold_days", [0, -1, 1.5, True])
def test_historical_replay_rejects_nonpositive_or_noninteger_hold_days_before_reads(
    tmp_path, monkeypatch, hold_days
):
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    provider = NoHistoryProvider()

    with pytest.raises(ValueError, match="positive integer"):
        _run_historical_universe_research_backtest_resolved(
            settings=_stock_only_settings(tmp_path),
            provider=provider,
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=hold_days,
            lookback_days=620,
            _resolved_universe_items=items,
            _resolved_pit_universe=universe,
        )
    assert provider.calls == 0
    assert adapter.signal_calls == []


@pytest.mark.parametrize("hold_days", [0, -1, 1.5, True])
def test_public_historical_entry_rejects_invalid_hold_days_before_resolving_artifact(
    tmp_path, monkeypatch, hold_days
):
    calls = {"resolve": 0}

    def forbidden_resolve(*args, **kwargs):
        calls["resolve"] += 1
        raise AssertionError("invalid hold_days must fail before artifact resolution")

    monkeypatch.setattr(
        "app.research_backtest._resolve_historical_universe", forbidden_resolve
    )

    with pytest.raises(ValueError, match="positive integer"):
        run_historical_universe_research_backtest(
            settings=_stock_only_settings(tmp_path),
            provider=NoHistoryProvider(),
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=hold_days,
            lookback_days=620,
            audited_pit_universe_path="/must/not/be/read.sqlite3",
            expected_coverage_audit_sha256="a" * 64,
        )

    assert calls["resolve"] == 0


@pytest.mark.parametrize(
    "flag",
    [
        "use_announcement_context",
        "use_industry_rotation_context",
        "use_margin_eligibility_context",
        "use_dragon_tiger_context",
    ],
)
def test_audited_artifact_rejects_unbound_external_contexts_before_provider_call(
    tmp_path, monkeypatch, flag
):
    frame = _artifact_frame()
    items = _universe_items(1)
    universe = FakeAuditedPITUniverse(items, frame["date"].tolist())
    adapter = FakeArtifactReplayAdapter(universe, frame)
    monkeypatch.setattr(
        "app.research_backtest.ArtifactNativeReplayAdapter", lambda value: adapter
    )
    provider = NoHistoryProvider()

    with pytest.raises(ValueError, match="external contexts"):
        _run_historical_universe_research_backtest_resolved(
            settings=_stock_only_settings(tmp_path),
            provider=provider,
            start_date="2025-04-01",
            max_deep=1,
            top_n=1,
            hold_days=1,
            lookback_days=620,
            _resolved_universe_items=items,
            _resolved_pit_universe=universe,
            **{flag: True},
        )
    assert provider.calls == 0
    assert adapter.signal_calls == []


# ---------------------------------------------------------------------------
# 2. allowed_actions 显式尊重 allow_buy / allow_watch / buy_only
# ---------------------------------------------------------------------------


def test_unknown_market_context_blocks_all_trades_even_high_buy_score(tmp_path, monkeypatch):
    """eligible < 100 → unknown → allow_buy=False → BUY/WATCH 都不穿透。"""
    _force_signal(monkeypatch, action="BUY", score=9.0)
    provider = StockOnlyProvider(direction="up")
    result = _run_stock_only(
        provider, _stock_only_settings(tmp_path), tmp_path, universe_n=50
    )

    assert result["summary"]["selected_trade_count"] == 0
    assert result["qualified_trades"] == []


def test_defensive_market_context_rejects_watch_but_allows_buy(tmp_path, monkeypatch):
    """down 行情 → defensive → allow_watch=False：WATCH 被拒，BUY 放行。"""
    settings = _stock_only_settings(tmp_path)
    provider = StockOnlyProvider(direction="down")

    _force_signal(monkeypatch, action="WATCH", score=5.0)
    result_watch = _run_stock_only(provider, settings, tmp_path)
    assert result_watch["summary"]["selected_trade_count"] == 0

    _force_signal(monkeypatch, action="BUY", score=5.0)
    result_buy = _run_stock_only(provider, settings, tmp_path)
    assert result_buy["summary"]["selected_trade_count"] > 0


def test_buy_only_does_not_let_watch_through_in_favorable(tmp_path, monkeypatch):
    """favorable 下 buy_only=True：即便信号是 WATCH，也不得穿透（WATCH 非买入）。"""
    settings = _stock_only_settings(tmp_path)
    provider = StockOnlyProvider(direction="up")

    _force_signal(monkeypatch, action="WATCH", score=5.0)
    result = _run_stock_only(provider, settings, tmp_path, buy_only=True)
    assert result["summary"]["selected_trade_count"] == 0


def test_favorable_market_context_allows_normal_qualification(tmp_path, monkeypatch):
    _force_signal(monkeypatch, action="BUY", score=5.0)
    provider = StockOnlyProvider(direction="up")
    result = _run_stock_only(provider, _stock_only_settings(tmp_path), tmp_path)

    assert result["summary"]["selected_trade_count"] > 0


# ---------------------------------------------------------------------------
# 3. trade 记录：market_context 字段 + relative_strength 仅 stock 字段
# ---------------------------------------------------------------------------


def test_trade_carries_stock_market_context_and_no_proxy_keys(tmp_path, monkeypatch):
    _force_signal(monkeypatch, action="BUY", score=5.0)
    provider = StockOnlyProvider(direction="up")
    result = _run_stock_only(provider, _stock_only_settings(tmp_path), tmp_path)

    trade = result["qualified_trades"][0]
    # market_level 保持兼容
    assert trade["market_level"] == "favorable"
    # 新增 market_context，且无 tags，含必需字段
    mc = trade["market_context"]
    assert mc["schema_version"] == STOCK_MARKET_CONTEXT_SCHEMA_VERSION
    assert mc["level"] == "favorable"
    assert "reasons" in mc and isinstance(mc["reasons"], list)
    assert mc["allow_buy"] is True
    assert mc["allow_watch"] is True
    assert "min_signal_score" in mc
    assert "score_adjustment" in mc
    assert "tags" not in mc
    # relative_strength 仅 stock 字段，无 proxy/ETF key
    rs = trade["relative_strength"]
    rs_json = json.dumps(rs, ensure_ascii=False)
    assert "proxy" not in rs_json and "etf" not in rs_json
    for forbidden in ("proxy_return_20d_avg_pct", "proxy_return_60d_avg_pct"):
        assert forbidden not in rs
    # stock relative tags 出现在 signal_tags
    assert any(tag.startswith("stock_rs") for tag in trade["signal_tags"])


# ---------------------------------------------------------------------------
# 4. _research_payload_from_trades：market_benchmark_summary 兼容
# ---------------------------------------------------------------------------


def _base_payload_kwargs(tmp_path):
    return dict(
        selected=[],
        all_trades=[],
        errors=[],
        settings=_stock_only_settings(tmp_path),
        provider=StockOnlyProvider(),
        start_date="2025-06-01",
        max_deep=200,
        top_n=200,
        hold_days=10,
        lookback_days=620,
        buy_only=False,
        min_score=0,
        stop_loss_pct=5,
        take_profit_pct=15,
        trailing_stop_pct=8,
        symbol_cooldown_days=0,
        max_active_positions=0,
        required_events=set(),
        require_all_announcement_events=False,
        excluded_events=set(),
        required_market_levels=set(),
        required_signal_tags=set(),
        require_all_signal_tags=False,
        excluded_signal_tags=set(),
        min_prior_win_rate=0,
        min_prior_avg_return=-100,
        max_prior_avg_adverse=100,
        use_announcement_context=False,
        announcement_lookback_days=30,
        announcement_blocked_count=0,
        announcement_scored_count=0,
        announcement_positive_count=0,
        announcement_watch_risk_count=0,
        announcement_fetch_errors=0,
        announcement_blocked_by_event={},
        candidate_count=0,
        fetched_symbols=0,
        include_qualified_trades=False,
    )


def test_payload_with_stock_summary_skips_benchmark_return(tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_benchmark(*args, **kwargs):
        calls["count"] += 1
        return 0.0

    monkeypatch.setattr("app.research_backtest._benchmark_return", fake_benchmark)

    stock_summary = {
        "stock_universe_equal_weight_daily_rebalanced_return_pct": 12.34,
        "stock_universe_benchmark_days": 3,
        "stock_universe_benchmark_min_coverage_pct": 95.0,
        "stock_universe_benchmark_eligible": True,
        "stock_universe_benchmark_reasons": ["ok"],
        "schema_version": "stock-universe-equal-weight-benchmark/v1",
    }
    result = _research_payload_from_trades(
        **_base_payload_kwargs(tmp_path),
        market_benchmark_summary=stock_summary,
    )
    summary = result["summary"]

    assert calls["count"] == 0
    assert "hs300etf_buy_hold_pct" not in summary
    assert "cybetf_buy_hold_pct" not in summary
    assert (
        summary["stock_universe_equal_weight_daily_rebalanced_return_pct"] == 12.34
    )
    assert summary["stock_universe_benchmark_eligible"] is True


def test_payload_without_stock_summary_keeps_etf_benchmark(tmp_path, monkeypatch):
    """None → 旧路径兼容：仍走 ETF _benchmark_return（candidate/live 旧行为）。"""
    calls = {"count": 0}

    def fake_benchmark(*args, **kwargs):
        calls["count"] += 1
        return 1.0

    monkeypatch.setattr("app.research_backtest._benchmark_return", fake_benchmark)

    result = _research_payload_from_trades(**_base_payload_kwargs(tmp_path))
    summary = result["summary"]

    # 两次 ETF benchmark 调用（hs300etf + cybetf）
    assert calls["count"] == 2
    assert "hs300etf_buy_hold_pct" in summary
    assert "cybetf_buy_hold_pct" in summary


# ---------------------------------------------------------------------------
# 5. stock benchmark 复利准确性 / fail closed / 日期边界
# ---------------------------------------------------------------------------


def _breadth(date, daily_return_pct, eligible=100, coverage=95.0):
    return {
        "eligible_count": eligible,
        "coverage_pct": coverage,
        "equal_weight_daily_return_pct": daily_return_pct,
    }


def test_artifact_evaluation_window_uses_fixed_calendar_warmup():
    sessions = [
        date.strftime("%Y-%m-%d")
        for date in pd.bdate_range("2025-01-02", periods=100)
    ]
    universe = FakeAuditedPITUniverse(_universe_items(1), sessions)

    window, expected = research_backtest_module._artifact_evaluation_window(
        universe,
        requested_start_date=sessions[0],
        analysis_end_date=sessions[-1],
    )

    assert window["schema_version"] == "artifact-evaluation-window/v1"
    assert window["requested_start_date"] == sessions[0]
    assert window["artifact_history_start_date"] == sessions[0]
    assert window["warmup_cutoff_date"] == sessions[90]
    assert window["effective_evaluation_start_date"] == sessions[90]
    assert window["available_pre_evaluation_sessions"] == 90
    assert window["expected_session_count"] == 10
    assert expected == sessions[90:]


def test_artifact_evaluation_window_preserves_pre_warmed_requested_start():
    sessions = [
        date.strftime("%Y-%m-%d")
        for date in pd.bdate_range("2024-01-02", periods=130)
    ]
    universe = FakeAuditedPITUniverse(_universe_items(1), sessions)

    window, expected = research_backtest_module._artifact_evaluation_window(
        universe,
        requested_start_date=sessions[100],
        analysis_end_date=sessions[-1],
    )

    assert window["warmup_cutoff_date"] == sessions[90]
    assert window["effective_evaluation_start_date"] == sessions[100]
    assert window["available_pre_evaluation_sessions"] == 100
    assert expected == sessions[100:]


def test_artifact_evaluation_window_advances_closed_requested_date_by_calendar():
    sessions = [
        date.strftime("%Y-%m-%d")
        for date in pd.bdate_range("2024-01-02", periods=130)
    ]
    universe = FakeAuditedPITUniverse(_universe_items(1), sessions)
    gap_index = next(
        index
        for index in range(91, len(sessions))
        if (pd.Timestamp(sessions[index]) - pd.Timestamp(sessions[index - 1])).days > 1
    )
    closed_date = (
        pd.Timestamp(sessions[gap_index]) - pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    window, expected = research_backtest_module._artifact_evaluation_window(
        universe,
        requested_start_date=closed_date,
        analysis_end_date=sessions[-1],
    )

    assert closed_date not in sessions
    assert window["effective_evaluation_start_date"] == sessions[gap_index]
    assert expected[0] == sessions[gap_index]


def test_artifact_evaluation_window_rejects_insufficient_history():
    sessions = [
        date.strftime("%Y-%m-%d")
        for date in pd.bdate_range("2025-01-02", periods=90)
    ]
    universe = FakeAuditedPITUniverse(_universe_items(1), sessions)

    with pytest.raises(ValueError, match="91 audited open sessions"):
        research_backtest_module._artifact_evaluation_window(
            universe,
            requested_start_date=sessions[0],
            analysis_end_date=sessions[-1],
        )


def test_stock_benchmark_compounds_daily_equal_weight_returns():
    breadth = {
        "2025-06-02": _breadth("2025-06-02", 1.0),
        "2025-06-03": _breadth("2025-06-03", 2.0),
        "2025-06-04": _breadth("2025-06-04", 3.0),
    }
    result = _stock_universe_equal_weight_benchmark(
        breadth, "2025-06-01", "2025-06-30", expected_sessions=sorted(breadth)
    )

    expected = round((((1.01) * (1.02) * (1.03)) - 1) * 100, 2)
    assert result["stock_universe_benchmark_eligible"] is True
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] == expected
    assert result["stock_universe_benchmark_days"] == 3
    assert result["stock_universe_benchmark_expected_days"] == 3
    assert result["stock_universe_benchmark_start_date"] == "2025-06-01"
    assert result["stock_universe_benchmark_end_date"] == "2025-06-30"
    assert result["stock_universe_benchmark_min_coverage_pct"] == 95.0
    assert result["schema_version"] == "stock-universe-equal-weight-benchmark/v2"


def test_stock_benchmark_v2_has_same_shape_on_success_and_failure():
    success = _stock_universe_equal_weight_benchmark(
        {"2025-06-02": _breadth("2025-06-02", 1.0)},
        "2025-06-02",
        "2025-06-02",
        expected_sessions=["2025-06-02"],
    )
    missing_session = _stock_universe_equal_weight_benchmark(
        {},
        "2025-06-02",
        "2025-06-02",
        expected_sessions=["2025-06-02"],
    )
    missing_end = _stock_universe_equal_weight_benchmark(
        {"2025-06-02": _breadth("2025-06-02", 1.0)},
        "2025-06-02",
        None,
        expected_sessions=["2025-06-02"],
    )

    assert set(success) == set(missing_session) == set(missing_end)
    assert all(
        result["schema_version"] == "stock-universe-equal-weight-benchmark/v2"
        for result in (success, missing_session, missing_end)
    )


def test_stock_benchmark_reports_observed_minimum_coverage_separately():
    breadth = {
        "2025-06-02": _breadth("2025-06-02", 1.0, coverage=99.0),
        "2025-06-03": _breadth("2025-06-03", 1.0, coverage=96.0),
    }
    result = _stock_universe_equal_weight_benchmark(
        breadth,
        "2025-06-01",
        "2025-06-30",
        expected_sessions=sorted(breadth),
    )
    assert result["stock_universe_benchmark_min_coverage_pct"] == 96.0
    assert result["stock_universe_benchmark_required_coverage_pct"] == 95.0


def test_stock_benchmark_date_boundary_excludes_out_of_range():
    breadth = {
        "2025-05-30": _breadth("2025-05-30", 5.0),  # before start
        "2025-06-02": _breadth("2025-06-02", 1.0),
        "2025-07-01": _breadth("2025-07-01", 9.0),  # after end
    }
    result = _stock_universe_equal_weight_benchmark(
        breadth, "2025-06-01", "2025-06-30", expected_sessions=["2025-06-02"]
    )
    assert result["stock_universe_benchmark_days"] == 1
    assert result["stock_universe_benchmark_eligible"] is True
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] == 1.0


def test_stock_benchmark_fails_closed_on_low_coverage():
    breadth = {"2025-06-02": _breadth("2025-06-02", 1.0, coverage=94.0)}
    result = _stock_universe_equal_weight_benchmark(
        breadth, "2025-06-01", "2025-06-30", expected_sessions=["2025-06-02"]
    )
    assert result["stock_universe_benchmark_eligible"] is False
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] is None
    assert result["stock_universe_benchmark_reasons"]


def test_stock_benchmark_fails_closed_on_small_eligible():
    breadth = {"2025-06-02": _breadth("2025-06-02", 1.0, eligible=99)}
    result = _stock_universe_equal_weight_benchmark(
        breadth, "2025-06-01", "2025-06-30", expected_sessions=["2025-06-02"]
    )
    assert result["stock_universe_benchmark_eligible"] is False
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] is None


def test_stock_benchmark_fails_closed_on_nan_daily_return():
    breadth = {
        "2025-06-02": _breadth("2025-06-02", None),
    }
    result = _stock_universe_equal_weight_benchmark(
        breadth, "2025-06-01", "2025-06-30", expected_sessions=["2025-06-02"]
    )
    assert result["stock_universe_benchmark_eligible"] is False
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] is None


def test_stock_benchmark_fails_closed_on_empty_window():
    result = _stock_universe_equal_weight_benchmark(
        {}, "2025-06-01", "2025-06-30", expected_sessions=[]
    )
    assert result["stock_universe_benchmark_eligible"] is False
    assert result["stock_universe_equal_weight_daily_rebalanced_return_pct"] is None
    assert result["stock_universe_benchmark_days"] == 0


def test_stock_benchmark_fails_closed_when_analysis_end_missing():
    result = _stock_universe_equal_weight_benchmark(
        {"2025-06-02": _breadth("2025-06-02", 1.0)},
        "2025-06-01",
        None,
        expected_sessions=["2025-06-02"],
    )
    assert result["stock_universe_benchmark_eligible"] is False


def test_stock_benchmark_fails_closed_on_missing_or_extra_session():
    breadth = {
        "2025-06-02": _breadth("2025-06-02", 1.0),
        "2025-06-03": _breadth("2025-06-03", 1.0),
    }
    missing = _stock_universe_equal_weight_benchmark(
        {"2025-06-02": breadth["2025-06-02"]},
        "2025-06-01",
        "2025-06-30",
        expected_sessions=["2025-06-02", "2025-06-03"],
    )
    extra = _stock_universe_equal_weight_benchmark(
        breadth,
        "2025-06-01",
        "2025-06-30",
        expected_sessions=["2025-06-02"],
    )
    assert missing["stock_universe_benchmark_eligible"] is False
    assert extra["stock_universe_benchmark_eligible"] is False


def test_stock_benchmark_rejects_total_loss_or_worse():
    for daily_return in (-100.0, -101.0):
        result = _stock_universe_equal_weight_benchmark(
            {"2025-06-02": _breadth("2025-06-02", daily_return)},
            "2025-06-01",
            "2025-06-30",
            expected_sessions=["2025-06-02"],
        )
        assert result["stock_universe_benchmark_eligible"] is False


# ---------------------------------------------------------------------------
# 6. historical summary 声明 stock-only 来源；序列化无 proxy/ETF
# ---------------------------------------------------------------------------


def test_historical_summary_declares_stock_only_source_and_serializes_clean(tmp_path, monkeypatch):
    _force_signal(monkeypatch, action="BUY", score=5.0)
    provider = StockOnlyProvider(direction="up")
    result = _run_stock_only(provider, _stock_only_settings(tmp_path), tmp_path)
    summary = result["summary"]

    assert summary["market_context_source"] == "point_in_time_stock_breadth"
    assert summary["market_context_etf_dependent"] is False
    assert "market_context_schema_version" in summary
    assert summary["artifact_evaluation_window"] is None
    assert summary["stock_universe_benchmark_start_date"] == "2025-04-01"
    assert "hs300etf_buy_hold_pct" not in summary
    assert "cybetf_buy_hold_pct" not in summary

    blob = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "510300" not in blob and "159915" not in blob
