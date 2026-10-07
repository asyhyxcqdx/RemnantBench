from __future__ import annotations

from pathlib import Path


def read_text_asset(name: str) -> str:
    return (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")

