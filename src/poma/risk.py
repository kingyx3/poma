from __future__ import annotations

from poma.models import CurrentPosition, OrderSide, ProposedTrade, TargetPosition


def validate_targets(targets: list[TargetPosition], max_position_pct: float) -> list[str]:
    warnings: list[str] = []
    total_weight = sum(t.target_weight for t in targets)
    if total_weight > 1.000001:
        warnings.append(f"target weights exceed 100%: {total_weight:.4f}")
    if any(t.target_weight > max_position_pct + 1e-9 for t in targets):
        warnings.append("one or more target weights exceed max_position_pct")
    if not targets:
        warnings.append("no target positions generated")
    return warnings


def generate_trades(
    targets: list[TargetPosition],
    current_positions: list[CurrentPosition],
    latest_prices: dict[str, float],
    portfolio_value_usd: float,
    min_trade_notional_usd: float,
    min_weight_delta_pct: float,
) -> tuple[list[ProposedTrade], list[str]]:
    current_by_ticker = {p.ticker: p for p in current_positions}
    target_by_ticker = {t.ticker: t.target_notional for t in targets}
    tickers = sorted(set(current_by_ticker) | set(target_by_ticker))
    trades: list[ProposedTrade] = []
    warnings: list[str] = []

    for ticker in tickers:
        current = current_by_ticker.get(ticker)
        current_value = current.market_value if current else 0.0
        target_value = target_by_ticker.get(ticker, 0.0)
        delta = target_value - current_value
        if abs(delta) < min_trade_notional_usd:
            continue
        if abs(delta) / portfolio_value_usd < min_weight_delta_pct:
            continue

        price = latest_prices.get(ticker)
        if not price or price <= 0:
            warnings.append(f"missing valid latest price for {ticker}; skipping trade")
            continue

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta) / price
        if side == OrderSide.SELL and current:
            quantity = min(quantity, abs(current.quantity))
        if quantity <= 0:
            continue

        trades.append(
            ProposedTrade(
                ticker=ticker,
                side=side,
                quantity=quantity,
                notional=abs(delta),
                reason="rebalance_to_target_weight",
            )
        )
    return trades, warnings


def enforce_turnover_limit(
    trades: list[ProposedTrade],
    portfolio_value_usd: float,
    max_turnover_pct: float,
) -> list[str]:
    turnover = sum(t.notional for t in trades) / portfolio_value_usd if portfolio_value_usd else 0
    if turnover > max_turnover_pct:
        return [f"turnover {turnover:.2%} exceeds limit {max_turnover_pct:.2%}; block execution"]
    return []
