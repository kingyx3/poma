from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

SNAPSHOT_COLUMNS = [
    "ticker",
    "name",
    "exchange",
    "market_cap",
    "market_cap_rank",
    "price",
    "float_shares",
    "shares_outstanding",
    "source",
    "as_of",
]


class CapSnapshotHistory:
    """Daily file store for provider snapshots and computed market-cap ranks."""

    def __init__(self, base_dir: Path) -> None:
        self.dir = Path(base_dir) / "market_snapshots"
        self.legacy_dir = Path(base_dir) / "cap_history"

    def save(self, snapshot: pd.DataFrame, as_of: date) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        frame = snapshot.copy()
        frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
        frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
        frame = frame.dropna(subset=["ticker", "market_cap"])
        frame = frame[frame["market_cap"] > 0]
        if "market_cap_rank" not in frame.columns:
            frame["market_cap_rank"] = (
                frame["market_cap"].rank(ascending=False, method="first").astype(int)
            )
        frame["as_of"] = as_of.isoformat()
        columns = [column for column in SNAPSHOT_COLUMNS if column in frame.columns]
        path = self.dir / f"{as_of.isoformat()}.csv"
        frame[columns].sort_values("market_cap_rank").to_csv(path, index=False)
        return path

    def save_many(self, snapshots: dict[date, pd.DataFrame]) -> list[Path]:
        return [self.save(snapshot, as_of) for as_of, snapshot in sorted(snapshots.items())]

    def load_asof(self, target: date) -> pd.DataFrame | None:
        """Return the most recent stored snapshot on or before `target`, or None if absent."""
        candidates = self._candidate_paths(target)
        if not candidates:
            return None
        return pd.read_csv(candidates[-1])

    def _candidate_paths(self, target: date) -> list[Path]:
        cutoff = target.isoformat()
        paths: list[Path] = []
        for directory in [self.legacy_dir, self.dir]:
            if directory.exists():
                paths.extend(p for p in directory.glob("*.csv") if p.stem <= cutoff)
        return sorted(paths)
