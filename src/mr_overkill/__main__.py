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

    if len(sys.argv) < 2:
        print(
            "Usage: python -m mr_overkill <command>\n\n"
            "Commands:\n"
            "  review-loop       Run AI review-fix loop\n"
            "  refactor-suggest  Run AI refactoring suggestions",
            file=sys.stderr,
        )
        sys.exit(1)

    command = sys.argv[1]
    sys.argv = sys.argv[1:]  # Shift so argparse sees correct prog name

    if command == "review-loop":
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
        if extra.with_review and exit_code == 0:
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
