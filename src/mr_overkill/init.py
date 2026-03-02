"""``mr-overkill init`` — scaffold ``.review-loop/`` in a target project."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterator

_REVIEW_LOOP_DIR = ".review-loop"


@contextmanager
def _data_root() -> Iterator[Path]:
    """Resolve the package data directory.

    When installed from a wheel, ``importlib.resources`` finds files that
    hatch ``force-include`` placed inside the package.  During development
    (editable install), those files don't exist under ``src/`` — fall back
    to the repository root.

    Yields the path inside the ``as_file`` context so that any temporary
    extraction directory stays alive while the caller uses it.
    """
    pkg = files("mr_overkill.data")
    with as_file(pkg) as data_dir:
        candidate = data_dir / "prompts" / "active"
        if candidate.is_dir():
            yield Path(data_dir)
            return

    # Development fallback: walk up from this file to the repo root.
    repo = Path(__file__).resolve().parent.parent.parent
    if (repo / "prompts" / "active").is_dir():
        yield repo
        return
    msg = "Cannot locate bundled data (prompts/active not found)"
    raise FileNotFoundError(msg)


def _copy_prompts(data: Path, dest: Path) -> list[str]:
    """Copy bundled prompt templates into *dest*/prompts/active/."""
    prompts_dest = dest / "prompts" / "active"
    prompts_dest.mkdir(parents=True, exist_ok=True)

    prompts_src = data / "prompts" / "active"
    manifest: list[str] = []
    for src_file in sorted(prompts_src.iterdir()):
        if src_file.is_file():
            target = prompts_dest / src_file.name
            shutil.copy2(src_file, target)
            manifest.append(f"prompts/active/{src_file.name}")
    return manifest


def _copy_rc_files(data: Path, dest: Path) -> list[str]:
    """Copy RC example files as live configs, preserving user edits."""
    manifest: list[str] = []
    rc_pairs = [
        (".reviewlooprc.example", ".reviewlooprc"),
        (".refactorsuggestrc.example", ".refactorsuggestrc"),
    ]
    for example_name, live_name in rc_pairs:
        live_path = dest / live_name
        src_path = data / example_name
        if not live_path.exists() and src_path.exists():
            shutil.copy2(src_path, live_path)
        manifest.append(live_name)
    return manifest


def _ensure_gitignore(project_root: Path) -> None:
    """Add ``.review-loop/`` to ``.gitignore`` if absent."""
    gitignore = project_root / ".gitignore"
    marker = ".review-loop/"

    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        if marker in content.splitlines():
            return
        # Ensure trailing newline before appending
        if content and not content.endswith("\n"):
            content += "\n"
    else:
        content = ""

    content += f"# review-loop (added by mr-overkill init)\n{marker}\n"
    gitignore.write_text(content, encoding="utf-8")


def _write_manifest(dest: Path, entries: list[str]) -> None:
    """Write ``.install-manifest`` listing tool-owned files."""
    manifest_path = dest / ".install-manifest"
    manifest_path.write_text(
        "\n".join(sorted(entries)) + "\n",
        encoding="utf-8",
    )


def init_project(target_dir: Path) -> None:
    """Initialize ``.review-loop/`` in the target project.

    Idempotent: safe to re-run.  Prompt templates are always refreshed.
    RC config files are only created if missing (preserving user edits).
    """
    with _data_root() as data:
        dest = target_dir / _REVIEW_LOOP_DIR
        dest.mkdir(parents=True, exist_ok=True)

        # 1. Copy prompt templates (always overwrite — tool-owned)
        manifest = _copy_prompts(data, dest)

        # 2. Copy RC files (only if missing — user-editable)
        manifest.extend(_copy_rc_files(data, dest))

    # 3. Create log directories
    (dest / "logs").mkdir(exist_ok=True)
    (dest / "logs" / "refactor").mkdir(exist_ok=True)

    # 4. Write install manifest
    _write_manifest(dest, manifest)

    # 5. Update .gitignore
    _ensure_gitignore(target_dir)

    n_prompts = len([e for e in manifest if e.startswith("prompts/")])
    print(f"Initialized {dest}/")
    print(f"  prompts:  {n_prompts} templates")
    print("  configs:  .reviewlooprc, .refactorsuggestrc")
    print("  logs:     logs/, logs/refactor/")
