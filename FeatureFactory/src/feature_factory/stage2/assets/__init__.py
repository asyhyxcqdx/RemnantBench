from __future__ import annotations

from pathlib import Path

_ASSET_ROOT = Path(__file__).resolve().parent


def asset_root() -> Path:
    return _ASSET_ROOT


def read_text_asset(relative_path: str) -> str:
    return (_ASSET_ROOT / relative_path).read_text(encoding="utf-8")


__all__ = ["asset_root", "read_text_asset"]
