from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import keyword
import math
import textwrap
import tokenize
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

GENERIC_TAIL_NAMES = {"main"}
NONPRODUCTION_SEGMENTS = {
    "_test",
    "_testing",
    "_tests",
    "_vendor",
    "atest",
    "bench",
    "benchmark",
    "benchmarks",
    "demo",
    "demos",
    "doc",
    "docs",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "migrations",
    "sample",
    "samples",
    "test",
    "test_repos",
    "test_utils",
    "testresources",
    "tests",
    "testdata",
    "testing",
    "testsuite",
    "third_party",
    "thirdparty",
    "tutorial",
    "tutorials",
    "unittest",
    "unittests",
    "vendor",
    "vendored",
}
EXPLICIT_GENERATED_SEGMENTS = {
    "autogen",
    "auto_generated",
    "generated",
}
SOURCE_ROOT_SEGMENTS = {"lib", "python", "src"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze physical dispersion and parallel-code signals in pairs."
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Run label and directory. Repeat for multiple runs.",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    run_summaries: dict[str, Any] = {}
    for spec in args.run:
        label, separator, raw_path = spec.partition("=")
        if not separator or not label or not raw_path:
            parser.error(f"invalid --run value: {spec!r}; expected LABEL=PATH")
        run_dir = Path(raw_path).expanduser().resolve()
        rows = analyze_run(label, run_dir)
        all_rows.extend(rows)
        run_summaries[label] = summarize_rows(rows)

    fieldnames = [
        "run_label",
        "pair_id",
        "full_name",
        "offset",
        "reinsert_source_lines",
        "implementation_units_count",
        "removed_object_targets_count",
        "removed_range_targets_count",
        "target_files_count",
        "parent_dirs_count",
        "largest_file_target_share_pct",
        "largest_file_line_share_pct",
        "repeated_tail_name_share_pct",
        "repeated_non_generic_tail_name_share_pct",
        "max_same_tail_name_count",
        "repeated_basename_file_share_pct",
        "exact_duplicate_target_share_pct",
        "structural_duplicate_target_share_pct",
        "parallel_target_share_pct",
        "parallel_line_share_pct",
        "nonproduction_target_share_pct",
        "nonproduction_line_share_pct",
        "explicit_generated_target_share_pct",
        "production_target_files_count",
        "production_domain_count",
        "exact_patch_group_size",
        "classification",
        "classification_reasons",
        "semantic_classification",
        "semantic_classification_reasons",
        "artifact_dir",
    ]
    write_csv(output_dir / "pair_dispersion_metrics.csv", all_rows, fieldnames)
    for classification in (
        "high_confidence_distributed",
        "high_confidence_parallel",
        "mixed_or_uncertain",
    ):
        write_csv(
            output_dir / f"{classification}.csv",
            [row for row in all_rows if row["classification"] == classification],
            fieldnames,
        )
    for classification in (
        "desired_distributed",
        "parallel_dominant",
        "parallel_mixed",
        "nonproduction_mixed",
        "concentrated_or_uncertain",
    ):
        write_csv(
            output_dir / f"{classification}.csv",
            [
                row
                for row in all_rows
                if row["semantic_classification"] == classification
            ],
            fieldnames,
        )

    summary = {
        "method": {
            "exact_duplicate_target": (
                "At least 3 nonblank lines; normalized source text is identical "
                "across different paths in the same pair."
            ),
            "structural_duplicate_target": (
                "At least 3 nonblank lines and 15 Python tokens; qualified-name "
                "tail and normalized token structure match across different paths. "
                "Identifiers and literal values are ignored."
            ),
            "classification": (
                "A conservative structural heuristic for audit prioritization, not "
                "a semantic ground-truth label."
            ),
            "semantic_classification": (
                "A stricter operational benchmark audit. It includes short "
                "cross-file duplicates, repeated basename+qualified-name families, "
                "non-production paths, and coarse production-module domains. "
                "Mixed rows still require human review."
            ),
        },
        "runs": run_summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"output_dir={output_dir}")
    print(f"pairs={len(all_rows)}")
    for label, summary_row in run_summaries.items():
        print(f"{label}={summary_row['classification_counts']}")


def analyze_run(label: str, run_dir: Path) -> list[dict[str, Any]]:
    candidate_dir = run_dir / "candidate_pairs"
    candidates = read_jsonl(candidate_dir / "accepted_candidates.jsonl")
    patch_hashes: Counter[str] = Counter()
    patch_hash_by_pair: dict[str, str] = {}
    for candidate in candidates:
        pair_id = str(candidate["pair_id"])
        patch_path = (
            candidate_dir / "pair_outputs" / pair_id / "obsolete_reinsert.patch"
        )
        patch_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        patch_hash_by_pair[pair_id] = patch_hash
        patch_hashes[patch_hash] += 1

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        pair_id = str(candidate["pair_id"])
        artifact_dir = candidate_dir / "pair_outputs" / pair_id
        targets = read_jsonl(artifact_dir / "reinsert_targets.jsonl")
        rows.append(
            analyze_pair(
                label=label,
                run_dir=run_dir,
                candidate=candidate,
                targets=targets,
                patch_group_size=patch_hashes[patch_hash_by_pair[pair_id]],
            )
        )
    return rows


def analyze_pair(
    *,
    label: str,
    run_dir: Path,
    candidate: dict[str, Any],
    targets: list[dict[str, Any]],
    patch_group_size: int,
) -> dict[str, Any]:
    targets_by_path = Counter(str(target["path"]) for target in targets)
    lines_by_path: Counter[str] = Counter()
    target_records: list[dict[str, Any]] = []
    for target in targets:
        source = target_source(target)
        path = str(target["path"])
        lines_by_path[path] += len(source.splitlines())
        target_records.append(
            {
                "path": path,
                "tail_name": qualified_name_tail(target),
                "source": source,
                "line_count": len(source.splitlines()),
                "exact_fingerprint": exact_fingerprint(source),
                "structural_fingerprint": structural_fingerprint(source),
                "short_exact_fingerprint": short_exact_fingerprint(source),
                "short_structural_fingerprint": short_structural_fingerprint(source),
                "token_shingles": token_shingles(source),
            }
        )

    target_count = len(target_records)
    source_lines = sum(lines_by_path.values())
    tail_names = Counter(record["tail_name"] for record in target_records)
    repeated_tail_targets = sum(count for count in tail_names.values() if count > 1)
    non_generic_tail_names = {
        name: count
        for name, count in tail_names.items()
        if not is_generic_tail_name(name)
    }
    non_generic_target_count = sum(non_generic_tail_names.values())
    repeated_non_generic_targets = sum(
        count for count in non_generic_tail_names.values() if count > 1
    )

    paths = sorted(targets_by_path)
    basename_counts = Counter(PurePosixPath(path).name for path in paths)
    repeated_basename_files = sum(
        count
        for basename, count in basename_counts.items()
        if count > 1 and basename != "__init__.py"
    )

    exact_duplicate_targets = duplicate_targets_across_paths(
        target_records, fingerprint_key="exact_fingerprint"
    )
    structural_duplicate_targets = duplicate_targets_across_paths(
        target_records,
        fingerprint_key="structural_fingerprint",
        include_tail_name=True,
    )
    parallel_target_indexes = parallel_target_indices(target_records)
    parallel_target_lines = sum(
        int(target_records[index]["line_count"]) for index in parallel_target_indexes
    )

    nonproduction_target_indexes = {
        index
        for index, record in enumerate(target_records)
        if is_nonproduction_path(str(record["path"]))
    }
    nonproduction_target_lines = sum(
        int(target_records[index]["line_count"])
        for index in nonproduction_target_indexes
    )
    generated_target_indexes = {
        index
        for index, record in enumerate(target_records)
        if is_explicit_generated_path(str(record["path"]))
    }
    production_paths = sorted(
        {
            str(record["path"])
            for index, record in enumerate(target_records)
            if index not in nonproduction_target_indexes
            and index not in generated_target_indexes
        }
    )
    production_domains = production_module_domains(
        production_paths, str(candidate["full_name"])
    )

    parent_dirs = {str(PurePosixPath(path).parent) for path in paths}
    largest_file_target_share = max(targets_by_path.values()) / target_count
    largest_file_line_share = max(lines_by_path.values()) / source_lines
    repeated_tail_share = repeated_tail_targets / target_count
    repeated_non_generic_tail_share = (
        repeated_non_generic_targets / non_generic_target_count
        if non_generic_target_count
        else 0.0
    )
    repeated_basename_share = repeated_basename_files / len(paths)
    exact_duplicate_share = exact_duplicate_targets / target_count
    structural_duplicate_share = structural_duplicate_targets / target_count
    parallel_target_share = len(parallel_target_indexes) / target_count
    parallel_line_share = parallel_target_lines / source_lines
    nonproduction_target_share = len(nonproduction_target_indexes) / target_count
    nonproduction_line_share = nonproduction_target_lines / source_lines
    generated_target_share = len(generated_target_indexes) / target_count

    classification, reasons = classify_pair(
        target_files=len(paths),
        parent_dirs=len(parent_dirs),
        largest_file_line_share=largest_file_line_share,
        repeated_non_generic_tail_share=repeated_non_generic_tail_share,
        max_same_tail_name_count=max(tail_names.values()),
        repeated_basename_share=repeated_basename_share,
        structural_duplicate_targets=structural_duplicate_targets,
        structural_duplicate_share=structural_duplicate_share,
    )
    semantic_classification, semantic_reasons = classify_semantic_pair(
        production_target_files=len(production_paths),
        production_domains=len(production_domains),
        largest_file_line_share=largest_file_line_share,
        parallel_target_share=parallel_target_share,
        parallel_line_share=parallel_line_share,
        nonproduction_target_share=nonproduction_target_share,
        nonproduction_line_share=nonproduction_line_share,
        generated_target_share=generated_target_share,
    )

    return {
        "run_label": label,
        "pair_id": str(candidate["pair_id"]),
        "full_name": str(candidate["full_name"]),
        "offset": int(candidate["offset"]),
        "reinsert_source_lines": int(candidate["reinsert_source_lines"]),
        "implementation_units_count": int(candidate["implementation_units_count"]),
        "removed_object_targets_count": int(candidate["removed_object_targets_count"]),
        "removed_range_targets_count": int(candidate["removed_range_targets_count"]),
        "target_files_count": len(paths),
        "parent_dirs_count": len(parent_dirs),
        "largest_file_target_share_pct": percentage(largest_file_target_share),
        "largest_file_line_share_pct": percentage(largest_file_line_share),
        "repeated_tail_name_share_pct": percentage(repeated_tail_share),
        "repeated_non_generic_tail_name_share_pct": percentage(
            repeated_non_generic_tail_share
        ),
        "max_same_tail_name_count": max(tail_names.values()),
        "repeated_basename_file_share_pct": percentage(repeated_basename_share),
        "exact_duplicate_target_share_pct": percentage(exact_duplicate_share),
        "structural_duplicate_target_share_pct": percentage(structural_duplicate_share),
        "parallel_target_share_pct": percentage(parallel_target_share),
        "parallel_line_share_pct": percentage(parallel_line_share),
        "nonproduction_target_share_pct": percentage(nonproduction_target_share),
        "nonproduction_line_share_pct": percentage(nonproduction_line_share),
        "explicit_generated_target_share_pct": percentage(generated_target_share),
        "production_target_files_count": len(production_paths),
        "production_domain_count": len(production_domains),
        "exact_patch_group_size": patch_group_size,
        "classification": classification,
        "classification_reasons": "; ".join(reasons),
        "semantic_classification": semantic_classification,
        "semantic_classification_reasons": "; ".join(semantic_reasons),
        "artifact_dir": str(
            run_dir / "candidate_pairs" / "pair_outputs" / str(candidate["pair_id"])
        ),
    }


def classify_semantic_pair(
    *,
    production_target_files: int,
    production_domains: int,
    largest_file_line_share: float,
    parallel_target_share: float,
    parallel_line_share: float,
    nonproduction_target_share: float,
    nonproduction_line_share: float,
    generated_target_share: float,
) -> tuple[str, list[str]]:
    if (
        nonproduction_target_share > 0
        or nonproduction_line_share > 0
        or generated_target_share > 0
    ):
        return "nonproduction_mixed", [
            f"non-production targets {percentage(nonproduction_target_share)}%",
            f"non-production lines {percentage(nonproduction_line_share)}%",
            f"explicit generated targets {percentage(generated_target_share)}%",
        ]

    if parallel_line_share >= 0.40 or (
        parallel_target_share >= 0.50 and parallel_line_share >= 0.20
    ):
        return "parallel_dominant", [
            f"parallel-family targets {percentage(parallel_target_share)}%",
            f"parallel-family lines {percentage(parallel_line_share)}%",
        ]

    if parallel_target_share >= 0.40 or parallel_line_share >= 0.25:
        return "parallel_mixed", [
            f"parallel-family targets {percentage(parallel_target_share)}%",
            f"parallel-family lines {percentage(parallel_line_share)}%",
        ]

    desired = (
        production_target_files >= 5
        and production_domains >= 2
        and largest_file_line_share <= 0.70
        and parallel_target_share <= 0.25
        and parallel_line_share <= 0.25
        and nonproduction_target_share == 0
        and nonproduction_line_share == 0
        and generated_target_share == 0
    )
    if desired:
        return "desired_distributed", [
            f"{production_target_files} production files",
            f"{production_domains} coarse production domains",
            f"largest file has {percentage(largest_file_line_share)}% of lines",
            f"parallel-family lines {percentage(parallel_line_share)}%",
        ]

    return "concentrated_or_uncertain", [
        f"{production_target_files} production files",
        f"{production_domains} coarse production domains",
        f"largest file has {percentage(largest_file_line_share)}% of lines",
        f"parallel-family targets {percentage(parallel_target_share)}%",
        f"parallel-family lines {percentage(parallel_line_share)}%",
        f"non-production lines {percentage(nonproduction_line_share)}%",
    ]


def classify_pair(
    *,
    target_files: int,
    parent_dirs: int,
    largest_file_line_share: float,
    repeated_non_generic_tail_share: float,
    max_same_tail_name_count: int,
    repeated_basename_share: float,
    structural_duplicate_targets: int,
    structural_duplicate_share: float,
) -> tuple[str, list[str]]:
    parallel_reasons: list[str] = []
    if structural_duplicate_targets >= 3 and structural_duplicate_share >= 0.20:
        parallel_reasons.append(
            f"structural duplicates {percentage(structural_duplicate_share)}%"
        )
    if max_same_tail_name_count >= 5 and repeated_non_generic_tail_share >= 0.40:
        parallel_reasons.append(
            "same method/function tails repeated across target files"
        )
    if repeated_basename_share >= 0.50 and parallel_reasons:
        parallel_reasons.append(
            f"repeated basenames cover {percentage(repeated_basename_share)}% of files"
        )

    distributed_reasons: list[str] = []
    if target_files >= 5 and parent_dirs >= 3:
        distributed_reasons.append(
            f"{target_files} files across {parent_dirs} parent directories"
        )
    if largest_file_line_share <= 0.70:
        distributed_reasons.append(
            f"largest file has {percentage(largest_file_line_share)}% of lines"
        )
    if structural_duplicate_share <= 0.10:
        distributed_reasons.append("low structural-duplicate share")
    if repeated_non_generic_tail_share <= 0.25:
        distributed_reasons.append("low repeated-name share")

    parallel = bool(parallel_reasons)
    distributed = (
        target_files >= 5
        and parent_dirs >= 3
        and largest_file_line_share <= 0.70
        and structural_duplicate_share <= 0.10
        and repeated_non_generic_tail_share <= 0.25
    )
    if distributed and not parallel:
        return "high_confidence_distributed", distributed_reasons
    if parallel and not distributed:
        return "high_confidence_parallel", parallel_reasons
    return "mixed_or_uncertain", parallel_reasons + distributed_reasons


def duplicate_targets_across_paths(
    records: list[dict[str, Any]],
    *,
    fingerprint_key: str,
    include_tail_name: bool = False,
) -> int:
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        fingerprint = record[fingerprint_key]
        if fingerprint is None:
            continue
        key: Any = fingerprint
        if include_tail_name:
            key = (record["tail_name"], fingerprint)
        groups[key].append(record)
    return sum(
        len(group)
        for group in groups.values()
        if len({record["path"] for record in group}) > 1
    )


def parallel_target_indices(records: list[dict[str, Any]]) -> set[int]:
    duplicate_indexes: set[int] = set()
    for fingerprint_key, include_tail_name in (
        ("short_exact_fingerprint", False),
        ("short_structural_fingerprint", True),
    ):
        groups: dict[Any, list[int]] = defaultdict(list)
        for index, record in enumerate(records):
            fingerprint = record[fingerprint_key]
            if fingerprint is None:
                continue
            key: Any = fingerprint
            if include_tail_name:
                key = (record["tail_name"], fingerprint)
            groups[key].append(index)
        for indexes in groups.values():
            if len({records[index]["path"] for index in indexes}) > 1:
                duplicate_indexes.update(indexes)

    structural_groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        fingerprint = record["short_structural_fingerprint"]
        if fingerprint is not None:
            structural_groups[str(fingerprint)].append(index)
    for indexes in structural_groups.values():
        if len({records[index]["path"] for index in indexes}) >= 3:
            duplicate_indexes.update(indexes)

    repeated_implementation_families: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        tail_name = str(record["tail_name"])
        if is_generic_tail_name(tail_name):
            continue
        repeated_implementation_families[tail_name].append(index)
    for indexes in repeated_implementation_families.values():
        for position, left_index in enumerate(indexes):
            left = records[left_index]
            left_shingles = left["token_shingles"]
            if not left_shingles:
                continue
            for right_index in indexes[position + 1 :]:
                right = records[right_index]
                if left["path"] == right["path"]:
                    continue
                right_shingles = right["token_shingles"]
                if not right_shingles:
                    continue
                union = left_shingles | right_shingles
                similarity = len(left_shingles & right_shingles) / len(union)
                same_basename = (
                    PurePosixPath(str(left["path"])).name
                    == PurePosixPath(str(right["path"])).name
                )
                threshold = 0.60 if same_basename else 0.80
                if similarity >= threshold:
                    duplicate_indexes.update((left_index, right_index))

    targets_by_path: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        targets_by_path[str(record["path"])].append(index)
    for indexes in targets_by_path.values():
        for position, left_index in enumerate(indexes):
            left_shingles = records[left_index]["token_shingles"]
            if not left_shingles:
                continue
            for right_index in indexes[position + 1 :]:
                right_shingles = records[right_index]["token_shingles"]
                if not right_shingles:
                    continue
                union = left_shingles | right_shingles
                similarity = len(left_shingles & right_shingles) / len(union)
                if similarity >= 0.85:
                    duplicate_indexes.update((left_index, right_index))
    return duplicate_indexes


def target_source(target: dict[str, Any]) -> str:
    if target["target_type"] == "removed_object":
        return str(target.get("source_text") or "")
    return "\n".join(
        str(removed_range.get("source_text") or "")
        for removed_range in target.get("removed_ranges") or []
    )


def qualified_name_tail(target: dict[str, Any]) -> str:
    qualified_name = str(target.get("qualified_name") or "")
    return qualified_name.rsplit(".", 1)[-1]


def exact_fingerprint(source: str) -> str | None:
    normalized = normalize_source(source)
    if nonblank_line_count(normalized) < 3:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def structural_fingerprint(source: str) -> str | None:
    normalized = normalize_source(source)
    if nonblank_line_count(normalized) < 3:
        return None
    tokens: list[str] = []
    try:
        generated = tokenize.generate_tokens(io.StringIO(normalized).readline)
        for token in generated:
            if token.type in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.COMMENT,
            }:
                continue
            if token.type == tokenize.NAME:
                tokens.append(
                    token.string if keyword.iskeyword(token.string) else "NAME"
                )
            elif token.type == tokenize.STRING:
                tokens.append("STRING")
            elif token.type == tokenize.NUMBER:
                tokens.append("NUMBER")
            elif token.type == tokenize.INDENT:
                tokens.append("INDENT")
            elif token.type == tokenize.DEDENT:
                tokens.append("DEDENT")
            else:
                tokens.append(token.string)
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return None
    if len(tokens) < 15:
        return None
    return hashlib.sha256("\x1f".join(tokens).encode()).hexdigest()


