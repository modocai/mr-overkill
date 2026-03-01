"""Tests for mr_overkill.git_ops — git operations."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mr_overkill.git_ops import (
    changed_files_since_snapshot,
    diff_hash,
    gen_uuid,
    git_all_dirty,
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
