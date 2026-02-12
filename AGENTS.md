## Branch Rules

Always commit and push before ending work on any branch other than develop.

## Commit Messages

Instead of mechanical listings, convey **why the change is needed**.

Bad examples:
- `fix(review-loop): eliminate temp file leak and unify error-code handling`
- `fix(review-loop): distinguish file-not-found from parse error, remove global pollution`

Good examples:
- `fix: resolve temp file leak on early exit`
- `fix: separate exit codes so Codex failure and JSON parse failure are distinguishable`
- `refactor: extract helper functions because main loop exceeded 400 lines`

Principles:
- Write the subject in English, capturing the motivation/context of the change
- Keep conventional commit prefixes (fix, feat, refactor, etc.)
- Add a detailed body after a blank line if needed
