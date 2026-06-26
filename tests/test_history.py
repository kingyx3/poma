from __future__ import annotations

from datetime import date

import pandas as pd

from poma.history import CapSnapshotHistory


def test_save_and_load_asof_returns_latest_prior_snapshot(tmp_path) -> None:
    history = CapSnapshotHistory(tmp_path)
    history.save(
        pd.DataFrame([{"ticker": "AAPL", "market_cap": 100, "price": 1.0}]),
        date(2026, 3, 1),
    )
    history.save(
        pd.DataFrame([{"ticker": "AAPL", "market_cap": 200, "price": 2.0}]),
        date(2026, 3, 20),
    )

    # Only ticker + market_cap are persisted (price is not part of the history).
    saved = pd.read_csv(tmp_path / "cap_history" / "2026-03-20.csv")
    assert list(saved.columns) == ["ticker", "market_cap"]

    asof = history.load_asof(date(2026, 3, 25))
    assert asof is not None
    assert int(asof.set_index("ticker").loc["AAPL", "market_cap"]) == 200


def test_load_asof_returns_none_before_any_snapshot(tmp_path) -> None:
    history = CapSnapshotHistory(tmp_path)
    assert history.load_asof(date(2026, 3, 25)) is None
    history.save(pd.DataFrame([{"ticker": "AAPL", "market_cap": 100}]), date(2026, 3, 20))
    assert history.load_asof(date(2026, 3, 1)) is None
