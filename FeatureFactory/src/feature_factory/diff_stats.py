from __future__ import annotations

from dataclasses import dataclass


_NOISE_FILE_NAMES = {
    ".coverage",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.lock",
    "Cargo.toml",
    "composer.json",
    "Gemfile.lock",
    "Gemfile",
    "go.mod",
    "Package.resolved",
    "Pipfile.lock",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "bun.lock",
    "bun.lockb",
    "composer.lock",
    "conda-lock.yaml",
    "conda-lock.yml",
    "flake.lock",
    "go.sum",
    "go.work.sum",
    "gradle.lockfile",
    "mix.lock",
    "npm-shrinkwrap.json",
    "package-lock.json",
    "pdm.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pubspec.lock",
    "requirements-dev.txt",
    "requirements.lock",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "yarn.lock",
}
_NOISE_FILE_NAMES_NORMALIZED = frozenset(name.lower() for name in _NOISE_FILE_NAMES)
_NOISE_PATH_PARTS = {
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".eggs",
    "__pycache__",
    "node_modules",
    "venv",
}
_NOISE_TOP_LEVEL_DIRS = {
    "build",
    "coverage",
    "dist",
    "target",
}
_NOISE_FILE_SUFFIXES = (
    ".a",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".lib",
    ".log",
    ".map",
    ".min.css",
    ".min.js",
    ".o",
    ".pyo",
    ".pyc",
    ".pot",
    ".so",
    ".tmp",
)
_NOISE_PATH_PART_SUFFIXES = (
    ".dist-info",
    ".egg-info",
)


@dataclass(frozen=True, slots=True)
class PatchDiffLineStats:
    files_changed: int
    lines_added: int
    lines_deleted: int

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_deleted


def core_patch_diff_line_stats(patch_text: str | None) -> PatchDiffLineStats:
    files: set[str] = set()
    lines_added = 0
    lines_deleted = 0
    current_paths: tuple[str, ...] = ()
    current_is_noise = False
    for line in str(patch_text or "").splitlines():
        if line.startswith("diff --git "):
            current_paths = _parse_git_diff_paths(line)
            current_is_noise = any(_is_noise_diff_path(path) for path in current_paths)
            display_path = _display_diff_path(current_paths)
            if display_path and not current_is_noise:
                files.add(display_path)
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if current_is_noise:
            continue
        if line.startswith("+"):
            lines_added += 1
        elif line.startswith("-"):
            lines_deleted += 1
    return PatchDiffLineStats(
        files_changed=len(files),
        lines_added=lines_added,
        lines_deleted=lines_deleted,
    )


def filter_noise_files_from_patch(patch_text: str | None) -> str:
    """Remove dependency, build, and generated-file diff blocks from a git patch."""
    text = str(patch_text or "")
    lines = text.splitlines(keepends=True)
    if not any(line.startswith("diff --git ") for line in lines):
        return text

    kept: list[str] = []
    current: list[str] = []
    current_is_noise = False
    for line in lines:
        if line.startswith("diff --git "):
            if current and not current_is_noise and not _is_binary_patch_block(current):
                kept.extend(current)
            current = [line]
            paths = _parse_git_diff_paths(line)
            current_is_noise = any(_is_noise_diff_path(path) for path in paths)
            continue
        if current:
            current.append(line)
    if current and not current_is_noise and not _is_binary_patch_block(current):
        kept.extend(current)
    return "".join(kept)


def _is_binary_patch_block(lines: list[str]) -> bool:
    return any(
        line.startswith("GIT binary patch")
        or (line.startswith("Binary files ") and line.rstrip().endswith(" differ"))
        for line in lines
    )


def _parse_git_diff_paths(line: str) -> tuple[str, ...]:
    rest = line.removeprefix("diff --git ").strip()
    if rest.startswith("a/") and " b/" in rest:
        old_path, new_path = rest.split(" b/", 1)
        return (_clean_git_path(old_path), _clean_git_path(f"b/{new_path}"))
    parts = rest.split()
    if len(parts) >= 2:
        return tuple(_clean_git_path(part) for part in parts[:2])
    return ()


def _clean_git_path(path: str) -> str:
    cleaned = path.strip().strip('"')
    if cleaned.startswith("a/") or cleaned.startswith("b/"):
        return cleaned[2:]
    return cleaned


def _display_diff_path(paths: tuple[str, ...]) -> str:
    for path in reversed(paths):
        if path and path != "/dev/null":
            return path
    return ""


def _is_noise_diff_path(path: str) -> bool:
    normalized = path.strip().strip("/")
    if not normalized or normalized == "/dev/null":
        return False
    parts = normalized.split("/")
    if any(part in _NOISE_PATH_PARTS for part in parts):
        return True
    if any(part.lower().endswith(_NOISE_PATH_PART_SUFFIXES) for part in parts):
        return True
    if parts[0] in _NOISE_TOP_LEVEL_DIRS:
        return True
    name = parts[-1].lower()
    if ".so." in name or ".dylib." in name:
        return True
    return name in _NOISE_FILE_NAMES_NORMALIZED or name.endswith(_NOISE_FILE_SUFFIXES)