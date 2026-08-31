"""Single source of truth for the paths overkill owns inside a project.

Three questions get asked about the same set of paths, in different modules:

1. May this file be dirty without blocking a run? (the dirty-worktree check)
2. Must it be stashed before the fixer touches the tree? (the stash allowlist)
3. Must it be kept out of commits, diffs and review scope? (the log prefixes)

Each used to carry its own copy of the answer, and they had already drifted:
the stash allowlist was missing two of the legacy rc paths the dirty check
allowed, so the same config file was protected under one of its names and not
under another.  Deriving the sets from the directory and file names below is
what keeps that from happening again — adding a name adds it everywhere.

This module deliberately imports nothing else from the package.  It answers
questions *about* paths; running git against them belongs to the caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# ── The workspace directory ──────────────────────────────────────────

#: Where overkill keeps its config, prompts and logs inside a project.
WORKSPACE_DIR = ".overkill"

#: What that directory was called before the rebrand.  Still recognised: a
#: repo initialised by an older version keeps working until ``overkill init``
#: migrates it, and half-migrated repos (both directories present) exist.
LEGACY_WORKSPACE_DIR = ".review-loop"

_WORKSPACE_DIRS = (WORKSPACE_DIR, LEGACY_WORKSPACE_DIR)

# ── Config file names ────────────────────────────────────────────────

RC_NAME = ".overkillrc"
LEGACY_RC_NAME = ".reviewlooprc"
REFACTOR_RC_NAME = ".refactorsuggestrc"

_RC_NAMES = (RC_NAME, LEGACY_RC_NAME, REFACTOR_RC_NAME)

# ── Derived path policy ──────────────────────────────────────────────

#: Prefixes of run artefacts.  These are the tool's own output, so they are
#: never review scope, never part of a commit, and never work in progress.
LOG_PREFIXES: tuple[str, ...] = tuple(f"{d}/logs/" for d in _WORKSPACE_DIRS)

#: Default location of the prompt templates, relative to the repo root.
DEFAULT_PROMPTS_DIR = f"{WORKSPACE_DIR}/prompts/active"

#: Files a run may find dirty without refusing to start, and which are stashed
#: before the fixer runs so it cannot commit them.  Every rc name is listed
#: under both workspace directories and bare at the repo root, because all
#: three layouts are in the wild; enumerating the product rather than the
#: combinations that happen to exist is what stops one from being forgotten.
ALLOWLISTED_FILES: frozenset[str] = frozenset(
    {".gitignore"}
    | {f"{d}/{name}" for d in _WORKSPACE_DIRS for name in _RC_NAMES}
    | set(_RC_NAMES)
)


def is_log_artefact(path: str) -> bool:
    """Whether *path* is one of the tool's own run artefacts."""
    return path.startswith(LOG_PREFIXES)


def is_tool_owned(path: str) -> bool:
    """Whether *path* belongs to overkill rather than to the author's work."""
    return path in ALLOWLISTED_FILES or is_log_artefact(path)


def non_allowlisted(paths: Iterable[str]) -> list[str]:
    """The paths in *paths* that are the author's, not the tool's.

    Callers pass whatever git reported dirty; a non-empty result is the reason
    to refuse a run, and the list itself is what the error message names.
    """
    return [p for p in paths if not is_tool_owned(p)]


def workspace_paths(root: Path, *parts: str) -> list[Path]:
    """The current and legacy locations of *parts* under *root*, in that order.

    Callers walk the list and take the first that exists, so a current layout
    always wins over a leftover legacy one.
    """
    return [root / d / Path(*parts) for d in _WORKSPACE_DIRS]
