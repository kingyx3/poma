# ADR 0002: Dev gateway PR checks avoid human IBKR 2FA

Date: 2026-06-28

Status: Accepted

## Context

The `dev-configure-gateway` job in **Auto CI/CD** runs on pull requests. Its purpose is to catch repository regressions in the gateway runtime helper installation, service rendering, and systemd startup path before merging.

The broker `configure-paper` action is intentionally stronger: it writes IBC credentials, submits the IBKR login flow, waits for mobile 2FA, and verifies a real `ib_insync` API handshake. That is appropriate for staging after merge, where an operator can approve the IBKR mobile prompt. It is not deterministic for unattended pull-request CI because failure can mean no human approved 2FA rather than a code defect.

## Decision

The pull-request `dev-configure-gateway` job uses `action: restart`.

This PR check:

1. Repairs and installs the Gateway runtime helpers.
2. Ensures the generated `ibgateway.service` starts through the IBC launch path.
3. Restarts `ibgateway.service` and emits runtime logs for diagnosis.

The staging `stg-configure-gateway` job continues to use `action: configure-paper` on pushes to `main`, and the production release job continues to use `action: configure-live`.

## Consequences

- Pull-request Auto CI/CD is deterministic and does not require human IBKR mobile 2FA approval.
- Gateway runtime and service regressions are still caught before merge.
- Credentialed broker login regressions are validated on staging after merge, where an operator can approve 2FA.
- Production remains gated by release promotion and the live configure path.

## Rejected alternatives

### Use `configure-paper` on every PR

This gives the strongest broker-login validation before merge, but it makes PR CI depend on a human approving the IBKR mobile notification within the timeout window. In unattended automation this creates false red builds and blocks unrelated repository fixes.

### Skip gateway validation on PRs

Skipping dev gateway validation would allow helper-install, service-rendering, and systemd startup regressions to reach `main`. The restart check keeps those deterministic validations without requiring broker login.
