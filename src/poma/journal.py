from __future__ import annotations

import hashlib
import json
from pathlib import Path

from poma.models import AccountSnapshot, CombinedTargetPosition, OrderResult, ProposedTrade, RebalancePlan


def _combined_targets_hash(combined_targets: tuple[CombinedTargetPosition, ...]) -> str:
    """Stable fingerprint of what this run intended to hold, for diffing across retries."""
    payload = [
        {"ticker": target.ticker, "target_notional": round(target.target_notional, 2)}
        for target in sorted(combined_targets, key=lambda target: target.ticker)
    ]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _account_snapshot_to_json(snapshot: AccountSnapshot) -> dict[str, object]:
    return {
        "cash_usd": snapshot.cash_usd,
        "positions_market_value_usd": snapshot.positions_market_value_usd,
        "net_liquidation_usd": snapshot.net_liquidation_usd,
        "total_value_usd": snapshot.total_value_usd,
        "account_id": snapshot.account_id,
        "timestamp_utc": snapshot.timestamp_utc,
        "positions": [
            {"ticker": position.ticker, "quantity": position.quantity, "market_value": position.market_value}
            for position in snapshot.positions
        ],
    }


def _trade_to_json(trade: ProposedTrade) -> dict[str, object]:
    payload = trade.__dict__.copy()
    payload["side"] = trade.side.value
    return payload


def _result_to_json(result: OrderResult) -> dict[str, object]:
    payload = result.__dict__.copy()
    payload["side"] = result.side.value
    return payload


class ExecutionJournal:
    """Persists planned orders and post-trade reconciliation for diagnosing this run later.

    ``record_planned`` is written right after a plan is built, before any order is submitted, so
    a crash mid-execution still leaves a record of what was intended. ``record_reconciliation``
    is written after submission with the broker's order results and, best-effort, a fresh
    post-trade account snapshot.
    """

    def __init__(self, state_dir: Path) -> None:
        self.orders_dir = state_dir / "orders"
        self.reconciliations_dir = state_dir / "reconciliations"

    def record_planned(self, plan: RebalancePlan) -> Path:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        path = self.orders_dir / f"{plan.run_id}.json"
        payload = {
            "run_id": plan.run_id,
            "session_date": plan.session_date,
            "target_book_hash": _combined_targets_hash(plan.combined_targets),
            "strategy_attribution": [
                {
                    "strategy_name": book.strategy_name,
                    "allocation_pct": book.allocation_pct,
                    "capital_usd": book.capital_usd,
                    "target_count": len(book.targets),
                    "warnings": list(book.warnings),
                }
                for book in plan.strategy_books
            ],
            "planned_trades": [_trade_to_json(trade) for trade in plan.trades],
            "expected_account_snapshot": {
                "cash_usd": plan.portfolio_cash_usd,
                "positions_market_value_usd": plan.portfolio_positions_value_usd,
                "net_liquidation_usd": plan.portfolio_net_liquidation_usd,
                "total_value_usd": plan.portfolio_value_usd,
            },
            "warnings": plan.warnings,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path

    def record_reconciliation(
        self,
        plan: RebalancePlan,
        post_trade_account_snapshot: AccountSnapshot | None,
        post_trade_snapshot_error: str | None = None,
    ) -> Path:
        self.reconciliations_dir.mkdir(parents=True, exist_ok=True)
        path = self.reconciliations_dir / f"{plan.run_id}.json"
        payload = {
            "run_id": plan.run_id,
            "session_date": plan.session_date,
            "order_results": [_result_to_json(result) for result in plan.execution_results],
            "post_trade_account_snapshot": (
                _account_snapshot_to_json(post_trade_account_snapshot)
                if post_trade_account_snapshot is not None
                else None
            ),
            "post_trade_snapshot_error": post_trade_snapshot_error,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path
