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

# ``:(top)`` anchors a pathspec at the repository root regardless of cwd.
_ADD_PATHSPEC = [
    ":(top)",
    ":(exclude,top).overkill/logs",
    ":(exclude,top).review-loop/logs",
]


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
    staged = _run(["git", "add", "-A", "--", *_ADD_PATHSPEC], cwd)
    if staged.returncode != 0:
        logger.error("git add failed: %s", staged.stderr.strip())
        return None

    listing = _run(["git", "diff", "--cached", "--name-only"], cwd)
    files = [f for f in listing.stdout.splitlines() if f.strip()]
    if not files:
        logger.error("Nothing to stage — no uncommitted work to scaffold.")
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
    if scaffold:
        result = _run(["git", "diff", scaffold, "HEAD"], cwd)
        if result.returncode == 0:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "wip-fixes.diff").write_text(
                result.stdout, encoding="utf-8"
            )

    head = _run(["git", "rev-parse", "HEAD"], cwd)
    if head.stdout.strip() == base:
        return True

    # Resetting to a commit HEAD does not descend from would move the branch
    # somewhere it has never been.  Refuse rather than guess.
    ancestry = _run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd)
    if ancestry.returncode != 0:
        logger.error(
            "Cannot unwind: %s is not an ancestor of HEAD. The scaffolding "
            "commit is still in place — reset manually once you have checked "
            "what happened.",
            base[:7],
        )
        return False

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
