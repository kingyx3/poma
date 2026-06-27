from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from poma.config import Settings

YAHOO_SUPPORTED_UNIVERSES = {"us_top_market_cap", "us_top500"}
YAHOO_EXCHANGES = ("NMS", "NYQ", "ASE")
YAHOO_DOWNLOAD_BATCH_SIZE = 100
_MARKET_CAP_KEYS = ("marketCap", "market_cap", "intradaymarketcap")
_PRICE_KEYS = ("price", "regularMarketPrice", "intradayprice")
_VOLUME_KEYS = ("volume", "regularMarketVolume", "regular_market_volume")
_AVERAGE_VOLUME_KEYS = ("averageVolume", "average_volume")
_AVERAGE_VOLUME_10D_KEYS = ("averageVolume10days", "average_volume_10d")
_FLOAT_KEYS = ("floatShares", "float_shares")
_SHARES_KEYS = ("sharesOutstanding", "shares_outstanding")


class MarketDataClient(Protocol):
    def current_universe_snapshot(self) -> pd.DataFrame:
        """Return a normalized snapshot with ticker, market_cap, and price columns."""


class HistoricalMarketDataClient(MarketDataClient, Protocol):
    def historical_universe_snapshots(
        self,
        current_snapshot: pd.DataFrame,
        lookback_days: int,
        end_date: date | None = None,
    ) -> dict[date, pd.DataFrame]:
        """Return daily normalized snapshots for a lookback window when supported."""


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _market_cap_value(row: dict[str, Any]) -> Any:
    return _first_value(row, _MARKET_CAP_KEYS)


def _load_yfinance() -> tuple[Any, Any]:
    try:
        import yfinance as yf
        from yfinance import EquityQuery
    except ImportError as exc:  # pragma: no cover - exercised only when dependency is absent
        raise RuntimeError("DATA_PROVIDER=yahoo requires yfinance to be installed") from exc
    return yf, EquityQuery


