from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite

from poma.config import ExecutionPriceBasis, Settings
from poma.execution_policy import resolve_execution_rule, rounded_execution_quantity
from poma.models import ExecutionQuote, InstrumentExecutionRule, OrderSide, ProposedTrade

# --- Limit price construction ----------------------------------------------------------------


def build_limit_price(side: OrderSide, reference_price: float, offset_bps: float) -> float:
    """Offset a limit price away from the reference price so it can rest and still fill.

    BUY offsets up (willing to pay slightly more than the reference); SELL offsets down
    (willing to accept slightly less), each by ``offset_bps`` basis points.
    """
    multiplier = 1 + offset_bps / 10_000 if side == OrderSide.BUY else 1 - offset_bps / 10_000
    return round(reference_price * multiplier, 2)


# --- Quote spread ---------------------------------------------------------------------------


def compute_spread_bps(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    midpoint = (bid + ask) / 2
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * 10_000


# --- Execution reference price selection -----------------------------------------------------

# Basis label recorded on a trade priced off the midpoint because its spread was wide.
WIDE_SPREAD_MIDPOINT_BASIS = "midpoint_wide_spread"


@dataclass(frozen=True)
class QuotePricing:
    """One trade's execution reference and limit price, or why the quote cannot price it."""

    reference_price: float | None
    limit_price: float | None
    basis: str
    warnings: list[str]


def select_execution_price(
    quote: ExecutionQuote,
    side: OrderSide,
    settings: Settings,
    *,
    allow_wide_spread: bool = False,
) -> tuple[float | None, list[str]]:
    """Select and validate one trade's execution reference price from a broker quote.

    Returns ``(None, warnings)`` when the quote fails a freshness, spread, or delayed-data
    check; every such warning carries the engine's ``block execution`` marker so the caller
    treats it as a hard stop rather than a soft fallback. ``allow_wide_spread`` admits spreads up
    to ``EXECUTION_WIDE_SPREAD_MAX_BPS``, priced off the midpoint (see ``price_from_quote``).
    """
    price, _basis, warnings = _select_execution_price(quote, side, settings, allow_wide_spread=allow_wide_spread)
    return price, warnings


def price_from_quote(
    quote: ExecutionQuote,
    side: OrderSide,
    settings: Settings,
    offset_bps: float,
    *,
    allow_wide_spread: bool = False,
) -> QuotePricing:
    """Reference price plus offset limit price for one trade.

    A wide-spread midpoint reference is the only case where the offset limit is capped: it is
    never allowed past the far side of the quote (the ask for a BUY, the bid for a SELL), so a
    wide-spread order is always at least as passive as plain side-of-market pricing.
    """
    price, basis, warnings = _select_execution_price(quote, side, settings, allow_wide_spread=allow_wide_spread)
    if price is None:
        return QuotePricing(None, None, basis, warnings)
    limit_price = build_limit_price(side, price, offset_bps)
    if basis == WIDE_SPREAD_MIDPOINT_BASIS:
        if side == OrderSide.BUY and quote.ask is not None:
            limit_price = min(limit_price, round(quote.ask, 2))
        elif side == OrderSide.SELL and quote.bid is not None:
            limit_price = max(limit_price, round(quote.bid, 2))
    return QuotePricing(price, limit_price, basis, warnings)


def _select_execution_price(
    quote: ExecutionQuote,
    side: OrderSide,
    settings: Settings,
    *,
    allow_wide_spread: bool,
) -> tuple[float | None, str, list[str]]:
    ticker = quote.ticker
    basis = settings.execution_price_basis
    if quote.contract_unresolved:
        reason = f" ({quote.broker_error})" if quote.broker_error else ""
        return None, basis.value, [
            f"IBKR has no US stock contract for {ticker}{reason}; the symbol may have changed "
            "or been delisted; block execution"
        ]
    numeric_fields = (quote.bid, quote.ask, quote.last, quote.age_seconds, quote.spread_bps)
    if any(value is not None and not isfinite(value) for value in numeric_fields):
        return None, basis.value, [f"non-finite execution quote for {ticker}; block execution"]
    if quote.age_seconds is not None and quote.age_seconds < 0:
        return None, basis.value, [f"invalid quote age for {ticker}; block execution"]
    if ((quote.bid is not None and quote.ask is not None and quote.bid > quote.ask)
            or (quote.spread_bps is not None and quote.spread_bps < 0)):
        return None, basis.value, [f"crossed execution quote for {ticker}; block execution"]
    if quote.is_delayed and not settings.allow_delayed_execution_quotes:
        return None, basis.value, [
            f"delayed execution quote for {ticker} but ALLOW_DELAYED_EXECUTION_QUOTES=false; "
            "block execution"
        ]

    max_age = settings.execution_quote_max_age_seconds
    if quote.age_seconds is None:
        reason = f" ({quote.broker_error})" if quote.broker_error else ""
        return None, basis.value, [f"missing quote timestamp for {ticker}{reason}; block execution"]
    if quote.age_seconds > max_age:
        return None, basis.value, [
            f"stale {quote.source} quote for {ticker} age={quote.age_seconds:.0f}s "
            f"max={max_age}s; block execution"
        ]

    spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)

    if basis == ExecutionPriceBasis.LAST:
        if quote.last is None or quote.last <= 0:
            return None, basis.value, [f"{ticker} missing last price; block execution"]
        return quote.last, basis.value, []

    # side_of_market: BUY references the ask (what a buyer must pay), SELL references the bid
    # (what a seller can actually receive). midpoint needs both sides.
    if basis == ExecutionPriceBasis.MIDPOINT:
        if quote.bid is None or quote.bid <= 0 or quote.ask is None or quote.ask <= 0:
            missing = "bid" if quote.bid is None or quote.bid <= 0 else "ask"
            return None, basis.value, [f"{ticker} missing {missing}; block execution"]
    else:
        price = quote.ask if side == OrderSide.BUY else quote.bid
        if price is None or price <= 0:
            missing_label = "ask" if side == OrderSide.BUY else "bid"
            return None, basis.value, [f"{ticker} missing {missing_label}; block execution"]

    if spread_bps is not None and spread_bps > settings.execution_max_spread_bps:
        ceiling = settings.execution_wide_spread_max_bps
        two_sided = quote.bid is not None and quote.ask is not None
        if allow_wide_spread and two_sided and spread_bps <= ceiling:
            return (quote.bid + quote.ask) / 2, WIDE_SPREAD_MIDPOINT_BASIS, []
        limit = ceiling if allow_wide_spread else settings.execution_max_spread_bps
        return None, basis.value, [
            f"wide quote for {ticker} spread={spread_bps:.0f}bps max={limit:.0f}bps; block execution"
        ]
    if basis == ExecutionPriceBasis.MIDPOINT:
        return (quote.bid + quote.ask) / 2, basis.value, []
    return price, basis.value, []


