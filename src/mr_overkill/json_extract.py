"""JSON extraction utilities ported from common.sh and self-review.sh.

Provides a 3-tier extraction pipeline (direct parse -> fenced code block ->
regex fallback) that mirrors _extract_json_from_file in the shell codebase,
plus helpers for path normalisation and refactoring-plan injection.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any


def extract_json_from_file(path: Path) -> dict[str, Any] | None:
    """3-tier JSON extraction: direct parse -> fenced code block -> regex.

    Tier 1: Try ``json.loads`` on the raw file content.  If the file contains
    multiple top-level JSON objects (JSONL), parse each line and return the
    last valid object (mirrors ``jq -s 'last'``).

    Tier 2: Look for a fenced code block (`` ```json ... ``` ``) and parse its
    content.

    Tier 3: Use a greedy regex to extract the outermost ``{ ... }`` and parse.

    Returns
    -------
    dict | None
        Parsed JSON dict, or ``None`` if every tier fails.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return None

    # ── Tier 1: direct parse ──────────────────────────────────────────
    result = _try_direct_parse(content)
    if result is not None:
        return result

    # ── Tier 2: fenced code block ─────────────────────────────────────
    result = _try_fenced_block(content)
    if result is not None:
        return result

    # ── Tier 3: regex fallback ────────────────────────────────────────
    return _try_regex_fallback(content)


def parse_review_json(
    path: Path,
    label: str,
) -> tuple[dict[str, Any] | None, int]:
    """Wrapper around :func:`extract_json_from_file` with standardised warnings.

    Parameters
    ----------
    path:
        File to parse.
    label:
        Human-readable label for warning messages (e.g. ``"review"``).

    Returns
    -------
    tuple[dict | None, int]
        ``(parsed_json, return_code)`` where *return_code* is 0 on success,
        2 if the file was not found, or 1 on parse error.
    """
    try:
        result = extract_json_from_file(path)
    except FileNotFoundError:
        warnings.warn(
            f"{label} output file not found ({path}).",
            stacklevel=2,
        )
        return None, 2

    if result is None:
        warnings.warn(
            f"Could not parse {label} output as JSON. See {path} for details.",
            stacklevel=2,
        )
        return None, 1

    return result, 0


def normalize_paths(review_json: dict[str, Any], repo_root: str) -> dict[str, Any]:
    """Convert absolute file paths in findings to repo-relative.

    Strips *repo_root* (with trailing ``/``) from each finding's
    ``code_location.file_path`` (falling back to ``absolute_file_path``) and
    removes the ``absolute_file_path`` key if present.
    """
    root = repo_root.rstrip("/") + "/"
    findings: list[dict[str, Any]] | None = review_json.get("findings")
    if findings is None:
        return review_json

    result = dict(review_json)
    new_findings: list[dict[str, Any]] = []
    for finding in findings:
        finding = dict(finding)
        loc = dict(finding.get("code_location", {}))
        raw_path: str = loc.get("file_path") or loc.get("absolute_file_path", "")
        if raw_path.startswith(root):
            raw_path = raw_path[len(root) :]
        loc["file_path"] = raw_path
        loc.pop("absolute_file_path", None)
        finding["code_location"] = loc
        new_findings.append(finding)

    result["findings"] = new_findings
    return result


def inject_refactoring_plan(
    review_json: dict[str, Any],
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Inject *plan* into *review_json* under the ``refactoring_plan`` key.

    If *plan* is ``None`` the original dict is returned unchanged.
    """
    if plan is None:
        return review_json
    return {**review_json, "refactoring_plan": plan}


# ── Private helpers ───────────────────────────────────────────────────


def _try_direct_parse(content: str) -> dict[str, Any] | None:
    """Tier 1: try ``json.loads`` directly, with JSONL fallback."""
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # JSONL: parse each non-blank line and keep the last valid dict.
    last_good: dict[str, Any] | None = None
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                last_good = obj
        except json.JSONDecodeError:
            continue
    return last_good


_FENCED_RE = re.compile(
    r"^```[a-zA-Z]*\s*\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)


def _try_fenced_block(content: str) -> dict[str, Any] | None:
    """Tier 2: extract JSON from a fenced code block."""
    match = _FENCED_RE.search(content)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(1))
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


_BRACE_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_regex_fallback(content: str) -> dict[str, Any] | None:
    """Tier 3: greedy regex for the outermost ``{ ... }``."""
    match = _BRACE_RE.search(content)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None
