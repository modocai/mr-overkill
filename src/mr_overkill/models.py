"""Shared data models and Protocol definitions for mr-overkill.

All modules depend on this file; it depends on nothing else in the package.
Protocols define DI contracts that enable parallel development of modules
in Wave 2 (retry, two_step_fix, loop_engine) without mutual imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

# ── Enums ────────────────────────────────────────────────────────────


class ErrorClass(StrEnum):
    """Classification of a CLI error for retry decisions."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class BudgetScope(StrEnum):
    """Granularity of a budget check — determines the usage threshold."""

    MICRO = "micro"
    MODULE = "module"
    LAYER = "layer"
    FULL = "full"


class FinalStatus(StrEnum):
    """Terminal states for a review/refactor loop run."""

    ALL_CLEAR = "all_clear"
    NO_DIFF = "no_diff"
    DRY_RUN = "dry_run"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    AUTO_COMMIT_DISABLED = "auto_commit_disabled"
    USER_ABORTED = "user_aborted"
    CODEX_ERROR = "codex_error"
    CODEX_BUDGET_TIMEOUT = "codex_budget_timeout"
    CLAUDE_ERROR = "claude_error"
    PARSE_ERROR = "parse_error"
    STASH_ERROR = "stash_error"
    STASH_CONFLICT = "stash_conflict"
    REVIEW_FAILED = "review_failed"
    COMMIT_PUSH_ERROR = "commit_push_error"


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetStatus:
    """Token-budget snapshot returned by a budget checker.

    Mirrors the JSON schema produced by check-claude-limit.sh and
    check-codex-limit.sh.
    """

    five_hour_used_pct: int | None
    seven_day_used_pct: int | None
    tokens_used: int
    mode: str  # "oauth" | "local" | "session_log" | "no_data"
    tier: str  # "pro" | "max5" | "max20"
    resets_at: str | None
    seven_day_resets_at: str | None = None
    estimated: bool = False


@dataclass(frozen=True)
class CodeLocation:
    """Source location of a review finding."""

    file_path: str
    line_range: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class ReviewFinding:
    """A single finding from a code review."""

    title: str
    confidence_score: float = 0.0
    code_location: CodeLocation = field(default_factory=lambda: CodeLocation(""))
    body: str = ""


@dataclass(frozen=True)
class ReviewResult:
    """Parsed and normalised output of a code review."""

    findings: list[ReviewFinding] = field(default_factory=list)
    overall_correctness: str = "not reviewed"
    refactoring_plan: dict[str, object] | None = None


@dataclass(frozen=True)
class WorktreeSnapshot:
    """A single file's state in a worktree snapshot.

    Corresponds to one line of _snapshot_worktree output:
    ``<hash>\\t<mode>\\t<path>``
    """

    file_hash: str  # git hash-object result, or "DELETED"
    mode: str  # "100644", "100755", or "000000"
    path: str


@dataclass(frozen=True)
class ResumeState:
    """Detected resume state from a previous interrupted run.

    Mirrors the JSON produced by _resume_detect_state in common.sh.
    """

    status: str  # "completed" | "resumable" | "no_logs"
    resume_from: int
    reuse_review: bool
    prev_status: str | None = None


@dataclass
class LoopConfig:
    """Configuration for review_fix_loop / refactor loop.

    Combines CLI args, rc-file values, and runtime state into a single
    structured object that replaces scattered global variables.
    """

    # Branches
    current_branch: str
    target_branch: str

    # Iteration limits
    max_loop: int
    max_subloop: int = 4

    # Behaviour flags
    dry_run: bool = False
    auto_commit: bool = True
    resume: bool = False
    auto_approve: bool = False

    # Retry / budget
    retry_max_wait: int = 7200
    retry_initial_wait: int = 30
    budget_scope: BudgetScope = BudgetScope.MODULE
    diagnostic_log: bool = False

    # Paths
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    prompts_dir: Path = field(default_factory=lambda: Path("prompts/active"))

    # PR
    pr_number: str | None = None

    # Refactor-specific
    scope: str | None = None  # micro | module | layer | full
    skip_initial_no_diff: bool = False  # refactor: 1st iteration has no diff


@dataclass(frozen=True)
class LoopResult:
    """Outcome of a complete review-fix loop run."""

    final_status: FinalStatus
    iterations_run: int
    summary_path: Path | None = None


# ── Protocols (DI contracts) ─────────────────────────────────────────


class RetryFn(Protocol):
    """Contract for a CLI-retry wrapper (e.g. retry_claude_cmd).

    Parameters
    ----------
    output_path : Path
        File to write command output to.
    label : str
        Human-readable label for logging (e.g. "fix opinion").
    cmd_args : list[str]
        Command and arguments to invoke.
    **kw
        Implementation-specific options (e.g. ``stdin``).

    Returns
    -------
    bool
        True on success, False on permanent failure / timeout.
    """

    def __call__(
        self,
        output_path: Path,
        label: str,
        cmd_args: list[str],
        **kw: object,
    ) -> bool: ...


class BudgetCheckFn(Protocol):
    """Contract for a pre-flight budget gate.

    Parameters
    ----------
    tool : str
        Tool identifier ("claude" or "codex").
    scope : BudgetScope
        Required budget scope (determines threshold).
    max_wait : int
        Maximum seconds to wait for budget recovery.

    Returns
    -------
    bool
        True if budget is sufficient (go), False on timeout (no-go).
    """

    def __call__(
        self,
        tool: str,
        scope: BudgetScope,
        max_wait: int,
    ) -> bool: ...


class FixFn(Protocol):
    """Contract for a two-step fix operation.

    Parameters
    ----------
    review_json : str
        Serialised review findings JSON.
    label : str
        Human-readable label for logging.
    **kw
        Implementation-specific options (prompts, session ids, etc.).

    Returns
    -------
    bool
        True on success, False on failure.
    """

    def __call__(
        self,
        review_json: str,
        label: str,
        **kw: object,
    ) -> bool: ...


class ResumeDetectorFn(Protocol):
    """Contract for detecting resume state from logs.

    Parameters
    ----------
    log_dir : Path
        Directory containing iteration log files.
    commit_pattern : str
        Git log search string for commit detection.

    Returns
    -------
    ResumeState
        Detected resume state.
    """

    def __call__(
        self,
        log_dir: Path,
        commit_pattern: str,
    ) -> ResumeState: ...
