# Gemini review guidance and benchmark

Follow-up to #171, separate from parallel reviewers (#170).

## Contract

Review and refactoring analysis use the existing role-aware `backend_command`:
Gemini runs `--sandbox --approval-mode plan`, AGY runs `--sandbox --mode plan`.
Only the fixer execution phase requests an explicit editing mode. Neither reviewer
silently falls back to `yolo` / `accept-edits` when a tool is denied.

Gemini CLI 0.60.0 plan mode can remove the shell tool entirely. Overkill therefore
captures branch diff evidence itself using Git with external diff and textconv
helpers disabled. Commit and no-commit WIP reviews receive their captured scope
artifact instead; later commit-review iterations also receive the fixer branch
diff. Failure to obtain evidence aborts review before calling the model. Current
source files remain available through the CLI's file-reading tools.

The guidance asks the reviewer to establish intent, trace affected callers and
contracts, test candidate findings against concrete execution paths, and combine
repeated root causes. It does not replace Overkill's confidence filter, JSON
schema, P0-P3 priorities, target branch, or commit/WIP scope overrides. Refactor
prompts retain their own source-list and micro/module/layer/full boundaries.

No installed slash command is invoked. That would make behavior depend on user
plugins, and the inspected commands assume `origin/HEAD`, Markdown output, and a
different severity scale. Existing customized prompt copies are not modified by
this code change. `overkill init` refreshes them and overwrites prompt templates,
so preserve intentional customizations before using it.

## Local headless permission probes

Tested on 2026-09-20 in disposable repositories, never the working project:

- Gemini CLI 0.60.0: `--sandbox --approval-mode plan --output-format text` read a
  sentinel file. Explicit attempts to overwrite it and create another source
  file were denied by the plan-path validator. The sentinel remained unchanged;
  no Git index change occurred. The model reported the shell tool unavailable.
- The fixture needed `GEMINI_CLI_TRUST_WORKSPACE=true` for this child process only.
  Without that setting, Gemini refused the untrusted temporary workspace before
  review. Overkill itself does not override the user's workspace trust.
- AGY 1.2.7: `--sandbox --mode plan --output-format text --print-timeout 120s`
  returned no review text and timed out after two minutes. No fixture changes
  were observed. This is **not** evidence of a successful AGY review or proof of
  AGY write prevention. The existing empty-output failure handling is retained.

These are CLI permission policies, not an OS-level immutability guarantee.
Gemini may write its own designated plan directory and session/cache files;
installed/user policies and future CLI versions can affect behavior. Prompt
instructions are a second layer, not the permission boundary. No claim is made
that arbitrary hostile plugins or user permission overrides are contained.

## Provenance and deliberately omitted behavior

