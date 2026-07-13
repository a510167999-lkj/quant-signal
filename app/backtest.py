from typing import Any, Dict, List

import pandas as pd

from app.execution import assess_entry_executability
from app.indicators import add_indicators
from app.signals import evaluate_signal


def _max_drawdown(equity: List[float]) -> float:
    peak = equity[0] if equity else 1.0
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, value / peak - 1)
    return max_dd


def run_backtest(frame: pd.DataFrame) -> Dict[str, Any]:
    enriched = add_indicators(frame)
    start_index = min(max(80, len(enriched) // 4), max(len(enriched) - 2, 1))

    cash = 1.0
    shares = 0.0
    entry_price = 0.0
    equity_curve: List[float] = []
    trades: List[Dict[str, Any]] = []

    for index in range(start_index, len(enriched) - 1):
        current = enriched.iloc[index]
        next_bar = enriched.iloc[index + 1]
        close_price = float(current["close"])
        next_open = float(next_bar.get("open", next_bar["close"]))
        signal = evaluate_signal(enriched.iloc[: index + 1])

        executable = assess_entry_executability(current, next_bar, decision_cutoff="next_open")
        if shares <= 0 and signal["action"] == "BUY" and executable["executable"]:
            shares = cash / next_open
            cash = 0.0
            entry_price = next_open
            trades.append(
                {
                    "signal_date": current["date"],
                    "date": next_bar["date"],
                    "side": "BUY",
                    "price": round(next_open, 4),
                    "score": signal["score"],
                    "entry_executability": executable,
                }
            )
        elif shares > 0:
            stop_loss = signal["levels"].get("stop_loss") or close_price * 0.94
            next_low = float(next_bar.get("low", next_open))
            stop_hit = bool(stop_loss and next_low <= float(stop_loss))
            should_exit = signal["action"] in {"SELL", "REDUCE"} or stop_hit
            if should_exit:
                exit_price = next_open
                if stop_hit and next_open > float(stop_loss):
                    exit_price = float(stop_loss)
                cash = shares * exit_price
                pnl = exit_price / entry_price - 1 if entry_price else 0
                shares = 0.0
                trades.append(
                    {
                        "signal_date": current["date"],
                        "date": next_bar["date"],
                        "side": "SELL",
                        "price": round(exit_price, 4),
                        "score": signal["score"],
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
        equity_curve.append(cash + shares * close_price)

    if shares > 0:
        final_price = float(enriched.iloc[-1]["close"])
        cash = shares * final_price
        pnl = final_price / entry_price - 1 if entry_price else 0
        trades.append(
            {
                "date": enriched.iloc[-1]["date"],
                "side": "MARK",
                "price": round(final_price, 4),
                "pnl_pct": round(pnl * 100, 2),
            }
        )

    strategy_return = cash - 1.0
    start_price = float(enriched.iloc[start_index]["close"])
    end_price = float(enriched.iloc[-1]["close"])
    buy_hold_return = end_price / start_price - 1 if start_price else 0
    exits = [trade for trade in trades if trade["side"] in {"SELL", "MARK"} and "pnl_pct" in trade]
    wins = [trade for trade in exits if trade["pnl_pct"] > 0]
    win_rate = len(wins) / len(exits) if exits else 0

    return {
        "start_date": str(enriched.iloc[start_index]["date"]),
        "end_date": str(enriched.iloc[-1]["date"]),
        "strategy_return_pct": round(strategy_return * 100, 2),
        "buy_hold_return_pct": round(buy_hold_return * 100, 2),
        "max_drawdown_pct": round(_max_drawdown(equity_curve) * 100, 2),
        "trade_count": len([trade for trade in trades if trade["side"] == "BUY"]),
        "win_rate_pct": round(win_rate * 100, 2),
        "recent_trades": trades[-10:],
    }


def evaluate_signal_outcomes(frame: pd.DataFrame, hold_days: int = 10) -> Dict[str, Any]:
    enriched = add_indicators(frame)
    start_index = min(max(80, len(enriched) // 4), max(len(enriched) - hold_days - 1, 1))
    outcomes: List[Dict[str, Any]] = []
    skipped_unexecutable_count = 0
    index = start_index
    while index < len(enriched) - hold_days - 1:
        signal = evaluate_signal(enriched.iloc[: index + 1])
        if signal["action"] in {"BUY", "WATCH"} and signal["score"] >= 2:
            entry_index = index + 1
            exit_index = entry_index + hold_days
            executable = assess_entry_executability(
                enriched.iloc[index],
                enriched.iloc[entry_index],
                decision_cutoff="next_open",
            )
            if not executable["executable"]:
                skipped_unexecutable_count += 1
                index += 1
                continue
            entry = float(enriched.iloc[entry_index].get("open", enriched.iloc[entry_index]["close"]))
            future = enriched.iloc[entry_index : exit_index + 1]
            exit_price = float(enriched.iloc[exit_index]["close"])
            future_return = exit_price / entry - 1 if entry else 0
            max_adverse = float(future["low"].min()) / entry - 1 if entry else 0
            outcomes.append(
                {
                    "signal_date": enriched.iloc[index]["date"],
                    "entry_date": enriched.iloc[entry_index]["date"],
                    "action": signal["action"],
                    "score": signal["score"],
                    "return_pct": round(future_return * 100, 2),
                    "max_adverse_pct": round(max_adverse * 100, 2),
                    "entry_executability": executable,
                }
            )
            index += hold_days
        else:
            index += 1

    wins = [item for item in outcomes if item["return_pct"] > 0]
    avg_return = sum(item["return_pct"] for item in outcomes) / len(outcomes) if outcomes else 0
    avg_adverse = sum(item["max_adverse_pct"] for item in outcomes) / len(outcomes) if outcomes else 0
    return {
        "hold_days": hold_days,
        "signal_count": len(outcomes),
        "win_rate_pct": round(len(wins) / len(outcomes) * 100, 2) if outcomes else 0,
        "avg_return_pct": round(avg_return, 2),
        "avg_adverse_pct": round(avg_adverse, 2),
        "skipped_unexecutable_count": skipped_unexecutable_count,
        "recent_outcomes": outcomes[-10:],
    }
