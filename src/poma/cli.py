from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from poma.broker import build_broker
from poma.config import TradingMode, get_settings
from poma.data import build_data_client, utc_run_id
from poma.market_calendar import should_rebalance_now
from poma.models import RebalancePlan
from poma.notifications import send_alert
from poma.risk import enforce_turnover_limit, generate_trades, validate_targets
from poma.state import LocalState
from poma.strategy import build_market_cap_targets, select_maintained_or_improved

app = typer.Typer(no_args_is_help=True)
console = Console()


def _write_report(plan: RebalancePlan, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{plan.run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": plan.run_id,
                "session_date": plan.session_date,
                "targets": [target.__dict__ for target in plan.targets],
                "trades": [
                    trade.__dict__ | {"side": trade.side.value}
                    for trade in plan.trades
                ],
                "warnings": plan.warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path


def _rebalance(session_date: str, force_dry_run: bool) -> RebalancePlan:
    settings = get_settings()
    if force_dry_run:
        settings = settings.model_copy(update={"trading_mode": TradingMode.DRY_RUN})

    data_client = build_data_client(settings)
    broker = build_broker(settings)

    current = data_client.current_universe_snapshot()
    previous = data_client.previous_universe_snapshot(settings.rank_lookback_periods)
    selected = select_maintained_or_improved(current, previous)
    targets = build_market_cap_targets(
        selected=selected,
        portfolio_value_usd=settings.portfolio_value_usd,
        cash_buffer_pct=settings.cash_buffer_pct,
        max_position_pct=settings.max_position_pct,
    )

    prices = {
        str(row.ticker): float(row.price)
        for row in current.itertuples()
        if hasattr(row, "price") and row.price is not None
    }
    positions = broker.positions()
    trades, trade_warnings = generate_trades(
        targets=targets,
        current_positions=positions,
        latest_prices=prices,
        portfolio_value_usd=settings.portfolio_value_usd,
        min_trade_notional_usd=settings.min_trade_notional_usd,
        min_weight_delta_pct=settings.min_weight_delta_pct,
    )
    warnings = validate_targets(targets, settings.max_position_pct)
    warnings.extend(trade_warnings)
    warnings.extend(
        enforce_turnover_limit(
            trades,
            settings.portfolio_value_usd,
            settings.max_turnover_pct,
        )
    )

    plan = RebalancePlan(
        run_id=utc_run_id(),
        session_date=session_date,
        targets=targets,
        trades=trades,
        warnings=warnings,
    )
    report_path = _write_report(plan, settings.report_dir)

    blocked = any("block execution" in warning for warning in warnings)
    if settings.trading_mode == TradingMode.DRY_RUN or blocked:
        console.print(f"Dry run / blocked. Report written to {report_path}")
        for warning in warnings:
            console.print(f"[yellow]WARNING[/yellow] {warning}")
        send_alert(
            settings,
            f"POMA {session_date}: dry-run/blocked, {len(trades)} proposed trades",
        )
        return plan

    broker.submit_trades(trades)
    send_alert(settings, f"POMA {session_date}: submitted {len(trades)} trades")
    console.print(f"Submitted {len(trades)} trades. Report written to {report_path}")
    return plan


@app.command()
def rebalance(
    session_date: str = typer.Option("manual", help="Session label used in the report."),
    dry_run: bool = typer.Option(False, help="Force dry-run mode for this run."),
) -> None:
    _rebalance(session_date=session_date, force_dry_run=dry_run)


@app.command()
def monitor(dry_run: bool = typer.Option(False, help="Force dry-run mode for this run.")) -> None:
    settings = get_settings()
    state = LocalState(settings.state_dir)

    first_decision = should_rebalance_now(
        calendar_name=settings.market_calendar,
        after_open_minutes=settings.rebalance_after_open_minutes,
        already_ran=False,
    )
    if not first_decision.session_date:
        console.print(f"Skipping: {first_decision.reason}")
        return

    decision = should_rebalance_now(
        calendar_name=settings.market_calendar,
        after_open_minutes=settings.rebalance_after_open_minutes,
        already_ran=state.has_rebalanced(first_decision.session_date),
    )
    if not decision.should_run:
        console.print(f"Skipping: {decision.reason}")
        return

    plan = _rebalance(session_date=first_decision.session_date, force_dry_run=dry_run)
    state.mark_rebalanced(first_decision.session_date, plan.run_id)


if __name__ == "__main__":
    app()
