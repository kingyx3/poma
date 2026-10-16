from __future__ import annotations

import json

import requests
from conftest import make_settings

from poma.notifications import send_alert
from poma.telegram_outbox import MAX_MESSAGES, TTL_SECONDS, drain, enqueue


def test_outbox_retries_only_after_due_and_removes_success(tmp_path, monkeypatch):
    monkeypatch.setattr("poma.telegram_outbox.random.uniform", lambda _a, _b: 1.0)
    assert enqueue(tmp_path, "📈 POMA · DEV\nfailed notification", now=1000)
    sent = []
    assert drain(tmp_path, lambda text: sent.append(text) or False, now=1059) == (0, 0)
    assert drain(tmp_path, lambda text: sent.append(text) or False, now=1060) == (0, 1)
    assert len(sent) == 1
    files = list((tmp_path / "telegram-outbox").glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["attempts"] == 1
    assert record["next_attempt_at"] == 1180
    assert drain(tmp_path, lambda text: sent.append(text) or True, now=1180) == (1, 1)
    assert len(sent) == 2
    assert list((tmp_path / "telegram-outbox").glob("*.json")) == []


def test_outbox_limits_size_batch_and_expiry(tmp_path):
    for i in range(MAX_MESSAGES):
        assert enqueue(tmp_path, f"message {i}", now=1000 + i * 0.001)
    assert not enqueue(tmp_path, "do-not-overflow", now=1001)
    messages = []
    assert drain(tmp_path, lambda msg: messages.append(msg) or True, now=1200, batch_size=3) == (3, 3)
    assert len(messages) == 3
    assert drain(tmp_path, lambda _msg: True, now=1000 + TTL_SECONDS + 10) == (0, 0)
    assert list((tmp_path / "telegram-outbox").glob("*.json")) == []


def test_failed_in_app_alert_is_durable_but_does_not_break_order(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("telegram unavailable")

    monkeypatch.setattr("poma.notifications.requests.post", fail)
    send_alert(make_settings(APP_ENV="dev", STATE_DIR=tmp_path), "Order state unverified")
    files = list((tmp_path / "telegram-outbox").glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["text"].startswith("📈 POMA · DEV")
    assert "Order state unverified" in record["text"]
    assert "test-token" not in files[0].read_text()


def test_outbox_delivery_failure_does_not_expose_credentials(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    reporter_path = Path(__file__).resolve().parents[1] / "ops/cron/alert_diagnostics.py"
    spec = importlib.util.spec_from_file_location("retry_cron_alert", reporter_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POMA_CRON_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "secret-chat")

    def fail(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(module.urllib.request, "urlopen", fail)
    assert not module.send_message("Only safe message text")
    files = list((tmp_path / "state/telegram-outbox").glob("*.json"))
    assert len(files) == 1
    payload = files[0].read_text()
    assert "secret-bot-token" not in payload
    assert "secret-chat" not in payload
    assert "Only safe message text" in payload
