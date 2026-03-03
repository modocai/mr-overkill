"""Entry point for ``python -m mr_overkill``."""

from __future__ import annotations

import logging
import sys


def main() -> None:
    """Dispatch subcommands."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) >= 2 and sys.argv[1] in ("-V", "--version"):
        from mr_overkill import __version__

        print(f"overkill {__version__}")
        sys.exit(0)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(
            "Usage: overkill <command>\n\n"
            "Commands:\n"
            "  init              Initialize .review-loop/ in a project\n"
            "  review-loop       Run AI review-fix loop\n"
            "  refactor-suggest  Run AI refactoring suggestions",
            file=sys.stderr,
        )
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    command = sys.argv[1]
    sys.argv = sys.argv[1:]  # Shift so argparse sees correct prog name

    if command == "init":
        import argparse
        from pathlib import Path

        from mr_overkill.init import init_project

        p = argparse.ArgumentParser(
            prog="overkill init",
            description="Initialize .review-loop/ in a project",
        )
        p.add_argument("target", nargs="?", default=".", help="Target directory")
        init_args = p.parse_args()
        init_project(Path(init_args.target).resolve())
        sys.exit(0)
    elif command == "review-loop":
        from mr_overkill.cli import parse_review_loop_args
        from mr_overkill.review_loop import run

        config = parse_review_loop_args()
        sys.exit(run(config))
    elif command == "refactor-suggest":
        from mr_overkill.cli import parse_refactor_suggest_args
        from mr_overkill.refactor_suggest import run as refactor_run

        config, extra = parse_refactor_suggest_args()
        exit_code = refactor_run(
            config, config.scope or "auto", create_pr=extra.create_pr,
        )
        if extra.with_review and exit_code == 0 and not config.dry_run:
            import subprocess

            ahead = subprocess.run(
                ["git", "rev-list", "--count",
                 f"{config.target_branch}..{config.current_branch}"],
                capture_output=True, text=True, check=False,
            )
            if ahead.returncode != 0:
                logging.getLogger(__name__).error(
                    "Failed to count commits: %s", ahead.stderr.strip(),
                )
                sys.exit(1)
            if int(ahead.stdout.strip() or "0") == 0:
                logging.getLogger(__name__).info(
                    "No refactor commits — skipping chained review-loop.",
                )
                sys.exit(exit_code)

            from mr_overkill.cli import parse_review_loop_args
            from mr_overkill.review_loop import run as review_run

            review_config = parse_review_loop_args([
                "-t", config.target_branch,
                "-n", str(extra.review_loops),
            ])
            exit_code = review_run(review_config)
        sys.exit(exit_code)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
