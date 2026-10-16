"""Read-only assessment of confirmed portfolio rebalance completion.

Broker acceptance is not a fill. No residual rebalancing trades are created here:
a missing or stale broker response cannot lead to duplicate submissions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

from poma.models import AccountSnapshot, TargetPosition
from poma.order_lifecycle import OrderLedgerEntry, OrderLifecycleState


def assess_rebalance(
    *,
    targets: list[TargetPosition],
    planned_value_usd: float,
    snapshot: AccountSnapshot | None,
    order_entries: Iterable[OrderLedgerEntry],
    original_broker_value_usd: float | None = None,
    min_trade_notional_usd: float,
    min_weight_delta_pct: float,
) -> dict[str, object]:
    """Compare actual holdings with original target weights at latest broker value.

    Cash drift and position drift are distinct views of potential underinvestment,
    not additive cost terms. Unknown order states and absent snapshots never certify
    target achievement. For capped mandates, outside-cap holdings can distort drift
    and are flagged for operator review.
    """
    orders = list(order_entries)
    requested = sum(max(entry.quantity, 0.0) for entry in orders)
    filled = sum(min(max(entry.filled_qty, 0.0), max(entry.quantity, 0.0)) for entry in orders)
    working = sum(not entry.is_terminal for entry in orders)
    unverified = sum(entry.lifecycle_state == OrderLifecycleState.UNKNOWN for entry in orders)
    report: dict[str, object] = {
        "status": "unknown",
        "order_count": len(orders),
        "working_orders": working,
        "unverified_orders": unverified,
        "requested_shares": requested,
        "filled_shares": filled,
        "fill_ratio": min(1.0, filled / requested) if requested > 0 else 1.0,
        "target_error_usd": 0.0,
        "target_error_pct": 0.0,
        "max_ticker_drift_usd": 0.0,
        "cash_drift_usd": None,
        "target_cash_usd": None,
        "observed_cash_usd": None,
        "snapshot_available": snapshot is not None,
        "warning": "",
    }
    if snapshot is None or planned_value_usd <= 0:
        report["warning"] = "no confirmed post-execution portfolio snapshot; cannot certify rebalance"
        return report
    actual_total = snapshot.total_value_usd
    if not math.isfinite(actual_total) or actual_total <= 0:
        report["warning"] = "invalid broker portfolio value; cannot certify rebalance"
        return report
    capped = original_broker_value_usd is not None and original_broker_value_usd > planned_value_usd * 1.001
    reference_total = min(planned_value_usd, actual_total) if capped else actual_total
    target_values = {target.ticker: target.target_weight * reference_total for target in targets}
    actual_values: dict[str, float] = {}
    for position in snapshot.positions:
        actual_values[position.ticker] = actual_values.get(position.ticker, 0.0) + position.market_value
    drift_by_ticker = {
        ticker: abs(actual_values.get(ticker, 0.0) - target_values.get(ticker, 0.0))
        for ticker in target_values.keys() | actual_values.keys()
    }
    drift = sum(drift_by_ticker.values())
    desired_cash = reference_total - sum(target_values.values())
    cash_drift = snapshot.cash_usd - desired_cash
    tolerance = max(min_trade_notional_usd, reference_total * min_weight_delta_pct)
    report.update(
        target_error_usd=drift,
        target_error_pct=drift / reference_total,
        max_ticker_drift_usd=max(drift_by_ticker.values(), default=0.0),
        cash_drift_usd=cash_drift,
        target_cash_usd=desired_cash,
        observed_cash_usd=snapshot.cash_usd,
    )
    if unverified:
        report["status"] = "unknown"
    elif working:
        report["status"] = "pending"
    elif drift > tolerance or abs(cash_drift) > tolerance:
        report["status"] = "incomplete"
    else:
        report["status"] = "target_reached"
    if capped:
        report["warning"] = (
            "broker equity exceeds the planned managed cap; outside-cap assets and cash "
            "may affect this exposure comparison"
        )
        # A cap-limited account cannot certify an all-account exposure match.
        report["status"] = "unknown"
    return report


def assess_saved_run(
    planned_path: Path,
    snapshot: AccountSnapshot | None,
    entries: Iterable[OrderLedgerEntry],
    *,
    min_trade_notional_usd: float,
    min_weight_delta_pct: float,
) -> dict[str, object]:
    """Read original approved targets, not a recomputed market-data strategy."""
    plan = json.loads(planned_path.read_text())
    run_id = str(plan["run_id"])
    targets = [TargetPosition(**row) for row in plan["targets"]]
    return assess_rebalance(
        targets=targets,
        planned_value_usd=float(plan["portfolio_value_usd"]),
        snapshot=snapshot,
        order_entries=[entry for entry in entries if entry.run_id == run_id],
        original_broker_value_usd=float(plan.get('broker_account_snapshot', {}).get('total_value_usd', plan['portfolio_value_usd'])),
        min_trade_notional_usd=min_trade_notional_usd,
        min_weight_delta_pct=min_weight_delta_pct,
    )
