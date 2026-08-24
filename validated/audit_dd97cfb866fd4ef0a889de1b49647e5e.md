## Title
Stale cross-operation `manualResolutions` map silently auto-resolves a new merge/rebase/cherry-pick conflict on the same file path - ([File: app/src/lib/stores/updates/changes-state.ts])

## Summary
The C4 bug (`vaultOrderHash[_vault]` being a single per-vault slot shared between an *active* listing and a newly created *proposed* listing, so a stale reference validates the wrong operation) has a structural analog in Desktop's conflict-tracking code: `manualResolutions`, a `Map<path, ManualConflictResolution>`, is a single per-repository slot that is carried forward verbatim across **different, unrelated conflict operations** (merge → rebase → cherry-pick transitions), instead of being scoped to the specific operation that produced it.

## Finding Description
`updateConflictState` in [1](#0-0)  always seeds the new conflict state from the *previous* conflict state's `manualResolutions`, regardless of whether the underlying operation is actually the same one: [2](#0-1) 

When the previous and next conflict states are of different kinds (e.g. a merge conflict is followed by a rebase conflict), the code explicitly acknowledges the transition but still returns `newConflictState`, which was built by `getConflictState(status, manualResolutions)` using the *old, unrelated* map — nothing clears it: [3](#0-2) 

`getConflictState` embeds the caller-supplied `manualResolutions` directly into the new `MergeConflictState`/`RebaseConflictState`/`CherryPickConflictState` object regardless of kind: [4](#0-3) [5](#0-4) [6](#0-5) 

The resulting stale map key (a file path) is then used as the sole "is this resolved" check for the *new* operation, with no verification that the resolution decision actually applies to the current conflict on that path: [7](#0-6) 

And when the user hits "Continue", `continueRebase` blindly stages whatever resolution is recorded for a matching path — again with no correlation to which operation that decision was made for: [8](#0-7) 

This mirrors the report's broken invariant exactly: a single shared storage slot (`vaultOrderHash[_vault]` / `manualResolutions`) is keyed by an identity that is not unique to the operation instance (vault address / file path), so a later, unrelated operation inherits validation state that belongs to an earlier one.

## Impact Explanation
If a repository is crafted (or naturally arranged, e.g. via a hostile branch history) such that a conflict recurs on the same file path across two different conflict "kinds" within a single continuous Desktop flow (e.g., user resolves a merge conflict on `config.yml` with "Use theirs", then — without the map being reset — a rebase or cherry-pick subsequently conflicts on `config.yml` again), Desktop will treat the file as already resolved in the new operation. `getResolvedFiles`/`getConflictedFiles` will report it as resolved, the "Continue" button will be enabled without ever showing the user the current conflict content, and `stageManualConflictResolution` will stage the *previous* operation's chosen side into the commit. This is a silent corruption of what the user commits/pushes: the user believes they are committing content they never reviewed for the current operation, and the staged content is effectively picked by whichever side ("ours"/"theirs") the attacker's branch history steers toward.

## Likelihood Explanation
This requires a specific sequencing (transition between merge/rebase/cherry-pick conflict kinds without the map being cleared, and a recurring file path) which is a narrower window than the C4 original (which needed only two sequential `propose()` calls). I was not able to fully verify, within the available tool budget, an end-to-end concrete Desktop user flow that reliably drives two different conflict kinds back-to-back on the same path purely from attacker-controlled repository content (as opposed to requiring some manual user action like "abort merge, then start a rebase"). This limits confidence that the path is reachable purely from a hostile clone/fetch without any unusual user steps, so likelihood should be treated as uncertain/moderate rather than confirmed high.

## Recommendation
Scope `manualResolutions` to the specific conflict operation instance rather than carrying it across kind transitions: clear (reset to an empty map) whenever `prevConflictState` and `newConflictState` are of different `kind`s (the code already detects this case at [3](#0-2)  but fails to act on it), analogous to the C4 fix of moving the order hash into the per-listing struct instead of a single per-vault slot.

## Proof of Concept
Conceptual reproduction using the existing test harness pattern in [9](#0-8) :
1. Seed `prevState` with a `merge` conflict state whose `manualResolutions` contains `{'shared.txt': theirs}` (as in the merge tests at lines 35-63).
2. Call `updateConflictState` with a `status` that instead reports `rebaseInternalState` (simulating a rebase conflict starting on `shared.txt`), taking the "otherwise we transitioned" branch at lines 307-310.
3. Observe the returned `RebaseConflictState.manualResolutions` still contains `{'shared.txt': theirs}` from the prior, unrelated merge — never reset for the new operation.
4. In the UI, `getConflictedFiles`/`hasUnresolvedConflicts` ( [7](#0-6) ) would report `shared.txt` as already resolved for the rebase, and `continueRebase` ( [8](#0-7) ) would stage the stale `theirs` resolution without the user ever reviewing the rebase's actual conflict on that file.

I was unable to fully confirm within the tool budget the exact minimal attacker-only sequence (no local/manual steps) that drives this transition in a live repository, so this should be verified with an integration test/live session before treating it as confirmed-exploitable.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L134-141)
```typescript
    return {
      kind: 'rebase',
      currentTip,
      manualResolutions,
      targetBranch,
      originalBranchTip,
      baseBranchTip,
    }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L149-153)
```typescript
    return {
      kind: 'cherryPick',
      manualResolutions,
      targetBranchName,
    }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L170-175)
```typescript
  return {
    kind: 'merge',
    currentBranch,
    currentTip,
    manualResolutions,
  }
```

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

**File:** app/src/lib/status.ts (L65-84)
```typescript
/**
 * Determine if we have any conflict markers or if its been resolved manually
 */
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

**File:** app/test/unit/stores/updates/update-conflict-state-test.ts (L181-231)
```typescript
  describe('rebase conflicts', () => {
    it('returns null when no REBASE_HEAD file found', () => {
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
      const status = createStatus({ rebaseInternalState: null })
      const conflictState = updateConflictState(
        prevState,
        status,
        new TestStatsStore()
      )
      assert(conflictState === null)
    })

    it('returns a value when status has REBASE_HEAD set and conflict present', () => {
      const prevState = createState({
        conflictState: null,
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
        manualResolutions: new Map<string, ManualConflictResolution>(),
        baseBranchTip: 'another-sha',
        targetBranch: 'my-feature-branch',
        originalBranchTip: 'some-other-sha',
      })
    })
```
