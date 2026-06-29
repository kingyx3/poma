from __future__ import annotations

import requests
from conftest import make_settings

from poma.notifications import send_alert


class _Resp:
    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None


def test_send_alert_prefixes_environment(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr("poma.notifications.requests.post", fake_post)
    send_alert(make_settings(APP_ENV="stg"), "portfolio updated — 3 orders")

    assert captured["json"]["text"].startswith("📈 POMA · STG\n")
    assert "portfolio updated" in captured["json"]["text"]


def test_send_alert_is_best_effort_on_failure(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise requests.RequestException("telegram down")

    monkeypatch.setattr("poma.notifications.requests.post", boom)
    # Must not raise: a Telegram outage cannot break a trading run.
    send_alert(make_settings(), "anything")
