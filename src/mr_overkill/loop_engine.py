"""Unified review-fix loop engine.

Uses Protocol-based DI for external operations (fix, review, budget)
and imports Wave 1 modules directly for JSON parsing, git ops, and reporting.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Protocol

from mr_overkill.git_ops import (
    commit_and_push,
    diff_hash,
    git_all_dirty,
    resume_reset_worktree,
    snapshot_worktree,
    stash_allowlisted,
    unstash_allowlisted,
)
from mr_overkill.json_extract import parse_review_json
from mr_overkill.models import (
    BudgetTimeoutError,
    FinalStatus,
    FixFn,
    LoopConfig,
    LoopResult,
    WorktreeSnapshot,
)
from mr_overkill.reporting import generate_summary, post_pr_comment
from mr_overkill.resume import detect_state

logger = logging.getLogger(__name__)

_BUDGET_TIMEOUT_STATUS: dict[str, FinalStatus] = {
    "claude": FinalStatus.CLAUDE_BUDGET_TIMEOUT,
    "codex": FinalStatus.CODEX_BUDGET_TIMEOUT,
    "gemini": FinalStatus.GEMINI_BUDGET_TIMEOUT,
}
_ERROR_STATUS: dict[str, FinalStatus] = {
    "claude": FinalStatus.CLAUDE_ERROR,
    "codex": FinalStatus.CODEX_ERROR,
    "gemini": FinalStatus.GEMINI_ERROR,
}


# ── Additional Protocol for the review step ──────────────────────────


class ReviewerFn(Protocol):
    """Contract for running a code review (e.g. Codex review).

    Parameters
    ----------
    output_path : Path
        File to write review output to.
    iteration : int
        Current loop iteration number.

    Returns
    -------
    bool
        True on success, False on failure.
    """

    def __call__(self, output_path: Path, iteration: int) -> bool: ...


class SelfReviewFn(Protocol):
    """Contract for the self-review sub-loop.

    Parameters
    ----------
    pre_fix_snapshot : list[WorktreeSnapshot]
        Worktree snapshot from before fixes were applied.
    max_subloop : int
        Maximum sub-iterations.
    log_dir : Path
        Directory for log files.
    iteration : int
        Outer iteration number.
    review_json_str : str
        Original review findings as JSON string.

    Returns
    -------
    str
        Summary string of self-review sub-iterations.
    """

    def __call__(
        self,
        pre_fix_snapshot: list[WorktreeSnapshot],
        max_subloop: int,
        log_dir: Path,
        iteration: int,
        review_json_str: str,
    ) -> str: ...


class PreFixConfirmFn(Protocol):
    """Optional confirmation gate called after review parsing, before fixing.

    Returns True to proceed, False to abort the loop.
    """

    def __call__(self, review_data: dict[str, object]) -> bool: ...


# ── Normalize review JSON paths ──────────────────────────────────────


def _normalize_paths(review: dict[str, object], cwd: Path | None) -> dict[str, object]:
    """Normalize absolute paths in review JSON to repo-relative."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return review

    from mr_overkill.json_extract import normalize_paths

    return normalize_paths(review, result.stdout.strip())


# ── Main loop engine ─────────────────────────────────────────────────


