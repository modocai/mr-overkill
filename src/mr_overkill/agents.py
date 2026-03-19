"""Agent abstractions for reviewer/coder backends.

Provides ABC classes and concrete implementations for review, fix, and
self-review agents.  Factory functions create the right agent based on
backend/variant parameters, replacing the closure-based factories that
were previously duplicated across review_loop.py and refactor_suggest.py.
"""

from __future__ import annotations

import json
import logging
import string
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from mr_overkill.budget.claude import claude_budget_sufficient
from mr_overkill.budget.codex import codex_budget_sufficient
from mr_overkill.budget.gemini import gemini_budget_sufficient
from mr_overkill.models import (
    BudgetCheckFn,
    BudgetScope,
    BudgetTimeoutError,
    LoopConfig,
    RetryFn,
    WorktreeSnapshot,
)
from mr_overkill.retry import (
    retry_claude_cmd,
    retry_codex_cmd,
    retry_gemini_cmd,
    wait_for_budget,
)
from mr_overkill.self_review import self_review_subloop
from mr_overkill.two_step_fix import claude_two_step_fix

logger = logging.getLogger(__name__)


# ── Budget / retry helpers (moved from review_loop.py) ───────────────


def _budget_check(
    tool: str, scope: BudgetScope, max_wait: int
) -> bool:
    """Direct budget check without waiting."""
    if tool == "claude":
        return claude_budget_sufficient(scope)
    if tool == "codex":
        return codex_budget_sufficient(scope)
    if tool == "gemini":
        return gemini_budget_sufficient(scope)
    return True


class _BudgetFn:
    """Budget-wait callable bound to config defaults.

    Implemented as a class to satisfy the ``BudgetCheckFn`` Protocol.
    """

    def __init__(self, config: LoopConfig) -> None:
        self._config = config

    def __call__(
        self, tool: str, scope: BudgetScope, max_wait: int
    ) -> bool:
        actual = (
            max_wait if max_wait > 0 else self._config.retry_max_wait
        )
        return wait_for_budget(_budget_check, tool, scope, actual)


def _make_budget_fn(config: LoopConfig) -> BudgetCheckFn:
    """Create a budget-wait function bound to config defaults."""
    return _BudgetFn(config)


class _RetryFn:
    """Retry callable bound to config settings.

    Implemented as a class to satisfy the ``RetryFn`` Protocol.
    """

    def __init__(self, config: LoopConfig) -> None:
        self._config = config

    def __call__(
        self,
        output_path: Path,
        label: str,
        cmd_args: list[str],
        **kw: object,
    ) -> bool:
        stdin = kw.get("stdin")
        return retry_claude_cmd(
            output_path,
            label,
            cmd_args,
            stdin=str(stdin) if stdin is not None else None,
            max_wait=self._config.retry_max_wait,
            initial_wait=self._config.retry_initial_wait,
            diagnostic_log=self._config.diagnostic_log,
        )


def _make_retry_fn(config: LoopConfig) -> RetryFn:
    """Create a retry function bound to config settings."""
    return _RetryFn(config)


# ── ABC definitions ──────────────────────────────────────────────────


class ReviewAgent(ABC):
    """Abstract base for agents that run code review."""

    @abstractmethod
    def __call__(self, output_path: Path, iteration: int) -> bool: ...


class FixAgent(ABC):
    """Abstract base for agents that apply fixes."""

    @abstractmethod
    def __call__(
        self, review_json: str, label: str, **kw: object
    ) -> bool: ...


class SelfReviewAgent(ABC):
    """Abstract base for agents that run self-review sub-loops."""

    @abstractmethod
    def __call__(
        self,
        pre_fix_snapshot: list[WorktreeSnapshot],
        max_subloop: int,
        log_dir: Path,
        iteration: int,
        review_json_str: str,
    ) -> str: ...


# ── Concrete implementations ────────────────────────────────────────


class CodexReviewAgent(ReviewAgent):
    """Codex-based reviewer for the standard review-loop."""

    def __init__(self, config: LoopConfig) -> None:
        self._config = config
        self._budget_fn = _make_budget_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        prompt_file = config.prompts_dir / "codex-review.prompt.md"
        if not prompt_file.is_file():
            logger.error("Review prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
        })

        if not self._budget_fn("codex", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Codex budget timeout (iteration {iteration})."
            )

        stderr_path = output_path.with_suffix(".stderr")
        return retry_codex_cmd(
            stderr_path,
            "Codex review",
            [
                "codex", "exec", "--sandbox", "read-only",
                "-o", str(output_path), prompt_text,
            ],
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
        )


