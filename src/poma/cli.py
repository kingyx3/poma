from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from poma.broker import build_broker
from poma.config import TradingMode, get_settings
from poma.data import build_data_client, utc_run_id
from poma.engine import RebalanceEngine, RebalanceOutcome
from poma.health import check_ibkr, run_checks
from poma.history import CapSnapshotHistory
from poma.market_calendar import should_rebalance_now
from poma.models import OrderResult, OrderSide, ProposedTrade, RebalancePlan
from poma.notifications import send_alert
from poma.order_status_alerts import order_status_alert
from poma.state import LocalState

app = typer.Typer(no_args_is_help=True, help="POMA market-cap rebalancer.")
console = Console()

_MAX_SUMMARY_LINES = 15


def _portfolio_status_label(status: str, executed: bool) -> str:
    if executed and status == "completed_with_order_issues":
        return "Completed with order issues"
    if executed:
        return "Portfolio updated"
    if status == "dry_run":
        return "Dry run — no orders submitted"
    if status == "blocked":
        return "Blocked — no orders submitted"
    return f"{status.replace('_', ' ').title()} — no orders submitted"


def _proposed_trade_summary_line(trade: ProposedTrade) -> str:
    return f"• {trade.side.value} {trade.ticker}: {trade.quantity:g} shares · ${trade.notional:,.0f}"


def _order_result_summary_line(result: OrderResult) -> str:
    average_fill = ""
    if result.average_fill_price is not None:
        average_fill = f" @ ${result.average_fill_price:.2f}"
    detail = f" · {result.message}" if result.message else ""
    return (
        f"• {result.side.value} {result.ticker}: {result.filled:g}/{result.quantity:g} shares"
        f"{average_fill} · ${result.notional:,.0f} · {result.status}{detail}"
    )


def _portfolio_summary(
    session_date: str,
    plan: RebalancePlan,
    status: str,
    executed: bool,
) -> str:
    """Human-readable Telegram summary of the portfolio change this run made or proposed."""
    items: list = plan.execution_results if executed else plan.trades
    buys = sum(1 for item in items if item.side == OrderSide.BUY)
    sells = sum(1 for item in items if item.side == OrderSide.SELL)
    lines = [
        "📊 Rebalance summary",
        f"Session: {session_date}",
        f"Status: {_portfolio_status_label(status, executed)}",
        f"Orders: {len(items)} total · {buys} buy · {sells} sell",
    ]

    if items:
        lines.extend(["", "Order details"])
    else:
        lines.append("Order details: none")

    for item in items[:_MAX_SUMMARY_LINES]:
        if executed and isinstance(item, OrderResult):
            lines.append(_order_result_summary_line(item))
        else:
            lines.append(_proposed_trade_summary_line(item))
    if len(items) > _MAX_SUMMARY_LINES:
        lines.append(f"• …and {len(items) - _MAX_SUMMARY_LINES} more")

    blocking_warnings = [warning for warning in plan.warnings if "block execution" in warning]
    if status == "blocked" and blocking_warnings:
        lines.extend(["", "Warnings"])
        lines.extend(f"• {warning}" for warning in blocking_warnings)
    return "\n".join(lines)


def _trade_to_json(trade: ProposedTrade) -> dict[str, object]:
    payload = trade.__dict__.copy()
    payload["side"] = trade.side.value
    return payload


def _result_to_json(result: OrderResult) -> dict[str, object]:
    payload = result.__dict__.copy()
    payload["side"] = result.side.value
    return payload


