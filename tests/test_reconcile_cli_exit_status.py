from __future__ import annotations

from types import SimpleNamespace

from conftest import make_settings
from typer.testing import CliRunner

from poma import cli
from poma.execution_manager import ReconcileSummary
from poma.models import OrderSide
from poma.order_lifecycle import OrderLifecycleState


def test_reconcile_errors_exit_nonzero_and_never_claim_success(monkeypatch, tmp_path):
    settings = make_settings(TRADING_MODE="paper", STATE_DIR=str(tmp_path))
    entry = SimpleNamespace(
        ticker="USMC",
        side=OrderSide.BUY,
        lifecycle_state=OrderLifecycleState.UNKNOWN,
        filled_qty=0,
        quantity=1,
        is_terminal=False,
        run_id="unavailable-history",
    )
    update = SimpleNamespace(entry=entry, action="error", detail="broker history unavailable", matched=False)

    class FakeManager:
        def __init__(self, *args):
            pass

        def reconcile(self):
            return ReconcileSummary(checked=1, updates=(update,))

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_broker", lambda settings: object())
    monkeypatch.setattr(cli, "ExecutionManager", FakeManager)
    monkeypatch.setattr(cli, "lifecycle_status_alert", lambda *args: "order unverified")
    monkeypatch.setattr(cli, "send_alert", lambda *args: None)

    result = CliRunner().invoke(cli.app, ["reconcile-orders"])

    assert result.exit_code == 1
    assert "ReconciliationError" in result.output
    assert "broker order finality remains" in result.output
    assert "unverified" in result.output
