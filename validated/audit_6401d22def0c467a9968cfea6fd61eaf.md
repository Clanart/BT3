### Title
Concurrent `_loadStatus` calls can overwrite fresher changes-state with a stale git status snapshot - ([File: app/src/lib/stores/app-store.ts])

### Summary
The Lighthouse bug is a classic "TOCTOU on shared mutable state": two concurrent async paths race to update the same value, and the completion order (not the start order) determines what wins, so a stale update can silently clobber a fresher one. The fix was to pass an "expected" value into the mutator and bail out if the live value no longer matches it. `AppStore._loadStatus` in this codebase has the same broken invariant: it awaits an async git status read and then unconditionally writes the result into `repositoryStateCache`, with no check that the state hasn't been superseded by a newer status load in the meantime.

### Finding Description
`AppStore._loadStatus` [1](#0-0)  does:
1. `await gitStore.loadStatus()` — an async git process invocation.
2. Unconditionally calls `repositoryStateCache.updateChangesState(repository, state => updateChangedFiles(state, status, clearPartialState))` and then `updateConflictState(...)`, merging the fetched `status` into whatever the *current* `IChangesState` happens to be at the time the promise resolves.

There is no "before/after" staleness guard here, unlike the pattern used elsewhere in the same file for comparable async races — e.g. `updateChangesStashDiff` explicitly snapshots state before the await and bails if the selection changed while awaiting [2](#0-1) , and the merge dialog's `updateStatus` does the same for `selectedBranch` [3](#0-2) .

`_loadStatus` is invoked from multiple independent call sites that can run concurrently for the same `Repository` (different promises, not serialized by any lock): `_refreshRepository` [4](#0-3) , `refreshChangesSection` [5](#0-4) , `recoverMissingRepository` [6](#0-5) , and directly from the dispatcher after operations like `abortRebase`/`continueRebase` [7](#0-6) . None of these callers coordinate with each other via a mutex on the changes state (the only guarded network-op flag is `isPushPullFetchInProgress`, which only serializes push/pull/fetch [8](#0-7) , not status loads).

`updateChangedFiles` merges the incoming `status.workingDirectory.files` with the *current* `state.workingDirectory` to preserve partial-selection state (`existingFile.withSelection(existingFile.selection)`) [9](#0-8) . If call A (started first, e.g. a background/file-watcher-triggered refresh) resolves *after* call B (started later, e.g. triggered right after a user discards/stages files or resolves conflicts), A's stale `status` — with pre-discard file list and pre-resolution `conflictState`/manual-resolutions map — is merged on top of the already-updated state, because `updateChangedFiles`/`updateConflictState` only look at "current state" for reconciliation, never at "the state that should have resulted from B." This is exactly the CGC race pattern: the value read at operation start is applied unconditionally at operation end, and only the value that "happens to be current" at commit time is used, not what was current when the async operation was kicked off.

### Impact Explanation
The corrupted values are `IChangesState.workingDirectory` (the working-directory file list backing what the user selects/stages) and `IChangesState.conflictState` (including `manualResolutions`). Because `_triggerConflictsFlow` and `initializeMultiCommitOperationIfConflictsFound` key off this same state [10](#0-9) , a stale overwrite can resurrect an already-resolved conflict state or resurrect discarded/committed files as "changed" with wrong selection, silently corrupting what the user is about to commit — the exact "silent corruption of what the user commits" class called out as valid impact.

### Likelihood Explanation
This requires no attacker-controlled repository content beyond ordinary git operations completing at variable speed (I/O jitter is enough); it is triggered purely by normal user/app concurrency (background refresh timer, file-system watcher refresh, and user-initiated actions like discard/stage/resolve-conflict all call into `_loadStatus`/`_refreshRepository` without mutual exclusion). No local/admin access or malicious remote is needed to trigger the race itself, though the severity is higher when combined with attacker-influenced repo state (e.g., conflicting/large working directories that slow down `git status`, widening the race window). I was not able to fully verify from the indexed code whether `GitStore.loadStatus()` itself has any internal request-coalescing (the `git-store.ts` grep for `loadStatus`/`_tip` inside that file returned no matches in this pass, so its exact implementation is not confirmed) — this is a gap that should be checked in a full session before finalizing severity.

### Recommendation
Snapshot the changes-state (or at least a monotonically increasing "status generation" counter) before calling `gitStore.loadStatus()`, and check it after the await before applying `updateChangedFiles`/`updateConflictState`, mirroring the pattern already used in `updateChangesStashDiff` and `merge-choose-branch-dialog.updateStatus`. Concretely: introduce a per-repository incrementing `statusRequestId` in `RepositoryStateCache`/`AppStore`, capture it before `await gitStore.loadStatus()`, and only merge results into the cache if the id is still current when the promise resolves; otherwise drop the stale result (analogous to passing the "expected cgc" into `backfill_validator_custody_requirements` in the Lighthouse fix).

### Proof of Concept
1. Trigger a background repository refresh (e.g. file watcher fires) which begins `_refreshRepository` → `_loadStatus`, kicking off a slow `git status` (can be simulated/slowed with a large working directory or many untracked files).
2. Before that resolves, perform a user action (stage/discard a file, or resolve a merge conflict) which triggers its own faster `_loadStatus`/`refreshChangesSection` call that resolves first and updates `IChangesState` to reflect the new (post-action) state.
3. The first, slower `_loadStatus` call then resolves and calls `updateChangesState`/`updateConflictState` with its stale `status`, unconditionally overwriting the just-updated `workingDirectory`/`conflictState` with pre-action data.
4. Observe the Changes list reverting to show already-discarded/staged files or a resolved conflict reappearing as unresolved, with no guard preventing the stale write — reproducible deterministically by manually delaying the first `gitStore.loadStatus()` call in a debugger/test harness to resolve after the second.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2969-3004)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _loadStatus(
    repository: Repository,
    clearPartialState: boolean = false
  ): Promise<IStatusResult | null> {
    const gitStore = this.gitStoreCache.get(repository)
    const status = await gitStore.loadStatus()

    if (status === null) {
      return null
    }

    this.repositoryStateCache.updateChangesState(repository, state =>
      updateChangedFiles(state, status, clearPartialState)
    )

    this.repositoryStateCache.updateChangesState(repository, state => ({
      conflictState: updateConflictState(state, status, this.statsStore),
    }))

    this.updateMultiCommitOperationConflictsIfFound(repository)
    await this.initializeMultiCommitOperationIfConflictsFound(
      repository,
      status
    )

    if (this.selectedRepository === repository) {
      this._triggerConflictsFlow(repository, status)
    }

    this.emitUpdate()

    this.updateChangesWorkingDirectoryDiff(repository)

    return status
  }
```

**File:** app/src/lib/stores/app-store.ts (L3656-3668)
```typescript
    const diff = await getCommitDiff(repository, file, file.commitish)

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesStateAfterLoad = stateAfterLoad.changesState

    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3960-3960)
```typescript
      type.kind === 'regular' && (await this._loadStatus(repository)) !== null
```

**File:** app/src/lib/stores/app-store.ts (L4093-4093)
```typescript
    const status = await this._loadStatus(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4331-4333)
```typescript
    if (options.includingStatus) {
      await this._loadStatus(repository, options.clearPartialState)
    }
```

**File:** app/src/lib/stores/app-store.ts (L5427-5450)
```typescript
  private async withPushPullFetch(
    repository: Repository,
    fn: () => Promise<void>
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    // Don't allow concurrent network operations.
    if (state.isPushPullFetchInProgress) {
      return
    }

    this.repositoryStateCache.update(repository, () => ({
      isPushPullFetchInProgress: true,
    }))
    this.emitUpdate()

    try {
      await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isPushPullFetchInProgress: false,
      }))
      this.emitUpdate()
    }
  }
```

**File:** app/src/ui/multi-commit-operation/choose-branch/merge-choose-branch-dialog.tsx (L114-122)
```typescript
    // The user has selected a different branch since we started or the branch
    // has changed, so don't update the preview with stale data.
    //
    // We don't have to check if the state changed from underneath us if we
    // loaded the status from cache, because that means we never kicked off an
    // async operation.
    if (this.state.selectedBranch?.tip.sha !== branch.tip.sha) {
      return
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1463-1466)
```typescript
  public async abortRebase(repository: Repository) {
    await this.appStore._abortRebase(repository)
    await this.appStore._loadStatus(repository)
    await this.refreshRepository(repository)
```

**File:** app/src/lib/stores/updates/changes-state.ts (L43-60)
```typescript
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
    })
```