def _write_report(plan: RebalancePlan, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{plan.run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": plan.run_id,
                "session_date": plan.session_date,
                "portfolio_value_usd": plan.portfolio_value_usd,
                "strategy": {
                    "name": plan.strategy_name,
                    "allocation_pct": plan.strategy_allocation_pct,
                    "capital_usd": plan.strategy_capital_usd,
                    "total_allocated_pct": plan.total_allocated_pct,
                    "total_allocated_usd": plan.total_allocated_usd,
                },
                "targets": [target.__dict__ for target in plan.targets],
                "trades": [_trade_to_json(trade) for trade in plan.trades],
                "execution_results": [
                    _result_to_json(result) for result in plan.execution_results
                ],
                "warnings": plan.warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path


def _run_rebalance(
    session_date: str,
    run_id: str,
    force_dry_run: bool,
) -> tuple[RebalanceOutcome, Path]:
    settings = get_settings()
    if force_dry_run:
        settings = settings.model_copy(update={"trading_mode": TradingMode.DRY_RUN})

    engine = RebalanceEngine(settings, history=CapSnapshotHistory(settings.data_dir))
    plan = engine.build_plan(session_date, run_id)
    report_path = _write_report(plan, settings.report_dir)

    blocked = engine.is_blocked(plan)
    is_dry_run = settings.trading_mode == TradingMode.DRY_RUN
    if is_dry_run or blocked:
        outcome = RebalanceOutcome(
            plan=plan,
            executed=False,
            blocked=blocked,
            status="dry_run" if is_dry_run else "blocked",
        )
        console.print(f"Dry run / blocked. Report written to {report_path}")
        for warning in plan.warnings:
            console.print(f"[yellow]WARNING[/yellow] {warning}")
        send_alert(settings, _portfolio_summary(session_date, plan, outcome.status, executed=False))
        return outcome, report_path

    send_alert(
        settings,
        "\n".join(
            [
                "🚀 Execution starting",
                f"Session: {session_date}",
                f"Orders: {len(plan.trades)}",
            ]
        ),
    )

    def alert_order_status(_: ProposedTrade, result: OrderResult) -> None:
        send_alert(settings, order_status_alert(session_date, result))

    plan = engine.execute(plan, order_status_callback=alert_order_status)
    status = engine.execution_status(plan.execution_results)
    outcome = RebalanceOutcome(plan=plan, executed=True, blocked=False, status=status)
    report_path = _write_report(plan, settings.report_dir)
    send_alert(settings, _portfolio_summary(session_date, plan, status, executed=True))
    console.print(f"Submitted {len(plan.trades)} trades. Report written to {report_path}")
    return outcome, report_path


@app.command()
def refresh_market_data(
    lookback_days: Annotated[
        int,
        typer.Option(help="Historical lookback to refresh. Use 0 for RANK_LOOKBACK_DAYS."),
    ] = 0,
) -> None:
    """Fetch the configured provider and store normalized snapshots under DATA_DIR."""
    settings = get_settings()
    days = lookback_days or settings.rank_lookback_days
    client = build_data_client(settings)
    history = CapSnapshotHistory(settings.data_dir)
    today = datetime.now(UTC).date()

    current = client.current_universe_snapshot()
    current_path = history.save(current, today)
    console.print(f"Saved current snapshot: {current_path} ({len(current)} rows)")

    if not hasattr(client, "historical_universe_snapshots"):
        console.print(
            f"Provider {settings.data_provider} does not support historical backfill; "
            "only the current snapshot was saved."
        )
        return

    snapshots = client.historical_universe_snapshots(current, days, end_date=today)
    paths = history.save_many(snapshots)
    console.print(f"Saved {len(paths)} historical snapshots under {history.dir}")


@app.command()
def rebalance(
    session_date: Annotated[
        str,
        typer.Option(help="Session label used in the report."),
    ] = "manual",
    dry_run: Annotated[
        bool,
        typer.Option(help="Force dry-run mode for this run."),
    ] = False,
) -> None:
    _run_rebalance(session_date=session_date, run_id=utc_run_id(), force_dry_run=dry_run)


@app.command()
def monitor(
    dry_run: Annotated[
        bool,
        typer.Option(help="Force dry-run mode for this run."),
    ] = False,
) -> None:
    settings = get_settings()
    state = LocalState(settings.state_dir)

    decision = should_rebalance_now(
        calendar_name=settings.market_calendar,
        after_open_minutes=settings.rebalance_after_open_minutes,
        already_ran=False,
    )
    if not decision.session_date:
        console.print(f"Skipping: {decision.reason}")
        return

    session_date = decision.session_date
    if state.has_session_attempt(session_date):
        status = state.session_status(session_date)
        console.print(f"Skipping: session already attempted with status={status}")
        return
    if not decision.should_run:
        console.print(f"Skipping: {decision.reason}")
        return

    run_id = utc_run_id()
    state.begin_session(session_date, run_id)
    try:
        outcome, report_path = _run_rebalance(
            session_date=session_date,
            run_id=run_id,
            force_dry_run=dry_run,
        )
        state.mark_session(session_date, run_id, outcome.status, report_path=str(report_path))
    except Exception as exc:
        state.mark_session(session_date, run_id, "failed", error=str(exc))
        send_alert(
            settings,
            "\n".join(["🚨 Rebalance run failed", f"Session: {session_date}", f"Error: {exc}"]),
        )
        raise


@app.command()
def doctor() -> None:
    """Check config, market-data provider, and IBKR connectivity; exit non-zero on failure."""
    settings = get_settings()
    console.print(
        f"mode={settings.trading_mode.value} provider={settings.data_provider} "
        f"account={settings.ibkr_account or 'unset'} "
        f"endpoint={settings.ibkr_host}:{settings.ibkr_port}"
    )
    checks = run_checks(settings)
    table = Table("check", "result", "detail")
    for check in checks:
        marker = "[green]ok[/green]" if check.ok else "[red]fail[/red]"
        table.add_row(check.name, marker, check.detail)
    console.print(table)

    if not all(check.ok for check in checks):
        raise typer.Exit(code=1)
    console.print("[green]All checks passed.[/green]")


@app.command(name="ibkr-check")
def ibkr_check() -> None:
    """Probe only the IBKR API handshake (ignores the data provider); exit non-zero on failure.

    Used by the IB Gateway Ops workflow to confirm the gateway is genuinely authenticated and
    serving the API, not merely listening on the socket.
    """
    settings = get_settings()
    check = check_ibkr(settings)
    marker = "[green]ok[/green]" if check.ok else "[red]fail[/red]"
    console.print(f"ibkr {marker}: {check.detail}")
    if not check.ok:
        raise typer.Exit(code=1)


@app.command()
def positions() -> None:
    """Print the broker's current stock positions (the live paper/live portfolio)."""
    settings = get_settings()
    broker = build_broker(settings)
    rows = broker.positions()
    if not rows:
        console.print(f"No positions ({settings.trading_mode.value} mode).")
        return

    table = Table("ticker", "quantity", "market_value")
    total = 0.0
    for position in sorted(rows, key=lambda p: p.market_value, reverse=True):
        total += position.market_value
        table.add_row(position.ticker, f"{position.quantity:g}", f"{position.market_value:,.2f}")
    table.add_row("[bold]TOTAL[/bold]", "", f"[bold]{total:,.2f}[/bold]")
    console.print(table)


if __name__ == "__main__":
    app()
