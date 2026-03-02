"""CLI argument parsing for mr-overkill entry points.

Provides shared argument parsing for review-loop and refactor-suggest,
mirroring the CLI interface of the bash scripts.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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

    rc_path = Path(result.stdout.strip()) / rc_name
    if not rc_path.is_file():
        return {}

    allowed_keys = {
        "TARGET_BRANCH", "MAX_LOOP", "MAX_SUBLOOP", "DRY_RUN",
        "AUTO_COMMIT", "PROMPTS_DIR", "RETRY_MAX_WAIT",
        "RETRY_INITIAL_WAIT", "BUDGET_SCOPE", "DIAGNOSTIC_LOG",
        "SCOPE", "AUTO_APPROVE", "CREATE_PR", "WITH_REVIEW",
        "REVIEW_LOOPS",
    }
    boolean_keys = {
        "DRY_RUN", "AUTO_COMMIT", "DIAGNOSTIC_LOG",
        "AUTO_APPROVE", "CREATE_PR", "WITH_REVIEW",
    }
    kv_re = re.compile(
        r"^\s*(\w+)=[\"']?([^\"']*)[\"']?\s*$"
    )
    values: dict[str, str] = {}

    for line in rc_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = kv_re.match(line)
        if m and m.group(1) in allowed_keys:
            key, val = m.group(1), m.group(2).strip()
            if key in boolean_keys and val.lower() not in ("true", "false"):
                msg = f"{rc_path.name}: {key} must be 'true' or 'false', got '{val}'."
                raise SystemExit(f"Error: {msg}")
            values[key] = val

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
        return Path(result.stdout.strip()) / prompts_dir
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
) -> BudgetScope:
    """Convert a string to BudgetScope, raising a clean CLI error on bad values."""
    try:
        return BudgetScope(raw)
    except ValueError:
        parser.error(
            f"BUDGET_SCOPE must be one of: "
            f"{', '.join(e.value for e in BudgetScope)}. Got {raw!r}"
        )


def parse_review_loop_args(
    argv: list[str] | None = None,
) -> LoopConfig:
    """Parse review-loop CLI arguments into a LoopConfig."""
    parser = argparse.ArgumentParser(
        prog="review-loop",
        description="AI-powered review-fix loop",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Run review only, do not fix",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Force fixes",
    )
    parser.add_argument(
        "--no-auto-commit",
        action="store_true",
        help="Fix but do not commit/push",
    )
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="Force commit/push",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from a previously interrupted run",
    )
    parser.add_argument(
        "--diagnostic-log",
        action="store_true",
        help="Save full event stream to sidecar files",
    )

    args = parser.parse_args(argv)

    # Load rc file defaults
    rc = _load_rc_file(".reviewlooprc")

    # Resolve values with precedence: CLI > rc file > defaults
    target = args.target or rc.get("TARGET_BRANCH", "develop")
    max_loop = args.max_loop if args.max_loop is not None else (_int_from_rc(rc, "MAX_LOOP", "0", parser) or None)

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

    dry_run = _resolve_bool(
        args.dry_run, args.no_dry_run, rc.get("DRY_RUN"), False
    )
    auto_commit = _resolve_bool(
        args.auto_commit, args.no_auto_commit,
        rc.get("AUTO_COMMIT"), True
    )
    diagnostic_log = args.diagnostic_log or rc.get(
        "DIAGNOSTIC_LOG", "false"
    ) == "true"

    retry_max_wait = _int_from_rc(rc, "RETRY_MAX_WAIT", "7200", parser)
    retry_initial_wait = _int_from_rc(rc, "RETRY_INITIAL_WAIT", "30", parser)
    if retry_max_wait < 1:
        parser.error("RETRY_MAX_WAIT must be a positive integer")
    if retry_initial_wait < 1:
        parser.error("RETRY_INITIAL_WAIT must be a positive integer")
    budget_scope_str = rc.get("BUDGET_SCOPE", "module")

    prompts_dir = _resolve_prompts_dir(
        rc.get("PROMPTS_DIR", "prompts/active")
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
    log_dir = git_root / "logs"

    # Restore saved values on resume when not explicitly given
    if args.resume:
        if args.target is None:
            saved = log_dir / "target-branch.txt"
            if saved.is_file():
                target = saved.read_text().strip()
        if args.max_loop is None:
            saved = log_dir / "max-loop.txt"
            if saved.is_file():
                max_loop = int(saved.read_text().strip())

    if args.resume and max_loop is None:
        parser.error(
            "Cannot determine max_loop for resume: logs/max-loop.txt is missing. "
            "Please provide -n / --max-loop explicitly."
        )

    return LoopConfig(
        current_branch=current_branch,
        target_branch=target,
        max_loop=max_loop or 1,
        max_subloop=max_subloop,
        dry_run=dry_run,
        auto_commit=auto_commit,
        resume=args.resume,
        retry_max_wait=retry_max_wait,
        retry_initial_wait=retry_initial_wait,
        budget_scope=_parse_budget_scope(budget_scope_str, parser),
        diagnostic_log=diagnostic_log,
        log_dir=log_dir,
        prompts_dir=prompts_dir,
        pr_number=pr_number,
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Run analysis only, do not fix",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
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

    args = parser.parse_args(argv)

    # Load rc file defaults
    rc = _load_rc_file(".refactorsuggestrc")

    # Resolve values: CLI > rc > defaults
    scope = args.scope or rc.get("SCOPE", "auto")
    target = args.target or rc.get("TARGET_BRANCH", "develop")
    max_loop = (
        args.max_loop if args.max_loop is not None
        else _int_from_rc(rc, "MAX_LOOP", "0", parser) or None
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

    dry_run = _resolve_bool(
        args.dry_run, args.no_dry_run, rc.get("DRY_RUN"), False
    )
    create_pr = _resolve_bool(
        args.create_pr, False, rc.get("CREATE_PR"), False
    )
    auto_approve = args.auto_approve or rc.get(
        "AUTO_APPROVE", "false"
    ) == "true"
    diagnostic_log = args.diagnostic_log or rc.get(
        "DIAGNOSTIC_LOG", "false"
    ) == "true"

    with_review = _resolve_bool(
        args.with_review, False, rc.get("WITH_REVIEW"), False
    )
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
        rc.get("PROMPTS_DIR", "prompts/active")
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
    log_dir = git_root / "logs" / "refactor"

    # Restore saved values on resume when not explicitly given
    if args.resume:
        if args.target is None:
            saved = log_dir / "target-branch.txt"
            if saved.is_file():
                target = saved.read_text().strip()
        if args.max_loop is None:
            saved = log_dir / "max-loop.txt"
            if saved.is_file():
                max_loop = int(saved.read_text().strip())
        if args.scope is None:
            saved = log_dir / "scope.txt"
            if saved.is_file():
                scope = saved.read_text().strip()

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

    config = LoopConfig(
        current_branch=current_branch,
        target_branch=target,
        max_loop=max_loop or 1,
        max_subloop=max_subloop,
        dry_run=dry_run,
        auto_commit=True,
        resume=args.resume,
        auto_approve=auto_approve,
        retry_max_wait=retry_max_wait,
        retry_initial_wait=retry_initial_wait,
        budget_scope=_parse_budget_scope(budget_scope_str, parser),
        diagnostic_log=diagnostic_log,
        log_dir=log_dir,
        prompts_dir=prompts_dir,
        scope=scope,
    )

    extra = _RefactorExtra(
        create_pr=create_pr,
        with_review=with_review,
        review_loops=review_loops,
    )

    return config, extra


def _resolve_bool(
    flag_true: bool | None,
    flag_false: bool,
    rc_value: str | None,
    default: bool,
) -> bool:
    """Resolve a boolean flag with CLI > rc > default precedence."""
    if flag_true:
        return True
    if flag_false:
        return False
    if rc_value is not None:
        return rc_value.lower() == "true"
    return default
