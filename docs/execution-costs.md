# Auditing IBKR execution costs without changing USMC exposure

POMA continues to allocate the invested sleeve to **USMC** by default. This work changes
execution safeguards and observability, **not** the selected fund or portfolio weights.
Rebalance sizing still combines same-ticker sleeve targets into one order; the per-order
notional cap is a safety block, not an instruction to split a large order.

## Routing, commissions, and order lifecycle

- POMA qualifies USD UCITS instruments by unique `conId`, primary exchange and currency.
  It requests a `SMART` order contract where IBKR supports it, never falls back to an
  ambiguous symbol-only contract and does **not** force venue-directed USMC orders. USMC's
  configured USD IBKR primary line is **EBS (SIX)**, while the planning data series is
  `USMC.L`; check the qualified instrument and actual fill venue before assuming LSE routing.
- IBKR permits **SmartRouted API** orders under either Fixed or Tiered pricing, while
  **directed API orders are not eligible for Tiered**. Do not assume that selecting Tiered
  in account settings makes a directed order eligible.
- POMA paper/live orders require `ORDER_TIME_IN_FORCE=DAY` so they cannot persist into
  another session and incur another minimum commission. One bounded escalation of
  **completely unfilled** orders is enabled by default (`AUTO_REPLACE_UNFILLED_ORDERS=true`).
  It can cancel and resubmit once after `REPLACE_AFTER_SECONDS` (120s), but only with a
  fresh two-sided IBKR quote inside `EXECUTION_MAX_SPREAD_BPS`, and never beyond the
  current ask for buys / bid for sells. Already-marketable orders are left untouched.
  Set false for more passive execution with potentially lower fill rates.
- Reconciliation cancels **zero-fill** orders after `CANCEL_AFTER_SECONDS` (300s).
  Partially filled orders are never automatically modified or cancelled for timeout:
  they remain working through the `DAY` lifetime. They may still expire with
  residual exposure; POMA reports that remaining gap instead of automatically
  fragmenting the trade.
  Avoid manual order fragmentation and frequent rebalancing merely to capture tiny drift.
- IBKR warns that modifications following a partial fill and overnight persistence may
  trigger additional commission minimums. Verify fees against the account's country,
  exchange and pricing schedule rather than applying US-share minimums to European ETFs.

