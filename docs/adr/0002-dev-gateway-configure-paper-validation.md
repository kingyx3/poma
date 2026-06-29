# ADR 0002: Dev gateway PR checks validate configure-paper

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on pull requests that need deploy or Gateway validation. The `configure-paper` action writes IBC credentials from GitHub Environment Secrets, forces a fresh Gateway login path, waits for IBKR mobile 2FA evidence and approval, and verifies a real authenticated `ib_insync` API handshake.

This credentialed path is intentionally stronger than a helper-only restart check. It proves that dev paper credentials, IBC config rendering, Gateway startup, fresh IBKR login/2FA delivery, API socket readiness, and the application `poma ibkr-check` handshake all work before gateway-related changes merge.

## Decision

Pull-request `dev-configure-gateway` uses `action: configure-paper`.

The dev PR check validates the full paper Gateway path:

1. Repairs and installs the Gateway runtime helpers when the runtime sentinel is stale.
2. Writes dev IBC paper credentials from GitHub Environment Secrets.
3. Truncates stale Gateway/IBC logs used for login-stage classification.
4. Forces `ibgateway.service` through a fresh stop/start path so the new config is loaded.
5. Requires fresh IBKR mobile 2FA evidence; socket/API readiness alone is not accepted.
6. Waits for IBKR mobile 2FA approval.
7. Verifies a real authenticated `ib_insync` API handshake through `poma ibkr-check`.

Staging `stg-configure-gateway` also uses `action: configure-paper` on pushes to `main`, and production release uses `action: configure-live`.

## Consequences

- Pull-request Auto CI/CD catches Gateway runtime, service, broker-login, 2FA-delivery, and authenticated API regressions before merge in dev.
- Dev PRs that touch deploy or Gateway paths require an operator to approve the IBKR mobile 2FA prompt within the workflow readiness window.
- A configure run that reaches an API socket without fresh 2FA evidence fails instead of producing a false-positive success from a stale Gateway session.
- A timeout can mean Gateway never reached fresh 2FA, or the operator did not approve 2FA in time, so diagnostics must clearly distinguish no-login-progress, 2FA-pending, socket readiness, and authenticated-handshake failures.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use only `restart` on PRs

A restart-only PR check is deterministic and does not require broker authentication, but it can miss credential rendering, IBC login automation, IBKR mobile 2FA delivery, and authenticated API handshake regressions until after merge.

### Accept socket/API readiness without fresh 2FA evidence

Socket/API readiness can be a stale-session false positive. Configure actions intentionally require a fresh 2FA signal before the authenticated API handshake is accepted.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, broker-login, and API readiness regressions to reach `main`.