class CodexRefactorReviewAgent(ReviewAgent):
    """Codex-based reviewer for scope-specific refactor analysis."""

    def __init__(self, config: LoopConfig, scope: str) -> None:
        self._config = config
        self._scope = scope
        self._budget_fn = _make_budget_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        scope = self._scope

        # Refresh source file list each iteration
        source_files_path = config.log_dir / "source-files.txt"
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        source_files_path.write_text(result.stdout)

        prompt_file = (
            config.prompts_dir / f"codex-refactor-{scope}.prompt.md"
        )
        if not prompt_file.is_file():
            logger.error("Prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
            "SOURCE_FILES_PATH": str(
                config.log_dir / "source-files.txt"
            ),
        })

        if not self._budget_fn("codex", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Codex budget timeout (iteration {iteration})."
            )

        stderr_path = output_path.with_suffix(".stderr")
        return retry_codex_cmd(
            stderr_path,
            "Codex analysis",
            [
                "codex", "exec", "--sandbox", "read-only",
                "-o", str(output_path), prompt_text,
            ],
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
        )


class ClaudeReviewAgent(ReviewAgent):
    """Claude-based reviewer for the standard review-loop."""

    def __init__(self, config: LoopConfig) -> None:
        self._config = config
        self._budget_fn = _make_budget_fn(config)
        self._retry_fn = _make_retry_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        prompt_file = config.prompts_dir / "claude-review.prompt.md"
        if not prompt_file.is_file():
            logger.error("Review prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
        })

        if not self._budget_fn("claude", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Claude budget timeout (iteration {iteration})."
            )

        return self._retry_fn(
            output_path,
            "Claude review",
            [
                "claude", "-p", "-",
                "--allowedTools", "Bash,Read,Glob,Grep",
            ],
            stdin=prompt_text,
        )


class ClaudeRefactorReviewAgent(ReviewAgent):
    """Claude-based reviewer for scope-specific refactor analysis."""

    def __init__(self, config: LoopConfig, scope: str) -> None:
        self._config = config
        self._scope = scope
        self._budget_fn = _make_budget_fn(config)
        self._retry_fn = _make_retry_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        scope = self._scope

        # Refresh source file list each iteration
        source_files_path = config.log_dir / "source-files.txt"
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        source_files_path.write_text(result.stdout)

        prompt_file = (
            config.prompts_dir / f"claude-refactor-{scope}.prompt.md"
        )
        if not prompt_file.is_file():
            logger.error("Prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
            "SOURCE_FILES_PATH": str(
                config.log_dir / "source-files.txt"
            ),
        })

        if not self._budget_fn("claude", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Claude budget timeout (iteration {iteration})."
            )

        return self._retry_fn(
            output_path,
            "Claude analysis",
            [
                "claude", "-p", "-",
                "--allowedTools", "Bash,Read,Glob,Grep",
            ],
            stdin=prompt_text,
        )


class GeminiReviewAgent(ReviewAgent):
    """Gemini-based reviewer for the standard review-loop."""

    def __init__(self, config: LoopConfig) -> None:
        self._config = config
        self._budget_fn = _make_budget_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        prompt_file = config.prompts_dir / "gemini-review.prompt.md"
        if not prompt_file.is_file():
            logger.error("Review prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
        })

        if not self._budget_fn("gemini", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Gemini budget timeout (iteration {iteration})."
            )

        return retry_gemini_cmd(
            output_path,
            "Gemini review",
            ["gemini", "--approval-mode", "plan", "-p", "-"],
            stdin=prompt_text,
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
        )


class GeminiRefactorReviewAgent(ReviewAgent):
    """Gemini-based reviewer for scope-specific refactor analysis."""

    def __init__(self, config: LoopConfig, scope: str) -> None:
        self._config = config
        self._scope = scope
        self._budget_fn = _make_budget_fn(config)

    def __call__(self, output_path: Path, iteration: int) -> bool:
        config = self._config
        scope = self._scope

        # Refresh source file list each iteration
        source_files_path = config.log_dir / "source-files.txt"
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        source_files_path.write_text(result.stdout)

        prompt_file = (
            config.prompts_dir / f"gemini-refactor-{scope}.prompt.md"
        )
        if not prompt_file.is_file():
            logger.error("Prompt not found: %s", prompt_file)
            return False

        tmpl = string.Template(
            prompt_file.read_text(encoding="utf-8")
        )
        prompt_text = tmpl.safe_substitute({
            "CURRENT_BRANCH": config.current_branch,
            "TARGET_BRANCH": config.target_branch,
            "ITERATION": str(iteration),
            "SOURCE_FILES_PATH": str(
                config.log_dir / "source-files.txt"
            ),
        })

        if not self._budget_fn("gemini", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Gemini budget timeout (iteration {iteration})."
            )

        return retry_gemini_cmd(
            output_path,
            "Gemini analysis",
            ["gemini", "--approval-mode", "plan", "-p", "-"],
            stdin=prompt_text,
            max_wait=config.retry_max_wait,
            initial_wait=config.retry_initial_wait,
        )


