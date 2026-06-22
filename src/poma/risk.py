from __future__ import annotations

from poma.models import CurrentPosition, OrderSide, ProposedTrade, TargetPosition


def validate_targets(targets: list[TargetPosition], max_position_pct: float) -> list[str]:
    warnings: list[str] = []
    total_weight = sum(t.target_weight for t in targets)
    if total_weight > 1.000001:
        warnings.append(f"target weights exceed 100%: {total_weight:.4f}")
    oversized = [t for t in targets if t.target_weight > max_position_pct + 1e-9]
    if oversized:
        warnings.append("one or more target weights exceed max_position_pct")
    if not targets:
        warnings.append("no target positions generated")
    return warnings


def generate_trades(
    targets: list[TargetPosition],
    current_positions: list[CurrentPosition],
    min_trade_notional_usd: float,
) -> list[ProposedTrade]:
    current_by_ticker = {p.ticker: p.market_value for p in current_positions}
    target_by_ticker = {t.ticker: t.target_notional for t in targets}
    tickers = sorted(set(current_by_ticker) | set(target_by_ticker))
    trades: list[ProposedTrade] = []
    for ticker in tickers:
        delta = target_by_ticker.get(ticker, 0.0) - current_by_ticker.get(ticker, 0.0)
        if abs(delta) < min_trade_notional_usd:
            continue
        trades.append(
            ProposedTrade(
                ticker=ticker,
                side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                notional=abs(delta),
                reason="rebalance_to_target_weight",
            )
        )
    return trades


def enforce_turnover_limit(
    trades: list[ProposedTrade],
    portfolio_value_usd: float,
    max_turnover_pct: float,
) -> list[str]:
    turnover = sum(t.notional for t in trades) / portfolio_value_usd if portfolio_value_usd else 0
    if turnover > max_turnover_pct:
        return [f"turnover {turnover:.2%} exceeds limit {max_turnover_pct:.2%}; block execution"]
    return []
