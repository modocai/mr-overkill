"""Commit-scope review: review an already-merged commit and improve it.

The normal review loop scopes itself with ``git diff <target>...<current>``.
A commit that already landed on the target branch is not in that diff, so the
loop exits ``NO_DIFF`` before reviewing anything.  This module supplies the
missing piece: it materialises the historical commit's diff to a file and
creates a throwaway ``review/*`` branch for the fixes to land on, which lets
the existing loop machinery run unchanged (see ``review_loop._prepare_commit_scope``).

Structurally this mirrors what ``refactor_suggest`` already does — create a
work branch, hand the reviewer a scope artefact, set ``skip_initial_no_diff``.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: The empty tree under SHA-1, git's default object format.  Only a fallback
#: for when git cannot be asked — :func:`empty_tree_sha` is the real answer,
#: because a repository created with ``--object-format=sha256`` has a
#: different one and this id does not resolve there at all.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def empty_tree_sha(cwd: Path | None = None) -> str:
    """The empty tree's object id in *this* repository's object format.

    Diffing against it turns a root commit — which has no parent to diff
    against — into a plain "everything was added" patch.  Asking git rather
    than hardcoding the SHA-1 value is what makes that work in a repository
    using SHA-256, where the hardcoded id resolves to nothing and the diff
    fails with a message about an empty commit instead.
    """
    result = _run(["git", "hash-object", "-t", "tree", "/dev/null"], cwd)
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        logger.debug(
            "Could not compute the empty tree id (%s); falling back to SHA-1.",
            result.stderr.strip() or f"exit {result.returncode}",
        )
        return EMPTY_TREE_SHA
    return sha


def resolve_commit(rev: str, cwd: Path | None = None) -> str | None:
    """Resolve *rev* to a full commit SHA, or ``None`` if it is not a commit.

    The ``^{commit}`` peel makes annotated tags resolve to the commit they
    point at and rejects revisions that name a tree or blob.
    """
    result = _run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def commit_base(sha: str, cwd: Path | None = None) -> str:
    """Return the revision *sha* should be diffed against.

    ``git show`` is not usable here: for a merge commit it prints a combined
    diff, which git suppresses by default, so the patch comes out **empty**.
    Most commits on a PR-merge branch are merge commits, so that failure would
    be the common case rather than the exotic one.  Diffing against the first
    parent gives a real patch for merges and is identical to ``git show`` for
    ordinary commits.

    Root commits have no parent at all; they diff against the empty tree.
    """
    result = _run(["git", "rev-list", "--parents", "-n", "1", sha], cwd)
    fields = result.stdout.split()
    if len(fields) < 2:
        return empty_tree_sha(cwd)
    return fields[1]


def is_merge_commit(sha: str, cwd: Path | None = None) -> bool:
    """True if *sha* has more than one parent."""
    result = _run(["git", "rev-list", "--parents", "-n", "1", sha], cwd)
    return len(result.stdout.split()) > 2


def commit_headline(sha: str, cwd: Path | None = None) -> str:
    """One-line description of *sha* for logs and the reviewer prompt."""
    result = _run(
        ["git", "log", "-1", "--format=%h — %s (%an, %ad)", "--date=short", sha],
        cwd,
    )
    return result.stdout.strip() or sha


def is_ancestor_of_head(sha: str, cwd: Path | None = None) -> bool:
    """True if *sha* is reachable from HEAD."""
    result = _run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd)
    return result.returncode == 0


def write_scope_diff(sha: str, output: Path, cwd: Path | None = None) -> int:
    """Write *sha*'s diff to *output* and return the byte count.

    Returns 0 when the commit is empty or git fails; the caller is expected to
    treat that as fatal rather than reviewing nothing.
    """
    base = commit_base(sha, cwd)
    result = _run(["git", "diff", base, sha], cwd)
    if result.returncode != 0:
        logger.error(
            "git diff %s %s failed (exit %d): %s",
            base,
            sha,
            result.returncode,
            result.stderr.strip(),
        )
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.stdout, encoding="utf-8")
    return len(result.stdout.encode("utf-8"))


def review_branch_name(sha: str) -> str:
    """Branch name for a commit-scope run: ``review/<short-sha>-<timestamp>``."""
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return f"review/{sha[:7]}-{ts}"


def create_branch_at_head(branch: str, cwd: Path | None = None) -> bool:
    """Create and switch to *branch* at the current HEAD.

    No stash dance is needed here (unlike ``refactor_suggest``, which branches
    from a *different* commit): branching from HEAD moves no files, so a dirty
    working tree carries over untouched.
    """
    result = _run(["git", "checkout", "-b", branch], cwd)
    if result.returncode != 0:
        logger.error("Failed to create branch %s: %s", branch, result.stderr.strip())
        return False
    logger.info("Created branch: %s", branch)
    return True
