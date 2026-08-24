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
    head_is_scaffold,
    is_in_head,
    merge_base,
    operation_in_progress,
    save_metadata,
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


def _branch(repo: Path) -> str:
    """The repo's initial branch name — 'main' or 'master' depending on git config."""
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD")


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
        original = _branch(tmp_git_repo)
        _git(tmp_git_repo, "checkout", "-q", "--orphan", "orphan")
        (tmp_git_repo / "other.txt").write_text("other\n")
        _git(tmp_git_repo, "add", "other.txt")
        _git(tmp_git_repo, "commit", "-m", "orphan root")

        assert merge_base(original, tmp_git_repo) is None


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

    def test_an_already_staged_log_artefact_stays_out_of_the_commit(
        self, tmp_git_repo: Path
    ) -> None:
        """Filtering the *add* is not enough: committing the whole index would
        put a log file the user had staged themselves into the scaffolding
        commit, and so into the diff the reviewer reads."""
        _write_log(tmp_git_repo)
        _git(tmp_git_repo, "add", ".overkill/logs/review-1.json")
        (tmp_git_repo / "real.py").write_text("real = 1\n")

        sha = create_scaffold_commit(tmp_git_repo)
        assert sha is not None

        files = _git(tmp_git_repo, "show", "--name-only", "--format=", sha).split()
        assert files == ["real.py"]
        # Left staged exactly as the user had it — not committed, not reset.
        assert _git(tmp_git_repo, "diff", "--cached", "--name-only") == (
            ".overkill/logs/review-1.json"
        )

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

    def test_a_failed_commit_says_the_index_is_left_staged(
        self, tmp_git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # ``--no-verify`` skips hooks but not signing, so a repo that signs
        # commits with an unavailable key fails here with everything staged.
        _git(tmp_git_repo, "config", "commit.gpgsign", "true")
        _git(tmp_git_repo, "config", "gpg.program", "false")
        (tmp_git_repo / "wip.py").write_text("wip = 1\n")

        assert create_scaffold_commit(tmp_git_repo) is None

        assert "is left in the index" in caplog.text
        assert _status(tmp_git_repo) == "A  wip.py"

    def test_a_failed_commit_reports_a_reason_from_stdout(
        self,
        tmp_git_repo: Path,
        out_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A submodule whose gitlink did not move is dirty to ``git diff`` but
        # stages nothing, so the commit fails with "no changes added to commit"
        # — which git prints on stdout, leaving stderr empty and the old
        # message blank.
        sub = out_dir / "sub"
        sub.mkdir()
        _git(sub, "init")
        _git(sub, "config", "user.email", "test@test.com")
        _git(sub, "config", "user.name", "Test")
        (sub / "a.txt").write_text("a\n")
        _git(sub, "add", "-A")
        _git(sub, "commit", "-m", "init")
        _git(
            tmp_git_repo,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(sub),
            "sub",
        )
        _git(tmp_git_repo, "commit", "-m", "add submodule")
        (tmp_git_repo / "sub" / "a.txt").write_text("dirty\n")

        assert create_scaffold_commit(tmp_git_repo) is None

        assert "no changes added to commit" in caplog.text

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


class TestOperationInProgress:
    def test_idle_repo(self, tmp_git_repo: Path) -> None:
        assert operation_in_progress(tmp_git_repo) is None

    def test_conflicted_merge_is_detected(self, tmp_git_repo: Path) -> None:
        # Committing here would conclude the merge, and unwinding would then
        # reset past it and drop MERGE_HEAD.
        original = _branch(tmp_git_repo)
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "conflict.txt").write_text("ours\n")
        _git(tmp_git_repo, "add", "conflict.txt")
        _git(tmp_git_repo, "commit", "-m", "ours")
        _git(tmp_git_repo, "checkout", "-q", "-b", "theirs", base)
        (tmp_git_repo / "conflict.txt").write_text("theirs\n")
        _git(tmp_git_repo, "add", "conflict.txt")
        _git(tmp_git_repo, "commit", "-m", "theirs")
        subprocess.run(
            ["git", "merge", original], cwd=tmp_git_repo, capture_output=True
        )

        assert operation_in_progress(tmp_git_repo) == "merge"


class TestIsInHead:
    def test_a_commit_on_the_branch_is_found(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "feature.py").write_text("x = 1\n")
        scaffold = create_scaffold_commit(tmp_git_repo)
        assert scaffold is not None

        assert is_in_head(scaffold, tmp_git_repo) is True

    def test_an_unwound_commit_is_not_found(self, tmp_git_repo: Path) -> None:
        # This is what a resumed --wip run faces after a soft failure took the
        # scaffolding down: the recorded SHA no longer exists in the history.
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "feature.py").write_text("x = 1\n")
        scaffold = create_scaffold_commit(tmp_git_repo)
        assert scaffold is not None
        _git(tmp_git_repo, "reset", "--quiet", "--mixed", base)

        assert is_in_head(scaffold, tmp_git_repo) is False


