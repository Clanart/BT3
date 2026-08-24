### Title
Stale manual conflict resolutions keyed only by file path are silently reapplied to unrelated later conflicts on the same path during a multi-commit rebase/cherry-pick - ([File: app/src/lib/stores/updates/changes-state.ts])

### Summary
`updateConflictState` deliberately carries a rebase/cherry-pick's `manualResolutions: Map<string, ManualConflictResolution>` forward across every status update for the lifetime of the operation, keyed solely by file `path`. `continueRebase`/`continueCherryPick` then blindly re-apply whatever resolution is stored for a given path the next time that path shows up as conflicted, with no check that the resolution was chosen for *this* conflict's content rather than an earlier commit's conflict on the same path.

### Finding Description
`updateConflictState` explicitly preserves `manualResolutions` from the previous conflict state whenever the conflict "kind" stays the same (`rebase` → `rebase`, `merge` → `merge`): [1](#0-0) 

This is confirmed by unit tests that assert resolutions persist "between updates in the same merge/rebase" even when `currentTip` changes to a new SHA (i.e., a new commit in the rebase has just started being applied): [2](#0-1) 

The map is keyed only by `path`, never by a content hash, blob OID, or commit SHA: [3](#0-2) 

When the user hits "Continue", `continueRebase` iterates the persisted map and stages whatever resolution ("ours"/"theirs") was recorded for that path, for any file currently present in the working directory list that matches the path key: [4](#0-3) [5](#0-4) 

A multi-commit rebase applies a sequence of attacker-authored commits one at a time. It is entirely possible, and easy for an attacker who controls the branch/commits being rebased (a cloned/fetched repository) to craft the commit sequence so the *same file path* conflicts twice: once during commit N (where the user picks, say, "theirs" to resolve a benign-looking conflict) and again during a later commit N+k in the *same rebase operation* (where the content is completely different — e.g., attacker-crafted malicious content designed to look like a trivial conflict). Because `manualResolutions` is never cleared or re-scoped between commits within the same rebase, and `stageManualConflictResolution` only checks that the file is *currently* conflicted (not that the resolution corresponds to *this* conflict's content), the old "theirs" choice from commit N is silently reapplied to the unrelated conflict in commit N+k. The user is never shown or asked to confirm a resolution for the second conflict — the UI (`updateMultiCommitOperationConflictsIfFound` copies `manualResolutions` straight into the multi-commit-operation step state) will likely still show the file as "resolved" from the stale map entry: [6](#0-5) 

This mirrors the Tapioca bug class exactly: a piece of state (accrual timestamp / here, a conflict resolution) that should be scoped to a specific window of time/operation is instead carried forward across an intervening period (a new, unrelated conflict episode) without being refreshed, so a decision that was valid for the first window is silently applied to the second.

### Impact Explanation
The result is silent corruption of what the user commits: content from an attacker-controlled repository can end up staged and committed without the user ever reviewing or being prompted for that specific conflict, because Desktop reuses a previous "ours/theirs" decision on the same path. This can smuggle attacker-controlled file content into the user's history and, if later pushed, into the shared remote — a core-logic violation of Desktop's conflict-resolution safety guarantee ("the user resolves each conflict they are shown").

### Likelihood Explanation
Requires a specific, but fully attacker-controllable precondition: the attacker crafts a branch/commit sequence (in a repository the victim clones/fetches/rebases onto) such that the same file path conflicts more than once within a single multi-commit rebase or cherry-pick operation, and the user resolves the first occurrence manually via "ours"/"theirs" (not by hand-editing, which clears the resolution via the `conflictMarkerCount === 0` check in `stageManualConflictResolution`). This is a plausible but non-trivial repo-crafting exercise, and requires the user to be running a multi-commit rebase against attacker-supplied history and to use manual conflict resolution — hence Medium-ish likelihood, matching the low/medium likelihood classification of the original Tapioca report.

### Recommendation
Scope `manualResolutions` entries to the specific conflict instance rather than just the file path — e.g., key by `(path, currentTip/ontoSha)` or by the blob OIDs of the conflicting sides, and clear/invalidate any stale entries whenever the rebase's `currentTip` changes (i.e., a new commit begins being applied) or when the working directory no longer reports that path as conflicted. `continueRebase`/`continueCherryPick` should refuse to auto-apply a resolution unless it can verify the resolution was made for the exact conflict currently on disk.

### Proof of Concept
1. Attacker publishes a repository/branch with commits `C1` and `C2` that both modify the same file `shared.txt`, arranged such that rebasing this branch onto the victim's branch produces a conflict on `shared.txt` twice — once while applying `C1` and again while applying `C2` — with unrelated/different conflicting content each time.
2. Victim starts a Desktop multi-commit rebase that includes both `C1` and `C2`.
3. Conflict on `shared.txt` occurs at `C1`; victim manually resolves via "Use theirs" in the UI, which calls `_updateManualConflictResolution` → stored in `changesState.conflictState.manualResolutions` keyed by `"shared.txt"`. [7](#0-6) 
4. Victim clicks "Continue rebase"; `C1` is applied successfully, `currentTip` advances, and per `updateConflictState`/tests, `manualResolutions` (still containing `"shared.txt" → theirs`) is preserved into the new conflict state for `C2`.
5. `C2` now also conflicts on `shared.txt` with entirely different (attacker-crafted) content. `getConflictedFiles`/`ContinueRebase` UI may already show `shared.txt` as resolved due to the stale map entry, or the user again clicks Continue without the map being cleared.
6. `continueRebase` re-applies the stored `"theirs"` resolution to the new conflict on `shared.txt` via `stageManualConflictResolution`, silently staging the attacker's second-conflict content without the victim ever reviewing it. [8](#0-7)

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L263-292)
```typescript
export function updateConflictState(
  state: IChangesState,
  status: IStatusResult,
  statsStore: IStatsStore
): ConflictState | null {
  const prevConflictState = state.conflictState

  const manualResolutions =
    prevConflictState !== null
      ? prevConflictState.manualResolutions
      : new Map<string, ManualConflictResolution>()

  const newConflictState = getConflictState(status, manualResolutions)

  if (prevConflictState == null && newConflictState == null) {
    return null
  }

  if (
    (prevConflictState == null || isMergeConflictState(prevConflictState)) &&
    (newConflictState == null || isMergeConflictState(newConflictState))
  ) {
    performEffectsForMergeStateChange(
      prevConflictState,
      newConflictState,
      status,
      statsStore
    )
    return newConflictState
  }
```

**File:** app/test/unit/stores/updates/update-conflict-state-test.ts (L233-269)
```typescript
    it('preserves manual resolutions when a rebase is detected', () => {
      const prevState = createState({
        conflictState: {
          kind: 'rebase',
          currentTip: 'old-sha',
          manualResolutions,
          targetBranch: 'my-feature-branch',
          baseBranchTip: 'another-sha',
          originalBranchTip: 'some-other-sha',
        },
      })
      const status = createStatus({
        rebaseInternalState: {
          targetBranch: 'my-feature-branch',
          baseBranchTip: 'another-sha',
          originalBranchTip: 'some-other-sha',
        },
        currentBranch: 'master',
        currentTip: 'first-sha',
        doConflictedFilesExist: true,
      })

      const conflictState = updateConflictState(
        prevState,
        status,
        new TestStatsStore()
      )

      assert.deepStrictEqual(conflictState, {
        kind: 'rebase',
        currentTip: 'first-sha',
        manualResolutions,
        targetBranch: 'my-feature-branch',
        baseBranchTip: 'another-sha',
        originalBranchTip: 'some-other-sha',
      })
    })
```

**File:** app/src/lib/app-state.ts (L494-522)
```typescript
export type RebaseConflictState = {
  readonly kind: 'rebase'
  /**
   * This is the commit ID of the HEAD of the in-flight rebase
   */
  readonly currentTip: string
  /**
   * The branch chosen by the user to be rebased
   */
  readonly targetBranch: string
  /**
   * The branch chosen as the baseline for the rebase
   */
  readonly baseBranch?: string

  /**
   * The commit ID of the target branch before the rebase was initiated
   */
  readonly originalBranchTip: string
  /**
   * The commit ID of the base branch onto which the history will be applied
   */
  readonly baseBranchTip: string
  /**
   * Manual resolutions chosen by the user for conflicted files to be applied
   * before continuing the rebase.
   */
  readonly manualResolutions: Map<string, ManualConflictResolution>
}
```

**File:** app/src/lib/git/rebase.ts (L438-462)
```typescript
export async function continueRebase(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map(),
  opts?: RebaseInteractiveOptions
): Promise<RebaseResult> {
  const trackedFiles = files.filter(f => {
    return f.status.kind !== AppFileStatusKind.Untracked
  })

  // apply conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `[continueRebase] couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```

**File:** app/src/lib/git/stage.ts (L22-53)
```typescript
export async function stageManualConflictResolution(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  manualResolution: ManualConflictResolution
): Promise<void> {
  const { status } = file
  // if somehow the file isn't in a conflicted state
  if (!isConflictedFileStatus(status)) {
    log.error(`tried to manually resolve unconflicted file (${file.path})`)
    return
  }

  if (isConflictWithMarkers(status) && status.conflictMarkerCount === 0) {
    // If somehow the user used the Desktop UI to solve the conflict via ours/theirs
    // but afterwards resolved manually the conflicts via an editor, used the manually
    // resolved file.
    return
  }

  const chosen =
    manualResolution === ManualConflictResolution.theirs
      ? status.entry.them
      : status.entry.us

  const addedInBoth =
    status.entry.us === GitStatusEntry.Added &&
    status.entry.them === GitStatusEntry.Added

  if (chosen === GitStatusEntry.UpdatedButUnmerged || addedInBoth) {
    await checkoutConflictedFile(repository, file, manualResolution)
  }

```

**File:** app/src/lib/stores/app-store.ts (L3138-3156)
```typescript
    const { step, operationDetail } = multiCommitOperationState
    if (
      step.kind !== MultiCommitOperationStepKind.ShowConflicts &&
      step.kind !== MultiCommitOperationStepKind.ShowCopilotConflicts
    ) {
      return
    }

    const { manualResolutions } = conflictState

    this.repositoryStateCache.updateMultiCommitOperationState(
      repository,
      () => ({
        step: {
          ...step,
          conflictState: { ...step.conflictState, manualResolutions },
        },
      })
    )
```

**File:** app/src/lib/stores/app-store.ts (L8795-8828)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public _updateManualConflictResolution(
    repository: Repository,
    path: string,
    manualResolution: ManualConflictResolution | null
  ) {
    this.repositoryStateCache.updateChangesState(repository, state => {
      const { conflictState } = state

      if (conflictState === null) {
        // not currently in a conflict, whatever
        return { conflictState }
      }

      const updatedManualResolutions = new Map(conflictState.manualResolutions)

      if (manualResolution !== null) {
        updatedManualResolutions.set(path, manualResolution)
      } else {
        updatedManualResolutions.delete(path)
      }

      return {
        conflictState: {
          ...conflictState,
          manualResolutions: updatedManualResolutions,
        },
      }
    })

    this.updateMultiCommitOperationStateAfterManualResolution(repository)

    this.emitUpdate()
  }
```
