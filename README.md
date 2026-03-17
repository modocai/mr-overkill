# :tophat: Mr. Overkill

> **"Refactoring is not a task. It's a lifestyle."**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT) [![Token Cost](https://img.shields.io/badge/Token%20Cost-Bankrupt-red)](#) [![Efficiency](https://img.shields.io/badge/Efficiency-0%25-orange)](#) [![Over-Engineering](https://img.shields.io/badge/Over--Engineering-Max-blueviolet)](#)

<img width="1024" height="1024" alt="image" src="https://github.com/user-attachments/assets/930a88d3-1224-47fc-bf5f-d9b367a8f29a" />


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
# Install the package
pip install overkill          # or: uv tool install overkill / pipx install overkill

# Initialize your project
overkill init /path/to/your-project
```

Or use the convenience script from the source repo:

```bash
git clone --depth 1 https://github.com/modocai/mr-overkill.git /tmp/overkill \
  && /tmp/overkill/install.sh /path/to/your-project \
  && rm -rf /tmp/overkill
```

## :hammer_and_wrench: Prerequisites (The "Rich Dev" Starter Pack)

You need these to participate in the madness:

**Accounts** (yes, you need both — that's the point):

- [OpenAI](https://platform.openai.com/) account (paid plan) — because free tier is for weak code
- [Anthropic](https://console.anthropic.com/) account (Pro/Max plan or API credits) — because Claude needs to think *deeply* about your variable names

**Runtime**:

- [Python](https://www.python.org/) 3.12+ — for the `overkill` CLI
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

# Review loop — review and fix diffs against target branch
overkill review-loop -n 3

# Refactor suggest — analyze full codebase for refactoring opportunities
overkill refactor-suggest -n 1 --dry-run
```

## Usage: overkill review-loop

```
overkill review-loop [OPTIONS]

Options:
  -t, --target <branch>    Target branch to diff against (default: develop)
  -n, --max-loop <N>       Maximum review-fix iterations (required, unless --resume)
  --max-subloop <N>        Maximum self-review sub-iterations per fix (default: 4)
  --no-self-review         Disable self-review (equivalent to --max-subloop 0)
  --dry-run                Run review only, do not fix
  --no-auto-commit         Fix but do not commit/push (single iteration)
  --resume                 Resume from a previously interrupted run (reuses existing logs)
  --reviewer-backend <be>  Reviewer backend: claude|codex (default: codex)
  --diagnostic-log         Save full Claude event stream to sidecar files

Examples:
  overkill review-loop -t main -n 3          # diff against main, max 3 loops
  overkill review-loop -n 5                  # diff against develop, max 5 loops
  overkill review-loop -n 1 --dry-run        # single review, no fixes
  overkill review-loop -n 3 --no-self-review # disable self-review sub-loop
  overkill review-loop --resume              # resume an interrupted run
  overkill review-loop -n 2 --reviewer-backend claude  # use Claude as reviewer
```

## Usage: overkill refactor-suggest

Unlike `review-loop` which reviews diffs, `refactor-suggest` analyzes the **entire codebase** for refactoring opportunities at a chosen scope level.

```
overkill refactor-suggest [OPTIONS]

Options:
  --scope <scope>          Refactoring scope: auto|micro|module|layer|full (default: auto)
  -t, --target <branch>    Target branch to base from (default: develop)
  -n, --max-loop <N>       Maximum analysis-fix iterations (default: 1)
  --max-subloop <N>        Maximum self-review sub-iterations per fix (default: 4)
  --no-self-review         Disable self-review (equivalent to --max-subloop 0)
  --dry-run                Run analysis only, do not apply fixes
  --no-dry-run             Force fixes even if .refactorsuggestrc sets DRY_RUN=true
  --auto-approve           Skip interactive confirmation for layer/full scope
  --create-pr              Create a draft PR after completing all iterations
  --resume                 Resume from a previously interrupted run (reuses existing logs)
  --with-review            Run review-loop after PR creation (default: 4 iterations)
  --with-review-loops <N>  Set review-loop iteration count (implies --with-review)
  --reviewer-backend <be>  Reviewer backend: claude|codex (default: codex)
  --diagnostic-log         Save full Claude event stream to sidecar files

Examples:
  overkill refactor-suggest -n 3                             # auto scope (budget-aware)
  overkill refactor-suggest --scope micro -n 3               # function/file-level fixes
  overkill refactor-suggest --scope module -n 2 --dry-run    # analyze module duplication
  overkill refactor-suggest --scope layer -n 1 --auto-approve  # cross-cutting concerns
  overkill refactor-suggest --scope full -n 1 --create-pr    # architecture redesign + PR
  overkill refactor-suggest -n 2 --with-review               # auto scope + auto review
  overkill refactor-suggest --scope module -n 3 --with-review-loops 6 # custom review
```

## Usage: overkill init

Initialize `.overkill/` in a project directory. Safe to re-run — prompts are refreshed, user-edited configs are preserved.

```
overkill init [TARGET_DIR]   # default: current directory
```

Creates:

```
.overkill/
├── prompts/active/          # 10 prompt templates
├── .overkillrc            # review-loop config
├── .refactorsuggestrc       # refactor-suggest config
├── logs/                    # runtime logs
│   └── refactor/            # refactor-suggest logs
└── .install-manifest        # tracks tool-owned files
```

### Scopes

| Scope | What it looks for | Blast radius |
|-------|-------------------|--------------|
| `auto` | Budget-aware automatic selection (default) | Varies — picks the highest scope your token budget allows |
| `micro` | Complex functions, dead code, in-file duplication | Low — single file |
| `module` | Cross-file duplication, module boundary issues | Low-medium — within a module |
| `layer` | Inconsistent error handling, logging, config patterns | Medium-high — across modules |
| `full` | Wrong abstractions, inverted dependencies, layer violations | High-critical — project-wide |

### How refactor-suggest works

```
1. Collect source file list (git ls-files)
2. Reviewer (Codex or Claude) analyzes the full codebase for scope-specific refactoring opportunities
3. (layer/full) Display refactoring plan and wait for confirmation
4. Claude applies refactoring (two-step: opinion → execute)
5. Claude self-reviews changes, re-fixes if needed
6. Auto-commit & push to refactoring branch
7. Repeat until clean or max iterations reached
8. (--create-pr) Create draft PR
9. (--with-review) Run review-loop on the new PR
```

Recommended workflow: start with `--dry-run` to review findings, then re-run without it to apply.

## Configuration

After running `overkill init`, config files live in `.overkill/`:

### .overkill/.overkillrc

```bash
TARGET_BRANCH="main"
MAX_LOOP=5
MAX_SUBLOOP=4
AUTO_COMMIT=true
REVIEWER_BACKEND="codex"    # or "claude"
PROMPTS_DIR="./custom-prompts"
```

See `.overkill/.overkillrc` for all available options.

### .overkill/.refactorsuggestrc

```bash
SCOPE="auto"
TARGET_BRANCH="develop"
MAX_LOOP=3
MAX_SUBLOOP=4
# DRY_RUN: safe default — remove to apply fixes (script default: false)
DRY_RUN=true
AUTO_APPROVE=false
CREATE_PR=false
WITH_REVIEW=false
REVIEW_LOOPS=4
REVIEWER_BACKEND="codex"    # or "claude"
PROMPTS_DIR="./custom-prompts"
```

## How review-loop works

```
1. Check prerequisites (git, codex, claude, jq, envsubst, target branch)
2. Create .overkill/logs/ directory
3. Loop (iteration 1..N):
   a. Generate diff: git diff $TARGET...$CURRENT
   b. Empty diff → exit
   c. Reviewer (Codex or Claude, via --reviewer-backend) reviews the diff → JSON with findings
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
4. Write summary to .overkill/logs/summary.md
```

## Output Files

All logs are git-ignored by default (inside `.overkill/`).

### review-loop logs (`.overkill/logs/`)

| File | Description |
|------|-------------|
| `review-N.json` | Codex review output for iteration N |
| `opinion-N.md` | Claude's opinion on review findings (iteration N) |
| `fix-N.md` | Claude fix log for iteration N |
| `self-review-N-M.json` | Claude self-review output (iteration N, sub-iteration M) |
| `refix-opinion-N-M.md` | Claude's opinion on self-review findings (iteration N, sub M) |
| `refix-N-M.md` | Claude re-fix log (iteration N, sub-iteration M) |
| `summary.md` | Final summary with status and per-iteration results |

### refactor-suggest logs (`.overkill/logs/refactor/`)

| File | Description |
|------|-------------|
| `source-files.txt` | List of files analyzed (from `git ls-files`) |
| `review-N.json` | Codex refactoring analysis for iteration N |
| `opinion-N.md` | Claude's opinion on refactoring findings (iteration N) |
| `fix-N.md` | Claude fix log for iteration N |
| `self-review-N-M.json` | Claude self-review (iteration N, sub-iteration M) |
| `refix-opinion-N-M.md` | Claude's opinion on self-review findings |
| `refix-N-M.md` | Claude re-fix log (iteration N, sub-iteration M) |
| `summary.md` | Final summary with scope, status, and per-iteration results |

## Token Budget Checker

The budget checker verifies Claude Code's 5-hour rate limit **before** starting expensive loops.

### How it estimates usage

| Mode | Data source | Accuracy |
|------|-------------|----------|
| **OAuth** (primary) | macOS Keychain → `security find-generic-password` → Anthropic OAuth API (`/oauth/usage`) | Exact — returns `five_hour.utilization` and `seven_day.utilization` directly from Anthropic |
| **Local** (fallback) | `~/.claude/projects/**/*.jsonl` session files — sums `input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens` from `message.usage` of assistant messages in the last 5 hours | Estimated — actual server-side limits are opaque; weekly usage (`seven_day_used_pct`) is unavailable (`null`) |

**Tier detection** reads `rateLimitTier` from `~/.claude/telemetry/*.json` (field `event_data.user_attributes`). Mapping: `default` → pro, `default_claude_max_5x` → max5, `default_claude_max_20x` → max20.

### Scope thresholds

Go/no-go decision based on current usage percentage:

| Scope | Go if used < | Typical use |
|-------|-------------|-------------|
| `micro` | 90% | Small single-file fix |
| `module` | 75% | Multi-file refactoring |
| `layer` | TBD | Cross-cutting changes |
| `full` | TBD | Full architecture review |

## Customizing Prompts

Edit the templates in `.overkill/prompts/active/`.

### review-loop prompts

- **`codex-review.prompt.md`** — Review prompt sent to Codex. Uses variables: `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.
- **`claude-review.prompt.md`** — Review prompt for Claude reviewer (symlink to codex-review by default).
- **`claude-fix.prompt.md`** — Opinion prompt: Claude evaluates review findings. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`.
- **`claude-fix-execute.prompt.md`** — Execute prompt: tells Claude to fix based on its opinion.
- **`claude-self-review.prompt.md`** — Self-review prompt for Claude to check its own fixes. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`, `${ITERATION}`.

### refactor-suggest prompts

Each scope has a dedicated prompt with scope-specific instructions, anti-pattern guardrails, and good/bad finding examples:

- **`codex-refactor-{micro,module,layer,full}.prompt.md`** — Codex reviewer prompts per scope.
- **`claude-refactor-{micro,module,layer,full}.prompt.md`** — Claude reviewer prompts (symlinks to codex versions by default).

All refactor prompts use variables: `${TARGET_BRANCH}`, `${ITERATION}`, `${SOURCE_FILES_PATH}`.

- **`claude-refactor-fix.prompt.md`** — Opinion prompt: Claude evaluates refactoring findings with scope-aware judgment. Uses: `${REVIEW_JSON}`, `${CURRENT_BRANCH}`, `${TARGET_BRANCH}`.
- **`claude-refactor-fix-execute.prompt.md`** — Execute prompt with safety guards (syntax check, scope overflow detection, regression testing).

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
# Quick — just nuke the directory
rm -rf .overkill

# Also remove the Python package
pip uninstall overkill       # or: uv tool uninstall overkill / pipx uninstall overkill
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit your changes
4. Open a Pull Request against `develop`
5. Run `overkill review-loop -n 3 --dry-run` on your PR branch — **required**. Let Mr. Overkill review your code before a human ever sees it.

## Development

```bash
git clone https://github.com/modocai/mr-overkill.git
cd mr-overkill
uv sync                      # install dev dependencies
uv run pytest                # run tests
uv run ruff check src/ tests/
uv run mypy src/
```

## Testing

```bash
# Python tests
uv run pytest --tb=short

# Bash integration tests (requires bats-core)
brew install bats-core       # one-time setup
bats test/                   # run all tests
```

## License

[MIT](LICENSE) &copy; 2026 ModocAI
