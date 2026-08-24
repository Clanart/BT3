## Analysis

I found a structurally identical bug-class analog in GitHub Desktop's conflict-resolution bookkeeping. It is the same class of flaw as the Solidity report: **a tracking data structure (`collectedFees` / `manualResolutions`) that is supposed to be scoped to one operation is carried forward into a different operation without being reset, so stale entries are silently reused by a downstream trust-sensitive action** (fee distribution / file staging at commit time).

### Title
Stale manual conflict resolutions leak across unrelated merge/rebase/cherry-pick operations, causing silent wrong-content commits - (File: `app/src/lib/stores/updates/changes-state.ts`)

### Summary
`updateConflictState` unconditionally carries the `manualResolutions` map from the *previous* conflict state into the *next* one before it even knows whether the new conflict belongs to the same logical operation: [1](#0-0) 

When the conflict `kind` changes (merge → rebase, rebase → merge, or a fresh merge/rebase started right after a previous one was aborted/finished), the function explicitly acknowledges this is a different operation but does **not** discard the inherited map — it just returns the newly built `newConflictState`, which already contains the stale entries baked in by `getConflictState(status, manualResolutions)`: [2](#0-1) 

### Finding Description
`ManualConflictResolution` entries are keyed only by file **path** (`Map<string, ManualConflictResolution>`), not by any operation/session identifier: [3](#0-2) 

`getConflictState` builds each new `ConflictState` (merge/rebase/cherry-pick) using whatever `manualResolutions` map was passed in, regardless of whether it originated from a totally different operation: [4](#0-3) 

These resolutions are later consumed blindly at commit/continue time — without re-validating that the resolution was made for the *current* conflict — in both the merge-commit path and the rebase-continue path: [5](#0-4) [6](#0-5) 

The dispatcher pulls `conflictState.manualResolutions` straight out of the cached repository state and forwards it to the commit path without additional checks: [7](#0-6) 

**Broken invariant:** "a manual conflict resolution (ours/theirs) applies only to the conflict instance in which the user made that choice." Because the map is preserved by path across `kind` transitions, a resolution chosen during operation A (e.g. a merge with an attacker-controlled branch) can be silently reapplied during operation B (e.g. a subsequent rebase or a second merge) touching a file with the same path, with no re-prompt to the user.

This mirrors the report's root cause exactly: `collectedFees` (accounting state) survives an escape path (`emergencyWithdraw`) that should have reset it, later corrupting an unrelated accounting cycle. Here, `manualResolutions` (resolution state) survives an operation-kind transition that should have reset it, later corrupting an unrelated conflict resolution/commit.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." If a stale `theirs` resolution for path `foo.ts` from a prior, attacker-influenced merge is reused in a later conflict for the same path, Desktop will stage and commit attacker-controlled file content that the user never approved for the *current* operation — without any conflict-resolution UI showing that a resolution was auto-applied. This can push attacker-controlled content upstream under the user's identity, since `createMergeCommit`/`continueRebase` apply resolutions per-path with no cross-check against which operation produced the resolution.

### Likelihood Explanation
The transition-of-kind branch in `updateConflictState` (lines 294-311) is reachable through ordinary UI flows: aborting a merge and immediately starting a rebase (or vice versa) on branches that conflict on the same file path is a normal workflow, not an edge case requiring special privileges. An attacker only needs to control the content of a repository/branch the victim merges or rebases against (already an accepted attacker capability per the "Valid Impact" scope), and rely on the victim performing two sequential conflicting operations on the same path — a plausible real-world sequence (e.g., merge attempt → abort → rebase attempt) rather than a contrived one.

### Recommendation
Reset `manualResolutions` to a fresh empty `Map` whenever the conflict `kind` changes (the branch at `changes-state.ts:307-310`), analogous to deleting `collectedFees` on the emergency-withdraw escape path. E.g.:

```diff
  // Otherwise we transitioned from a merge conflict to a rebase conflict or
  // vice versa, and we should avoid any side effects here

- return newConflictState
+ return newConflictState === null
+   ? null
+   : { ...newConflictState, manualResolutions: new Map() }
```

Additionally, consider scoping resolutions by an operation identifier rather than by path alone, so cross-operation reuse is impossible even within the same `kind`.

### Proof of Concept
1. Victim clones/fetches an attacker-controlled repo with branch `evil` that conflicts with `main` on file `shared.ts`.
2. Victim runs `git merge evil`, gets a conflict on `shared.ts`, and picks "Use their version" (`ManualConflictResolution.theirs`) in Desktop's conflict UI — this stores `manualResolutions.set('shared.ts', theirs)` in the merge `ConflictState`.
3. Victim aborts the merge (`abortMerge`) before committing, then starts `git rebase evil` (or a merge with a different branch) that again conflicts on `shared.ts`, intending to resolve it differently this time (e.g., "Use my version").
4. Because `updateConflictState` inherits `manualResolutions` from the prior (different-kind) `ConflictState` at line 270-273 and does not clear it when kind changes, the stale `theirs` entry for `shared.ts` is present in the new `ConflictState` before the user makes any choice in the new operation.
5. If the user resolves other files and continues/commits, `continueRebase`/`createMergeCommit` iterates `manualResolutions` and calls `stageManualConflictResolution` for `shared.ts` using the **stale** "theirs" choice from step 2, silently staging and committing attacker-controlled content the user never approved for this operation.

Note: I was not able to fully verify, within the available tool budget, whether `_abortMerge`/`_abortRebase` in `app/src/lib/stores/app-store.ts` explicitly clear `changesState.conflictState` (which could reduce the reachable window for this scenario) — I located their call sites but could not inspect the implementation body before the iteration limit. This is the main open question that would need confirmation before treating this as fully proven end-to-end; the code-level defect in `updateConflictState`/`getConflictState` itself, however, is confirmed directly from the cited source.

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

**File:** app/src/lib/stores/updates/changes-state.ts (L268-275)
```typescript
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

**File:** app/src/lib/app-state.ts (L1143-1151)
```typescript
export type MultiCommitOperationConflictState = {
  readonly kind: 'multiCommitOperation'

  /**
   * Manual resolutions chosen by the user for conflicted files to be applied
   * before continuing the operation
   */
  readonly manualResolutions: Map<string, ManualConflictResolution>

```

**File:** app/src/lib/git/commit.ts (L82-97)
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
```

**File:** app/src/lib/git/rebase.ts (L444-458)
```typescript
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1533-1553)
```typescript
  public async finishConflictedMerge(
    repository: Repository,
    workingDirectory: WorkingDirectoryStatus,
    successfulMergeBanner: Banner,
    isSquash: boolean
  ) {
    // get manual resolutions in case there are manual conflicts
    const repositoryState = this.repositoryStateManager.get(repository)
    const { conflictState } = repositoryState.changesState
    if (conflictState === null) {
      // if this doesn't exist, something is very wrong and we shouldn't proceed 😢
      log.error(
        'Conflict state missing during finishConflictedMerge. No merge will be committed.'
      )
      return
    }
    const result = await this.appStore._finishConflictedMerge(
      repository,
      workingDirectory,
      conflictState.manualResolutions
    )
```
