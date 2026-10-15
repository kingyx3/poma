from __future__ import annotations

from ops.scripts.inspect_unresolved_orders import (
    _execution_summary,
    _identity_match,
    _normalize_side,
    _position_from_snapshot,
)
from poma.models import OpenOrderSnapshot, OrderSide
from poma.order_lifecycle import OrderLedgerEntry


def _entry(order_ref: str, *, perm_id: int | None = 99) -> OrderLedgerEntry:
    return OrderLedgerEntry(
        ledger_key="ledger-1",
        order_ref=order_ref,
        run_id="run-1",
        session_date="2026-08-31",
        ticker="RY",
        side=OrderSide.SELL,
        quantity=1,
        limit_price=200,
        perm_id=perm_id,
    )


def _snapshot(order_ref: str, *, order_id: int = 10, perm_id: int = 99) -> OpenOrderSnapshot:
    return OpenOrderSnapshot(
        order_ref=order_ref,
        order_id=order_id,
        perm_id=perm_id,
        ticker="RY",
        side=OrderSide.SELL,
        raw_status="Filled",
        filled=1,
        remaining=0,
        avg_fill_price=200,
    )


def test_identity_match_requires_exact_order_ref() -> None:
    assert _identity_match(_entry("poma:run-1:0:RY:SELL"), _snapshot("poma:run-1:0:RY:SELL"))


def test_identity_match_does_not_adopt_same_broker_ids_with_different_order_ref() -> None:
    entry = _entry("poma:run-1:0:RY:SELL")
    snapshot = _snapshot("poma:other:0:RY:SELL", order_id=10, perm_id=99)
    assert not _identity_match(entry, snapshot)


def test_normalize_side_maps_ibkr_execution_codes() -> None:
    assert _normalize_side("BOT") == "BUY"
    assert _normalize_side("SLD") == "SELL"
    assert _normalize_side("SELL") == "SELL"


def test_execution_summary_recognizes_full_sell_by_perm_id() -> None:
    executions = [
        {
            "ticker": "RY",
            "side": "SLD",
            "shares": 0.4,
            "perm_id": 99,
        },
        {
            "ticker": "RY",
            "side": "SLD",
            "shares": 0.6,
            "perm_id": 99,
        },
        {
            "ticker": "RY",
            "side": "BOT",
            "shares": 1.0,
            "perm_id": 99,
        },
    ]

    summary = _execution_summary(_entry("poma:run-1:0:RY:SELL"), executions)

    assert summary == {
        "matching_execution_count": 2,
        "matching_shares": 1.0,
        "ledger_quantity": 1,
        "full_quantity_observed": True,
    }


def test_execution_summary_rejects_wrong_perm_id() -> None:
    executions = [{"ticker": "RY", "side": "SLD", "shares": 1.0, "perm_id": 100}]

    summary = _execution_summary(_entry("poma:run-1:0:RY:SELL"), executions)

    assert summary["matching_execution_count"] == 0
    assert summary["full_quantity_observed"] is False


def test_position_from_snapshot_returns_matching_position() -> None:
    snapshot = {
        "positions": [
            {"ticker": "AAPL", "quantity": 2, "market_value": 400},
            {"ticker": "RY", "quantity": 3, "market_value": 610},
        ]
    }

    assert _position_from_snapshot(snapshot, "RY") == {
        "ticker": "RY",
        "quantity": 3,
        "market_value": 610,
    }


def test_position_from_snapshot_treats_absent_ticker_as_zero() -> None:
    snapshot = {"positions": [{"ticker": "AAPL", "quantity": 2, "market_value": 400}]}

    assert _position_from_snapshot(snapshot, "RY") == {
        "ticker": "RY",
        "quantity": 0.0,
        "market_value": 0.0,
    }


def test_order_ops_does_not_treat_empty_ledger_as_successful_order_verification(
    tmp_path, monkeypatch, capsys,
) -> None:
    from conftest import make_settings

    from ops.scripts import inspect_unresolved_orders
    from poma.models import AccountSnapshot

    settings = make_settings(DATA_DIR=tmp_path / "data", STATE_DIR=tmp_path / "state",
                             REPORT_DIR=tmp_path / "reports")
    calls = []

    class ReadOnlyBroker:
        def fetch_open_order_snapshots(self):
            calls.append("open")
            return []

        def fetch_completed_order_snapshots(self):
            calls.append("completed")
            return []

        def account_snapshot(self):
            calls.append("portfolio")
            return AccountSnapshot(cash_usd=1000.0, positions=(), positions_market_value_usd=0.0)

    monkeypatch.setattr(inspect_unresolved_orders, "get_settings", lambda: settings)
    monkeypatch.setattr(inspect_unresolved_orders, "build_broker", lambda _settings: ReadOnlyBroker())

    assert inspect_unresolved_orders.main() == 0
    output = capsys.readouterr().out
    assert "unresolved_count=0" in output
    assert "durable_order_history_count=0" in output
    assert "HISTORY_WARNING=no durable order events present" in output
    assert "broker_open_count=0" in output
    assert "current_broker_portfolio=" in output
    assert calls == ["open", "completed", "portfolio"]
