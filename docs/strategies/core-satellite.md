# Core-satellite strategy

## Strategy id

```text
core_satellite
```

A fixed-weight sleeve: one core UCITS ETF plus thematic satellite UCITS ETFs, all configured from GitHub Environment variables. The default is a 70% core (`USMC`) and a 30% satellite sleeve split equally between cybersecurity (`USPY`) and consumer staples (`IUCS`), so 15% each.

```text
INVESTMENT_STRATEGY_WEIGHTS=core_satellite=1.0
CASH_TARGET_PCT=0.02
```

With the 2% cash reserve, target weights of total equity are USMC 68.6%, USPY 14.7%, IUCS 14.7%, cash 2%.

## GitHub Environment variables

Set these under **Settings → Environments → [environment] → Environment variables** (not secrets). Paper/live preflight requires the first three whenever `INVESTMENT_STRATEGY_WEIGHTS` contains `core_satellite`, so a default can never silently pick the funds.

| Variable | Default | Meaning |
|---|---|---|
| `INVESTMENT_STRATEGY_WEIGHTS` | none (required) | Include `core_satellite=1.0`, or a share of it alongside other sleeves. |
| `CORE_SATELLITE_CORE_TICKER` | `USMC` | Core fund. Any registered UCITS ticker. |
| `CORE_SATELLITE_CORE_PCT` | `0.70` | Core share of the sleeve, above 0 and below 1. The remainder is the satellite sleeve. |
| `CORE_SATELLITE_SATELLITES` | `USPY,IUCS` | Satellite funds. Bare tickers are equal weighted; `USPY=0.6,IUCS=0.4` sets shares of the satellite sleeve (must total 100%). |
| `CORE_SATELLITE_REQUIRE_IRISH_DOMICILE` | `true` | Reject any fund whose ISIN does not start with `IE`. |
| `MIN_WEIGHT_DELTA_PCT` | `0.0025` | Rebalance band, see below. |

Variables are validated at startup. An unknown ticker, a duplicate, weights that do not total 100%, or a ticker that is both core and satellite fails before any order is planned.

## Registered funds

All are Ireland-domiciled, accumulating, USD lines. The ticker is the IBKR symbol; Yahoo prices use the `.L` suffix.

| Role | Ticker | Fund | ISIN | Listing |
|---|---|---|---|---|
| Core | `USMC` | Franklin US Mega Cap 100 | IE0008M1R3N4 | SIX (`EBS`) |
| Core | `SP1` | L&G S&P 100 | IE000MU0FDZ8 | LSE ETF |
| Cybersecurity | `USPY` | L&G Cyber Security | IE00BYPLS672 | LSE ETF |
| Cybersecurity | `BUGG` | Global X Cybersecurity | IE00BMH5Y871 | LSE ETF |
| Consumer staples | `IUCS` | iShares S&P 500 Consumer Staples Sector | IE00B40B8R38 | LSE ETF |

Adding a fund means adding one entry to `src/poma/ucits_instruments.py`. Every registered ticker is also a valid direct sleeve, so `INVESTMENT_STRATEGY_WEIGHTS=USMC=0.70,USPY=0.15,IUCS=0.15` is equivalent to this strategy at the default settings. Use `core_satellite` for the validation, the Irish-domicile guard, and one place to change the split.

**Verify before live.** The ISINs and the vendor-reported tickers above were checked against public listings, but vendors disagree on two LSE tickers (`USPY` vs `ISPY` for L&G; `BUG` vs `BUGG` for Global X), and the IBKR symbol and conId for each line have not been proven against a Gateway. The runtime fails closed on a missing or ambiguous conId, a non-USD currency or the wrong primary exchange, but you must still pass the paper gates in [US mega-cap UCITS readiness](us-megacap-ucits.md) for each new ticker: contract qualification, quotes, spreads, a tiny paper fill, and cancel/replace.

## Why Irish-domiciled funds for a Singapore investor

Singapore has no income tax treaty with the US. A Singapore resident holding a US-listed ETF suffers 30% US withholding tax on US dividends. An Ireland-domiciled fund holding US shares claims the US-Ireland treaty rate of 15% at fund level. Irish UCITS funds also avoid direct US estate-tax exposure on US-situs assets, which applies to non-US individuals above a low threshold. Accumulating share classes reinvest dividends inside the fund, so the 15% is the only dividend tax in the chain.

Practical notes:

- Cybersecurity pays little in dividends, so the withholding saving is small there; the domicile benefit is mainly estate-tax and consistency. The saving matters most for consumer staples (`IUCS`) and the core fund.
- Singapore does not tax capital gains, and generally does not tax foreign-sourced dividends received by individuals. Confirm your own position.
- This is general information, not tax advice.

## Rebalancing

POMA already runs once per market session and only trades a ticker when its gap clears `MIN_TRADE_NOTIONAL_USD`, `MIN_WEIGHT_DELTA_PCT` and whole-share rounding. That behaves as a drift band. `MIN_WEIGHT_DELTA_PCT` is measured against total portfolio value, so the default 0.25% rebalances very eagerly for a 14.7% sleeve. Suggested settings:

| Goal | `MIN_WEIGHT_DELTA_PCT` |
|---|---|
| Tight tracking, more trades | `0.0025` |
| Rebalance when a satellite drifts about 1 point of the account (suggested) | `0.01` |
| Loose, tax-lot friendly | `0.02` |

New cash is spent on whichever funds are most underweight, because every run targets the full weights.

## Risk notes

- Overlap: your core fund already holds the large US consumer-staples and technology names. Satellites add to, not diversify away from, that exposure.
- Cybersecurity funds are small, with higher fees (about 0.5-0.7% TER versus 0.15% for `IUCS` and 0.09% for `USMC`), wider spreads and more tracking error. Check spreads against `EXECUTION_MAX_SPREAD_BPS`.
- Fixed weights rebalance against momentum: they sell winners and buy laggards.
- All lines trade in USD on the LSE (USMC on SIX); the engine enforces each line's own market hours at order time. London UCITS holdings require `MARKET_CALENDAR=LSE` and `REBALANCE_AFTER_OPEN_MINUTES>=390` in paper/live, which the config enforces.
- Orders are whole shares. A satellite with a high share price on a small account may round noticeably; check the dry-run report.
- No backtest of this allocation is included. Past performance does not predict future returns.

## Rolling out

1. Set the variables in the `dev` environment, then run **Deploy GCP VM** with `trading_mode=dry_run` and check the report targets.
2. Run `paper` with the IBKR paper account and confirm each ticker qualifies and fills a small order.
3. Promote to `prd` only through the live-readiness gates in [`docs/production-readiness.md`](../production-readiness.md).

Switching an existing portfolio (for example from `USMC=1.0`) rebalances in one run: the core is trimmed to its new target, then the satellites are bought with the proceeds and available cash (sells first, then buys after cash refreshes).
