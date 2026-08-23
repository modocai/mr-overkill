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
from importlib.resources import as_file, files
from pathlib import Path

from mr_overkill import commit_scope, wip_scope
from mr_overkill.budget import SKIP_BUDGET_ENV_VAR, budget_gate_disabled
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


def _format_reviewer_context(raw: str) -> str:
    """Wrap non-empty reviewer context in a markdown section."""
    if not raw:
        return ""
    return f"## Author Context\n\n{raw}"


_SCOPE_NOTE_MARKER = "${REVIEW_SCOPE_NOTE}"

# Shared by both WIP mechanisms: the code under review is a draft, and the
# reviewer has to be told so or it will report the draft-ness as the defect.
_WIP_DRAFT_CALIBRATION = """
### This work is unfinished

It has not been committed yet, so judge it as a **draft**:

- Do flag defects in what is actually written — bugs, unsafe assumptions, \
resource leaks, debug statements or credentials left behind.
- Do **not** flag incompleteness itself. A half-implemented feature, a missing \
test for code still being written, or a function that is clearly the next thing \
the author will fill in is not a finding.
"""


def _format_wip_scope(config: LoopConfig) -> str:
    """Build the WIP-scope override block for whichever mechanism is in play."""
    if config.scope_diff_file is not None:
        # No-commit run: the branch diff shows only committed work, so it is
        # actively misleading here — the scope artefact is the whole truth.
        return f"""
> **REVIEW MODE: UNCOMMITTED WORK — read this first. It overrides the framing \
in the line above and re-scopes the Instructions below.**

The change under review is the author's **uncommitted working tree**, captured \
in full at `{config.scope_diff_file}`. Read that file first.

**Ignore the `git diff` command in the Instructions section.** It compares two \
commits, so it shows only the part of the work that happens to be committed \
already — reviewing it would silently skip everything the author is actually \
working on.

The scope diff was taken from the working tree as it exists right now, so its \
paths and line numbers are **current** and you may cite them directly.
{_WIP_DRAFT_CALIBRATION}"""

    # Scaffolding-commit run: the branch diff is exactly right, but the first
    # commit's message would otherwise read as the change under review.
    return f"""
> **REVIEW MODE: UNCOMMITTED WORK — read this first.**

The first commit on this branch, `{wip_scope.SCAFFOLD_MESSAGE}`, is not a change \
the author wrote a commit for. It is their **uncommitted work**, parked in a \
throwaway commit so that it can be reviewed and so that fixes have somewhere to \
land. It will be unwound when this run finishes. Review it exactly as if the \
author had committed it deliberately — it is the change under review.

Any commit after it is a fix an earlier iteration of this loop already applied.
{_WIP_DRAFT_CALIBRATION}"""


def _format_review_scope(config: LoopConfig, iteration: int) -> str:
    """Build the scope override block, or "" in normal branch-diff mode.

    Empty-string-means-no-section, like ``EXTRA_REVIEW_GUIDELINES`` in the
    self-review prompt.  Note ``string.Template`` does not substitute
    recursively, so every value here is interpolated in Python.
    """
    sha = config.scope_commit
    if not sha:
        return _format_wip_scope(config) if config.wip else ""

    diff_path = config.scope_diff_file
    ancestry = ""
    if not commit_scope.is_ancestor_of_head(sha):
        ancestry = (
            f"\n> WARNING: `{sha[:7]}` is **not an ancestor of HEAD**. Much of the "
            "code it touched may not exist in the working tree at all. Report only "
            "defects you can locate in a file that exists right now.\n"
        )

    if iteration <= 1:
        fixes = (
            "None yet — this is the first pass. The `git diff` command in the "
            "Instructions section will print **nothing** on this iteration. That is "
            "expected, it is not an error, and it is **not** the change under review."
        )
    else:
        fixes = (
            f"This is iteration {iteration}. Earlier iterations already produced fix "
            "commits on this branch, and the `git diff` command in the Instructions "
            "section prints **those fixes** — not the change under review. Use it to "
            "(a) confirm which of your earlier findings are now resolved — **never "
            "re-report a resolved finding** — and (b) look for new defects the fixes "
            "themselves introduced.\n\nIf every earlier finding is resolved and you "
            'find nothing new, return zero findings and `"patch is correct"`.'
        )

    return f"""
> **REVIEW MODE: COMMIT SCOPE — read this first. It overrides the framing in the \
line above and re-scopes the Instructions and Review Guidelines below.**

You are **not** reviewing a proposed change. You are reviewing a change that was \
**already merged**:

- **Commit under review**: {commit_scope.commit_headline(sha)}
- **Its diff**: `{diff_path}`
{ancestry}
Read that diff file first. Do **not** try to reconstruct it with `git show` or \
`git diff` — it has already been generated correctly for you, including for merge \
commits (where `git show` prints nothing at all).

### The scope diff is historical — the code has moved on

Other commits have landed since. A line number, a function, or a whole file in the \
scope diff may no longer exist, may have been renamed, or may already have been fixed.

1. Use the scope diff **only** to decide *what is in scope*: which files and which \
behaviour the commit touched.
2. **The current contents of the working tree are the sole authority on whether a \
defect exists.** Before reporting anything, open the current file and confirm the \
defect is still there. If the current code already handles it, drop the finding.
3. Every `code_location` must be a **current** path with **current** line numbers, \
verified by reading the file. Line numbers copied from the scope diff will be wrong.
4. If a file in the scope diff no longer exists, skip it.

### Reading the guidelines below

Wherever a guideline says "this diff", read it as "the change made by \
`{sha[:7]}`, as it manifests in the code as it exists right now". The requirement \
that the issue be **introduced by this diff** still holds: do not flag pre-existing \
problems, and do not flag problems introduced by *later* commits.

### Fixes already applied on this branch

{fixes}
"""


