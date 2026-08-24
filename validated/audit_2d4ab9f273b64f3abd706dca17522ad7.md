### Title
`checkoutIndex` treats all `git checkout-index` failures (exit code 1) as success, silently corrupting the "Discard Changes" result - ([File: app/src/lib/git/checkout-index.ts])

### Summary
`checkoutIndex()` calls `git checkout-index -f -u -q --stdin -z` with `successExitCodes: new Set([0, 1])`, meaning *any* non-zero-but-1 exit from `checkout-index` is treated as a successful operation and no error is ever surfaced to the caller. This is the same class of bug as the reported `EncryptedERC` issue: the code assumes an external, potentially-failing operation succeeded without actually verifying that the operation did what it was supposed to do, letting a partially-failed state be reported as fully successful. [1](#0-0) 

### Finding Description
`checkoutIndex` is the last step of the "Discard Changes" feature: after resetting the index (`resetPaths`) it force-overwrites the working tree with `checkout-index` so that discarded files revert to HEAD's content. [2](#0-1) [3](#0-2) 

The function's own doc-comment states the intent is narrow — only to suppress errors for "paths that don't exist in the index (-q)" — but the implementation goes further and accepts **any** exit code `1` from `checkout-index` as success: [4](#0-3) [5](#0-4) 

`git checkout-index` (per its own semantics) returns exit code `1` for a whole class of genuine per-file write failures — e.g. inability to create a file at the target path (permission errors, a path component that is a symlink, a directory occupying the file's path, etc.) — not just the "-q, missing-from-index" case the comment describes. Because Desktop's `git()` wrapper (`app/src/lib/git/core.ts`) only throws `GitError` when the exit code is outside `successExitCodes`, and here `1` is unconditionally whitelisted, `checkoutIndex` returns normally even when one or more of the requested files failed to actually be rewritten. [6](#0-5) 

The caller, `GitStore.discardChanges`, wraps this in `performFailableOperation` and, seeing no thrown error, considers the discard fully successful — the UI reports the working directory as clean and reverted. [7](#0-6)  There is no post-hoc verification (e.g. re-running `getStatus` and asserting the target files match HEAD) to confirm the checkout actually happened.

This differs from every other place in the codebase that whitelists a non-zero exit code — those cases whitelist a *specific, well-understood* code with a documented, singular meaning (e.g. `128` for "unborn HEAD" in `getIndexChanges`, or `2` for "conflict markers found" in `getFilesWithConflictMarkers`), and the code branches explicitly on that meaning. [8](#0-7) [9](#0-8)  `checkoutIndex` instead blanket-accepts `1` without inspecting *which* file(s) failed or why.

### Impact Explanation
If a repository the user clones/fetches contains content that causes a per-file `checkout-index` write failure for a path the user later attempts to discard (for example a working-tree entry that git cannot overwrite due to a symlink/permission/path conflict introduced by the attacker's tree), Desktop will:
- Report "Discard Changes" as fully successful with no error dialog,
- Leave the actual (potentially attacker-influenced or user's uncommitted) file content in the working directory untouched,
- Leave the user believing their working directory now matches HEAD, when it does not.

This is a silent corruption of what the user believes about their repository's state, matching the "silent corruption of what the user commits or pushes" impact class — a user who continues working from the false belief that a file was discarded could unknowingly stage/commit/push content that should have been reverted, or fail to notice reintroduced malicious content persisting in their tree.

### Likelihood Explanation
Likelihood is constrained by needing a specific per-file checkout-index failure to be triggered organically (this requires unusual working-tree/index conditions rather than a simple content mismatch), so it is not a trivial one-click exploit. However, no local/admin access or credential compromise is required — the necessary trigger conditions originate purely from repository content and OS-level filesystem behavior during a normal `checkout-index` operation Desktop performs as part of its core "Discard Changes" workflow, which is a common, unprivileged user action.

### Recommendation
- Restrict the accepted "benign" exit code to the specific `-q`, "path missing from index" case rather than blanket-whitelisting all `exitCode === 1` results.
- After invoking `checkout-index`, inspect `stderr`/output to distinguish "missing from index" messages from genuine write failures, and re-throw/report a `GitError` for the latter.
- Optionally, verify post-condition (e.g. diff status of the target paths against HEAD) before reporting a successful discard to the user.

### Proof of Concept
1. Attacker crafts and publishes a git tree such that, when a victim clones it and edits a specific working-tree path, that path (or one of its parent directory components) ends up in a state where git cannot rewrite it in-place (e.g., due to a filesystem-level conflict).
2. Victim edits/modifies the file locally and opens GitHub Desktop's "Discard Changes" on it.
3. `GitStore.discardChanges` calls `resetPaths` then `checkoutIndex`, which internally runs `git checkout-index -f -u -q --stdin -z` and receives exit code `1` for that path. [5](#0-4) 
4. Because `successExitCodes` includes `1`, `git()` returns normally with no `GitError` thrown. [6](#0-5) 
5. Desktop's UI reports the discard as successful even though the working-tree file was never actually reverted to HEAD's content — the discrepancy is silent and undetected by the application.

### Citations

**File:** app/src/lib/git/checkout-index.ts (L12-13)
```typescript
 * Note that this function will not yield errors for paths that don't
 * exist in the index (-q).
```

**File:** app/src/lib/git/checkout-index.ts (L21-40)
```typescript
export async function checkoutIndex(
  repository: Repository,
  paths: ReadonlyArray<string>
) {
  if (!paths.length) {
    return
  }

  const options = {
    successExitCodes: new Set([0, 1]),
    stdin: paths.join('\0'),
  }

  await git(
    ['checkout-index', '-f', '-u', '-q', '--stdin', '-z'],
    repository.path,
    'checkoutIndex',
    options
  )
}
```

**File:** app/src/lib/stores/git-store.ts (L1636-1649)
```typescript
    await this.performFailableOperation(async () => {
      if (submodulePaths.length > 0) {
        await resetSubmodulePaths(this.repository, submodulePaths)
      }

      await resetPaths(
        this.repository,
        GitResetMode.Mixed,
        'HEAD',
        necessaryPathsToReset
      )
      await checkoutIndex(this.repository, necessaryPathsToCheckout)
    })
  }
```

**File:** docs/technical/discard-changes.md (L35-41)
```markdown
### Checkout Paths

The last step is to replace the modified files in the working directory with
whatever is currently in the index - this ensures that Desktop only replaces
files that the user has chosen to discard.

**Git CLI equivalent**: `git checkout-index -f -u -- [path]`
```

**File:** app/src/lib/git/core.ts (L322-353)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }

          const gitErrorDescription =
            gitError !== null
              ? getDescriptionForError(gitError, coerceToString(result.stderr))
              : null
          const gitResult = {
            ...result,
            gitError,
            gitErrorDescription,
            path,
          }

          let acceptableError = true
          if (gitError !== null && opts.expectedErrors) {
            acceptableError = opts.expectedErrors.has(gitError)
          }

          if ((gitError !== null && acceptableError) || acceptableExitCode) {
            return gitResult
          }
```

**File:** app/src/lib/git/diff-index.ts (L82-102)
```typescript
  const args = ['diff-index', '--cached', '--name-status', '--no-renames', '-z']

  let result = await git(
    [...args, 'HEAD', '--'],
    repository.path,
    'getIndexChanges',
    {
      successExitCodes: new Set([0, 128]),
    }
  )

  // 128 from diff-index either means that the path isn't a repository or (more
  // likely) that the repository HEAD is unborn. If HEAD is unborn we'll diff
  // the index against the null tree instead.
  if (result.exitCode === 128) {
    result = await git(
      [...args, NullTreeSHA],
      repository.path,
      'getIndexChanges'
    )
  }
```

**File:** app/src/lib/git/diff-check.ts (L9-17)
```typescript
export async function getFilesWithConflictMarkers(
  repositoryPath: string
): Promise<Map<string, number>> {
  const { stdout } = await git(
    ['diff', '--check'],
    repositoryPath,
    'getFilesWithConflictMarkers',
    { successExitCodes: new Set([0, 2]) }
  )
```
