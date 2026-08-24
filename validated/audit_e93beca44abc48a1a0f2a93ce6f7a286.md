## Finding



### Title
Broken cherry-pick-in-progress guard causes `continueCherryPick` to always proceed, risking silent commit of stale/unintended staged content - ([File: app/src/lib/git/cherry-pick.ts])

### Summary
`continueCherryPick` is meant to bail out with `CherryPickResult.UnableToStart` if the on-disk cherry-pick state (`CHERRY_PICK_HEAD`) has disappeared before Desktop calls `git cherry-pick --continue` (or the empty-commit fallback). The guard is written as `if (await !isCherryPickHeadFound(repository))`, which due to operator precedence evaluates `!isCherryPickHeadFound(repository)` (negating the `Promise` object itself, always `false`) before awaiting, so the `await` resolves to `false` and the branch is **never** taken. [1](#0-0) 

The same unawaited-negation pattern also exists earlier in `getCherryPickSnapshot`, where the "no cherry-pick in progress → return null" short-circuit is likewise dead code. [2](#0-1) [3](#0-2) 

### Finding Description
`isCherryPickHeadFound` returns a `Promise<boolean>` (it is awaited correctly elsewhere, e.g. `app-store.ts`/`status.ts` callers use `await isCherryPickHeadFound(...)`). In `continueCherryPick`, the check is:

```ts
if (await !isCherryPickHeadFound(repository)) {
  return CherryPickResult.UnableToStart
}
``` [1](#0-0) 

Because `!` binds tighter than `await`, this is parsed as `await (!isCherryPickHeadFound(repository))`. `isCherryPickHeadFound(repository)` returns a `Promise` object, and `!<object>` is always `false` in JavaScript regardless of the eventual resolved value. `await false` is `false`. The `if` body is therefore dead code — the function comment says "make sure cherry pick is still in progress to continue" but the check can never stop execution. [4](#0-3) 

As a result, `continueCherryPick` always falls through to stage files and run either `git commit --allow-empty` or `git cherry-pick --continue` against whatever the current index/working directory state is, even in scenarios where `CHERRY_PICK_HEAD` is already gone (e.g., the cherry-pick was aborted/finished by another process, a hook, or a race with a concurrent operation touching `.git`), instead of returning `UnableToStart` as the code intends. [5](#0-4) 

This falls squarely into the same bug class as the report: **a safety check whose result is not actually observed/enforced**, so the code takes the path that should only be taken on success (a real, still-in-progress cherry-pick), regardless of the real state. There is no other check in `continueCherryPick` that re-validates the sequencer state before invoking `git commit --allow-empty` / `git cherry-pick --continue`, so nothing else "does not stop the path."

### Impact Explanation
Impact is a silent-corruption-of-commit-history class issue: Desktop can call `git commit --allow-empty` or `git cherry-pick --continue` in a state where the intended precondition (cherry-pick actively in progress) is false, producing commits/history changes the user did not actually authorize through the expected flow, without ever surfacing the intended `UnableToStart` failure to the UI. This matches the "silent corruption of what the user commits or pushes" impact category. It does not grant code execution or credential exfiltration, so severity is bounded to unintended/incorrect commit content rather than a sandbox escape.

### Likelihood Explanation
Medium-low. It requires the cherry-pick sequencer state (`CHERRY_PICK_HEAD`) to be removed or altered out from under Desktop between starting a cherry-pick and the user clicking "Continue" (e.g., a concurrent git operation, an external tool, or a race triggered while resolving conflicts), which is a plausible but not everyday occurrence for users who use both Desktop and external git tooling on the same repository, or who trigger overlapping multi-commit operations.

### Recommendation
Fix operator precedence/await ordering everywhere `isCherryPickHeadFound` is used so the promise is resolved before negation:

```ts
if (!(await isCherryPickHeadFound(repository))) {
  return CherryPickResult.UnableToStart
}
```

Apply the same fix to the two other broken call sites in `getCherryPickSnapshot`. [2](#0-1) [3](#0-2) 

### Proof of Concept
1. Start a cherry-pick in Desktop that produces a conflict, so `CHERRY_PICK_HEAD` is written to `.git`.
2. While Desktop's conflict UI is open, externally remove/rename `.git/CHERRY_PICK_HEAD` (simulating a concurrent tool, hook, or race), or otherwise cause `isCherryPickHeadFound` to resolve `false`.
3. Click "Continue" in Desktop, which invokes `continueCherryPick`.
4. Expected: Desktop returns `CherryPickResult.UnableToStart` because the cherry-pick state is gone.
5. Actual: because `if (await !isCherryPickHeadFound(repository))` never evaluates truthy, Desktop proceeds to stage files and run `git commit --allow-empty` / `git cherry-pick --continue` against the current index, producing a commit the guard was specifically designed to prevent. [6](#0-5)

### Citations

**File:** app/src/lib/git/cherry-pick.ts (L216-222)
```typescript
export async function getCherryPickSnapshot(
  repository: Repository
): Promise<ICherryPickSnapshot | null> {
  if (!isCherryPickHeadFound(repository)) {
    // If there no cherry pick head, there is no cherry pick in progress.
    return null
  }
```

**File:** app/src/lib/git/cherry-pick.ts (L300-305)
```typescript

    if (!isCherryPickHeadFound(repository)) {
      // We redo this check just because a user technically could end the
      // cherry-pick by the time we got here.
      return null
    }
```

**File:** app/src/lib/git/cherry-pick.ts (L368-476)
```typescript
 * Proceed with the current cherry pick operation and report back on whether it completed
 *
 * It is expected that the index has staged files which are cleanly cherry
 * picked onto the base branch, and the remaining unstaged files are those which
 * need manual resolution or were changed by the user to address inline
 * conflicts.
 *
 * @param files - The working directory of files. These are the files that are
 * detected to have changes that we want to stage for the cherry pick.
 */
export async function continueCherryPick(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map(),
  progressCallback?: (progress: IMultiCommitOperationProgress) => void
): Promise<CherryPickResult> {
  // only stage files related to cherry pick
  const trackedFiles = files.filter(f => {
    return f.status.kind !== AppFileStatusKind.Untracked
  })

  // apply conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file === undefined) {
      log.error(
        `[continueCherryPick] couldn't find file ${path} even though there's a manual resolution for it`
      )
      continue
    }
    await stageManualConflictResolution(repository, file, resolution)
  }

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))
  await stageFiles(repository, otherFiles)

  const status = await getStatus(repository, false)
  if (status == null) {
    log.warn(
      `[continueCherryPick] unable to get status after staging changes,
        skipping any other steps`
    )
    return CherryPickResult.UnableToStart
  }

  // make sure cherry pick is still in progress to continue
  if (await !isCherryPickHeadFound(repository)) {
    return CherryPickResult.UnableToStart
  }

  let options: IGitStringExecutionOptions = {
    expectedErrors: new Set([
      GitError.MergeConflicts,
      GitError.ConflictModifyDeletedInBranch,
      GitError.UnresolvedConflicts,
    ]),
    env: {
      // if we don't provide editor, we can't detect git errors
      GIT_EDITOR: ':',
    },
  }

  if (progressCallback !== undefined) {
    const snapshot = await getCherryPickSnapshot(repository)
    if (snapshot === null) {
      log.warn(
        `[continueCherryPick] unable to get cherry-pick status, skipping other steps`
      )
      return CherryPickResult.UnableToStart
    }

    options = configureOptionsWithCallBack(
      options,
      snapshot.commits,
      progressCallback,
      snapshot.cherryPickedCount
    )
  }

  const trackedFilesAfter = status.workingDirectory.files.filter(
    f => f.status.kind !== AppFileStatusKind.Untracked
  )

  if (trackedFilesAfter.length === 0) {
    log.warn(
      `[cherryPick] no tracked changes to commit, continuing cherry-pick but skipping this commit`
    )

    // This commits the empty commit so that the cherry picked commit still
    // shows up in the target branches history.
    const result = await git(
      ['commit', '--allow-empty'],
      repository.path,
      'continueCherryPickSkipCurrentCommit',
      options
    )

    return parseCherryPickResult(result)
  }

  const result = await git(
    ['cherry-pick', '--continue'],
    repository.path,
    'continueCherryPick',
    options
  )

  return parseCherryPickResult(result)
}
```
