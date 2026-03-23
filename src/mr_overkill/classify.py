"""Error classification for CLI command failures."""

from __future__ import annotations

import re
from pathlib import Path

from mr_overkill.models import ErrorClass

_MAX_STDERR_BYTES = 4096

# Patterns are compiled once at import time for efficiency.
_TRANSIENT_RE = re.compile(
    r"rate.limit"
    r"|too many requests"
    r"|(?:^|[^0-9])429(?:[^0-9]|$)"
    r"|overloaded"
    r"|(?:^|[^0-9])529(?:[^0-9]|$)"
    r"|(?:^|[^0-9])50[03](?:[^0-9]|$)"
    r"|internal server error"
    r"|capacity"
    r"|token.*limit"
    r"|quota.*exceeded"
    r"|temporarily unavailable",
    re.MULTILINE,
)

_PERMANENT_RE = re.compile(
    r"auth.*fail"
    r"|unauthorized"
    r"|(?:^|[^0-9])403(?:[^0-9]|$)"
    r"|forbidden"
    r"|invalid.*api.key"
    r"|permission denied",
    re.MULTILINE,
)


def classify_cli_error(stderr_path: Path, exit_code: int) -> ErrorClass:
    """Classify a CLI error as transient, permanent, or unknown.

    Reads up to 4 096 bytes from *stderr_path* (tolerates missing files),
    combines with the *exit_code*, and pattern-matches against known error
    signatures.
    """
    head = ""
    if stderr_path.is_file():
        try:
            raw = stderr_path.read_bytes()[:_MAX_STDERR_BYTES]
            head = raw.decode("utf-8", errors="replace")
        except OSError:
            pass

    text = f"exit={exit_code}\n{head}".lower()

    if _TRANSIENT_RE.search(text):
        return ErrorClass.TRANSIENT
    if _PERMANENT_RE.search(text):
        return ErrorClass.PERMANENT
    return ErrorClass.UNKNOWN
