# ADR 0002: Dev gateway configure-paper requires IBKR 2FA, same as staging

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on every pull request and calls the **IB Gateway Ops** workflow against the `dev` environment. Its purpose is to verify that IBC can be configured, that `ibgateway.service` starts cleanly, and that the API socket on `127.0.0.1:7497` opens and passes the real `ib_insync` `managedAccounts` handshake.

An earlier approach used `action: restart` for the dev job, on the grounds that PRs cannot block on a human IBKR mobile 2FA approval. That approach proved inadequate:

- `restart` only reinstalls the runtime helpers and confirms `systemd` can start the unit. It does not write IBC credentials, does not run the login flow, and does not prove that broker authentication reaches the 2FA stage.
- The IBKR mobile notification is only triggered when IBC submits the login form. `restart` never reaches that point, so silent credential or IBC-config regressions can land on `main` without detection.
- GCP free-tier hardware is slow to start Java and fill the login form. The longer `configure-paper` timeouts (`IB_GATEWAY_LOGIN_PROGRESS_GRACE_SECONDS=200`, `IB_GATEWAY_2FA_APPROVAL_TIMEOUT_SECONDS=360`) give the VM enough runway to reach the 2FA prompt reliably.
- The startup-stage diagnosis added alongside this change (`STARTUP_STAGE`, `STARTUP_ACTION`, `STARTUP_REASON`, `NEXT_ACTION`) means a pre-login failure on `dev` is now classified and surfaced in the GitHub step summary rather than masked as a generic timeout.

## Decision

The `dev-configure-gateway` job uses `action: configure-paper`, the same action used by `stg-configure-gateway`.

Both dev and staging:

1. Write the IBC credential config via `sudo poma-configure-ibc`.
2. Validate the written IBC config and Gateway install via `poma-diagnose-ibgateway validate`.
3. Restart `ibgateway.service` so IBC reads the new config.
4. Poll `127.0.0.1:7497` while running startup-stage classification at each interval.
5. On socket open, confirm the session via the real `ib_insync` `managedAccounts` handshake (`poma ibkr-check`).

The operator is expected to approve the IBKR mobile 2FA prompt during the configured timeout window for both `dev` and `stg` configure-paper runs.

## Consequences

- Every PR that touches gateway-relevant paths requires a successful IBKR 2FA approval and API handshake before it can merge.
- The `dev` GitHub Environment must have `IBKR_LOGIN_ID`, `IBKR_LOGIN_SECRET`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` secrets configured, identical to the `stg` environment.
- Credential or IBC-config regressions are caught on PR rather than after merging to `main`.
- The `dev-configure-gateway` job has the same 25-minute timeout as the `stg-configure-gateway` job to accommodate slow GCP free-tier Java startup.
- If the 2FA prompt is not approved within the timeout, both `dev` and `stg` configure-paper jobs fail. The step summary startup classification indicates whether Gateway reached the login/2FA stage or failed earlier.

## Rejected alternatives

### Keep `action: restart` for dev

This avoids the 2FA approval requirement on PRs but cannot detect credential regressions, IBC-config issues, or login-stage failures before they reach `main`. It was the previous approach and proved insufficient.

### Skip the real API handshake for dev

Checking only that the socket opens (`nc -z 127.0.0.1:7497`) without the `ib_insync` `managedAccounts` handshake would allow a partially-started Gateway to pass. The full handshake is kept for both environments.
