#!/usr/bin/env python3
"""Compatibility wrapper for Harbor job report generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


repo_root = Path(__file__).resolve().parents[1]
report_module_path = repo_root / "harbor" / "src" / "harbor" / "report.py"

if not report_module_path.exists():
    raise SystemExit(f"Could not find Harbor report module: {report_module_path}")

spec = importlib.util.spec_from_file_location("harbor_report", report_module_path)
if spec is None or spec.loader is None:
    raise SystemExit(f"Could not load Harbor report module: {report_module_path}")
harbor_report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = harbor_report
spec.loader.exec_module(harbor_report)


if __name__ == "__main__":
    raise SystemExit(harbor_report.main())