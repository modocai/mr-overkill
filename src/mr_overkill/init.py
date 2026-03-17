"""``overkill init`` — scaffold ``.overkill/`` in a target project."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

logger = logging.getLogger(__name__)

_OVERKILL_DIR = ".overkill"
_LEGACY_DIR = ".review-loop"


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
    """Copy RC example files as live configs, preserving user edits.

    If the live config already exists under ``.overkill/``, it is kept
    as-is.  Otherwise, a legacy rc at the repo root is migrated first;
    only when neither exists does the bundled example get copied.
    """
    manifest: list[str] = []
    project_root = dest.parent
    rc_pairs = [
        (".overkillrc.example", ".overkillrc", ".reviewlooprc"),
        (".refactorsuggestrc.example", ".refactorsuggestrc", None),
    ]
    for example_name, live_name, legacy_name in rc_pairs:
        live_path = dest / live_name
        if not live_path.exists():
            # Check for legacy rc names at repo root
            legacy_names = [n for n in (legacy_name, live_name) if n]
            migrated = False
            for ln in legacy_names:
                legacy = project_root / ln
                if legacy.is_file():
                    shutil.copy2(legacy, live_path)
                    migrated = True
                    break
            if not migrated and (src_path := data / example_name).exists():
                shutil.copy2(src_path, live_path)
        manifest.append(live_name)
    return manifest


def _ensure_gitignore(project_root: Path) -> None:
    """Add ``.overkill/`` to ``.gitignore`` if absent."""
    gitignore = project_root / ".gitignore"
    marker = ".overkill/"
    legacy_marker = ".review-loop/"

    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Migrate legacy marker
        if legacy_marker in lines and marker not in lines:
            content = content.replace(legacy_marker, marker)
            content = content.replace(
                "# review-loop (added by overkill init)",
                "# overkill (added by overkill init)",
            )
            content = content.replace(
                "# review-loop (added by installer)",
                "# overkill (added by overkill init)",
            )
            gitignore.write_text(content, encoding="utf-8")
            return

        if marker in lines:
            return
        # Ensure trailing newline before appending
        if content and not content.endswith("\n"):
            content += "\n"
    else:
        content = ""

    content += f"# overkill (added by overkill init)\n{marker}\n"
    gitignore.write_text(content, encoding="utf-8")


def _write_manifest(dest: Path, entries: list[str]) -> None:
    """Write ``.install-manifest`` listing tool-owned files."""
    manifest_path = dest / ".install-manifest"
    manifest_path.write_text(
        "\n".join(sorted(entries)) + "\n",
        encoding="utf-8",
    )


def _migrate_legacy_dir(target_dir: Path) -> None:
    """Migrate ``.review-loop/`` to ``.overkill/`` if present."""
    legacy = target_dir / _LEGACY_DIR
    dest = target_dir / _OVERKILL_DIR

    if not legacy.is_dir() or dest.is_dir():
        return

    shutil.move(str(legacy), str(dest))
    logger.info("Migrated %s/ → %s/", _LEGACY_DIR, _OVERKILL_DIR)

    # Rename legacy RC file inside the migrated directory
    old_rc = dest / ".reviewlooprc"
    new_rc = dest / ".overkillrc"
    if old_rc.is_file() and not new_rc.is_file():
        old_rc.rename(new_rc)
        logger.info("Renamed .reviewlooprc → .overkillrc")

    print(f"Migrated {_LEGACY_DIR}/ → {_OVERKILL_DIR}/")


def init_project(target_dir: Path) -> None:
    """Initialize ``.overkill/`` in the target project.

    Idempotent: safe to re-run.  Prompt templates are always refreshed.
    RC config files are only created if missing (preserving user edits).
    """
    # Migrate legacy .review-loop/ if present
    _migrate_legacy_dir(target_dir)

    with _data_root() as data:
        dest = target_dir / _OVERKILL_DIR
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
    ok = _OVERKILL_DIR
    print(f"Initialized {dest}/")
    print(f"  {ok}/prompts/active/  {n_prompts} templates")
    print(f"  {ok}/.overkillrc")
    print(f"  {ok}/.refactorsuggestrc")
    print(f"  {ok}/logs/")
