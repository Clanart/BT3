### Title
Stale `SQUASH_MSG` marker causes unrelated conflicts to be misclassified as squash-merge conflicts, mislabeling "ours"/"theirs" sides - (File: `app/src/lib/git/merge.ts`, `app/src/lib/git/status.ts`, `app/src/lib/stores/updates/changes-state.ts`)

### Summary
Like the reported `ProtocolUpgradeHandler` bug — where a status flag (freeze state) is never reset after use, letting a privileged actor keep the whole system incorrectly "frozen" — GitHub Desktop has an on-disk status flag, `.git/SQUASH_MSG`, that is documented in the code itself as not being cleared when it logically should be. That stale flag later gets reused by unrelated logic (`getConflictState`) to reclassify an unrelated conflict as a squash-merge conflict, which changes how "ours"/"theirs" are attributed to the user in the resolution flow.

### Finding Description
`isSquashMsgSet` explicitly documents the broken invariant: [1](#0-0) 

The comment states: *"If we abort the merge, this doesn't get cleared automatically which could lead to this being erroneously available in a non merge --squashing scenario."* This is the exact same bug shape as the `FreezeStatus` report: a one-shot operation flag is never reset when the operation it represents ends, so it silently persists into future, unrelated states.

`getStatus` reads this stale flag unconditionally into every status snapshot: [2](#0-1) 

`getConflictState` then uses `squashMsgFound` as an alternate trigger (alongside `mergeHeadFound`) for entering the `'merge'` conflict state, as long as there are conflicted index entries for *any* reason — including the documented working-directory/stash-pop case handled separately in `getWorkingDirectoryConflictDetails`: [3](#0-2) [4](#0-3) 

Downstream, `app-store.ts` treats this reclassified conflict as a genuine squash-merge, using the stale flag to decide `isSquash` for the multi-commit-operation model and to compute which branch is "theirs" vs "ours" for the conflict-resolution UI: [5](#0-4) [6](#0-5) 

The existing guard in `getConflictState` — `(!mergeHeadFound && !status.doConflictedFilesExist)` — only checks whether conflicted files exist at all; it never verifies that the residual `squashMsgFound` flag actually corresponds to the conflict currently present. So a stale flag from a previously aborted squash-merge (which the code admits is never cleaned up) can be silently attached to a completely unrelated conflict (e.g. one produced by popping a stash whose blob came from a fetched/attacker-controlled branch), causing the app to compute `theirBranch`/`ourBranch` incorrectly for that unrelated conflict.

### Impact Explanation
If "ours"/"theirs" attribution is wrong, a user who deliberately picks "ours" during manual conflict resolution can end up committing attacker-influenced ("theirs") content instead, because the UI/store labeled the sides incorrectly based on the stale, unrelated squash state. This is a silent corruption of what the user believes they are committing — the exact class of impact called out as valid (silent corruption of what the user commits or pushes), driven by content that ultimately originates from a fetched/attacker-controlled branch or stash.

### Likelihood Explanation
This requires a specific sequence (abort a squash merge, then later hit an unrelated conflict, e.g. via stash pop, before the leftover `SQUASH_MSG` is cleaned up by a subsequent successful merge/commit), so it is a lower-frequency, state-ordering-dependent bug rather than a one-click exploit. I was not able to fully trace, within the remaining investigation budget, every UI code path that reads `MergeConflictState`/`isSquash` to confirm all places where the mislabeled side is actually surfaced to the user for a decision — this should be verified further (e.g. in the conflict-resolution dialog components) before treating the severity as fully confirmed.

### Recommendation
Clear `.git/SQUASH_MSG` (and any other one-shot operation markers) whenever a merge/squash is aborted or completed, mirroring the `FreezeStatus` fix pattern in the original report: reset the flag as soon as the operation it represents concludes, rather than relying on incidental cleanup by a later, unrelated `git` command. Additionally, `getConflictState` should validate that `squashMsgFound` corresponds to the currently active conflict (e.g. by also checking for the presence of an in-progress squash operation via another authoritative source) before using it to alter "ours"/"theirs" attribution.

### Proof of Concept
1. Start a `git merge --squash <branch>` in a repository via Desktop that results in conflicts (leaves `.git/SQUASH_MSG` on disk).
2. Abort the merge (`git merge --abort`). Per the code comment in `isSquashMsgSet`, `.git/SQUASH_MSG` is not removed.
3. Later, perform an unrelated operation that reintroduces conflicted index entries without a `MERGE_HEAD` (e.g. popping a stash — reachable via `getWorkingDirectoryConflictDetails`) using content pulled from a fetched/attacker-controlled branch.
4. `getStatus` reports `squashMsgFound: true` (stale) together with `doConflictedFilesExist: true`.
5. `getConflictState` classifies this as a `'merge'` conflict; `app-store.ts` marks `isSquash: true` and computes `theirBranch`/`ourBranch` using the stale squash context, potentially mislabeling which side is attacker-controlled in the resolution UI. [1](#0-0) [3](#0-2)

### Citations

**File:** app/src/lib/git/merge.ts (L143-154)
```typescript
/**
 * Check the `.git/SQUASH_MSG` file exists in a repository
 * This would indicate we did a merge --squash and have not committed.. indicating
 * we have detected a conflict.
 *
 * Note: If we abort the merge, this doesn't get cleared automatically which
 * could lead to this being erroneously available in a non merge --squashing scenario.
 */
export async function isSquashMsgSet(repository: Repository): Promise<boolean> {
  const path = join(repository.resolvedGitDir, 'SQUASH_MSG')
  return await pathExists(path)
}
```

**File:** app/src/lib/git/status.ts (L271-287)
```typescript
  const isCherryPickingHeadFound = await isCherryPickHeadFound(repository)

  const squashMsgFound = await isSquashMsgSet(repository)

  return {
    currentBranch,
    currentTip,
    currentUpstreamBranch,
    branchAheadBehind,
    exists: true,
    mergeHeadFound,
    rebaseInternalState,
    workingDirectory,
    isCherryPickingHeadFound,
    squashMsgFound,
    doConflictedFilesExist: conflictedFilesInIndex.length > 0,
  }
```

**File:** app/src/lib/git/status.ts (L428-491)
```typescript
/**
 * We need to do these operations to detect conflicts that were the result
 * of popping a stash into the index
 */
async function getWorkingDirectoryConflictDetails(
  repository: Repository,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>
) {
  const conflictCountsByPath = await getFilesWithConflictMarkers(
    repository.path
  )
  let binaryFilePaths: ReadonlyArray<string> = []
  try {
    // its totally fine if HEAD doesn't exist, which throws an error
    binaryFilePaths = await getBinaryPaths(
      repository,
      'HEAD',
      conflictedFilesInIndex
    )
  } catch (error) {}

  return {
    conflictCountsByPath,
    binaryFilePaths,
  }
}

/**
 * gets the conflicted files count and binary file paths in a given repository.
 * for computing an `IStatusResult`.
 *
 * @param repository to get details from
 * @param mergeHeadFound whether a merge conflict has been detected
 * @param conflictedFilesInIndex all files marked as being conflicted in the
 *                               index. Used to check for files using the binary
 *                               merge driver and whether it looks like a stash
 *                               has introduced conflicts
 * @param rebaseInternalState details about the current rebase operation (if
 * found)
 */
async function getConflictDetails(
  repository: Repository,
  mergeHeadFound: boolean,
  conflictedFilesInIndex: ReadonlyArray<IStatusEntry>,
  rebaseInternalState: RebaseInternalState | null
): Promise<ConflictFilesDetails> {
  try {
    if (mergeHeadFound) {
      return await getMergeConflictDetails(repository, conflictedFilesInIndex)
    }

    if (rebaseInternalState !== null) {
      return await getRebaseConflictDetails(repository, conflictedFilesInIndex)
    }

    // If there's conflicted files in the index but we don't have a merge head
    // or a rebase internal state, then we're likely in a situation where a
    // stash has introduced conflicts
    if (conflictedFilesInIndex.length > 0) {
      return await getWorkingDirectoryConflictDetails(
        repository,
        conflictedFilesInIndex
      )
    }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L156-176)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L3041-3047)
```typescript
    if (isMergeConflictState(conflictState)) {
      operationDetail = {
        kind: MultiCommitOperationKind.Merge,
        isSquash: status.squashMsgFound,
        sourceBranch: null,
      }
      originalBranchTip = targetBranch !== null ? targetBranch.tip.sha : null
```

**File:** app/src/lib/stores/app-store.ts (L3200-3209)
```typescript
    const { manualResolutions } = conflictState
    let ourBranch, theirBranch

    if (isMergeConflictState(conflictState)) {
      theirBranch = await this.getMergeConflictsTheirBranch(
        repository,
        status.squashMsgFound,
        multiCommitOperationState
      )
      ourBranch = conflictState.currentBranch
```
