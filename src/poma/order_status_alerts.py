from __future__ import annotations

from poma.models import OrderResult


def order_status_alert(session_date: str, result: OrderResult) -> str:
    order_id = f" id={result.order_id}" if result.order_id is not None else ""
    average_fill = "" if result.average_fill_price is None else f" avg={result.average_fill_price:.2f}"
    return (
        f"{session_date}: order status changed — {result.status} "
        f"{result.side.value} {result.ticker} filled={result.filled:g}/{result.quantity:g} "
        f"(${result.notional:,.0f}){average_fill}{order_id}"
    )
