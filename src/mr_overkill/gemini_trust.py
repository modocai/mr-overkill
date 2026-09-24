"""Carry the user's Gemini folder-trust decision into the sandbox.

Gemini refuses to run headless in a folder it does not trust (exit 55), and
overkill always runs it with ``--sandbox``.  Under the macOS seatbelt sandbox
Gemini looks for its trust list in ``~/.cache/.gemini`` rather than
``~/.gemini``, and the sandbox profile denies reading the real file, so a
folder the user trusted is still refused.

Trust is what stops a reviewed repository's own ``.gemini/`` config from being
loaded, so it is not ours to grant wholesale.  Instead the trust list is read
here, on the host, with Gemini's own matching rules, and
``GEMINI_CLI_TRUST_WORKSPACE=true`` is passed in only when the user has
already trusted the directory.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

TRUST_ENV_VAR = "GEMINI_CLI_TRUST_WORKSPACE"
# Exit code Gemini uses for FatalUntrustedWorkspaceError.
UNTRUSTED_EXIT_CODE = 55

_TRUST_FOLDER = "TRUST_FOLDER"
_TRUST_PARENT = "TRUST_PARENT"
_DO_NOT_TRUST = "DO_NOT_TRUST"


def trusted_folders_path(env: Mapping[str, str]) -> Path:
    """Where Gemini keeps the trust list when it runs outside a sandbox."""
    override = env.get("GEMINI_CLI_TRUSTED_FOLDERS_PATH")
    if override:
        return Path(override)
    home = env.get("GEMINI_CLI_HOME") or str(Path.home())
    return Path(home) / ".gemini" / "trustedFolders.json"


def _normalize(path: str) -> str:
    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = os.path.abspath(path)
    # Gemini folds case on the platforms whose default filesystems do.
    if sys.platform in ("darwin", "win32"):
        return resolved.lower()
    return resolved


def _is_subpath(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:  # different drives on Windows
        return False


def is_path_trusted(rules: Mapping[str, object], location: str) -> bool | None:
    """Apply Gemini's longest-match trust rules.

    Returns True or False when a rule decides, None when none matches.
    """
    target = _normalize(location)
    best_len = -1
    best_level: object = None
    for rule_path, level in rules.items():
        effective = os.path.dirname(rule_path) if level == _TRUST_PARENT else rule_path
        if _is_subpath(_normalize(effective), target) and len(rule_path) > best_len:
            best_len = len(rule_path)
            best_level = level
    if best_level == _DO_NOT_TRUST:
        return False
    if best_level in (_TRUST_FOLDER, _TRUST_PARENT):
        return True
    return None


def _load_rules(path: Path) -> Mapping[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # Gemini accepts comments here; leave anything we cannot parse to it.
        logger.debug("Cannot read Gemini trust list %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def sandbox_env(
    cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, str] | None:
    """Environment for a sandboxed Gemini run, or None to inherit unchanged.

    Anything the user already set about trust wins: an explicit
    ``GEMINI_CLI_TRUST_WORKSPACE`` either way, or restricted mode.
    """
    base = os.environ if env is None else env
    if TRUST_ENV_VAR in base or base.get("GEMINI_RESTRICTED_MODE") == "true":
        return None
    rules = _load_rules(trusted_folders_path(base))
    if not rules:
        return None
    location = str(cwd if cwd is not None else Path.cwd())
    if is_path_trusted(rules, location) is not True:
        return None
    return {**base, TRUST_ENV_VAR: "true"}


def untrusted_hint(cwd: Path | None = None) -> str:
    """What to tell the user when Gemini still refuses the folder."""
    location = cwd if cwd is not None else Path.cwd()
    return (
        f"Gemini refused {location} as untrusted. overkill passes your trust "
        f"decision into Gemini's sandbox only for folders in "
        f"{trusted_folders_path(os.environ)}. Trust it by running `gemini` "
        f"there once, or set {TRUST_ENV_VAR}=true for this run if you trust "
        f"the repository's .gemini/ configuration."
    )
