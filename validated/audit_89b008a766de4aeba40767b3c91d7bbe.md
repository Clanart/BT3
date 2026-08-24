Based on my investigation, the closest analog to the Gatekeeper bug (a "reset"/transition path that fails to clear per-item state, so a downstream check keeps trusting a stale flag) is the conflict-resolution state machine in GitHub Desktop's `updateConflictState`.

### Title
Stale manual conflict resolutions silently carried across an unrelated merge/rebase/cherry-pick transition, causing attacker-controlled conflict content to be auto-staged without user review - (`File: app/src/lib/stores/updates/changes-state.ts`)

### Summary
`updateConflictState` computes the `manualResolutions` map to reuse for a *new* conflict state solely based on whether a previous `conflictState` object existed in memory (`prevConflictState !== null`), not on whether the new conflict belongs to the same git operation. When the conflict *kind* changes (merge → rebase, rebase → cherry-pick, etc.) the function explicitly acknowledges this transition but still returns `newConflictState` carrying over the old, unrelated `manualResolutions` map instead of clearing it.

### Finding Description [1](#0-0) 

The map is keyed only by relative file path: [2](#0-1) 

and the code explicitly notes it deliberately skips clean-up when the conflict kind changes: [3](#0-2) 

That carried-over map is then trusted, without re-validation against the *current* operation, by the very functions that decide whether a file still needs user attention: [4](#0-3) 

and by the commit/continue paths that mechanically apply the stored choice and stage the result: [5](#0-4) [6](#0-5) 

This mirrors the Gatekeeper flaw precisely: `resetAllGates` decremented `claimedCount` but never `delete`d the underlying per-token gate record, so `validateProof` (which trusts the stale `claimed` flag) kept behaving as if the gate were still claimed. Here, a transition between conflict "operations" (merge/rebase/cherry-pick) never clears the per-path `manualResolutions` record, so `hasUnresolvedConflicts`/`getResolvedFiles` (which trust that map) keep treating a brand-new, unrelated conflict on the same file path as already resolved.

### Impact Explanation
If an attacker crafts branches/remotes such that two different conflicting operations (e.g. a merge the user aborts and a subsequent rebase, both conflicting on the same file path) occur without an intervening git-status poll that nulls out `conflictState`, GitHub Desktop will reuse the previously chosen `ours`/`theirs` resolution for the *new* conflict. The new conflict is then hidden from the conflicts-review UI (`getConflictedFiles`/`getResolvedFiles`) and its content is mechanically staged via `git checkout --ours/--theirs` and committed via `createMergeCommit`/`continueCherryPick` without the user ever reviewing the new, attacker-influenced conflict markers. This is a silent corruption of what the user commits (and potentially pushes), matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Medium-Low. It requires (a) a prior conflict where the user made a manual `ours`/`theirs` choice on a given path, and (b) a second, unrelated conflicting operation on a file with the *same relative path* occurring before the app's next status refresh clears `conflictState` to `null`. Both conditions are plausible in ordinary abort-and-retry workflows (e.g. abort a merge, then try rebasing instead) and can be engineered by an attacker who controls the conflicting branches/paths, but the timing window (no intervening status refresh) makes it non-deterministic.

### Recommendation
- In `updateConflictState`, only carry forward `manualResolutions` when `prevConflictState` and `newConflictState` are the *same* conflict kind (and ideally the same underlying operation, e.g. same `targetBranch`/`currentTip` lineage); otherwise start from a fresh `new Map()`.
- Treat the kind-change branch (lines 307-311) the same as the "conflict resolved" branches: explicitly reset resolutions instead of silently forwarding `newConflictState` unchanged.
- Defensively re-validate in `createMergeCommit`/`continueCherryPick`/`continueRebase` that a stored manual resolution still corresponds to a conflict from the *current* operation before applying it.

### Proof of Concept
1. Trigger a merge that conflicts on `shared.txt`; in the conflicts dialog choose "Use their changes" for `shared.txt` (sets `manualResolutions = { 'shared.txt': theirs }` in `conflictState`).
2. Abort the merge via the Desktop UI/CLI, then immediately start a rebase against a different branch that also conflicts on `shared.txt` (different, attacker-controlled content), before Desktop's next background status refresh completes.
3. Because `updateConflictState` (`app/src/lib/stores/updates/changes-state.ts:270-311`) forwards the old `manualResolutions` map into the new (rebase) `conflictState` regardless of kind change, `shared.txt` is immediately treated as already resolved by `getResolvedFiles`/`hasUnresolvedConflicts` (`app/src/lib/status.ts:151-173`).
4. Clicking "Continue rebase" runs `stageManualConflictResolution` for `shared.txt` using the stale `theirs` choice (`app/src/lib/git/commit.ts` / `cherry-pick.ts`), silently staging and committing the new, unreviewed conflict content from the rebase.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L263-311)
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

**File:** app/src/lib/git/commit.ts (L82-101)
```typescript
export async function createMergeCommit(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map()
): Promise<string> {
  // apply manual conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = files.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```

**File:** app/src/lib/git/cherry-pick.ts (L389-402)
```typescript
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
```
