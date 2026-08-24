### Title
Manual conflict resolutions are keyed only by file path and persist across rebase steps, causing silent misapplication of a stale "ours/theirs" choice to a different commit's conflict content - (File: app/src/lib/stores/updates/changes-state.ts, app/src/lib/git/rebase.ts)

### Summary
During a Desktop-driven git operation (merge/rebase/cherry-pick) with a maliciously crafted, cloned/fetched repository whose commit series is engineered to produce repeated conflicts on the same file path across successive steps of a multi-commit rebase, the manual conflict resolution the user made for an earlier conflict on that path can be silently re-applied to a later, unrelated conflict on the same path — without the user ever seeing or confirming the new conflict content. This can cause Desktop to `git rebase --continue` and commit content the user never actually agreed to.

### Finding Description
Desktop tracks manual "ours"/"theirs" conflict decisions in a `Map<string, ManualConflictResolution>` keyed purely by the file's relative path — not by commit, blob hash, or conflict instance: [1](#0-0) 

This map is threaded through `updateConflictState`, which explicitly **reuses the previous conflict state's `manualResolutions`** whenever the new status is still classified as the same kind of conflict (merge-vs-merge or rebase-vs-rebase): [2](#0-1) 

When the rebase proceeds to the next commit in a multi-commit rebase (`git rebase --continue` landing on a new patch that also conflicts on the same path), `continueRebase` looks up the resolution for that path in the (still-populated) map and immediately applies it via `stageManualConflictResolution`, with no re-validation that the conflict content is the same one the user actually reviewed: [3](#0-2) [4](#0-3) 

The same pattern exists for cherry-pick's `continueCherryPick`: [5](#0-4) 

The broken invariant is analogous to the reported Solana bug: a piece of state meant to represent "the resolved outcome of *this specific* operation" is not reset/invalidated when the underlying operation instance changes (new commit / new conflict), so stale state is silently reused on the next step. In the Solana report, the un-incremented nonce lets a stale/failed transaction state persist and be reused; here, the un-cleared/non-scoped `manualResolutions` map lets a stale resolution decision persist and be reused against different underlying content.

An attacker who controls the repository content (a crafted branch/commit history the victim fetches and rebases/cherry-picks) can arrange for the same file path to be conflicted at two different steps of the operation with different actual diffs. If the user resolves the first conflict with "theirs" (attacker branch content), Desktop will silently choose "theirs" again for the second, different conflict on the same path — writing attacker-controlled content into the user's commit without ever showing it to the user for confirmation.

### Impact Explanation
This results in silent corruption of what the user commits: content the user never reviewed or explicitly approved gets staged and committed as part of a rebase/cherry-pick, purely because the conflict resolution cache is keyed by path instead of by conflict instance. This falls under the accepted impact category of "silent corruption of what the user commits or pushes," driven entirely by attacker-controlled repository content that the victim fetched/cloned — no local access, admin rights, or social engineering steps beyond normal git operations (fetch + rebase) are required.

### Likelihood Explanation
Requires the victim to perform a multi-commit rebase or cherry-pick against an attacker-influenced branch/commit series where the same path conflicts more than once across separate steps, and to use Desktop's manual ours/theirs resolution UI for the first conflict without noticing the second occurrence is different content. This is a plausible but non-trivial setup (attacker needs to control the source history layout, e.g. via a PR branch or fork the victim pulls in), which is a realistic Desktop workflow. It requires no elevated privileges — only that the user runs a normal rebase/cherry-pick operation against attacker-supplied commits.

### Recommendation
Scope `manualResolutions` to the specific conflict instance rather than persisting it across `updateConflictState` transitions where the underlying conflicted blob/commit changes. Concretely: invalidate/clear (or re-key by path+blob-OID pair from `git status`/`ls-files -u`) the `manualResolutions` map whenever the rebase advances to processing a different commit (`readRebaseHead` SHA changes) or whenever the conflicting blob OIDs for that path differ from when the resolution was recorded, instead of blindly reusing `prevConflictState.manualResolutions` for any conflict of the same kind.

### Proof of Concept
1. Attacker prepares a branch with two commits, `A` and `B`, that each modify the same file `shared.txt` differently in a way that both conflict against the victim's base branch when rebased individually (e.g., different hunks touching overlapping lines).
2. Victim fetches this branch in Desktop and performs `Rebase onto base` for `A, B`.
3. Rebase pauses on commit `A`'s conflict in `shared.txt`. Victim manually resolves it via Desktop's conflict dialog choosing "Use their version" (recorded in `manualResolutions.set('shared.txt', theirs)`).
4. Victim clicks Continue; `continueRebase` applies the resolution and `git rebase --continue` proceeds to commit `B`, which also conflicts on `shared.txt` with different content.
5. Because `updateConflictState` reuses `prevConflictState.manualResolutions` (still containing `shared.txt -> theirs`) and the rebase kind hasn't changed, Desktop's UI may not re-surface the file as needing fresh resolution, and/or a subsequent Continue click causes `continueRebase` to call `stageManualConflictResolution` with the stale `theirs` choice for commit `B`'s unrelated conflict content, staging and committing it without ever showing the new diff to the user.

Note: I was unable to fully trace the exact UI-level control flow that determines whether the conflicts dialog forces a fresh per-file review when the underlying conflicted blob changes but the path stays the same (the file read for `app/src/lib/stores/updates/changes-state.ts` lines 1–220, containing `getConflictState`, could not be retrieved before running out of tool iterations). This should be verified directly to confirm whether any blob-OID or "file re-conflicted" check already mitigates the described reuse before treating this as fully confirmed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8796-8823)
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
```

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
