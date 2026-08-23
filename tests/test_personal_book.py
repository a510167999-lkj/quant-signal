from __future__ import annotations

import json
from pathlib import Path

from app import personal_book as book
from app import personal_capital_contract as contract
from app import research_goal_contract as goal


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


def test_pause_entries_after_ten_percent_drawdown() -> None:
    state = book.empty_state(200_000)
    state["equity"] = 179_000.0
    state["peak_equity"] = 200_000.0
    halted = book.refresh_halt(state)
    assert halted["halt"] == "pause_entries"
    bounce = {
        "status": "buy",
        "as_of": "2026-08-21",
        "pick": {"symbol": "000001", "name": "平安银行", "entry_date": "2026-08-22"},
        "holding": None,
    }
    ticket = book.build_ticket(bounce, halted, price=10.0)
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
