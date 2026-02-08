# Codex-Claude Review-Fix Loop

Codex(reviewer) and Claude(developer) collaborate to automatically improve code quality through an iterative review-fix loop.

## Prerequisites

- [codex](https://github.com/openai/codex) — OpenAI Codex CLI
- [claude](https://github.com/anthropics/claude-code) — Claude Code CLI
- [jq](https://jqlang.github.io/jq/) — JSON processor
- [gh](https://cli.github.com/) — GitHub CLI (for PR comments)
- [envsubst](https://www.gnu.org/software/gettext/) — part of GNU gettext
- git

## Quick Start

```bash
# In your project directory:
./bin/review-loop.sh -n 3
```

## Installation in Another Project

```bash
# Copy bin/ and templates/ into target project
/path/to/review_collaboration/install.sh /path/to/target-project

# Or use as git submodule
cd your-project
git submodule add <repo-url> review_collaboration
./review_collaboration/bin/review-loop.sh -n 3
```

## Usage

```
review-loop.sh [OPTIONS]

Options:
  -t, --target <branch>    Target branch to diff against (default: develop)
  -n, --max-loop <N>       Maximum review-fix iterations (required)
  --dry-run                Run review only, do not fix
  -h, --help               Show this help message

Examples:
  review-loop.sh -t main -n 3          # diff against main, max 3 loops
  review-loop.sh -n 5                  # diff against develop, max 5 loops
  review-loop.sh -n 1 --dry-run        # single review, no fixes
```

## How It Works

```
1. Check prerequisites (git, codex, claude, jq, envsubst, target branch)
2. Create .ai-review-logs/ directory
3. Loop (iteration 1..N):
   a. Generate diff: git diff $TARGET...$CURRENT
   b. Empty diff → exit
   c. Codex reviews the diff → JSON with findings
   d. No findings + "patch is correct" → exit
   e. Claude fixes all issues (P0-P3)
   f. Auto-commit fixes to branch
   g. Push to remote (updates PR)
   h. Post review findings + fix summary as PR comment
   i. Next iteration reviews the updated committed state
4. Write summary to .ai-review-logs/summary.md
```

## Output Files

All logs are saved to `.ai-review-logs/` (git-ignored by default):

| File | Description |
|------|-------------|
| `review-N.json` | Codex review output for iteration N |
| `fix-N.md` | Claude fix log for iteration N |
| `summary.md` | Final summary with status and per-iteration results |

## Customizing Prompts

Edit the templates in `templates/`:

- **`codex-review.prompt.md`** — Review prompt sent to Codex. Uses `envsubst` variables: `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.
- **`claude-fix.prompt.md`** — Fix prompt sent to Claude. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`.

## Priority Levels

| Level | Meaning | Action |
|-------|---------|--------|
| P0 | Blocking release | Fixed by Claude |
| P1 | Urgent | Fixed by Claude |
| P2 | Normal | Fixed by Claude |
| P3 | Low / nice-to-have | Fixed by Claude |

## Exit Conditions

The loop terminates when any of these occur:

- **all_clear** — No findings and overall verdict is "patch is correct"
- **no_diff** — No changes between branches
- **dry_run** — Review-only mode
- **max_iterations_reached** — Hit the `-n` limit
- **parse_error** — Could not parse Codex output as JSON