def short_exact_fingerprint(source: str) -> str | None:
    normalized = normalize_source(source)
    if not normalized or not is_meaningful_short_source(normalized):
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def short_structural_fingerprint(source: str) -> str | None:
    normalized = normalize_source(source)
    if not normalized:
        return None
    tokens = normalized_python_tokens(normalized)
    if len(tokens) < 6 or not is_meaningful_short_source(normalized):
        return None
    return hashlib.sha256("\x1f".join(tokens).encode()).hexdigest()


def normalized_python_tokens(source: str) -> tuple[str, ...]:
    tokens: list[str] = []
    try:
        generated = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in generated:
            if token.type in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.COMMENT,
            }:
                continue
            if token.type == tokenize.NAME:
                tokens.append(
                    token.string if keyword.iskeyword(token.string) else "NAME"
                )
            elif token.type == tokenize.STRING:
                tokens.append("STRING")
            elif token.type == tokenize.NUMBER:
                tokens.append("NUMBER")
            elif token.type == tokenize.INDENT:
                tokens.append("INDENT")
            elif token.type == tokenize.DEDENT:
                tokens.append("DEDENT")
            else:
                tokens.append(token.string)
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return ()
    return tuple(tokens)


def token_shingles(source: str) -> frozenset[tuple[str, ...]]:
    normalized = normalize_source(source)
    tokens = normalized_python_tokens(normalized)
    if len(tokens) < 6:
        return frozenset()
    width = 3 if len(tokens) >= 9 else 2
    return frozenset(
        tuple(tokens[index : index + width]) for index in range(len(tokens) - width + 1)
    )


