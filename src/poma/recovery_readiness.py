"""Fail-closed broker recovery audit after an interrupted scheduled reconciliation.

The VM cron incident state doubles as a persistent execution gate. Only a successful
read-only recovery audit allows the cron reporter to clear an active incident.
No order placement/cancellation is performed by this module.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from poma.broker import BrokerUnavailable
from poma.ibkr_order_history import fetch_completed_order_snapshots
from poma.order_lifecycle import OrderLifecycleState


def assert_trading_recovery_ready(state_dir: Path) -> None:
    """Block a new paper/live plan while scheduled broker verification is unresolved.

    Missing state is normal on a fresh deployment. Existing but malformed state is
    *not* evidence that a previous failure was recovered.
    """
    path = Path(state_dir) / "cron-alert-reconcile-orders.json"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise BrokerUnavailable(
            "reconcile incident state unreadable; block new trading until investigated"
        ) from exc
    if not isinstance(raw, dict) or type(raw.get("active")) is not bool:
        raise BrokerUnavailable(
            "reconcile incident state invalid; block new trading until investigated"
        )
    if raw["active"]:
        raise BrokerUnavailable(
            "scheduled broker reconciliation incident still active; "
            "new trading blocked pending authenticated recovery audit"
        )


def verify_broker_recovery(broker, store, settings, summary) -> str:
    """Probe account/positions and recent completed orders after open-order reconciliation.

    IBKR history is time-bounded: this checks *available* recent API completions,
    not a lifetime guarantee that no old order was ever placed.
    """
    if any(u.action in {"error", "unverified"} for u in summary.updates):
        raise BrokerUnavailable("broker recovery audit failed: unresolved order updates")
    entries = store.load_latest_entries()
    if any(
        e.lifecycle_state == OrderLifecycleState.UNKNOWN or e.raw_status == "NotOpenUnverified"
        for e in entries.values() if not e.is_terminal
    ):
        raise BrokerUnavailable("broker recovery audit failed: unknown local order state")

    try:
        snapshot = broker.account_snapshot()
    except Exception as exc:
        raise BrokerUnavailable(
            "broker recovery audit failed: authenticated cash/positions snapshot unavailable"
        ) from exc
    if (
        snapshot.account_id != settings.ibkr_account
        or not math.isfinite(snapshot.total_value_usd)
        or snapshot.total_value_usd <= 0
    ):
        raise BrokerUnavailable(
            "broker recovery audit failed: account identity or portfolio value not verified"
        )

    try:
        completed = fetch_completed_order_snapshots(settings)
    except Exception as exc:
        raise BrokerUnavailable(
            "broker recovery audit failed: completed-order/execution evidence unavailable"
        ) from exc

    known_roots = {
        re.sub(r":r[0-9]+$", "", entry.ledger_key): entry
        for entry in entries.values()
    }
    for order in completed:
        root = re.sub(r":r[0-9]+$", "", order.order_ref)
        recorded = known_roots.get(root)
        if recorded is None:
            raise BrokerUnavailable(
                "broker recovery audit found a recent completed POMA order absent "
                "from the durable ledger; manual investigation required"
            )
        if recorded.ticker != order.ticker or recorded.side != order.side:
            raise BrokerUnavailable(
                "broker recovery audit found a completed order identity conflict"
            )
        if order.order_ref == recorded.order_ref:
            if recorded.lifecycle_state == OrderLifecycleState.UNKNOWN:
                raise BrokerUnavailable("broker recovery audit found an unknown completed order")
            if order.raw_status == "Filled" and (
                recorded.is_terminal
                and recorded.lifecycle_state != OrderLifecycleState.FILLED
            ):
                raise BrokerUnavailable(
                    "broker recovery audit found a completed fill/ledger status conflict"
                )
            if not recorded.is_terminal:
                raise BrokerUnavailable(
                    "broker recovery audit found a completed order still recorded as working"
                )

    return (
        "Broker recovery audit passed: open POMA orders, authenticated account cash/positions "
        "and available recent completed orders/executions checked. "
        "Historical broker order finality beyond IBKR retention is not certified."
    )
