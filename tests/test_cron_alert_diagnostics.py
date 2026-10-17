from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTER = REPO_ROOT / "ops/cron/alert_diagnostics.py"


def _module():
    spec = importlib.util.spec_from_file_location("poma_cron_alert_diagnostics", REPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incident_classification_deduplication_and_verified_run_recovery(tmp_path, monkeypatch):
    reporter = _module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POMA_CRON_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APP_ENV", "dev")
    messages = []
    monkeypatch.setattr(reporter, "send_message", lambda message: messages.append(message) or True)
    (tmp_path / "state/orders").mkdir(parents=True)
    (tmp_path / "state/orders/open_orders.jsonl").write_text(
        json.dumps({"ledger_key": "key", "lifecycle_state": "unknown"}) + "\n"
    )
    logfile = tmp_path / "reconcile.log"
    logfile.write_text(
        "[2026-10-17T00:00:00Z] ConnectionRefusedError: Connect call failed\n"
        "[2026-10-17T00:00:00Z] password=do-not-send DU123456\n"
    )
    reporter.report(logfile, 1, "reconcile-orders", 0)
    assert len(messages) == 1
    assert "IBKR_CONNECTION_REFUSED" in messages[0]
    assert "Local unresolved ledger entries: 1; broker orders unverified" in messages[0]
    assert "do-not-send" not in messages[0]
    assert "DU123456" not in messages[0]
    reporter.report(logfile, 1, "reconcile-orders", 0)
    assert len(messages) == 1
    reporter.report(logfile, 1, "reconcile-orders", 0)
    assert len(messages) == 2
    reporter.report(logfile, 1, "reconcile-orders", 0)
    assert len(messages) == 2
    reporter.report(logfile, 0, "reconcile-orders", logfile.stat().st_size)
    assert len(messages) == 3
    assert "recovered" in messages[2]
    assert "does NOT independently certify" in messages[2]
    status = json.loads((tmp_path / "state/cron-alert-reconcile-orders.json").read_text())
    assert not status["active"]


def test_report_uses_only_current_command_output_and_safe_exception_types(tmp_path):
    reporter = _module()
    path = tmp_path / "cron.log"
    path.write_text("ConnectionRefusedError: stale older run\n")
    offset = path.stat().st_size
    with path.open("a") as stream:
        stream.write("ValueError: access_key=should-not-appear\n")
    category, _, signature = reporter.classify(reporter.recent_lines(path, offset))
    assert category == "APP_EXCEPTION"
    assert signature == "ValueError"
    assert "should-not-appear" not in reporter.scrub("access_key=should-not-appear")
    assert reporter.classify(["docker: Cannot connect to the Docker daemon"])[0] == "DOCKER_UNAVAILABLE"


def test_host_wrapper_retains_exit_code_and_records_incident(tmp_path):
    (tmp_path / "ops/cron").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "ops/cron/run_logged.sh", tmp_path / "ops/cron/run_logged.sh")
    shutil.copy(REPORTER, tmp_path / "ops/cron/alert_diagnostics.py")
    env = dict(os.environ)
    env["POMA_COMMAND_LOCK_PATH"] = str(tmp_path / "lock")
    env["POMA_CRON_ALERT_STATE_DIR"] = str(tmp_path / "state")
    logfile = tmp_path / "cron.log"
    command = ["bash", "ops/cron/run_logged.sh", str(logfile), "bash", "-c"]
    failed = subprocess.run(
        command + ["echo 'ConnectionRefusedError: Connect call failed'; exit 17"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert failed.returncode == 17
    assert "status=17" in logfile.read_text()
    status_path = tmp_path / "state/cron-alert-scheduled-command.json"
    assert status_path.exists()
    assert json.loads(status_path.read_text())["category"] == "IBKR_CONNECTION_REFUSED"
    passed = subprocess.run(
        command + ["echo success"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert passed.returncode == 0
    assert not json.loads(status_path.read_text())["active"]


def test_one_incident_survives_gateway_restart_error_category_transition(tmp_path, monkeypatch):
    reporter = _module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POMA_CRON_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APP_ENV", "dev")
    clock = [1000]
    monkeypatch.setattr(reporter.time, "time", lambda: clock[0])
    messages = []
    monkeypatch.setattr(reporter, "send_message", lambda msg: messages.append(msg) or True)
    path = tmp_path / "reconcile.log"
    path.write_text("BrokerUnavailable: gateway API handshake failed\n")
    reporter.report(path, 1, "reconcile-orders", 0)
    assert len(messages) == 1

    # Gateway restart changes the signature, not the underlying unresolved incident.
    clock[0] += 60
    path.write_text("ConnectionRefusedError: Connect call failed\n")
    reporter.report(path, 1, "reconcile-orders", 0)
    clock[0] += 60
    reporter.report(path, 1, "reconcile-orders", 0)
    state = json.loads((tmp_path / "state/cron-alert-reconcile-orders.json").read_text())
    assert state["count"] == 3
    assert state["first_failed_at"] == 1000
    assert state["category"] == "IBKR_CONNECTION_REFUSED"
    assert "Consecutive failures: 3" in messages[-1]

    clock[0] += 60
    reporter.report(path, 0, "reconcile-orders", 0)
    assert "Previous failures: 3" in messages[-1]
    assert "Incident duration: 3 min" in messages[-1]