def _review_prompt_vars(config: LoopConfig, iteration: int) -> dict[str, str]:
    """Template variables shared by all three review prompts."""
    return {
        "CURRENT_BRANCH": config.current_branch,
        "TARGET_BRANCH": config.target_branch,
        "ITERATION": str(iteration),
        "REVIEWER_CONTEXT": _format_reviewer_context(config.reviewer_context),
        "REVIEW_SCOPE_NOTE": _format_review_scope(config, iteration),
    }


def _render_review_prompt(
    prompt_file: Path, config: LoopConfig, iteration: int
) -> str | None:
    """Render a review prompt, or ``None`` if it cannot be used.

    In a scoped mode a prompt that predates the feature would silently drop the
    scope note and have the reviewer inspect the wrong diff — which reads as
    "no findings" rather than as a failure.  Refuse instead.
    """
    raw = prompt_file.read_text(encoding="utf-8")
    if (config.scope_commit or config.wip) and _SCOPE_NOTE_MARKER not in raw:
        logger.error(
            "Prompt %s predates scoped review support (no %s marker). "
            "Run 'overkill init' to refresh the prompt templates.",
            prompt_file,
            _SCOPE_NOTE_MARKER,
        )
        return None
    return string.Template(raw).safe_substitute(_review_prompt_vars(config, iteration))


# ── Review schema (single source of truth for structured output) ─────


def _review_schema_text() -> str:
    """Read the bundled review JSON Schema as a string."""
    return files("mr_overkill.data").joinpath("review.schema.json").read_text(
        encoding="utf-8"
    )


def _unwrap_claude_structured_output(output_path: Path) -> None:
    """Replace Claude's ``--output-format json`` wrapper with its inner schema object.

    Claude CLI emits ``{"type":"result", ..., "structured_output": {...}, ...}``
    when both ``--output-format json`` and ``--json-schema`` are set. The
    schema-conforming object lives under ``structured_output``. Downstream
    consumers expect the file to contain that object directly. If the file does
    not match the wrapper shape (older Claude versions, error responses), it is
    left untouched so the parser's fallback tiers can still try.
    """
    try:
        raw = output_path.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(wrapper, dict):
        return
    inner = wrapper.get("structured_output")
    if isinstance(inner, dict):
        output_path.write_text(json.dumps(inner), encoding="utf-8")


# ── Budget / retry helpers (moved from review_loop.py) ───────────────


def _budget_check(
    tool: str, scope: BudgetScope, max_wait: int
) -> bool:
    """Direct budget check without waiting."""
    if budget_gate_disabled():
        logger.info(
            "Budget gate disabled via %s — skipping %s check.",
            SKIP_BUDGET_ENV_VAR,
            tool,
        )
        return True
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
        if self._config.skip_budget_gate:
            logger.info(
                "Budget gate disabled (--no-budget-gate) — running %s anyway.",
                tool,
            )
            return True
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

        prompt_text = _render_review_prompt(prompt_file, config, iteration)
        if prompt_text is None:
            return False

        if not self._budget_fn("codex", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Codex budget timeout (iteration {iteration})."
            )

        stderr_path = output_path.with_suffix(".stderr")
        with as_file(
            files("mr_overkill.data").joinpath("review.schema.json")
        ) as schema_path:
            return retry_codex_cmd(
                stderr_path,
                "Codex review",
                [
                    "codex", "exec", "--sandbox", "read-only",
                    "--output-schema", str(schema_path),
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

        prompt_text = _render_review_prompt(prompt_file, config, iteration)
        if prompt_text is None:
            return False

        if not self._budget_fn("claude", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Claude budget timeout (iteration {iteration})."
            )

        ok = self._retry_fn(
            output_path,
            "Claude review",
            [
                "claude", "-p", "-",
                "--allowedTools", "Bash,Read,Glob,Grep",
                "--output-format", "json",
                "--json-schema", _review_schema_text(),
            ],
            stdin=prompt_text,
        )
        if ok:
            _unwrap_claude_structured_output(output_path)
        return ok


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

        prompt_text = _render_review_prompt(prompt_file, config, iteration)
        if prompt_text is None:
            return False

        if not self._budget_fn("gemini", config.budget_scope, 0):
            raise BudgetTimeoutError(
                f"Gemini budget timeout (iteration {iteration})."
            )

        return retry_gemini_cmd(
            output_path,
            "Gemini review",
            ["gemini", "--sandbox", "--approval-mode", "yolo", "-p", "-"],
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
            ["gemini", "--sandbox", "--approval-mode", "yolo", "-p", "-"],
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
