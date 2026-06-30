from __future__ import annotations

import importlib


def load() -> None:
    importlib.import_module("sitecustomize")
