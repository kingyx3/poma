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
    name: str = ""
    theme: str = ""

    @property
    def domicile(self) -> str:
        """ISO country prefix of the ISIN; ``IE`` marks an Ireland-domiciled fund."""
        return self.isin[:2].upper()


# SP1: L&G S&P 100 (IE000MU0FDZ8), USMC: Franklin US Mega Cap 100 (IE0008M1R3N4).
# Both are accumulating, Ireland-domiciled USD lines. SP1 trades on the LSE ETF segment. USMC
# is traded on IBKR's SIX Swiss Exchange line (primary exchange "EBS", USD): IBKR returns "no
# security definition" for the LSEETF/USD lookup, and a symbol-only USMC resolves to the
# unrelated Principal US Mega-Cap ETF on NASDAQ. The Yahoo symbol is price data only and may
# differ from the order venue: Yahoo has no USMC.SW, its SIX line is the thinly traded US100.SW
# (stale quotes), while USMC.L is the same fund (same ISIN/NAV) with fresh USD prices.
#
# Thematic satellites (all Ireland-domiciled, accumulating, USD lines on the LSE ETF segment):
# USPY: L&G Cyber Security (IE00BYPLS672); some vendors show ISPY for this ISIN, but ISPY is the
# GBP/Borsa Italiana line. BUGG: Global X Cybersecurity (IE00BMH5Y871); vendors disagree on
# whether the LSE USD line is BUG or BUGG. IUCS: iShares S&P 500 Consumer Staples Sector
# (IE00B40B8R38). The IBKR symbol, conId and USD/LSEETF routing of these lines are NOT proven
# offline: the runtime conId/currency/primary-exchange gate fails closed, and each line must
# still pass the paper Gateway checks in docs/strategies/core-satellite.md before live use.
USD_LSE_UCITS: dict[str, UcitsListing] = {
    "SP1": UcitsListing("SP1", "SP1.L", "IE000MU0FDZ8", name="L&G S&P 100", theme="core"),
    "USMC": UcitsListing(
        "USMC", "USMC.L", "IE0008M1R3N4", primary_exchange="EBS", calendar="SIX",
        name="Franklin US Mega Cap 100", theme="core",
    ),
    "USPY": UcitsListing(
        "USPY", "USPY.L", "IE00BYPLS672", name="L&G Cyber Security", theme="cybersecurity",
    ),
    "BUGG": UcitsListing(
        "BUGG", "BUGG.L", "IE00BMH5Y871", name="Global X Cybersecurity", theme="cybersecurity",
    ),
    "IUCS": UcitsListing(
        "IUCS", "IUCS.L", "IE00B40B8R38", name="iShares S&P 500 Consumer Staples",
        theme="consumer_staples",
    ),
}


def ucits_listing(ticker: str) -> UcitsListing | None:
    return USD_LSE_UCITS.get(ticker.upper())


def price_symbol(ticker: str) -> str:
    listing = ucits_listing(ticker)
    return listing.yahoo_symbol if listing is not None else ticker
