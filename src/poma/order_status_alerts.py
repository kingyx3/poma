from __future__ import annotations

from poma.models import OrderResult


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
