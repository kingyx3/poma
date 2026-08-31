#!/usr/bin/env python3
"""Read-only diagnostics for unresolved durable POMA orders.

Print unresolved ledger records and exact broker-side open/completed-history matches. This
script never submits, modifies, cancels, replaces, or persists an order.
"""

from __future__ import annotations

import json
from typing import Any

from poma.broker import IbkrBroker, build_broker
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

    print(f"broker_open_count={len(broker_open)}")
    print(f"broker_completed_count={len(broker_completed)}")

    for index, entry in enumerate(unresolved, start=1):
        print()
        print(f"===== unresolved #{index} =====")
        print(json.dumps(_entry_json(entry), indent=2, sort_keys=True, default=str))

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

        print("broker_open_exact_order_ref_matches=")
        print(json.dumps(open_matches, indent=2, sort_keys=True, default=str))
        print("broker_completed_exact_order_ref_matches=")
        print(json.dumps(completed_matches, indent=2, sort_keys=True, default=str))

        if not open_matches and not completed_matches:
            print(
                "diagnosis=no exact orderRef evidence in current open or terminal completed "
                "history; keep UNKNOWN/fail-closed"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