The installed Gemini extension is `code-review` 0.1.0, upstream commit
`a2fb26fd3218fb12400fc1a8a5191cddc89ffd1c`:
[command source](https://github.com/gemini-cli-extensions/code-review/blob/a2fb26fd3218fb12400fc1a8a5191cddc89ffd1c/commands/code-review.toml),
[Apache-2.0 license](https://github.com/gemini-cli-extensions/code-review/blob/a2fb26fd3218fb12400fc1a8a5191cddc89ffd1c/LICENSE).
The local AGY plugin's `skills/code-review/SKILL.md` contains the same review
structure; its minimal manifest identifies only `code-review`, not an upstream
revision or separate license. No AGY asset is redistributed.

The new instructions are independently written Overkill guidance informed by
review heuristics, not copied upstream prompt text. No upstream persona, examples,
output template, severity taxonomy, or slash-command implementation is bundled.
Direct import was rejected: it would add Apache notice obligations and, more
importantly, violate Overkill's target, scoped-review and machine-output contracts.
Upstream's restriction to changed lines also cannot override historical commit
review's requirement to cite current locations. Its broad style findings and
blanket high severity for correctness bugs do not replace our high-signal filter.

Relevant provider references:

- [Gemini Plan Mode](https://geminicli.com/docs/cli/plan-mode/): read-only analysis,
  plan-directory exception, and policy customization.
- [Gemini policy engine](https://geminicli.com/docs/core/policy-engine/): configurable
  tool permission rules. Overkill does not broaden these with shell allowlists.
- [Gemini workspace trust](https://geminicli.com/docs/cli/trusted-folders/): headless
  trust must be established separately from tool permissions.
- [Antigravity modes](https://antigravity.google/docs/cli/modes/) and
  [headless execution](https://antigravity.google/docs/cli/headless/): mode and
  headless tool-permission settings are distinct. AGY plan mode alone must not be
  advertised as an enforced read-only boundary.

## Reproducing the benchmark

See [benchmark instructions](../benchmarks/README.md). All prompt variants run
under the same plan-mode command and receive the same captured scope evidence;
we do not revive unsafe historical `yolo` execution merely to compare prompts.
The pinned pre-171 prompt fixtures are Overkill's own templates, not Google assets.

This is a small deterministic-fixture smoke benchmark, not statistical proof of
superior review quality. Finding matches use expected locations and defect terms;
manual inspection is still needed for semantic equivalence. Empty clean reviews
have vacuous precision/recall, so read per-case results as well as aggregate rates.
A reported permission error in the sandbox case is a fixed fixture scenario, not
an assertion that every CLI attempted a forbidden test. Concurrent runs use
independent temporary repositories; the final Overkill PR review separately
exercises Gemini and Codex concurrently on the same branch.

Cost is `null` (unknown), not zero: text-mode CLI output does not provide reliable
billable token/cost accounting. Latency includes CLI startup and tool work.
Snapshots compare tracked/untracked file content and Git state in the fixture;
they are observational evidence, not a sandbox, and do not audit global CLI caches.

## Recorded smoke results

Run: 2026-09-20 America/New_York (2026-09-21 02:30 UTC), Gemini CLI 0.60.0,
provider-configured model (not pinned), three concurrent isolated fixtures,
240-second per-call limit. [Machine-readable aggregate](../benchmarks/results/gemini-171-2026-09-20.json)
includes template hashes and invocation metadata. Five cases per variant:

| Variant | Precision | Recall | FP | Parsed schema | Scope | Mean latency | Writes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 1.00 | 1.00 | 0 | 100% | 100% | 18.40s | 0 |
| Review-only | 1.00 | 1.00 | 0 | 100% | 100% | 15.28s | 0 |
| Adapted | 1.00 | 1.00 | 0 | 100% | 100% | 14.87s | 0 |

All 15 invocations exited successfully; no fixture file, ref, config, or logical
index changes were observed. Overkill's tolerant JSON extractor accepted every
response. A separate raw `json.loads` check accepted 4/5 baseline responses (one
used Markdown fences) and 5/5 for both other variants. Thus "parsed schema" is
not a claim of strict raw JSON compliance. All nine seeded-defect responses
matched their expected source locations; the six clean/permission-error cases
returned no findings. Cost remains unknown.

Conclusion: the adapted prompt retained detection and scope accuracy on this
small corpus; it did **not** demonstrate higher precision/recall than the other
variants. Latency differences are descriptive, not a reliable speedup claim.
Several conditional zero-division findings were over-prioritized as P0, including
by the adapted prompt: priority calibration remains a limitation, not a measured
success. The primary improvement is removal of review-time edit auto-approval
and preservation of scope/output contracts without provider slash commands.

Exploratory runs were excluded after fixture review found an unintended second
bug in a seeded case and a type-coercion regression in the supposed clean case.
The recorded run uses the corrected, regression-tested fixtures for every
variant. Raw audit artifacts are under `/tmp/overkill-171-verified-benchmark` on
the execution machine; rerunning the harness produces a fresh independent report.

## PR review corrections

The first Gemini+Codex Overkill review identified two transport/runtime issues:
AGY receives its prompt in argv, so large captured diffs now go to a per-review
`.diff` artifact referenced by the prompt, rather than into command-line arguments.
A >3 MB regression test checks this path. Gemini continues to receive evidence
on stdin. The live benchmark now explicitly requires POSIX (Linux/macOS/WSL),
rejecting native Windows before process creation because wrapper-only termination
cannot guarantee cleanup of children holding stdout/stderr pipes.
