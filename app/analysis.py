from app.backtest import evaluate_signal_outcomes, run_backtest
from app.market_data import frame_to_points, normalize_symbol
from app.schemas import AnalyzeRequest, AnalyzeResponse
from app.signals import evaluate_signal


def build_analysis(request: AnalyzeRequest, data_provider, disclaimer: str) -> AnalyzeResponse:
    normalized_symbol = normalize_symbol(request.symbol, request.market)
    frame, source = data_provider.history(
        symbol=normalized_symbol,
        market=request.market,
        lookback_days=request.lookback_days,
        adjust=request.adjust,
    )
    signal = evaluate_signal(frame)
    backtest = run_backtest(frame)
    backtest["signal_outcomes"] = evaluate_signal_outcomes(frame)
    return AnalyzeResponse(
        symbol=normalized_symbol,
        market=request.market,
        name=request.name,
        source=source,
        as_of=signal["as_of"],
        action=signal["action"],
        action_label=signal["action_label"],
        score=signal["score"],
        confidence=signal["confidence"],
        last_close=signal["last_close"],
        entry_zone=signal["entry_zone"],
        levels=signal["levels"],
        trade_plans=signal["trade_plans"],
        reasons=signal["reasons"],
        risks=signal["risks"],
        confirmations=signal["confirmations"],
        indicators=signal["indicators"],
        candles=frame_to_points(frame, limit=220),
        backtest=backtest,
        disclaimer=disclaimer,
    )
