"""Tests for commit_scope — run against a real git repo, not mocks.

The whole point of this module is that git's own behaviour is surprising here
(``git show`` silently prints nothing for a merge commit, root commits have no
parent to diff against), so mocking subprocess would test our assumptions
rather than git's actual output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from mr_overkill.commit_scope import (
    EMPTY_TREE_SHA,
    commit_base,
    commit_headline,
    create_branch_at_head,
    is_ancestor_of_head,
    is_merge_commit,
    resolve_commit,
    review_branch_name,
    write_scope_diff,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit(repo: Path, name: str, body: str, message: str) -> str:
    (repo / name).write_text(body)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_merge(repo: Path) -> str:
    """Build a real merge commit and return its SHA."""
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side.txt", "from the side branch\n", "side: add file")
    _git(repo, "checkout", "-q", "-")
    _git(repo, "reset", "-q", "--hard", base)
    _commit(repo, "main.txt", "from the main branch\n", "main: add file")
    _git(repo, "merge", "--no-ff", "-m", "merge side", "side")
    return _git(repo, "rev-parse", "HEAD")


class TestCommitBase:
    def test_normal_commit_uses_parent(self, tmp_git_repo: Path) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        assert commit_base(sha, tmp_git_repo) == root

    def test_root_commit_uses_empty_tree(self, tmp_git_repo: Path) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        assert commit_base(root, tmp_git_repo) == EMPTY_TREE_SHA

    def test_merge_commit_uses_first_parent(self, tmp_git_repo: Path) -> None:
        merge = _make_merge(tmp_git_repo)
        first_parent = _git(tmp_git_repo, "rev-parse", f"{merge}^1")
        assert commit_base(merge, tmp_git_repo) == first_parent


class TestIsMergeCommit:
    def test_normal(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        assert is_merge_commit(sha, tmp_git_repo) is False

    def test_merge(self, tmp_git_repo: Path) -> None:
        assert is_merge_commit(_make_merge(tmp_git_repo), tmp_git_repo) is True


class TestWriteScopeDiff:
    def test_normal_commit(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        out = tmp_git_repo / "scope.diff"
        size = write_scope_diff(sha, out, tmp_git_repo)
        assert size > 0
        assert "+hello" in out.read_text()

    def test_merge_commit_is_not_empty(self, tmp_git_repo: Path) -> None:
        """The regression this whole module exists for.

        ``git show <merge>`` prints a combined diff, which git suppresses by
        default — the patch comes out empty.  On a PR-merge workflow that is
        the *common* case, so an empty scope diff would silently skip the
        review for most commits a user would want to improve.
        """
        merge = _make_merge(tmp_git_repo)
        shown = subprocess.run(
            ["git", "show", "--format=", merge],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert shown.strip() == "", "precondition: git show is empty for a merge"

        out = tmp_git_repo / "scope.diff"
        assert write_scope_diff(merge, out, tmp_git_repo) > 0
        assert "side.txt" in out.read_text()

    def test_root_commit(self, tmp_git_repo: Path) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        out = tmp_git_repo / "scope.diff"
        assert write_scope_diff(root, out, tmp_git_repo) > 0
        assert "README.md" in out.read_text()

    def test_empty_commit_returns_zero(self, tmp_git_repo: Path) -> None:
        _git(tmp_git_repo, "commit", "--allow-empty", "-m", "chore: trigger CI")
        sha = _git(tmp_git_repo, "rev-parse", "HEAD")
        out = tmp_git_repo / "scope.diff"
        assert write_scope_diff(sha, out, tmp_git_repo) == 0

    def test_creates_parent_directory(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        out = tmp_git_repo / "logs" / "nested" / "scope.diff"
        assert write_scope_diff(sha, out, tmp_git_repo) > 0
        assert out.is_file()


class TestResolveCommit:
    def test_full_and_short_sha(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        assert resolve_commit(sha, tmp_git_repo) == sha
        assert resolve_commit(sha[:7], tmp_git_repo) == sha

    def test_head_relative(self, tmp_git_repo: Path) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        assert resolve_commit("HEAD~1", tmp_git_repo) == root

    def test_annotated_tag_peels_to_commit(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        _git(tmp_git_repo, "tag", "-a", "v1.0", "-m", "release")
        assert resolve_commit("v1.0", tmp_git_repo) == sha

    def test_rejects_tree(self, tmp_git_repo: Path) -> None:
        assert resolve_commit("HEAD^{tree}", tmp_git_repo) is None

    def test_rejects_unknown(self, tmp_git_repo: Path) -> None:
        assert resolve_commit("nope123", tmp_git_repo) is None


class TestIsAncestorOfHead:
    def test_true_for_ancestor(self, tmp_git_repo: Path) -> None:
        root = _git(tmp_git_repo, "rev-parse", "HEAD")
        _commit(tmp_git_repo, "a.txt", "hello\n", "add a")
        assert is_ancestor_of_head(root, tmp_git_repo) is True

    def test_false_for_sibling_branch(self, tmp_git_repo: Path) -> None:
        base = _git(tmp_git_repo, "rev-parse", "HEAD")
        _git(tmp_git_repo, "checkout", "-q", "-b", "side")
        sibling = _commit(tmp_git_repo, "side.txt", "side\n", "side commit")
        _git(tmp_git_repo, "checkout", "-q", "-")
        _git(tmp_git_repo, "reset", "-q", "--hard", base)
        assert is_ancestor_of_head(sibling, tmp_git_repo) is False


class TestCommitHeadline:
    def test_includes_subject(self, tmp_git_repo: Path) -> None:
        sha = _commit(tmp_git_repo, "a.txt", "hello\n", "fix: something real")
        assert "fix: something real" in commit_headline(sha, tmp_git_repo)

    def test_falls_back_to_sha(self, tmp_git_repo: Path) -> None:
        assert commit_headline("nope123", tmp_git_repo) == "nope123"


class TestReviewBranchName:
    def test_format(self) -> None:
        name = review_branch_name("abcdef1234567890")
        assert name.startswith("review/abcdef1-")
        stamp = name.rsplit("-", 2)[-2:]
        assert len(stamp[0]) == 8 and stamp[0].isdigit()
        assert len(stamp[1]) == 6 and stamp[1].isdigit()


class TestCreateBranchAtHead:
    def test_success(self, tmp_git_repo: Path) -> None:
        assert create_branch_at_head("review/abc-1", tmp_git_repo) is True
        assert _git(tmp_git_repo, "rev-parse", "--abbrev-ref", "HEAD") == "review/abc-1"

    def test_keeps_dirty_files(self, tmp_git_repo: Path) -> None:
        """Branching from HEAD moves no files, so no stash dance is needed."""
        (tmp_git_repo / "wip.txt").write_text("uncommitted\n")
        assert create_branch_at_head("review/abc-2", tmp_git_repo) is True
        assert (tmp_git_repo / "wip.txt").read_text() == "uncommitted\n"

    def test_duplicate_name_fails(self, tmp_git_repo: Path) -> None:
        assert create_branch_at_head("review/abc-3", tmp_git_repo) is True
        _git(tmp_git_repo, "checkout", "-q", "-")
        assert create_branch_at_head("review/abc-3", tmp_git_repo) is False
