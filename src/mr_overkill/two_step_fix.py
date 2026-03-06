"""Two-step Claude fix: opinion (read-only) then execute (edit tools).

Ports ``_claude_two_step_fix`` from ``common.sh``.
"""

from __future__ import annotations

import logging
import string
from pathlib import Path

from mr_overkill.git_ops import gen_uuid
from mr_overkill.models import BudgetCheckFn, BudgetScope, RetryFn

logger = logging.getLogger(__name__)


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
) -> bool:
    """Run a two-step Claude fix: opinion (read-only) then execute (edit).

    Step 1 (opinion): Claude analyses the review findings with read-only tools.
    Step 2 (execute): Claude applies fixes using the same session, with edit
    tools enabled.

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
    if not budget_fn("claude", budget_scope, budget_max_wait):
        logger.error("Claude budget timeout before %s opinion.", label)
        return False

    logger.info("Running Claude %s (step 1: opinion)...", label)
    ok = retry_fn(
        opinion_file,
        f"{label} opinion",
        ["claude", "-p", "-", "--session-id", session_id,
         "--allowedTools", "Read,Glob,Grep"],
        stdin=prompt_text,
    )
    if not ok:
        logger.error(
            "Claude %s opinion failed. See %s for details.",
            label,
            opinion_file,
        )
        return False

    logger.info("Opinion saved to %s", opinion_file)

    # ── Step 2: Execute ──────────────────────────────────────────────
    if not budget_fn("claude", budget_scope, budget_max_wait):
        logger.error("Claude budget timeout before %s execute.", label)
        return False

    logger.info("Running Claude %s (step 2: execute)...", label)
    exec_prompt_text = (prompts_dir / execute_prompt).read_text(encoding="utf-8")

    ok = retry_fn(
        fix_file,
        f"{label} execute",
        ["claude", "-p", "-", "--resume", session_id,
         "--allowedTools", "Edit,Read,Glob,Grep,Bash"],
        stdin=exec_prompt_text,
    )
    if not ok:
        logger.error("Claude %s execute failed. See %s for details.", label, fix_file)
        return False

    logger.info("%s log saved to %s", label, fix_file)
    return True
