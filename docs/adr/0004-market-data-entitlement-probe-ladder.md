# ADR 0004: Prove real-time market data entitlement with a probe-only data type ladder

Date: 2026-07-02

Status: Accepted

## Context

ADR-0003's market data readiness check surfaced the dev paper account's real entitlement gap
(IBKR error 10089: real-time API market data requires an additional subscription; only delayed
data is available) and worked around it with `ALLOW_DELAYED_EXECUTION_QUOTES=true` for non-live
modes. Two problems remained:

- The 2026-07-02 dev `configure-paper` run executed `poma ibkr-check` at 05:20 UTC with the US
  market closed. Neither the live request nor the delayed retry ticked within its wait, IBKR
  reported no error, and the check soft-passed as "request may still be warming up". The probe
  could not distinguish "market closed, nothing ticking" from "entitlement broken" -- which is
  exactly the question ops needed answered, because dev paper trading was silently running on
  15-minute delayed quotes at best. The decision has since been made that dev paper trading must
  use real-time prices, so the delayed-quote workaround must become visible and temporary rather
  than silent and permanent.
- The trading-permission probe sent its what-if order with `transmit=False`, which IBKR rejects
  with error 321 ("What-If order should have transmit flag set to TRUE") -- noisy in every
  configure log even though the preview itself succeeded.

## Decision

- `_probe_market_data` walks a probe-only market data type ladder -- live (1), frozen (2),
  delayed (3), delayed-frozen (4) -- stopping at the first evidence and always restoring live
  afterwards. Frozen data serves the last real-time quote of a closed session and requires the
  same entitlement as live, so a frozen tick off-hours proves real-time entitlement
  ("after-market-hours live pricing"), while a delayed-only tick proves that entitlement is
  missing. On the frozen steps a finite bid/ask/last/close also counts as evidence, since frozen
  snapshots may populate prices without a fresh tick timestamp. The ladder exists only in the
  probe: `execution_quotes`/`_retry_missing_quotes_as_delayed` are untouched, and a regression
  test pins that the execution path never requests data types 2 or 4 -- frozen quotes are stale
  by definition and must never price orders.
- The probe returns a structured verdict (`market_data_type`, `market_data_realtime`,
  `market_data_soft_failure` on `IbkrHealth`) instead of a message string that `check_ibkr` had
  to sniff for "warming up" hints. `poma ibkr-check` output now states
  `market_data_type=...` and `realtime_entitlement=yes|no` directly.
- Severity is market-hours aware via `poma.market_calendar.is_market_open` (calendar failures
  degrade to "unknown", treated like closed, never crash a gateway check): silence while the US
  market is open is a hard failure (entitlement or data-farm problem, conclusively); silence
  while closed stays a soft warning but now says "market closed -- probe inconclusive".
- New `REQUIRE_LIVE_EXECUTION_QUOTES` setting: when true, only a live/frozen tick passes --
  delayed-only data and market-closed silence both hard-fail. `deploy-gcp-vm.yml` defaults it to
  `true` for every non-live `TRADING_MODE`, so dev/stg configure fails loudly until the paper
  account's real-time API market data subscription and paper-account market data sharing are
  actually fixed, and proves the fix the first time a live/frozen tick arrives. Live keeps
  `false` because a delayed-only probe already hard-fails there
  (`ALLOW_DELAYED_EXECUTION_QUOTES=false`), and demanding proof-of-tick would block legitimate
  off-hours live configures. `ALLOW_DELAYED_EXECUTION_QUOTES=true` stays on for non-live during
  the transition; flip it to `false` once `verify-market-data` confirms real-time ticks.
- New probe wait setting `MARKET_DATA_PROBE_WAIT_SECONDS` (default 5s per ladder step; the
  delayed steps wait twice as long per ADR-0003's finding that delayed subscriptions start
  ticking slowly). Worst case ~30s, well inside the 240s ops-side `ibkr-check` timeout.
- New read-only **IB Gateway Ops** action `verify-market-data`: runs `poma ibkr-check` against
  the currently running Gateway session with no repair and no restart, so a green run proves the
  deployed session genuinely serves entitled quotes. Run it (workflow_dispatch) after changing
  IBKR market data subscriptions/sharing; Telegram notification comes from the existing
  always-on step.
- The what-if trading-permission probe now sets `transmit=True` (error 321 fix); `whatIf=True`
  alone already guarantees the order is never executed.

## Consequences

- `poma ibkr-check` answers "does this session reach real-time prices" directly and
  conclusively at any time of day, instead of soft-passing on market-closed silence.
- Dev/stg gateway configure hard-fails until the IBKR account-side fix (real-time API market
  data subscription on the funded account + "share real-time market data with paper trading
  account") is completed -- an intentional, loud transition state.
- The probe takes up to ~30s when nothing ticks (four ladder steps), still once per
  check/rebalance-probe, not per symbol.
- `src/poma/broker.py` now imports `poma.market_calendar` (lazily, probe-only);
  `pandas_market_calendars` was already a runtime dependency via `poma monitor`.

## Rejected alternatives

### Add frozen data to the execution-path delayed retry

Frozen quotes are the last prices of a closed session -- stale by definition. Execution pricing
correctness depends on quote freshness (`EXECUTION_QUOTE_MAX_AGE_SECONDS`), so frozen data is
useful as entitlement *evidence* but must never become an order's reference price.

### A scheduled market-hours cron check

Considered scheduling `ibkr-check` during regular trading hours so silence would be conclusive.
Rejected in favor of making the probe itself conclusive off-hours via the frozen step: the
existing configure-time checks and the on-demand `verify-market-data` action then cover the same
question without a new scheduled workflow that only fires from the default branch.

### Only lengthen the probe wait

ADR-0003 already rejected wait-widening for the entitlement failure shape; a market-closed
session can legitimately serve no live/delayed ticks for hours, so no finite wait distinguishes
"closed" from "broken" without the frozen step and market-hours awareness.
