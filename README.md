# :tophat: Mr. Overkill

> **"Refactoring is not a task. It's a lifestyle."**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT) [![Token Cost](https://img.shields.io/badge/Token%20Cost-Bankrupt-red)](#) [![Efficiency](https://img.shields.io/badge/Efficiency-0%25-orange)](#) [![Over-Engineering](https://img.shields.io/badge/Over--Engineering-Max-blueviolet)](#)

**Mr. Overkill** is an automated loop that forces **Codex** (the pedantic reviewer) and **Claude** (the tired developer) into a locked room. They will not stop refactoring your code until it is "perfectly over-engineered" or your API credit runs out.

## :warning: WARNING: FINANCIAL HAZARD

**Do not run this script if you value your money.**

This tool is designed to:

1. :fire: **Burn Tokens:** It ignores "good enough" and strives for "unnecessarily complex."
2. :money_with_wings: **Drain Wallets:** Requires OpenAI (Paid) AND Anthropic (Pro/Max) simultaneously.
3. :infinity: **Loop Forever:** It might turn your "Hello World" into a Microservices Architecture.

---

## Quick Install (If you dare)

```bash
# Install into your project
git clone --depth 1 https://github.com/modocai/mr-overkill.git /tmp/mr-overkill \
  && /tmp/mr-overkill/install.sh /path/to/your-project \
  && rm -rf /tmp/mr-overkill
```

Or clone and install manually:

```bash
git clone https://github.com/modocai/mr-overkill.git
./mr-overkill/install.sh /path/to/your-project
```

## :hammer_and_wrench: Prerequisites (The "Rich Dev" Starter Pack)

You need these to participate in the madness:

**Accounts** (yes, you need both — that's the point):

- [OpenAI](https://platform.openai.com/) account (paid plan) — because free tier is for weak code
- [Anthropic](https://console.anthropic.com/) account (Pro/Max plan or API credits) — because Claude needs to think *deeply* about your variable names

**Runtime**:

- [Node.js](https://nodejs.org/) v18+ — Codex and Claude Code CLI are npm packages, so yes, you need this
- A fast credit card — essential

**CLI Tools**:

```bash
npm install -g @openai/codex        # Codex CLI
npm install -g @anthropic-ai/claude-code  # Claude Code CLI
```

- [jq](https://jqlang.github.io/jq/) — JSON processor
- [gh](https://cli.github.com/) — GitHub CLI (optional, for PR comments)
- [envsubst](https://www.gnu.org/software/gettext/) — part of GNU gettext (macOS: `brew install gettext`)
- [perl](https://www.perl.org/) — used for JSON extraction and deduplication (pre-installed on macOS and most Linux)
- git

## Quick Start

```bash
# In your project directory (after install):
.review-loop/bin/review-loop.sh -n 3
```

## Usage

```
review-loop.sh [OPTIONS]

Options:
  -t, --target <branch>    Target branch to diff against (default: develop)
  -n, --max-loop <N>       Maximum review-fix iterations (required)
  --max-subloop <N>        Maximum self-review sub-iterations per fix (default: 2)
  --no-self-review         Disable self-review (equivalent to --max-subloop 0)
  --dry-run                Run review only, do not fix
  --no-auto-commit         Fix but do not commit/push (single iteration)
  -V, --version            Show version
  -h, --help               Show this help message

Examples:
  review-loop.sh -t main -n 3          # diff against main, max 3 loops
  review-loop.sh -n 5                  # diff against develop, max 5 loops
  review-loop.sh -n 1 --dry-run        # single review, no fixes
  review-loop.sh -n 3 --no-self-review # disable self-review sub-loop
  review-loop.sh --version             # print version
```

## Configuration (.reviewlooprc)

Create a `.reviewlooprc` file in your project root to set defaults. CLI arguments always take precedence.

```bash
# .reviewlooprc
TARGET_BRANCH="main"
MAX_LOOP=5
MAX_SUBLOOP=2
AUTO_COMMIT=true
PROMPTS_DIR="./custom-prompts"
```

See `.review-loop/.reviewlooprc.example` for all available options.

## How It Works

```
1. Check prerequisites (git, codex, claude, jq, envsubst, target branch)
2. Create .review-loop/logs/ directory
3. Loop (iteration 1..N):
   a. Generate diff: git diff $TARGET...$CURRENT
   b. Empty diff → exit
   c. Codex reviews the diff → JSON with findings
   d. No findings + "patch is correct" → exit
   e. Claude fixes all issues (P0-P3)
   f. Sub-loop (1..MAX_SUBLOOP):
      - Claude self-reviews the uncommitted fixes (git diff)
      - If clean → break
      - Claude re-fixes based on self-review findings
   g. Auto-commit all fixes + re-fixes to branch
   h. Push to remote (updates PR)
   i. Post review/fix/self-review summary as PR comment
   j. Next iteration reviews the updated committed state
4. Write summary to .review-loop/logs/summary.md
```

## Output Files

All logs are saved to `.review-loop/logs/` (git-ignored by default):

| File | Description |
|------|-------------|
| `review-N.json` | Codex review output for iteration N |
| `opinion-N.md` | Claude's opinion on review findings (iteration N) |
| `fix-N.md` | Claude fix log for iteration N |
| `self-review-N-M.json` | Claude self-review output (iteration N, sub-iteration M) |
| `refix-opinion-N-M.md` | Claude's opinion on self-review findings (iteration N, sub M) |
| `refix-N-M.md` | Claude re-fix log (iteration N, sub-iteration M) |
| `summary.md` | Final summary with status and per-iteration results |

## Customizing Prompts

Edit the templates in `.review-loop/prompts/active/` (or `prompts/active/` in the source repo):

- **`codex-review.prompt.md`** — Review prompt sent to Codex. Uses `envsubst` variables: `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.
- **`claude-fix.prompt.md`** — Opinion prompt: Claude evaluates review findings. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`.
- **`claude-fix-execute.prompt.md`** — Execute prompt: tells Claude to fix based on its opinion.
- **`claude-self-review.prompt.md`** — Self-review prompt for Claude to check its own fixes. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.

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

This removes the `.review-loop/` directory and its `.gitignore` entry.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit your changes
4. Open a Pull Request against `develop`

## License

[MIT](LICENSE) &copy; 2026 ModocAI
