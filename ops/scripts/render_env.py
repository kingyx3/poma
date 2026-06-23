#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path

ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

ALLOW_EMPTY = {"FMP_API_KEY", "IBKR_ACCOUNT"}
PLACEHOLDER_VALUES = {"replace_me", "changeme", "todo"}


def parse_env_example(path: Path) -> list[tuple[str | None, str]]:
    entries: list[tuple[str | None, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = ENV_LINE_RE.match(raw_line)
        if match:
            entries.append((match.group(1), match.group(2)))
        else:
            entries.append((None, raw_line))
    return entries


def format_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("environment variable values must be single-line")
    if value == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,\-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_env_lines(entries: list[tuple[str | None, str]], strict_env: bool) -> list[str]:
    output: list[str] = []
    resolved: dict[str, str] = {}
    missing: list[str] = []

    for key, raw_line in entries:
        if key is None:
            output.append(raw_line)
            continue

        if strict_env and key not in os.environ:
            missing.append(key)

        value = os.environ.get(key, raw_line)
        resolved[key] = value
        output.append(f"{key}={format_env_value(value)}")

    errors = []
    if missing:
        errors.append("missing environment variables: " + ", ".join(sorted(missing)))

    for key, value in resolved.items():
        if key in ALLOW_EMPTY:
            continue
        if value.strip() == "":
            errors.append(f"{key} must not be empty")
        if value.strip().lower() in PLACEHOLDER_VALUES:
            errors.append(f"{key} still uses placeholder value {value!r}")

    if resolved.get("DATA_PROVIDER") == "fmp" and not resolved.get("FMP_API_KEY"):
        errors.append("FMP_API_KEY is required when DATA_PROVIDER=fmp")

    if resolved.get("TRADING_MODE") in {"paper", "live"} and not resolved.get("IBKR_ACCOUNT"):
        errors.append("IBKR_ACCOUNT is required when TRADING_MODE is paper or live")

    if errors:
        raise SystemExit("; ".join(errors))

    return output


def render_env(example_path: Path, output_path: Path, strict_env: bool) -> None:
    entries = parse_env_example(example_path)
    output = build_env_lines(entries, strict_env=strict_env)
    output_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    output_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a POMA .env file from process env values.")
    parser.add_argument("--example", default=".env.example", type=Path)
    parser.add_argument("--output", default=".env", type=Path)
    parser.add_argument(
        "--strict-env",
        action="store_true",
        help="Require every key in .env.example to be present in the process environment.",
    )
    args = parser.parse_args()

    render_env(args.example, args.output, strict_env=args.strict_env)


if __name__ == "__main__":
    main()
