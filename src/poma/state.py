from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"completed", "completed_with_order_issues", "dry_run", "blocked", "failed"}
ACTIVE_STATUSES = TERMINAL_STATUSES | {"running"}


class LocalState:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "rebalance_state.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write(self, payload: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def session_status(self, session_date: str) -> str | None:
        payload = self._read()
        if payload.get("last_rebalance_session") != session_date:
            return None
        status = payload.get("last_rebalance_status")
        return str(status) if status else None

    def has_session_attempt(self, session_date: str) -> bool:
        status = self.session_status(session_date)
        return status in ACTIVE_STATUSES

    def begin_session(self, session_date: str, run_id: str) -> None:
        payload = self._read()
        payload["last_rebalance_session"] = session_date
        payload["last_rebalance_run_id"] = run_id
        payload["last_rebalance_status"] = "running"
        payload["last_rebalance_started_at"] = _utc_now()
        self._write(payload)

    def mark_session(
        self,
        session_date: str,
        run_id: str,
        status: str,
        report_path: str | None = None,
        error: str | None = None,
    ) -> None:
        payload = self._read()
        payload["last_rebalance_session"] = session_date
        payload["last_rebalance_run_id"] = run_id
        payload["last_rebalance_status"] = status
        payload["last_rebalance_finished_at"] = _utc_now()
        if report_path:
            payload["last_rebalance_report_path"] = report_path
        if error:
            payload["last_rebalance_error"] = error
        self._write(payload)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