class YahooFinanceMarketDataClient:
    """Yahoo Finance adapter isolated behind the normalized provider contract."""

    def __init__(self, settings: Settings) -> None:
        if settings.universe not in YAHOO_SUPPORTED_UNIVERSES:
            supported = ", ".join(sorted(YAHOO_SUPPORTED_UNIVERSES))
            raise ValueError(f"unsupported Yahoo universe={settings.universe}; supported={supported}")
        self.settings = settings
        self._yf, self._equity_query = _load_yfinance()

    def _largest_us_equities_query(self) -> Any:
        query_cls = self._equity_query
        return query_cls(
            "and",
            [
                query_cls("eq", ["region", "us"]),
                query_cls("is-in", ["exchange", *YAHOO_EXCHANGES]),
                query_cls("gt", ["intradaymarketcap", 0]),
                query_cls("gt", ["intradayprice", 1]),
            ],
        )

    @staticmethod
    def _extract_quotes(response: dict[str, Any]) -> list[dict[str, Any]]:
        if not response:
            return []
        if "quotes" in response:
            return list(response["quotes"] or [])
        result = response.get("finance", {}).get("result", [])
        if result and "quotes" in result[0]:
            return list(result[0]["quotes"] or [])
        result = response.get("result", [])
        if result and "quotes" in result[0]:
            return list(result[0]["quotes"] or [])
        raise ValueError(f"unexpected Yahoo screen response shape: {sorted(response)}")

    def current_universe_snapshot(self) -> pd.DataFrame:
        limit = int(self.settings.yahoo_screener_limit)
        page_size = min(int(self.settings.yahoo_screener_page_size), 250)
        rows: list[dict[str, Any]] = []
        query = self._largest_us_equities_query()

        for offset in range(0, limit, page_size):
            response = self._yf.screen(
                query,
                offset=offset,
                size=min(page_size, limit - offset),
                sortField="intradaymarketcap",
                sortAsc=False,
            )
            rows.extend(self._extract_quotes(response))

        records = []
        for row in rows:
            symbol = str(row.get("symbol", "")).upper().strip()
            market_cap = _market_cap_value(row)
            price = _first_value(row, _PRICE_KEYS)
            volume = _first_value(row, _VOLUME_KEYS)
            average_volume = _first_value(row, _AVERAGE_VOLUME_KEYS)
            average_volume_10d = _first_value(row, _AVERAGE_VOLUME_10D_KEYS)
            shares_outstanding = _first_value(row, _SHARES_KEYS)
            if shares_outstanding is None and market_cap is not None and price not in (None, 0):
                shares_outstanding = float(market_cap) / float(price)
            dollar_volume = None
            if volume is not None and price not in (None, 0):
                dollar_volume = float(volume) * float(price)
            if not symbol:
                continue
            records.append(
                {
                    "ticker": symbol,
                    "name": row.get("shortName") or row.get("longName"),
                    "exchange": row.get("exchange"),
                    "market_cap": market_cap,
                    "price": price,
                    "volume": volume,
                    "average_volume": average_volume,
                    "average_volume_10d": average_volume_10d,
                    "dollar_volume": dollar_volume,
                    "float_shares": _first_value(row, _FLOAT_KEYS),
                    "shares_outstanding": shares_outstanding,
                    "source": "yahoo",
                    "as_of": date.today().isoformat(),
                }
            )

        frame = _normalise_snapshot(records, require_price=True)
        return frame.sort_values("market_cap", ascending=False).head(limit).reset_index(drop=True)

    def historical_universe_snapshots(
        self,
        current_snapshot: pd.DataFrame,
        lookback_days: int,
        end_date: date | None = None,
    ) -> dict[date, pd.DataFrame]:
        """Estimate daily historical market caps from Yahoo close prices."""
        end = end_date or date.today()
        start = end - timedelta(days=lookback_days + 10)
        current = _normalise_snapshot(current_snapshot.to_dict("records"), require_price=True)
        current = current.copy()
        current["shares_outstanding"] = pd.to_numeric(
            current.get("shares_outstanding"),
            errors="coerce",
        )
        inferred_shares = current["market_cap"] / current["price"]
        current["shares_outstanding"] = current["shares_outstanding"].fillna(inferred_shares)
        current = current.dropna(subset=["shares_outstanding"])
        current = current[current["shares_outstanding"] > 0]
        shares_by_symbol = current.set_index("ticker")["shares_outstanding"].to_dict()
        if not shares_by_symbol:
            return {}

        records_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        symbols = list(shares_by_symbol)
        for chunk in _chunked(symbols, YAHOO_DOWNLOAD_BATCH_SIZE):
            prices = self._download_close_prices(chunk, start, end)
            for symbol, series in prices.items():
                shares = float(shares_by_symbol[symbol])
                current_row = current[current["ticker"] == symbol].iloc[0].to_dict()
                for timestamp, close in series.dropna().items():
                    as_of = timestamp.date() if hasattr(timestamp, "date") else timestamp
                    records_by_date[as_of].append(
                        {
                            "ticker": symbol,
                            "name": current_row.get("name"),
                            "exchange": current_row.get("exchange"),
                            "market_cap": float(close) * shares,
                            "price": float(close),
                            "volume": current_row.get("volume"),
                            "average_volume": current_row.get("average_volume"),
                            "average_volume_10d": current_row.get("average_volume_10d"),
                            "dollar_volume": current_row.get("dollar_volume"),
                            "float_shares": current_row.get("float_shares"),
                            "shares_outstanding": shares,
                            "source": "yahoo_estimated",
                            "as_of": as_of.isoformat(),
                        }
                    )

        snapshots: dict[date, pd.DataFrame] = {}
        cutoff = end - timedelta(days=lookback_days)
        for as_of, records in records_by_date.items():
            if cutoff <= as_of <= end:
                snapshots[as_of] = _normalise_snapshot(records, require_price=True)
        return snapshots

    def _download_close_prices(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> dict[str, pd.Series]:
        data = self._yf.download(
            tickers=" ".join(symbols),
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        if data is None or data.empty:
            return {}

        prices: dict[str, pd.Series] = {}
        if isinstance(data.columns, pd.MultiIndex):
            for symbol in symbols:
                for price_column in ("Close", "Adj Close"):
                    key = (symbol, price_column)
                    if key in data.columns:
                        prices[symbol] = pd.to_numeric(data[key], errors="coerce")
                        break
            return prices

        if len(symbols) == 1:
            symbol = symbols[0]
            for price_column in ("Close", "Adj Close"):
                if price_column in data.columns:
                    prices[symbol] = pd.to_numeric(data[price_column], errors="coerce")
                    break
        return prices


def _normalise_snapshot(rows: list[dict[str, Any]], require_price: bool = False) -> pd.DataFrame:
    if not rows:
        raise ValueError("market data provider returned no rows")

    frame = pd.DataFrame(rows)
    rename_map = {
        "symbol": "ticker",
        "ticker": "ticker",
        "marketCap": "market_cap",
        "intradaymarketcap": "market_cap",
        "market_cap": "market_cap",
        "regularMarketPrice": "price",
        "intradayprice": "price",
        "price": "price",
        "regularMarketVolume": "volume",
        "averageVolume": "average_volume",
        "averageVolume10days": "average_volume_10d",
        "floatShares": "float_shares",
        "sharesOutstanding": "shares_outstanding",
    }
    valid_renames = {key: value for key, value in rename_map.items() if key in frame.columns}
    frame = frame.rename(columns=valid_renames)
    required = {"ticker", "market_cap"}
    if require_price:
        required.add("price")
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"provider snapshot missing required columns: {sorted(missing)}")
    columns = [
        column
        for column in [
            "ticker",
            "name",
            "exchange",
            "market_cap",
            "price",
            "volume",
            "average_volume",
            "average_volume_10d",
            "dollar_volume",
            "float_shares",
            "shares_outstanding",
            "source",
            "as_of",
        ]
        if column in frame
    ]
    frame = frame[columns].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    for column in [
        "market_cap",
        "price",
        "volume",
        "average_volume",
        "average_volume_10d",
        "dollar_volume",
        "float_shares",
        "shares_outstanding",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required_subset = ["ticker", "market_cap"] + (["price"] if require_price else [])
    frame = frame.dropna(subset=required_subset)
    frame = frame[frame["ticker"] != ""]
    frame = frame[frame["market_cap"] > 0]
    if require_price and "price" in frame:
        frame = frame[frame["price"] > 0]
    frame = frame.drop_duplicates(subset=["ticker"], keep="first")
    if frame.empty:
        raise ValueError("provider snapshot had no valid market-cap rows")
    return frame.reset_index(drop=True)


class FixtureMarketDataClient:
    """Deterministic provider used for local dry-runs and tests."""

    def current_universe_snapshot(self) -> pd.DataFrame:
        today = date.today().isoformat()
        return pd.DataFrame(
            [
                {
                    "ticker": "MSFT",
                    "market_cap": 3_100_000_000_000,
                    "price": 420,
                    "shares_outstanding": 7_380_952_381,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "NVDA",
                    "market_cap": 3_000_000_000_000,
                    "price": 125,
                    "shares_outstanding": 24_000_000_000,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "AAPL",
                    "market_cap": 2_900_000_000_000,
                    "price": 195,
                    "shares_outstanding": 14_871_794_872,
                    "source": "fixture",
                    "as_of": today,
                },
                {
                    "ticker": "AMZN",
                    "market_cap": 1_900_000_000_000,
                    "price": 180,
                    "shares_outstanding": 10_555_555_556,
                    "source": "fixture",
                    "as_of": today,
                },
            ]
        )


def build_data_client(settings: Settings) -> MarketDataClient:
    if settings.data_provider == "fixture":
        return FixtureMarketDataClient()
    if settings.data_provider == "yahoo":
        return YahooFinanceMarketDataClient(settings)
    raise ValueError("unsupported DATA_PROVIDER; expected fixture or yahoo")


def utc_run_id(prefix: str = "rebalance") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
