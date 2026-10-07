#!/usr/bin/env python3
"""Verify or import the frozen evaluation images downloaded from ModelScope."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_images(root: Path = ROOT) -> None:
    images = json.loads((root / "environment/docker-images.json").read_text())
    failures = []
    for image in images:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image["tag"]],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode or result.stdout.strip() != image["id"]:
            failures.append(image["tag"])
    if failures:
        raise ValueError("Missing or different Docker images:\n" + "\n".join(failures))
    print(f"Verified {len(images)} frozen Docker images")


def verify_parts(directory: Path, root: Path = ROOT) -> list[Path]:
    manifest = json.loads((root / "environment/image-archive.json").read_text())
    paths = []
    for part in manifest["parts"]:
        path = directory / part["file"]
        if not path.is_file() or path.stat().st_size != part["bytes"]:
            raise ValueError(f"Missing or truncated image part: {path}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != part["sha256"]:
            raise ValueError(f"Image part checksum mismatch: {path}")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "verify", "load"))
    parser.add_argument("--directory", type=Path, default=ROOT / "downloads")
    args = parser.parse_args()
    try:
        if args.action == "check":
            check_images()
            return
        paths = verify_parts(args.directory)
        print(f"Verified {len(paths)} image archive parts", flush=True)
        if args.action == "verify":
            return
        # Docker detects gzip itself. Feed all parts in manifest order without
        # creating an extra decompressed archive on disk.
        process = subprocess.Popen(["docker", "load"], stdin=subprocess.PIPE)
        if process.stdin is None:
            raise RuntimeError("Docker load has no input stream")
        try:
            for path in paths:
                with path.open("rb") as stream:
                    while chunk := stream.read(8 * 1024**2):
                        process.stdin.write(chunk)
        finally:
            process.stdin.close()
            status = process.wait()
        if status:
            raise SystemExit(status)
        check_images()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
