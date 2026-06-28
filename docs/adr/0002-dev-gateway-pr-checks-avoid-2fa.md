# ADR 0002: Superseded — dev gateway PR checks avoided human IBKR 2FA

Date: 2026-06-28

Status: Superseded by dev `configure-paper` validation

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on pull requests. It originally avoided credentialed broker login and only checked gateway runtime helper installation, service rendering, and systemd startup before merging. Dev now intentionally runs `action: configure-paper` so pull-request validation proves the paper Gateway login and authenticated API handshake work in the dev environment when an operator approves IBKR mobile 2FA.

The broker `configure-paper` action is intentionally stronger: it writes IBC credentials, submits the IBKR login flow, waits for mobile 2FA, and verifies a real `ib_insync` API handshake. That is appropriate for staging after merge, where an operator can approve the IBKR mobile prompt. It is not deterministic for unattended pull-request CI because failure can mean no human approved 2FA rather than a code defect.

## Decision

The pull-request `dev-configure-gateway` job no longer uses `action: restart`; it uses `action: configure-paper`.

This PR check now:

1. Repairs and installs the Gateway runtime helpers.
2. Writes dev IBC paper credentials from GitHub Environment Secrets.
3. Restarts `ibgateway.service` through IBC.
4. Waits for IBKR mobile 2FA approval and verifies a real authenticated `ib_insync` handshake.

The staging `stg-configure-gateway` job also uses `action: configure-paper` on pushes to `main`, and the production release job continues to use `action: configure-live`.

## Consequences

- Pull-request Auto CI/CD requires a dev operator to approve IBKR mobile 2FA when gateway-related changes run.
- Gateway runtime, service, broker-login, and authenticated API regressions are caught before merge in dev.
- Unattended PRs can fail if no operator approves 2FA within the readiness timeout.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use `configure-paper` on every PR

This gives the strongest broker-login validation before merge, but it makes PR CI depend on a human approving the IBKR mobile notification within the timeout window. In unattended automation this creates false red builds and blocks unrelated repository fixes.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, and systemd startup regressions to reach `main`. The restart check keeps those deterministic validations without requiring broker login.
