from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_SCRIPT = REPO_ROOT / "infra/gcp-free-tier/startup.sh"

# The startup script is intentionally minimal host prep so cloud-init finishes quickly.
REQUIRED_STARTUP_SNIPPETS = (
    "apt-get install -y --no-install-recommends ca-certificates cron curl python3",
    "curl -fsSL https://get.docker.com | sh",
    "rm -rf /var/lib/apt/lists/*",
    'READY_SENTINEL="$${READY_DIR}/vm-ready"',
    'FAILED_SENTINEL="$${READY_DIR}/vm-startup-failed"',
    "record_startup_failure()",
    "trap record_startup_failure EXIT",
    'rm -f "$${READY_SENTINEL}" "$${FAILED_SENTINEL}"',
    '$(cat /proc/sys/kernel/random/boot_id)',
    'chmod 0644 "$${READY_SENTINEL}"',
    # Swap keeps the 1 GB e2-micro from OOM-wedging under IB Gateway + Docker + the app.
    "mkswap /swapfile",
    "swapon /swapfile",
    'APP_UID="${app_uid}"',
    'APP_GID="${app_gid}"',
    'groupadd --non-unique --gid "$${APP_GID}" "$${APP_USER}"',
    'groupadd --gid "$${APP_GID}" "$${APP_USER}"',
    'useradd --non-unique --uid "$${APP_UID}" --gid "$${APP_GID}" --create-home --shell /bin/bash "$${APP_USER}"',
    'useradd --uid "$${APP_UID}" --gid "$${APP_GID}" --create-home --shell /bin/bash "$${APP_USER}"',
    'must use uid=$${APP_UID} gid=$${APP_GID}',
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
# IB Gateway Ops workflow handles after relevant Auto CI/CD deploys or explicit manual dispatch.
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

    assert script.index('useradd --uid "$${APP_UID}"') < script.index("apt-get update")


def test_gcp_startup_script_does_not_install_ib_gateway() -> None:
    script = STARTUP_SCRIPT.read_text(encoding="utf-8")

    for snippet in FORBIDDEN_STARTUP_SNIPPETS:
        assert snippet not in script, snippet
