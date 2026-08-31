#!/usr/bin/env python3
"""Read-only diagnostics for unresolved durable POMA orders.

Print unresolved ledger records, local lifecycle history, exact POMA orderRef matches, and raw
IBKR completed/execution evidence correlated by broker permId. This script never submits,
modifies, cancels, replaces, reconciles, or persists an order.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from poma.broker import IbkrBroker, _connect_ib, build_broker
from poma.config import get_settings
from poma.ibkr_order_history import (
    fetch_completed_order_snapshots as fetch_ibkr_completed_order_snapshots,
)
from poma.models import OpenOrderSnapshot
from poma.order_lifecycle import OrderLedgerEntry
from poma.order_store import OrderStore


def _entry_json(entry: OrderLedgerEntry) -> dict[str, object]:
    return entry.to_json()


def _snapshot_json(snapshot: OpenOrderSnapshot) -> dict[str, object]:
    return {
        "order_ref": snapshot.order_ref,
        "order_id": snapshot.order_id,
        "perm_id": snapshot.perm_id,
        "ticker": snapshot.ticker,
        "side": snapshot.side.value,
        "raw_status": snapshot.raw_status,
        "filled": snapshot.filled,
        "remaining": snapshot.remaining,
        "avg_fill_price": snapshot.avg_fill_price,
    }


def _identity_match(entry: OrderLedgerEntry, snapshot: OpenOrderSnapshot) -> bool:
    """Use the same primary identity as reconciliation: exact POMA orderRef."""
    return bool(entry.order_ref and snapshot.order_ref == entry.order_ref)


def _completed_snapshots(
    broker: Any,
    settings: Any,
    wanted_order_refs: set[str],
) -> list[OpenOrderSnapshot]:
    fetch_completed = getattr(broker, "fetch_completed_order_snapshots", None)
    if callable(fetch_completed):
        return list(fetch_completed())
    if isinstance(broker, IbkrBroker):
        return fetch_ibkr_completed_order_snapshots(settings, wanted_order_refs)
    return []


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _local_events(store: OrderStore, wanted_ledger_keys: set[str]) -> dict[str, list[dict[str, object]]]:
    """Return append-only lifecycle events for the unresolved ledger keys without modifying them."""
    events: dict[str, list[dict[str, object]]] = defaultdict(list)
    if not store.events_path.exists():
        return events
    for line_number, raw_line in enumerate(store.events_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"LOCAL_EVENT_PARSE_WARNING line={line_number}: {exc}")
            continue
        ledger_key = str(payload.get("ledger_key", ""))
        if ledger_key in wanted_ledger_keys:
            event = dict(payload)
            event["_event_line"] = line_number
            events[ledger_key].append(event)
    return events


def _raw_completed_json(trade: object) -> dict[str, object]:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    return {
        "order_ref": str(getattr(order, "orderRef", "") or ""),
        "order_id": _positive_int(getattr(order, "orderId", None)),
        "perm_id": _positive_int(getattr(order, "permId", None)),
        "account": str(getattr(order, "account", "") or ""),
        "ticker": str(getattr(contract, "symbol", "") or "").upper(),
        "side": str(getattr(order, "action", "") or "").upper(),
        "quantity": float(getattr(order, "totalQuantity", 0.0) or 0.0),
        "status": str(getattr(status, "status", "") or ""),
        "filled": float(trade.filled() or 0.0) if hasattr(trade, "filled") else 0.0,
        "remaining": float(trade.remaining() or 0.0) if hasattr(trade, "remaining") else None,
    }


def _raw_execution_json(fill: object) -> dict[str, object]:
    execution = getattr(fill, "execution", None)
    contract = getattr(fill, "contract", None)
    time_value = getattr(fill, "time", None) or getattr(execution, "time", None)
    return {
        "exec_id": str(getattr(execution, "execId", "") or ""),
        "time": str(time_value or ""),
        "account": str(getattr(execution, "acctNumber", "") or ""),
        "ticker": str(getattr(contract, "symbol", "") or "").upper(),
        "side": str(getattr(execution, "side", "") or "").upper(),
        "shares": float(getattr(execution, "shares", 0.0) or 0.0),
        "price": float(getattr(execution, "price", 0.0) or 0.0),
        "order_id": _positive_int(getattr(execution, "orderId", None)),
        "perm_id": _positive_int(getattr(execution, "permId", None)),
        "client_id": _positive_int(getattr(execution, "clientId", None)),
        "exchange": str(getattr(execution, "exchange", "") or ""),
    }


def _fetch_raw_ibkr_evidence(settings: Any) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Read unfiltered completed orders and executions from IBKR for permId correlation."""
    ib = _connect_ib(settings, client_id=settings.ibkr_client_id)
    try:
        completed = [_raw_completed_json(trade) for trade in ib.reqCompletedOrders(apiOnly=True)]
        executions = [_raw_execution_json(fill) for fill in ib.reqExecutions()]
        return completed, executions
    finally:
        ib.disconnect()


def _perm_id_matches(rows: list[dict[str, object]], perm_id: int | None) -> list[dict[str, object]]:
    if perm_id is None:
        return []
    return [row for row in rows if _positive_int(row.get("perm_id")) == perm_id]


