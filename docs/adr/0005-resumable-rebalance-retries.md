# ADR 0005: Resume transient rebalance failures under the same run

Date: 2026-08-28

Status: Accepted

## Context

Production paper logs showed three recurring classes of missed rebalances:

- execution quotes were temporarily wider than `EXECUTION_MAX_SPREAD_BPS`, so a safe local
  `QuoteBlocked` result consumed the whole session;
- sells were accepted but had not filled before the buy phase refreshed broker cash, so
  `BuyingPowerBlocked` correctly refused to assume unconfirmed sell proceeds but the session
  then became terminal;
- scheduled `monitor` and `reconcile-orders` processes could still compete for the constrained
  IB Gateway host outside the specifically staggered cron second.

The durable order ledger already gives POMA the key primitive needed for safe recovery: a stable
`run_id` and deterministic orderRef per trade. Broker-visible orders can therefore replay
idempotently while a local pre-acceptance failure can be retried without creating a duplicate.

## Decision

- Add a non-terminal `retry_wait` session state. `monitor` resumes `running` and `retry_wait`
  sessions with the exact same `run_id`.
- Bound automatic recovery to 12 monitor attempts (roughly one hour at the five-minute monitor
  cadence). Exhaustion returns the underlying terminal status and sends an operator alert.
- Retry only outcomes proven to be pre-acceptance: `QuoteBlocked`, `BuyingPowerBlocked`,
  `BrokerUnavailable`, and `OrderNotAccepted` with no broker order id and no fill. Accepted,
  filled, unknown, or otherwise ambiguous broker states remain idempotent replays and are never
  blindly resubmitted.
- Sample an execution quote up to three times, five seconds apart, before recording a final
  `QuoteBlocked`. Only tickers that still fail quote validation are retried.
- Preserve SELL share quantity during execution repricing. BUY quantity remains notional-based.
  This prevents a planned one-share sell from becoming 0.99 shares after a price increase and
  flooring to zero under whole-share execution rules.
- Continue to refresh actual broker cash before buys. A buy blocked while sells are still
  working moves the session to `retry_wait`; later monitor ticks replay accepted sells, allow the
  reconciler to observe their lifecycle, refresh cash again, and submit only buys the broker
  cash can actually fund.
- If IBKR no longer reports a non-cancelled local order as open, keep it `UNKNOWN` and
  non-terminal instead of labeling it `expired`. This fails closed: the order cannot be
  resubmitted and later sessions stay blocked until broker/operator evidence establishes the
  final state.
- Serialize all scheduled POMA commands with a host-wide `flock` acquired before Docker is
  started. A busy cron invocation exits successfully and relies on the next scheduled tick rather
  than queueing another memory/CPU-heavy container behind the active job.

## Consequences

- Transient market-quality, Gateway, and sell-settlement conditions no longer automatically lose
  the entire trading day.
- The sell-before-buy safety rule is unchanged: unfilled sells never count as buying power.
- Duplicate-order protection is stricter for ambiguity: unknown broker outcomes remain unresolved
  rather than being guessed terminal.
- The free-tier host has less concurrency pressure, but moving live trading to a host with more
  memory remains operationally preferable to relying on swap.
- Retry classification is intentionally narrow. New failure classes must be explicitly proven
  pre-acceptance before being added to the automatic retry set.
