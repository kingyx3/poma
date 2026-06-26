from __future__ import annotations

from conftest import FakeBroker, make_settings
from typer.testing import CliRunner

from poma.cli import _portfolio_summary, app
from poma.health import Check
from poma.models import CurrentPosition, OrderResult, OrderSide, ProposedTrade, RebalancePlan

runner = CliRunner()


def test_portfolio_summary_reports_executed_fills() -> None:
    results = [
        OrderResult("AAPL", OrderSide.BUY, 10, 1950.0, 1, "Filled", 10, 195.0),
        OrderResult("NVDA", OrderSide.SELL, 5, 625.0, 2, "Filled", 5, 125.0),
    ]
    plan = RebalancePlan("run", "2026-06-26", [], [], results, [])
    msg = _portfolio_summary("2026-06-26", plan, "completed", executed=True)

    assert "portfolio updated" in msg
    assert "2 orders (1 BUY / 1 SELL)" in msg
    assert "BUY AAPL 10@195.00" in msg
    assert "SELL NVDA 5@125.00" in msg


def test_portfolio_summary_blocked_includes_reason_and_no_change() -> None:
    trades = [ProposedTrade("NVDA", OrderSide.SELL, 5, 625.0, 125.0, 124.0, "rebalance")]
    plan = RebalancePlan("run", "d", [], trades, [], ["turnover 99% exceeds limit; block execution"])
    msg = _portfolio_summary("d", plan, "blocked", executed=False)

    assert "no change" in msg
    assert "SELL NVDA 5" in msg
    assert "block execution" in msg


def test_doctor_exits_nonzero_when_a_check_fails(monkeypatch) -> None:
    monkeypatch.setattr("poma.cli.get_settings", lambda: make_settings(TRADING_MODE="paper"))
    monkeypatch.setattr("poma.cli.run_checks", lambda settings: [Check("ibkr", False, "down")])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_doctor_exits_zero_when_all_checks_pass(monkeypatch) -> None:
    monkeypatch.setattr("poma.cli.get_settings", lambda: make_settings())
    ok_checks = [Check("data_provider", True, "ok")]
    monkeypatch.setattr("poma.cli.run_checks", lambda settings: ok_checks)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_positions_renders_portfolio(monkeypatch) -> None:
    monkeypatch.setattr("poma.cli.get_settings", lambda: make_settings(TRADING_MODE="paper"))
    broker = FakeBroker(positions=[CurrentPosition("AAPL", 10, 1950.0)])
    monkeypatch.setattr("poma.cli.build_broker", lambda settings: broker)
    result = runner.invoke(app, ["positions"])
    assert result.exit_code == 0
    assert "AAPL" in result.stdout
    assert "TOTAL" in result.stdout
