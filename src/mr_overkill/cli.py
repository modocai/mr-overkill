"""CLI argument parsing for overkill entry points.

Provides shared argument parsing for review-loop and refactor-suggest,
mirroring the CLI interface of the bash scripts.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mr_overkill import __version__, workspace_policy
from mr_overkill.commit_scope import resolve_commit
from mr_overkill.models import BudgetScope, LoopConfig


def _detect_current_branch() -> str:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "HEAD"


def _symbolic_ref(rev: str) -> str:
    """Full name of the ref *rev* resolves through, "" if it resolves without one."""
    result = subprocess.run(
        ["git", "rev-parse", "--symbolic-full-name", rev],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_stable_target(rev: str, current_branch: str) -> bool:
    """Whether *rev* still means the same commit after the loop commits a fix.

    A revision expression like ``HEAD~5`` names no ref at all and is measured
    from HEAD every time it is read.  ``HEAD`` itself, and the branch the fix
    commits land on, do name one — and it moves with every one of them.
    Everything else — another branch, a tag, a bare SHA — stays put.
    """
    ref = _symbolic_ref(rev)
    return bool(ref) and ref not in ("HEAD", f"refs/heads/{current_branch}")


def _detect_pr_number(branch: str) -> str | None:
    """Detect open PR number for the given branch."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number", "-q", ".number"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _load_rc_file(rc_name: str) -> dict[str, str]:
    """Load KEY=VALUE pairs from an rc file in the git root.

    Mirrors the safe parsing from review-loop.sh / refactor-suggest.sh.
    Only whitelisted keys are accepted.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}

    git_root = Path(result.stdout.strip())
    rc_path, legacy_dir = workspace_policy.workspace_paths(git_root, rc_name)
    if not rc_path.is_file():
        # Also check for the legacy rc name in the legacy directory
        legacy_old_name = (
            git_root / workspace_policy.LEGACY_WORKSPACE_DIR
            / workspace_policy.LEGACY_RC_NAME
            if rc_name == workspace_policy.RC_NAME else None
        )
        # Legacy repo-root name: .overkillrc was .reviewlooprc
        legacy_root_name = (
            workspace_policy.LEGACY_RC_NAME
            if rc_name == workspace_policy.RC_NAME else rc_name
        )
        legacy_root = git_root / legacy_root_name
        if legacy_dir.is_file():
            logging.getLogger(__name__).warning(
                "%s found at .review-loop/ (legacy location). "
                "Please run 'overkill init' to migrate to .overkill/",
                rc_name,
            )
            rc_path = legacy_dir
        elif legacy_old_name and legacy_old_name.is_file():
            logging.getLogger(__name__).warning(
                ".reviewlooprc found at .review-loop/ (legacy location). "
                "Please run 'overkill init' to migrate to .overkill/.overkillrc",
            )
            rc_path = legacy_old_name
        elif (git_root / rc_name).is_file():
            logging.getLogger(__name__).warning(
                "%s found at repo root (legacy location). "
                "Please move it to .overkill/%s",
                rc_name, rc_name,
            )
            rc_path = git_root / rc_name
        elif legacy_root.is_file():
            logging.getLogger(__name__).warning(
                "%s found at repo root (legacy location). "
                "Please move it to .overkill/%s",
                legacy_root_name, rc_name,
            )
            rc_path = legacy_root
        else:
            return {}

    allowed_keys = {
        "TARGET_BRANCH", "MAX_LOOP", "MAX_SUBLOOP", "DRY_RUN",
        "AUTO_COMMIT", "PROMPTS_DIR", "RETRY_MAX_WAIT",
        "RETRY_INITIAL_WAIT", "BUDGET_SCOPE", "DIAGNOSTIC_LOG",
        "SCOPE", "AUTO_APPROVE", "CREATE_PR", "WITH_REVIEW",
        "REVIEW_LOOPS", "FIX_NITS", "REVIEWER_BACKEND",
        "REVIEWER_CONTEXT", "CI_TRIGGER_MODE", "NO_BUDGET_GATE",
        "COMMIT_SCOPE_PUSH",
    }
    boolean_keys = {
        "DRY_RUN", "AUTO_COMMIT", "DIAGNOSTIC_LOG",
        "AUTO_APPROVE", "CREATE_PR", "WITH_REVIEW", "FIX_NITS",
        "NO_BUDGET_GATE", "COMMIT_SCOPE_PUSH",
    }
    kv_re = re.compile(
        r"""^\s*(\w+)=(?:"([^"]*)"|'([^']*)'|(.*?))\s*$"""
    )
    values: dict[str, str] = {}

    for line in rc_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = kv_re.match(line)
        if m and m.group(1) in allowed_keys:
            raw = m.group(2) or m.group(3) or m.group(4) or ""
            key, val = m.group(1), raw.strip()
            if key in boolean_keys and val.lower() not in ("true", "false"):
                msg = f"{rc_path.name}: {key} must be 'true' or 'false', got '{val}'."
                raise SystemExit(f"Error: {msg}")
            values[key] = val.lower() if key in boolean_keys else val

    return values


