"""Core-satellite sleeve built entirely from GitHub Environment variables.

One core UCITS ETF holds ``CORE_SATELLITE_CORE_PCT`` of the sleeve; the remainder is split across
thematic satellite UCITS ETFs (default: cybersecurity and consumer staples, equal weighted).
See docs/strategies/core-satellite.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from poma.models import StrategyTarget, StrategyTargetBook
from poma.strategies.base import StrategyContext, StrategyDataRequirements
from poma.ucits_instruments import USD_LSE_UCITS, UcitsListing

if TYPE_CHECKING:
    from poma.config import Settings

NAME = "core_satellite"

_LATEST_PRICE_DAYS = 10
_WEIGHT_TOLERANCE = 1e-8


@dataclass(frozen=True)
class CoreSatellitePlan:
    """Validated core/satellite weights, as fractions of the sleeve (not the account)."""

    core_ticker: str
    core_weight: float
    satellite_weights: dict[str, float]

    @property
    def weights(self) -> dict[str, float]:
        return {self.core_ticker: self.core_weight, **self.satellite_weights}

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(self.weights)


def _parse_fraction(raw: str, label: str) -> float:
    text = raw.strip()
    try:
        value = float(text[:-1].strip()) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        raise ValueError(f"{label} must be a number such as 0.15 or 15%, got {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def parse_satellites(raw: str) -> dict[str, float]:
    """Parse ``USPY,IUCS`` (equal weight) or ``USPY=0.6,IUCS=0.4`` (explicit shares of the satellite sleeve)."""
    entries = [entry.strip() for entry in (raw or "").split(",") if entry.strip()]
    if not entries:
        raise ValueError("CORE_SATELLITE_SATELLITES must list at least one satellite ticker")
    with_weight = ["=" in entry for entry in entries]
    if any(with_weight) and not all(with_weight):
        raise ValueError(
            "CORE_SATELLITE_SATELLITES must either list bare tickers (equal weight) "
            "or give every ticker an explicit =weight"
        )
    tickers = [(entry.split("=", 1)[0]).strip().upper() for entry in entries]
    if len(set(tickers)) != len(tickers):
        raise ValueError("CORE_SATELLITE_SATELLITES must not repeat a ticker")
    if not all(with_weight):
        return {ticker: 1.0 / len(tickers) for ticker in tickers}
    weights = {
        ticker: _parse_fraction(entry.split("=", 1)[1], f"CORE_SATELLITE_SATELLITES weight for {ticker}")
        for ticker, entry in zip(tickers, entries, strict=True)
    }
    if any(weight <= 0 for weight in weights.values()):
        raise ValueError("CORE_SATELLITE_SATELLITES weights must be positive")
    total = sum(weights.values())
    if not math.isclose(total, 1.0, abs_tol=_WEIGHT_TOLERANCE, rel_tol=0):
        raise ValueError(
            f"CORE_SATELLITE_SATELLITES weights must sum to 100% of the satellite sleeve (got {total:.4%})"
        )
    return weights


def _require_listing(ticker: str, label: str) -> UcitsListing:
    listing = USD_LSE_UCITS.get(ticker)
    if listing is None:
        raise ValueError(
            f"{label} {ticker!r} is not a registered USD UCITS ETF; "
            f"registered: {', '.join(sorted(USD_LSE_UCITS))}"
        )
    return listing


def resolve_plan(settings: Settings) -> CoreSatellitePlan:
    """Validate the CORE_SATELLITE_* variables and return the sleeve weights. Raises ``ValueError``."""
    core_ticker = (settings.core_satellite_core_ticker or "").strip().upper()
    core_pct = settings.core_satellite_core_pct
    if not math.isfinite(core_pct) or not 0 < core_pct < 1:
        raise ValueError(
            "CORE_SATELLITE_CORE_PCT must be above 0 and below 1; the remainder is the satellite sleeve"
        )
    satellites = parse_satellites(settings.core_satellite_satellites)
    if core_ticker in satellites:
        raise ValueError(f"{core_ticker} cannot be both the core and a satellite")

    listings = {core_ticker: _require_listing(core_ticker, "CORE_SATELLITE_CORE_TICKER")}
    for ticker in satellites:
        listings[ticker] = _require_listing(ticker, "CORE_SATELLITE_SATELLITES ticker")
    if settings.core_satellite_require_irish_domicile:
        foreign = sorted(t for t, listing in listings.items() if listing.domicile != "IE")
        if foreign:
            raise ValueError(
                f"{', '.join(foreign)}: not Ireland-domiciled; Irish funds get the 15% US-Ireland treaty "
                "rate on US dividends. Set CORE_SATELLITE_REQUIRE_IRISH_DOMICILE=false to allow others."
            )

    satellite_pct = 1.0 - core_pct
    return CoreSatellitePlan(
        core_ticker=core_ticker,
        core_weight=core_pct,
        satellite_weights={ticker: weight * satellite_pct for ticker, weight in satellites.items()},
    )


class CoreSatelliteStrategy:
    """Hold a core UCITS ETF plus thematic UCITS ETF satellites at fixed target weights."""

    name = NAME

    def data_requirements(self, settings: Settings) -> StrategyDataRequirements:
        return StrategyDataRequirements(
            uses_universe=False,
            price_history_tickers=resolve_plan(settings).tickers,
            price_history_days=_LATEST_PRICE_DAYS,
        )

    def build_targets(self, context: StrategyContext) -> StrategyTargetBook:
        plan = resolve_plan(context.settings)
        targets = tuple(
            StrategyTarget(
                strategy_name=self.name,
                ticker=ticker,
                sleeve_weight=weight,
                portfolio_weight=weight * context.allocation_pct,
                target_notional=weight * context.capital_usd,
            )
            for ticker, weight in plan.weights.items()
        )
        return StrategyTargetBook(
            strategy_name=self.name,
            allocation_pct=context.allocation_pct,
            capital_usd=context.capital_usd,
            targets=targets,
            diversified_fund_tickers=plan.tickers,
        )
