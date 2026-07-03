from __future__ import annotations

from poma.execution_pricing import build_limit_price
from poma.models import CurrentPosition, OrderSide, ProposedTrade, TargetPosition

# --- Target risk: combined portfolio-level target checks -----------------------------------


def validate_targets(targets: list[TargetPosition], max_position_pct: float) -> list[str]:
    warnings: list[str] = []
    total_weight = sum(t.target_weight for t in targets)
    if total_weight > 1.000001:
        warnings.append(f"target weights exceed 100%: {total_weight:.4f}; block execution")
    if any(t.target_weight > max_position_pct + 1e-9 for t in targets):
        warnings.append("one or more target weights exceed max_position_pct; block execution")
    if not targets:
        warnings.append("no target positions generated")
    return warnings


# --- Trade risk: order generation and per-batch limits --------------------------------------


def _position_reference_price(position: CurrentPosition | None) -> float | None:
    if position is None or position.quantity <= 0 or position.market_value <= 0:
        return None
    return position.market_value / position.quantity


def generate_trades(
    targets: list[TargetPosition],
    current_positions: list[CurrentPosition],
    latest_prices: dict[str, float],
    portfolio_value_usd: float,
    min_trade_notional_usd: float,
    min_weight_delta_pct: float,
    limit_offset_bps: float,
) -> tuple[list[ProposedTrade], list[str]]:
    """Stage A (planning): size trade quantities off the Yahoo snapshot price.

    This never becomes the final execution limit price for paper/live orders; it only sizes
    quantity/notional for the plan. ``poma.execution_pricing.apply_execution_quotes`` reprices
    off a fresh broker quote immediately before submission (see ``ExecutionManager``).
    """
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
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        # reference_price != reference_price catches NaN, which is truthy and not <= 0.
        if reference_price is None or reference_price != reference_price or reference_price <= 0:
            broker_position_price = _position_reference_price(current)
            if side == OrderSide.SELL and broker_position_price is not None:
                reference_price = broker_position_price
                warnings.append(
                    f"missing valid latest price for held {ticker}; using broker position "
                    "market value for risk-reducing sell planning"
                )
            else:
                warnings.append(f"missing valid latest price for {ticker}; skipping trade")
                continue

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
                limit_price=build_limit_price(side, reference_price, limit_offset_bps),
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


def filter_trades_by_estimated_transaction_cost(
    trades: list[ProposedTrade],
    *,
    min_trade_notional_usd: float,
    estimated_transaction_cost_bps: float,
    estimated_transaction_cost_fixed_usd: float,
) -> tuple[list[ProposedTrade], list[str]]:
    """Drop trades whose estimated all-in cost overwhelms their rebalance value."""
    if estimated_transaction_cost_bps <= 0 and estimated_transaction_cost_fixed_usd <= 0:
        return trades, []

    kept: list[ProposedTrade] = []
    warnings: list[str] = []
    for trade in trades:
        estimated_cost = estimated_transaction_cost_fixed_usd + (
            trade.notional * estimated_transaction_cost_bps / 10_000
        )
        benefit_after_cost = trade.notional - estimated_cost
        if benefit_after_cost < min_trade_notional_usd:
            warnings.append(
                f"{trade.ticker}: estimated transaction cost ${estimated_cost:,.2f} "
                f"leaves ${benefit_after_cost:,.2f} of rebalance benefit, below "
                f"MIN_TRADE_NOTIONAL_USD=${min_trade_notional_usd:,.2f}; skipping trade"
            )
            continue
        kept.append(trade)
    return kept, warnings


def enforce_buying_power(trades: list[ProposedTrade], available_cash_usd: float) -> list[str]:
    """Block execution when planned net buy cash would exceed the account's available cash.

    BUY checks use the order's limit price, not only the reference notional, because that is
    the maximum cash the submitted order can consume. SELL credits use their limit as the
    conservative minimum proceeds for this preliminary netting check; live submission still
    refreshes broker cash after the sell phase before any buy order is sent.
    """
    net_cash_outflow = sum(trade.buy_cash_required_usd for trade in trades) - sum(
        trade.sell_cash_credit_usd for trade in trades
    )
    if net_cash_outflow > available_cash_usd + 1e-6:
        return [
            f"planned net buy cash requirement (${net_cash_outflow:,.2f}) exceeds available cash "
            f"(${available_cash_usd:,.2f}); block execution"
        ]
    return []