def _resolve_prompts_dir(prompts_dir: str) -> Path:
    """Resolve prompts directory relative to git root."""
    p = Path(prompts_dir)
    if p.is_absolute():
        return p

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        git_root = Path(result.stdout.strip())
        resolved = git_root / prompts_dir
        # Legacy fallback: .overkill/... → .review-loop/...
        current_prefix = f"{workspace_policy.WORKSPACE_DIR}/"
        legacy_prefix = f"{workspace_policy.LEGACY_WORKSPACE_DIR}/"
        if not resolved.is_dir() and current_prefix in prompts_dir:
            legacy = git_root / prompts_dir.replace(
                current_prefix, legacy_prefix, 1
            )
            if legacy.is_dir():
                logging.getLogger(__name__).warning(
                    "Prompts dir found at %s (legacy location). "
                    "Please run 'overkill init' to migrate to .overkill/",
                    legacy,
                )
                return legacy
        # Inverse fallback: .review-loop/... → .overkill/...
        if not resolved.is_dir() and legacy_prefix in prompts_dir:
            migrated = git_root / prompts_dir.replace(
                legacy_prefix, current_prefix, 1
            )
            if migrated.is_dir():
                logging.getLogger(__name__).warning(
                    "Prompts dir migrated to %s. "
                    "Please update PROMPTS_DIR in your rc file.",
                    migrated,
                )
                return migrated
        return resolved
    return p


def _resolve_log_dir(git_root: Path, *parts: str, resume: bool) -> Path:
    """Log directory for this run, preferring the current workspace layout.

    A repo half-migrated between the two layouts can hold both, so a resume
    additionally prefers whichever directory actually has the run metadata —
    picking the empty one would report there is nothing to resume.
    """
    candidates = workspace_policy.workspace_paths(git_root, "logs", *parts)
    log_dir = next((c for c in candidates if c.is_dir()), candidates[0])
    if resume and not (log_dir / "max-loop.txt").is_file():
        with_metadata = next(
            (c for c in candidates if (c / "max-loop.txt").is_file()), None
        )
        if with_metadata is not None:
            log_dir = with_metadata
    return log_dir


def _int_from_rc(
    rc: dict[str, str],
    key: str,
    default: str,
    parser: argparse.ArgumentParser,
) -> int:
    """Parse an integer from rc file, raising a clean CLI error on bad values."""
    raw = rc.get(key, default)
    try:
        return int(raw)
    except ValueError:
        parser.error(f"invalid integer for {key} in rc file: {raw!r}")


