from __future__ import annotations

from pathlib import Path


def test_cron_runs_app_commands_through_timestamped_logger() -> None:
    cron = Path("ops/cron/poma.cron").read_text()

    assert "bash ops/cron/run_logged.sh /opt/poma/logs/poma-cron.log" in cron
    assert "bash ops/cron/run_logged.sh /opt/poma/logs/poma-reconcile-cron.log" in cron
    assert ">> /opt/poma/logs" not in cron


def test_timestamped_logger_marks_lines_and_alerts_on_failures() -> None:
    script = Path("ops/cron/run_logged.sh").read_text()

    assert "date -u" in script
    assert "%Y-%m-%dT%H:%M:%SZ" in script
    assert "append_timestamped_output" in script
    assert "send_failure_alert" in script
    assert "Scheduled command failed" in script
    assert "TELEGRAM_BOT_TOKEN" in script
    assert "TELEGRAM_CHAT_ID" in script
