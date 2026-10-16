"""Audit executed POMA orders using actual IBKR execution/Flex report rows.

This is intentionally read-only. Missing separately reported fees stay unknown rather
than being guessed from commission schedules; quote-based slippage is an approximation.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from poma.order_lifecycle import OrderLedgerEntry


@dataclass(frozen=True)
class ExecutionFill:
    execution_id: str
    order_ref: str
    symbol: str
    side: str
    shares: float
    price_usd: float
    commission_usd: float | None
    exchange_fees_usd: float | None
    pricing_plan: str
    routing: str
    executed_at_utc: str


@dataclass(frozen=True)
class OrderExecutionCost:
    order_ref: str
    symbol: str
    side: str
    fills: int
    shares: float
    average_price_usd: float
    notional_usd: float
    commission_usd: float | None
    exchange_fees_usd: float | None
    reported_broker_fees_usd: float | None
    reported_fee_bps: float | None
    captured_quote_spread_bps: float | None
    signed_price_slippage_bps: float | None
    signed_price_slippage_usd: float | None
    pricing_plan: str
    routing: str
    ledger_matched: bool
    warning: str


_HEADERS = (
    "order_ref", "symbol", "side", "fills", "shares", "average_price_usd",
    "notional_usd", "commission_usd", "exchange_fees_usd",
    "reported_broker_fees_usd", "reported_fee_bps",
    "captured_quote_spread_bps", "signed_price_slippage_bps",
    "signed_price_slippage_usd", "pricing_plan", "routing", "ledger_matched",
    "warning",
)


def _column(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name, "")
        if value is not None and value.strip():
            return value.strip()
    return ""


def _number(raw: str, field: str, line: int, *, required: bool) -> float | None:
    if not raw:
        if required:
            raise ValueError(f"row {line}: {field} is required")
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"row {line}: invalid {field}: {raw!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"row {line}: {field} must be finite")
    return number


def read_execution_fills(path: Path) -> list[ExecutionFill]:
    """Read normalized IBKR execution data, preserving missing cost components as null.

    An export must contain true executed fills, not submitted orders or hypothetical fees.
    If the Flex report has different headers, map them to the documented CSV schema first.
    """
    fills: dict[str, ExecutionFill] = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"execution_id", "order_ref", "symbol", "side", "shares", "price_usd"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"execution CSV requires columns: {', '.join(sorted(required))}")
        for line, row in enumerate(reader, start=2):
            execution_id = _column(row, "execution_id")
            order_ref = _column(row, "order_ref")
            symbol = _column(row, "symbol").upper()
            side = _column(row, "side").upper()
            side = {"BOT": "BUY", "SLD": "SELL"}.get(side, side)
            if not execution_id or not order_ref or not symbol or side not in {"BUY", "SELL"}:
                raise ValueError(f"row {line}: execution_id, order_ref, symbol and BUY/SELL side are required")
            shares = _number(_column(row, "shares"), "shares", line, required=True)
            price = _number(_column(row, "price_usd"), "price_usd", line, required=True)
            assert shares is not None and price is not None
            if shares <= 0 or price <= 0:
                raise ValueError(f"row {line}: shares and price_usd must be positive")
            commission = _number(_column(row, "commission_usd"), "commission_usd", line, required=False)
            exchange_fees = _number(
                _column(row, "exchange_fees_usd"), "exchange_fees_usd", line, required=False
            )
            plan = _column(row, "pricing_plan").lower() or "unknown"
            routing = _column(row, "routing").upper() or "UNKNOWN"
            if plan not in {"fixed", "tiered", "unknown"}:
                raise ValueError(f"row {line}: pricing_plan must be fixed, tiered or unknown")
            if routing not in {"SMART", "DIRECTED", "UNKNOWN"}:
                raise ValueError(f"row {line}: routing must be SMART, DIRECTED or UNKNOWN")
            fill = ExecutionFill(
                execution_id, order_ref, symbol, side, shares, price,
                commission, exchange_fees, plan, routing,
                _column(row, "executed_at_utc"),
            )
            if execution_id in fills and fills[execution_id] != fill:
                raise ValueError(f"row {line}: conflicting duplicate execution_id {execution_id!r}")
            fills[execution_id] = fill
    return list(fills.values())


def _ledger_by_ref(entries: dict[str, OrderLedgerEntry]) -> dict[str, OrderLedgerEntry]:
    refs: dict[str, OrderLedgerEntry] = {}
    for entry in entries.values():
        refs[entry.ledger_key] = entry
        refs[entry.order_ref] = entry
        if entry.replacement_order_ref:
            refs[entry.replacement_order_ref] = entry
    return refs


def analyze_execution_costs(
    fills: list[ExecutionFill], entries: dict[str, OrderLedgerEntry]
) -> list[OrderExecutionCost]:
    """Aggregate fills per broker orderRef (NOT across replacement orders).

    This makes extra minimum commissions visible on replacement orderRefs. Quoted
    spreads and slippage use the ledger's pre-submit quote only when the entry's
    current orderRef matches: earlier replacements may have different reference
    prices and must not be compared to a later replacement's quote.
    """
    grouped: dict[str, list[ExecutionFill]] = defaultdict(list)
    for fill in fills:
        if fill.order_ref.startswith("poma:"):
            grouped[fill.order_ref].append(fill)
    ledger_refs = _ledger_by_ref(entries)
    costs: list[OrderExecutionCost] = []
    for order_ref, rows in sorted(grouped.items()):
        first = rows[0]
        if any(
            (f.symbol, f.side, f.pricing_plan, f.routing) !=
            (first.symbol, first.side, first.pricing_plan, first.routing) for f in rows
        ):
            raise ValueError(f"{order_ref}: fills disagree on symbol, side, plan or routing")
        shares = sum(f.shares for f in rows)
        notional = sum(f.shares * f.price_usd for f in rows)
        price = notional / shares
        commission = sum(f.commission_usd for f in rows if f.commission_usd is not None)
        exchange_fees = sum(f.exchange_fees_usd for f in rows if f.exchange_fees_usd is not None)
        complete_commission = all(f.commission_usd is not None for f in rows)
        complete_exchange = all(f.exchange_fees_usd is not None for f in rows)
        commission_value = commission if complete_commission else None
        exchange_value = exchange_fees if complete_exchange else None
        broker_fees = (
            commission_value + exchange_value
            if commission_value is not None and exchange_value is not None else None
        )
        entry = ledger_refs.get(order_ref)
        # A replacement changes the quote basis; do not compute slippage off the old quote.
        same_quote = (entry is not None and order_ref == entry.order_ref and entry.reference_price_source == "ibkr")
        quote_spread = entry.quote_spread_bps if same_quote else None
        reference = entry.reference_price if same_quote else None
        sign = 1 if first.side == "BUY" else -1
        slippage = sign * (price - reference) / reference * 10_000 if reference and reference > 0 else None
        slippage_usd = sign * (price - reference) * shares if reference and reference > 0 else None
        warnings = []
        if not complete_commission:
            warnings.append("commission missing")
        if not complete_exchange:
            warnings.append("exchange/third-party fees not separately reported")
        if first.routing == "DIRECTED" and first.pricing_plan == "tiered":
            warnings.append("directed API execution is not Tiered-eligible; verify effective IBKR rate")
        if not same_quote:
            warnings.append("no matching pre-submit quote for slippage")
        costs.append(
            OrderExecutionCost(
                order_ref, first.symbol, first.side, len(rows), shares, price, notional,
                commission_value, exchange_value, broker_fees,
                broker_fees / notional * 10_000 if broker_fees is not None else None,
                quote_spread, slippage, slippage_usd, first.pricing_plan,
                first.routing, entry is not None, "; ".join(warnings),
            )
        )
    return costs


def plan_summary(costs: list[OrderExecutionCost]) -> dict[str, dict[str, float | int | None]]:
    """Descriptive observations by actual plan, not a counterfactual savings estimate."""
    grouped: dict[str, list[OrderExecutionCost]] = defaultdict(list)
    for item in costs:
        grouped[item.pricing_plan].append(item)
    summaries = {}
    for plan, items in sorted(grouped.items()):
        total_notional = sum(item.notional_usd for item in items)
        complete = all(item.reported_broker_fees_usd is not None for item in items)
        complete_commission = all(item.commission_usd is not None for item in items)
        summaries[plan] = {
            "orders": len(items),
            "notional_usd": total_notional,
            "reported_commission_usd": (
                sum(item.commission_usd for item in items if item.commission_usd is not None)
                if complete_commission else None
            ),
            "reported_broker_fees_usd": (
                sum(item.reported_broker_fees_usd for item in items if item.reported_broker_fees_usd is not None)
                if complete else None
            ),
            "orders_with_missing_fees": sum(item.reported_broker_fees_usd is None for item in items),
        }
    return summaries


def write_execution_costs(path: Path, costs: list[OrderExecutionCost]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=_HEADERS)
        writer.writeheader()
        for item in costs:
            writer.writerow(asdict(item))
