"""Git operations for the review/refactor loop.

Ports git-related functions from ``common.sh``: sha256, diff_hash, gen_uuid,
git_all_dirty, snapshot_worktree, changed_files_since_snapshot,
stash_allowlisted, unstash_allowlisted, commit_and_push.

Key improvement: ``changed_files_since_snapshot`` uses a dict for O(1) lookups
instead of the O(n²) awk scan in the bash version.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import uuid
from pathlib import Path

from mr_overkill.models import WorktreeSnapshot

logger = logging.getLogger(__name__)


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with common defaults."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input,
        capture_output=True,
        text=True,
        check=False,
    )


def sha256(data: str | bytes) -> str:
    """Compute SHA-256 hex digest of *data*."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def diff_hash(target_branch: str, current_branch: str, cwd: Path | None = None) -> str:
    """SHA-256 of the diff between *target_branch* and *current_branch*."""
    result = _run(["git", "diff", f"{target_branch}...{current_branch}"], cwd=cwd)
    return sha256(result.stdout)


def gen_uuid() -> str:
    """Generate a lowercase UUID v4."""
    return str(uuid.uuid4())


def git_all_dirty(cwd: Path | None = None) -> list[str]:
    """List all dirty/untracked files (unique, deduplicated)."""
    files: set[str] = set()

    for cmd in [
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]:
        result = _run(cmd, cwd=cwd)
        for f in result.stdout.splitlines():
            f = f.strip()
            if f:
                files.add(f)

    return sorted(files)


def snapshot_worktree(cwd: Path | None = None) -> list[WorktreeSnapshot]:
    """Snapshot every dirty/untracked file's hash + mode.

    Returns a list of :class:`WorktreeSnapshot` entries.
    """
    entries: list[WorktreeSnapshot] = []
    base = Path(cwd) if cwd else Path.cwd()

    for f in git_all_dirty(cwd):
        fpath = base / f
        if fpath.is_file():
            result = _run(["git", "hash-object", f], cwd=cwd)
            file_hash = result.stdout.strip() or "UNHASHABLE"
            import os

            mode = "100755" if os.access(fpath, os.X_OK) else "100644"
            entries.append(WorktreeSnapshot(file_hash=file_hash, mode=mode, path=f))
        else:
            entries.append(WorktreeSnapshot(file_hash="DELETED", mode="000000", path=f))

    return entries


