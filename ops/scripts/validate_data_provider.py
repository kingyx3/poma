#!/usr/bin/env python3
from __future__ import annotations

import sys

import pandas as pd

from poma.config import get_settings
from poma.data import build_data_client

REQUIRED_COLUMNS = {"ticker", "market_cap", "price"}
MIN_EXPECTED_ROWS = 50


def validate_snapshot(name: str, frame: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        errors.append(f"{name} snapshot missing required columns: {sorted(missing)}")
        return errors

    if len(frame) < MIN_EXPECTED_ROWS:
        errors.append(f"{name} snapshot has only {len(frame)} rows")

    if frame["ticker"].isna().any() or (frame["ticker"].astype(str).str.strip() == "").any():
        errors.append(f"{name} snapshot has empty tickers")

    if frame["ticker"].duplicated().any():
        errors.append(f"{name} snapshot has duplicate tickers")

    for column in ["market_cap", "price"]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any():
            errors.append(f"{name} snapshot has non-numeric {column} values")
        if (numeric <= 0).any():
            errors.append(f"{name} snapshot has non-positive {column} values")

    return errors


def main() -> None:
    settings = get_settings()
    if settings.data_provider == "fixture":
        print("DATA_PROVIDER=fixture; external provider validation skipped")
        return

    client = build_data_client(settings)
    current = client.current_universe_snapshot()
    previous = client.previous_universe_snapshot(settings.rank_lookback_days)

    errors = []
    errors.extend(validate_snapshot("current", current))
    errors.extend(validate_snapshot("previous", previous))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "Data provider validation passed: "
        f"current_rows={len(current)} previous_rows={len(previous)}"
    )


if __name__ == "__main__":
    main()
