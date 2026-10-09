# Core ETF strategy

## Strategy id

```text
core_etf
```

Default allocation:

```text
STRATEGY_ALLOCATIONS=core_etf=0.98,cash=0.02
CORE_ETF_WEIGHTS=VTI=1.0
CORE_ETF_TREND_FILTER=false
```

## What it does

The sleeve holds a fixed basket of broad, liquid ETFs at the weights in `CORE_ETF_WEIGHTS` (default: 100% `VTI`, the whole US stock market at a 0.03% expense ratio). Each run it re-checks the target and only trades when the existing guards say the gap is worth trading (`MIN_TRADE_NOTIONAL_USD`, `MIN_WEIGHT_DELTA_PCT`, whole-share rounding). In practice that means one buy when the sleeve is first funded and a small buy a few times a year as dividends and deposits pile up as cash.

The expected return is the market's return minus a few basis points. There is no claim of beating the market and no guarantee of profit: a broad equity index can lose half its value, as it did in 2008. What this strategy removes is the trading friction that made the previous strategy lose money.

### Optional trend filter

`CORE_ETF_TREND_FILTER=true` holds each ETF only while its last completed month-end close is above the average of its last `CORE_ETF_TREND_SMA_MONTHS` (default 10) month-end closes. Otherwise that ETF's weight goes to `CORE_ETF_SAFE_ASSET` (an ETF ticker such as `SGOV`, or `cash`). The signal only uses completed months, so it changes at most once a month, and it typically switches one to two times a year.

It is off by default because the evidence below shows it lowers drawdowns but not returns, and since 2010 it has lagged buy-and-hold. Turn it on if a smaller worst-case loss matters more to you than the last couple of points of return.

### Data

The strategy does not use the Yahoo market-cap screener. It asks the engine for daily adjusted closes of its ETFs (`DATA_PROVIDER=yahoo` downloads them with `yfinance`), plans with the latest close, and the execution layer still reprices every order off a fresh IBKR quote. If ETF prices cannot be loaded, the plan blocks rather than leaving the sleeve in cash.

### Risk guards that changed with it

- `MAX_POSITION_PCT` stays a single-company cap; the strategy marks its ETFs as diversified funds, which exempts them from it.
- `MAX_ORDER_NOTIONAL_USD` now defaults to `25000` because the whole sleeve is bought in one order per ETF. It still fails closed: raise it if the account grows past about $25k.
- `MAX_TURNOVER_PCT` now measures one-sided turnover (the larger of total buys and total sells). Switching from 50 stocks to one ETF sells ~98% and buys ~98%, which is a 98% rotation and passes the default `1.0` limit instead of counting as 196%.

## Audit of the previous strategy

POMA had one strategy, `rank_velocity_size_equal_weight`: every trading day, rank the ~500 largest US-listed stocks by `z(size) + z(90-day market-cap rank change)`, hold the top 50 equal-weighted in whole shares.

Findings:

1. **It trades far too often for its account size.** Each day's 90-day rank change shifts names across the top-50 cutoff, and equal weights are re-trued whenever a name drifts 0.25% of the portfolio. Replayed with POMA's own selection and order code, it turned the portfolio over about 48 times a year with about 2,300 orders a year.
2. **Orders are too small for fixed commissions.** At ~$10k, each position is ~$196, so the DEV run's orders were 1 to 20 shares worth $135 to $365. IBKR's $1 minimum commission is 30 to 75 bps on those, before the spread.
3. **The universe includes thinly quoted names.** The Yahoo top-500 screen includes ADRs and foreign listings (`ITUB`, `E`, `WPM`) and names whose IBKR quotes were 70 to 525 bps wide or missing (`A`, `COP`, `WPM`, `WBD`), so orders were blocked or crossed wide spreads.
4. **The signal shows no reliable edge before costs.** Rebalanced daily, it returned 15.9% a year gross versus 15.4% for simply equal-weighting the same stocks, a gap well inside noise for one five-year sample. A 90-day window also overlaps the short-term reversal effect that works against momentum.
5. **Rebalancing monthly helps a lot but does not fix small accounts.** Monthly with a hold buffer cuts cost drag to about 2.8% a year at $10k, which is still more than most stock-selection signals can be expected to earn.

## Backtests

Reproduce with `python research/strategy_audit_backtest.py` (downloads public price files from GitHub on first run). Every fill pays IBKR Pro Fixed commissions ($0.005/share, $1 minimum, 1% maximum) plus a half-spread of 10 bps for stocks and 1 bp for the ETF. Prices exclude dividends for every row, so returns are understated by roughly 2% a year across the board and are comparable with each other.

### 2013-02 to 2018-02, S&P 500 members, $10k account

| Strategy | Net CAGR | Turnover / yr | Orders / yr | Cost drag / yr | Max drawdown |
|---|---:|---:|---:|---:|---:|
| `rank_velocity` daily (previous default) | -24.7% | 48.4x | 2,285 | 39.0% | -77.9% |
| `rank_velocity` monthly | 12.4% | 9.1x | 479 | 4.5% | -13.7% |
| `rank_velocity` monthly + 25-rank buffer | 13.9% | 5.6x | 313 | 2.8% | -15.6% |
| `core_etf` (new default) | 11.9% | 0.15x | <1 | ~0.0% | -14.0% |
| `core_etf` + trend filter | 9.1% | 0.9x | 1 | ~0.0% | -16.9% |

At $100k the previous strategy's cost drag falls to 7.4% a year (7.7% net CAGR) because commissions are a smaller share of each order; `core_etf` is unchanged at 11.8%. For reference, the S&P 500 price index returned 12.1% a year and an equal-weight basket of the same survivor stocks returned 15.4%.

### 1991 to 2022, S&P 500 index as the ETF, $10k

| Strategy | Net CAGR | Switches / yr | Max drawdown |
|---|---:|---:|---:|
| `core_etf` buy-and-hold | 7.8% | 0 | -56.0% |
| `core_etf` + trend filter (risk-off earns 0%) | 7.4% | ~1.3 | -23.5% |

On Shiller's monthly S&P total-return series from 1880 to 2023 (not part of the script), the 10-month filter returned about the same as buy-and-hold (9.4% vs 9.3% a year) with roughly half the worst drawdown (-46% vs -82%). On daily index data from 2010 to 2022 it trailed buy-and-hold by about 4.5 points a year. That is why it is opt-in.

### Caveats

- The stock replay uses the 505 companies in the S&P 500 in early 2018, so it ignores companies that fell out (survivorship bias), which flatters every stock-picking row. Market-cap ranks are approximated by holding current market caps fixed and moving them with 2013-2018 prices.
- One five-year window is a small sample; the turnover and cost numbers are robust, the return differences between stock rows are not.
- Commission and spread assumptions are estimates. IBKR Tiered pricing is cheaper per order on small trades; real spreads on the thinly quoted names above were wider than 10 bps.
- Past returns do not predict future returns. Nothing here guarantees a profit.

## Switching back

```text
STRATEGY_ALLOCATIONS=rank_velocity_size_equal_weight=0.98,cash=0.02
MAX_ORDER_NOTIONAL_USD=2000
```

The first run after switching strategies in either direction sells the old holdings and buys the new ones in one rebalance (sells first, then buys after cash refreshes).
