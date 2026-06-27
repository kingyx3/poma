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

    saved = pd.read_csv(tmp_path / "market_snapshots" / "2026-03-20.csv")
    assert "ticker" in saved.columns
    assert "market_cap" in saved.columns
    assert "price" in saved.columns
    assert "market_cap_rank" in saved.columns

    asof = history.load_asof(date(2026, 3, 25))
    assert asof is not None
    assert int(asof.set_index("ticker").loc["AAPL", "market_cap"]) == 200


def test_save_many_persists_sorted_snapshot_dates(tmp_path) -> None:
    history = CapSnapshotHistory(tmp_path)
    paths = history.save_many(
        {
            date(2026, 3, 20): pd.DataFrame([{"ticker": "AAPL", "market_cap": 200}]),
            date(2026, 3, 1): pd.DataFrame([{"ticker": "AAPL", "market_cap": 100}]),
        }
    )
    assert [path.name for path in paths] == ["2026-03-01.csv", "2026-03-20.csv"]


def test_load_asof_returns_none_before_any_snapshot(tmp_path) -> None:
    history = CapSnapshotHistory(tmp_path)
    assert history.load_asof(date(2026, 3, 25)) is None
    history.save(pd.DataFrame([{"ticker": "AAPL", "market_cap": 100}]), date(2026, 3, 20))
    assert history.load_asof(date(2026, 3, 1)) is None