IBKR's commission disclosures:
[stocks/ETFs](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)
and [US directed API orders](https://investors.interactivebrokers.com/en/accounts/fees/alternate-exchange-commissions.php).

## Actual-fill audit (read-only)

Export executed trades from **IBKR Activity Statements or a Flex Query**, with execution
ID, order reference, symbol, side, shares, USD execution price, actual charged commission,
and *separately itemized* exchange/third-party fees where available. Convert the exported
columns into this UTF-8 CSV schema (one row per actual execution; never estimated orders):

```csv
execution_id,order_ref,symbol,side,shares,price_usd,commission_usd,exchange_fees_usd,pricing_plan,routing,executed_at_utc
```

- `order_ref` must be the POMA IBKR `orderRef` (e.g. `poma:<run>:0:USMC:BUY`).
  Rows without `poma:` are ignored, never attributed to a managed strategy.
- `commission_usd` is the actual charged commission in USD, **not** a value calculated
  from a public fee schedule. Leave blank if IBKR hasn't reported it.
- `exchange_fees_usd` means exchange/other fees **additional to** the reported commission.
  Only enter a separately charged amount not already included in `commission_usd`.
  If IBKR includes these within commission or does not break them out, leave it blank;
  do **not** add an included amount again. A blank is unknown, **not zero**.
- `pricing_plan` is an operator-labeled **actual** `fixed`, `tiered` or `unknown`
  for that execution period, not inferred from an order's apparent price. `routing`
  is `SMART`, `DIRECTED` or `UNKNOWN` as confirmed from the broker report.
  `executed_at_utc` is optional (UTC ISO timestamp).
- Export commissions, fees and prices in the **same USD currency** or perform a
  separately audited conversion before importing. Do not mix fee currencies.
- Duplicate execution IDs are deduplicated; conflicting duplicates or invalid values
  fail rather than silently inflate fees.

Run from the host with access to POMA's persisted state (no Gateway login required):

```bash
poma execution-costs ibkr-executions-normalized.csv \
  --state-dir state \
  --output-csv reports/execution-costs.csv
```

Each output row represents an IBKR *broker order reference*: fill count, filled shares,
volume-weighted price, notional, commission, separately charged external fees, known
combined cash charges, bid–ask quote spread in bps, and side-signed price slippage.
A replacement order reference is **separate**, making another commission minimum visible.
The pre-submit spread comes from the durable ledger and is **not** a realized spread.
Slippage is `(fill - pre-submit reference) / reference` for buys (sign reversed for sells),
not a contemporaneous arrival-midpoint implementation shortfall. Missing/mismatched quote
references produce blank slippage rather than invented precision. **Do not add spread and
slippage together as if independent costs.**

The CLI summarizes actual commission totals for observed Fixed and Tiered executions,
plus fully itemized fees only when *all* fee fields are present. **These totals are not
counterfactual savings estimates**: account volume, order size, venue, spread, dates and
liquidity may differ between samples. Compare similar USMC orders under both plans,
including missing fee breakdowns and net execution prices, before changing the IBKR
pricing plan. If routed DIRECTED, a `tiered` label is flagged for verification.

The CSV should stay in ignored `reports/` and the source export outside Git; actual
account statements may contain confidential broker/account details.

## Periodic USMC versus SPYL review

Quarterly, compare USMC to **SPYL** as an intentional *exposure* decision:
USMC targets a US mega-cap-100 selection; SPYL tracks the broader S&P 500.
Review index methodology, overlap/concentration, sector exposure, TER, assets/trading
volume, USD dealing line, bid–ask spread, IBKR trading permissions, venue and executed
all-in costs. Do not silently map `SPYL` onto `USMC` or modify the default
`INVESTMENT_STRATEGY_WEIGHTS=USMC=1.0`. SPYL is **not registered** as a direct
POMA trading instrument; adding it would require separate qualification,
calendar, price-series and safety tests and an explicit strategy decision.

## Target fulfillment, controlled escalation and larger portfolios

Order accepted is **not** target reached. A paper/live submission records a latest
quality assessment at `state/reconciliations/<run_id>-quality.json` alongside the
execution journal. The scheduled `poma reconcile-orders` command refreshes the
assessment as IBKR orders fill, expire or cancel; it compares the *original* approved
target weights with the latest broker holdings and cash. Metrics include aggregate
absolute exposure drift (USD and fraction of equity), largest ticker drift, separate
cash drift, working/unverified orders, and recorded filled/requested quantity.
The final status is `pending`, `target_reached`, `incomplete`, or `unknown`.
An unavailable snapshot, unresolved broker state or separately capped mandate
never certifies completion. **Cash drift and exposure drift overlap; do not sum them.**

POMA deliberately does not generate new residual orders from this read-only check.
A partially executed DAY order continues working, but if the remainder expires the
next assessment is `incomplete` and an alert is sent, rather than paying an
unreviewed new minimum or risking a duplicated outstanding broker order.
The earlier once-per-session monitor continues to treat a submission as *attempted*
for idempotency; consult the quality report to judge target fulfillment.

For a legitimate USMC order exceeding the **$25,000 default safety cap**, inspect
the intended allocation, IBKR buying power and verified USMC contract/venue.
Explicitly raise `MAX_ORDER_NOTIONAL_USD` to an operator-approved **single-order**
ceiling (e.g. `50000` for a reviewed $40k rebalance), then test in paper mode.
Do not bypass the cap or break the ETF into many small commission-bearing orders.
Both plan sizing and fresh IBKR execution quote enforce the approved cap.

Use matched real USMC samples to compare a resting order against the one-time
zero-fill escalation. Track broker-reported per-order fees and slippage, fill ratio,
residual cash and target-weight error; report **fee bps** separately from
**reference-price slippage bps**. The reference is commonly ask/bid, so their
sum is not an arrival-midpoint implementation shortfall. If any actual broker
fee or reference quote is missing, the corresponding weighted metric remains
unknown rather than guessed. No unsupported counterfactual Fixed/Tiered claims.

Escalation also fails closed if the refreshed price worsens beyond
`MAX_REPRICE_ADVERSE_BPS=50` from the originally captured IBKR reference,
or if the repriced full remaining order exceeds `MAX_ORDER_NOTIONAL_USD`.
These are independent of the bid/ask spread guard and prevent chasing a sharp
market move even when that move comes with a tight spread.

An accepted-but-unfilled submission marks the per-session monitor state
`orders_pending`, which **still blocks resubmitting that session**. Scheduled
order reconciliation upgrades this to `completed` only when the target portfolio
check reports `target_reached`, or `completed_with_order_issues` if it reports
`incomplete` after the orders close. `pending` and `unknown` never certify
target completion. This avoids treating IBKR `Submitted` as a completed trade.
