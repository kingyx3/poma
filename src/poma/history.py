from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class CapSnapshotHistory:
    """Append-only daily store of (ticker, market_cap) snapshots under the state directory.

    Each rebalance persists that day's current snapshot. This accumulates the point-in-time
    market-cap history needed to reintroduce a rank-improvement tilt later, without any
    per-run historical API calls (which FMP only exposes per-symbol and would rate-limit).
    """

    def __init__(self, base_dir: Path) -> None:
        self.dir = Path(base_dir) / "cap_history"

    def save(self, snapshot: pd.DataFrame, as_of: date) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        columns = [column for column in ("ticker", "market_cap") if column in snapshot.columns]
        path = self.dir / f"{as_of.isoformat()}.csv"
        snapshot[columns].to_csv(path, index=False)
        return path

    def load_asof(self, target: date) -> pd.DataFrame | None:
        """Return the most recent stored snapshot on or before `target`, or None if absent."""
        if not self.dir.exists():
            return None
        cutoff = target.isoformat()
        candidates = sorted(p for p in self.dir.glob("*.csv") if p.stem <= cutoff)
        if not candidates:
            return None
        return pd.read_csv(candidates[-1])
