# Operations runbook

POMA order lifecycle state is refreshed automatically.

Limit orders are checked after the rebalance process exits. The next scheduled rebalance also refreshes local order state before deciding whether stale rows should block planning.

When the broker no longer reports a POMA order as open, the local ledger is made terminal. If POMA had requested the cancel, the terminal state is `cancelled`; otherwise it is treated as externally resolved.
