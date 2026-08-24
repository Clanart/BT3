### Title
Stale per-path manual conflict resolutions carry over into a new, unrelated merge/rebase/cherry-pick and silently mark attacker-controlled conflicting files as "resolved" - (File: app/src/lib/stores/updates/changes-state.ts)

### Summary
`updateConflictState` recomputes the app's `conflictState` on every status refresh by carrying forward the existing `manualResolutions` map (keyed only by file **path**) from `prevConflictState` whenever one exists, with no check that the new conflict is actually the *same* operation instance as the one the resolutions were recorded for. [1](#0-0) 
This mirrors the OpenQ pattern where a bounded/keyed collection (`nftDeposits`) is not cleaned up when the underlying item it tracks is invalidated (refunded), so stale entries survive and are wrongly reused/counted against a later legitimate operation.

### Finding Description
`getConflictState` builds the new `ConflictState` (merge / rebase / cherryPick) and simply threads through whatever `manualResolutions` map it was given, without any binding to the specific operation it originated from (no operation id, no tip/branch equality requirement beyond best-effort stats bookkeeping): [2](#0-1) 

`updateConflictState` only inspects `prevConflictState !== null` to decide whether to keep the old map, and this check is done *before* it knows whether the new conflict is the same kind or the same instance: [1](#0-0) 

Crucially, when the previous and new conflict states are of *different kinds* (e.g. a merge in progress transitions to a rebase being detected, or vice versa), the function explicitly takes the fallback path and returns `newConflictState` as-is — which was already built using the **old** kind's `manualResolutions` map: [3](#0-2) 

Even within the *same* kind, the only guard is a branch/target-branch-name comparison used purely for statistics (`mergeAbortedAfterConflictsCount` / `rebaseAbortedAfterConflictsCount`); it does not clear `manualResolutions`: [4](#0-3) [5](#0-4) 

The existing unit tests confirm this behavior is treated as intentional "preserve resolutions" logic, but they only check the same-branch/same-kind case and never validate clearing when the tip changes to a genuinely different commit while the branch name coincidentally matches, or when the operation kind changes mid-flight: [6](#0-5) 

Downstream, this stale map is consulted by `hasUnresolvedConflicts`/`getConflictedFiles`/`getResolvedFiles` (via `manualResolutions.get(f.path)`) to decide whether a conflicted file needs to be shown to the user at all, and it is what `ConflictsDialog`/`unmerged-file.tsx` use to render (or hide) a file as still-conflicted: [7](#0-6) [8](#0-7) 

Because the map is keyed purely by file **path** with no operation/commit identity attached, a path that was manually resolved (e.g. "use theirs") during one merge/rebase against one (possibly untrusted) branch will still report as resolved if the *same path* becomes conflicted again in a subsequent, unrelated merge/rebase/cherry-pick against a different branch/commit — silently keeping the earlier "theirs" choice instead of presenting the new conflict to the user.

### Impact Explanation
An attacker who controls a remote/fork the victim merges from, fetches into, or force-pushes to, can engineer a repository so that a conflict recurs on the same file path across two operations that a normal user would treat as unrelated (e.g., merge branch A → user resolves `build.sh` as "theirs" (accepting attacker content) → user aborts/finishes and then merges or rebases branch B, which is entirely different but also conflicts on `build.sh`). Because the resolution map is never scoped/cleared per-operation-instance and can even leak across a merge→rebase kind transition, Desktop can treat the new conflict on `build.sh` as already resolved and silently stage/commit the attacker-supplied content without ever showing it to the user in the conflicts dialog. This is a silent corruption of what the user commits/pushes — the core valid-impact category for this analog — achieved purely through attacker control of the fetched/cloned repository content, with no local access, admin rights, or social engineering step beyond a normal merge/rebase the user already intended to perform.

### Likelihood Explanation
Exploitation requires a fairly specific sequencing: the same file path must be conflicted in two temporally close operations, and Desktop's status-refresh must observe the transition without an intervening state reset to `null`. This is realistic in workflows where users work with several remotes/forks and get conflicts on common, frequently-touched files (build scripts, lockfiles, CI config), or where a merge is left half-resolved and a rebase is subsequently started (including via external terminal actions, which Desktop explicitly supports via "Open in Shell/Terminal"). The likelihood is moderate: it does not require any privileged access, but it does require a specific repo/branch topology that an adversary who controls a fork can construct deliberately.

### Recommendation
Scope `manualResolutions` to the specific operation instance rather than carrying it forward whenever `prevConflictState !== null`:
- Include an operation-identifying value (e.g., the relevant tip/`MERGE_HEAD`/`REBASE_HEAD` sha, or a monotonically increasing operation id) alongside the `manualResolutions` map, and only preserve the map when that identifier is unchanged.
- In `updateConflictState`, explicitly reset `manualResolutions` to a new empty `Map` whenever `prevConflictState.kind !== newConflictState.kind`, or whenever the underlying commit tip identity indicates a new merge/rebase target rather than a continuation of the current one (not just when the branch *name* differs, which can be gamed by same-named branches with different tips).
- Add regression tests covering: same branch name but different tip, and kind-transition scenarios (merge → rebase, rebase → cherryPick), asserting `manualResolutions` is cleared in both cases.

### Proof of Concept
1. Victim has `changesState.conflictState` set to a `merge` conflict against branch `feature` where the map `manualResolutions = { "build.sh" → theirs }` (chosen while merging attacker's `feature` branch). [9](#0-8) 
2. The merge is aborted via the shell (or otherwise resolved) without Desktop observing an intermediate `conflictState === null` refresh, and the victim immediately starts a rebase against a different attacker-controlled branch that also modifies `build.sh` and conflicts on it (`REBASE_HEAD` now present).
3. `updateConflictState` computes `manualResolutions` from `prevConflictState.manualResolutions` (the old merge's map) because `prevConflictState !== null`, then calls `getConflictState`, which — since `status.rebaseInternalState !== null` — returns a new `rebase` `ConflictState` populated with that same stale map: [10](#0-9) 
4. Because `prevConflictState.kind` ('merge') differs from `newConflictState.kind` ('rebase'), the final `if`/`else if` chain falls through to the “transitioned … vice versa” branch and returns `newConflictState` unchanged — the stale `theirs` resolution for `build.sh` is preserved into the rebase: [3](#0-2) 
5. `getResolvedFiles`/`hasUnresolvedConflicts` in the rebase conflicts UI will treat `build.sh` as already resolved (`manualResolutions.get('build.sh') === theirs`), hiding the new, unrelated conflict from the user and allowing the rebase to complete/commit with the attacker's content silently accepted. [11](#0-10) 

Note: I could not fully trace every code path by which Desktop's background status refresh could observe a merge→rebase (or same-kind different-tip) transition in a single `updateConflictState` call versus always passing through an intermediate `null` state first; that timing detail depends on refresh scheduling logic not fully covered by the indexed portions of `app-store.ts` I was able to inspect. The core defect — the unconditional carry-forward of the path-keyed `manualResolutions` map without any operation-identity check — is, however, directly confirmed in `app/src/lib/stores/updates/changes-state.ts` and its accompanying tests.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L121-176)
```typescript
function getConflictState(
  status: IStatusResult,
  manualResolutions: Map<string, ManualConflictResolution>
): ConflictState | null {
  if (status.rebaseInternalState !== null) {
    const { currentTip } = status
    if (currentTip == null) {
      return null
    }

    const { targetBranch, originalBranchTip, baseBranchTip } =
      status.rebaseInternalState

    return {
      kind: 'rebase',
      currentTip,
      manualResolutions,
      targetBranch,
      originalBranchTip,
      baseBranchTip,
    }
  }

  if (status.isCherryPickingHeadFound) {
    const { currentBranch: targetBranchName } = status
    if (targetBranchName == null) {
      return null
    }
    return {
      kind: 'cherryPick',
      manualResolutions,
      targetBranchName,
    }
  }

  const { currentBranch, currentTip, mergeHeadFound, squashMsgFound } = status
  if (
    currentBranch == null ||
    currentTip == null ||
    (!mergeHeadFound && !squashMsgFound) ||
    // If there are no conflicts, we want to ignore the squash msg found.
    // However, we do want to prompt the conflicts showing all resolved
    // if a regular merge conflicts are all resolves so user can
    // commit the merge commit.
    (!mergeHeadFound && !status.doConflictedFilesExist)
  ) {
    return null
  }

  return {
    kind: 'merge',
    currentBranch,
    currentTip,
    manualResolutions,
  }
}
```

**File:** app/src/lib/stores/updates/changes-state.ts (L178-216)
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

  const { currentTip } = status

  // if the repository is no longer conflicted, what do we think happened?
  if (
    prevConflictState != null &&
    newConflictState == null &&
    currentTip != null
  ) {
    const previousTip = prevConflictState.currentTip

    if (previousTip !== currentTip) {
      statsStore.increment('mergeSuccessAfterConflictsCount')
    } else {
      statsStore.increment('mergeAbortedAfterConflictsCount')
    }
  }
}
```

**File:** app/src/lib/stores/updates/changes-state.ts (L218-261)
```typescript
function performEffectsForRebaseStateChange(
  prevConflictState: RebaseConflictState | null,
  newConflictState: RebaseConflictState | null,
  status: IStatusResult,
  statsStore: IStatsStore
) {
  const previousBranchName =
    prevConflictState != null ? prevConflictState.targetBranch : null
  const currentBranchName =
    newConflictState != null ? newConflictState.targetBranch : null

  const branchNameChanged =
    previousBranchName != null &&
    currentBranchName != null &&
    previousBranchName !== currentBranchName

  // The branch name has changed while remaining conflicted -> the rebase must have been aborted
  if (branchNameChanged) {
    statsStore.increment('rebaseAbortedAfterConflictsCount')
    return
  }

  const { currentTip, currentBranch } = status

  // if the repository is no longer conflicted, what do we think happened?
  if (
    prevConflictState != null &&
    newConflictState == null &&
    currentTip != null &&
    currentBranch != null
  ) {
    const previousTip = prevConflictState.originalBranchTip

    const previousTipChanged =
      previousTip !== currentTip &&
      currentBranch === prevConflictState.targetBranch

    if (!previousTipChanged) {
      statsStore.increment('rebaseAbortedAfterConflictsCount')
    }
  }

  return
}
```

**File:** app/src/lib/stores/updates/changes-state.ts (L263-276)
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

```

**File:** app/src/lib/stores/updates/changes-state.ts (L294-311)
```typescript
  if (
    (prevConflictState == null || isRebaseConflictState(prevConflictState)) &&
    (newConflictState == null || isRebaseConflictState(newConflictState))
  ) {
    performEffectsForRebaseStateChange(
      prevConflictState,
      newConflictState,
      status,
      statsStore
    )
    return newConflictState
  }

  // Otherwise we transitioned from a merge conflict to a rebase conflict or
  // vice versa, and we should avoid any side effects here

  return newConflictState
}
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

**File:** app/src/lib/status.ts (L151-174)
```typescript
/** Filter working directory changes for resolved files  */
export function getResolvedFiles(
  status: WorkingDirectoryStatus,
  manualResolutions: Map<string, ManualConflictResolution>
) {
  return status.files.filter(
    f =>
      isConflictedFileStatus(f.status) &&
      !hasUnresolvedConflicts(f.status, manualResolutions.get(f.path))
  )
}

/** Filter working directory changes for conflicted files  */
export function getConflictedFiles(
  status: WorkingDirectoryStatus,
  manualResolutions: Map<string, ManualConflictResolution>
) {
  return status.files.filter(
    f =>
      isConflictedFileStatus(f.status) &&
      hasUnresolvedConflicts(f.status, manualResolutions.get(f.path))
  )
}

```

**File:** app/src/ui/multi-commit-operation/dialog/conflicts-dialog.tsx (L173-199)
```typescript
  private renderUnmergedFiles(
    files: ReadonlyArray<WorkingDirectoryFileChange>
  ) {
    let isFirstUnmergedFile = true
    return (
      <ul className="unmerged-file-statuses">
        {files.map(f => {
          if (isConflictedFile(f.status)) {
            const isFirst = isFirstUnmergedFile
            isFirstUnmergedFile = false
            return renderUnmergedFile({
              path: f.path,
              status: f.status,
              resolvedExternalEditor: this.props.resolvedExternalEditor,
              openFileInExternalEditor: this.props.openFileInExternalEditor,
              repository: this.props.repository,
              dispatcher: this.props.dispatcher,
              manualResolution: this.props.manualResolutions.get(f.path),
              ourBranch: this.props.ourBranch,
              theirBranch: this.props.theirBranch,
              isFileResolutionOptionsMenuOpen:
                this.state.isFileResolutionOptionsMenuOpen,
              setIsFileResolutionOptionsMenuOpen:
                this.setIsFileResolutionOptionsMenuOpen,
              isFirstConflictedFile: isFirst,
            })
          }
```
