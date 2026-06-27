from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


def _example_keys(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and line[:1].isupper():
            key, value = line.split("=", 1)
            pairs[key] = value
    return pairs


def test_render_env_requires_all_keys_in_strict_mode(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("TELEGRAM_BOT_TOKEN", None)

    result = subprocess.run(
        [
            sys.executable,
            "ops/scripts/render_env.py",
            "--strict-env",
            "--output",
            str(tmp_path / ".env"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing environment variables" in result.stderr


def test_render_env_writes_all_example_keys_from_environment(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    values = _example_keys(repo_root / ".env.example")
    values.update(
        {
            "APP_ENV": "production",
            "IBKR_ACCOUNT": "U1234567",
            "TELEGRAM_BOT_TOKEN": "123456:telegram-token",
            "TELEGRAM_CHAT_ID": "-1001234567890",
        }
    )

    env = os.environ.copy()
    env.update(values)

    output_path = tmp_path / ".env"
    subprocess.run(
        [
            sys.executable,
            "ops/scripts/render_env.py",
            "--strict-env",
            "--output",
            str(output_path),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    rendered = output_path.read_text(encoding="utf-8")
    assert "APP_ENV=production" in rendered
    assert "IBKR_ACCOUNT=U1234567" in rendered
    assert "TELEGRAM_BOT_TOKEN=123456:telegram-token" in rendered
    assert "TELEGRAM_CHAT_ID=-1001234567890" in rendered
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
