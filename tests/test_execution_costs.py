from __future__ import annotations

import csv
from pathlib import Path

import pytest

from poma.execution_costs import (
    ExecutionFill,
    analyze_execution_costs,
    plan_summary,
    read_execution_fills,
    write_execution_costs,
)
from poma.models import OrderSide
from poma.order_lifecycle import OrderLedgerEntry


def _fill(execution_id: str, **overrides) -> ExecutionFill:
    values = dict(
        execution_id=execution_id,
        order_ref="poma:run:0:USMC:BUY",
        symbol="USMC",
        side="BUY",
        shares=3.0,
        price_usd=100.10,
        commission_usd=0.7,
        exchange_fees_usd=0.1,
        pricing_plan="fixed",
        routing="SMART",
        executed_at_utc="2026-10-15T14:45:00Z",
    )
    values.update(overrides)
    return ExecutionFill(**values)


def _entry() -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="poma:run:0:USMC:BUY",
        order_ref="poma:run:0:USMC:BUY",
        run_id="run",
        session_date="2026-10-15",
        ticker="USMC",
        side=OrderSide.BUY,
        quantity=10.0,
        limit_price=100.5,
        reference_price=100.0,
        reference_price_source="ibkr",
        quote_spread_bps=20.0,
    )


def test_aggregate_actual_fills_records_spread_slippage_and_additive_fees() -> None:
    fills = [
        _fill("one", shares=3, price_usd=100.10, commission_usd=0.7, exchange_fees_usd=0.1),
        _fill("two", shares=2, price_usd=100.20, commission_usd=0.4, exchange_fees_usd=0.05),
    ]
    costs = analyze_execution_costs(fills, {_entry().ledger_key: _entry()})
    assert len(costs) == 1
    cost = costs[0]
    assert cost.fills == 2
    assert cost.shares == 5
    assert cost.average_price_usd == pytest.approx(100.14)
    assert cost.commission_usd == pytest.approx(1.1)
    assert cost.exchange_fees_usd == pytest.approx(0.15)
    assert cost.reported_broker_fees_usd == pytest.approx(1.25)
    assert cost.captured_quote_spread_bps == 20.0
    assert cost.signed_price_slippage_bps == pytest.approx(14.0)
    assert cost.signed_price_slippage_usd == pytest.approx(0.7)
    assert cost.warning == ""


def test_missing_fee_is_unknown_not_zero_and_plan_comparison_uses_real_totals() -> None:
    base = _fill("one", exchange_fees_usd=None)
    tiered = _fill(
        "two", order_ref="poma:run:1:USMC:SELL", side="SELL",
        pricing_plan="tiered", commission_usd=0.5, exchange_fees_usd=0.2,
    )
    costs = analyze_execution_costs([base, tiered], {})
    fixed_cost = next(c for c in costs if c.pricing_plan == "fixed")
    assert fixed_cost.exchange_fees_usd is None
    assert fixed_cost.reported_broker_fees_usd is None
    assert "not separately reported" in fixed_cost.warning
    summary = plan_summary(costs)
    assert summary["fixed"]["reported_commission_usd"] == 0.7
    assert summary["fixed"]["reported_broker_fees_usd"] is None
    assert summary["tiered"]["reported_broker_fees_usd"] == pytest.approx(0.7)
    assert summary["fixed"]["orders_with_missing_fees"] == 1


def test_replacements_are_separate_broker_orders_and_old_quote_is_not_reused() -> None:
    entry = _entry()
    from dataclasses import replace

    replaced = replace(entry, order_ref=entry.order_ref + ":r1", reference_price=101.0)
    original = _fill("one", price_usd=100.1)
    new = _fill("two", order_ref=replaced.order_ref, price_usd=101.1)
    costs = analyze_execution_costs([original, new], {entry.ledger_key: replaced})
    assert len(costs) == 2
    assert costs[0].signed_price_slippage_bps is None
    assert costs[1].signed_price_slippage_bps == pytest.approx((101.1 - 101) / 101 * 10000)


def test_report_ignores_manual_fills_and_flags_directed_tiered(tmp_path: Path) -> None:
    manual = _fill("one", order_ref="manual:some-order")
    directed = _fill("two", pricing_plan="tiered", routing="DIRECTED")
    costs = analyze_execution_costs([manual, directed], {})
    assert len(costs) == 1
    assert "not Tiered-eligible" in costs[0].warning
    output = tmp_path / "reports" / "costs.csv"
    write_execution_costs(output, costs)
    with output.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["order_ref"] == directed.order_ref
    assert rows[0]["exchange_fees_usd"] == "0.1"


def test_read_deduplicates_exec_id_and_fails_conflicting_or_invalid_rows(tmp_path: Path) -> None:
    path = tmp_path / "executions.csv"
    path.write_text(
        "execution_id,order_ref,symbol,side,shares,price_usd,commission_usd,exchange_fees_usd,pricing_plan,routing\n"
        "A,poma:run:0:USMC:BUY,USMC,BOT,3,100.10,0.7,,fixed,SMART\n"
        "A,poma:run:0:USMC:BUY,USMC,BOT,3,100.10,0.7,,fixed,SMART\n"
    )
    fills = read_execution_fills(path)
    assert len(fills) == 1
    assert fills[0].side == "BUY"
    assert fills[0].exchange_fees_usd is None
    path.write_text(path.read_text().replace("A,poma:run:0:USMC:BUY,USMC,BOT,3,100.10,0.7,,fixed,SMART\n", 
                                           "A,poma:run:0:USMC:BUY,USMC,BOT,0,100.10,0.7,,fixed,SMART\n", 1))
    with pytest.raises(ValueError, match="must be positive"):
        read_execution_fills(path)


def test_read_rejects_duplicate_exec_id_with_conflicting_fill(tmp_path: Path) -> None:
    path = tmp_path / "executions.csv"
    path.write_text(
        "execution_id,order_ref,symbol,side,shares,price_usd\n"
        "A,poma:run:0:USMC:BUY,USMC,BUY,3,100\n"
        "A,poma:run:0:USMC:BUY,USMC,BUY,2,100\n"
    )
    with pytest.raises(ValueError, match="conflicting duplicate"):
        read_execution_fills(path)


def test_plan_cost_summary_reports_normalized_fees_and_slippage() -> None:
    costs = analyze_execution_costs(
        [_fill("a", commission_usd=1.0, exchange_fees_usd=0.5)],
        {"poma:run:0:USMC:BUY": _entry()},
    )
    summary = plan_summary(costs)
    assert summary["fixed"]["reported_fee_bps"] is not None
    assert summary["fixed"]["reference_slippage_bps"] is not None
    assert summary["fixed"]["reported_broker_fees_usd"] == 1.5
