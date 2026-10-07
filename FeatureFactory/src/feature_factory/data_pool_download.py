from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a FeatureFactory data pool zip archive")
    parser.add_argument("--pool-id", required=True, help="Data pool id")
    parser.add_argument(
        "--selection-id",
        default="",
        help="Server-side download selection id. Preferred over listing many --asset-id values.",
    )
    parser.add_argument(
        "--asset-id",
        action="append",
        dest="asset_ids",
        default=[],
        help="Selected asset id. Repeat to download multiple assets.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="FeatureFactory admin base URL, for example http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output zip path. Defaults to the server-provided filename in the current directory.",
    )
    return parser.parse_args()


def _filename_from_headers(response: httpx.Response, fallback: str) -> str:
    content_disposition = str(response.headers.get("content-disposition") or "")
    filename_match = re.search(r'filename="([^"]+)"', content_disposition)
    if filename_match:
        value = filename_match.group(1).strip()
        if value:
            return value
    filename_star_match = re.search(r"filename\\*=UTF-8''([^;]+)", content_disposition)
    if filename_star_match:
        value = filename_star_match.group(1).strip()
        if value:
            return value
    return fallback


def main() -> int:
    args = _parse_args()
    base_url = str(args.base_url or "").rstrip("/")
    pool_id = str(args.pool_id or "").strip()
    if not base_url:
        raise SystemExit("--base-url is required")
    if not pool_id:
        raise SystemExit("--pool-id is required")
    selection_id = str(args.selection_id or "").strip()
    asset_ids = [str(asset_id or "").strip() for asset_id in list(args.asset_ids or []) if str(asset_id or "").strip()]
    url = f"{base_url}/api/data-pools/{quote(pool_id, safe='')}/download.zip"
    request_kwargs: dict[str, object] = {}
    method = "GET"
    if selection_id:
        url = f"{base_url}/api/data-pools/{quote(pool_id, safe='')}/download-selections/{quote(selection_id, safe='')}.zip"
    elif asset_ids:
        url = f"{base_url}/api/data-pools/{quote(pool_id, safe='')}/download-selection.zip"
        method = "POST"
        request_kwargs["json"] = {"asset_ids": asset_ids}
    out_arg = str(args.out or "").strip()
    with httpx.stream(method, url, follow_redirects=True, timeout=None, **request_kwargs) as response:
        response.raise_for_status()
        if not out_arg:
            default_name = _filename_from_headers(response, f"data-pool-{pool_id}.zip")
            target_path = Path.cwd() / default_name
        else:
            target_path = Path(out_arg).expanduser()
            if not target_path.is_absolute():
                target_path = Path.cwd() / target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as handle:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                handle.write(chunk)
    print(str(target_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
