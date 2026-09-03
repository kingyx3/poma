#!/usr/bin/env python3
"""Read-only diagnostics for unresolved durable POMA orders.

Print unresolved ledger records, local lifecycle history, exact POMA orderRef matches, raw IBKR
completed/execution evidence correlated by broker permId, and retained account-position evidence.
This script never submits, modifies, cancels, replaces, reconciles, or persists an order.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
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


def _normalize_side(value: object) -> str:
    side = str(value or "").upper()
    return {"BOT": "BUY", "SLD": "SELL"}.get(side, side)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _ticker_event_summary(store: OrderStore, ticker: str, session_date: str) -> list[dict[str, object]]:
    """Summarize all locally recorded trades for this ticker from the unresolved session onward."""
    if not store.events_path.exists():
        return []
    grouped: dict[str, dict[str, object]] = {}
    for line_number, raw_line in enumerate(store.events_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("ticker", "")).upper() != ticker.upper():
            continue
        if str(payload.get("session_date", "")) < session_date:
            continue
        ledger_key = str(payload.get("ledger_key", ""))
        if not ledger_key:
            continue
        summary = grouped.setdefault(
            ledger_key,
            {
                "ledger_key": ledger_key,
                "first_event_line": line_number,
                "last_event_line": line_number,
            },
        )
        summary.update(
            {
                "last_event_line": line_number,
                "run_id": payload.get("run_id"),
                "session_date": payload.get("session_date"),
                "side": payload.get("side"),
                "quantity": payload.get("quantity"),
                "lifecycle_state": payload.get("lifecycle_state"),
                "raw_status": payload.get("raw_status"),
                "filled_qty": payload.get("filled_qty"),
                "remaining_qty": payload.get("remaining_qty"),
                "order_id": payload.get("order_id"),
                "perm_id": payload.get("perm_id"),
                "last_status_at": payload.get("last_status_at"),
            }
        )
    return sorted(grouped.values(), key=lambda row: (str(row.get("session_date", "")), int(row["first_event_line"])))


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
        "side": _normalize_side(getattr(order, "action", "")),
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
        "side": _normalize_side(getattr(execution, "side", "")),
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
        and _normalize_side(row.get("side")) == entry.side.value
        and (entry.perm_id is None or _positive_int(row.get("perm_id")) == entry.perm_id)
    ]
    shares = sum(float(row.get("shares", 0.0) or 0.0) for row in same_trade)
    return {
        "matching_execution_count": len(same_trade),
        "matching_shares": shares,
        "ledger_quantity": entry.quantity,
        "full_quantity_observed": shares >= entry.quantity - 1e-9,
    }


def _position_from_snapshot(snapshot: object, ticker: str) -> dict[str, object] | None:
    if not isinstance(snapshot, dict):
        return None
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        return None
    for position in positions:
        if not isinstance(position, dict):
            continue
        if str(position.get("ticker", "")).upper() == ticker.upper():
            return {
                "ticker": ticker.upper(),
                "quantity": position.get("quantity"),
                "market_value": position.get("market_value"),
            }
    return {"ticker": ticker.upper(), "quantity": 0.0, "market_value": 0.0}


def _run_journal_evidence(store: OrderStore, entry: OrderLedgerEntry) -> dict[str, object]:
    planned_path = store.orders_dir / f"{entry.run_id}.json"
    reconciliation_path = store.orders_dir.parent / "reconciliations" / f"{entry.run_id}.json"
    planned = _read_json(planned_path)
    reconciliation = _read_json(reconciliation_path)
    return {
        "planned_journal_path": str(planned_path),
        "planned_journal": planned,
        "reconciliation_path": str(reconciliation_path),
        "reconciliation": reconciliation,
        "planned_ry_position": _position_from_snapshot(
            planned.get("broker_account_snapshot") if planned else None,
            entry.ticker,
        ),
        "post_submission_ry_position": _position_from_snapshot(
            reconciliation.get("post_trade_account_snapshot") if reconciliation else None,
            entry.ticker,
        ),
    }


def _report_position_timeline(report_dir: Path, entry: OrderLedgerEntry) -> list[dict[str, object]]:
    """Read retained broker snapshots for this ticker from the unresolved session onward."""
    rows: list[dict[str, object]] = []
    if not report_dir.exists():
        return rows
    for path in sorted(report_dir.glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        session = str(payload.get("session_date", ""))
        if session < entry.session_date:
            continue
        snapshot = payload.get("broker_account_snapshot")
        position = _position_from_snapshot(snapshot, entry.ticker)
        ticker_trades = [
            trade
            for trade in payload.get("trades", [])
            if isinstance(trade, dict) and str(trade.get("ticker", "")).upper() == entry.ticker.upper()
        ]
        ticker_results = [
            result
            for result in payload.get("execution_results", [])
            if isinstance(result, dict) and str(result.get("ticker", "")).upper() == entry.ticker.upper()
        ]
        rows.append(
            {
                "path": str(path),
                "run_id": payload.get("run_id"),
                "session_date": session,
                "snapshot_timestamp_utc": snapshot.get("timestamp_utc") if isinstance(snapshot, dict) else None,
                "position": position,
                "trades": ticker_trades,
                "execution_results": ticker_results,
            }
        )
    return rows


def _current_position(broker: Any, ticker: str) -> dict[str, object] | None:
    account_snapshot = getattr(broker, "account_snapshot", None)
    if not callable(account_snapshot):
        return None
    snapshot = account_snapshot()
    positions = getattr(snapshot, "positions", ()) or ()
    for position in positions:
        if str(getattr(position, "ticker", "")).upper() == ticker.upper():
            return {
                "ticker": ticker.upper(),
                "quantity": getattr(position, "quantity", None),
                "market_value": getattr(position, "market_value", None),
                "snapshot_timestamp_utc": getattr(snapshot, "timestamp_utc", None),
            }
    return {
        "ticker": ticker.upper(),
        "quantity": 0.0,
        "market_value": 0.0,
        "snapshot_timestamp_utc": getattr(snapshot, "timestamp_utc", None),
    }


def _host_log_matches(entry: OrderLedgerEntry, host_logs_dir: Path = Path("/host-logs")) -> list[dict[str, object]]:
    """Search retained text logs for exact broker/order identities; no broad ticker-only adoption."""
    if not host_logs_dir.exists():
        return []
    needles = [entry.order_ref]
    if entry.perm_id:
        needles.append(str(entry.perm_id))
    matches: list[dict[str, object]] = []
    for path in sorted(host_logs_dir.glob("*.log")):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if any(needle and needle in line for needle in needles):
                matches.append({"path": str(path), "line": line_number, "text": line})
                if len(matches) >= 200:
                    return matches
    return matches


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
        print("all_ticker_ledger_summary_since_session=")
        print(
            json.dumps(
                _ticker_event_summary(store, entry.ticker, entry.session_date),
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        print("original_run_journal_evidence=")
        print(json.dumps(_run_journal_evidence(store, entry), indent=2, sort_keys=True, default=str))
        print("retained_report_position_timeline=")
        print(json.dumps(_report_position_timeline(settings.report_dir, entry), indent=2, sort_keys=True, default=str))
        try:
            current_position = _current_position(broker, entry.ticker)
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            current_position = {"error": f"{type(exc).__name__}: {exc}"}
        print("current_broker_position=")
        print(json.dumps(current_position, indent=2, sort_keys=True, default=str))
        print("host_log_exact_identity_matches=")
        print(json.dumps(_host_log_matches(entry), indent=2, sort_keys=True, default=str))

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
                "diagnosis=current IBKR APIs have aged out the exact orderRef/permId evidence; use the retained local "
                "journal/position timeline above for operator review and keep UNKNOWN unless that evidence is "
                "conclusive"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
