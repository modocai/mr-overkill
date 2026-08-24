"""WIP scope review: include uncommitted working-tree changes in the review.

The normal review loop scopes itself with ``git diff <target>...<current>``,
which only ever sees **committed** work.  This module supplies the two
mechanisms that let uncommitted work be reviewed instead, picked by whether
the run is allowed to commit (see ``review_loop._prepare_wip_scope``):

*No-commit runs* (``--dry-run`` / ``--no-auto-commit``) get
:func:`write_worktree_diff`, a scope artefact handed to the reviewer the same
way ``commit_scope`` hands over a historical commit's patch.

*Committing runs* get :func:`create_scaffold_commit`, which parks the
uncommitted work in a throwaway commit so the loop's convergence machinery —
all of which reads the commit graph — runs completely unchanged.  The
scaffolding is torn down by :func:`unwind` when the loop finishes, leaving
zero new commits behind and the working tree dirty again.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from mr_overkill.git_ops import git_all_dirty

logger = logging.getLogger(__name__)

SCAFFOLD_MESSAGE = "wip: review-loop scaffolding [skip ci]"

# Log artefacts are not part of anyone's work in progress.  Same list as
# ``git_ops.commit_and_push``'s ``exclude_prefixes``.
_LOG_PREFIXES = (".overkill/logs/", ".review-loop/logs/")


def _run(
    cmd: list[str], cwd: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )


def uncommitted_files(cwd: Path | None = None) -> list[str]:
    """Dirty and untracked files that count as work in progress."""
    return [
        f
        for f in git_all_dirty(cwd)
        if not any(f.startswith(p) for p in _LOG_PREFIXES)
    ]


def _untracked_files(cwd: Path | None = None) -> list[str]:
    """Untracked, non-ignored files, excluding log artefacts."""
    result = _run(["git", "ls-files", "--others", "--exclude-standard"], cwd)
    return [
        f
        for f in (line.strip() for line in result.stdout.splitlines())
        if f and not any(f.startswith(p) for p in _LOG_PREFIXES)
    ]


def _staged_deletions(cwd: Path | None = None) -> list[str]:
    """Paths whose deletion is already staged, a rename's source included.

    ``git diff --cached --name-only`` reports a rename as its destination
    alone, so :func:`uncommitted_files` never sees the source: committing the
    destination by itself leaves the source's deletion staged, the tree dirty,
    and the loop's clean-tree check rejects the run.  ``--no-renames`` splits
    the rename back into the delete half the scaffolding has to cover too.
    """
    result = _run(
        [
            "git", "diff", "--cached", "--name-only",
            "--no-renames", "--diff-filter=D",
        ],
        cwd,
    )
    return [
        f
        for f in (line.strip() for line in result.stdout.splitlines())
        if f and not any(f.startswith(p) for p in _LOG_PREFIXES)
    ]


# Files git drops in the git dir while a multi-step operation is unfinished.
_IN_PROGRESS_MARKERS = {
    "MERGE_HEAD": "merge",
    "CHERRY_PICK_HEAD": "cherry-pick",
    "REVERT_HEAD": "revert",
    "rebase-merge": "rebase",
    "rebase-apply": "rebase",
}


def operation_in_progress(cwd: Path | None = None) -> str | None:
    """Name of an unfinished git operation, or ``None`` if the repo is idle.

    Committing in the middle of one would conclude it: a scaffolding commit
    made during a conflicted merge *is* the merge commit, and unwinding it
    resets past the merge and drops ``MERGE_HEAD``, leaving no way to finish
    what the author started.
    """
    result = _run(["git", "rev-parse", "--git-dir"], cwd)
    if result.returncode != 0:
        return None
    git_dir = Path(cwd or ".") / result.stdout.strip()
    for marker, name in _IN_PROGRESS_MARKERS.items():
        if (git_dir / marker).exists():
            return name
    return None


def merge_base(target: str, cwd: Path | None = None) -> str | None:
    """Fork point of *target* and HEAD, or ``None`` if they are unrelated."""
    result = _run(["git", "merge-base", target, "HEAD"], cwd)
    if result.returncode != 0:
        logger.error(
            "No merge base between '%s' and HEAD: %s",
            target,
            result.stderr.strip(),
        )
        return None
    return result.stdout.strip() or None


def _worktree_diff(commit: str, cwd: Path | None = None) -> str | None:
    """``git diff <commit>`` against the working tree; ``None`` if git failed.

    ``git diff <commit>`` compares the commit against the *working tree*, which
    is the whole point here: committed work and uncommitted edits land in one
    patch.

    Untracked files are invisible to ``git diff``, so they are staged as
    intent-to-add first — the trick ``self_review._generate_diff`` uses.  Only
    the untracked paths are added and only those are reset afterwards, so a
    user's existing staged/unstaged split survives untouched.
    """
    untracked = _untracked_files(cwd)
    if untracked:
        _run(
            ["git", "add", "--intent-to-add", "--pathspec-from-file=-"],
            cwd,
            stdin="\n".join(untracked),
        )
    try:
        result = _run(["git", "diff", commit], cwd)
        if result.returncode != 0:
            logger.error(
                "git diff %s failed (exit %d): %s",
                commit,
                result.returncode,
                result.stderr.strip(),
            )
            return None
        return result.stdout
    finally:
        if untracked:
            _run(
                ["git", "reset", "--quiet", "--pathspec-from-file=-"],
                cwd,
                stdin="\n".join(untracked),
            )


def write_worktree_diff(target: str, output: Path, cwd: Path | None = None) -> int:
    """Write the fork-point-to-working-tree diff to *output*; return byte count.

    Returns 0 when git fails or there is nothing to review; the caller is
    expected to treat that as fatal rather than reviewing an empty patch.
    """
    base = merge_base(target, cwd)
    if base is None:
        return 0
    diff = _worktree_diff(base, cwd)
    if diff is None:
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(diff, encoding="utf-8")
    return len(diff.encode("utf-8"))


def create_scaffold_commit(cwd: Path | None = None) -> str | None:
    """Commit every uncommitted change as a throwaway commit; return its SHA.

    ``--no-verify`` is deliberate: work in progress routinely fails the hooks
    it will pass by the time it is finished, and a hook rejecting a commit that
    exists only to be torn down again would make the mode unusable.
    """
    deleted = set(_staged_deletions(cwd))
    files = sorted(set(uncommitted_files(cwd)) | deleted)
    if not files:
        logger.error("Nothing to stage — no uncommitted work to scaffold.")
        return None

    # ``git add`` matches its pathspec against the working tree and the index
    # only, so naming a path that is in neither — a staged deletion, a staged
    # rename's source — makes it abort the whole add.  Those paths are already
    # staged as deleted, so they belong in the commit's pathspec, not here.
    root = Path(cwd or ".")
    to_add = [f for f in files if f not in deleted or (root / f).exists()]

    # Stage the explicit list rather than ``git add -A`` with an
    # ``:(exclude)`` pathspec: naming a gitignored path in a pathspec makes
    # git abort the whole add — after it has already staged part of it —
    # and the log directory is gitignored in plenty of repos.  ``-A`` still
    # applies to the listed paths, so deletions are staged too.
    if to_add:
        staged = _run(
            ["git", "add", "-A", "--pathspec-from-file=-"],
            cwd,
            stdin="\n".join(to_add),
        )
        if staged.returncode != 0:
            logger.error(
                "git add failed, and the index may be partially staged: %s",
                staged.stderr.strip(),
            )
            return None

    # ``git add -A`` sweeps in anything not covered by .gitignore, so show
    # exactly what is going into the commit.
    logger.info("Parking %d uncommitted file(s) in a scaffolding commit:", len(files))
    for f in files:
        logger.info("  %s", f)

    # Pathspec-limited like the add: the index may already hold a log artefact
    # the user staged themselves, and committing the whole index would put it in
    # the scaffolding commit — and so in the branch diff the reviewer reads —
    # despite being excluded from the scope above.  It stays staged, untouched.
    committed = _run(
        [
            "git", "commit", "--no-verify", "--quiet",
            "-m", SCAFFOLD_MESSAGE, "--pathspec-from-file=-",
        ],
        cwd,
        stdin="\n".join(files),
    )
    if committed.returncode != 0:
        # "nothing to commit" — a dirty submodule whose gitlink did not move
        # stages nothing — is explained on stdout, not stderr.
        detail = (
            committed.stderr.strip()
            or committed.stdout.strip()
            or f"exit {committed.returncode}"
        )
        logger.error(
            "Scaffolding commit failed, and anything it staged is left in "
            "the index (file contents are untouched): %s",
            detail,
        )
        return None

    head = _run(["git", "rev-parse", "HEAD"], cwd)
    sha = head.stdout.strip()
    logger.info("Scaffolding commit: %s", sha[:7])
    return sha or None


def head_is_scaffold(cwd: Path | None = None) -> bool:
    """Whether HEAD is itself a leftover scaffolding commit.

    The subject is the only signal that survives a wiped log directory, so it
    is what a fresh run has to go on: the metadata files are read back on
    ``--resume`` alone, and a run interrupted before its unwind is exactly the
    case that has to be caught here.
    """
    result = _run(["git", "log", "-1", "--format=%s"], cwd)
    return result.returncode == 0 and result.stdout.strip() == SCAFFOLD_MESSAGE


def is_in_head(commit: str, cwd: Path | None = None) -> bool:
    """Whether *commit* is an ancestor of HEAD (or HEAD itself)."""
    return _run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd
    ).returncode == 0


def save_metadata(log_dir: Path, base: str, scaffold: str | None) -> None:
    """Record where to unwind to, so a later ``--resume`` can find it.

    An interrupted --wip run is only recoverable while these survive: they are
    the sole record of where the scaffolding sits and what it was made on.  So
    they are written as soon as the scaffolding exists rather than once per
    run — a resume that re-parks the work replaces a SHA already dangling.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "wip-base.txt").write_text(base)
    (log_dir / "wip-scaffold.txt").write_text(scaffold or "")


