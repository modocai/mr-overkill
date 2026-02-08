# Codex-Claude Review-Fix Loop

Codex(reviewer) and Claude(developer) collaborate to automatically improve code quality through an iterative review-fix loop.

## Quick Install

```bash
# Install into your project
git clone --depth 1 https://github.com/modocai/review_collaboration.git /tmp/review_collaboration \
  && /tmp/review_collaboration/install.sh /path/to/your-project \
  && rm -rf /tmp/review_collaboration
```

Or clone and install manually:

```bash
git clone https://github.com/modocai/review_collaboration.git
./review_collaboration/install.sh /path/to/your-project
```

## Prerequisites

**Accounts**:

- [OpenAI](https://platform.openai.com/) account (paid plan) — Codex CLI 사용에 필요
- [Anthropic](https://console.anthropic.com/) account (Pro/Max plan 또는 API credits) — Claude Code CLI 사용에 필요

**Runtime**:

- [Node.js](https://nodejs.org/) v18+ — Codex, Claude Code CLI 실행에 필요

**CLI Tools**:

```bash
npm install -g @openai/codex        # Codex CLI
npm install -g @anthropic-ai/claude-code  # Claude Code CLI
```

- [jq](https://jqlang.github.io/jq/) — JSON processor
- [gh](https://cli.github.com/) — GitHub CLI (optional, for PR comments)
- [envsubst](https://www.gnu.org/software/gettext/) — part of GNU gettext (macOS: `brew install gettext`)
- git

## Quick Start

```bash
# In your project directory:
./bin/review-loop.sh -n 3
```

## Usage

```
review-loop.sh [OPTIONS]

Options:
  -t, --target <branch>    Target branch to diff against (default: develop)
  -n, --max-loop <N>       Maximum review-fix iterations (required)
  --dry-run                Run review only, do not fix
  --no-auto-commit         Fix but do not commit/push (single iteration)
  -V, --version            Show version
  -h, --help               Show this help message

Examples:
  review-loop.sh -t main -n 3          # diff against main, max 3 loops
  review-loop.sh -n 5                  # diff against develop, max 5 loops
  review-loop.sh -n 1 --dry-run        # single review, no fixes
  review-loop.sh --version             # print version
```

## Configuration (.reviewlooprc)

Create a `.reviewlooprc` file in your project root to set defaults. CLI arguments always take precedence.

```bash
# .reviewlooprc
TARGET_BRANCH="main"
MAX_LOOP=5
AUTO_COMMIT=true
PROMPTS_DIR="./custom-prompts"
```

See `.reviewlooprc.example` for all available options.

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

Edit the templates in `prompts/active/`:

- **`codex-review.prompt.md`** — Review prompt sent to Codex. Uses `envsubst` variables: `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.
- **`claude-fix.prompt.md`** — Fix prompt sent to Claude. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`.

Reference prompts (read-only originals) are in `prompts/reference/`.

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
- **auto_commit_disabled** — `--no-auto-commit` or `AUTO_COMMIT=false`; fixes applied but not committed
- **parse_error** — Could not parse Codex output as JSON

## Uninstall

```bash
# Remove review-loop from a target project
./uninstall.sh /path/to/your-project
```

This removes `bin/review-loop.sh`, `prompts/active/`, `.reviewlooprc.example`, and the `.ai-review-logs/` entry from `.gitignore`.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit your changes
4. Open a Pull Request against `develop`

## License

[MIT](LICENSE) &copy; 2026 ModocAI
