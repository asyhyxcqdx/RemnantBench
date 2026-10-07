from __future__ import annotations

from feature_factory.diff_stats import filter_noise_files_from_patch
from feature_factory.stage3.service import _stage3_diff_stats_from_patch_text as stage3_diff_stats
from feature_factory.stage4.service import _stage3_diff_stats_from_patch_text as stage4_diff_stats


def test_diff_stats_ignore_lockfiles_and_generated_noise() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1,2 +1,3 @@\n"
        "-old-lock-line\n"
        "+new-lock-line\n"
        "+extra-lock-line\n"
        "diff --git a/web/package-lock.json b/web/package-lock.json\n"
        "--- a/web/package-lock.json\n"
        "+++ b/web/package-lock.json\n"
        "@@ -1 +1 @@\n"
        "-old-package-lock\n"
        "+new-package-lock\n"
        "diff --git a/dist/app.min.js b/dist/app.min.js\n"
        "--- a/dist/app.min.js\n"
        "+++ b/dist/app.min.js\n"
        "@@ -1 +1 @@\n"
        "-minified-old\n"
        "+minified-new\n"
        "diff --git a/peewee.egg-info/PKG-INFO b/peewee.egg-info/PKG-INFO\n"
        "--- a/peewee.egg-info/PKG-INFO\n"
        "+++ b/peewee.egg-info/PKG-INFO\n"
        "@@ -1 +1 @@\n"
        "-old-metadata\n"
        "+new-metadata\n"
        "diff --git a/nested/pkg.dist-info/METADATA b/nested/pkg.dist-info/METADATA\n"
        "--- a/nested/pkg.dist-info/METADATA\n"
        "+++ b/nested/pkg.dist-info/METADATA\n"
        "@@ -1 +1 @@\n"
        "-old-dist-metadata\n"
        "+new-dist-metadata\n"
    )

    assert stage3_diff_stats(patch_text) == {
        "total_changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
    }
    assert stage4_diff_stats(patch_text) == {
        "files_changed": 1,
        "lines_added": 1,
        "lines_deleted": 1,
        "lines_changed": 2,
    }


def test_diff_stats_return_zero_for_lockfile_only_patch() -> None:
    patch_text = (
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    assert stage3_diff_stats(patch_text) == {
        "total_changed_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
    }
    assert stage4_diff_stats(patch_text) == {
        "files_changed": 0,
        "lines_added": 0,
        "lines_deleted": 0,
        "lines_changed": 0,
    }


def test_diff_stats_ignore_dependency_manifest_files() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/pyproject.toml b/pyproject.toml\n"
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1 +1 @@\n"
        "-old-dep\n"
        "+new-dep\n"
        "diff --git a/package.json b/package.json\n"
        "--- a/package.json\n"
        "+++ b/package.json\n"
        "@@ -1 +1 @@\n"
        "-old-script\n"
        "+new-script\n"
        "diff --git a/setup.py b/setup.py\n"
        "--- a/setup.py\n"
        "+++ b/setup.py\n"
        "@@ -1 +1 @@\n"
        "-old-setup\n"
        "+new-setup\n"
    )

    assert stage3_diff_stats(patch_text) == {
        "total_changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
    }
    assert stage4_diff_stats(patch_text) == {
        "files_changed": 1,
        "lines_added": 1,
        "lines_deleted": 1,
        "lines_changed": 2,
    }


def test_filter_noise_files_from_patch_keeps_only_functional_diff_blocks() -> None:
    patch_text = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1 +1 @@\n"
        "-https://pypi.org/simple\n"
        "+https://mirrors.example/simple\n"
        "diff --git a/web/package-lock.json b/web/package-lock.json\n"
        "--- a/web/package-lock.json\n"
        "+++ b/web/package-lock.json\n"
        "@@ -1 +1 @@\n"
        "-old-lock\n"
        "+new-lock\n"
        "diff --git a/contrib/libexample.so.6 b/contrib/libexample.so.6\n"
        "new file mode 100755\n"
        "GIT binary patch\n"
        "literal 1000\n"
        "zbinarypayload\n"
        "diff --git a/assets/blob.data b/assets/blob.data\n"
        "new file mode 100644\n"
        "GIT binary patch\n"
        "literal 500\n"
        "zotherbinarypayload\n"
        "diff --git a/po/messages.pot b/po/messages.pot\n"
        "--- a/po/messages.pot\n"
        "+++ b/po/messages.pot\n"
        "@@ -1 +1 @@\n"
        "-old generated translation catalog\n"
        "+new generated translation catalog\n"
    )

    filtered = filter_noise_files_from_patch(patch_text)

    assert "src/app.py" in filtered
    assert "+new\n" in filtered
    assert "uv.lock" not in filtered
    assert "package-lock.json" not in filtered
    assert "libexample.so.6" not in filtered
    assert "assets/blob.data" not in filtered
    assert "messages.pot" not in filtered
    assert "mirrors.example" not in filtered


def test_filter_noise_files_from_patch_preserves_non_git_diff_text() -> None:
    patch_text = "--- a/app.py\n+++ b/app.py\n-old\n+new\n"

    assert filter_noise_files_from_patch(patch_text) == patch_text