from __future__ import annotations

from collections.abc import Iterable

from poma.broker import ORDER_REF_PREFIX, _connect_ib
from poma.config import Settings
from poma.models import OpenOrderSnapshot, OrderSide

_TERMINAL_COMPLETED_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


def fetch_completed_order_snapshots(
    settings: Settings,
    wanted_order_refs: Iterable[str] | None = None,
) -> list[OpenOrderSnapshot]:
    """Return terminal POMA API orders from IBKR completed-order history.

    ``reqCompletedOrders(apiOnly=True)`` is the authority that an API order is completed. A
    follow-up ``reqExecutions`` hydrates fill details when IBKR still exposes them. Exact POMA
    ``orderRef`` matching keeps this history lookup from adopting unrelated/manual orders.
    """
    wanted = set(wanted_order_refs or ())
    ib = _connect_ib(settings, client_id=settings.ibkr_client_id)
    try:
        completed = list(ib.reqCompletedOrders(apiOnly=True))
        try:
            ib.reqExecutions()
        except Exception:  # noqa: BLE001 - terminal completed-order evidence remains usable without fill history
            pass

        hydrated_by_perm_id = {
            int(getattr(trade.order, "permId", 0) or 0): trade
            for trade in ib.trades()
            if int(getattr(trade.order, "permId", 0) or 0) > 0
        }

        snapshots: list[OpenOrderSnapshot] = []
        for completed_trade in completed:
            order = completed_trade.order
            order_ref = str(getattr(order, "orderRef", "") or "")
            if not order_ref.startswith(f"{ORDER_REF_PREFIX}:"):
                continue
            if wanted and order_ref not in wanted:
                continue
            account = str(getattr(order, "account", "") or "")
            if settings.ibkr_account and account not in ("", settings.ibkr_account):
                continue

            perm_id = int(getattr(order, "permId", 0) or 0)
            trade = hydrated_by_perm_id.get(perm_id, completed_trade)
            status = str(getattr(trade.orderStatus, "status", "") or "")
            if not status:
                status = str(getattr(completed_trade.orderStatus, "status", "") or "")
            if status not in _TERMINAL_COMPLETED_STATUSES:
                continue

            action = str(getattr(order, "action", "") or "").upper()
            if action not in {OrderSide.BUY.value, OrderSide.SELL.value}:
                continue

            total_quantity = float(getattr(order, "totalQuantity", 0.0) or 0.0)
            filled = float(trade.filled() or 0.0)
            if status == "Filled" and filled <= 1e-9:
                # The completedOrder callback itself does not carry fill quantities. A Filled
                # terminal status still proves the full submitted quantity executed even when
                # execution history is unavailable by the time reconciliation runs.
                filled = total_quantity
            remaining = max(total_quantity - filled, 0.0)
            snapshots.append(
                OpenOrderSnapshot(
                    order_ref=order_ref,
                    order_id=_optional_positive_int(getattr(order, "orderId", None)),
                    perm_id=_optional_positive_int(getattr(order, "permId", None)),
                    ticker=str(getattr(trade.contract, "symbol", "") or "").upper(),
                    side=OrderSide(action),
                    raw_status=status,
                    filled=filled,
                    remaining=remaining,
                    avg_fill_price=_weighted_average_fill_price(trade),
                )
            )
        return snapshots
    finally:
        ib.disconnect()


def _weighted_average_fill_price(trade: object) -> float | None:
    total_quantity = 0.0
    total_notional = 0.0
    for fill in getattr(trade, "fills", ()) or ():
        execution = getattr(fill, "execution", None)
        if execution is None:
            continue
        shares = float(getattr(execution, "shares", 0.0) or 0.0)
        price = float(getattr(execution, "price", 0.0) or 0.0)
        if shares <= 0 or price <= 0:
            continue
        total_quantity += shares
        total_notional += shares * price
    if total_quantity <= 1e-9:
        return None
    return total_notional / total_quantity


def _optional_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