# --- Repricing trades against broker execution quotes ------------------------------------------


def apply_execution_quotes(
    trades: list[ProposedTrade],
    quotes: dict[str, ExecutionQuote],
    settings: Settings,
    rules: dict[str, InstrumentExecutionRule] | None = None,
    *,
    allow_wide_spread: bool = False,
) -> tuple[list[ProposedTrade], list[str]]:
    """Reprice every trade off a fresh broker quote, dropping any that fail a safety check.

    BUY quantity is recomputed from the trade's already-approved notional so price movement does
    not silently increase the intended buy notional. SELL quantity deliberately preserves the
    already-approved share delta: recomputing a one-share sell from ``notional / fresh_price``
    can turn it into 0.99 shares after a small price rise, which then floors to zero under the
    whole-share execution rule and incorrectly drops the sell. The preserved SELL quantity is
    still re-rounded against the instrument rule before submission.
    """
    repriced: list[ProposedTrade] = []
    warnings: list[str] = []
    for trade in trades:
        quote = quotes.get(trade.ticker)
        if quote is None:
            warnings.append(
                f"missing {settings.execution_price_source.value} execution quote for "
                f"{trade.ticker}; block execution"
            )
            continue

        pricing = price_from_quote(
            quote, trade.side, settings, settings.limit_offset_bps, allow_wide_spread=allow_wide_spread
        )
        price, limit_price = pricing.reference_price, pricing.limit_price
        if price is None or limit_price is None:
            warnings.extend(pricing.warnings)
            continue

        spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)
        raw_quantity = trade.quantity if trade.side == OrderSide.SELL else trade.notional / price
        quantity = raw_quantity
        if rules is not None:
            rule = resolve_execution_rule(trade.ticker, rules)
            quantity = rounded_execution_quantity(quantity, trade.side, rule)
            if quantity <= 0 or quantity < rule.min_quantity:
                warnings.append(
                    f"{trade.ticker}: repriced quantity {raw_quantity:.6f} rounds "
                    "below the tradable minimum for this instrument; skipping trade"
                )
                continue
        if limit_price <= 0 or quantity * max(price, limit_price) > settings.max_order_notional_usd:
            warnings.append(f"{trade.ticker}: repriced order exceeds price/notional safety limits; block execution")
            continue
        repriced.append(
            replace(
                trade,
                quantity=quantity,
                notional=quantity * price,
                reference_price=price,
                limit_price=limit_price,
                reference_price_source=settings.execution_price_source.value,
                reference_price_basis=pricing.basis,
                reference_price_as_of_utc=quote.selected_price_as_of_utc,
                quote_age_seconds=quote.age_seconds,
                quote_spread_bps=spread_bps,
            )
        )
    return repriced, warnings
