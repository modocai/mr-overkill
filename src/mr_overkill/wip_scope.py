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


def write_worktree_diff(target: str, output: Path, cwd: Path | None = None) -> int:
    """Write the fork-point-to-working-tree diff to *output*; return byte count.

    Returns 0 when git fails or there is nothing to review; the caller is
    expected to treat that as fatal rather than reviewing an empty patch.

    Untracked files are invisible to ``git diff``, so they are staged as
    intent-to-add first — the trick ``self_review._generate_diff`` uses.  Only
    the untracked paths are added and only those are reset afterwards, so a
    user's existing staged/unstaged split survives untouched.
    """
    base = merge_base(target, cwd)
    if base is None:
        return 0

    untracked = _untracked_files(cwd)
    if untracked:
        _run(
            ["git", "add", "--intent-to-add", "--pathspec-from-file=-"],
            cwd,
            stdin="\n".join(untracked),
        )
    try:
        # ``git diff <commit>`` compares the commit against the *working tree*,
        # which is the whole point here: committed branch work and uncommitted
        # edits land in one patch.
        result = _run(["git", "diff", base], cwd)
        if result.returncode != 0:
            logger.error(
                "git diff %s failed (exit %d): %s",
                base,
                result.returncode,
                result.stderr.strip(),
            )
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.stdout, encoding="utf-8")
        return len(result.stdout.encode("utf-8"))
    finally:
        if untracked:
            _run(
                ["git", "reset", "--quiet", "--pathspec-from-file=-"],
                cwd,
                stdin="\n".join(untracked),
            )


def create_scaffold_commit(cwd: Path | None = None) -> str | None:
    """Commit every uncommitted change as a throwaway commit; return its SHA.

    ``--no-verify`` is deliberate: work in progress routinely fails the hooks
    it will pass by the time it is finished, and a hook rejecting a commit that
    exists only to be torn down again would make the mode unusable.
    """
    files = uncommitted_files(cwd)
    if not files:
        logger.error("Nothing to stage — no uncommitted work to scaffold.")
        return None

    # Stage the explicit list rather than ``git add -A`` with an
    # ``:(exclude)`` pathspec: naming a gitignored path in a pathspec makes
    # git abort the whole add — after it has already staged part of it —
    # and the log directory is gitignored in plenty of repos.  ``-A`` still
    # applies to the listed paths, so deletions are staged too.
    staged = _run(
        ["git", "add", "-A", "--pathspec-from-file=-"],
        cwd,
        stdin="\n".join(files),
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

    committed = _run(
        ["git", "commit", "--no-verify", "--quiet", "-m", SCAFFOLD_MESSAGE], cwd
    )
    if committed.returncode != 0:
        logger.error("Scaffolding commit failed: %s", committed.stderr.strip())
        return None

    head = _run(["git", "rev-parse", "HEAD"], cwd)
    sha = head.stdout.strip()
    logger.info("Scaffolding commit: %s", sha[:7])
    return sha or None


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

    result = _run(["git", "diff", scaffold, "HEAD"], cwd)
    # An empty diff means the loop committed nothing, so there is nothing to
    # report — and writing it would replace an earlier attempt's artefact with
    # a file that says the loop changed nothing.
    if result.returncode == 0 and result.stdout.strip():
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "wip-fixes.diff").write_text(result.stdout, encoding="utf-8")

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
