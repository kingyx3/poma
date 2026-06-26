from __future__ import annotations

from conftest import FakeBroker, make_settings
from typer.testing import CliRunner

from poma.cli import app
from poma.health import Check
from poma.models import CurrentPosition

runner = CliRunner()


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