def _parse_budget_scope(
    raw: str,
    parser: argparse.ArgumentParser,
    allowed: frozenset[BudgetScope] | None = None,
) -> BudgetScope:
    """Convert a string to BudgetScope, raising a clean CLI error on bad values."""
    try:
        scope = BudgetScope(raw)
    except ValueError:
        choices = allowed or frozenset(BudgetScope)
        parser.error(
            f"BUDGET_SCOPE must be one of: "
            f"{', '.join(e.value for e in choices)}. Got {raw!r}"
        )
    if allowed and scope not in allowed:
        parser.error(
            f"BUDGET_SCOPE must be one of: "
            f"{', '.join(e.value for e in allowed)}. Got {raw!r}"
        )
    return scope


def parse_review_loop_args(
    argv: list[str] | None = None,
) -> LoopConfig:
    """Parse review-loop CLI arguments into a LoopConfig."""
    parser = argparse.ArgumentParser(
        prog="review-loop",
        description="AI-powered review-fix loop",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-t", "--target",
        default=None,
        help="Target branch to diff against (default: develop)",
    )
    parser.add_argument(
        "-n", "--max-loop",
        type=int,
        default=None,
        help="Maximum review-fix iterations (required)",
    )
    parser.add_argument(
        "--max-subloop",
        type=int,
        default=None,
        help="Maximum self-review sub-iterations (default: 4)",
    )
    parser.add_argument(
        "--no-self-review",
        action="store_true",
        help="Disable self-review",
    )
    dry_run_grp = parser.add_mutually_exclusive_group()
    dry_run_grp.add_argument(
        "--dry-run",
        action="store_const",
        const=True,
        dest="dry_run",
        help="Run review only, do not fix",
    )
    dry_run_grp.add_argument(
        "--no-dry-run",
        action="store_const",
        const=False,
        dest="dry_run",
        help="Force fixes",
    )
    auto_commit_grp = parser.add_mutually_exclusive_group()
    auto_commit_grp.add_argument(
        "--auto-commit",
        action="store_const",
        const=True,
        dest="auto_commit",
        help="Force commit/push",
    )
    auto_commit_grp.add_argument(
        "--no-auto-commit",
        action="store_const",
        const=False,
        dest="auto_commit",
        help="Fix but do not commit/push",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a previously interrupted run",
    )
    parser.add_argument(
        "--fix-nits",
        action="store_true",
        default=None,
        help="Also flag nits and style issues during self-review",
    )
    parser.add_argument(
        "--diagnostic-log",
        action="store_true",
        help="Save full event stream to sidecar files",
    )
    parser.add_argument(
        "--no-budget-gate",
        action="store_true",
        default=None,
        help=(
            "Skip token-budget checks and run the CLI backends regardless. "
            "Use when local budget data is stale or wrong "
            "(same as OVERKILL_SKIP_BUDGET=1)"
        ),
    )
    parser.add_argument(
        "--reviewer-backend",
        default=None,
        choices=["claude", "codex", "gemini"],
        help="Backend for code review (default: codex)",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Additional context for the reviewer (e.g. design intent, constraints)",
    )
    parser.add_argument(
        "--ci-trigger-mode",
        default=None,
        choices=["every", "last-only", "none"],
        help=(
            "CI trigger policy for iteration commits. "
            "'last-only' (default): append [skip ci] to iteration commits and "
            "push a single empty 'chore: trigger CI' commit only on PASS. "
            "'every': every commit triggers CI. "
            "'none': append [skip ci] with no trigger commit."
        ),
    )
    parser.add_argument(
        "--commit",
        default=None,
        metavar="REV",
        help=(
            "Review an already-merged commit instead of the branch diff. "
            "Creates a review/<sha>-<ts> branch off HEAD and applies fixes "
            "there; no PR is created. REV is any single commit (sha, tag, "
            "HEAD~3) — ranges are not supported. Cannot be used with -t"
        ),
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=None,
        help=(
            "Push the auto-created review branch to the remote "
            "(default: the branch stays local)"
        ),
    )
    parser.add_argument(
        "--wip",
        action="store_true",
        help=(
            "Include uncommitted working-tree changes in the review. "
            "With commits enabled they are parked in a scaffolding commit "
            "that is unwound when the run finishes"
        ),
    )

    args = parser.parse_args(argv)

    if args.commit:
        if ".." in args.commit:
            parser.error(
                "--commit takes a single commit; ranges (A..B) are not supported"
            )
        if args.target:
            parser.error(
                "--commit derives its base from the commit itself; "
                "-t/--target cannot be combined with it"
            )
        if args.wip:
            parser.error(
                "--commit and --wip are different review scopes; "
                "pick one"
            )

    # Load rc file defaults
    rc = _load_rc_file(workspace_policy.RC_NAME)

    # Resolve values with precedence: CLI > rc file > defaults
    target = args.target or rc.get("TARGET_BRANCH", "develop")
    rc_max = _int_from_rc(rc, "MAX_LOOP", "0", parser)
    max_loop = (
        args.max_loop if args.max_loop is not None
        else (rc_max or None)
    )

    if not args.resume and max_loop is None:
        parser.error("-n / --max-loop is required")

    if max_loop is not None and max_loop < 1:
        parser.error("--max-loop must be a positive integer")

    max_subloop = (
        0 if args.no_self_review
        else (
            args.max_subloop
            if args.max_subloop is not None
            else _int_from_rc(rc, "MAX_SUBLOOP", "4", parser)
        )
    )
    if max_subloop < 0:
        parser.error("--max-subloop must be non-negative")

    dry_run = _resolve_bool(args.dry_run, rc.get("DRY_RUN"), False)
    auto_commit = _resolve_bool(args.auto_commit, rc.get("AUTO_COMMIT"), True)
    fix_nits = _resolve_bool(args.fix_nits, rc.get("FIX_NITS"), False)
    diagnostic_log = args.diagnostic_log or rc.get(
        "DIAGNOSTIC_LOG", "false"
    ) == "true"
    skip_budget_gate = _resolve_bool(
        args.no_budget_gate, rc.get("NO_BUDGET_GATE"), False
    )

    retry_max_wait = _int_from_rc(rc, "RETRY_MAX_WAIT", "7200", parser)
    retry_initial_wait = _int_from_rc(rc, "RETRY_INITIAL_WAIT", "30", parser)
    if retry_max_wait < 1:
        parser.error("RETRY_MAX_WAIT must be a positive integer")
    if retry_initial_wait < 1:
        parser.error("RETRY_INITIAL_WAIT must be a positive integer")
    budget_scope_str = rc.get("BUDGET_SCOPE", "micro")

    prompts_dir = _resolve_prompts_dir(
        rc.get("PROMPTS_DIR", workspace_policy.DEFAULT_PROMPTS_DIR)
    )

    current_branch = _detect_current_branch()

    # Log directory
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    git_root = Path(result.stdout.strip()) if result.returncode == 0 else Path(".")
    log_dir = _resolve_log_dir(git_root, resume=args.resume)

    # Restore saved values on resume when not explicitly given
    restored_target = None
    if args.resume:
        if args.target is None:
            saved = log_dir / "target-branch.txt"
            if saved.is_file():
                target = restored_target = saved.read_text().strip()
        if args.max_loop is None:
            saved = log_dir / "max-loop.txt"
            if saved.is_file():
                try:
                    max_loop = int(saved.read_text().strip())
                except ValueError:
                    parser.error(f"malformed max-loop value in {saved}")
            else:
                parser.error(
                    f"--resume requires {saved} or explicit --max-loop"
                )
        if args.reviewer_backend is None:
            saved = log_dir / "reviewer-backend.txt"
            if saved.is_file():
                args.reviewer_backend = saved.read_text().strip()
        if args.context is None:
            saved = log_dir / "reviewer-context.txt"
            if saved.is_file():
                args.context = saved.read_text().strip()
        if args.ci_trigger_mode is None:
            saved = log_dir / "ci-trigger-mode.txt"
            if saved.is_file():
                args.ci_trigger_mode = saved.read_text().strip()
        if args.push is None:
            saved = log_dir / "push-branch.txt"
            if saved.is_file():
                args.push = saved.read_text().strip() == "true"

    wip_base = None
    wip_scaffold = None
    if args.resume and args.wip:
        saved = log_dir / "wip-base.txt"
        if saved.is_file():
            wip_base = saved.read_text().strip() or None
        saved = log_dir / "wip-scaffold.txt"
        if saved.is_file():
            wip_scaffold = saved.read_text().strip() or None

    scope_commit = None
    if args.commit:
        scope_commit = resolve_commit(args.commit)
        if scope_commit is None:
            parser.error(f"--commit: not a commit: {args.commit!r}")

    if args.resume:
        saved = log_dir / "scope-commit.txt"
        if saved.is_file():
            restored = saved.read_text().strip()
            if scope_commit and scope_commit != restored:
                parser.error(
                    f"--commit {args.commit!r} differs from the resumed run's "
                    f"commit {restored}"
                )
            scope_commit = restored
            if args.wip:
                # The mutual-exclusion check above only sees an explicit
                # --commit. A restored one slips past it, and the resulting
                # config takes the commit-scope branch below — leaving a
                # --wip --no-auto-commit resume to reach the loop's resume
                # reset, which stashes the very working tree it reviews.
                parser.error(
                    f"the resumed run is commit-scoped ({restored[:7]}); "
                    "--commit and --wip are different review scopes. "
                    "Drop --wip, or start a fresh --wip run."
                )
            if args.target:
                parser.error(
                    "--commit derives its base from the commit itself; "
                    "-t/--target cannot be combined with a resumed "
                    "commit-scope run"
                )

    if max_loop is not None and max_loop < 1:
        parser.error("--max-loop must be a positive integer")

    reviewer_backend = args.reviewer_backend or rc.get("REVIEWER_BACKEND", "codex")
    if reviewer_backend not in ("claude", "codex", "gemini"):
        parser.error(
            f"REVIEWER_BACKEND must be 'claude', 'codex', or 'gemini',"
            f" got {reviewer_backend!r}"
        )

    reviewer_context = (
        args.context if args.context is not None else rc.get("REVIEWER_CONTEXT", "")
    )

    ci_trigger_mode = (
        args.ci_trigger_mode
        if args.ci_trigger_mode is not None
        else rc.get("CI_TRIGGER_MODE", "last-only")
    )
    if ci_trigger_mode not in ("every", "last-only", "none"):
        parser.error(
            f"CI_TRIGGER_MODE must be 'every', 'last-only', or 'none',"
            f" got {ci_trigger_mode!r}"
        )

    if scope_commit:
        # The work branch is created off HEAD, so the branch diff (and hence
        # the loop's convergence check) is "the fixes so far". On resume that
        # base was fixed when the branch was created and HEAD has since moved
        # past it, so the restored value wins.
        if restored_target is None:
            head = resolve_commit("HEAD")
            if head is None:
                parser.error(
                    "--commit requires a repository with at least one commit"
                )
            target = head
        # Every iteration commit should look normal: there is no PR to stay
        # quiet for, and no trigger commit worth saving up.
        ci_trigger_mode = "every"
        pr_number = None
    elif args.wip:
        if args.resume and not (auto_commit and not dry_run):
            # Without commits a --wip run is a single pass with no state worth
            # resuming, and resuming one would be actively harmful: the loop's
            # resume reset stashes the very working tree being reviewed.
            parser.error(
                "--wip without commits is a single pass; there is nothing to "
                "resume. Drop --resume, or allow commits so the work is "
                "scaffolded first."
            )
        if auto_commit and not dry_run:
            # The scaffolding commit becomes HEAD, so a target that resolves
            # at loop time — "HEAD", or the branch we are sitting on — would
            # then name the scaffolding commit itself and the diff would come
            # out empty.  Pin it to what HEAD means right now.  On resume that
            # pinning already happened and the restored value wins.
            if restored_target is None:
                pinned = resolve_commit(target)
                if pinned is None:
                    parser.error(f"-t/--target: not a commit: {target!r}")
                target = pinned
        elif max_loop and max_loop > 1:
            logging.getLogger(__name__).warning(
                "--wip without commits runs a single iteration; "
                "-n %d has no effect.",
                max_loop,
            )
        # Findings describe uncommitted code that is not in any PR, so a
        # comment on the branch's PR would point at nothing.
        pr_number = None
    else:
        pr_number = _detect_pr_number(current_branch)
        # The target is re-read on every iteration, so one that moves with
        # HEAD slides forward as the loop's own fix commits land: the range
        # shrinks off its oldest end and the commits that fall out of it stop
        # being reviewed — silently, since the diff still looks healthy.  Pin
        # it to what it means now.  A ref of its own keeps its name, which the
        # reviewer prompt and the run report both read better for.  The
        # --commit and --wip paths above pin their own targets already, and a
        # resumed run inherits the pinning its first run did.
        if (
            auto_commit
            and not dry_run
            and restored_target is None
            and not _is_stable_target(target, current_branch)
        ):
            pinned = resolve_commit(target)
            if pinned is None:
                parser.error(f"-t/--target: not a commit: {target!r}")
            target = pinned

    # COMMIT_SCOPE_PUSH describes the auto-created review/* branch only; a
    # normal branch run must always push, or its first fix commit stays local.
    push_branch = (
        _resolve_bool(args.push, rc.get("COMMIT_SCOPE_PUSH"), False)
        if scope_commit
        else True
    )
    if args.wip:
        # Not configurable: pushing here would publish unfinished work — and
        # the scaffolding commit holding it — to a shared branch.
        push_branch = False

    return LoopConfig(
        current_branch=current_branch,
        target_branch=target,
        max_loop=max_loop or 1,
        max_subloop=max_subloop,
        dry_run=dry_run,
        fix_nits=fix_nits,
        auto_commit=auto_commit,
        resume=args.resume,
        retry_max_wait=retry_max_wait,
        retry_initial_wait=retry_initial_wait,
        budget_scope=_parse_budget_scope(
            budget_scope_str, parser,
            allowed=frozenset({BudgetScope.MICRO, BudgetScope.MODULE}),
        ),
        skip_budget_gate=skip_budget_gate,
        diagnostic_log=diagnostic_log,
        log_dir=log_dir,
        prompts_dir=prompts_dir,
        pr_number=pr_number,
        reviewer_backend=reviewer_backend,
        reviewer_context=reviewer_context,
        ci_trigger_mode=ci_trigger_mode,
        scope_commit=scope_commit,
        scope_diff_file=(log_dir / "scope.diff") if scope_commit else None,
        push_branch=push_branch,
        wip=args.wip,
        wip_base=wip_base,
        wip_scaffold=wip_scaffold,
    )


