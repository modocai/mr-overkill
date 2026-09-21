# Gemini Prompt Benchmark

Stdlib-only harness for issue #171. It creates tiny temporary Git repos, renders
Gemini review prompt variants, runs the Gemini CLI in bounded read-only plan mode,
and writes prompts, raw CLI output, snapshots, `results.json`, `report.json`, and
`report.md` to the selected output directory.

Variants:
- `baseline`: vendored pre-171 Gemini review prompt fixture.
- `review-only`: vendored prompt fixture with the review-only constraint.
- `adapted`: exact current production `prompts/active/gemini-review.prompt.md`.

Run all variants/cases:

```sh
uv run python -m benchmarks.gemini_prompt_benchmark \
  --output-dir /tmp/overkill-171-benchmark \
  --workers 3 \
  --timeout 240
```

Run a smaller slice:

```sh
uv run python -m benchmarks.gemini_prompt_benchmark \
  --variants adapted \
  --cases seeded-bug,wip-scope \
  --output-dir /tmp/overkill-171-slice
```

Notes:
- The harness sets `GEMINI_CLI_TRUST_WORKSPACE=true` only for the Gemini child process because its generated fixture repos are known-safe.
- Do not commit raw output directories; keep them under `/tmp` or another external archive path.
- The harness references captured diff evidence files for every variant so prompt comparisons do not depend on Gemini shell availability.

## Fixture Provenance and Limits

`fixtures/gemini-review-review-only.prompt.md` is the Overkill Gemini template from commit
`4dc4b45083a022a2daaf72fe316d6c74e6c68bc0` (MIT); the baseline removes only its
`Review-only constraint` section. These are not copied Google extension assets.
The adapted variant is loaded directly from the working production template.

All variants share the same read-only command and captured diff file, so this measures
prompt differences, not historical write-enabled execution. Seeded regressions
have existing callers/tests. The non-default target includes an `origin/HEAD`
decoy; WIP changes are deliberately uncommitted. The sandbox case contains a
synthetic recorded PermissionError so the scenario is reproducible everywhere.

Precision/recall use location overlap plus defect terms, not a semantic judge;
scope accuracy checks allowed files, not every changed line. Scores on empty
clean reviews are vacuous. Small samples do not establish statistical superiority.
Cost is unknown (`null`), not free. Raw prompts and outputs are kept in the output
directory for manual audit. Global CLI session/cache writes are outside the
fixture snapshot; this measurement is not a security sandbox.

Live CLI runs require POSIX process groups (Linux, macOS, or WSL). Native Windows
is rejected before spawning: terminating only the wrapper cannot reliably bound
children holding output pipes. Unit-only fixture/scoring checks do not launch a
provider. The harness kills the entire POSIX process group on timeout.
