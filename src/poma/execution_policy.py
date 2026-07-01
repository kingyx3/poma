from __future__ import annotations

import math
from dataclasses import replace

from poma.models import InstrumentExecutionRule, ProposedTrade

DEFAULT_EXECUTION_RULE = InstrumentExecutionRule(ticker="*")


def build_execution_rules(non_fractional_tickers: str) -> dict[str, InstrumentExecutionRule]:
    """Build per-ticker rules for instruments known not to accept fractional orders.

    Every ticker not listed here keeps the fractional-friendly default, since a small
    portfolio depends on fractional sizing to hit its target weights.
    """
    tickers = [ticker.strip().upper() for ticker in non_fractional_tickers.split(",") if ticker.strip()]
    return {
        ticker: InstrumentExecutionRule(
            ticker=ticker,
            allows_fractional=False,
            min_quantity=1.0,
            quantity_increment=1.0,
        )
        for ticker in tickers
    }


def _rounded_quantity(quantity: float, rule: InstrumentExecutionRule) -> float:
    if not rule.allows_fractional:
        quantity = math.floor(quantity)
    if rule.quantity_increment > 0:
        quantity = math.floor(quantity / rule.quantity_increment + 1e-9) * rule.quantity_increment
    return quantity


def apply_execution_policy(
    trades: list[ProposedTrade],
    rules: dict[str, InstrumentExecutionRule],
) -> tuple[list[ProposedTrade], list[str]]:
    """Round each trade to what its instrument can actually execute.

    Rounding only ever moves toward zero (floor), never up, so a trade never grows past the
    sizing the risk engine already approved. Trades that round below their tradable minimum
    are dropped with a warning instead of being sent to the broker as an invalid order.
    """
    adjusted: list[ProposedTrade] = []
    warnings: list[str] = []
    for trade in trades:
        rule = rules.get(trade.ticker, DEFAULT_EXECUTION_RULE)
        quantity = _rounded_quantity(trade.quantity, rule)
        if quantity <= 0 or quantity < rule.min_quantity:
            warnings.append(
                f"{trade.ticker}: quantity {trade.quantity:.6f} rounds below the tradable "
                f"minimum for this instrument (allows_fractional={rule.allows_fractional}); "
                "skipping trade"
            )
            continue
        if quantity != trade.quantity:
            trade = replace(trade, quantity=quantity, notional=quantity * trade.reference_price)
        adjusted.append(trade)
    return adjusted, warnings
