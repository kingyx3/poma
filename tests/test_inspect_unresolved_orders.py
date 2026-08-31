from __future__ import annotations

from types import SimpleNamespace

from ops.scripts.inspect_unresolved_orders import _matches


def test_matches_prefers_perm_id() -> None:
    row = SimpleNamespace(order_id=10, perm_id=99, order_ref="poma-a", ticker="RY", side="SELL")
    candidate = SimpleNamespace(order_id=11, perm_id=99, order_ref="other", ticker="AAPL", side="BUY")
    assert _matches(row, candidate)


def test_matches_falls_back_to_order_ref() -> None:
    row = SimpleNamespace(order_id=None, perm_id=None, order_ref="poma-a", ticker="RY", side="SELL")
    candidate = SimpleNamespace(order_id=None, perm_id=None, order_ref="poma-a", ticker="RY", side="SELL")
    assert _matches(row, candidate)


def test_matches_ticker_and_side_only_as_last_resort() -> None:
    row = SimpleNamespace(order_id=None, perm_id=None, order_ref=None, ticker="RY", side="SELL")
    candidate = SimpleNamespace(order_id=None, perm_id=None, order_ref=None, ticker="RY", side="SELL")
    assert _matches(row, candidate)
