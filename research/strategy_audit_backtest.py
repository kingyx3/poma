#!/usr/bin/env python3
"""Reproduce the cost/turnover audit in docs/strategies/core-etf.md.

Replays POMA's own selection, trade-generation and whole-share rounding code over public daily
price data, charging IBKR Pro Fixed commissions plus a half-spread on every fill:

1. ``rank_velocity_size_equal_weight`` on 2013-02..2018-02 S&P 500 member closes, rebalanced
   daily (current behavior), monthly, and monthly with a 25-rank hold buffer.
2. ``core_etf`` on the same window and on 1991-2022, using the S&P 500 index as the ETF proxy,
   with and without the 10-month trend filter.

Research only: never imported by the app, never places orders. Inputs are price-only (no
dividends), the stock universe has survivorship bias, and market-cap ranks are approximated;
see the doc for how those caveats bear on the conclusions. Usage:

    python research/strategy_audit_backtest.py [--cache-dir research/.cache]
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import pandas as pd

from poma.execution_policy import apply_execution_policy, build_execution_rules
from poma.models import CurrentPosition, OrderSide, TargetPosition
from poma.risk import generate_trades
from poma.strategies.core_etf import trend_is_up
from poma.strategy import build_equal_weight_targets, select_by_combined_factor

SOURCES = {
    # Daily OHLCV for the ~505 S&P 500 members of early 2018 (Kaggle "S&P 500 stock data").
    "all_stocks_5yr.csv": "https://raw.githubusercontent.com/plotly/datasets/master/all_stocks_5yr.csv",
    # Current S&P 500 market caps and prices, used only to approximate relative company size.
    "constituents-financials.csv": (
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies-financials/main/data/"
        "constituents-financials.csv"
    ),
    # Daily S&P 500 index closes 1990-2022.
    "sp500_index.csv.gz": (
        "https://raw.githubusercontent.com/skfolio/skfolio/main/src/skfolio/datasets/data/sp500_index.csv.gz"
    ),
}

STOCK_HALF_SPREAD_BPS = 10.0
ETF_HALF_SPREAD_BPS = 1.0
SLEEVE_PCT = 0.98
MIN_TRADE_NOTIONAL_USD = 25.0
MIN_WEIGHT_DELTA_PCT = 0.0025
LIMIT_OFFSET_BPS = 10.0
RULES = build_execution_rules("", fractional_shares=False)


def ibkr_fixed_commission(quantity: float, notional: float) -> float:
    """IBKR Pro Fixed US stock/ETF pricing: $0.005/share, $1.00 minimum, 1% of trade value maximum."""
    return min(max(1.0, 0.005 * quantity), 0.01 * notional)


def fetch(cache_dir: Path) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in SOURCES.items():
        path = cache_dir / name
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(url, partial)  # noqa: S310 - fixed https URLs above
            partial.rename(path)
        paths[name] = path
    return paths


class Book:
    """Cash plus whole-share positions, filled at close +/- half-spread with commissions."""

    def __init__(self, capital: float, half_spread_bps: float) -> None:
        self.capital = capital
        self.cash = capital
        self.positions: dict[str, float] = {}
        self.half_spread_bps = half_spread_bps
        self.commissions = self.spread_cost = self.traded = 0.0
        self.orders = 0

    def value(self, prices: pd.Series) -> float:
        return self.cash + sum(qty * prices[ticker] for ticker, qty in self.positions.items())

    def rebalance(self, targets: list[TargetPosition], prices: pd.Series) -> None:
        portfolio_value = self.value(prices)
        current = [
            CurrentPosition(ticker, qty, qty * prices[ticker]) for ticker, qty in self.positions.items() if qty
        ]
        trades, _ = generate_trades(
            targets,
            current,
            prices.to_dict(),
            portfolio_value,
            MIN_TRADE_NOTIONAL_USD,
            MIN_WEIGHT_DELTA_PCT,
            LIMIT_OFFSET_BPS,
        )
        trades, _ = apply_execution_policy(trades, RULES, available_cash_usd=self.cash)
        for trade in sorted(trades, key=lambda item: item.side != OrderSide.SELL):
            price = prices[trade.ticker]
            sign = 1 if trade.side == OrderSide.BUY else -1
            fill = price * (1 + sign * self.half_spread_bps / 10_000)
            commission = ibkr_fixed_commission(trade.quantity, trade.quantity * price)
            if sign > 0 and self.cash < trade.quantity * fill + commission:
                continue
            self.cash -= sign * trade.quantity * fill + commission
            self.positions[trade.ticker] = self.positions.get(trade.ticker, 0.0) + sign * trade.quantity
            self.commissions += commission
            self.spread_cost += trade.quantity * price * self.half_spread_bps / 10_000
            self.traded += trade.quantity * price
            self.orders += 1


def summarize(label: str, book: Book, equity: pd.Series) -> dict[str, object]:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    average = equity.mean()
    return {
        "strategy": label,
        "capital": book.capital,
        "cagr": (equity.iloc[-1] / book.capital) ** (1 / years) - 1,
        "turnover_per_yr": book.traded / average / years,
        "orders_per_yr": book.orders / years,
        "cost_drag_per_yr": (book.commissions + book.spread_cost) / average / years,
        "max_drawdown": (equity / equity.cummax() - 1).min(),
    }


def rank_velocity(
    closes: pd.DataFrame,
    shares: pd.Series,
    capital: float,
    cadence: str,
    buffer: int = 0,
    max_holdings: int = 50,
    lookback_days: int = 90,
) -> dict[str, object]:
    book = Book(capital, STOCK_HALF_SPREAD_BPS)
    equity, last_month, held = [], None, set()
    for i, day in enumerate(closes.index):
        prices = closes.loc[day]
        due = cadence == "daily" or day.month != last_month
        last_month = day.month
        if due and i > 0:
            current = pd.DataFrame({"ticker": closes.columns, "market_cap": shares * prices, "price": prices})
            past = closes.index[closes.index <= day - pd.Timedelta(days=lookback_days)]
            if len(past):
                historical = pd.DataFrame({"ticker": closes.columns, "market_cap": shares * closes.loc[past[-1]]})
                ranked = select_by_combined_factor(current.dropna(), historical.dropna(), len(current))
            else:
                ranked = current.dropna().sort_values("market_cap", ascending=False)
            if buffer and held:
                keep = [t for t in ranked["ticker"].head(max_holdings + buffer) if t in held]
                fill = [t for t in ranked["ticker"] if t not in keep][: max_holdings - len(keep)]
                selected = ranked[ranked["ticker"].isin(keep + fill)]
            else:
                selected = ranked.head(max_holdings)
            held = set(selected["ticker"])
            sleeve = build_equal_weight_targets(selected, book.value(prices) * SLEEVE_PCT, 0.10)
            targets = [TargetPosition(t.ticker, t.target_weight * SLEEVE_PCT, t.target_notional) for t in sleeve]
            book.rebalance(targets, prices)
        equity.append(book.value(prices))
    label = f"rank_velocity {cadence}" + (f" +{buffer}-rank buffer" if buffer else "")
    return summarize(label, book, pd.Series(equity, index=closes.index))


def core_etf(index: pd.Series, capital: float, start: str, end: str, trend: bool) -> dict[str, object]:
    etf = index / 20.0  # VTI-like share price, so whole-share rounding is realistic
    window = etf.loc[start:end]
    book = Book(capital, ETF_HALF_SPREAD_BPS)
    equity = []
    for day, price in window.items():
        prices = pd.Series({"ETF": price})
        invested = True
        if trend:
            signal = trend_is_up(etf.loc[:day], 10)
            invested = True if signal is None else signal
        value = book.value(prices)
        targets = [TargetPosition("ETF", SLEEVE_PCT, SLEEVE_PCT * value)] if invested else []
        book.rebalance(targets, prices)
        equity.append(book.value(prices))
    label = "core_etf + 10m trend filter" if trend else "core_etf buy-and-hold"
    return summarize(label, book, pd.Series(equity, index=window.index))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-dir", type=Path, default=Path("research/.cache"))
    args = parser.parse_args()
    paths = fetch(args.cache_dir)

    stocks = pd.read_csv(paths["all_stocks_5yr.csv"], parse_dates=["date"])
    closes = stocks.pivot(index="date", columns="Name", values="close").ffill()
    financials = pd.read_csv(paths["constituents-financials.csv"]).set_index("Symbol")
    # Approximation: hold today's market caps fixed at the window's last close to get share
    # counts, so ranks move with 2013-2018 relative prices. Size ordering is approximate.
    shares = (financials["Market Cap"].reindex(closes.columns) / closes.iloc[-1]).dropna()
    closes = closes[shares.index]
    index = pd.read_csv(paths["sp500_index.csv.gz"], parse_dates=["Date"], index_col="Date")["SP500"]
    start, end = closes.index[0].strftime("%Y-%m-%d"), closes.index[-1].strftime("%Y-%m-%d")

    rows = []
    for capital in (10_000, 100_000):
        rows.append(rank_velocity(closes, shares, capital, "daily"))
        rows.append(rank_velocity(closes, shares, capital, "monthly"))
        rows.append(rank_velocity(closes, shares, capital, "monthly", buffer=25))
        rows.append(core_etf(index, capital, start, end, trend=False))
        rows.append(core_etf(index, capital, start, end, trend=True))
    table = pd.DataFrame(rows)
    pd.options.display.float_format = "{:,.4f}".format
    print(f"Window {start} .. {end}, price-only, net of IBKR Fixed commissions and half-spreads")
    print(table.to_string(index=False))

    years = (closes.index[-1] - closes.index[0]).days / 365.25
    benchmark = index.loc[start:end]
    universe_ew = closes.pct_change(fill_method=None).mean(axis=1).add(1).cumprod()
    print(f"S&P 500 index price CAGR: {(benchmark.iloc[-1] / benchmark.iloc[0]) ** (1 / years) - 1:.4f}")
    universe_cagr = universe_ew.iloc[-1] ** (1 / years) - 1
    print(f"Equal-weight 2018 S&P 500 members price CAGR (survivorship-biased): {universe_cagr:.4f}")

    long_rows = [core_etf(index, 10_000, "1991-01-02", "2022-12-28", trend=trend) for trend in (False, True)]
    print("\n1991-01 .. 2022-12, $10k, price-only, risk-off weight earns 0%")
    print(pd.DataFrame(long_rows).to_string(index=False))


if __name__ == "__main__":
    main()
