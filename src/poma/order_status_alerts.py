from __future__ import annotations

from poma.models import OrderResult, ProposedTrade
from poma.order_lifecycle import OrderLedgerEntry


def _quote_line(
    source: str | None,
    basis: str | None,
    age_seconds: float | None,
    spread_bps: float | None,
) -> str | None:
    """One "Quote: ..." line summarizing freshness, in case a report reader needs to trust it."""
    if not source or source == "snapshot":
        return None
    bits = [f"source={source}"]
    if basis:
        bits.append(f"basis={basis}")
    if age_seconds is not None:
        bits.append(f"quote_age={age_seconds:.1f}s")
    if spread_bps is not None:
        bits.append(f"spread={spread_bps:.1f}bps")
    return "Quote: " + ", ".join(bits)


def lifecycle_status_alert(entry: OrderLedgerEntry, action: str | None) -> str:
    """Render a Telegram message for an order lifecycle change found during reconciliation."""
    lines = [
        "🔁 Order lifecycle update",
        f"Session: {entry.session_date}",
        f"Status: {entry.lifecycle_state.value}",
        f"Order: {entry.side.value} {entry.ticker}",
        f"Filled: {entry.filled_qty:g}/{entry.quantity:g}",
    ]
    if entry.avg_fill_price is not None:
        lines.append(f"Average fill: ${entry.avg_fill_price:.2f}")
    if action == "replace":
        lines.append(f"Action: replaced with a more aggressive limit (${entry.limit_price:.2f})")
    elif action == "cancel":
        lines.append("Action: cancelled after exceeding the unfilled-order timeout")
    if entry.order_id is not None:
        lines.append(f"Order ID: {entry.order_id}")
    quote_line = _quote_line(
        entry.reference_price_source,
        entry.reference_price_basis,
        entry.quote_age_seconds,
        entry.quote_spread_bps,
    )
    if quote_line:
        lines.append(quote_line)
    if entry.terminal_reason:
        lines.append(f"Detail: {entry.terminal_reason}")
    return "\n".join(lines)


def order_status_alert(session_date: str, result: OrderResult, trade: ProposedTrade | None = None) -> str:
    """Render a clear Telegram message for an individual order status change.

    ``trade`` is optional so historical/blocked-order callers that only have an ``OrderResult``
    keep working; when present, its execution-quote metadata is included so the message shows
    exactly what price the order was anchored against and how fresh that quote was.
    """
    lines = [
        "🔔 Order status update",
        f"Session: {session_date}",
        f"Status: {result.status}",
        f"Order: {result.side.value} {result.ticker}",
        f"Filled: {result.filled:g}/{result.quantity:g}",
        f"Notional: ${result.notional:,.0f}",
    ]
    if result.average_fill_price is not None:
        lines.append(f"Average fill: ${result.average_fill_price:.2f}")
    if result.order_id is not None:
        lines.append(f"Order ID: {result.order_id}")
    if trade is not None:
        quote_line = _quote_line(
            trade.reference_price_source,
            trade.reference_price_basis,
            trade.quote_age_seconds,
            trade.quote_spread_bps,
        )
        if quote_line:
            lines.append(quote_line)
    if result.message:
        lines.append(f"Detail: {result.message}")
    return "\n".join(lines)
