# ADR 0002: Superseded — dev gateway configure-paper required IBKR 2FA

Date: 2026-06-28

Status: Superseded by `docs/adr/0002-dev-gateway-pr-checks-avoid-2fa.md`

## Context

This document previously accepted running `configure-paper` for pull-request `dev-configure-gateway` checks. That path requires a human to approve the IBKR mobile 2FA prompt during the PR workflow timeout.

In unattended CI, that proved too brittle: a failed pull-request workflow can mean that no human approved 2FA, not that the PR branch is broken.

## Superseding decision

Pull-request `dev-configure-gateway` now uses `action: restart` to validate deterministic runtime helper installation and systemd startup without requiring broker login.

Staging still uses `action: configure-paper` after merge, where an operator can approve IBKR mobile 2FA and the workflow can verify the real `ib_insync` API handshake. Production release still uses `action: configure-live`.