def is_meaningful_short_source(source: str) -> bool:
    tokens = normalized_python_tokens(source)
    if len(tokens) < 6:
        return False
    compact = " ".join(tokens)
    return compact not in {
        "return",
        "return NAME",
        "return None",
        "pass",
        "break",
        "continue",
    }


def is_nonproduction_path(path: str) -> bool:
    original_parts = list(PurePosixPath(path).parts)
    for original_part in original_parts[:-1]:
        part = original_part.lower()
        normalized_part = part.strip("_")
        if (
            part in NONPRODUCTION_SEGMENTS
            or normalized_part in NONPRODUCTION_SEGMENTS
            or part.startswith("test_")
            or part.endswith(("_test", "_tests"))
        ):
            return True
    original_filename = original_parts[-1]
    filename = original_filename.lower()
    original_stem = PurePosixPath(original_filename).stem
    if (
        filename in {"test.py", "tests.py", "conftest.py"}
        or filename.startswith("test_")
        or filename.endswith(("_test.py", "_tests.py"))
        or original_stem.endswith(("Test", "Tests"))
    ):
        return True
    return False


def is_explicit_generated_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path).parts]
    return any(
        part in EXPLICIT_GENERATED_SEGMENTS
        or "generated" in part
        or part.endswith("_pb2.py")
        for part in parts
    )


