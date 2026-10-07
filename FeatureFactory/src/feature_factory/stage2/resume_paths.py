from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def rewrite_stage2_path_references(
    value: Any,
    *,
    stage2_root: Path,
    destination_run_id: str,
) -> tuple[Any, bool]:
    stage2_root = stage2_root.expanduser().resolve()
    destination_run_id = str(destination_run_id or "").strip()
    if not destination_run_id:
        return value, False

    destination_runtime_dir = stage2_root / "runtime" / destination_run_id
    destination_workspace_dir = stage2_root / "runs" / destination_run_id
    runtime_pattern = re.compile(re.escape(str(stage2_root / "runtime")) + r"/[^/]+")
    workspace_pattern = re.compile(re.escape(str(stage2_root / "runs")) + r"/[^/]+")

    def _rewrite(item: Any) -> tuple[Any, bool]:
        if isinstance(item, str):
            rewritten = runtime_pattern.sub(str(destination_runtime_dir), item)
            rewritten = workspace_pattern.sub(str(destination_workspace_dir), rewritten)
            return rewritten, rewritten != item
        if isinstance(item, list):
            changed = False
            rewritten_items: list[Any] = []
            for child in item:
                rewritten_child, child_changed = _rewrite(child)
                rewritten_items.append(rewritten_child)
                changed = changed or child_changed
            return rewritten_items, changed
        if isinstance(item, dict):
            changed = False
            rewritten_dict: dict[str, Any] = {}
            for key, child in item.items():
                rewritten_child, child_changed = _rewrite(child)
                rewritten_dict[str(key)] = rewritten_child
                changed = changed or child_changed
            return rewritten_dict, changed
        return item, False

    return _rewrite(value)


def rewrite_stage2_json_file_path_references(
    path: Path,
    *,
    stage2_root: Path,
    destination_run_id: str,
) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rewritten_payload, changed = rewrite_stage2_path_references(
        payload,
        stage2_root=stage2_root,
        destination_run_id=destination_run_id,
    )
    if not changed:
        return False
    path.write_text(
        json.dumps(rewritten_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def rewrite_stage2_json_tree_path_references(
    root: Path,
    *,
    stage2_root: Path,
    destination_run_id: str,
) -> list[Path]:
    if not root.exists():
        return []
    rewritten_paths: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if rewrite_stage2_json_file_path_references(
            path,
            stage2_root=stage2_root,
            destination_run_id=destination_run_id,
        ):
            rewritten_paths.append(path)
    return rewritten_paths
