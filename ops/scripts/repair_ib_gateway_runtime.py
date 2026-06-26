#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

APP_USER = "poma"
IB_GATEWAY_DIR = Path("/opt/ibgateway")
IB_GATEWAY_INSTALLER_URL = "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
IBC_VERSION = "3.24.0"
IBC_DIR = Path("/opt/ibc")
IBC_ZIP_URL = f"https://github.com/IbcAlpha/IBC/releases/download/{IBC_VERSION}/IBCLinux-{IBC_VERSION}.zip"
IB_GATEWAY_RUNTIME_DIR = Path("/run/poma-ibgateway")
IB_GATEWAY_LOG_DIR = Path("/var/log/poma/ibgateway")
LEGACY_RUNTIME_DIR = Path("/tmp/poma-ibgateway")

APT_PACKAGES = (
    "ca-certificates",
    "curl",
    "fluxbox",
    "netcat-openbsd",
    "openjdk-17-jre-headless",
    "procps",
    "unzip",
    "x11vnc",
    "xterm",
    "xvfb",
)
REQUIRED_COMMAND_PACKAGES = {
    "Xvfb": "xvfb",
    "curl": "curl",
    "fluxbox": "fluxbox",
    "java": "openjdk-17-jre-headless",
    "nc": "netcat-openbsd",
    "unzip": "unzip",
    "x11vnc": "x11vnc",
}


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def stop_gateway_service() -> None:
    run(["systemctl", "stop", "ibgateway"], check=False)


def ensure_app_user() -> None:
    if run(["id", APP_USER], check=False).returncode == 0:
        return
    run(["useradd", "--create-home", "--shell", "/bin/bash", APP_USER])


def chown_recursive(path: Path) -> None:
    if path.exists():
        run(["chown", "-R", f"{APP_USER}:{APP_USER}", str(path)])


def ensure_runtime_packages() -> None:
    missing_packages = sorted(
        {
            package
            for command, package in REQUIRED_COMMAND_PACKAGES.items()
            if shutil.which(command) is None
        }
    )
    if not missing_packages:
        return

    print(
        "Installing missing IB Gateway runtime packages: "
        + ", ".join(missing_packages)
    )
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", *APT_PACKAGES])


def ensure_runtime_dirs() -> None:
    for path, mode in (
        (Path("/home/poma/Jts"), 0o700),
        (Path("/home/poma/ibc/logs"), 0o700),
        (IB_GATEWAY_RUNTIME_DIR, 0o700),
        (IB_GATEWAY_LOG_DIR, 0o750),
        (LEGACY_RUNTIME_DIR, 0o700),
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        chown_recursive(path)

    chown_recursive(Path("/home/poma/ibc"))


def find_gateway_executable() -> Path | None:
    candidates = [
        path
        for path in IB_GATEWAY_DIR.glob("**/ibgateway")
        if path.is_file() and os.access(path, os.X_OK)
    ]
    return sorted(candidates)[-1] if candidates else None


def find_gateway_jars_dirs() -> list[Path]:
    return sorted(
        path
        for path in IB_GATEWAY_DIR.glob("**/jars")
        if path.is_dir() and any(path.glob("*.jar"))
    )


def has_gateway_artifacts() -> bool:
    return find_gateway_executable() is not None or bool(find_gateway_jars_dirs())


def ensure_ib_gateway_installed() -> None:
    if has_gateway_artifacts():
        chown_recursive(IB_GATEWAY_DIR)
        return
    if IB_GATEWAY_DIR.exists():
        shutil.rmtree(IB_GATEWAY_DIR)

    print(f"Installing IB Gateway into {IB_GATEWAY_DIR}")
    IB_GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    fd, installer_name = tempfile.mkstemp(
        prefix="ibgateway-installer.", suffix=".sh", dir="/tmp"
    )
    os.close(fd)
    installer = Path(installer_name)
    try:
        run(["curl", "-fsSL", IB_GATEWAY_INSTALLER_URL, "-o", str(installer)])
        installer.chmod(0o700)
        run(["bash", str(installer), "-q", "-dir", str(IB_GATEWAY_DIR)])
    finally:
        installer.unlink(missing_ok=True)

    if not has_gateway_artifacts():
        raise RuntimeError(
            "IB Gateway installer completed but no executable or jars were found under "
            f"{IB_GATEWAY_DIR}"
        )
    chown_recursive(IB_GATEWAY_DIR)


def ensure_ibc_installed() -> None:
    gatewaystart = IBC_DIR / "gatewaystart.sh"
    if gatewaystart.exists():
        chown_recursive(IBC_DIR)
        return

    print(f"Installing IBC {IBC_VERSION} into {IBC_DIR}")
    shutil.rmtree(IBC_DIR, ignore_errors=True)
    IBC_DIR.mkdir(parents=True, exist_ok=True)
    fd, zip_name = tempfile.mkstemp(prefix="ibc.", suffix=".zip", dir="/tmp")
    os.close(fd)
    ibc_zip = Path(zip_name)
    try:
        run(["curl", "-fsSL", IBC_ZIP_URL, "-o", str(ibc_zip)])
        run(["unzip", "-q", str(ibc_zip), "-d", str(IBC_DIR)])
    finally:
        ibc_zip.unlink(missing_ok=True)

    for script in list(IBC_DIR.glob("*.sh")) + list((IBC_DIR / "scripts").glob("*.sh")):
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if not gatewaystart.exists():
        raise RuntimeError(f"IBC installer completed but {gatewaystart} is still missing")
    chown_recursive(IBC_DIR)


def main() -> int:
    stop_gateway_service()
    ensure_runtime_packages()
    ensure_app_user()
    ensure_runtime_dirs()
    ensure_ib_gateway_installed()
    ensure_ibc_installed()
    print("IB Gateway runtime artifacts, packages, and directories are repaired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