def unwind(
    base: str,
    scaffold: str | None,
    log_dir: Path,
    cwd: Path | None = None,
    branch: str | None = None,
) -> bool:
    """Tear the scaffolding down: back to *base* with the changes uncommitted.

    ``git reset --mixed`` never touches file contents, so the fixes stay in the
    working tree — only the commits disappear.  The net change the loop made is
    saved to ``wip-fixes.diff`` first, because once the scaffolding commit is
    gone there is no way left to tell the user's own work from the AI's edits.
    """
    head = _run(["git", "rev-parse", "HEAD"], cwd)
    if head.stdout.strip() == base:
        return True

    # ``base`` alone is not enough to prove this branch is the one that was
    # scaffolded: a sibling branch off the same commit passes an ancestry test
    # against it and would be silently rewound.  The scaffolding commit is
    # unique to the run, so that is what has to be in HEAD's history.
    if not scaffold:
        logger.error(
            "Cannot unwind: no scaffolding commit was recorded. HEAD is left "
            "alone; check the history before resetting anything.",
        )
        return False
    if not is_in_head(scaffold, cwd):
        logger.error(
            "Cannot unwind: scaffolding commit %s is not in HEAD's history, so "
            "this is not the branch it was made on. HEAD is left alone.",
            scaffold[:7],
        )
        return False
    # Ancestry is not identity: the scaffolding commit is also in the history
    # of anything branched off it.  *branch* is the branch the scaffolding was
    # made on, so a mismatch here means the reset would rewind someone else's
    # commits into uncommitted changes.
    if branch:
        current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
        on = current.stdout.strip()
        if current.returncode != 0 or on != branch:
            logger.error(
                "Cannot unwind: the scaffolding was made on '%s' but HEAD is "
                "on '%s'. HEAD is left alone; switch back and undo it there: "
                "git reset --mixed %s",
                branch,
                on or "(unknown)",
                base,
            )
            return False

    # Diffed against the working tree, not HEAD: a fixer that edited files and
    # then failed leaves HEAD at the scaffolding commit, and those edits are
    # exactly what has to stay distinguishable from the user's own work once
    # the reset below mixes the two.
    fixes = _worktree_diff(scaffold, cwd)
    # An empty diff means the loop changed nothing, so there is nothing to
    # report — and writing it would replace an earlier attempt's artefact with
    # a file that says the loop changed nothing.
    if fixes and fixes.strip():
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "wip-fixes.diff").write_text(fixes, encoding="utf-8")

    reset = _run(["git", "reset", "--quiet", "--mixed", base], cwd)
    if reset.returncode != 0:
        logger.error(
            "git reset --mixed %s failed: %s", base[:7], reset.stderr.strip()
        )
        return False
    logger.info(
        "Scaffolding removed — back at %s with the changes uncommitted.", base[:7]
    )
    return True
