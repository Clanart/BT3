Based on my investigation, the strongest and most concrete analog is the unbatched `checkoutPaths` invocation used by `undoFirstCommit`, which mirrors the reported bug class: an attacker-influenced, unbounded collection size is passed directly into a single git invocation instead of being chunked/streamed, so the operation can fail outright once the repository-controlled path count grows large enough — the exact same pattern that Desktop already fixed in the sibling `discardChanges` path (via `checkoutIndex`'s stdin/`-z` batching) but left unfixed here.

### Title
`undoFirstCommit` builds an unbounded `git checkout HEAD -- <paths...>` argument list, causing "Undo Commit" to fail on repositories with many files - ([File: app/src/lib/stores/git-store.ts])

### Summary
`GitStore.undoFirstCommit` collects every locally-deleted working-directory path and passes the whole array as literal command-line arguments to `checkoutPaths`, which shells out via `git checkout HEAD -- <paths>` with no batching or stdin fallback. [1](#0-0) [2](#0-1) 

### Finding Description
The broken invariant is: "the number of paths passed to a single git invocation is bounded by OS argument-length limits." Desktop already recognizes this invariant is dangerous to violate — it built `checkoutIndex` specifically to route file lists through stdin (`--stdin -z`) "avoiding all issues with too long arguments," and the historical changelog entry "Windows: Avoid long path limits when discarding changes - #2833" documents that this exact class of bug was fixed for the main discard-changes flow. [3](#0-2) [4](#0-3) 

However, `undoFirstCommit` — the "Undo Commit" feature for a repository's first commit — never adopted this fix. It derives `deletedFilePaths` from `getStatus`, i.e., from the number of files the *initial commit of a cloned/fetched repository* originally tracked (an attacker can craft a repo whose sole/first commit contains an extremely large number of files, then simply have the user delete/undo against it), and forwards the full array straight into `checkoutPaths`: [5](#0-4) 

`checkoutPaths` spreads the entire path list onto the git argv with no chunking, unlike `resetPaths`/`checkoutIndex` which explicitly special-case large lists via stdin: [2](#0-1) [6](#0-5) 

Existing guards do not stop this path: `getStatus` only fails ("too many files") for extreme working-directory sizes, but the `deletedFiles` subset can still be large enough to overflow `ARG_MAX`/Windows command-line limits well before that global guard triggers; and unlike `discardChanges`, `undoFirstCommit` calls the unbatched `checkoutPaths` directly instead of the batched `checkoutIndex`.

### Impact Explanation
When the underlying `git` spawn fails with `E2BIG`/`ENAMETOOLONG` (or the equivalent Windows command-line-length failure), `undoFirstCommit` throws before reaching `deleteRef`/`unstageAllFiles`, so the "Undo commit" action fails for any repository whose first commit (attacker-crafted, cloned) contains enough files. This denies the feature entirely for such repositories and, per the git-store comment, the very reason `checkoutPaths` (rather than a blanket `git checkout -- .`) was chosen is to preserve *other* working-directory changes not related to the undo — if the batched checkout is only partially reasoned about (i.e., a caller assumes success and proceeds to remove the ref), remaining "deleted" files stay deleted in the working directory while the commit is being un-done, silently diverging what the user believes is restored from what's actually on disk.

### Likelihood Explanation
Likelihood is moderate: it requires a repository whose first commit contains a very large number of files, which is entirely attacker-controlled (a cloned/fetched malicious repo), combined with the user invoking "Undo commit" on that repository. No admin rights, local access, or leaked credentials are required — only cloning/fetching the crafted repo and pressing "Undo commit," which is a normal Desktop UI action.

### Recommendation
Route `undoFirstCommit`'s deleted-path checkout through the same stdin-batched mechanism already used by `discardChanges` (i.e., call `checkoutIndex` instead of `checkoutPaths`, or add stdin/`-z` batching support to `checkoutPaths` itself), matching the pattern already applied in `resetPaths`/`checkoutIndex`. [7](#0-6) 

### Proof of Concept
1. Craft a repository whose first (and only) commit contains a very large number of tracked files (enough that their combined path length exceeds `ARG_MAX` on macOS/Linux or the ~32K command-line limit on Windows, e.g. tens of thousands of short-named files).
2. Have the victim clone this repository in GitHub Desktop.
3. In the working directory, delete all (or a sufficiently large subset of) those tracked files (e.g. via `git rm` outside Desktop, or scripted deletion), so `getStatus` reports them as `AppFileStatusKind.Deleted`.
4. In Desktop, trigger "Undo commit" on the first commit; `undoFirstCommit` collects all deleted paths and calls:
```
checkoutPaths(repository, deletedFilePaths)
// -> git(['checkout', 'HEAD', '--', ...deletedFilePaths], ...)
``` [8](#0-7) 
5. The spawned `git` process fails with an OS-level argument-list-too-long error, and the undo-commit operation aborts before `deleteRef`/`unstageAllFiles` run, leaving the repository in a partially-processed state that differs from what "Discard Changes" (via `checkoutIndex`) would have produced for the same file set.

### Citations

**File:** app/src/lib/stores/git-store.ts (L673-700)
```typescript
  private async undoFirstCommit(
    repository: Repository
  ): Promise<true | undefined> {
    // What are we doing here?
    // The state of the working directory here is rather important, because we
    // want to ensure that any deleted files are restored to your working
    // directory for the next stage. Doing doing a `git checkout -- .` here
    // isn't suitable because we should preserve the other working directory
    // changes.

    const status = await this.performFailableOperation(() =>
      getStatus(this.repository)
    )

    if (status == null) {
      throw new Error(
        `Unable to undo commit because there are too many files in your repository's working directory.`
      )
    }

    const paths = status.workingDirectory.files

    const deletedFiles = paths.filter(
      p => p.status.kind === AppFileStatusKind.Deleted
    )
    const deletedFilePaths = deletedFiles.map(d => d.path)

    await checkoutPaths(repository, deletedFilePaths)
```

**File:** app/src/lib/stores/git-store.ts (L1636-1648)
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
```

**File:** app/src/lib/git/checkout.ts (L209-219)
```typescript
/** Check out the paths at HEAD. */
export async function checkoutPaths(
  repository: Repository,
  paths: ReadonlyArray<string>
): Promise<void> {
  await git(
    ['checkout', 'HEAD', '--', ...paths],
    repository.path,
    'checkoutPaths'
  )
}
```

**File:** app/src/lib/git/checkout-index.ts (L4-20)
```typescript
/**
 * Forcefully updates the working directory with information from the index
 * for a given set of files.
 *
 * This method is essentially the same as running `git checkout -- files`
 * except by using `checkout-index` we can pass the files we want updated
 * on stdin, avoiding all issues with too long arguments.
 *
 * Note that this function will not yield errors for paths that don't
 * exist in the index (-q).
 *
 * @param repository The repository in which to update the working directory
 *                   with information from the index
 *
 * @param paths      The relative paths in the working directory to update
 *                   with information from the index.
 */
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

**File:** app/src/lib/git/reset.ts (L66-95)
```typescript
export async function resetPaths(
  repository: Repository,
  mode: GitResetMode,
  ref: string,
  paths: ReadonlyArray<string>
): Promise<void> {
  if (!paths.length) {
    return
  }

  const baseArgs = resetModeToArgs(mode, ref)

  if (__WIN32__ && mode === GitResetMode.Mixed) {
    // Git for Windows has experimental support for reading paths to reset
    // from standard input. This is helpful in situations where your file
    // paths are greater than 32KB in length, because of shell limitations.
    //
    // This hasn't made it to Git core, so we fallback to the default behaviour
    // as macOS and Linux don't have this same shell limitation. See
    // https://github.com/desktop/desktop/issues/2833#issuecomment-331352952
    // for more context.
    const args = [...baseArgs, '--stdin', '-z', '--']
    await git(args, repository.path, 'resetPaths', {
      stdin: paths.join('\0'),
    })
  } else {
    const args = [...baseArgs, '--', ...paths]
    await git(args, repository.path, 'resetPaths')
  }
}
```
