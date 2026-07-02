# ADR 0003: Validate IBKR market data readiness, not just trading permissions

Date: 2026-07-01

Status: Accepted

## Context

A dev paper rebalance ran with an authenticated, trade-enabled Gateway session and still blocked
every single order with `missing quote timestamp for TICKER; block execution`. `poma ibkr-check`
(used by **IB Gateway Ops** `configure-paper`/`configure-live` and by ADR-0002's PR-time
`dev-configure-gateway` job) had already reported success, because it only validated a socket
connection, the configured account, and a what-if order preview -- never that a market data tick
actually arrives.

`IbkrBroker.execution_quotes()` requests market data via `ib_insync.IB.reqMktData` but never
listened to `IB.errorEvent`, so IBKR API errors explaining a missing tick (e.g. error 354
"Requested market data is not subscribed", warning 10167 "delayed market data not available",
2103-2108 market data farm connection status) were logged internally by ib_insync and otherwise
discarded. Every symbol failed identically because the root cause -- an account-level market data
entitlement/sharing gap, not a per-symbol data problem -- applies to the whole session at once.
The failure was only visible after the fact, per order, in Telegram, with no indication of why.

## Decision

- `IbkrBroker`/`probe_ibkr` capture `IB.errorEvent` while a market data request is outstanding
  (`_collect_market_data_errors`) and attach the raw IBKR error text to the affected
  `ExecutionQuote.broker_error`; a farm-wide error with no specific contract is attached to every
  ticker still missing a tick, since that is exactly the failure shape a shared entitlement gap
  produces. The "missing quote timestamp" block message now includes this reason when available.
- `probe_ibkr` (the function behind `poma ibkr-check`) requests a live market data tick for a
  probe symbol after connecting, exactly like `execution_quotes` does at rebalance time (same
  live-then-delayed-fallback behavior gated by `ALLOW_DELAYED_EXECUTION_QUOTES`), and reports
  `market_data_ok`/`market_data_message` on `IbkrHealth`. `check_ibkr` now fails the check when
  market data does not work, so `poma ibkr-check` -- and therefore Gateway configure -- fails
  loudly with the IBKR reason instead of silently succeeding. This check is skipped when
  `EXECUTION_PRICE_SOURCE=snapshot`, since paper/live execution never reads an IBKR quote in that
  mode.
- Auto CI/CD's change detection (`is_shared_vm_gateway_path`) now treats `src/poma/broker.py` and
  `src/poma/health.py` as shared paths: changing either runs both `dev-deploy` and
  `dev-configure-gateway`, so a PR that touches the credentialed Gateway/market-data path always
  re-validates it, the same way shared VM foundation changes already did in ADR-0002.
- This check immediately proved its value: running it against the real dev paper account surfaced
  `Error 10089: Requested market data requires additional subscription for API ... Delayed market
  data is available`, confirming the account lacks the separate IBKR "API market data" opt-in but
  does have delayed data. `deploy-gcp-vm.yml`'s CI runtime defaults now set
  `ALLOW_DELAYED_EXECUTION_QUOTES=true` for every `TRADING_MODE` except `live` (which keeps the
  conservative `false` default), so dev/stg paper trading uses delayed quotes for execution pricing
  out of the box instead of requiring a manual account-side fix before trading can work at all.

## Consequences

- A market data entitlement/sharing gap is caught at Gateway-configure time, with the actual IBKR
  error text, instead of discovered order-by-order during the next rebalance.
- `IbkrBroker._connect()` and `probe_ibkr()` now share one connect helper (`_connect_ib`) that
  requests live market data on every session; the delayed-data retry logic
  (`_retry_missing_quotes_as_delayed`) is a free function shared between `execution_quotes` and
  the new probe instead of being duplicated.
- `poma ibkr-check` takes a few seconds longer (one extra market data round trip) on every
  Gateway-configure run; this fits well within the existing readiness budget.
- PRs touching `src/poma/broker.py` or `src/poma/health.py` now also run `dev-configure-gateway`,
  which requires the same dev Gateway/broker secrets ADR-0002 already requires for Gateway-owned
  paths.

## Rejected alternatives

### Only widen `EXECUTION_QUOTE_WAIT_SECONDS`

The failure was not a timing problem -- even the most liquid symbols (AAPL) never received a tick
under the account's actual entitlement gap, and a longer wait would not change that. It would only
slow down every rebalance without adding diagnostic value.

### Fail `execution_quotes` outright on any IBKR error

Raising on every error/warning callback would also fail on benign informational messages (market
data farm "is OK" status, generic reconnect notices) that ib_insync's own wrapper already treats
as non-fatal. Surfacing the captured text in the existing block-execution warning keeps the
existing per-order safety behavior and adds the reason, rather than introducing a new failure mode.
