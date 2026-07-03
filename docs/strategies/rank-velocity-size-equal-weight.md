# Rank velocity + size equal-weight strategy

This page documents the current built-in strategy-specific behavior. General app structure, portfolio sizing, cash sleeves, portfolio-level target combining, and execution controls are documented in [`docs/architecture.md`](../architecture.md) and [`docs/portfolio-management.md`](../portfolio-management.md).

## Strategy id

```text
rank_velocity_size_equal_weight
```

Use this id in `STRATEGY_ALLOCATIONS` to allocate capital to the strategy sleeve.

Current default allocation:

```text
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.98,cash=0.02
```

That default gives this strategy 98% of the resolved managed portfolio value and leaves 2% in passive cash. The allocation model itself is not strategy-specific; see [`docs/portfolio-management.md`](../portfolio-management.md).

## Objective

The strategy ranks large US-listed companies using a dual score:

1. **Size** — larger current market cap is better.
2. **Rank-rising velocity** — companies that moved up the market-cap ranking over the lookback window score higher.

The strategy is a US top-market-cap strategy. It is not a strict S&P 500, Nasdaq-100, QQQ, or index-constituent replication strategy.

## Universe and data source

Production configuration uses:

```text
DATA_PROVIDER=yahoo
UNIVERSE=us_top_market_cap
YAHOO_SCREENER_LIMIT=500
```

The Yahoo provider requests the largest US-listed equities by current market cap, normalizes rows into POMA's provider snapshot contract, and saves snapshots under `DATA_DIR/market_snapshots/`.

Yahoo's free feed does not provide a clean bulk historical market-cap endpoint. POMA stores daily point-in-time snapshots and can backfill estimated historical market caps from Yahoo close prices using current share-count information.

## Share-class deduplication

The strategy selects at company level, not raw ticker level. It collapses multiple share classes of the same issuer before ranking. Issuer/name metadata is preferred when available; exact market-cap bucket dedupe is used as a fallback when issuer metadata is unavailable.

The goal is to avoid selecting multiple tickers that represent substantially the same company exposure.

## Lookback and scoring

Default lookback:

```text
RANK_LOOKBACK_DAYS=90
```

Rank 1 is the largest company by market cap. Rank-rising velocity is computed as:

```text
previous_rank - current_rank
```

A positive value means the company moved up the market-cap ranking over the lookback window.

The strategy computes:

- current size factor;
- rank-rising velocity factor;
- z-score normalization for both factors;
- equal-weighted combined score.

```text
combined_score = z(size) + z(rank_rising_velocity)
```

The combined score favours companies that are both large and climbing the market-cap ranking.

## Selection and weighting

Default selection size:

```text
MAX_HOLDINGS=50
```

The strategy selects the top `MAX_HOLDINGS` company stocks by `combined_score`, then equal-weights selected names inside this strategy sleeve. The portfolio-level risk engine can still cap or block orders after the strategy target book is produced.

Whole-share sizing happens later in the portfolio/order layer. By default, POMA uses whole shares because the IBKR API rejects fractional order sizes for accounts that are not approved for fractional API trading.

## Missing history behavior

If insufficient rank history exists, the strategy can fall back to current market-cap selection and writes a plan warning. To build history before the first rebalance, run:

```bash
poma refresh-market-data
```

The fallback keeps dry-run and first-run planning usable, but historical snapshots improve the rank-rising-velocity signal.

## Strategy-specific knobs

| Variable | Default | Meaning |
|---|---:|---|
| `DATA_PROVIDER` | `yahoo` | Provider used for production market data. |
| `UNIVERSE` | `us_top_market_cap` | Provider universe requested by the Yahoo adapter. |
| `YAHOO_SCREENER_LIMIT` | `500` | Number of large US-listed equities requested before dedupe and scoring. |
| `YAHOO_SCREENER_PAGE_SIZE` | `250` | Yahoo screen page size. |
| `RANK_LOOKBACK_DAYS` | `90` | Rank-rising-velocity lookback window. |
| `MAX_HOLDINGS` | `50` | Number of selected companies when enough valid tickers exist. |
| `MAX_POSITION_PCT` | `0.10` | Per-position cap applied by the portfolio/risk layer. |

See [`docs/configuration.md`](../configuration.md) for the full runtime configuration table.