class ClaudeFixAgent(FixAgent):
    """Claude-based fixer using configurable two-step fix prompts."""

    def __init__(
        self,
        config: LoopConfig,
        *,
        opinion_prompt: str = "claude-fix.prompt.md",
        execute_prompt: str = "claude-fix-execute.prompt.md",
    ) -> None:
        self._config = config
        self._retry_fn = _make_retry_fn(config)
        self._budget_fn = _make_budget_fn(config)
        self._opinion_prompt = opinion_prompt
        self._execute_prompt = execute_prompt

    def __call__(
        self, review_json: str, label: str, **kw: object
    ) -> bool:
        config = self._config
        log_dir = config.log_dir

        return claude_two_step_fix(
            review_json=review_json,
            opinion_file=log_dir / f"opinion-{label}.md",
            fix_file=log_dir / f"fix-{label}.md",
            label=label,
            retry_fn=self._retry_fn,
            budget_fn=self._budget_fn,
            prompts_dir=config.prompts_dir,
            current_branch=config.current_branch,
            target_branch=config.target_branch,
            budget_scope=config.budget_scope,
            budget_max_wait=config.retry_max_wait,
            opinion_prompt=self._opinion_prompt,
            execute_prompt=self._execute_prompt,
            fix_history=str(kw.get("fix_history", "")),
        )


class ClaudeSelfReviewAgent(SelfReviewAgent):
    """Claude-based self-review agent wrapping self_review_subloop."""

    def __init__(
        self,
        config: LoopConfig,
        fixer: FixAgent,
    ) -> None:
        self._config = config
        self._fixer = fixer
        self._retry_fn = _make_retry_fn(config)
        self._budget_fn = _make_budget_fn(config)

    def __call__(
        self,
        pre_fix_snapshot: list[WorktreeSnapshot],
        max_subloop: int,
        log_dir: Path,
        iteration: int,
        review_json_str: str,
    ) -> str:
        config = self._config
        fixer = self._fixer

        def fix_fn(
            review_json: str, label: str, **kw: object
        ) -> bool:
            return bool(fixer(review_json, label, **kw))

        return self_review_subloop(
            pre_fix_snapshot=pre_fix_snapshot,
            max_subloop=max_subloop,
            log_dir=log_dir,
            iteration=iteration,
            review_json_str=review_json_str,
            retry_fn=self._retry_fn,
            budget_fn=self._budget_fn,
            fix_fn=fix_fn,
            prompts_dir=config.prompts_dir,
            current_branch=config.current_branch,
            target_branch=config.target_branch,
            budget_scope=config.budget_scope,
            dry_run=config.dry_run,
            fix_nits=config.fix_nits,
            original_review_json=json.loads(review_json_str),
        )


# ── Factory functions ────────────────────────────────────────────────


def create_review_agent(
    config: LoopConfig,
    *,
    scope: str | None = None,
) -> ReviewAgent:
    """Create the appropriate review agent.

    Parameters
    ----------
    config : LoopConfig
        Loop configuration.
    scope : str, optional
        Refactor scope (e.g. "micro", "module").  When provided,
        returns a refactor-specific reviewer; otherwise returns the
        standard review-loop reviewer.
    """
    backend = config.reviewer_backend
    if scope is not None:
        if backend == "claude":
            return ClaudeRefactorReviewAgent(config, scope)
        if backend == "gemini":
            return GeminiRefactorReviewAgent(config, scope)
        return CodexRefactorReviewAgent(config, scope)
    if backend == "claude":
        return ClaudeReviewAgent(config)
    if backend == "gemini":
        return GeminiReviewAgent(config)
    return CodexReviewAgent(config)


def create_fix_agent(
    config: LoopConfig,
    *,
    variant: str = "review",
) -> FixAgent:
    """Create the appropriate fix agent.

    Parameters
    ----------
    config : LoopConfig
        Loop configuration.
    variant : str
        ``"review"`` for the standard review-loop fixer,
        ``"refactor"`` for the refactor-specific fixer.
    """
    if variant == "refactor":
        return ClaudeFixAgent(
            config,
            opinion_prompt="claude-refactor-fix.prompt.md",
            execute_prompt="claude-refactor-fix-execute.prompt.md",
        )
    return ClaudeFixAgent(config)


def create_self_review_agent(
    config: LoopConfig,
    fixer: FixAgent,
) -> SelfReviewAgent:
    """Create a self-review agent.

    Parameters
    ----------
    config : LoopConfig
        Loop configuration.
    fixer : FixAgent
        The fix agent to use for re-fix attempts during self-review.
    """
    return ClaudeSelfReviewAgent(config, fixer)
