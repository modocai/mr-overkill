"""Tests for wip_scope — run against a real git repo, not mocks.

Every function here exists because of a git behaviour that is easy to get
wrong and impossible to observe through a mock: untracked files are invisible
to ``git diff`` until they are staged as intent-to-add, ``git add -A`` sweeps
in the log directory unless a pathspec excludes it, and ``git reset --mixed``
must leave file contents alone while moving the branch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mr_overkill.wip_scope import (
    SCAFFOLD_MESSAGE,
    create_scaffold_commit,
    merge_base,
    uncommitted_files,
    unwind,
    write_worktree_diff,
)


@pytest.fixture()
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A place for artefacts that is *not* inside the repo under test.

    ``tmp_git_repo`` is rooted at ``tmp_path``, so writing there would show up
    as an untracked file and change the very status these tests assert on.
    """
    return tmp_path_factory.mktemp("wip-out")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain")


def _write_log(repo: Path, name: str = "review-1.json") -> None:
    log_dir = repo / ".overkill" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text('{"findings": []}')


class TestUncommittedFiles:
    def test_clean_repo_is_empty(self, tmp_git_repo: Path) -> None:
        assert uncommitted_files(tmp_git_repo) == []

    def test_collects_modified_staged_and_untracked(
        self, tmp_git_repo: Path
    ) -> None:
        (tmp_git_repo / "README.md").write_text("# changed\n")
        (tmp_git_repo / "staged.py").write_text("x = 1\n")
        _git(tmp_git_repo, "add", "staged.py")
        (tmp_git_repo / "untracked.py").write_text("y = 2\n")

        assert uncommitted_files(tmp_git_repo) == [
            "README.md",
            "staged.py",
            "untracked.py",
        ]

    def test_log_artefacts_are_not_work_in_progress(
        self, tmp_git_repo: Path
    ) -> None:
        _write_log(tmp_git_repo)
        assert uncommitted_files(tmp_git_repo) == []


class TestMergeBase:
    def test_ancestor_target_is_its_own_merge_base(
        self, tmp_git_repo: Path
    ) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "a.txt").write_text("a\n")
        _git(tmp_git_repo, "add", "a.txt")
        _git(tmp_git_repo, "commit", "-m", "add a")

        assert merge_base(root, tmp_git_repo) == root

    def test_unrelated_history_returns_none(self, tmp_git_repo: Path) -> None:
        # An orphan branch shares no history with HEAD at all.
        _git(tmp_git_repo, "checkout", "-q", "--orphan", "orphan")
        (tmp_git_repo / "other.txt").write_text("other\n")
        _git(tmp_git_repo, "add", "other.txt")
        _git(tmp_git_repo, "commit", "-m", "orphan root")

        assert merge_base("master", tmp_git_repo) is None