def production_module_domains(paths: list[str], full_name: str) -> set[str]:
    if not paths:
        return set()
    repo_name = full_name.rsplit("/", 1)[-1].lower().replace("-", "_").replace(".", "_")
    split_paths = [list(PurePosixPath(path).parts) for path in paths]
    for parts in split_paths:
        while parts and parts[0].lower() in SOURCE_ROOT_SEGMENTS:
            parts.pop(0)

    while split_paths and all(parts for parts in split_paths):
        first = split_paths[0][0].lower().replace("-", "_").replace(".", "_")
        if not all(
            parts[0].lower().replace("-", "_").replace(".", "_") == first
            for parts in split_paths
        ):
            break
        if first != repo_name and len(split_paths[0]) <= 2:
            break
        for parts in split_paths:
            parts.pop(0)
        if first != repo_name:
            break

    domains: set[str] = set()
    for original_path, parts in zip(paths, split_paths):
        if len(parts) >= 2:
            domains.add(parts[0].lower())
        elif parts:
            domains.add(PurePosixPath(parts[0]).stem.lower())
        else:
            domains.add(PurePosixPath(original_path).stem.lower())
    return domains


def normalize_source(source: str) -> str:
    dedented = textwrap.dedent(source)
    return "\n".join(line.rstrip() for line in dedented.strip().splitlines())


