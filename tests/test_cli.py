from __future__ import annotations

from conftest import FakeBroker, make_settings
from typer.testing import CliRunner

from poma.broker import BROKER_UNAVAILABLE_STATUS
from poma.cli import _broker_unavailable_alert, _portfolio_summary, app
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

    assert "📊 Rebalance summary" in msg
    assert "Status: Portfolio updated" in msg
    assert "Orders: 2 total · 1 buy · 1 sell" in msg
    assert "• BUY AAPL: 10/10 shares @ $195.00 · $1,950 · Filled" in msg
    assert "• SELL NVDA: 5/5 shares @ $125.00 · $625 · Filled" in msg


def test_portfolio_summary_blocked_includes_reason_and_no_change() -> None:
    trades = [
        ProposedTrade("NVDA", OrderSide.SELL, 5, 625.0, 125.0, 124.0, "rebalance")
    ]
    warnings = ["turnover 99% exceeds limit; block execution"]
    plan = RebalancePlan("run", "d", [], trades, [], warnings)
    msg = _portfolio_summary("d", plan, "blocked", executed=False)

    assert "Status: Blocked — no orders submitted" in msg
    assert "• SELL NVDA: 5 shares · $625" in msg
    assert "Warnings" in msg
    assert "block execution" in msg


def test_broker_unavailable_alert_is_batch_level_not_order_specific() -> None:
    message = _broker_unavailable_alert(
        "2026-06-29",
        OrderResult(
            ticker="NVDA",
            side=OrderSide.BUY,
            quantity=0.5,
            notional=98.0,
            order_id=None,
            status=BROKER_UNAVAILABLE_STATUS,
            filled=0.0,
            average_fill_price=None,
            message="broker unavailable before submitting orders; no orders submitted: Not connected",
        ),
    )

    assert message == (
        "🚫 Broker unavailable\n"
        "Session: 2026-06-29\n"
        "Status: no orders accepted by IBKR for this batch\n"
        "Detail: broker unavailable before submitting orders; no orders submitted: Not connected"
    )
    assert "Order:" not in message


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
