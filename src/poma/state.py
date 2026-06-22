from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

    def has_rebalanced(self, session_date: str) -> bool:
        return self._read().get("last_rebalance_session") == session_date

    def mark_rebalanced(self, session_date: str, run_id: str) -> None:
        payload = self._read()
        payload["last_rebalance_session"] = session_date
        payload["last_rebalance_run_id"] = run_id
        self._write(payload)
