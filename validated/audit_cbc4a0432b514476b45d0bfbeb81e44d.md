### Title
Stale manual conflict resolutions are silently reapplied to a new merge/rebase on a different branch tip, causing corrupted commits - ([File: app/src/lib/stores/updates/changes-state.ts])

### Summary
The reported bug's broken invariant is: a per-operation resolution flag (`hasRefunded`) is keyed by an identifier (`gameId`+`player`) that is reused across two logically distinct sessions (leave→rejoin→cancel), and the flag is never cleared when the session context is re-entered, permanently corrupting the later session's outcome. The Desktop analog is `updateConflictState()` in `app/src/lib/stores/updates/changes-state.ts`, which carries a `manualResolutions: Map<string, ManualConflictResolution>` keyed only by file `path` across successive git conflict operations, and explicitly detects — but does not clear the map on — the case where the underlying merge/rebase context has changed (`branchNameChanged`).

### Finding Description
`updateConflictState` grabs the previous resolution map unconditionally before it knows whether the new conflict belongs to the same operation: [1](#0-0) 

It then evaluates `performEffectsForMergeStateChange` / `performEffectsForRebaseStateChange`, which explicitly detect a `branchNameChanged` condition — meaning the conflicted operation the user is now looking at is not the same one the resolutions were recorded for (the prior merge/rebase was aborted and a new one against a different branch is already in progress) — but in that branch they only increment a telemetry counter and `return`, without discarding `manualResolutions`: [2](#0-1) [3](#0-2) 

Because `newConflictState` was already built by `getConflictState(status, manualResolutions)` using the *old* map (line 275) before this check runs, the stale resolutions for the previous merge/rebase are returned untouched as part of the state for the brand-new conflict. Those resolutions are keyed purely by file `path`, with no binding to the specific blob/commit content that was being resolved, so a resolution recorded against one branch's conflicting content ("theirs" from branch A) is transparently reused for a completely different conflicting content on the same path from branch B.

This resolution map subsequently drives the automatic staging of file content without further user confirmation, both for merges (`_updateManualConflictResolution` → `updateMultiCommitOperationStateAfterManualResolution`) and for rebases (`continueRebase`), which iterates the map and calls `stageManualConflictResolution` for each `path` it contains: [4](#0-3) [5](#0-4) 

If the UI has already advanced past the `ShowConflicts` step for that path (because it "remembers" it as resolved), the file is staged and committed using the leftover resolution instead of the actual current conflict markers, with no re-prompt to the user.

### Impact Explanation
This results in silent corruption of what the user commits: a file conflict from an attacker-controlled branch/PR can be resolved once by the user, and if the app transitions into a second conflicted operation against a different git object (e.g., the user aborts and quickly starts a new merge/rebase against another remote-tracking branch that also touches the same path) before Desktop's status refresh catches up, the stale "theirs"/"ours" decision from the first conflict is silently reapplied to the second, unrelated conflict. Since an attacker can craft the conflicting content of a fetched/merged branch (a git object under attacker control per the reachable-path criteria), this can be used to smuggle attacker-chosen content into a commit that the victim believes they manually reviewed and resolved, and which is then pushed. This matches "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The `branchNameChanged` code path is explicitly modeled in the codebase (both for merge and rebase state transitions) and is exercised by existing unit tests confirming the manual-resolutions map is preserved across conflict-state transitions: [6](#0-5) 

The trigger requires the app to observe a status transition directly from "conflicted against branch A" to "conflicted against branch B" — a plausible sequence when a user rapidly aborts and restarts a merge/rebase, or when automated/rapid conflict-resolution flows (e.g., driven by scripting or by quick successive user actions) are in play. It does not require local/physical access, admin rights, or leaked credentials — only that the victim merges/rebases attacker-influenced branches that conflict on the same path.

### Recommendation
Clear `manualResolutions` whenever `getConflictState`/`updateConflictState` detects that the underlying operation context has changed (i.e., in the `branchNameChanged` branches of `performEffectsForMergeStateChange` and `performEffectsForRebaseStateChange`, and more generally key resolutions by more than just file `path` — e.g., include the current conflicted blob SHA or `MERGE_HEAD`/rebase step identity — so a resolution can never be silently reused for different underlying content).

### Proof of Concept
Not independently executable from static analysis alone; the following unit-test-style scenario demonstrates the code path using the existing test harness in `app/test/unit/stores/updates/update-conflict-state-test.ts`:
1. Call `updateConflictState` with `prevState.conflictState = { kind: 'merge', currentBranch: 'feature-A', currentTip: 'shaA', manualResolutions: Map{'file.txt' → theirs} }`.
2. Call it again with a `status` whose `currentBranch` is `'feature-B'` (a different, still-conflicted branch) and `mergeHeadFound: true`, `doConflictedFilesExist: true`.
3. Observe that `performEffectsForMergeStateChange` detects `branchNameChanged` and returns early (`app/src/lib/stores/updates/changes-state.ts:194-198`), yet the returned `newConflictState` (built at line 275) still contains `manualResolutions = Map{'file.txt' → theirs}` inherited from the `feature-A` merge, despite `file.txt`'s conflicting content now originating entirely from `feature-B`.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L178-198)
```typescript
function performEffectsForMergeStateChange(
  prevConflictState: MergeConflictState | null,
  newConflictState: MergeConflictState | null,
  status: IStatusResult,
  statsStore: IStatsStore
): void {
  const previousBranchName =
    prevConflictState != null ? prevConflictState.currentBranch : null
  const currentBranchName =
    newConflictState != null ? newConflictState.currentBranch : null

  const branchNameChanged =
    previousBranchName != null &&
    currentBranchName != null &&
    previousBranchName !== currentBranchName

  // The branch name has changed while remaining conflicted -> the merge must have been aborted
  if (branchNameChanged) {
    statsStore.increment('mergeAbortedAfterConflictsCount')
    return
  }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L268-275)
```typescript
  const prevConflictState = state.conflictState

  const manualResolutions =
    prevConflictState !== null
      ? prevConflictState.manualResolutions
      : new Map<string, ManualConflictResolution>()

  const newConflictState = getConflictState(status, manualResolutions)
```

**File:** app/src/lib/stores/updates/changes-state.ts (L281-292)
```typescript
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

**File:** app/src/lib/git/rebase.ts (L448-458)
```typescript
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
```

**File:** app/src/lib/stores/app-store.ts (L8796-8827)
```typescript
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
```

**File:** app/test/unit/stores/updates/update-conflict-state-test.ts (L35-63)
```typescript
    it('preserves manual resolutions between updates in the same merge', () => {
      const prevState = createState({
        conflictState: {
          kind: 'merge',
          currentBranch: 'old-branch',
          currentTip: 'old-sha',
          manualResolutions,
        },
      })
      const status = createStatus({
        mergeHeadFound: true,
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
        kind: 'merge',
        currentBranch: 'master',
        currentTip: 'first-sha',
        manualResolutions,
      })
    })
```
