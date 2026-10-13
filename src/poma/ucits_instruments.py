"""Explicit mappings for USD-traded Irish UCITS ETFs.

Only explicitly listed USD lines are supported here: the strategy ticker is the IBKR
trading symbol while Yahoo Finance expects an exchange suffix. Live orders require a
unique IBKR conId for the listed primary exchange/currency, current quotes and an open
regular-market session on that listing's own calendar; there is no symbol-only order
fallback (a bare "USMC" on SMART can resolve to an unrelated NASDAQ fund).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UcitsListing:
    ticker: str
    yahoo_symbol: str
    isin: str
    primary_exchange: str = "LSEETF"
    currency: str = "USD"
    calendar: str = "LSE"


# SP1: L&G S&P 100 (IE000MU0FDZ8), USMC: Franklin US Mega Cap 100 (IE0008M1R3N4).
# Both are accumulating, Ireland-domiciled USD lines. SP1 trades on the LSE ETF segment. USMC
# is traded on IBKR's SIX Swiss Exchange line (primary exchange "EBS", USD): IBKR returns "no
# security definition" for the LSEETF/USD lookup, and a symbol-only USMC resolves to the
# unrelated Principal US Mega-Cap ETF on NASDAQ. The Yahoo symbol is price data only and may
# differ from the order venue: Yahoo has no USMC.SW, its SIX line is the thinly traded US100.SW
# (stale quotes), while USMC.L is the same fund (same ISIN/NAV) with fresh USD prices.
USD_LSE_UCITS: dict[str, UcitsListing] = {
    "SP1": UcitsListing("SP1", "SP1.L", "IE000MU0FDZ8"),
    "USMC": UcitsListing("USMC", "USMC.L", "IE0008M1R3N4", primary_exchange="EBS", calendar="SIX"),
}


def ucits_listing(ticker: str) -> UcitsListing | None:
    return USD_LSE_UCITS.get(ticker.upper())


def price_symbol(ticker: str) -> str:
    listing = ucits_listing(ticker)
    return listing.yahoo_symbol if listing is not None else ticker
