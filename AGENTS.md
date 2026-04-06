## Project Identity

- **Repo**: `modocai/mr-overkill` (GitHub)
- **PyPI package name**: `overkill` (pending publisher registered)
- **Python import**: `mr_overkill` (source in `src/mr_overkill/`)
- **CLI entrypoint**: `overkill` (subcommands: `review-loop`, `refactor-suggest`, `init`, `check-budget`)
- **Version**: managed in `pyproject.toml` → `[project] version`
- **CI/CD**: GitHub Actions — `publish.yml` triggers PyPI publish

## Pull Request Rules

Every PR must pass the review loop (`overkill review-loop --dry-run`) before merging. No exceptions. We eat our own dog food — if Mr. Overkill can't approve it, neither can you.

## Branch Rules

Always commit and push before ending work on any branch other than develop.
Never commit directly to `main` or `develop`. All changes must go through branch → PR → review before merge.
Never rebase or force-push `main` or `develop` — this destroys shared history.

## PR Merge Process

Before merging a PR, always fetch the target branch and check for new commits:

```
git fetch origin <target-branch>
git log HEAD..origin/<target-branch> --oneline
```

**A) Target branch has new commits:**
1. Merge the target branch into your feature branch (`git merge origin/<target-branch>`)
2. Resolve conflicts if any
3. Push the merge commit
4. Re-run the review loop — the merged code must pass review again

**B) Target branch is up to date:**
1. Merge the PR:
   - Feature branch → target: `gh pr merge --merge --delete-branch`
   - `develop` → `main`: `gh pr merge --merge` (**never** `--delete-branch` — `develop` is a long-lived branch)
2. Switch to the target branch, pull, and delete the local feature branch (if applicable)

## Release Process

1. **Feature PR → develop**
   - Create a feature branch, make changes, push
   - Run `overkill review-loop --dry-run -n 3` — must pass
   - Merge PR into `develop` (`gh pr merge --merge --delete-branch`)

2. **Release branch → main**
   - Create `release/x.y.z` from `develop`
   - Bump version in `pyproject.toml` (`[project] version`)
   - Commit: `chore: bump version to x.y.z`
   - Push and open PR targeting `main`
   - Run review loop — if the review produces fix commits, these must also be merged back into `develop` (step 4 handles this)
   - Merge PR (`gh pr merge --merge --delete-branch`)

3. **Publish to PyPI**
   - Create a GitHub Release: `gh release create vx.y.z --target main --generate-notes`
   - This triggers `publish.yml` → automatic PyPI publish

4. **Sync develop**
   - `git checkout develop && git pull origin develop`
   - `git merge origin/main && git push origin develop`

## Commit Messages

Principles:
- Write the subject in English, capturing the motivation/context of the change
- Keep conventional commit prefixes (fix, feat, refactor, etc.)
- Add a detailed body after a blank line if needed
