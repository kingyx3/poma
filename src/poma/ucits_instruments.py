"""Explicit mappings for USD-traded Irish UCITS ETFs.

Only LSE USD lines are supported here: the strategy ticker is the IBKR trading
symbol while Yahoo Finance expects a '.L' suffix. Live orders require a unique IBKR conId for the USD/LSEETF listing, live market
quotes and exchange regular-session checks; no raw-symbol order fallback.
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


# SP1: L&G S&P 100 (IE000MU0FDZ8), USMC: Franklin US Mega Cap 100
# (IE0008M1R3N4). Both are accumulating, Ireland-domiciled USD LSE lines.
USD_LSE_UCITS: dict[str, UcitsListing] = {
    "SP1": UcitsListing("SP1", "SP1.L", "IE000MU0FDZ8"),
    "USMC": UcitsListing("USMC", "USMC.L", "IE0008M1R3N4"),
}


def ucits_listing(ticker: str) -> UcitsListing | None:
    return USD_LSE_UCITS.get(ticker.upper())


def price_symbol(ticker: str) -> str:
    listing = ucits_listing(ticker)
    return listing.yahoo_symbol if listing is not None else ticker
