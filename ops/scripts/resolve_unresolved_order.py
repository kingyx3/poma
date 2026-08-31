#!/usr/bin/env python3
"""Guarded operator repair for one stale POMA order in paper trading.

This command does not submit, cancel, or replace broker orders. It only changes the durable
local order ledger after re-validating a tightly scoped set of retained evidence. The repair is
intentionally limited to marking an UNKNOWN SELL as filled in paper mode; it never clears the
rebalance session marker or starts a new rebalance.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from poma.broker import build_broker
from poma.config import TradingMode, get_settings
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState
from poma.order_store import OrderStore

_CONFIRMATION = "RESOLVE FILLED"
_ZERO_TOLERANCE = 1e-9


def _position_quantity(snapshot: object, ticker: str) -> float:
    ticker = ticker.upper()
    for position in getattr(snapshot, "positions", ()) or ():
        if str(getattr(position, "ticker", "")).upper() == ticker:
            return float(getattr(position, "quantity", 0.0) or 0.0)
    return 0.0


def _retained_post_submission_quantity(state_dir: Path, entry: OrderLedgerEntry) -> float | None:
    path = state_dir / "reconciliations" / f"{entry.run_id}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    snapshot = payload.get("post_trade_account_snapshot") or {}
    for position in snapshot.get("positions", []) or []:
        if str(position.get("ticker", "")).upper() == entry.ticker.upper():
            return float(position.get("quantity", 0.0) or 0.0)
    return 0.0


def _event_evidence(store: OrderStore, entry: OrderLedgerEntry) -> dict[str, object]:
    accepted_seen = False
    perm_id_seen = False
    later_poma_broker_activity: list[dict[str, object]] = []
    if not store.events_path.exists():
        return {
            "accepted_seen": False,
            "perm_id_seen": False,
            "later_poma_broker_activity": [],
        }

    cutoff = entry.last_status_at or ""
    for line_number, raw_line in enumerate(store.events_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if payload.get("ledger_key") == entry.ledger_key:
            lifecycle = str(payload.get("lifecycle_state", ""))
            accepted_seen = accepted_seen or lifecycle in {
                OrderLifecycleState.BROKER_ACCEPTED.value,
                OrderLifecycleState.PARTIALLY_FILLED.value,
                OrderLifecycleState.SUBMITTED.value,
            }
            perm_id_seen = perm_id_seen or int(payload.get("perm_id") or 0) == int(entry.perm_id or 0)
            continue

        if str(payload.get("ticker", "")).upper() != entry.ticker.upper():
            continue
        event_time = str(payload.get("last_status_at") or payload.get("submitted_at") or "")
        if cutoff and event_time and event_time <= cutoff:
            continue
        lifecycle = str(payload.get("lifecycle_state", ""))
        broker_identity = int(payload.get("order_id") or 0) > 0 or int(payload.get("perm_id") or 0) > 0
        if broker_identity or lifecycle in {
            OrderLifecycleState.SUBMITTED.value,
            OrderLifecycleState.BROKER_ACCEPTED.value,
            OrderLifecycleState.PARTIALLY_FILLED.value,
            OrderLifecycleState.FILLED.value,
        }:
            later_poma_broker_activity.append(
                {
                    "event_line": line_number,
                    "ledger_key": payload.get("ledger_key"),
                    "side": payload.get("side"),
                    "lifecycle_state": lifecycle,
                    "order_id": payload.get("order_id"),
                    "perm_id": payload.get("perm_id"),
                    "last_status_at": payload.get("last_status_at"),
                }
            )

    return {
        "accepted_seen": accepted_seen,
        "perm_id_seen": perm_id_seen,
        "later_poma_broker_activity": later_poma_broker_activity,
    }


def _operator_resolved_entry(entry: OrderLedgerEntry, *, reason: str, resolved_at: str) -> OrderLedgerEntry:
    return replace(
        entry,
        lifecycle_state=OrderLifecycleState.FILLED,
        raw_status="OperatorResolvedFilled",
        filled_qty=entry.quantity,
        remaining_qty=0.0,
        last_status_at=resolved_at,
        terminal_reason=reason,
    )


def _backup_open_snapshot(store: OrderStore, resolved_at: str) -> Path:
    backup_dir = store.orders_dir / "operator_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = resolved_at.replace(":", "").replace("+00:00", "Z")
    destination = backup_dir / f"open_orders-before-{suffix}.jsonl"
    if store.open_orders_path.exists():
        shutil.copy2(store.open_orders_path, destination)
    else:
        destination.write_text("")
    return destination


def _append_resolution_audit(store: OrderStore, payload: dict[str, object]) -> Path:
    path = store.orders_dir / "operator_resolutions.jsonl"
    with path.open("a") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-ref", required=True)
    parser.add_argument("--perm-id", required=True, type=int)
    parser.add_argument("--expected-current-quantity", required=True, type=float)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--actor", default="unknown")
    parser.add_argument("--workflow-run-id", default="unknown")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.confirmation != _CONFIRMATION:
        raise SystemExit(f"confirmation must exactly equal {_CONFIRMATION!r}")
    if not args.apply:
        raise SystemExit("refusing to mutate without --apply")

    settings = get_settings()
    if settings.trading_mode != TradingMode.PAPER:
        raise SystemExit(
            f"operator resolution is intentionally limited to paper mode; current mode={settings.trading_mode}"
        )

    store = OrderStore(settings.state_dir)
    matches = [entry for entry in store.load_open_orders() if entry.order_ref == args.order_ref]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one open ledger row for order_ref={args.order_ref!r}; found {len(matches)}")
    entry = matches[0]

    if entry.lifecycle_state != OrderLifecycleState.UNKNOWN:
        raise SystemExit(f"ledger row must be UNKNOWN; found {entry.lifecycle_state.value}")
    if entry.side.value != "SELL":
        raise SystemExit(f"this guarded repair only supports SELL orders; found {entry.side.value}")
    if entry.perm_id != args.perm_id:
        raise SystemExit(f"permId mismatch: ledger={entry.perm_id} requested={args.perm_id}")
    if entry.quantity <= 0 or entry.filled_qty > _ZERO_TOLERANCE:
        raise SystemExit(
            f"expected positive unfilled quantity; quantity={entry.quantity} filled_qty={entry.filled_qty}"
        )

    broker = build_broker(settings)
    open_snapshots = list(broker.fetch_open_order_snapshots())
    if any(snapshot.order_ref == entry.order_ref for snapshot in open_snapshots):
        raise SystemExit("exact orderRef is currently open at IBKR; refusing operator resolution")
    if entry.perm_id and any(snapshot.perm_id == entry.perm_id for snapshot in open_snapshots):
        raise SystemExit("ledger permId is currently open at IBKR; refusing operator resolution")

    account_snapshot = broker.account_snapshot()
    current_quantity = _position_quantity(account_snapshot, entry.ticker)
    if abs(current_quantity - args.expected_current_quantity) > _ZERO_TOLERANCE:
        raise SystemExit(
            f"current {entry.ticker} quantity changed: expected={args.expected_current_quantity} actual={current_quantity}"
        )

    retained_quantity = _retained_post_submission_quantity(settings.state_dir, entry)
    if retained_quantity is None:
        raise SystemExit("missing retained post-submission reconciliation snapshot for the original run")
    if retained_quantity + _ZERO_TOLERANCE < entry.quantity:
        raise SystemExit(
            f"retained post-submission {entry.ticker} quantity={retained_quantity} is below order quantity={entry.quantity}"
        )
    if retained_quantity - current_quantity + _ZERO_TOLERANCE < entry.quantity:
        raise SystemExit(
            "retained-to-current position drop is smaller than the unresolved SELL quantity; evidence is not sufficient"
        )

    event_evidence = _event_evidence(store, entry)
    if not event_evidence["accepted_seen"]:
        raise SystemExit("local event history does not prove broker acceptance; refusing repair")
    if entry.perm_id and not event_evidence["perm_id_seen"]:
        raise SystemExit("local event history does not contain the requested permId; refusing repair")
    if event_evidence["later_poma_broker_activity"]:
        raise SystemExit(
            "later POMA broker activity exists for this ticker; refusing automatic inference: "
            + json.dumps(event_evidence["later_poma_broker_activity"], sort_keys=True)
        )

    resolved_at = datetime.now(UTC).isoformat()
    reason = (
        "operator-resolved filled from retained evidence after IBKR history aged out: "
        f"order_ref={entry.order_ref}; perm_id={entry.perm_id}; "
        f"post_submission_{entry.ticker}_qty={retained_quantity:g}; current_{entry.ticker}_qty={current_quantity:g}; "
        f"actor={args.actor}; workflow_run_id={args.workflow_run_id}. "
        "No fill price was inferred. Rebalance session state was not cleared."
    )
    resolved = _operator_resolved_entry(entry, reason=reason, resolved_at=resolved_at)
    backup_path = _backup_open_snapshot(store, resolved_at)
    store.upsert(resolved)
    audit_path = _append_resolution_audit(
        store,
        {
            "resolved_at": resolved_at,
            "actor": args.actor,
            "workflow_run_id": args.workflow_run_id,
            "action": "resolve_filled",
            "order_ref": entry.order_ref,
            "perm_id": entry.perm_id,
            "ticker": entry.ticker,
            "side": entry.side.value,
            "quantity": entry.quantity,
            "retained_post_submission_quantity": retained_quantity,
            "current_broker_quantity": current_quantity,
            "event_evidence": event_evidence,
            "backup_path": str(backup_path),
        },
    )

    print("operator_resolution=applied")
    print(f"order_ref={entry.order_ref}")
    print(f"perm_id={entry.perm_id}")
    print(f"ticker={entry.ticker}")
    print(f"resolved_lifecycle={resolved.lifecycle_state.value}")
    print(f"filled_qty={resolved.filled_qty:g}")
    print(f"avg_fill_price={resolved.avg_fill_price}")
    print(f"backup_path={backup_path}")
    print(f"audit_path={audit_path}")
    print("rebalance_session_state=unchanged")
    print("broker_orders_submitted=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
