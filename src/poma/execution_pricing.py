from __future__ import annotations

from dataclasses import replace

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


def select_execution_price(
    quote: ExecutionQuote,
    side: OrderSide,
    settings: Settings,
) -> tuple[float | None, list[str]]:
    """Select and validate one trade's execution reference price from a broker quote.

    Returns ``(None, warnings)`` when the quote fails a freshness, spread, or delayed-data
    check; every such warning carries the engine's ``block execution`` marker so the caller
    treats it as a hard stop rather than a soft fallback.
    """
    ticker = quote.ticker
    if quote.is_delayed and not settings.allow_delayed_execution_quotes:
        return None, [
            f"delayed execution quote for {ticker} but ALLOW_DELAYED_EXECUTION_QUOTES=false; "
            "block execution"
        ]

    max_age = settings.execution_quote_max_age_seconds
    if quote.age_seconds is None:
        reason = f" ({quote.broker_error})" if quote.broker_error else ""
        return None, [f"missing quote timestamp for {ticker}{reason}; block execution"]
    if quote.age_seconds > max_age:
        return None, [
            f"stale {quote.source} quote for {ticker} age={quote.age_seconds:.0f}s "
            f"max={max_age}s; block execution"
        ]

    spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)
    basis = settings.execution_price_basis

    if basis == ExecutionPriceBasis.LAST:
        if quote.last is None or quote.last <= 0:
            return None, [f"{ticker} missing last price; block execution"]
        return quote.last, []

    if basis == ExecutionPriceBasis.MIDPOINT:
        if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0:
            missing = "bid" if quote.bid is None or quote.bid <= 0 else "ask"
            return None, [f"{ticker} missing {missing}; block execution"]
        if spread_bps is not None and spread_bps > settings.execution_max_spread_bps:
            return None, [
                f"wide quote for {ticker} spread={spread_bps:.0f}bps "
                f"max={settings.execution_max_spread_bps:.0f}bps; block execution"
            ]
        return (quote.bid + quote.ask) / 2, []

    # side_of_market: BUY references the ask (what a buyer must pay), SELL references the bid
    # (what a seller can actually receive).
    if side == OrderSide.BUY:
        price = quote.ask
        missing_label = "ask"
    else:
        price = quote.bid
        missing_label = "bid"
    if price is None or price <= 0:
        return None, [f"{ticker} missing {missing_label}; block execution"]
    if spread_bps is not None and spread_bps > settings.execution_max_spread_bps:
        return None, [
            f"wide quote for {ticker} spread={spread_bps:.0f}bps "
            f"max={settings.execution_max_spread_bps:.0f}bps; block execution"
        ]
    return price, []


# --- Repricing trades against broker execution quotes ------------------------------------------


def apply_execution_quotes(
    trades: list[ProposedTrade],
    quotes: dict[str, ExecutionQuote],
    settings: Settings,
    rules: dict[str, InstrumentExecutionRule] | None = None,
) -> tuple[list[ProposedTrade], list[str]]:
    """Reprice every trade off a fresh broker quote, dropping any that fail a safety check.

    Quantity is recomputed from the trade's already-approved notional divided by the newly
    selected reference price, so a moved quote changes share count rather than order size.
    When ``rules`` is provided, the recomputed quantity is re-rounded to what the instrument
    can execute (whole shares by default), so repricing never reintroduces a fractional size
    the broker would reject.
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

        price, price_warnings = select_execution_price(quote, trade.side, settings)
        if price is None:
            warnings.extend(price_warnings)
            continue

        spread_bps = quote.spread_bps if quote.spread_bps is not None else compute_spread_bps(quote.bid, quote.ask)
        quantity = trade.notional / price
        if rules is not None:
            rule = resolve_execution_rule(trade.ticker, rules)
            quantity = rounded_execution_quantity(quantity, trade.side, rule)
            if quantity <= 0 or quantity < rule.min_quantity:
                warnings.append(
                    f"{trade.ticker}: repriced quantity {trade.notional / price:.6f} rounds "
                    "below the tradable minimum for this instrument; skipping trade"
                )
                continue
        repriced.append(
            replace(
                trade,
                quantity=quantity,
                notional=quantity * price,
                reference_price=price,
                limit_price=build_limit_price(trade.side, price, settings.limit_offset_bps),
                reference_price_source=settings.execution_price_source.value,
                reference_price_basis=settings.execution_price_basis.value,
                reference_price_as_of_utc=quote.selected_price_as_of_utc,
                quote_age_seconds=quote.age_seconds,
                quote_spread_bps=spread_bps,
            )
        )
    return repriced, warnings