def changed_files_since_snapshot(
    snapshot: list[WorktreeSnapshot],
    cwd: Path | None = None,
    exclude_prefix: str | None = None,
    exclude_prefixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Compare current dirty files against a snapshot.

    Returns list of file paths that changed (new, modified, or deleted).
    Uses a dict for O(1) lookups (vs O(n²) awk in bash).
    """
    _prefixes = exclude_prefixes or ((exclude_prefix,) if exclude_prefix else ())
    snap_map: dict[str, tuple[str, str]] = {
        s.path: (s.file_hash, s.mode) for s in snapshot
    }
    base = Path(cwd) if cwd else Path.cwd()
    changed: list[str] = []

    for f in git_all_dirty(cwd):
        if _prefixes and any(f.startswith(p) for p in _prefixes):
            continue

        fpath = base / f
        if fpath.is_file():
            result = _run(["git", "hash-object", f], cwd=cwd)
            cur_hash = result.stdout.strip() or "UNHASHABLE"
            import os

            cur_mode = "100755" if os.access(fpath, os.X_OK) else "100644"
        else:
            cur_hash = "DELETED"
            cur_mode = "000000"

        prev = snap_map.get(f)
        if prev is None or cur_hash != prev[0] or cur_mode != prev[1]:
            changed.append(f)

    return changed


def resume_reset_worktree(cwd: Path | None = None) -> None:
    """Stash uncommitted changes and reset worktree for a clean resume.

    Mirrors ``_resume_reset_working_tree`` from the bash version:
    safety-stash any dirty state, then ``git reset HEAD`` + ``git checkout``.
    """
    dirty = git_all_dirty(cwd)
    if dirty:
        logger.info("Stashing uncommitted changes before resume reset...")
        result = _run(
            ["git", "stash", "push", "--include-untracked",
             "-m", "review-loop: pre-resume safety stash"],
            cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to stash uncommitted changes. "
                "Aborting resume to prevent data loss."
            )

    toplevel = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    top = toplevel.stdout.strip()
    logger.info("Resetting partial edits from interrupted run...")
    _run(["git", "reset", "--quiet", "HEAD"], cwd=cwd)
    result = _run(["git", "checkout", "--", "."], cwd=Path(top))
    if result.returncode != 0:
        logger.warning("git checkout failed during resume reset.")


def stash_allowlisted(files: list[str], cwd: Path | None = None) -> bool:
    """Stash specific allowlisted files if dirty.

    Returns ``True`` if files were stashed, ``False`` if nothing to stash.
    Raises ``RuntimeError`` on stash failure.
    """
    dirty = set(git_all_dirty(cwd))
    to_stash = [f for f in files if f in dirty]

    if not to_stash:
        return False

    result = _run(
        ["git", "stash", "push", "--quiet", "--include-untracked", "--", *to_stash],
        cwd=cwd,
    )
    if result.returncode != 0:
        msg = "Failed to stash allowlisted files"
        raise RuntimeError(msg)
    return True


def unstash_allowlisted(cwd: Path | None = None) -> bool:
    """Pop the most recent stash entry.

    Returns ``True`` on success, ``False`` on failure.
    """
    result = _run(["git", "stash", "pop", "--index", "--quiet"], cwd=cwd)
    if result.returncode == 0:
        return True

    result = _run(["git", "stash", "pop", "--quiet"], cwd=cwd)
    return result.returncode == 0


def commit_and_push(
    snapshot: list[WorktreeSnapshot],
    message: str,
    branch: str = "",
    push: bool = True,
    no_verify: bool = False,
    cwd: Path | None = None,
) -> bool:
    """Commit files changed since snapshot and push if upstream exists.

    ``push=False`` keeps the commit local unconditionally.  An empty *branch*
    is not enough for that: ``_push_current_branch`` pushes whenever an
    upstream already exists, which a resumed review branch may well have.

    ``no_verify=True`` skips commit hooks.  Only throwaway commits should ask
    for it — see the WIP scaffolding in ``wip_scope``.

    Returns ``True`` if a commit was made, ``False`` if nothing to commit.
    Raises ``RuntimeError`` if ``git commit`` or ``git push`` fails.
    """
    changed = changed_files_since_snapshot(
        snapshot, cwd=cwd,
        exclude_prefixes=(".overkill/logs/", ".review-loop/logs/"),
    )
    if not changed:
        logger.info("No file changes after fix — nothing to commit.")
        return False

    # Stage changed files
    pathspec = "\n".join(changed)
    _run(["git", "add", "--pathspec-from-file=-"], cwd=cwd, input=pathspec)

    # Commit
    result = _run(
        [
            "git", "commit",
            *(["--no-verify"] if no_verify else []),
            "-m", message, "--pathspec-from-file=-",
        ],
        cwd=cwd,
        input=pathspec,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr.strip()}")

    logger.info("Committed.")

    if push:
        _push_current_branch(branch=branch, cwd=cwd)
    else:
        logger.info("Push disabled — commit stays local.")
    return True


def _push_current_branch(branch: str = "", cwd: Path | None = None) -> None:
    """Push the current branch, setting upstream on first push if needed."""
    upstream_check = _run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=cwd,
    )
    if upstream_check.returncode == 0:
        push_result = _run(["git", "push"], cwd=cwd)
        if push_result.returncode != 0:
            raise RuntimeError(f"git push failed: {push_result.stderr.strip()}")
        logger.info("Pushed.")
        return
    if branch:
        remote_check = _run(["git", "remote"], cwd=cwd)
        remotes = remote_check.stdout.strip().splitlines()
        remote = "origin" if "origin" in remotes else (remotes[0] if remotes else "")
        if remote:
            push_result = _run(["git", "push", "-u", remote, branch], cwd=cwd)
            if push_result.returncode != 0:
                raise RuntimeError(f"git push failed: {push_result.stderr.strip()}")
            logger.info("Pushed (upstream set).")
            return
        logger.info("No upstream/remote set — skipping push.")
        return
    logger.info("No upstream set — skipping push.")


def push_trigger_commit(branch: str = "", cwd: Path | None = None) -> bool:
    """Push an empty ``chore: trigger CI`` commit.

    Used by ``--ci-trigger-mode last-only`` to fire CI once after a run of
    iteration commits that were each tagged with ``[skip ci]``. Returns
    ``True`` on success, ``False`` if the empty commit could not be created
    (e.g. repo has no HEAD yet). Raises ``RuntimeError`` if ``git push`` fails.

    Idempotent: if HEAD already lacks ``[skip ci]`` (a prior call succeeded
    locally but the push may not have reached the remote), skip creating
    another empty commit and just retry the push.
    """
    head_msg = _run(["git", "log", "-1", "--pretty=%B"], cwd=cwd).stdout
    if head_msg and "[skip ci]" not in head_msg:
        logger.info("HEAD already triggers CI — retrying push only.")
        _push_current_branch(branch=branch, cwd=cwd)
        return True

    result = _run(
        ["git", "commit", "--allow-empty", "-m", "chore: trigger CI"],
        cwd=cwd,
    )
    if result.returncode != 0:
        logger.warning(
            "trigger commit failed: %s",
            result.stderr.strip() or result.stdout.strip(),
        )
        return False
    logger.info("Created CI trigger commit.")
    _push_current_branch(branch=branch, cwd=cwd)
    return True
