from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RETRY_WAIT_STATUS = "retry_wait"
TERMINAL_STATUSES = {
    "completed",
    "completed_with_order_issues",
    "no_orders_accepted",
    "dry_run",
    "blocked",
    "failed",
}
ACTIVE_STATUSES = TERMINAL_STATUSES | {"running", RETRY_WAIT_STATUS}


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

    def session_run_id(self, session_date: str) -> str | None:
        payload = self._read()
        if payload.get("last_rebalance_session") != session_date:
            return None
        run_id = payload.get("last_rebalance_run_id")
        return str(run_id) if run_id else None

    def session_attempt_count(self, session_date: str) -> int:
        payload = self._read()
        if payload.get("last_rebalance_session") != session_date:
            return 0
        try:
            return int(payload.get("last_rebalance_attempt_count", 0))
        except (TypeError, ValueError):
            return 0

    def begin_session(self, session_date: str, run_id: str) -> int:
        """Mark one execution attempt running and return the durable same-run attempt number.

        A retryable outcome keeps the same ``run_id``. Preserving the original start timestamp
        and incrementing a durable attempt counter lets monitor bound automatic retries without
        weakening ExecutionManager's orderRef idempotency guarantees.
        """
        payload = self._read()
        same_run = (
            payload.get("last_rebalance_session") == session_date
            and payload.get("last_rebalance_run_id") == run_id
        )
        previous_attempts = self.session_attempt_count(session_date) if same_run else 0
        attempt_count = previous_attempts + 1
        payload["last_rebalance_session"] = session_date
        payload["last_rebalance_run_id"] = run_id
        payload["last_rebalance_status"] = "running"
        payload["last_rebalance_attempt_count"] = attempt_count
        if not same_run or not payload.get("last_rebalance_started_at"):
            payload["last_rebalance_started_at"] = _utc_now()
        payload["last_rebalance_attempt_started_at"] = _utc_now()
        self._write(payload)
        return attempt_count

    def mark_retry_wait(
        self,
        session_date: str,
        run_id: str,
        *,
        reason: str,
        report_path: str | None = None,
    ) -> None:
        payload = self._read()
        payload["last_rebalance_session"] = session_date
        payload["last_rebalance_run_id"] = run_id
        payload["last_rebalance_status"] = RETRY_WAIT_STATUS
        payload["last_rebalance_retry_reason"] = reason
        payload["last_rebalance_finished_at"] = _utc_now()
        if report_path:
            payload["last_rebalance_report_path"] = report_path
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
        payload.pop("last_rebalance_retry_reason", None)
        if report_path:
            payload["last_rebalance_report_path"] = report_path
        if error:
            payload["last_rebalance_error"] = error
        self._write(payload)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
