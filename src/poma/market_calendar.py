from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas_market_calendars as mcal


@dataclass(frozen=True)
class MarketDecision:
    should_run: bool
    session_date: str | None
    reason: str


def _session_bounds(calendar_name: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Return exchange-local session bounds converted to UTC, or None off trading days."""
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    calendar = mcal.get_calendar(calendar_name)
    session_date = now.astimezone(calendar.tz).date()
    schedule = calendar.schedule(
        start_date=session_date.isoformat(),
        end_date=session_date.isoformat(),
    )
    if schedule.empty:
        return None
    market_open = schedule.iloc[0]["market_open"].to_pydatetime().astimezone(UTC)
    market_close = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    return market_open, market_close


def is_market_open(calendar_name: str, now_utc: datetime | None = None) -> bool:
    """Whether the requested exchange regular trading session is currently open."""
    now = now_utc or datetime.now(UTC)
    bounds = _session_bounds(calendar_name, now)
    if bounds is None:
        return False
    market_open, market_close = bounds
    return market_open <= now < market_close


def should_rebalance_now(
    calendar_name: str,
    after_open_minutes: int,
    already_ran: bool,
    now_utc: datetime | None = None,
) -> MarketDecision:
    now = now_utc or datetime.now(UTC)
    bounds = _session_bounds(calendar_name, now)
    if bounds is None:
        return MarketDecision(False, None, "not a trading day")

    session_date = now.astimezone(mcal.get_calendar(calendar_name).tz).date().isoformat()
    market_open, market_close = bounds
    rebalance_time = market_open + timedelta(minutes=after_open_minutes)

    if already_ran:
        return MarketDecision(False, session_date, "rebalance already ran for this session")
    if now < rebalance_time:
        return MarketDecision(False, session_date, "market has not been open long enough")
    if now >= market_close:
        return MarketDecision(False, session_date, "market is closed")
    return MarketDecision(True, session_date, "rebalance window open")
