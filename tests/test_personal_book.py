from __future__ import annotations

import json
from pathlib import Path

from app import personal_book as book
from app import personal_capital_contract as contract
from app import personal_notify as notify
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import merged_kernel
from app.factor_v3_path_a_protocol_bounce_daily import bounce_dn2_negext_variant
from app.factor_v3_path_a_protocol_signal_specs import iter_protocol_signal_bounce_variants


def test_contract_stays_off_the_research_gate() -> None:
    assert contract.AUTOMATIC_TRADING_ALLOWED is False
    assert contract.EFFECTIVE_STRATEGY is False
    assert contract.PRODUCTION_PROFILE is False
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 26.0
    assert contract.CANDIDATE_ID == "bounce_dn2_negext"


def test_lots_round_down_and_reject_tiny_notional() -> None:
    sized = book.size_open(capital_cny=200_000, price=12.0, cash_cny=200_000)
    assert sized["lots"] == 83
    assert sized["shares"] == 8300
    assert abs(sized["notional"] - 99_600) < 1e-6
    tiny = book.size_open(capital_cny=200_000, price=12.0, cash_cny=800)
    assert tiny["action"] == "untradeable"


def test_commission_has_five_yuan_floor() -> None:
    assert book.commission_cny(1_000) == 5.0
    assert abs(book.commission_cny(100_000) - 25.0) < 1e-9
    sell = book.sell_costs_cny(100_000, symbol="600000")
    assert sell["stamp"] == 50.0
    assert abs(sell["transfer"] - 1.0) < 1e-9


def test_cash_intent_is_a_valid_ticket() -> None:
    bounce = {
        "status": "cash",
        "as_of": "2026-08-21",
        "data_through": "2026-08-21",
        "pick": None,
        "holding": None,
        "automatic_trading_allowed": False,
        "effective_strategy": False,
    }
    ticket = book.build_ticket(bounce, book.empty_state(200_000), price=None)
    assert ticket["action"] == "cash"
    assert ticket["automatic_trading_allowed"] is False
    assert ticket["effective_strategy"] is False
    assert "26" not in (ticket.get("headline") or "")


def test_buy_ticket_uses_half_capital_in_lots() -> None:
    bounce = {
        "status": "buy",
        "as_of": "2026-08-21",
        "data_through": "2026-08-21",
        "pick": {
            "symbol": "600621",
            "name": "华鑫股份",
            "signal_date": "2026-08-10",
            "entry_date": "2026-08-11",
            "exit_date": "2026-08-18",
        },
        "holding": None,
    }
    ticket = book.build_ticket(bounce, book.empty_state(200_000), price=12.0)
    assert ticket["action"] == "open"
    assert ticket["lots"] == 83
    assert ticket["symbol"] == "600621"


def test_skip_does_not_open_and_fill_uses_trade_price() -> None:
    skipped = book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "skip",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "side": "buy",
        },
    )
    assert skipped["cash"] == 200_000
    assert skipped["positions"] == []
    opened = book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
            "planned_exit": "2026-08-18",
        },
    )
    assert opened["positions"][0]["shares"] == 8300
    assert opened["cash"] < 200_000 - 99_000
    closed = book.apply_event(
        opened,
        {
            "kind": "fill",
            "as_of": "2026-08-18",
            "symbol": "600621",
            "side": "sell",
            "price": 11.80,
            "shares": 8300,
            "commission": 25.0,
        },
    )
    assert closed["positions"] == []
    assert closed["cash"] < 200_000


def _open_600621() -> dict:
    return book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
            "planned_exit": "2026-08-18",
        },
    )


def _buy_other() -> dict:
    return {
        "status": "buy",
        "as_of": "2026-08-21",
        "pick": {"symbol": "000001", "name": "平安银行", "entry_date": "2026-08-22"},
        "holding": None,
    }


