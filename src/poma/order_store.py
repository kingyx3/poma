from __future__ import annotations

import json
from pathlib import Path

from poma.order_lifecycle import OrderLedgerEntry


class OrderStore:
    """Durable order lifecycle ledger.

    ``open_orders.jsonl`` is a rewritten snapshot of every order not yet in a terminal state
    (one line per order, keyed by ``ledger_key``) so a fresh process can answer "what is still
    open" without replaying history. ``order_events.jsonl`` is a pure append log of every
    lifecycle transition ever recorded, kept for audit/debugging even after an order leaves
    the open snapshot.
    """

    def __init__(self, state_dir: Path) -> None:
        self.orders_dir = state_dir / "orders"
        self.open_orders_path = self.orders_dir / "open_orders.jsonl"
        self.events_path = self.orders_dir / "order_events.jsonl"

    def load_open_orders(self) -> list[OrderLedgerEntry]:
        if not self.open_orders_path.exists():
            return []
        entries = []
        for line in self.open_orders_path.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(OrderLedgerEntry.from_json(json.loads(line)))
        return entries

    def get(self, ledger_key: str) -> OrderLedgerEntry | None:
        for entry in self.load_open_orders():
            if entry.ledger_key == ledger_key:
                return entry
        return None

    def upsert(self, entry: OrderLedgerEntry) -> None:
        """Record a lifecycle transition; drop the order from the open snapshot once terminal."""
        entries = {existing.ledger_key: existing for existing in self.load_open_orders()}
        if entry.is_terminal:
            entries.pop(entry.ledger_key, None)
        else:
            entries[entry.ledger_key] = entry
        self._save_open_orders(list(entries.values()))
        self._append_event(entry)

    def _save_open_orders(self, entries: list[OrderLedgerEntry]) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(entry.to_json(), sort_keys=True) for entry in sorted(entries, key=lambda e: e.ledger_key)]
        content = "\n".join(lines)
        self.open_orders_path.write_text(f"{content}\n" if content else "")

    def _append_event(self, entry: OrderLedgerEntry) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a") as handle:
            handle.write(json.dumps(entry.to_json(), sort_keys=True) + "\n")
