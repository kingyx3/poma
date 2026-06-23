# IB Gateway operations runbook

This repo provisions IB Gateway on the same GCP e2-micro VM as POMA. It is designed for low cost: one VM, no Secret Manager, no Artifact Registry, no extra scheduler, and no managed database.

## What the VM installs

The VM startup script installs and enables:

- Stable Linux IB Gateway in `/opt/ibgateway`.
- Linux IBC in `/opt/ibc`.
- `ibgateway.service` under `systemd`.
- A headless display using `Xvfb` and `fluxbox`.
- Localhost-only VNC on port `5900` for operator access through an IAP SSH tunnel.
- `/usr/local/bin/poma-configure-ibc` for one-time local IBC setup.

The service runs as the `poma` user. It starts raw IB Gateway until `/home/poma/ibc/config.ini` exists. After that file exists, it starts Gateway through IBC.

## First-time setup flow

1. Run the WIF bootstrap workflow in GitHub Actions.
2. Run the GCP VM deploy workflow in GitHub Actions.
3. SSH to the VM through IAP:

```bash
gcloud compute ssh poma-free-tier --zone us-west1-b --tunnel-through-iap
```

4. Configure IBC locally on the VM:

```bash
sudo poma-configure-ibc
```

5. Approve broker mobile authentication if prompted.
6. Confirm the service is active:

```bash
sudo systemctl status ibgateway --no-pager
```

7. Confirm the API socket is reachable:

```bash
nc -z 127.0.0.1 7497 && echo "IB Gateway API socket is reachable"
```

8. Only after the socket is reachable, set `TRADING_MODE=paper` through GitHub Variables and redeploy.

## Viewing the Gateway GUI

Use VNC only through an IAP SSH tunnel. Do not expose VNC to the public internet.

From your local machine:

```bash
gcloud compute ssh poma-free-tier \
  --zone us-west1-b \
  --tunnel-through-iap \
  -- -L 5900:127.0.0.1:5900
```

Then open a local VNC client to `127.0.0.1:5900`.

## Service commands

```bash
sudo systemctl status ibgateway --no-pager
sudo systemctl restart ibgateway
sudo journalctl -u ibgateway -n 200 --no-pager
```

## Files to know

| Path | Purpose |
|---|---|
| `/opt/ibgateway` | IB Gateway install directory. |
| `/opt/ibc` | IBC install directory. |
| `/home/poma/ibc/config.ini` | Local IBC config created by `poma-configure-ibc`. |
| `/home/poma/Jts` | Gateway settings directory. |
| `/tmp/poma-ibgateway` | Runtime logs for `Xvfb`, `fluxbox`, and VNC. |
| `/usr/local/bin/poma-run-ib-gateway` | systemd entrypoint for Gateway/IBC. |
| `/usr/local/bin/poma-configure-ibc` | interactive VM-local IBC setup helper. |

## Paper/live checklist

Before `TRADING_MODE=paper`:

- `ibgateway.service` is active.
- `127.0.0.1:7497` is reachable on the VM.
- Telegram alerts are configured and tested.
- `IBKR_ACCOUNT` is configured as a GitHub Secret.
- `DATA_PROVIDER` is either `fixture` for dry-run testing or `fmp` with validated data output.

Before `TRADING_MODE=live`:

- Paper mode has run successfully for at least one full trading week.
- `ALLOW_LIVE_TRADING=true` is set intentionally.
- Order size, turnover, daily trade count, and position caps have been reviewed.
- The latest rebalance report has been manually reviewed.
- You are comfortable with Gateway restart and mobile authentication recovery.

## Expected limitations

IBKR authentication still requires operator involvement when mobile approval, session reset, or account prompts occur. The repo makes this easier by supervising Gateway and adding IBC, but it does not try to hide or bypass broker authentication requirements.
