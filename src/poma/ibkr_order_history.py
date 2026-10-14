from __future__ import annotations

from collections.abc import Iterable
from contextlib import suppress
from math import isclose

from poma.broker import ORDER_REF_PREFIX, _connect_ib
from poma.config import Settings
from poma.models import OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry

_TERMINAL_COMPLETED_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


def fetch_completed_order_snapshots(
    settings: Settings,
    wanted_order_refs: Iterable[str] | None = None,
    *,
    unresolved_entries: Iterable[OrderLedgerEntry] = (),
) -> list[OpenOrderSnapshot]:
    """Return terminal POMA API orders from IBKR completed-order history.

    ``reqCompletedOrders(apiOnly=True)`` is the authority that an API order is completed. A
    follow-up ``reqExecutions`` hydrates fill details when IBKR still exposes them. Exact POMA
    ``orderRef`` matching keeps this history lookup from adopting unrelated/manual orders.
    If completed-order history misses an order, complete execution evidence for an exact
    durable permId may prove a full fill. Partial or unmatched executions never close an order.
    """
    wanted = set(wanted_order_refs or ())
    ib = _connect_ib(settings, client_id=settings.ibkr_client_id)
    try:
        # Completed-order history is not always available through a new API session. A full
        # set of executions may independently establish a fill even if this query fails.
        try:
            completed = list(ib.reqCompletedOrders(apiOnly=True))
        except Exception:  # noqa: BLE001 - remain fail-closed unless executions prove a full fill
            completed = []
        executions: list[object] = []
        with suppress(Exception):
            executions = list(ib.reqExecutions())

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
            # The hydrated trade cache can still say "Submitted" after the authoritative
            # completed-order callback says "Filled". Prefer the terminal completed status.
            completed_status = str(getattr(completed_trade.orderStatus, "status", "") or "")
            hydrated_status = str(getattr(trade.orderStatus, "status", "") or "")
            status = (
                completed_status if completed_status in _TERMINAL_COMPLETED_STATUSES else hydrated_status
            )
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
        snapshots.extend(_execution_proven_fills(unresolved_entries, executions, settings, snapshots))
        return snapshots
    finally:
        ib.disconnect()



def _execution_proven_fills(
    unresolved_entries: Iterable[OrderLedgerEntry],
    executions: list[object],
    settings: Settings,
    completed_snapshots: list[OpenOrderSnapshot],
) -> list[OpenOrderSnapshot]:
    """Infer Filled only from an exact full-quantity execution set for a durable broker permId.

    IBKR executions do not carry POMA's orderRef, so a broker-assigned permId is mandatory.
    Never infer cancellation or a terminal partial fill from absence in the open-order API.
    Replaced orders can have multiple permIds and need completed-order history instead.
    """
    completed_refs = {snapshot.order_ref for snapshot in completed_snapshots}
    fills_by_perm_id: dict[int, list[object]] = {}
    for fill in executions:
        execution = getattr(fill, "execution", None)
        perm_id = _optional_positive_int(getattr(execution, "permId", None))
        if perm_id is not None:
            fills_by_perm_id.setdefault(perm_id, []).append(fill)

    snapshots: list[OpenOrderSnapshot] = []
    for entry in unresolved_entries:
        if (
            not entry.perm_id
            or entry.replace_count
            or entry.replacement_order_ref
            or entry.order_ref in completed_refs
            or entry.quantity <= 0
        ):
            continue
        fills = fills_by_perm_id.get(entry.perm_id, [])
        if not fills:
            continue

        # Every execution on the permId must agree on instrument, side, and account.
        # If any identity is missing or contradictory, keep the order unresolved.
        expected_side = "BOT" if entry.side == OrderSide.BUY else "SLD"
        if any(
            str(getattr(getattr(fill, "contract", None), "symbol", "")).upper() != entry.ticker.upper()
            or str(getattr(fill.execution, "side", "")).upper() not in (expected_side, entry.side.value)
            or (
                settings.ibkr_account
                and str(getattr(fill.execution, "acctNumber", "")) != settings.ibkr_account
            )
            for fill in fills
        ):
            continue

        unique_fills: dict[str, object] = {}
        for fill in fills:
            execution = fill.execution
            exec_id = str(getattr(execution, "execId", "") or "")
            if not exec_id:
                unique_fills.clear()
                break
            unique_fills.setdefault(exec_id, fill)
        if not unique_fills:
            continue
        filled = sum(float(getattr(fill.execution, "shares", 0.0) or 0.0) for fill in unique_fills.values())
        if not isclose(filled, entry.quantity, rel_tol=0.0, abs_tol=1e-6):
            continue
        notional = sum(
            float(fill.execution.shares) * float(fill.execution.price) for fill in unique_fills.values()
        )
        if any(float(fill.execution.price) <= 0 for fill in unique_fills.values()):
            continue
        snapshots.append(
            OpenOrderSnapshot(
                order_ref=entry.order_ref,
                order_id=entry.order_id,
                perm_id=entry.perm_id,
                ticker=entry.ticker,
                side=entry.side,
                raw_status="Filled",
                filled=entry.quantity,
                remaining=0.0,
                avg_fill_price=notional / filled,
            )
        )
    return snapshots


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