def test_halt_ratchets_peak_then_blocks_buy_after_drawdown() -> None:
    opened = _open_600621()
    peaked = book.mark_to_market(opened, {"600621": 18.0})
    assert peaked["halt_peak"] == peaked["equity"]
    assert peaked["halt_peak"] > float(opened["capital"])
    pulled = book.mark_to_market(peaked, {"600621": 14.5})
    assert pulled["halt"] == "pause_entries"
    sold_pause = book.apply_event(
        pulled,
        {
            "kind": "fill",
            "as_of": "2026-08-18",
            "symbol": "600621",
            "side": "sell",
            "price": 14.5,
            "shares": 8300,
        },
    )
    paused_buy = book.build_ticket(_buy_other(), sold_pause, price=10.0)
    assert paused_buy["action"] == "halted"
    deep = book.mark_to_market(peaked, {"600621": 12.0})
    assert deep["halt"] == "flatten"
    held_buy = book.build_ticket(_buy_other(), deep, price=12.0, closes={"600621": 12.0})
    assert held_buy["action"] == "close"
    assert held_buy["symbol"] == "600621"
    sold_flat = book.apply_event(
        deep,
        {
            "kind": "fill",
            "as_of": "2026-08-18",
            "symbol": "600621",
            "side": "sell",
            "price": 12.0,
            "shares": 8300,
        },
    )
    assert sold_flat["flatten_until_resume"] is True
    flat_buy = book.build_ticket(_buy_other(), sold_flat, price=10.0)
    assert flat_buy["action"] == "halted"
    assert flat_buy["action"] != "open"


def test_pause_entries_after_ten_percent_drawdown() -> None:
    opened = book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
        },
    )
    cash = float(opened["cash"])
    close = (179_000.0 - cash) / 8300
    marked = book.mark_to_market(opened, {"600621": close})
    assert marked["halt"] == "pause_entries"
    bounce = {
        "status": "buy",
        "as_of": "2026-08-21",
        "pick": {"symbol": "000001", "name": "平安银行", "entry_date": "2026-08-22"},
        "holding": None,
    }
    ticket = book.build_ticket(bounce, marked, price=close, closes={"600621": close})
    assert ticket["action"] == "halted"


def test_skip_hides_same_day_buy_ticket() -> None:
    bounce = {
        "status": "buy",
        "as_of": "2026-08-11",
        "pick": {
            "symbol": "600621",
            "name": "华鑫股份",
            "entry_date": "2026-08-12",
        },
        "holding": None,
    }
    skipped = book.apply_event(
        book.empty_state(200_000),
        {"kind": "skip", "as_of": "2026-08-11", "symbol": "600621", "side": "buy"},
    )
    ticket = book.build_ticket(bounce, skipped, price=12.0)
    assert ticket["action"] == "cash"
    assert "跳过" in ticket["headline"]


def test_skipped_buy_does_not_become_a_hold() -> None:
    bounce = {
        "status": "holding",
        "as_of": "2026-08-12",
        "holding": {
            "symbol": "600621",
            "name": "华鑫股份",
            "exit_date": "2026-08-18",
        },
        "pick": None,
    }
    ticket = book.build_ticket(bounce, book.empty_state(200_000), price=12.0)
    assert ticket["action"] == "mismatch"
    assert ticket["positions_match"] is False


def test_skip_versus_fill_forks_personal_equity_not_research_oos() -> None:
    bounce = {
        "status": "buy",
        "as_of": "2026-08-11",
        "oos_book": {"net_return_pct": -2.64, "trade_count": 1},
        "ledger": [{"symbol": "600621", "return_pct": -2.1903}],
        "pick": {"symbol": "600621", "name": "华鑫股份", "entry_date": "2026-08-12"},
        "holding": None,
    }
    frozen = json.dumps(bounce, sort_keys=True)
    start = book.empty_state(200_000)
    skipped = book.apply_event(
        start,
        {"kind": "skip", "as_of": "2026-08-11", "symbol": "600621", "side": "buy"},
    )
    filled = book.apply_event(
        start,
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
        },
    )
    assert json.dumps(bounce, sort_keys=True) == frozen
    assert skipped["cash"] == 200_000
    assert skipped["equity"] == 200_000
    assert filled["cash"] < skipped["cash"]
    assert filled["positions"][0]["avg_price"] == 12.05


def test_mark_to_market_uses_close_not_avg_price() -> None:
    opened = book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
        },
    )
    cost_nav = float(opened["cash"]) + 8300 * 12.05
    marked = book.mark_to_market(opened, {"600621": 10.0})
    assert marked["equity"] == round(float(opened["cash"]) + 8300 * 10.0, 2)
    assert marked["equity"] != round(cost_nav, 2)
    assert marked["equity_mark"]["600621"]["source"] == "jiaoch_close"
    assert marked["equity_mark"]["600621"]["not_a_fill"] is True
    assert marked["equity_mark"]["600621"]["fill_avg_price"] == 12.05


