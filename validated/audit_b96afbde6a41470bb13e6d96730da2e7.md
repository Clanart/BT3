### Title
Stale per-path manual conflict resolutions carry across sequential rebase/cherry-pick steps and get silently re-applied to unrelated conflicts - ([File: app/src/lib/stores/updates/changes-state.ts])

### Summary
`updateConflictState` (`app/src/lib/stores/updates/changes-state.ts`) carries the `manualResolutions` `Map<string, ManualConflictResolution>` forward from the previous `conflictState` any time a non-null `conflictState` of the same kind (`merge` or `rebase`) persists, keyed only by file `path`. During a multi-step operation (rebase across several commits, or a cherry-pick of several commits) the `conflictState` stays non-null and of the same kind across each step while `currentTip`/`originalBranchTip` change, but the `manualResolutions` map is never re-derived or pruned against the *new* step's actual conflicted paths.

### Finding Description
`getConflictState` builds each new `ConflictState` from `manualResolutions` inherited unchanged from `prevConflictState`: [1](#0-0) [2](#0-1) 

The map is only reset to `new Map()` when `prevConflictState` is `null`, i.e. when there was no conflict at all before this status refresh. It is not cleared when the rebase/cherry-pick advances from one commit to the next (which changes `currentTip`/`originalBranchTip` but keeps `conflictState.kind` the same, so the same map object is reused).

That inherited map is later trusted verbatim by the operation-continuation code, which resolves any path present in the map by checking out `--ours`/`--theirs` and staging it, without verifying the resolution was chosen for *this* conflict occurrence of that path: [3](#0-2) [4](#0-3) [5](#0-4) 

This mirrors the report's broken invariant: an array/map that shrinks or changes composition ("newDistros.length" being smaller) but whose stale entries are never deleted, so old entries silently continue to apply to a different, newer context. Here the "context" is the specific conflict instance for a path at a given rebase/cherry-pick step; the "entry" is the `path -> ManualConflictResolution` pair, which is never invalidated when the underlying `currentTip`/commit changes.

### Impact Explanation
An attacker who controls the remote/repository content (a malicious upstream branch being rebased onto, or a series of commits being cherry-picked) can construct a sequence of commits where the *same file path* conflicts on step 1 and step 2, but with unrelated content each time. If the user resolves the first conflict as "theirs" for that path, the resolution is silently reapplied to the second, unrelated conflict on the same path — without prompting the user, and even though the second conflict's actual git status is different (`GitStatusEntry.UpdatedButUnmerged` etc. for different blobs). This can cause the user to unknowingly commit/push attacker-favored content on a step they never reviewed, i.e. silent corruption of what the user commits — matching the same class of harm called out in the seed report (functions behaving "unintuitively" on stale state and moving things "in unexpected ways").

### Likelihood Explanation
This requires only an attacker-controlled sequence of git history (a rebase target branch or a set of cherry-picked commits) that repeatedly touches the same path with real merge conflicts, and a user resolving via the Desktop UI once per rebase/cherry-pick run using "Resolve using..." on the manual-conflict dropdown, which is normal usage for multi-commit rebase/cherry-pick with conflicts. No local/admin access, no malware, and no unnatural steps are needed beyond normal conflict resolution during a rebase involving attacker-supplied commits — a scenario Desktop explicitly supports (rebasing onto a remote/PR branch).

### Recommendation
Scope `manualResolutions` to the specific conflict occurrence instead of the file path alone — e.g., key resolutions by `(path, currentTip or rebaseCurrentCommit)`, or explicitly clear/prune the `manualResolutions` map whenever `currentTip`/`originalBranchTip` (i.e., the commit being applied) changes within `updateConflictState`, mirroring how the underlying "distributions" bug was fixed by deleting stale entries rather than leaving them to be picked up implicitly by later loops.

### Proof of Concept
1. Attacker prepares a branch/PR with commits `A` and `B`, both of which modify `file.txt` at the same path but with unrelated content on each commit relative to the user's branch, guaranteeing a conflict on `file.txt` at both rebase steps.
2. User rebases their branch onto the attacker branch via Desktop. Step 1 (commit `A`) conflicts on `file.txt`; user manually resolves via the UI (`ManualConflictResolution.theirs`), calling `dispatcher.updateManualConflictResolution` → `_updateManualConflictResolution` in `app-store.ts`, which stores `('file.txt', theirs)` in `conflictState.manualResolutions`.
3. User continues the rebase (`continueRebase`); step 1 completes correctly.
4. Step 2 (commit `B`) also conflicts on `file.txt` (unrelated diff), but `updateConflictState`/`getConflictState` reuses the same `manualResolutions` map from step 1 because `prevConflictState` (kind `rebase`) is not null.
5. When `continueRebase` runs for step 2, it iterates `manualResolutions`, finds `file.txt`, and calls `stageManualConflictResolution(repository, file, ManualConflictResolution.theirs)` automatically — checking out and staging "theirs" for the new, unrelated conflict without ever showing it to the user.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L121-141)
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

**File:** app/src/lib/git/rebase.ts (L443-458)
```typescript
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
```

**File:** app/src/lib/git/cherry-pick.ts (L389-399)
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
```

**File:** app/src/lib/git/stage.ts (L22-62)
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

  switch (chosen) {
    case GitStatusEntry.Deleted:
      return removeConflictedFile(repository, file)
    case GitStatusEntry.Added:
    case GitStatusEntry.UpdatedButUnmerged:
      return addConflictedFile(repository, file)
    default:
      assertNever(chosen, 'unaccounted for git status entry possibility')
  }
```
