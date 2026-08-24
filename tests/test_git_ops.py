"""Tests for mr_overkill.git_ops — git operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mr_overkill.git_ops import (
    changed_files_since_snapshot,
    commit_and_push,
    diff_hash,
    gen_uuid,
    git_all_dirty,
    push_trigger_commit,
    sha256,
    snapshot_worktree,
)


class TestSha256:
    def test_basic_hash(self) -> None:
        result = sha256("hello")
        assert len(result) == 64
        assert result == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e"
            "1b161e5c1fa7425e73043362938b9824"
        )

    def test_bytes_input(self) -> None:
        assert sha256(b"hello") == sha256("hello")


class TestGenUuid:
    def test_format(self) -> None:
        uid = gen_uuid()
        assert len(uid) == 36
        assert uid == uid.lower()
        parts = uid.split("-")
        assert len(parts) == 5

    def test_unique(self) -> None:
        assert gen_uuid() != gen_uuid()


class TestDiffHash:
    def test_same_branch_produces_hash(self, tmp_git_repo: Path) -> None:
        # diff of branch against itself should produce a hash (of empty diff)
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result = diff_hash(branch, branch, cwd=tmp_git_repo)
        assert len(result) == 64


class TestGitAllDirty:
    def test_clean_repo(self, tmp_git_repo: Path) -> None:
        assert git_all_dirty(cwd=tmp_git_repo) == []

    def test_dirty_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "dirty.txt").write_text("dirty")
        result = git_all_dirty(cwd=tmp_git_repo)
        assert "dirty.txt" in result

    def test_deduplication(self, tmp_git_repo: Path) -> None:
        # Stage and modify the same file
        f = tmp_git_repo / "dup.txt"
        f.write_text("v1")
        subprocess.run(
            ["git", "add", "dup.txt"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        f.write_text("v2")  # now in both staged and unstaged
        result = git_all_dirty(cwd=tmp_git_repo)
        assert result.count("dup.txt") == 1


class TestSnapshotWorktree:
    def test_snapshot_contains_dirty_files(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "new.txt").write_text("content")
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        paths = [s.path for s in snapshot]
        assert "new.txt" in paths

    def test_snapshot_hash_is_git_hash(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "file.txt").write_text("test content")
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        entry = next(s for s in snapshot if s.path == "file.txt")
        assert entry.file_hash != "DELETED"
        assert len(entry.file_hash) == 40  # git hash is 40 hex chars


class TestChangedFilesSinceSnapshot:
    def test_modified_file_detected(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "track.txt").write_text("original")
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        (tmp_git_repo / "track.txt").write_text("modified")
        changed = changed_files_since_snapshot(snapshot, cwd=tmp_git_repo)
        assert "track.txt" in changed

    def test_unmodified_file_excluded(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "stable.txt").write_text("same")
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        # Don't modify — should not appear in changed
        changed = changed_files_since_snapshot(snapshot, cwd=tmp_git_repo)
        assert "stable.txt" not in changed

    def test_new_file_detected(self, tmp_git_repo: Path) -> None:
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        (tmp_git_repo / "brand_new.txt").write_text("new file")
        changed = changed_files_since_snapshot(snapshot, cwd=tmp_git_repo)
        assert "brand_new.txt" in changed

    def test_exclude_prefix(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "logs").mkdir()
        (tmp_git_repo / "logs" / "debug.log").write_text("log")
        (tmp_git_repo / "src.txt").write_text("src")
        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        (tmp_git_repo / "logs" / "debug.log").write_text("modified log")
        (tmp_git_repo / "src.txt").write_text("modified src")
        changed = changed_files_since_snapshot(
            snapshot, cwd=tmp_git_repo, exclude_prefix="logs/"
        )
        assert "src.txt" in changed
        assert not any(f.startswith("logs/") for f in changed)


class TestPushTriggerCommit:
    """Empty trigger commit used by ``--ci-trigger-mode last-only``."""

    @patch("mr_overkill.git_ops._run")
    def test_creates_empty_commit_and_pushes(self, mock_run: MagicMock) -> None:
        # 1) HEAD message has [skip ci], 2) commit succeeds,
        # 3) upstream check succeeds, 4) push succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="fix: something [skip ci]\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="origin/feat/x", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        assert push_trigger_commit() is True

        commit_call = mock_run.call_args_list[1]
        assert commit_call.args[0] == [
            "git", "commit", "--allow-empty", "-m", "chore: trigger CI",
        ]
        push_call = mock_run.call_args_list[3]
        assert push_call.args[0] == ["git", "push"]

    @patch("mr_overkill.git_ops._run")
    def test_commit_failure_returns_false_without_push(
        self, mock_run: MagicMock,
    ) -> None:
        # HEAD has [skip ci]; then commit attempt fails — no push should follow.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="fix: something [skip ci]\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="nothing to commit"),
        ]
        assert push_trigger_commit() is False
        assert mock_run.call_count == 2

    @patch("mr_overkill.git_ops._run")
    def test_sets_upstream_when_no_upstream_yet(
        self, mock_run: MagicMock,
    ) -> None:
        # HEAD has [skip ci], commit ok, upstream check fails,
        # remote check returns 'origin', push -u ok
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="fix: x [skip ci]\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=128, stdout="", stderr="no upstream"),
            MagicMock(returncode=0, stdout="origin\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        assert push_trigger_commit(branch="feat/x") is True
        push_call = mock_run.call_args_list[4]
        assert push_call.args[0] == [
            "git", "push", "-u", "origin", "feat/x",
        ]

    @patch("mr_overkill.git_ops._run")
    def test_skips_extra_commit_when_head_already_triggers_ci(
        self, mock_run: MagicMock,
    ) -> None:
        # HEAD lacks [skip ci] (trigger commit already exists from a prior
        # run that failed before pushing); only the push should be retried.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="chore: trigger CI\n", stderr=""),
            MagicMock(returncode=0, stdout="origin/feat/x", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        assert push_trigger_commit() is True
        # No new commit attempted; HEAD check + upstream check + push only.
        assert mock_run.call_count == 3
        assert mock_run.call_args_list[1].args[0] == [
            "git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        ]
        assert mock_run.call_args_list[2].args[0] == ["git", "push"]


class TestCommitAndPushPushFlag:
    """``push=False`` must skip the push outright.

    An empty *branch* argument is not enough: ``_push_current_branch`` checks
    for an upstream first and pushes whenever one exists, which a review/*
    branch the user already published does.
    """

    def _upstream_repo(self, tmp_git_repo: Path, tmp_path: Path) -> Path:
        """Give the repo a real remote with an upstream-tracked branch."""
        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(remote)], check=True, capture_output=True
        )
        for args in (
            ["remote", "add", "origin", str(remote)],
            ["push", "-u", "origin", "HEAD"],
        ):
            subprocess.run(
                ["git", *args], cwd=tmp_git_repo, check=True, capture_output=True
            )
        return remote

    def _head_of(self, repo: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_push_false_keeps_commit_local(
        self, tmp_git_repo: Path, tmp_path: Path
    ) -> None:
        remote = self._upstream_repo(tmp_git_repo, tmp_path)
        remote_before = self._head_of(remote)

        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        (tmp_git_repo / "fix.txt").write_text("a fix\n")

        assert commit_and_push(
            snapshot, "fix: local only", "", push=False, cwd=tmp_git_repo
        ) is True
        assert self._head_of(tmp_git_repo) != remote_before
        assert self._head_of(remote) == remote_before

    def test_push_true_publishes(
        self, tmp_git_repo: Path, tmp_path: Path
    ) -> None:
        remote = self._upstream_repo(tmp_git_repo, tmp_path)
        remote_before = self._head_of(remote)

        snapshot = snapshot_worktree(cwd=tmp_git_repo)
        (tmp_git_repo / "fix.txt").write_text("a fix\n")

        assert commit_and_push(
            snapshot, "fix: published", "", push=True, cwd=tmp_git_repo
        ) is True
        assert self._head_of(remote) != remote_before
        assert self._head_of(remote) == self._head_of(tmp_git_repo)


class TestCommitAndPushNoVerify:
    """WIP commits carry unfinished work, so the hooks that would reject it
    have to be skipped — and only there."""

    def _repo_with_failing_hook(self, repo: Path) -> None:
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def test_hooks_block_a_normal_commit(self, tmp_git_repo: Path) -> None:
        self._repo_with_failing_hook(tmp_git_repo)
        (tmp_git_repo / "a.py").write_text("a = 1\n")

        with pytest.raises(RuntimeError):
            commit_and_push([], "test: a", push=False, cwd=tmp_git_repo)

    def test_no_verify_gets_the_commit_through(self, tmp_git_repo: Path) -> None:
        self._repo_with_failing_hook(tmp_git_repo)
        (tmp_git_repo / "a.py").write_text("a = 1\n")

        assert commit_and_push(
            [], "test: a", push=False, no_verify=True, cwd=tmp_git_repo
        )
        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_git_repo, capture_output=True, text=True, check=True,
        )
        assert log.stdout.strip() == "test: a"
