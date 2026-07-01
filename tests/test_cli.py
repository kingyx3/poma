from __future__ import annotations

import json

from conftest import FakeBroker, make_settings
from typer.testing import CliRunner

from poma.broker import BROKER_UNAVAILABLE_STATUS
from poma.cli import _broker_unavailable_alert, _portfolio_summary, _run_rebalance, app
from poma.health import Check
from poma.models import CurrentPosition, OpenOrderSnapshot, OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.order_lifecycle import OrderLedgerEntry
from poma.order_store import OrderStore

runner = CliRunner()


class _ReconcileBroker(FakeBroker):
    """FakeBroker plus the lifecycle query/cancel/replace methods reconcile-orders needs."""

    def __init__(self, snapshots: list[OpenOrderSnapshot]) -> None:
        super().__init__()
        self._snapshots = snapshots

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        return self._snapshots

    def cancel_order(self, order_id: int) -> bool:
        return True

    def replace_order(self, **_kwargs) -> OpenOrderSnapshot:
        raise AssertionError("not exercised by this test")


def test_cli_command_group_builds() -> None:
    # Guards against typer/click incompatibilities (e.g. click >=8.2 with typer 0.12.3)
    # that raise "Secondary flag is not valid for non-boolean flag" when the command group
    # is constructed, which crashes every `poma` invocation in the deployed image.
    from typer.main import get_command

    command = get_command(app)
    assert {"rebalance", "monitor", "doctor", "ibkr-check", "positions", "reconcile-orders"} <= set(
        command.commands
    )


def test_portfolio_summary_reports_executed_fills() -> None:
    results = [
        OrderResult("AAPL", OrderSide.BUY, 10, 1950.0, 1, "Filled", 10, 195.0),
        OrderResult("NVDA", OrderSide.SELL, 5, 625.0, 2, "Filled", 5, 125.0),
    ]
    plan = RebalancePlan("run", "2026-06-26", [], [], results, [])
    msg = _portfolio_summary("2026-06-26", plan, "completed", executed=True)

    assert "📊 Rebalance summary" in msg
    assert "Status: Orders accepted/submitted" in msg
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


def test_run_rebalance_writes_execution_journal_before_and_after_a_run(monkeypatch, tmp_path) -> None:
    settings = make_settings(
        STATE_DIR=str(tmp_path / "state"),
        REPORT_DIR=str(tmp_path / "reports"),
        DATA_DIR=str(tmp_path / "data"),
    )
    monkeypatch.setattr("poma.cli.get_settings", lambda: settings)
    monkeypatch.setattr("poma.cli.send_alert", lambda *_args, **_kwargs: None)

    outcome, report_path = _run_rebalance(
        session_date="2026-07-01",
        run_id="run-journal-1",
        force_dry_run=False,
    )

    assert outcome.status == "dry_run"
    assert report_path.exists()
    order_journal_path = tmp_path / "state" / "orders" / "run-journal-1.json"
    assert order_journal_path.exists()
    # dry_run never submits orders, so no reconciliation file is written.
    assert not (tmp_path / "state" / "reconciliations" / "run-journal-1.json").exists()


def test_run_rebalance_report_includes_broker_snapshot_and_capital_breakdown(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(
        STATE_DIR=str(tmp_path / "state"),
        REPORT_DIR=str(tmp_path / "reports"),
        DATA_DIR=str(tmp_path / "data"),
    )
    monkeypatch.setattr("poma.cli.get_settings", lambda: settings)
    monkeypatch.setattr("poma.cli.send_alert", lambda *_args, **_kwargs: None)

    _, report_path = _run_rebalance(
        session_date="2026-07-01",
        run_id="run-journal-2",
        force_dry_run=False,
    )

    report = json.loads(report_path.read_text())
    assert set(report["broker_account_snapshot"]) == {
        "cash_usd",
        "positions_market_value_usd",
        "net_liquidation_usd",
        "total_value_usd",
    }
    assert report["cash_sleeve_usd"] == 0.02 * settings.dry_run_portfolio_value_usd
    assert report["unallocated_capital_usd"] >= 0
    assert report["target_exposure_usd"] > 0


def test_reconcile_orders_reports_no_open_orders(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "poma.cli.get_settings",
        lambda: make_settings(TRADING_MODE="paper", STATE_DIR=str(tmp_path / "state")),
    )
    monkeypatch.setattr("poma.cli.build_broker", lambda settings: _ReconcileBroker([]))

    result = runner.invoke(app, ["reconcile-orders"])

    assert result.exit_code == 0
    assert "No open orders to reconcile." in result.stdout


def test_reconcile_orders_updates_the_ledger_and_alerts_on_fill(monkeypatch, tmp_path) -> None:
    settings = make_settings(TRADING_MODE="paper", STATE_DIR=str(tmp_path / "state"))
    store = OrderStore(settings.state_dir)
    store.upsert(
        OrderLedgerEntry(
            ledger_key="poma:run-1:0:AAPL:BUY",
            order_ref="poma:run-1:0:AAPL:BUY",
            run_id="run-1",
            session_date="2026-07-01",
            ticker="AAPL",
            side=OrderSide.BUY,
            quantity=5.0,
            limit_price=196.20,
        )
    )
    broker = _ReconcileBroker(
        [
            OpenOrderSnapshot(
                order_ref="poma:run-1:0:AAPL:BUY",
                order_id=1,
                perm_id=None,
                ticker="AAPL",
                side=OrderSide.BUY,
                raw_status="Filled",
                filled=5.0,
                remaining=0.0,
                avg_fill_price=196.4,
            )
        ]
    )
    monkeypatch.setattr("poma.cli.get_settings", lambda: settings)
    monkeypatch.setattr("poma.cli.build_broker", lambda _settings: broker)
    alerts: list[str] = []
    monkeypatch.setattr("poma.cli.send_alert", lambda _settings, message: alerts.append(message))

    result = runner.invoke(app, ["reconcile-orders"])

    assert result.exit_code == 0
    assert "AAPL" in result.stdout
    assert store.load_open_orders() == []
    assert any("Order lifecycle update" in alert for alert in alerts)


def test_reconcile_orders_skips_in_dry_run_mode(monkeypatch) -> None:
    monkeypatch.setattr("poma.cli.get_settings", lambda: make_settings(TRADING_MODE="dry_run"))

    result = runner.invoke(app, ["reconcile-orders"])

    assert result.exit_code == 0
    assert "nothing to reconcile" in result.stdout


def test_positions_renders_portfolio(monkeypatch) -> None:
    monkeypatch.setattr("poma.cli.get_settings", lambda: make_settings(TRADING_MODE="paper"))
    broker = FakeBroker(positions=[CurrentPosition("AAPL", 10, 1950.0)])
    monkeypatch.setattr("poma.cli.build_broker", lambda settings: broker)
    result = runner.invoke(app, ["positions"])
    assert result.exit_code == 0
    assert "AAPL" in result.stdout
    assert "TOTAL" in result.stdout
