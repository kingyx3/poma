# US mega-cap UCITS ETF readiness (Singapore-based non-US investor)

## Candidates (not automatic investment instructions)

The user prefers US mega-caps instead of the broader VTI basket. Two *Ireland-domiciled,
accumulating* ETFs have explicit USD London (LSE) mappings in the code:

| Strategy symbol | London USD line | Fund / index | ISIN | TER | Caveat |
|---|---|---|---|---:|---|
| `SP1` | `SP1` / Yahoo `SP1.L` | L&G S&P 100 UCITS ETF / S&P 100 | IE000MU0FDZ8 | 0.15% | Fund launched in 2025; smaller asset base |
| `USMC` | `USMC` / Yahoo `USMC.L` | Franklin US Mega Cap 100 UCITS ETF / Solactive US Mega Cap 100 Select | IE0008M1R3N4 | 0.09% | Not literally the S&P 100 index |

Sources: [L&G S&P 100 fund](https://fundcentres.landg.com/en/at/adviser-wealth/fund-centre/ETF/SP-100/),
[SP1 LSE listing](https://www.londonstockexchange.com/stock/SP1/legal-and-general-asset-management/company-page),
[Franklin fund](https://www.franklintempletonoffshore.com/en-os/investments/etfs/price-and-performance/products/46588/ETA/franklin-us-mega-cap-100-ucits-etf/IE0008M1R3N4),
[Franklin LSE listing details](https://www.justetf.com/en/etf-profile.html?isin=IE0008M1R3N4).

The S&P 100 selection methodology considers sector balance and listed options; it is **not**
strictly the 100 largest companies in rank order. The Franklin index is closer to a strict
mega-cap tilt. Both are cap weighted, so large winners automatically grow as a proportion
of the portfolio. That also concentrates valuation, sector and single-name risks.

## Safety / configuration

Default remains `CORE_ETF_WEIGHTS=VTI=1.0`. The UCITS lines are **opt-in for dry-run/paper only**:

```env
TRADING_MODE=paper
CORE_ETF_WEIGHTS=SP1=1.0
```

or `CORE_ETF_WEIGHTS=USMC=1.0`, only on a paper account with the relevant LSE
trading permissions and market-data entitlement. The order symbol is **SP1 / USMC**;
Yahoo historical prices are **SP1.L / USMC.L**, mapped back to the order symbol so
position reconciliation does not introduce a duplicate symbol.

The IBKR contract is `Stock(<symbol>, "SMART", "USD", primaryExchange="LSEETF")`.
Only SMART is used for LSE quotes; IEX, a US venue, is skipped. These contract
details and Yahoo quote *currency* must be confirmed on the actual paper Gateway.
Do not trade solely on assumed ISIN or web-sourced data.

Live configurations that target either UCITS ETF are rejected by the settings validator.
An IBKR readiness probe for AAPL does **not** establish LSE ETF order permission or
market-data subscription. Broker paper contract qualification, USD currency consistency,
minimum tick, spread, fills, partial fills, cancel/replace and closed-market behavior
remain explicit live-promotion gates.

## Trading calendar caveat

POMA still schedules rebalances on the US market calendar. LSE trading hours and
holidays can differ; US and London markets overlap for part of the US morning only.
The current mapping is for paper testing, not a production international multi-calendar
implementation. Do not remove the live guard until LSE market-hours and holiday handling
are implemented and validated.

## Suggested tests

1. Unit tests: `pytest -q tests/test_ucits_large_cap_readiness.py`.
2. Regression: `ruff check . && pytest -q` plus the usual Docker/Terraform CI.
3. Paper Gateway, on a non-live account: check `SP1` and `USMC` qualify as
   USD LSEETF contracts with the expected ISIN; ensure subscribed quotes have
   acceptable freshness and spreads.
4. Paper *only*, after market-calendar controls: submit a tiny order within size
   limits and confirm cancel, partial/full fill, order-ref recovery, and no duplicate
   replacement. No automated live test order is permitted.
5. Obtain user investment approval and promote via a separate reviewed PR if
   all gates pass.

No backtested excess return or dominance over the S&P 500 is implied. The
operating-cost comparison must include LSE bid/ask spreads and IBKR minimum
commission, not just the fund TER.
