#!/usr/bin/env python3
from __future__ import annotations

import sys

import pandas as pd

from poma.config import get_settings
from poma.data import build_data_client

MIN_EXPECTED_ROWS = 50
CURRENT_REQUIRED_COLUMNS = {"ticker", "market_cap", "price"}


def validate_snapshot(name: str, frame: pd.DataFrame, required_columns: set[str]) -> list[str]:
    errors: list[str] = []
    missing = required_columns - set(frame.columns)
    if missing:
        errors.append(f"{name} snapshot missing required columns: {sorted(missing)}")
        return errors

    if len(frame) < MIN_EXPECTED_ROWS:
        errors.append(f"{name} snapshot has only {len(frame)} rows")

    tickers = frame["ticker"].astype(str).str.strip()
    if frame["ticker"].isna().any() or (tickers == "").any():
        errors.append(f"{name} snapshot has empty tickers")

    if tickers.duplicated().any():
        errors.append(f"{name} snapshot has duplicate tickers")

    for column in sorted(required_columns - {"ticker"}):
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

    errors = validate_snapshot("current", current, CURRENT_REQUIRED_COLUMNS)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Data provider validation passed: current_rows={len(current)}")


if __name__ == "__main__":
    main()
