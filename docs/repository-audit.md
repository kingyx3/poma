# Repository reliability audit

Audit baseline: `b5d24ce` (main). Reviewed Python portfolio/strategy/data paths, broker
submission and reconciliation, CLI/state persistence, Gateway/IBC helpers, Docker,
cron, GitHub workflows, Terraform and operational documentation.

## Fixed findings

| Severity | Finding | Correction and regression evidence |
|---|---|---|
| High | A disconnect inside/after `placeOrder` could become retryable `BrokerUnavailable`, even after transmission. A crash before callbacks left a `PLANNED` record. | Persist submission uncertainty before the network call; preserve uncertain outcomes; stop the rest of the batch. Tests simulate process death and a socket failure after transmission. |
| High | Cancel-and-replace had no durable replacement identity before transmission. | Record replacement intent and its new orderRef before cancellation/submission; interrupted replacements remain unresolved. |
| High | Requested cancellations immediately allowed a new run; disappearance after cancellation was treated as confirmed cancellation. | Pending cancellation blocks new runs. Completed broker evidence is required; disappearance alone remains UNKNOWN because a fill can race the request. |
| High | State/snapshots could be truncated by interrupted writes; snapshot-before-event ordering could lose final order evidence. | Atomic fsynced state/snapshot replacement; fsynced append-first event log and recovery replay. Corrupt journals fail closed. Cached ledger reads avoid repeated full replay in one process. |
| High | NaN/infinity could pass numeric comparisons in quotes, settings or available cash. Repricing could exceed an approved order cap. | Reject non-finite settings, prices, ages, spreads and cash; reject crossed quotes and negative ages; recheck notional/positive limit after repricing. |
| High | Cancel/replace matched only an order ID. | Require the configured account and a POMA orderRef; replacements also match ticker/side. |
| High | Deployment enabled live cron before Gateway authentication. | Live deploy leaves the app crontab paused; successful configure-live enables it only with explicit live config. |
| Medium | Production verification forced paper mode; socket verification could succeed without an installed app. | Select live mode for prd and require the app/API check. |
| Medium | 2FA detection accepted config keys/generic notifications, deleted logs and rejected a valid resumed session. | Preserve logs using inode/offset checkpoints; recognize runtime authentication events; always require account/trading/API verification after a challenge or open socket. |
| Medium | IBC credential setter interpreted backslash escapes through awk `-v`. | Preserve literal credential bytes through awk ENVIRON; reject multiline credential framing. |
| Medium | Provider normalization converted missing tickers into strings and accepted infinite prices/caps. | Drop missing tickers and non-finite required values. |
| Medium | History lookup sorted full paths, allowing an older current-layout snapshot to beat newer legacy history. | Compare dates across layouts and prefer current layout only on equal dates; save CSV snapshots atomically. |

## Follow-up after merge to main (`eb55427`)

- CLI `rebalance`, `monitor`, `reconcile-orders` and the operator resolution helper
  share a nonblocking process lock in `STATE_DIR`, covering decisions, broker calls
  and durable writes. Manual rebalance returns exit 75 on contention; scheduled
  commands skip that invocation. Tests cover process death, exceptions, CLI exclusion
  and independent state directories. The existing host cron lock remains in place.
- Routine Terraform deploys inspect the saved plan before apply and reject deletion,
  replacement or removal from management of VM/disk resources. Unknown protected
  actions and malformed/incomplete plans fail closed. Fresh creation and in-place
  updates remain supported. Explicit undeploy remains a destructive operator action.

## Validation

Baseline: 408 tests passed; Ruff passed. Added behavioral regression tests for the
findings above and updated tests that encoded unsafe cancellation/disconnect behavior.
The PR records the final full-suite and CI results. Local Python is 3.12; CI exercises
the deployed Python 3.11 dependency constraints and Docker build. No real-money order
was used for verification.

## Production gates and remaining limitations

- **Production provisioning is incomplete in this checkout:** `ops/deploy/environments/prd.env`
  is absent. Production workflows explicitly require it. Generate it using the existing
  WIF bootstrap workflow with the real project/environment settings; do not invent values.
- **Live IBKR authentication has not been demonstrated by this audit.** GitHub secrets,
  production Gateway runtime and mobile approval are external requirements. Run
  `configure-live`, approve IBKR Mobile if requested, then obtain a passing live-account
  `ibkr-check` and market-hours entitlement verification before enabling scheduled trading.
- **Live deployment now pauses scheduling until Gateway verification succeeds.** Manual live
  deployments must be followed by configure-live; a failed configure leaves scheduling paused.
  This protects new schedules, not containers already running before a deployment starts.
- **VM replacement needs a migration:** startup changes can require replacement and state
  is on the boot disk. Routine deploy now refuses that plan. Pause trading, drain commands,
  back up and verify restoration of order/state/data before a reviewed migration. Explicit
  undeploy or out-of-band cloud deletion can still destroy the disk; this is not a backup.
- **Serialization scope is one shared state directory on one host:** supported mutation
  commands now lock internally. Custom scripts calling persistence classes directly must
  also acquire `poma.runtime_lock.runtime_lock`; atomic files alone are not multi-writer
  transactions. All containers must mount the same durable state volume.
- **Uncertainty is deliberately fail-closed:** truncated journals, missing broker terminal
  history or an interrupted replacement can require operator reconciliation. Do not delete
  the ledger or clear state to force execution without checking broker records.
- **Strategy evidence is separate from software tests:** Yahoo reconstructed historical
  caps use current shares/universe and are not point-in-time research data. Backtesting,
  paper soak time, commissions, FX and suitability gates in `production-readiness.md`
  remain mandatory. Automated tests cannot establish investment performance.
- Dependency constraints pin top-level packages, not the complete transitive environment;
  reproducible locking and dependency/security scanning remain useful follow-up work.

## References checked

IBC's upstream authentication dialog handler distinguishes an authentication dialog
from login completion; POMA must retain its authenticated API verification:
https://github.com/IbcAlpha/IBC/blob/master/src/ibcalpha/ibc/SecondFactorAuthenticationDialogHandler.java