def review_fix_loop(
    config: LoopConfig,
    *,
    reviewer: ReviewerFn,
    fixer: FixFn,
    self_reviewer: SelfReviewFn | None = None,
    pre_fix_confirm: PreFixConfirmFn | None = None,
    commit_pattern: str = "fix(ai-review): apply iteration",
    cwd: Path | None = None,
) -> LoopResult:
    """Run the review-fix loop.

    Orchestrates: resume detection → review → parse → fix → self-review →
    commit → PR comment → summary generation.

    Parameters
    ----------
    config : LoopConfig
        Full loop configuration.
    reviewer : ReviewerFn
        Callable that runs the code review step.
    fixer : FixFn
        Callable that applies fixes based on review findings.
    self_reviewer : SelfReviewFn, optional
        Callable that runs self-review sub-loop after fixes.
    pre_fix_confirm : PreFixConfirmFn, optional
        Callable invoked after review parsing but before fixing.
        Receives the parsed review dict; returns False to abort.
    commit_pattern : str
        Git log search pattern for resume detection.
    cwd : Path, optional
        Working directory for git operations.
    """
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Resume detection ─────────────────────────────────────────────
    resume_from = 1
    reuse_review = False

    if config.resume:
        state = detect_state(log_dir, commit_pattern, cwd=cwd)

        if state.status == "no_logs":
            logger.error("No previous logs found in %s. Nothing to resume.", log_dir)
            return LoopResult(
                final_status=FinalStatus.REVIEW_FAILED,
                iterations_run=0,
            )

        # Validate that saved metadata matches the current run
        branch_file = log_dir / "branch.txt"
        target_file = log_dir / "target-branch.txt"
        saved_branch = (
            branch_file.read_text().strip()
            if branch_file.is_file() else None
        )
        saved_target = (
            target_file.read_text().strip()
            if target_file.is_file() else None
        )
        if not saved_branch:
            logger.error(
                "Resume metadata (branch.txt) missing in %s"
                " — cannot verify branch safety.",
                log_dir,
            )
            return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)
        if saved_branch != config.current_branch:
            logger.error(
                "Resume branch mismatch: logs are from '%s'"
                " but current branch is '%s'.",
                saved_branch, config.current_branch,
            )
            return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)
        if saved_target and saved_target != config.target_branch:
            logger.error(
                "Resume target mismatch: logs target '%s' but current target is '%s'.",
                saved_target, config.target_branch,
            )
            return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)
        # Resuming a commit-scope run as a plain review (or vice versa) would
        # reuse logs describing a different review scope entirely.
        scope_file = log_dir / "scope-commit.txt"
        saved_scope = (
            scope_file.read_text().strip() if scope_file.is_file() else ""
        )
        if saved_scope != (config.scope_commit or ""):
            logger.error(
                "Resume scope mismatch: logs are for commit '%s' "
                "but this run has '%s'.",
                saved_scope or "(none)",
                config.scope_commit or "(none)",
            )
            return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)
        # Resuming a scaffolded --wip run without its base would leave the
        # scaffolding commit behind for good; resuming a plain run as --wip
        # would scaffold on top of someone else's logs.
        wip_file = log_dir / "wip-base.txt"
        saved_wip = wip_file.read_text().strip() if wip_file.is_file() else ""
        if saved_wip != (config.wip_base or ""):
            logger.error(
                "Resume WIP mismatch: logs were scaffolded at '%s' "
                "but this run has '%s'.",
                saved_wip or "(none)",
                config.wip_base or "(none)",
            )
            return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)

        # Already-completed runs can short-circuit after branch validation
        if state.status == "completed":
            resolved_status = FinalStatus(state.prev_status or "max_iterations_reached")
            # Re-attempt the CI trigger push: a prior run may have written
            # the terminal summary but failed (or been interrupted) before
            # pushing the trigger commit, leaving the remote on a [skip ci]
            # commit. push_trigger_commit is idempotent.
            needs_trigger = (
                resolved_status == FinalStatus.ALL_CLEAR
                and config.ci_trigger_mode in ("last-only", "none")
                and _has_skipped_fix_commit(commit_pattern, log_dir, cwd)
            )
            return LoopResult(
                final_status=resolved_status,
                iterations_run=0,
                summary_path=_generate_summary_safe(config, resolved_status),
                made_skipped_fix_commit=needs_trigger,
            )

        resume_from = state.resume_from
        reuse_review = state.reuse_review
        logger.info(
            "Resuming from iteration %d (reuse_review=%s)",
            resume_from,
            reuse_review,
        )

        # Reset partial edits from interrupted run (non-dry-run only)
        if not config.dry_run:
            if not _validate_target_branch(config.target_branch, cwd):
                logger.error(
                    "Target branch '%s' does not exist.",
                    config.target_branch,
                )
                return LoopResult(
                    final_status=FinalStatus.REVIEW_FAILED,
                    iterations_run=0,
                )
            resume_reset_worktree(cwd=cwd)

    # ── Validate target branch (before any destructive operations) ───
    if not _validate_target_branch(config.target_branch, cwd):
        logger.error("Target branch '%s' does not exist.", config.target_branch)
        return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)

    # ── Validate resume point ────────────────────────────────────────
    if resume_from > config.max_loop:
        logger.error(
            "Resume point (%d) exceeds max_loop (%d). "
            "Use -n to set a higher max_loop or start a fresh run.",
            resume_from, config.max_loop,
        )
        return LoopResult(final_status=FinalStatus.REVIEW_FAILED, iterations_run=0)

    # ── Clean working tree check ────────────────────────────────────
    # Reject non-allowlisted dirty files in non-dry-run mode to prevent
    # user WIP from being mixed into AI auto-fix commits.  A --wip run that
    # makes no commits has nothing to mix them into, so the check does not
    # apply there; a --wip run that does commit has already parked the work
    # in its scaffolding commit and reaches this point with a clean tree.
    if not config.dry_run and not (config.wip and not config.auto_commit):
        non_allowed = _reject_dirty_worktree(cwd)
        if non_allowed:
            logger.error(
                "Working tree is not clean. Commit or stash your changes "
                "before running review-loop, or pass --wip to include them "
                "in the review.\n  Dirty files: %s",
                ", ".join(non_allowed),
            )
            return LoopResult(
                final_status=FinalStatus.REVIEW_FAILED, iterations_run=0,
            )

    # ── Fresh-run cleanup (after all preflight checks pass) ───────
    if not config.resume:
        _clean_stale_logs(log_dir)
        _save_metadata(config, cwd)

    # ── Main loop ────────────────────────────────────────────────────
    final_status = FinalStatus.MAX_ITERATIONS_REACHED
    iterations_run = 0
    allowed_stashed = False
    had_findings = False
    fix_committed = False
    # On resume, prior iteration commits already exist; in last-only/none
    # modes those carry [skip ci], so a final trigger commit is still needed
    # even if no new fix commit is made in this process.
    made_skipped_fix_commit = (
        config.resume
        and resume_from > 1
        and config.ci_trigger_mode in ("last-only", "none")
    )

    for i in range(1, config.max_loop + 1):
        logger.info("── Iteration %d / %d ──", i, config.max_loop)

        # Skip completed iterations on resume
        if i < resume_from:
            logger.info("[resume] Skipping iteration %d (already completed).", i)
            continue

        # a. Check diff (skip on first iteration for refactor-created branches)
        try:
            no_diff = _no_diff(config.target_branch, config.current_branch, cwd)
        except RuntimeError as exc:
            logger.error("git diff check failed: %s", exc)
            final_status = FinalStatus.REVIEW_FAILED
            break

        if no_diff:
            if i == 1 and config.skip_initial_no_diff:
                logger.info(
                    "No diff on iteration 1 "
                    "(expected for a freshly created work branch)."
                )
            elif had_findings and not fix_committed:
                if config.scope_commit:
                    # The scope note tells the reviewer to confirm every
                    # finding against the current working tree, so a no-op
                    # fixer means confirmed defects are still unresolved —
                    # report that rather than the generic fixer error.
                    logger.warning(
                        "Fixer produced no code changes for the reported "
                        "findings — they were either stale or left unfixed.",
                    )
                    final_status = FinalStatus.FINDINGS_UNFIXED
                else:
                    logger.error(
                        "No diff after fix — previous iteration had findings but "
                        "fixer produced no code changes.",
                    )
                    final_status = FinalStatus.CLAUDE_ERROR
                break
            elif had_findings:
                logger.warning(
                    "Branch now matches %s after fix — nothing to merge.",
                    config.target_branch,
                )
                final_status = FinalStatus.NO_DIFF
                break
            else:
                logger.info(
                    "No diff between %s and %s.",
                    config.target_branch,
                    config.current_branch,
                )
                final_status = FinalStatus.NO_DIFF
                break

        # b. Run review
        review_file = log_dir / f"review-{i}.json"

        # Check if we can reuse a saved review
        can_reuse = (
            config.resume
            and reuse_review
            and i == resume_from
            and review_file.is_file()
        )

        if can_reuse:
            # Invalidate if diff changed
            saved_hash_file = log_dir / f"diff-hash-{i}.txt"
            if saved_hash_file.is_file():
                saved = saved_hash_file.read_text().strip()
                current = diff_hash(
                    config.target_branch, config.current_branch, cwd=cwd
                )
                if saved != current:
                    logger.info("[resume] Diff changed; re-running review.")
                    can_reuse = False
            else:
                can_reuse = False

        if can_reuse:
            # Invalidate if reviewer backend changed
            saved_backend_file = log_dir / "reviewer-backend.txt"
            if saved_backend_file.is_file():
                saved_backend = saved_backend_file.read_text().strip()
                if saved_backend != config.reviewer_backend:
                    logger.info(
                        "[resume] Backend changed (%s -> %s); re-running review.",
                        saved_backend,
                        config.reviewer_backend,
                    )
                    can_reuse = False
            else:
                logger.info("[resume] No backend metadata; re-running review.")
                can_reuse = False

        if can_reuse:
            logger.info("[resume] Reusing saved review: %s", review_file)
        else:
            try:
                review_ok = reviewer(review_file, i)
            except BudgetTimeoutError:
                logger.error("Budget timeout during review (iteration %d).", i)
                final_status = _BUDGET_TIMEOUT_STATUS.get(
                    config.reviewer_backend,
                    FinalStatus.CODEX_BUDGET_TIMEOUT,
                )
                break
            if not review_ok:
                final_status = _ERROR_STATUS.get(
                    config.reviewer_backend,
                    FinalStatus.CODEX_ERROR,
                )
                break

            # Save diff hash and backend metadata after successful review
            current_hash = diff_hash(
                config.target_branch, config.current_branch, cwd=cwd
            )
            (log_dir / f"diff-hash-{i}.txt").write_text(current_hash)
            (log_dir / "reviewer-backend.txt").write_text(config.reviewer_backend)

        # c. Parse review JSON
        review_data, _rc = parse_review_json(review_file, "review")
        if review_data is None:
            final_status = FinalStatus.PARSE_ERROR
            break

        review_data = _normalize_paths(review_data, cwd)

        findings = review_data.get("findings", [])
        if not isinstance(findings, list):
            logger.warning("findings is not a list — treating as parse error")
            final_status = FinalStatus.PARSE_ERROR
            break
        findings_count = len(findings)
        overall = review_data.get("overall_correctness", "?")
        logger.info("Findings: %d | Overall: %s", findings_count, overall)
        if findings_count > 0:
            had_findings = True

        # d. All clear?
        if findings_count == 0 and overall in {"patch is correct", "code is clean"}:
            logger.info("All clear — no issues found.")
            if config.pr_number:
                post_pr_comment(
                    pr_number=config.pr_number,
                    iteration=i,
                    max_loop=config.max_loop,
                    review_json=review_data,
                )
            final_status = FinalStatus.ALL_CLEAR
            iterations_run = i
            break

        # e. Dry-run check
        if config.dry_run:
            logger.info("Dry-run mode — skipping fixes.")
            final_status = FinalStatus.DRY_RUN
            iterations_run = i
            break

        # e2. Pre-fix confirmation gate (e.g. layer/full refactor plan)
        if pre_fix_confirm and not pre_fix_confirm(review_data):
            logger.info("Aborted by user.")
            final_status = FinalStatus.USER_ABORTED
            iterations_run = i
            break

        # f. Stash allowlisted files
        try:
            allowed_stashed = stash_allowlisted(
                [
                    ".gitignore",
                    ".overkill/.overkillrc",
                    ".overkill/.refactorsuggestrc",
                    # Legacy paths for pre-migration users
                    ".reviewlooprc",
                    ".refactorsuggestrc",
                    ".review-loop/.reviewlooprc",
                    ".review-loop/.refactorsuggestrc",
                ],
                cwd=cwd,
            )
        except RuntimeError:
            final_status = FinalStatus.STASH_ERROR
            break

        try:
            # g. Snapshot worktree
            pre_fix_snapshot = snapshot_worktree(cwd=cwd)

            # h. Fix
            review_json_str = json.dumps(review_data)
            try:
                fix_ok = fixer(review_json_str, str(i))
            except Exception:
                logger.exception("Fixer raised an unexpected exception.")
                fix_ok = False
            if not fix_ok:
                final_status = FinalStatus.CLAUDE_ERROR
                break

            # i. Self-review sub-loop
            self_review_summary = ""
            if self_reviewer and config.max_subloop > 0:
                self_review_summary = self_reviewer(
                    pre_fix_snapshot,
                    config.max_subloop,
                    log_dir,
                    i,
                    review_json_str,
                )

            # j. Commit & push
            if config.auto_commit:
                subject = f"{commit_pattern} {i} fixes"
                if config.ci_trigger_mode in ("last-only", "none"):
                    subject += " [skip ci]"
                commit_msg = (
                    f"{subject}\n\n"
                    f"Auto-generated by review loop (iteration {i}/{config.max_loop})"
                )
                if self_review_summary:
                    summary_oneline = (
                        self_review_summary
                        .replace("\n", "; ")
                        .rstrip("; ")
                    )
                    commit_msg += f"\nSelf-review: {summary_oneline}"
                try:
                    fix_committed = commit_and_push(
                        pre_fix_snapshot,
                        commit_msg,
                        config.current_branch,
                        push=config.push_branch,
                        # A --wip run's commits all get torn down again, and
                        # they carry the same unfinished work the scaffolding
                        # commit had to skip hooks for.
                        no_verify=config.wip,
                        cwd=cwd,
                    )
                except RuntimeError as e:
                    logger.error("commit_and_push failed — aborting loop: %s", e)
                    final_status = FinalStatus.COMMIT_PUSH_ERROR
                    iterations_run = i
                    break
                if fix_committed and config.ci_trigger_mode in ("last-only", "none"):
                    made_skipped_fix_commit = True
                if (
                    not fix_committed
                    and config.scope_commit
                    and i == config.max_loop
                ):
                    # The no-diff check that catches a no-op fixer runs at
                    # the top of the next iteration, and there is none left.
                    # Without this, confirmed findings would report success.
                    logger.warning(
                        "Fixer produced no code changes on the final "
                        "iteration — findings were either stale or left "
                        "unfixed.",
                    )
                    final_status = FinalStatus.FINDINGS_UNFIXED
                    iterations_run = i
                    break
            else:
                logger.info("AUTO_COMMIT is disabled — skipping commit and push.")
        finally:
            if not _unstash_safe(allowed_stashed, cwd):
                final_status = FinalStatus.STASH_ERROR
            allowed_stashed = False

        if final_status == FinalStatus.STASH_ERROR:
            iterations_run = i
            break

        # Stop after first iteration when auto-commit is off
        if not config.auto_commit:
            final_status = FinalStatus.AUTO_COMMIT_DISABLED
            iterations_run = i
            break

        # k. Post PR comment
        if config.pr_number:
            post_pr_comment(
                pr_number=config.pr_number,
                iteration=i,
                max_loop=config.max_loop,
                review_json=review_data,
                fix_file=log_dir / f"fix-{i}.md",
                opinion_file=log_dir / f"opinion-{i}.md",
                self_review_summary=self_review_summary,
                max_subloop=config.max_subloop,
            )

        iterations_run = i

    # Ensure unstash on any exit path
    if not _unstash_safe(allowed_stashed, cwd):
        final_status = FinalStatus.STASH_ERROR

    # ── Summary ──────────────────────────────────────────────────────
    summary_path = _generate_summary_safe(config, final_status)

    return LoopResult(
        final_status=final_status,
        iterations_run=iterations_run,
        summary_path=summary_path,
        made_skipped_fix_commit=made_skipped_fix_commit,
    )


