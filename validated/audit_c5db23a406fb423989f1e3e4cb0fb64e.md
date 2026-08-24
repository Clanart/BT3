### Title
Missing `--` separator allows ref/argv-boundary confusion in `resetModeToArgs` - (File: app/src/lib/git/reset.ts)

### Summary
`resetModeToArgs` builds the argv for `git reset` by concatenating the mode flag (`--hard`/`--soft`) directly with the caller-supplied `ref` string, with no `--` separator to force git to treat the following token as a revision rather than an option.

### Finding Description
`resetModeToArgs` constructs argv as `['reset', '--hard', ref]` (or `--soft`/plain `reset`) and passes it straight to `git()`: [1](#0-0) 
`reset()` calls this helper directly with the caller-provided `ref` and forwards the resulting argv to the `git` child-process spawn wrapper without any option/argument separation: [2](#0-1) 

Notably, the sibling function `resetPaths` in the same file *does* insert a `--` separator before the path list (`[...baseArgs, '--', ...paths]`), which shows the codebase is aware of this argv-injection risk pattern elsewhere but does not apply the same protection to the `ref` argument in `resetModeToArgs`/`reset`: [3](#0-2) 

If `ref` were a string beginning with `-` (e.g. `--upload-pack=/tmp/evil`), it would be parsed by git as an option token immediately following `--hard`, rather than as the literal revision, because there is no `--` boundary.

### Impact Explanation
If reached with attacker-influenced content, this pattern could let git interpret an option string (e.g., `--upload-pack=...`, or other reset/config-affecting flags) instead of a revision, potentially causing repo-write or configuration-injection side effects during a reset operation.

### Likelihood Explanation
This is difficult to confirm as practically exploitable from the code I was able to inspect. The identified callers of `reset(repository, GitResetMode..., ref)` are in `app/src/lib/stores/app-store.ts` and `app/src/lib/stores/git-store.ts`, but I was not able to fully verify, within the available tool calls, whether the `ref` values passed at those call sites are free-form attacker-controlled strings or are restricted to commit SHAs (which git always renders as lowercase hex and can never begin with `-`). Git also enforces `check-ref-format`-style restrictions on ref/branch names for locally created and fetched refs, which generally reject refnames starting with `-`, further reducing the likelihood that a remote could smuggle a dash-prefixed value into `ref` via a branch/tag name. Without confirming a concrete attacker-controlled call site that passes an unsanitized, non-SHA string into `reset()`, I cannot assert this is currently reachable/exploitable end-to-end — the vulnerable *pattern* is real in `resetModeToArgs`, but the full attack path (from an attacker-controlled entrypoint to this sink with a dash-prefixed value) is unconfirmed.

### Recommendation
Regardless of current reachability, harden `resetModeToArgs`/`reset` defensively by inserting a `--` separator before `ref`, mirroring the existing pattern already used in `resetPaths` for its `paths` argument, e.g. `['reset', '--hard', '--', ref]`.

### Proof of Concept
Not independently verified end-to-end due to incomplete confirmation of caller-supplied `ref` values; the structural issue can be demonstrated by unit-testing `resetModeToArgs(GitResetMode.Hard, '--upload-pack=/tmp/evil')` and observing the produced argv `['reset', '--hard', '--upload-pack=/tmp/evil']`, which git will parse as an option rather than a literal revision, since no attacker-controlled call site passing such a value has been confirmed in the reviewed callers (`app-store.ts`, `git-store.ts`).

### Citations

**File:** app/src/lib/git/reset.ts (L27-38)
```typescript
function resetModeToArgs(mode: GitResetMode, ref: string): string[] {
  switch (mode) {
    case GitResetMode.Hard:
      return ['reset', '--hard', ref]
    case GitResetMode.Mixed:
      return ['reset', ref]
    case GitResetMode.Soft:
      return ['reset', '--soft', ref]
    default:
      return assertNever(mode, `Unknown reset mode: ${mode}`)
  }
}
```

**File:** app/src/lib/git/reset.ts (L41-49)
```typescript
export async function reset(
  repository: Repository,
  mode: GitResetMode,
  ref: string
): Promise<true> {
  const args = resetModeToArgs(mode, ref)
  await git(args, repository.path, 'reset')
  return true
}
```

**File:** app/src/lib/git/reset.ts (L91-94)
```typescript
  } else {
    const args = [...baseArgs, '--', ...paths]
    await git(args, repository.path, 'resetPaths')
  }
```
