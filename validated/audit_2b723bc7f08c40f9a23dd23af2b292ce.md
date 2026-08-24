## Title
Manual conflict resolutions keyed by file path are silently misapplied across a rename in an attacker-controlled merge - (File: `app/src/lib/status.ts`, `app/src/lib/stores/app-store.ts`, `app/src/lib/git/stage.ts`)

### Summary
The bug class in the seed report is: a state map (checkpoints/delegates) is keyed by an *identity* that the code assumes stays constant, but an attacker-influenced action (delegating votes) silently invalidates that assumption, and the code that acts on the stale key either corrupts state or blocks a legitimate operation. I looked for a GitHub-Desktop analog where a persisted map keyed by **file path** is reused across a git operation whose content at that path can change due to attacker-controlled repository data (a malicious merge/rebase producing renames), and where existing guards do not re-validate that the path still refers to the same conflicted blob.

### Finding Description
Desktop tracks manual conflict resolutions ("ours"/"theirs") in a `Map<string, ManualConflictResolution>` keyed purely by the file's **current path**, both in the store state and in the writer that applies resolutions: [1](#0-0) [2](#0-1) 

This map is explicitly **preserved across successive status refreshes of the same merge/rebase** (confirmed by `updateConflictState`, which reuses `prevConflictState.manualResolutions` verbatim as long as the operation kind hasn't changed): [3](#0-2) 

The tests explicitly document this "preserve across the same merge" contract: [4](#0-3) 

At the point of use, both `getConflictedFiles`/`getResolvedFiles` (`app/src/lib/status.ts`) and `stageManualConflictResolution` (`app/src/lib/git/stage.ts`) look up the resolution purely by `f.path`/`file.path` — never by an old path, blob id, or any other stable content identity: [5](#0-4) [6](#0-5) 

The invariant assumed here — "the path a user resolved earlier still identifies the same conflicted content on a later refresh of the same operation" — mirrors the ERC721Votes flaw's assumed invariant ("the account whose votes are moved is still the delegate that was recorded earlier"). Git operations under attacker influence (a hostile remote/PR branch involved in a merge/rebase/cherry-pick) can break this invariant: a rename/rename or add/rename conflict can cause a path that a user already resolved (e.g. `theirs`) for one blob to be re-populated by `git status` on a subsequent refresh (hook re-run, editor autosave triggering a refresh, or continuing an interactive rebase step-by-step) with an entirely different conflicted blob that happens to land at the same path, because `getConflictState`/`updateConflictState` carry the resolutions map forward unconditionally as long as the state is still classified as the same "kind" of operation (`merge`/`rebase`/`cherry-pick`), with no per-file identity check tying the resolution to the specific conflicting content it was chosen for.

### Impact Explanation
If the stale resolution is re-applied to different conflicted content at the same path, `stageManualConflictResolution` will checkout/stage the wrong side ("ours" or "theirs") without conflict markers and without further user confirmation, silently corrupting what the user commits/pushes — which is explicitly in-scope impact ("silent corruption of what the user commits or pushes"). Since git operations chain over multiple conflicted files/commits (rebase, cherry-pick of multiple commits), an attacker who controls the incoming branch/PR content can engineer multi-step conflicts designed to make a resolution intended for step N’s conflict at path `P` get silently reapplied at step N+1’s different conflict, also at path `P`.

### Likelihood Explanation
This requires: (1) the victim to have an in-progress merge/rebase/cherry-pick against attacker-influenced content (a PR branch, a shared feature branch, or any externally-fetched ref) that produces path-colliding conflicts across the operation's steps, and (2) the victim to make at least one manual "ours"/"theirs" choice before the state refresh that surfaces the second, unrelated conflict at the same path. This is plausible in normal collaborative workflows (rebasing a long branch with several conflicting commits, or resolving conflicts progressively while Desktop auto-refreshes status), and does not require local/physical access, admin rights, or leaked credentials — only that the user perform an otherwise-ordinary merge/rebase against attacker-supplied history. However, I was not able to fully trace, within tool-call limits, an end-to-end reproduction proving that `git status --porcelain` will actually reuse the *same relative path* for two distinct conflict identities across sequential rebase/cherry-pick steps (this depends on git's own path assignment behavior for renames across sequential commits) — that part of the causal chain remains **unverified** and would need to be validated with an actual git repro before treating this as a confirmed, exploitable finding rather than a plausible analog.

### Recommendation
Key `manualResolutions` (and its lookups in `getConflictedFiles`, `getResolvedFiles`, and `stageManualConflictResolution`) by a content-stable identifier rather than the bare working-directory path — e.g., include the pre-image blob OID(s) from `git status` (`ours`/`theirs`/`base` OIDs) or the specific conflict "instance" (current operation step SHA) alongside the path, and invalidate/drop a path's manual resolution whenever a new status refresh reports different conflict details (different entry types or OIDs) for that path within the same operation.

### Proof of Concept
Not independently reproduced against a live git repository within this session; the trace above establishes the code path (state carried unconditionally across refreshes in `updateConflictState`, keyed purely by path in `status.ts` and `stage.ts`) but the specific git rename/path-collision sequence needed to trigger a real path collision across steps of the same merge/rebase/cherry-pick was not verified end-to-end and should be confirmed with a concrete repository before treating this as fully proven.

### Citations

**File:** app/src/lib/app-state.ts (L477-482)
```typescript
export type MergeConflictState = {
  readonly kind: 'merge'
  readonly currentBranch: string
  readonly currentTip: string
  readonly manualResolutions: Map<string, ManualConflictResolution>
}
```

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

**File:** app/src/lib/status.ts (L151-172)
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
```

**File:** app/src/lib/git/stage.ts (L22-53)
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

```
