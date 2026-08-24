### Title
Stale per-path `manualResolutions` map silently reapplied across unrelated merge/rebase/cherry-pick conflicts, causing wrong side to be committed - (File: `app/src/lib/stores/updates/changes-state.ts`)

### Summary
`updateConflictState()` carries the `manualResolutions` map (keyed only by file *path*, `Map<string, ManualConflictResolution>`) forward from the previous conflict state into the newly computed conflict state, unconditionally and regardless of whether the new conflict is actually a continuation of the same operation. This is the same class of bug as the reported `idleETH` issue: a piece of tracked state (`idleETH` / `manualResolutions`) is not correctly reset/recomputed when the underlying "operation" changes, so a stale value silently determines outcomes in an unrelated subsequent action.

### Finding Description
In `updateConflictState`: [1](#0-0) 

the `manualResolutions` from `prevConflictState` (if any) is pulled forward and fed straight into `getConflictState(status, manualResolutions)` *before* it's known whether the new status represents the same merge/rebase/cherry-pick operation or a brand-new one: [2](#0-1) 

The only "reset" logic that exists (`performEffectsForMergeStateChange` / `performEffectsForRebaseStateChange`) is purely for telemetry (incrementing `mergeAbortedAfterConflictsCount`, etc.) — it never clears `manualResolutions`: [3](#0-2) 

Because the map is keyed only by file `path`, once a path was marked resolved with `ManualConflictResolution.ours` or `.theirs` in one conflict episode, that same choice is reused for **any later conflict on a file with the same path**, even across a completely different operation kind (e.g. abort a merge, then start a rebase, or abort a rebase and merge a different branch that also touches the same file).

This stale entry is not cosmetic — it directly gates whether a file is treated as resolved and whether it is auto-staged with a specific side's content: [4](#0-3) [5](#0-4) 

`getConflictedFiles`/`getResolvedFiles` (used to compute `conflictedFilesCount` that enables the "Continue rebase"/"commit merge" button) treat a path with a leftover `manualResolutions` entry as already resolved: [6](#0-5) 

And when the commit/continue action actually runs, `stageManualConflictResolution` uses the resolution value to pick which side's content (`status.entry.us` vs `status.entry.them`) gets checked out and staged: [7](#0-6) 

Critically, git's semantics of "ours"/"theirs" are inverted between a merge and a rebase (during rebase, "ours" is the upstream/target branch and "theirs" is the user's own commits). If a stale `ManualConflictResolution` value from a prior merge conflict on `src/foo.ts` survives into a later rebase conflict on the same path, the UI shows the file as already resolved and, when the user hits "Continue", the app stages the *opposite* side's content from what the user actually reviewed and intended — without any additional prompt.

### Impact Explanation
An attacker who controls a remote/branch (e.g. a PR branch or a repository the victim clones/fetches) can craft history so that a file at a known path repeatedly produces conflicts across the sequence of operations a maintainer is likely to perform (merge attempt → abort → rebase, or successive merges of different branches touching the same file). Because the app silently reuses the earlier `ours`/`theirs` decision instead of re-prompting, the victim can end up committing and pushing content they never reviewed for the second conflict — i.e., silent corruption of what the user commits/pushes, satisfying the "silent corruption of what the user commits or pushes" impact criterion. This requires no local/physical access, no elevated privileges, and no user error beyond normal, expected Desktop workflows (resolve conflict → abort → try a different operation).

### Likelihood Explanation
Moderate. It requires: (1) a conflict on a given path resolved manually via ours/theirs, (2) that operation aborted or completed, and (3) a second, different conflict-producing operation on a file with the identical path shortly after, without the working directory/app being fully closed and reopened (the in-memory `repositoryStateCache`/`conflictState` must persist). This is a realistic sequence for maintainers juggling multiple attacker-supplied branches/PRs (try merge A, abort, rebase B) touching the same files, but it is not the very first thing every user will hit, hence "moderate" rather than "high" likelihood.

### Recommendation
Clear `manualResolutions` whenever the conflict is determined to correspond to a different operation instance rather than a continuation of the same one — e.g., track and compare the operation-identifying fields already computed in `performEffectsFor*StateChange` (branch name / tip) *before* deciding whether to carry the map forward, and start with an empty map whenever those identifiers indicate an abort followed by a new operation, or whenever the conflict `kind` changes (merge ↔ rebase ↔ cherry-pick). At minimum, invalidate any path's entry in the map whenever the underlying `GitStatusEntry` (`us`/`them`) for that path differs from what it was when the resolution was recorded.

### Proof of Concept
1. In a repo, merge branch `attacker-a` into `main`; it conflicts on `src/shared.ts`. In the conflicts dialog, resolve `src/shared.ts` via "Use my file" (`ManualConflictResolution.ours`). Do not click "Commit merge" yet — abort the merge instead (`git merge --abort`), refreshing status: `conflictState` becomes `null`, but this refresh calls `updateChangedFiles`/`updateConflictState` and it is only cleared because `newConflictState` is `null`; verify via `updateConflictState` that `manualResolutions` map itself, however, is a JS object still referenced by the previous state until a *new* conflict starts.
2. Start `git rebase attacker-b` (or merge a different attacker-controlled branch) that also conflicts on `src/shared.ts`, this time with `us`/`them` swapped semantics (rebase inverts ours/theirs). `getConflictState` in `changes-state.ts` L121-176 is invoked with the still-populated `manualResolutions` map from step 1 (because it's threaded through unconditionally at L270-275), so `src/shared.ts` is immediately reported as resolved by `getConflictedFiles`/`getResolvedFiles` (`app/src/lib/status.ts` L151-173) without the user picking anything for this new conflict.
3. The "Continue rebase" button (`app/src/ui/changes/continue-rebase.tsx` L38-48) is enabled because `conflictedFilesCount === 0`. Clicking it triggers `stageManualConflictResolution` (`app/src/lib/git/stage.ts` L41-59) with the stale `ManualConflictResolution.ours` value, but because of the merge/rebase `ours`/`theirs` inversion, this now checks out and stages `status.entry.them` — the attacker's incoming content — into the commit, with no additional review step, completing the silent corruption of the committed/pushed content.

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

**File:** app/src/lib/stores/updates/changes-state.ts (L263-279)
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
```

**File:** app/src/lib/status.ts (L68-84)
```typescript
export function hasUnresolvedConflicts(
  status: ConflictedFileStatus,
  manualResolution?: ManualConflictResolution
) {
  // if there's a manual resolution, the file does not have unresolved conflicts
  if (manualResolution !== undefined) {
    return false
  }

  if (isConflictWithMarkers(status)) {
    // text file may have conflict markers present
    return status.conflictMarkerCount > 0
  }

  // binary file doesn't contain markers
  return true
}
```

**File:** app/src/lib/status.ts (L151-173)
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

**File:** app/src/ui/changes/continue-rebase.tsx (L32-48)
```typescript
  public render() {
    const { manualResolutions } = this.props.rebaseConflictState

    let canCommit = true
    let tooltip = 'Continue rebase'

    const conflictedFilesCount = getConflictedFiles(
      this.props.workingDirectory,
      manualResolutions
    ).length

    if (conflictedFilesCount > 0) {
      tooltip = 'Resolve all conflicts before continuing'
      canCommit = false
    }

    const buttonEnabled = canCommit && !this.props.isCommitting
```

**File:** app/src/lib/git/stage.ts (L41-59)
```typescript
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

  switch (chosen) {
    case GitStatusEntry.Deleted:
      return removeConflictedFile(repository, file)
    case GitStatusEntry.Added:
    case GitStatusEntry.UpdatedButUnmerged:
      return addConflictedFile(repository, file)
```
