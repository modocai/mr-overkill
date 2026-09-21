"""Readable, narrowly scoped evidence for Google review backends."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from mr_overkill.two_step_fix import backend_command


@contextmanager
def google_review_evidence(
    backend: str, content: str,
) -> Iterator[tuple[Path, list[str]]]:
    """Expose only generated evidence, without disabling repository ignore rules."""
    with TemporaryDirectory(prefix="overkill-review-") as directory:
        root = Path(directory).resolve()
        evidence = root / "evidence.txt"
        evidence.write_text(content, encoding="utf-8")
        flag = "--add-dir" if backend == "agy" else "--include-directories"
        yield evidence, [*backend_command(backend), flag, str(root)]