@dataclass
class _RefactorExtra:
    """Refactor-suggest flags outside LoopConfig."""

    create_pr: bool = False
    with_review: bool = False
    review_loops: int = 4


def parse_refactor_suggest_args(
    argv: list[str] | None = None,
) -> tuple[LoopConfig, _RefactorExtra]:
    """Parse refactor-suggest CLI arguments.

    Returns a (LoopConfig, _RefactorExtra) tuple.  The extra struct
    carries refactor-specific flags that don't belong in LoopConfig.
    """
    parser = argparse.ArgumentParser(
        prog="refactor-suggest",
        description="AI-powered refactoring suggestions",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--scope",
        default=None,
        choices=["auto", "micro", "module", "layer", "full"],
        help="Refactoring scope (default: auto)",
    )
    parser.add_argument(
        "-t", "--target",
        default=None,
        help="Target branch (default: develop)",
    )
    parser.add_argument(
        "-n", "--max-loop",
        type=int,
        default=None,
        help="Maximum analysis-fix iterations (default: 1)",
    )
    parser.add_argument(
        "--max-subloop",
        type=int,
        default=None,
        help="Maximum self-review sub-iterations (default: 4)",
    )
    parser.add_argument(
        "--no-self-review",
        action="store_true",
        help="Disable self-review",
    )
    dry_run_grp = parser.add_mutually_exclusive_group()
    dry_run_grp.add_argument(
        "--dry-run",
        action="store_const",
        const=True,
        dest="dry_run",
        help="Run analysis only, do not fix",
    )
    dry_run_grp.add_argument(
        "--no-dry-run",
        action="store_const",
        const=False,
        dest="dry_run",
        help="Force fixes",
    )
    parser.add_argument(
        "--create-pr",
        action="store_true",
        default=None,
        help="Create a draft PR after completing iterations",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a previously interrupted run",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip interactive confirmation for layer/full scope",
    )
    parser.add_argument(
        "--fix-nits",
        action="store_true",
        default=None,
        help="Also flag nits and style issues during self-review",
    )
    parser.add_argument(
        "--diagnostic-log",
        action="store_true",
        help="Save full event stream to sidecar files",
    )
    parser.add_argument(
        "--no-budget-gate",
        action="store_true",
        default=None,
        help=(
            "Skip token-budget checks and run the CLI backends regardless. "
            "Use when local budget data is stale or wrong "
            "(same as OVERKILL_SKIP_BUDGET=1)"
        ),
    )
    parser.add_argument(
        "--with-review",
        action="store_true",
        default=None,
        help="Run review-loop after PR creation",
    )
    parser.add_argument(
        "--with-review-loops",
        type=int,
        default=None,
        help="Review-loop iterations (implies --with-review)",
    )
    parser.add_argument(
        "--reviewer-backend",
        default=None,
        choices=["claude", "codex", "gemini"],
        help="Backend for code review (default: codex)",
    )

    args = parser.parse_args(argv)

    # Load rc file defaults
    rc = _load_rc_file(workspace_policy.REFACTOR_RC_NAME)

    # Resolve values: CLI > rc > defaults
    scope = args.scope or rc.get("SCOPE", "auto")
    target = args.target or rc.get("TARGET_BRANCH", "develop")
    max_loop_rc = (
        _int_from_rc(rc, "MAX_LOOP", "0", parser) if "MAX_LOOP" in rc
        else None
    )
    max_loop = (
        args.max_loop if args.max_loop is not None
        else max_loop_rc
    )
    if max_loop is not None and max_loop < 1:
        parser.error("--max-loop must be a positive integer")

    max_subloop = (
        0 if args.no_self_review
        else (
            args.max_subloop
            if args.max_subloop is not None
            else _int_from_rc(rc, "MAX_SUBLOOP", "4", parser)
        )
    )
    if max_subloop < 0:
        parser.error("--max-subloop must be non-negative")

    dry_run = _resolve_bool(args.dry_run, rc.get("DRY_RUN"), False)
    fix_nits = _resolve_bool(args.fix_nits, rc.get("FIX_NITS"), False)
    create_pr = _resolve_bool(args.create_pr, rc.get("CREATE_PR"), False)
    auto_approve = args.auto_approve or rc.get(
        "AUTO_APPROVE", "false"
    ) == "true"
    diagnostic_log = args.diagnostic_log or rc.get(
        "DIAGNOSTIC_LOG", "false"
    ) == "true"
    skip_budget_gate = _resolve_bool(
        args.no_budget_gate, rc.get("NO_BUDGET_GATE"), False
    )

    with_review = _resolve_bool(args.with_review, rc.get("WITH_REVIEW"), False)
    review_loops = (
        args.with_review_loops
        if args.with_review_loops is not None
        else _int_from_rc(rc, "REVIEW_LOOPS", "4", parser)
    )
    if review_loops < 1:
        parser.error("--with-review-loops must be a positive integer")
    if args.with_review_loops is not None:
        with_review = True
    if with_review:
        create_pr = True

    retry_max_wait = _int_from_rc(rc, "RETRY_MAX_WAIT", "7200", parser)
    retry_initial_wait = _int_from_rc(rc, "RETRY_INITIAL_WAIT", "30", parser)
    if retry_max_wait < 1:
        parser.error("RETRY_MAX_WAIT must be a positive integer")
    if retry_initial_wait < 1:
        parser.error("RETRY_INITIAL_WAIT must be a positive integer")
    budget_scope_str = rc.get("BUDGET_SCOPE", "module")

    prompts_dir = _resolve_prompts_dir(
        rc.get("PROMPTS_DIR", workspace_policy.DEFAULT_PROMPTS_DIR)
    )

    current_branch = _detect_current_branch()

    # Log directory
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    git_root = (
        Path(result.stdout.strip()) if result.returncode == 0
        else Path(".")
    )
    log_dir = _resolve_log_dir(git_root, "refactor", resume=args.resume)

    # Restore saved values on resume when not explicitly given
    if args.resume:
        if args.target is None:
            saved = log_dir / "target-branch.txt"
            if saved.is_file():
                target = saved.read_text().strip()
        if args.max_loop is None:
            saved = log_dir / "max-loop.txt"
            if saved.is_file():
                try:
                    max_loop = int(saved.read_text().strip())
                except ValueError:
                    parser.error(f"malformed max-loop value in {saved}")
        if args.scope is None:
            saved = log_dir / "scope.txt"
            if saved.is_file():
                scope = saved.read_text().strip()
        if args.reviewer_backend is None:
            saved = log_dir / "reviewer-backend.txt"
            if saved.is_file():
                args.reviewer_backend = saved.read_text().strip()

    if args.resume and max_loop is None:
        parser.error(
            "Cannot determine max_loop for resume: logs/max-loop.txt is missing. "
            "Please provide -n / --max-loop explicitly."
        )

    _valid_scopes = {"auto", "micro", "module", "layer", "full"}
    if scope not in _valid_scopes:
        parser.error(
            f"SCOPE must be one of: {', '.join(sorted(_valid_scopes))}. "
            f"Got {scope!r}"
        )

    reviewer_backend = args.reviewer_backend or rc.get("REVIEWER_BACKEND", "codex")
    if reviewer_backend not in ("claude", "codex", "gemini"):
        parser.error(
            f"REVIEWER_BACKEND must be 'claude', 'codex', or 'gemini',"
            f" got {reviewer_backend!r}"
        )

    config = LoopConfig(
        current_branch=current_branch,
        target_branch=target,
        max_loop=max_loop or 1,
        max_subloop=max_subloop,
        dry_run=dry_run,
        fix_nits=fix_nits,
        auto_commit=True,
        resume=args.resume,
        auto_approve=auto_approve,
        retry_max_wait=retry_max_wait,
        retry_initial_wait=retry_initial_wait,
        budget_scope=_parse_budget_scope(
            budget_scope_str, parser,
            allowed=frozenset({BudgetScope.MICRO, BudgetScope.MODULE}),
        ),
        skip_budget_gate=skip_budget_gate,
        diagnostic_log=diagnostic_log,
        log_dir=log_dir,
        prompts_dir=prompts_dir,
        scope=scope,
        reviewer_backend=reviewer_backend,
        ci_trigger_mode="every",
    )

    extra = _RefactorExtra(
        create_pr=create_pr,
        with_review=with_review,
        review_loops=review_loops,
    )

    return config, extra


def _resolve_bool(
    flag_value: bool | None,
    rc_value: str | None,
    default: bool,
) -> bool:
    """Resolve a boolean flag with CLI > rc > default precedence."""
    if flag_value is not None:
        return flag_value
    if rc_value is not None:
        return rc_value.lower() == "true"
    return default
