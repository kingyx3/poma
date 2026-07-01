from __future__ import annotations

from poma.models import OrderResult
from poma.order_lifecycle import OrderLedgerEntry


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
    if entry.terminal_reason:
        lines.append(f"Detail: {entry.terminal_reason}")
    return "\n".join(lines)


def order_status_alert(session_date: str, result: OrderResult) -> str:
    """Render a clear Telegram message for an individual order status change."""
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
    if result.message:
        lines.append(f"Detail: {result.message}")
    return "\n".join(lines)