def nonblank_line_count(source: str) -> int:
    return sum(bool(line.strip()) for line in source.splitlines())


def is_generic_tail_name(name: str) -> bool:
    return name in GENERIC_TAIL_NAMES or (name.startswith("__") and name.endswith("__"))


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classifications = Counter(str(row["classification"]) for row in rows)
    semantic_classifications = Counter(
        str(row["semantic_classification"]) for row in rows
    )
    return {
        "pairs": len(rows),
        "classification_counts": dict(classifications),
        "classification_percentages": {
            name: percentage(count / len(rows))
            for name, count in classifications.items()
        },
        "semantic_classification_counts": dict(semantic_classifications),
        "semantic_classification_percentages": {
            name: percentage(count / len(rows))
            for name, count in semantic_classifications.items()
        },
        "structural_duplicate_share_pct": quantiles(
            float(row["structural_duplicate_target_share_pct"]) for row in rows
        ),
        "parallel_target_share_pct": quantiles(
            float(row["parallel_target_share_pct"]) for row in rows
        ),
        "parallel_line_share_pct": quantiles(
            float(row["parallel_line_share_pct"]) for row in rows
        ),
        "nonproduction_line_share_pct": quantiles(
            float(row["nonproduction_line_share_pct"]) for row in rows
        ),
        "largest_file_line_share_pct": quantiles(
            float(row["largest_file_line_share_pct"]) for row in rows
        ),
        "parent_dirs_count": quantiles(float(row["parent_dirs_count"]) for row in rows),
        "exact_patch_group_size": quantiles(
            float(row["exact_patch_group_size"]) for row in rows
        ),
    }


def quantiles(values: Iterable[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "min": interpolate_quantile(ordered, 0),
        "p25": interpolate_quantile(ordered, 0.25),
        "median": interpolate_quantile(ordered, 0.5),
        "p75": interpolate_quantile(ordered, 0.75),
        "p90": interpolate_quantile(ordered, 0.9),
        "max": interpolate_quantile(ordered, 1),
    }


def interpolate_quantile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(values[lower], 3)
    weight = position - lower
    return round(values[lower] + (values[upper] - values[lower]) * weight, 3)


def percentage(value: float) -> float:
    return round(value * 100, 2)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
