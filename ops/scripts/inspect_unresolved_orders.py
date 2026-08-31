#!/usr/bin/env python3
"""Read-only diagnostics for unresolved durable POMA orders.

Prints unresolved ledger records and broker-side open/completed matches. This script never
submits, modifies, cancels, replaces, or persists an order.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from poma.broker import build_broker
from poma.config import get_settings
from poma.order_store import OrderStore


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"repr": repr(value)}


def _first_attr(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _read_unresolved(store: OrderStore) -> list[Any]:
    for method_name in ("open_orders", "list_open", "unresolved_orders"):
        method = getattr(store, method_name, None)
        if method is not None:
            return list(method())
    raise RuntimeError("OrderStore exposes no supported unresolved-order listing method")


def _broker_snapshot(broker: Any, *method_names: str) -> list[Any]:
    for method_name in method_names:
        method = getattr(broker, method_name, None)
        if method is None:
            continue
        return list(method())
    return []


def _matches(row: Any, candidate: Any) -> bool:
    order_id = _first_attr(row, "order_id", "broker_order_id", "orderId")
    perm_id = _first_attr(row, "perm_id", "permId")
    order_ref = _first_attr(row, "order_ref", "orderRef")
    ticker = _first_attr(row, "ticker", "symbol")
    side = _first_attr(row, "side", "action")

    candidate_order_id = _first_attr(candidate, "order_id", "broker_order_id", "orderId")
    candidate_perm_id = _first_attr(candidate, "perm_id", "permId")
    candidate_order_ref = _first_attr(candidate, "order_ref", "orderRef")
    candidate_ticker = _first_attr(candidate, "ticker", "symbol")
    candidate_side = _first_attr(candidate, "side", "action")

    if perm_id not in (None, 0, "") and candidate_perm_id == perm_id:
        return True
    if order_id not in (None, 0, "") and candidate_order_id == order_id:
        return True
    if order_ref and candidate_order_ref == order_ref:
        return True
    return bool(
        ticker
        and candidate_ticker == ticker
        and side
        and str(candidate_side).upper() == str(side).upper()
    )


def main() -> int:
    settings = get_settings()
    store = OrderStore(settings.state_dir)
    unresolved = _read_unresolved(store)

    print(f"state_dir={settings.state_dir}")
    print(f"unresolved_count={len(unresolved)}")
    if not unresolved:
        return 0

    broker = build_broker(settings)

    try:
        broker_open = _broker_snapshot(broker, "open_orders")
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve partial output
        print(f"OPEN_ORDER_QUERY_ERROR={type(exc).__name__}: {exc}")
        broker_open = []

    try:
        broker_completed = _broker_snapshot(
            broker,
            "completed_orders",
            "completed_order_history",
            "completed_orders_snapshot",
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must preserve partial output
        print(f"COMPLETED_ORDER_QUERY_ERROR={type(exc).__name__}: {exc}")
        broker_completed = []

    print(f"broker_open_count={len(broker_open)}")
    print(f"broker_completed_count={len(broker_completed)}")

    for index, row in enumerate(unresolved, start=1):
        print()
        print(f"===== unresolved #{index} =====")
        print(json.dumps(_as_dict(row), indent=2, sort_keys=True, default=str))

        open_matches = [_as_dict(item) for item in broker_open if _matches(row, item)]
        completed_matches = [_as_dict(item) for item in broker_completed if _matches(row, item)]

        print("broker_open_matches=")
        print(json.dumps(open_matches, indent=2, sort_keys=True, default=str))
        print("broker_completed_matches=")
        print(json.dumps(completed_matches, indent=2, sort_keys=True, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