# ── Private helpers ──────────────────────────────────────────────────


def _has_skipped_fix_commit(
    commit_pattern: str, log_dir: Path, cwd: Path | None
) -> bool:
    """Return True if any [skip ci] fix commit exists since the run started."""
    start_file = log_dir / "start-commit.txt"
    if not start_file.is_file():
        return False
    start = start_file.read_text().strip()
    if not start:
        return False
    result = subprocess.run(
        [
            "git", "log", "--fixed-strings",
            f"--grep={commit_pattern}", "--grep=[skip ci]", "--all-match",
            "--oneline", f"{start}..HEAD",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _validate_target_branch(target: str, cwd: Path | None) -> bool:
    """Return True if *target* is a valid git ref."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", target],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _reject_dirty_worktree(cwd: Path | None) -> list[str]:
    """Return non-allowlisted dirty files, or empty list if clean."""
    allowlisted = {
        ".gitignore",
        ".overkill/.overkillrc",
        ".overkill/.refactorsuggestrc",
        # Legacy paths for pre-migration users
        ".overkillrc",
        ".reviewlooprc",
        ".refactorsuggestrc",
        ".review-loop/.overkillrc",
        ".review-loop/.reviewlooprc",
        ".review-loop/.refactorsuggestrc",
    }
    return [
        f for f in git_all_dirty(cwd)
        if f not in allowlisted
        and not f.startswith(".overkill/logs/")
        and not f.startswith(".review-loop/logs/")
    ]


def _no_diff(target: str, current: str, cwd: Path | None) -> bool:
    """Check if there's any diff between target and current branch."""
    result = subprocess.run(
        ["git", "diff", "--quiet", f"{target}...{current}"],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if result.returncode > 1:
        stderr = result.stderr.decode() if result.stderr else ""
        raise RuntimeError(
            f"git diff failed (exit {result.returncode}): {stderr}"
        )
    return result.returncode == 0


def _clean_stale_logs(log_dir: Path) -> None:
    """Remove iteration artifacts from prior runs.

    Cleanup stale log files from previous runs so that fresh runs
    do not mix stale review/fix/summary files into new results.
    """
    patterns = [
        "review-*.json",
        "fix-*.md",
        "opinion-*.md",
        "self-review-*.json",
        "refix-*.md",
        "refix-opinion-*.md",
        "summary.md",
        "*.stream.jsonl",
    ]
    for pat in patterns:
        for f in log_dir.glob(pat):
            f.unlink()


def _save_metadata(config: LoopConfig, cwd: Path | None) -> None:
    """Save run metadata for resume support."""
    log_dir = config.log_dir
    (log_dir / "branch.txt").write_text(config.current_branch)
    (log_dir / "target-branch.txt").write_text(config.target_branch)
    if config.scope_commit:
        (log_dir / "scope-commit.txt").write_text(config.scope_commit)
        # Only commit-scope runs have a push choice to make; a resume that
        # forgets it would silently keep the fix commits local.
        (log_dir / "push-branch.txt").write_text(
            "true" if config.push_branch else "false"
        )
    else:
        # Drop a marker a prior commit-scope run left in this repo-wide
        # log dir, so --resume cannot restore an unrelated commit's scope.
        (log_dir / "scope-commit.txt").unlink(missing_ok=True)
        (log_dir / "push-branch.txt").unlink(missing_ok=True)
    if config.wip_base:
        # An interrupted --wip run is only recoverable while these survive:
        # they are the sole record of where to unwind the scaffolding to.
        (log_dir / "wip-base.txt").write_text(config.wip_base)
        (log_dir / "wip-scaffold.txt").write_text(config.wip_scaffold or "")
    else:
        (log_dir / "wip-base.txt").unlink(missing_ok=True)
        (log_dir / "wip-scaffold.txt").unlink(missing_ok=True)
    (log_dir / "max-loop.txt").write_text(str(config.max_loop))
    (log_dir / "reviewer-backend.txt").write_text(config.reviewer_backend)
    (log_dir / "reviewer-context.txt").write_text(config.reviewer_context)
    (log_dir / "ci-trigger-mode.txt").write_text(config.ci_trigger_mode)
    if config.scope:
        (log_dir / "scope.txt").write_text(config.scope)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode == 0:
        (log_dir / "start-commit.txt").write_text(head.stdout.strip())


def _generate_summary_safe(
    config: LoopConfig,
    final_status: FinalStatus | None = None,
) -> Path | None:
    """Generate summary, returning None on failure."""
    try:
        status = final_status or FinalStatus.MAX_ITERATIONS_REACHED
        return generate_summary(
            title="Review Loop Summary",
            log_dir=config.log_dir,
            current_branch=config.current_branch,
            target_branch=config.target_branch,
            max_loop=config.max_loop,
            final_status=status,
        )
    except Exception:
        logger.warning("Failed to generate summary.", exc_info=True)
        return None


def _unstash_safe(stashed: bool, cwd: Path | None) -> bool:
    """Pop stash if needed, logging failures.

    Returns ``True`` if unstash succeeded or was not needed, ``False`` on failure.
    """
    if stashed and not unstash_allowlisted(cwd=cwd):
        logger.warning("Failed to restore stashed files. Check 'git stash list'.")
        return False
    return True
