# IB Gateway operations

POMA runs IB Gateway on the same GCP e2-micro VM as the bot. The goal is one cheap host, supervised Gateway, and no broker credentials in GitHub or GCP managed secrets.

## Production flow

1. Deploy the VM using [`deployment-gcp-free-tier.md`](deployment-gcp-free-tier.md).
2. SSH to the VM:

```bash
gcloud compute ssh poma-free-tier --zone us-west1-b --tunnel-through-iap
```

3. Configure VM-local IBC credentials:

```bash
sudo poma-configure-ibc
```

4. Approve broker mobile authentication when prompted.
5. Verify Gateway before paper/live mode:

```bash
sudo systemctl status ibgateway --no-pager
nc -z 127.0.0.1 7497 && echo "IB Gateway API socket is reachable"
```

6. Only after the socket is reachable, set `TRADING_MODE=paper` and redeploy.

## What is automated

The VM startup script installs and enables:

- IB Gateway in `/opt/ibgateway`.
- IBC in `/opt/ibc`.
- `ibgateway.service` under `systemd`.
- A headless display and localhost-only VNC for recovery.
- `/usr/local/bin/poma-configure-ibc` for the one required local credential setup.

The service starts raw IB Gateway until `/home/poma/ibc/config.ini` exists. After local setup, it starts Gateway through IBC as one foreground systemd process.

## Recovery commands

```bash
sudo systemctl restart ibgateway
sudo journalctl -u ibgateway -n 200 --no-pager
```

Open the Gateway GUI only through IAP tunnelling:

```bash
gcloud compute ssh poma-free-tier \
  --zone us-west1-b \
  --tunnel-through-iap \
  -- -L 5900:127.0.0.1:5900
```

Then connect a local VNC client to `127.0.0.1:5900`.

## Live trading gate

Do not switch to `TRADING_MODE=live` until:

- Paper mode has run successfully for at least one full trading week.
- `ALLOW_LIVE_TRADING=true` is set intentionally.
- Order size, turnover, daily trade count, and position caps are reviewed.
- The latest rebalance report is manually reviewed.

IBKR authentication can still require operator action for mobile approval, session reset, or account prompts. The repo supervises and restarts Gateway, but it does not bypass broker authentication requirements.