def _execution_summary(entry: OrderLedgerEntry, executions: list[dict[str, object]]) -> dict[str, object]:
    same_trade = [
        row
        for row in executions
        if str(row.get("ticker", "")).upper() == entry.ticker.upper()
        and str(row.get("side", "")).upper() == entry.side.value
        and (not getattr(entry, "perm_id", None) or _positive_int(row.get("perm_id")) == entry.perm_id)
    ]
    shares = sum(float(row.get("shares", 0.0) or 0.0) for row in same_trade)
    return {
        "matching_execution_count": len(same_trade),
        "matching_shares": shares,
        "ledger_quantity": entry.quantity,
        "full_quantity_observed": shares >= entry.quantity - 1e-9,
    }


def main() -> int:
    settings = get_settings()
    store = OrderStore(settings.state_dir)
    unresolved = store.load_open_orders()

    print(f"state_dir={settings.state_dir}")
    print(f"trading_mode={settings.trading_mode}")
    print(f"unresolved_count={len(unresolved)}")
    if not unresolved:
        return 0

    wanted_order_refs = {entry.order_ref for entry in unresolved if entry.order_ref}
    wanted_ledger_keys = {entry.ledger_key for entry in unresolved}
    events_by_key = _local_events(store, wanted_ledger_keys)
    broker = build_broker(settings)
    print(f"broker_type={type(broker).__name__}")

    try:
        broker_open = list(broker.fetch_open_order_snapshots())
    except Exception as exc:  # noqa: BLE001 - preserve ledger output if IBKR read fails
        print(f"OPEN_ORDER_QUERY_ERROR={type(exc).__name__}: {exc}")
        broker_open = []

    try:
        broker_completed = _completed_snapshots(broker, settings, wanted_order_refs)
    except Exception as exc:  # noqa: BLE001 - preserve ledger output if history read fails
        print(f"COMPLETED_ORDER_QUERY_ERROR={type(exc).__name__}: {exc}")
        broker_completed = []

    raw_completed: list[dict[str, object]] = []
    raw_executions: list[dict[str, object]] = []
    if isinstance(broker, IbkrBroker):
        try:
            raw_completed, raw_executions = _fetch_raw_ibkr_evidence(settings)
        except Exception as exc:  # noqa: BLE001 - diagnostics remain useful with local evidence only
            print(f"RAW_IBKR_EVIDENCE_QUERY_ERROR={type(exc).__name__}: {exc}")

    print(f"broker_open_count={len(broker_open)}")
    print(f"broker_completed_count={len(broker_completed)}")
    print(f"raw_completed_count={len(raw_completed)}")
    print(f"raw_execution_count={len(raw_executions)}")

    for index, entry in enumerate(unresolved, start=1):
        print()
        print(f"===== unresolved #{index} =====")
        print(json.dumps(_entry_json(entry), indent=2, sort_keys=True, default=str))

        print("local_order_events=")
        print(json.dumps(events_by_key.get(entry.ledger_key, []), indent=2, sort_keys=True, default=str))

        open_matches = [
            _snapshot_json(snapshot)
            for snapshot in broker_open
            if _identity_match(entry, snapshot)
        ]
        completed_matches = [
            _snapshot_json(snapshot)
            for snapshot in broker_completed
            if _identity_match(entry, snapshot)
        ]
        raw_completed_perm_matches = _perm_id_matches(raw_completed, entry.perm_id)
        raw_execution_perm_matches = _perm_id_matches(raw_executions, entry.perm_id)

        print("broker_open_exact_order_ref_matches=")
        print(json.dumps(open_matches, indent=2, sort_keys=True, default=str))
        print("broker_completed_exact_order_ref_matches=")
        print(json.dumps(completed_matches, indent=2, sort_keys=True, default=str))
        print("raw_completed_perm_id_matches=")
        print(json.dumps(raw_completed_perm_matches, indent=2, sort_keys=True, default=str))
        print("raw_execution_perm_id_matches=")
        print(json.dumps(raw_execution_perm_matches, indent=2, sort_keys=True, default=str))
        print("raw_execution_evidence_summary=")
        print(json.dumps(_execution_summary(entry, raw_execution_perm_matches), indent=2, sort_keys=True))

        if open_matches:
            print("diagnosis=exact orderRef is currently open at IBKR; keep unresolved")
        elif completed_matches:
            print("diagnosis=exact terminal orderRef evidence is available to normal reconciliation")
        elif raw_execution_perm_matches:
            print(
                "diagnosis=broker execution evidence exists for the ledger permId; inspect shares/side/ticker/account "
                "before any terminal ledger repair"
            )
        elif raw_completed_perm_matches:
            print(
                "diagnosis=raw broker completed-order evidence exists for the ledger permId but was not adopted by "
                "exact-orderRef reconciliation; inspect identity/status before any terminal ledger repair"
            )
        else:
            print(
                "diagnosis=no exact orderRef or permId evidence in currently available IBKR open/completed/execution "
                "history; keep UNKNOWN/fail-closed"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
