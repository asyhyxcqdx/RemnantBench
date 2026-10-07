#!/usr/bin/env python3
"""Verify the frozen RemnantBench dataset or its download archive."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.archive:
        expected = json.loads((ROOT / "environment/data-archive.json").read_text())
        if sha256(args.archive) != expected["sha256"]:
            raise SystemExit("Dataset archive checksum mismatch")
        print("Dataset archive verified")
        return
    expected = json.loads((ROOT / "dataset-checksums.json").read_text())
    failed = [
        name
        for name, digest in expected.items()
        if not (args.root / name).is_file() or sha256(args.root / name) != digest
    ]
    if failed:
        raise SystemExit("Missing or changed frozen files:\n" + "\n".join(failed))
    print(f"Verified {len(expected)} frozen dataset files")


if __name__ == "__main__":
    main()
