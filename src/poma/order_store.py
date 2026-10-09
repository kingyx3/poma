from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from poma.order_lifecycle import OrderLedgerEntry
from poma.persistence import atomic_write_text


class OrderStore:
    """Durable order lifecycle ledger.

    ``open_orders.jsonl`` is a rewritten snapshot of every order not yet in a terminal state
    (one line per order, keyed by ``ledger_key``) so a fresh process can answer "what is still
    open"; the event log is authoritative for crash recovery. ``order_events.jsonl`` is a pure append log of every
    lifecycle transition ever recorded, kept for audit/debugging even after an order leaves
    the open snapshot.
    """

    def __init__(self, state_dir: Path) -> None:
        self.orders_dir = state_dir / "orders"
        self.open_orders_path = self.orders_dir / "open_orders.jsonl"
        self.events_path = self.orders_dir / "order_events.jsonl"
        self._cache: dict[str, OrderLedgerEntry] | None = None
        self._cache_signature: tuple | None = None

    def _signature(self) -> tuple:
        return tuple(
            (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None
            for path in (self.open_orders_path, self.events_path)
        )

    def load_open_orders(self) -> list[OrderLedgerEntry]:
        return [entry for entry in self._latest_entries().values() if not entry.is_terminal]

    def _latest_entries(self) -> dict[str, OrderLedgerEntry]:
        # The fsynced event log is authoritative after a crash between append and snapshot.
        # Read legacy snapshots too, so existing installations need no migration.
        signature = self._signature()
        if self._cache is not None and signature == self._cache_signature:
            return self._cache.copy()
        entries: dict[str, OrderLedgerEntry] = {}
        for path in (self.open_orders_path, self.events_path):
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                if line.strip():
                    entry = OrderLedgerEntry.from_json(json.loads(line))
                    entries[entry.ledger_key] = entry
        self._cache = entries.copy()
        self._cache_signature = signature
        return entries

    def get(self, ledger_key: str) -> OrderLedgerEntry | None:
        for entry in self.load_open_orders():
            if entry.ledger_key == ledger_key:
                return entry
        return None

    def get_latest_many(self, ledger_keys: Iterable[str]) -> dict[str, OrderLedgerEntry]:
        """Most recent recorded state for each key, terminal or not, in one pass per file.

        A terminal order is dropped from ``open_orders.jsonl`` by ``upsert``, so a same-run
        retry that needs to recognize an order which has *already reached a terminal state*
        (filled/cancelled/rejected) since it was submitted has to fall back to the append-only
        event log for those keys, rather than treating "not open" as "never submitted".
        """
        keys = set(ledger_keys)
        if not keys:
            return {}
        return {key: entry for key, entry in self._latest_entries().items() if key in keys}

    def get_latest_run_trades(self, run_id: str) -> dict[tuple[str, str], OrderLedgerEntry]:
        """Return the latest entry for each ticker/side previously planned in one run.

        Rebalance retries rebuild the residual plan from the latest account snapshot. A filled or
        otherwise removed trade can therefore disappear from the plan and shift the sequence
        number of the remaining trades. Reusing the original ledger key by ``(ticker, side)``
        keeps orderRef idempotency stable even when those sequence offsets change.
        """
        return {
            (entry.ticker, entry.side.value): entry
            for entry in self._latest_entries().values() if entry.run_id == run_id
        }

    def upsert(self, entry: OrderLedgerEntry) -> None:
        """Record a lifecycle transition; drop the order from the open snapshot once terminal."""
        entries = self._latest_entries()
        entries[entry.ledger_key] = entry
        self._append_event(entry)
        self._save_open_orders([value for value in entries.values() if not value.is_terminal])
        self._cache = entries
        self._cache_signature = self._signature()

    def _save_open_orders(self, entries: list[OrderLedgerEntry]) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(entry.to_json(), sort_keys=True) for entry in sorted(entries, key=lambda e: e.ledger_key)]
        content = "\n".join(lines)
        atomic_write_text(self.open_orders_path, f"{content}\n" if content else "")

    def _append_event(self, entry: OrderLedgerEntry) -> None:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a") as handle:
            handle.write(json.dumps(entry.to_json(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