def test_fifteen_percent_drawdown_closes_held_name_until_resume() -> None:
    opened = book.apply_event(
        book.empty_state(200_000),
        {
            "kind": "fill",
            "as_of": "2026-08-11",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
            "planned_exit": "2026-08-18",
        },
    )
    marked = book.mark_to_market(opened, {"600621": 8.0})
    assert marked["halt"] == "flatten"
    bounce_buy = {
        "status": "buy",
        "as_of": "2026-08-21",
        "pick": {"symbol": "000001", "name": "平安银行", "entry_date": "2026-08-22"},
        "holding": None,
    }
    ticket = book.build_ticket(bounce_buy, marked, price=8.0, closes={"600621": 8.0})
    assert ticket["action"] == "close"
    assert ticket["symbol"] == "600621"
    resumed = book.apply_event(marked, {"kind": "resume", "as_of": "2026-08-21"})
    assert resumed["flatten_until_resume"] is False
    after = book.build_ticket(bounce_buy, resumed, price=10.0)
    assert after["action"] in {"open", "cash", "untradeable", "halted"}
    assert after["action"] != "close"


def test_m2_sizer_caps_each_name_at_thirty_percent_and_is_not_default() -> None:
    default = book.build_ticket(
        {
            "status": "buy",
            "as_of": "2026-08-21",
            "pick": {"symbol": "600621", "name": "华鑫股份", "entry_date": "2026-08-22"},
            "holding": None,
        },
        book.empty_state(200_000),
        price=12.0,
    )
    assert default["candidate_id"] == "bounce_dn2_negext"
    assert default.get("slots") is None
    sized = book.size_open_m2(
        capital_cny=200_000,
        cash_cny=200_000,
        names=[
            {"symbol": "600621", "name": "华鑫股份", "price": 12.0},
            {"symbol": "000001", "name": "平安银行", "price": 10.0},
        ],
    )
    assert sized["candidate_id"] == "personal_bounce_m2"
    assert sized["effective_strategy"] is False
    assert len(sized["slots"]) == 2
    for slot in sized["slots"]:
        assert slot["action"] == "open"
        assert slot["notional"] <= 200_000 * 0.30 + 1e-6
    from app.factor_v3_path_a_protocol_signal_specs import iter_protocol_signal_bounce_variants

    assert "personal_bounce_m2" not in {
        row["candidate_id"] for row in iter_protocol_signal_bounce_variants()
    }


def test_notify_formatter_does_not_change_lots_or_cash(monkeypatch) -> None:
    ticket = {
        "action": "open",
        "symbol": "600621",
        "lots": 83,
        "cash": 100000.0,
    }
    before = dict(ticket)
    text = notify.format_ticket_notice(ticket)
    assert ticket == before
    assert "83" in text
    assert "100000" in text
    assert "600621" in text
    monkeypatch.delenv("PERSONAL_NOTIFY_WEBHOOK", raising=False)
    sent = notify.send_ticket_notice(ticket)
    assert sent["sent"] is False
    assert sent["reason"] == "missing_credentials"
    assert sent["text"] == text


def test_ui_keeps_follow_skip_and_bounce_kernel_untouched() -> None:
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "跳过" in html
    assert "我跟了" in html
    assert 'id="personalFillPrice"' in html
    full = merged_kernel(bounce_dn2_negext_variant())
    assert full["hold_days"] == 5
    assert full["symbol_cooldown_days"] == 5
    assert full["max_active_positions"] == 1


def test_cache_last_close(tmp_path: Path) -> None:
    path = tmp_path / "a_600621_1400_qfq.json"
    path.write_text(
        json.dumps(
            {
                "source": "Jiaoch stk_mins daily qfq; jiaoch-daily-bars/shares-cny/v2",
                "records": [
                    {"date": "2026-08-20", "close": 11.5},
                    {"date": "2026-08-21", "close": 12.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert book.last_close_from_cache(tmp_path, "600621") == 12.0
