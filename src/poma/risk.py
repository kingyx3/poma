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


def _build_limit_price(side: OrderSide, reference_price: float, offset_bps: float) -> float:
    multiplier = 1 + offset_bps / 10_000 if side == OrderSide.BUY else 1 - offset_bps / 10_000
    return round(reference_price * multiplier, 2)


def generate_trades(
    targets: list[TargetPosition],
    current_positions: list[CurrentPosition],
    latest_prices: dict[str, float],
    portfolio_value_usd: float,
    min_trade_notional_usd: float,
    min_weight_delta_pct: float,
    limit_offset_bps: float,
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

        reference_price = latest_prices.get(ticker)
        # reference_price != reference_price catches NaN, which is truthy and not <= 0.
        if reference_price is None or reference_price != reference_price or reference_price <= 0:
            warnings.append(f"missing valid latest price for {ticker}; skipping trade")
            continue

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta) / reference_price
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
                reference_price=reference_price,
                limit_price=_build_limit_price(side, reference_price, limit_offset_bps),
                reason="rebalance_to_target_weight",
            )
        )
    return trades, warnings


def enforce_turnover_limit(
    trades: list[ProposedTrade],
    portfolio_value_usd: float,
    max_turnover_pct: float,
) -> list[str]:
    turnover = 0.0
    if portfolio_value_usd:
        turnover = sum(t.notional for t in trades) / portfolio_value_usd
    if turnover > max_turnover_pct:
        return [
            f"turnover {turnover:.2%} exceeds limit {max_turnover_pct:.2%}; block execution"
        ]
    return []


def enforce_order_limits(
    trades: list[ProposedTrade],
    max_order_notional_usd: float,
    max_daily_trades: int,
) -> list[str]:
    warnings: list[str] = []
    if len(trades) > max_daily_trades:
        warnings.append(
            f"trade count {len(trades)} exceeds limit {max_daily_trades}; block execution"
        )
    oversized = [trade for trade in trades if trade.notional > max_order_notional_usd]
    if oversized:
        tickers = ", ".join(trade.ticker for trade in oversized)
        warnings.append(
            f"orders exceed max notional {max_order_notional_usd:.2f}: {tickers}; block execution"
        )
    return warnings
