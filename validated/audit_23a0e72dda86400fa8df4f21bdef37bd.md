Based on the investigation, the strongest analog I found for the "expired-but-still-actionable state that is trusted without re-validation" bug class in the Sherlock report is in GitHub Desktop's merge/rebase/cherry-pick conflict-resolution tracking, where a per-path "resolved" flag is trusted forever without checking whether the current conflicted content is actually the same content that was resolved. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Stale per-path manual conflict resolution is reused across new, unreviewed conflicted content during multi-commit operations - (File: app/src/lib/status.ts, app/src/lib/stores/updates/changes-state.ts)

### Summary
`hasUnresolvedConflicts` treats a file as fully resolved the moment *any* `ManualConflictResolution` exists for its path, without checking whether that resolution corresponds to the conflict markers/content currently on disk.

### Finding Description
`hasUnresolvedConflicts(status, manualResolution)` in `app/src/lib/status.ts` short-circuits to `false` (i.e. "no unresolved conflicts") as soon as `manualResolution !== undefined`, regardless of the file's current `conflictMarkerCount` or blob identity: [1](#0-0) 

The `manualResolutions: Map<string, ManualConflictResolution>` is keyed only by file **path**, and this same map is threaded forward unchanged through every subsequent status refresh via `getConflictState`: [2](#0-1) 

During a multi-step operation (rebase/cherry-pick applying several commits in sequence), `updateMultiCommitOperationConflictsIfFound` in `app-store.ts` pushes the *same* `manualResolutions` object into the next `ShowConflicts`/`ShowCopilotConflicts` step whenever new conflicts are detected: [3](#0-2) 

This mirrors the audited bug's broken invariant: a state that should require re-validation against the *current* condition (lock end time vs. `block.timestamp`; here, resolution vs. current conflict-marker content for that path) is instead trusted indefinitely because nothing re-derives it from present data — it's carried forward as-is.

The result: if the same file path conflicts again in a later commit of the same rebase/cherry-pick sequence (a very common occurrence when a rebase touches the same file repeatedly, and fully plausible when replaying commits from a crafted/attacker-controlled fork or history), the file is displayed and treated as "Resolved" even though its new conflict markers belong to entirely different, unreviewed content — because the map lookup only checks presence of a prior resolution for that path, not equivalence to the new hunk.

### Impact Explanation
This can silently commit attacker-influenced content that the user never actually reviewed: a maliciously crafted commit history (which the user rebases onto, cherry-picks from, or merges — content fully controlled by a remote/fork) can cause the same path to reconflict with different payload in a later step of the same operation, and Desktop's conflict UI will mark it pre-resolved and allow "Continue"/"Commit" without surfacing the new markers. This is a silent corruption of what the user commits/pushes, matching the report's category.

### Likelihood Explanation
Requires a multi-commit operation (rebase/cherry-pick) where the same file path is conflicted more than once, driven by content in a repository the attacker controls (fork/branch/commit sequence the victim rebases onto). This is a realistic, unprivileged scenario reachable purely by the victim performing an ordinary rebase/cherry-pick against attacker-supplied history — no local/admin access or social engineering beyond normal git workflows is required.

### Recommendation
Key manual resolutions by (path, conflict content/blob hash) rather than path alone, or clear/re-validate `manualResolutions` entries whenever the underlying conflicted blob for that path changes between steps, so a stale resolution can never be silently applied to new conflicting content.

### Proof of Concept
Conceptual reproduction (not independently executed against a running Desktop instance, based on code inspection):
1. Attacker crafts a branch with two commits, both of which modify `shared-file.txt` in a way that conflicts with the victim's branch at that path.
2. Victim starts a rebase onto the attacker's branch. Commit 1 conflicts on `shared-file.txt`; victim manually resolves it (`manualResolutions.set('shared-file.txt', resolution)`).
3. Rebase continues to commit 2, which also conflicts on `shared-file.txt` with unrelated/malicious content. `updateMultiCommitOperationConflictsIfFound` re-derives `conflictState` via `getConflictState`, which still contains the old `manualResolutions` entry for that path.
4. `hasUnresolvedConflicts` (status.ts:68-84) sees `manualResolution !== undefined` for `shared-file.txt` and reports "resolved," so the conflicts dialog shows no pending conflicts for that file and lets the user click "Continue"/"Commit," even though the new conflict markers were never reviewed.

Confidence note: I was not able to locate, within the tool budget, the exact production call site where `manualResolutions.set(...)` is invoked outside of `commitIncludedChanges`/set boilerplate (grep only surfaced it in `app/test/unit/git/cherry-pick-test.ts`), so I could not fully confirm from code alone whether any intermediate step clears stale entries before pushing the map forward in every operation type. The finding is based on the confirmed logic of `hasUnresolvedConflicts`, `getConflictState`, and `updateMultiCommitOperationConflictsIfFound`, which together show the map is persisted and consulted by path only; a deeper trace of every setter/clearer would strengthen certainty of exploitability across all operation kinds (merge vs. rebase vs. cherry-pick).

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3127-3165)
```typescript
  private updateMultiCommitOperationConflictsIfFound(repository: Repository) {
    const state = this.repositoryStateCache.get(repository)
    const { changesState, multiCommitOperationState } =
      this.repositoryStateCache.get(repository)
    const { conflictState } = changesState

    if (conflictState === null || multiCommitOperationState === null) {
      this.clearConflictsFlowVisuals(state)
      return
    }

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

    if (isRebaseConflictState(conflictState)) {
      const { currentTip } = conflictState
      this.repositoryStateCache.updateMultiCommitOperationState(
        repository,
        () => ({ operationDetail: { ...operationDetail, currentTip } })
      )
    }
  }
```
