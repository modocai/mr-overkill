"""Two-step fix: opinion (read-only) then execute (edit tools).

Ports ``_claude_two_step_fix`` from ``common.sh``.
"""

from __future__ import annotations

import logging
import string
from pathlib import Path

from mr_overkill.git_ops import gen_uuid
from mr_overkill.models import BudgetCheckFn, BudgetScope, RetryFn

logger = logging.getLogger(__name__)


def backend_command(backend: str, *, edit: bool = False) -> list[str]:
    """Build non-interactive commands with role-appropriate permissions."""
    if backend == "codex":
        return [
            "codex", "exec", "--sandbox",
            "workspace-write" if edit else "read-only", "-",
        ]
    if backend == "agy":
        return [
            "agy", "--sandbox", "--mode",
            "accept-edits" if edit else "plan", "--output-format", "text",
        ]
    if backend == "gemini":
        return [
            "gemini", "--sandbox", "--approval-mode",
            "yolo" if edit else "plan", "--output-format", "text", "-p", "-",
        ]
    if backend == "claude":
        return [
            "claude", "-p", "-", "--allowedTools",
            "Edit,Read,Glob,Grep,Bash" if edit else "Read,Glob,Grep",
        ]
    raise ValueError(f"Unsupported backend: {backend}")


def _render_prompt(
    template_path: Path,
    variables: dict[str, str],
) -> str:
    """Render a prompt template with ``$VAR`` substitution.

    Uses :class:`string.Template` which supports ``$VAR`` and ``${VAR}``
    syntax, matching the ``envsubst`` behaviour from the bash scripts.
    Missing variables are left as-is (safe_substitute).
    """
    content = template_path.read_text(encoding="utf-8")
    tmpl = string.Template(content)
    return tmpl.safe_substitute(variables)


def claude_two_step_fix(
    review_json: str,
    opinion_file: Path,
    fix_file: Path,
    label: str,
    *,
    retry_fn: RetryFn,
    budget_fn: BudgetCheckFn,
    prompts_dir: Path,
    current_branch: str,
    target_branch: str,
    budget_scope: BudgetScope = BudgetScope.MODULE,
    budget_max_wait: int = 7200,
    opinion_prompt: str = "claude-fix.prompt.md",
    execute_prompt: str = "claude-fix-execute.prompt.md",
    fix_history: str = "",
    backend: str = "claude",
) -> bool:
    """Run a two-step fix with the selected backend.

    Claude resumes the opinion session; other backends receive the opinion
    explicitly in a fresh editing invocation. The historical function name
    is retained for compatibility.

    Returns True on success, False on failure.
    """
    session_id = gen_uuid()

    # ── Step 1: Opinion ──────────────────────────────────────────────
    prompt_vars = {
        "CURRENT_BRANCH": current_branch,
        "TARGET_BRANCH": target_branch,
        "REVIEW_JSON": review_json,
        "FIX_HISTORY": fix_history,
    }
    prompt_text = _render_prompt(prompts_dir / opinion_prompt, prompt_vars)

    # Pre-flight budget check
    if not budget_fn(backend, budget_scope, budget_max_wait):
        logger.error("%s budget timeout before %s opinion.", backend, label)
        return False

    logger.info("Running %s %s (step 1: opinion)...", backend, label)
    opinion_cmd = backend_command(backend)
    if backend == "claude":
        opinion_cmd = ["claude", "-p", "-", "--session-id", session_id,
                       "--allowedTools", "Read,Glob,Grep"]
    ok = retry_fn(
        opinion_file,
        f"{label} opinion",
        opinion_cmd,
        stdin=prompt_text,
    )
    if not ok:
        logger.error(
            "%s %s opinion failed. See %s for details.",
            backend, label,
            opinion_file,
        )
        return False

    logger.info("Opinion saved to %s", opinion_file)

    # ── Step 2: Execute ──────────────────────────────────────────────
    if not budget_fn(backend, budget_scope, budget_max_wait):
        logger.error("%s budget timeout before %s execute.", backend, label)
        return False

    logger.info("Running %s %s (step 2: execute)...", backend, label)
    exec_prompt_text = (prompts_dir / execute_prompt).read_text(encoding="utf-8")
    execute_cmd = backend_command(backend, edit=True)
    if backend == "claude":
        execute_cmd = ["claude", "-p", "-", "--resume", session_id,
                       "--allowedTools", "Edit,Read,Glob,Grep,Bash"]
    else:
        if not opinion_file.is_file() or not opinion_file.read_text().strip():
            logger.error("Missing opinion output for %s", label)
            return False
        # Pass the exact assessment forward without depending on session APIs.
        exec_prompt_text = (
            prompt_text + "\n\nPrior assessment:\n"
            + opinion_file.read_text(encoding="utf-8")
            + "\n\nExecution instructions:\n" + exec_prompt_text
        )

    ok = retry_fn(
        fix_file,
        f"{label} execute",
        execute_cmd,
        stdin=exec_prompt_text,
    )
    if not ok:
        logger.error(
            "%s %s execute failed. See %s for details.", backend, label, fix_file
        )
        return False

    logger.info("%s log saved to %s", label, fix_file)
    return True