class TestHeadIsScaffold:
    def test_a_leftover_scaffolding_commit_is_recognised(
        self, tmp_git_repo: Path
    ) -> None:
        # What a run killed before its unwind leaves behind.
        (tmp_git_repo / "feature.py").write_text("x = 1\n")
        assert create_scaffold_commit(tmp_git_repo) is not None

        assert head_is_scaffold(tmp_git_repo) is True

    def test_an_ordinary_commit_is_not(self, tmp_git_repo: Path) -> None:
        assert head_is_scaffold(tmp_git_repo) is False

    def test_an_unwound_scaffold_is_not(self, tmp_git_repo: Path) -> None:
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "feature.py").write_text("x = 1\n")
        assert create_scaffold_commit(tmp_git_repo) is not None
        _git(tmp_git_repo, "reset", "--quiet", "--mixed", base)

        assert head_is_scaffold(tmp_git_repo) is False


class TestSaveMetadata:
    def test_it_replaces_a_dangling_scaffold_sha(self, tmp_path: Path) -> None:
        # The re-park path calls this to overwrite the SHA a soft-failed run
        # left behind; a stale one has no route back to the parked work.
        log_dir = tmp_path / "logs"
        save_metadata(log_dir, "b" * 40, "c" * 40)
        save_metadata(log_dir, "b" * 40, "d" * 40)

        assert (log_dir / "wip-base.txt").read_text() == "b" * 40
        assert (log_dir / "wip-scaffold.txt").read_text() == "d" * 40


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

    def test_an_empty_net_change_leaves_the_artefact_alone(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # A run that scaffolds and then commits nothing has no net change to
        # report, and writing the empty diff would replace an earlier
        # attempt's record with a file claiming the loop changed nothing.
        (out_dir / "wip-fixes.diff").write_text("--- an earlier attempt\n")
        (tmp_git_repo / "feature.py").write_text("def f():\n    pass\n")
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        scaffold = create_scaffold_commit(tmp_git_repo)

        assert unwind(base, scaffold, out_dir, tmp_git_repo)

        assert (out_dir / "wip-fixes.diff").read_text() == (
            "--- an earlier attempt\n"
        )

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

    def test_missing_scaffold_refuses(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        # Without the scaffolding commit there is no proof this is even the
        # branch the run happened on.
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        (tmp_git_repo / "x.py").write_text("x = 1\n")
        head = create_scaffold_commit(tmp_git_repo)

        assert not unwind(base, None, out_dir, tmp_git_repo)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == head

    def test_refuses_on_a_branch_cut_from_the_scaffolding(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        """Ancestry is not identity: a branch cut from the scaffolding commit
        has it in its history too, so resetting on the strength of that would
        turn this branch's own commits into uncommitted changes."""
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)
        original = _branch(tmp_git_repo)
        _git(tmp_git_repo, "checkout", "-q", "-b", "descendant")
        (tmp_git_repo / "later.py").write_text("later = 1\n")
        _git(tmp_git_repo, "add", "later.py")
        _git(tmp_git_repo, "commit", "-m", "work on the descendant branch")
        head = _git(tmp_git_repo, "rev-parse", "HEAD")

        assert not unwind(base, scaffold, out_dir, tmp_git_repo, branch=original)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == head

    def test_saves_uncommitted_fixer_edits(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        """A fixer that edits files and then fails leaves HEAD at the
        scaffolding commit, so a commit-to-commit diff would report nothing
        and the reset would fold those edits into the author's own work."""
        (tmp_git_repo / "feature.py").write_text("def f():\n    return 1\n")
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        scaffold = create_scaffold_commit(tmp_git_repo)
        (tmp_git_repo / "feature.py").write_text("def f() -> int:\n    return 1\n")
        (tmp_git_repo / "added_by_the_fixer.py").write_text("helper = 1\n")

        assert unwind(base, scaffold, out_dir, tmp_git_repo)

        fixes = (out_dir / "wip-fixes.diff").read_text()
        assert "def f() -> int:" in fixes
        assert "added_by_the_fixer.py" in fixes
        # The intent-to-add used to make the new file visible is undone.
        assert _git(tmp_git_repo, "diff", "--cached", "--name-only") == ""

    def test_refuses_on_a_sibling_branch(
        self, tmp_git_repo: Path, out_dir: Path
    ) -> None:
        """A sibling branch off the same base passes an ancestry test against
        that base, so resetting on the strength of it would rewind the wrong
        branch. Only the scaffolding commit identifies the right one."""
        base, scaffold = self._scaffold_then_fix(tmp_git_repo)
        _git(tmp_git_repo, "stash", "-u")
        _git(tmp_git_repo, "checkout", "-q", "-b", "sibling", base)
        (tmp_git_repo / "sibling.py").write_text("sibling = 1\n")
        _git(tmp_git_repo, "add", "sibling.py")
        _git(tmp_git_repo, "commit", "-m", "work on the sibling branch")
        head = _git(tmp_git_repo, "rev-parse", "HEAD")

        assert not unwind(base, scaffold, out_dir, tmp_git_repo)
        assert _git(tmp_git_repo, "rev-parse", "HEAD") == head
