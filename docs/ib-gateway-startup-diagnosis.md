# IB Gateway startup diagnosis

Use this page when the Gateway configure workflow finishes without opening `127.0.0.1:7497`.

The key rule is: when there is no broker approval prompt and no API listener, assume Gateway or IBC failed before login until the workflow reports otherwise.

## Configure flow

1. Run **IB Gateway Ops** for the target environment.
2. Choose `configure-paper` for paper mode or `configure-live` for live mode.
3. Read the GitHub step summary after the run.
4. Only approve the broker prompt after the startup stage says Gateway reached login or two-factor authentication.

## Startup stage output

The workflow runs:

```text
poma-diagnose-ibgateway startup-check
```

It prints a compact diagnosis in both the log and step summary:

```text
STARTUP_STAGE=<stage>
STARTUP_ACTION=<ready|continue|fail>
STARTUP_REASON=<reason>
NEXT_ACTION=<operator or developer action>
```

Important stages:

| Stage | Meaning |
|---|---|
| `service-active-no-xvfb` | systemd is active, but the headless display is not running. |
| `headless-gui-incomplete` | the display started, but a GUI sidecar is missing. |
| `ibc-not-running` | IBC config exists, but IBC is not running. |
| `java-gateway-not-running` | no Java or Gateway process is alive. |
| `gateway-log-error` | recent logs contain a fatal Gateway or IBC error. |
| `gateway-running-no-login-progress-timeout` | Gateway stayed alive but did not show login, 2FA, or API progress before the grace deadline. |
| `login-reached-2fa-pending` | Gateway reached broker authentication. Approve the prompt. |
| `api-socket-open` | port `7497` is listening; after two stable VM-local socket polls, the workflow proceeds to the real API handshake. |

## Manual checks

Run these from the VM shell when a workflow fails:

```bash
sudo poma-diagnose-ibgateway validate --mode paper
sudo poma-diagnose-ibgateway startup-check --log-lines 80
sudo poma-diagnose-ibgateway diagnose --log-lines 200
sudo systemctl restart ibgateway
```

The full diagnosis includes redacted config, launcher settings, process state, listeners, service logs, and Gateway/IBC log tails. Failed runs also print a final compact diagnosis and the redacted diagnostic tail directly in the job log so the actionable `STARTUP_STAGE`, `STARTUP_REASON`, and `NEXT_ACTION` are visible without opening only the step summary.

## Transient socket and service restarts

IB Gateway can briefly open `127.0.0.1:7497` before the authenticated API session is ready. The workflow now requires two consecutive successful socket polls on the VM before it runs `poma ibkr-check`, then captures the redacted `ibkr-check` tail if the real `ib_insync` handshake fails. Running the poll loop on the VM avoids repeated IAP SSH sessions from GitHub Actions. If no login/API progress appears before the grace deadline, the VM-local loop runs `poma-diagnose-ibgateway startup-check` and fails with the same compact startup diagnosis.
