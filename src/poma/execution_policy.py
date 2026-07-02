from __future__ import annotations

import math
from dataclasses import replace

from poma.models import InstrumentExecutionRule, OrderSide, ProposedTrade

DEFAULT_RULE_TICKER = "*"
FRACTIONAL_EXECUTION_RULE = InstrumentExecutionRule(ticker=DEFAULT_RULE_TICKER)
WHOLE_SHARE_EXECUTION_RULE = InstrumentExecutionRule(
    ticker=DEFAULT_RULE_TICKER,
    allows_fractional=False,
    min_quantity=1.0,
    quantity_increment=1.0,
)
# IBKR rejects fractional-sized API orders outright (error 10243 "Fractional-sized order cannot
# be placed via API"), so whole-share sizing is the default for every instrument.
DEFAULT_EXECUTION_RULE = WHOLE_SHARE_EXECUTION_RULE


def build_execution_rules(
    non_fractional_tickers: str,
    *,
    fractional_shares: bool = False,
) -> dict[str, InstrumentExecutionRule]:
    """Build the execution rule table, keyed by ticker with ``*`` as the default rule.

    Whole-share sizing is the default because the IBKR API refuses fractional order sizes
    (error 10243). ``fractional_shares=True`` restores fractional-friendly sizing as the
    default, with ``non_fractional_tickers`` naming per-ticker whole-share exceptions.
    """
    default = FRACTIONAL_EXECUTION_RULE if fractional_shares else WHOLE_SHARE_EXECUTION_RULE
    rules = {DEFAULT_RULE_TICKER: default}
    tickers = [ticker.strip().upper() for ticker in non_fractional_tickers.split(",") if ticker.strip()]
    for ticker in tickers:
        rules[ticker] = replace(WHOLE_SHARE_EXECUTION_RULE, ticker=ticker)
    return rules


def resolve_execution_rule(
    ticker: str,
    rules: dict[str, InstrumentExecutionRule],
) -> InstrumentExecutionRule:
    return rules.get(ticker) or rules.get(DEFAULT_RULE_TICKER) or DEFAULT_EXECUTION_RULE


def rounded_execution_quantity(quantity: float, side: OrderSide, rule: InstrumentExecutionRule) -> float:
    """Round a quantity to what the instrument can execute, by side.

    BUY rounds to the nearest whole share (up or down) so orders stay centered on the target
    notional; SELL always rounds down so a rounded order can never sell more than the position
    the plan sized against.
    """
    if not rule.allows_fractional:
        quantity = math.floor(quantity + 0.5) if side == OrderSide.BUY else math.floor(quantity)
    if rule.quantity_increment > 0:
        quantity = math.floor(quantity / rule.quantity_increment + 1e-9) * rule.quantity_increment
    return quantity


def apply_execution_policy(
    trades: list[ProposedTrade],
    rules: dict[str, InstrumentExecutionRule],
    *,
    available_cash_usd: float | None = None,
) -> tuple[list[ProposedTrade], list[str]]:
    """Round each trade to what its instrument can actually execute.

    Buys round to the nearest whole share and sells round down (see
    ``rounded_execution_quantity``). Trades that round below their tradable minimum are dropped
    with a warning instead of being sent to the broker as an invalid order. When
    ``available_cash_usd`` is provided, buy round-ups that would push total buy notional past
    available cash plus planned sell proceeds are demoted back down to their floored quantity,
    so rounding up can never turn an affordable plan into a buying-power block.
    """
    adjusted: list[ProposedTrade] = []
    floors_by_index: dict[int, float] = {}
    warnings: list[str] = []
    for trade in trades:
        rule = resolve_execution_rule(trade.ticker, rules)
        quantity = rounded_execution_quantity(trade.quantity, trade.side, rule)
        if quantity <= 0 or quantity < rule.min_quantity:
            warnings.append(
                f"{trade.ticker}: quantity {trade.quantity:.6f} rounds below the tradable "
                f"minimum for this instrument (allows_fractional={rule.allows_fractional}); "
                "skipping trade"
            )
            continue
        floored = rounded_execution_quantity(trade.quantity, OrderSide.SELL, rule)
        if trade.side == OrderSide.BUY and floored < quantity:
            floors_by_index[len(adjusted)] = floored
        if quantity != trade.quantity:
            trade = replace(trade, quantity=quantity, notional=quantity * trade.reference_price)
        adjusted.append(trade)

    if available_cash_usd is not None:
        adjusted, demotion_warnings = _demote_buy_roundups_to_fit_cash(
            adjusted, floors_by_index, rules, available_cash_usd
        )
        warnings.extend(demotion_warnings)
    return adjusted, warnings


def _demote_buy_roundups_to_fit_cash(
    trades: list[ProposedTrade],
    floors_by_index: dict[int, float],
    rules: dict[str, InstrumentExecutionRule],
    available_cash_usd: float,
) -> tuple[list[ProposedTrade], list[str]]:
    """Round buy round-ups back down until total buy notional fits the cash budget.

    The budget is available cash plus planned (already-rounded) sell proceeds, matching how
    ``enforce_buying_power`` nets a same-day rebalance. Largest round-up overshoot is demoted
    first; a demotion to below the tradable minimum drops the trade entirely.
    """
    buy_notional = sum(trade.notional for trade in trades if trade.side == OrderSide.BUY)
    sell_notional = sum(trade.notional for trade in trades if trade.side == OrderSide.SELL)
    budget = available_cash_usd + sell_notional
    overshoot = buy_notional - budget
    if overshoot <= 1e-6:
        return trades, []

    demotable = sorted(
        (
            ((trades[index].quantity - floored) * trades[index].reference_price, index, floored)
            for index, floored in floors_by_index.items()
        ),
        reverse=True,
    )

    warnings: list[str] = []
    dropped: set[int] = set()
    adjusted = list(trades)
    for saving, index, floored in demotable:
        if overshoot <= 1e-6:
            break
        trade = adjusted[index]
        rule = resolve_execution_rule(trade.ticker, rules)
        overshoot -= saving
        if floored <= 0 or floored < rule.min_quantity:
            dropped.add(index)
            warnings.append(
                f"{trade.ticker}: rounded-up buy dropped to fit available cash; skipping trade"
            )
            continue
        adjusted[index] = replace(trade, quantity=floored, notional=floored * trade.reference_price)
        warnings.append(
            f"{trade.ticker}: rounded-up buy demoted to {floored:g} share(s) to fit available cash"
        )
    return [trade for index, trade in enumerate(adjusted) if index not in dropped], warnings