class TestWriteWorktreeDiff:
    def test_captures_committed_and_uncommitted_together(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "committed.py").write_text("committed = True\n")
        _git(tmp_git_repo, "add", "committed.py")
        _git(tmp_git_repo, "commit", "-m", "a real commit")
        (tmp_git_repo / "modified.py").write_text("modified = True\n")
        _git(tmp_git_repo, "add", "modified.py")
        _git(tmp_git_repo, "commit", "-m", "another real commit")
        (tmp_git_repo / "modified.py").write_text("modified = 'edited'\n")

        out = out_dir / "wip.diff"
        size = write_worktree_diff(base, out, tmp_git_repo)

        text = out.read_text()
        assert size == len(text.encode())
        assert "committed = True" in text
        assert "modified = 'edited'" in text

    def test_untracked_files_are_included(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # The reason intent-to-add exists: a plain ``git diff`` cannot see a
        # brand-new file, which is exactly the kind of work being reviewed.
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "brand_new.py").write_text("def hello(): ...\n")

        plain = subprocess.run(
            ["git", "diff", base],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "brand_new.py" not in plain.stdout

        out = out_dir / "wip.diff"
        assert write_worktree_diff(base, out, tmp_git_repo) > 0
        assert "brand_new.py" in out.read_text()
        assert "def hello()" in out.read_text()

    def test_staged_unstaged_split_survives(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # Undoing intent-to-add with a blanket reset would unstage the user's
        # own staged work; only the untracked paths may be touched.
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "staged.py").write_text("staged = 1\n")
        _git(tmp_git_repo, "add", "staged.py")
        (tmp_git_repo / "README.md").write_text("# unstaged\n")
        (tmp_git_repo / "untracked.py").write_text("untracked = 1\n")
        before = _status(tmp_git_repo)

        write_worktree_diff(base, out_dir / "wip.diff", tmp_git_repo)

        assert _status(tmp_git_repo) == before

    def test_log_artefacts_are_excluded(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        _write_log(tmp_git_repo)
        (tmp_git_repo / "real.py").write_text("real = 1\n")

        out = out_dir / "wip.diff"
        write_worktree_diff(base, out, tmp_git_repo)

        assert "real.py" in out.read_text()
        assert "review-1.json" not in out.read_text()

    def test_unrelated_target_reports_failure(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        out = out_dir / "wip.diff"
        assert write_worktree_diff("nonexistent-branch", out, tmp_git_repo) == 0
        assert not out.exists()


class TestCreateScaffoldCommit:
    def test_parks_everything_and_leaves_a_clean_tree(
        self, tmp_git_repo: Path
    ) -> None:
        (tmp_git_repo / "README.md").write_text("# changed\n")
        (tmp_git_repo / "new.py").write_text("new = 1\n")

        sha = create_scaffold_commit(tmp_git_repo)

        assert sha == _git(tmp_git_repo, "rev-parse", "HEAD")
        assert _status(tmp_git_repo) == ""
        assert SCAFFOLD_MESSAGE in _git(tmp_git_repo, "log", "-1", "--format=%s%n%b")
        files = _git(tmp_git_repo, "show", "--name-only", "--format=", sha).split()
        assert sorted(files) == ["README.md", "new.py"]

    def test_log_directory_stays_out_of_the_commit(
        self, tmp_git_repo: Path
    ) -> None:
        _write_log(tmp_git_repo)
        (tmp_git_repo / "real.py").write_text("real = 1\n")

        sha = create_scaffold_commit(tmp_git_repo)
        assert sha is not None

        files = _git(tmp_git_repo, "show", "--name-only", "--format=", sha).split()
        assert files == ["real.py"]

    def test_gitignored_log_directory_does_not_abort_the_add(
        self, tmp_git_repo: Path
    ) -> None:
        """Regression: excluding the log dir by pathspec makes git refuse the
        whole add when that directory is gitignored — which it usually is."""
        (tmp_git_repo / ".gitignore").write_text(".overkill/\n")
        _write_log(tmp_git_repo)
        (tmp_git_repo / "real.py").write_text("real = 1\n")

        sha = create_scaffold_commit(tmp_git_repo)

        assert sha is not None
        files = _git(tmp_git_repo, "show", "--name-only", "--format=", sha).split()
        assert sorted(files) == [".gitignore", "real.py"]
        assert _status(tmp_git_repo) == ""

    def test_deletions_are_staged(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").unlink()

        sha = create_scaffold_commit(tmp_git_repo)

        assert sha is not None
        assert _status(tmp_git_repo) == ""
        assert not (tmp_git_repo / "README.md").exists()

    def test_clean_tree_refuses(self, tmp_git_repo: Path) -> None:
        head = _git(tmp_git_repo, "rev-parse", "HEAD")
        assert create_scaffold_commit(tmp_git_repo) is None
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == head

    def test_failing_pre_commit_hook_does_not_block(
        self, tmp_git_repo: Path
    ) -> None:
        # Work in progress routinely fails hooks it will pass once finished.
        hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        (tmp_git_repo / "wip.py").write_text("wip = 1\n")

        assert create_scaffold_commit(tmp_git_repo) is not None
        assert _status(tmp_git_repo) == ""


class TestUnwind:
    def _scaffold_then_fix(self, repo: Path) -> tuple[str, str]:
        """Simulate a full run: park WIP, then commit an AI fix on top."""
        base = _git(repo, "rev-parse", "HEAD")
        (repo / "feature.py").write_text("def f():\n    return 1\n")
        scaffold = create_scaffold_commit(repo)
        assert scaffold is not None
        (repo / "feature.py").write_text("def f() -> int:\n    return 1\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-m", "fix: add a type hint")
        return base, scaffold

    def test_restores_history_and_keeps_the_work(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)

        assert unwind(base, scaffold, out_dir, tmp_git_repo)

        assert _git(tmp_git_repo, "rev-parse", "HEAD") == base
        # The fixed content survives the reset, uncommitted.
        assert (tmp_git_repo / "feature.py").read_text() == (
            "def f() -> int:\n    return 1\n"
        )
        assert "feature.py" in _status(tmp_git_repo)

    def test_leaves_nothing_staged(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)

        unwind(base, scaffold, out_dir, tmp_git_repo)

        assert _git(tmp_git_repo, "diff", "--cached", "--name-only") == ""

    def test_saves_the_ai_net_change(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # Once the scaffolding is gone there is no way left to tell the
        # author's own work from the loop's edits, so it is saved first.
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)

        unwind(base, scaffold, out_dir, tmp_git_repo)

        fixes = (out_dir / "wip-fixes.diff").read_text()
        assert "def f() -> int:" in fixes
        assert "-def f():" in fixes

    def test_is_idempotent(self, tmp_git_repo: Path, out_dir: Path) -> None:
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)
        unwind(base, scaffold, out_dir, tmp_git_repo)

        assert unwind(base, scaffold, out_dir, tmp_git_repo)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == base

    def test_refuses_when_base_is_not_an_ancestor(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # Resetting to a commit HEAD does not descend from would move the
        # branch somewhere it has never been.
        (tmp_git_repo / "earlier.py").write_text("earlier = 1\n")
        _git(tmp_git_repo, "add", "earlier.py")
        _git(tmp_git_repo, "commit", "-m", "an earlier commit")
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)
        _git(tmp_git_repo, "checkout", "-q", "-b", "elsewhere", base + "^")
        head = _git(tmp_git_repo, "rev-parse", "HEAD")

        assert not unwind(base, scaffold, out_dir, tmp_git_repo)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == head

    def test_no_scaffold_recorded_still_resets(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "x.py").write_text("x = 1\n")
        create_scaffold_commit(tmp_git_repo)

        assert unwind(base, None, out_dir, tmp_git_repo)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == base
        assert not (out_dir / "wip-fixes.diff").exists()
