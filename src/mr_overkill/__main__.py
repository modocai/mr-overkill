"""Entry point for ``python -m mr_overkill``."""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch subcommands."""
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
        print("refactor-suggest: not yet implemented", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
