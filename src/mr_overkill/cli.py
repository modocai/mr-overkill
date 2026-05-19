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

from mr_overkill import __version__
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
    rc_path = git_root / ".overkill" / rc_name
    if not rc_path.is_file():
        # Fall back to the legacy .review-loop/ location
        legacy_dir = git_root / ".review-loop" / rc_name
        # Also check for legacy .reviewlooprc name in .review-loop/
        legacy_old_name = (
            git_root / ".review-loop" / ".reviewlooprc"
            if rc_name == ".overkillrc" else None
        )
        # Legacy repo-root name: .overkillrc was .reviewlooprc
        legacy_root_name = ".reviewlooprc" if rc_name == ".overkillrc" else rc_name
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
        "REVIEWER_CONTEXT", "CI_TRIGGER_MODE",
    }
    boolean_keys = {
        "DRY_RUN", "AUTO_COMMIT", "DIAGNOSTIC_LOG",
        "AUTO_APPROVE", "CREATE_PR", "WITH_REVIEW", "FIX_NITS",
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
        if not resolved.is_dir() and ".overkill/" in prompts_dir:
            legacy = git_root / prompts_dir.replace(
                ".overkill/", ".review-loop/", 1
            )
            if legacy.is_dir():
                logging.getLogger(__name__).warning(
                    "Prompts dir found at %s (legacy location). "
                    "Please run 'overkill init' to migrate to .overkill/",
                    legacy,
                )
                return legacy
        # Inverse fallback: .review-loop/... → .overkill/...
        if not resolved.is_dir() and ".review-loop/" in prompts_dir:
            migrated = git_root / prompts_dir.replace(
                ".review-loop/", ".overkill/", 1
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

    args = parser.parse_args(argv)

    # Load rc file defaults
    rc = _load_rc_file(".overkillrc")

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

    retry_max_wait = _int_from_rc(rc, "RETRY_MAX_WAIT", "7200", parser)
    retry_initial_wait = _int_from_rc(rc, "RETRY_INITIAL_WAIT", "30", parser)
    if retry_max_wait < 1:
        parser.error("RETRY_MAX_WAIT must be a positive integer")
    if retry_initial_wait < 1:
        parser.error("RETRY_INITIAL_WAIT must be a positive integer")
    budget_scope_str = rc.get("BUDGET_SCOPE", "micro")

    prompts_dir = _resolve_prompts_dir(
        rc.get("PROMPTS_DIR", ".overkill/prompts/active")
    )

    current_branch = _detect_current_branch()
    pr_number = _detect_pr_number(current_branch)

    # Log directory
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    git_root = Path(result.stdout.strip()) if result.returncode == 0 else Path(".")
    log_dir = git_root / ".overkill" / "logs"
    if not log_dir.is_dir():
        legacy_log = git_root / ".review-loop" / "logs"
        if legacy_log.is_dir():
            log_dir = legacy_log

    # In mixed-state repos, fall back to legacy logs when resume metadata
    # is missing from the preferred directory.
    if args.resume and not (log_dir / "max-loop.txt").is_file():
        legacy_log = git_root / ".review-loop" / "logs"
        if (legacy_log / "max-loop.txt").is_file():
            log_dir = legacy_log

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
        diagnostic_log=diagnostic_log,
        log_dir=log_dir,
        prompts_dir=prompts_dir,
        pr_number=pr_number,
        reviewer_backend=reviewer_backend,
        reviewer_context=reviewer_context,
        ci_trigger_mode=ci_trigger_mode,
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
    rc = _load_rc_file(".refactorsuggestrc")

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
        rc.get("PROMPTS_DIR", ".overkill/prompts/active")
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
    log_dir = git_root / ".overkill" / "logs" / "refactor"
    if not log_dir.is_dir():
        legacy_log = git_root / ".review-loop" / "logs" / "refactor"
        if legacy_log.is_dir():
            log_dir = legacy_log

    # In mixed-state repos, fall back to legacy logs when resume metadata
    # is missing from the preferred directory.
    if args.resume and not (log_dir / "max-loop.txt").is_file():
        legacy_log = git_root / ".review-loop" / "logs" / "refactor"
        if (legacy_log / "max-loop.txt").is_file():
            log_dir = legacy_log

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
