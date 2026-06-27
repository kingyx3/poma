from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "infra/gcp-free-tier/startup.sh"

# The startup script is intentionally minimal host prep so cloud-init finishes quickly.
REQUIRED_STARTUP_SNIPPETS = (
    "apt-get install -y --no-install-recommends ca-certificates cron curl python3",
    "curl -fsSL https://get.docker.com | sh",
    "rm -rf /var/lib/apt/lists/*",
    'READY_SENTINEL="$${READY_DIR}/vm-ready"',
    'rm -f "$${READY_SENTINEL}"',
    '$(cat /proc/sys/kernel/random/boot_id)',
    'chmod 0644 "$${READY_SENTINEL}"',
    # Swap keeps the 1 GB e2-micro from OOM-wedging under IB Gateway + Docker + the app.
    "mkswap /swapfile",
    "swapon /swapfile",
    'useradd --create-home --shell /bin/bash "$${APP_USER}"',
    'usermod -aG docker "$${APP_USER}"',
    'mkdir -p \\',
    '"$${APP_DIR}/data"',
    "systemctl enable --now docker",
    "systemctl enable --now cron",
    "systemctl is-active --quiet docker",
    "systemctl is-active --quiet cron",
)

# Heavy IB Gateway provisioning must NOT live in the boot path: it stalled cloud-init and
# duplicated ops/scripts/repair_ib_gateway_runtime.py + install_ibc_config_helper.py, which the
# IB Gateway Ops workflow runs after every deploy.
FORBIDDEN_STARTUP_SNIPPETS = (
    "ibgateway.service",
    "poma-run-ib-gateway",
    "poma-configure-ibc",
    "IB_GATEWAY_INSTALLER_URL",
    "gatewaystart.sh",
    "Xvfb",
    "x11vnc",
)


def test_gcp_startup_script_is_minimal_host_bootstrap() -> None:
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")

    for snippet in REQUIRED_STARTUP_SNIPPETS:
        assert snippet in script, snippet


def test_gcp_startup_script_does_not_install_ib_gateway() -> None:
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")

    for snippet in FORBIDDEN_STARTUP_SNIPPETS:
        assert snippet not in script, snippet
