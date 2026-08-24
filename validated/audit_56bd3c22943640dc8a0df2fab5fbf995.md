### Title
Stale manual conflict resolutions are silently reused across merge sessions when a merge is aborted and restarted with a different branch - ([File: app/src/lib/stores/updates/changes-state.ts])

### Summary
This is the closest verifiable Desktop analog to the Sherlock M-10 pattern: a piece of session state (`shutdownVotes` in the Solidity report) that is never reset when a process ends, so it silently corrupts/blocks the *next* invocation of the same process. In Desktop, the analogous state is the `manualResolutions` map inside `ConflictState`, tracked in `updateConflictState()`.

### Finding Description
`updateConflictState()` carries the `manualResolutions` map forward from the previous `ConflictState` any time the previous and new state are both of the same conflict kind (`merge` or `rebase`): [1](#0-0) 

This is intentional for the common case of consecutive status refreshes within the *same* merge/rebase, so the user's per-file "ours/theirs" choices aren't lost between polls. However, the function detects when the underlying merge source branch has changed (`branchNameChanged`, meaning the previous merge was aborted and a new one started) but only records a telemetry stat — it never clears `manualResolutions`: [2](#0-1) 

So if a user aborts a merge (e.g., via `git merge --abort` outside Desktop, or an external tool) and then merges a *different* branch — one an attacker controls (e.g., a shared/attacker-pushed branch) — before Desktop observes an intermediate "no conflict" status, any file path that was manually resolved in the first, unrelated merge retains that resolution (`ManualConflictResolution.ours`/`.theirs`) in the new merge's `ConflictState.manualResolutions`, keyed only by file path. The new merge's UI can then treat that file as already resolved with the old choice, without ever showing the attacker-controlled conflicting content to the user for review.

The `MergeConflictState`/`RebaseConflictState` types show `manualResolutions` is a plain `Map<string, ManualConflictResolution>` keyed by path with no session/commit identifier tying a resolution to the specific merge it was made in: [3](#0-2) [4](#0-3) 

Existing guard: only the `currentTip`/`currentBranch` change is checked for statistics purposes; there is no code path that calls `.clear()` on `manualResolutions` or drops the field when `branchNameChanged` is detected, so it does not stop stale resolutions from propagating into the new session.

### Impact Explanation
If exploited, this results in silent corruption of what the user commits: a file conflict introduced by a new (potentially attacker-controlled) merge source is auto-resolved using a decision the user made for an entirely different merge, without giving the user a chance to inspect the new conflicting content. This falls under "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Likelihood is moderate-to-low. It requires: (1) the user or an external process to abort an in-progress merge without Desktop observing an intervening "no conflict" refresh, and (2) a second merge (from an attacker-influenced branch) to reintroduce a conflict on the exact same file path. This is a plausible but non-trivial sequence, and I was not able to fully trace every code path that might reset `conflictState`/`manualResolutions` on explicit "Abort merge" clicks within Desktop itself (e.g., `dispatcher.abortMerge`) versus external aborts, so confidence that Desktop's own in-app abort flow leaves this state uncleared is not 100% verified — this analysis is based on `updateConflictState`'s logic as read, not a full runtime trace.

### Recommendation
When `branchNameChanged` (or the equivalent "this is a new merge/rebase session, not a continuation") is detected in `performEffectsForMergeStateChange`/`performEffectsForRebaseStateChange`, reset `manualResolutions` to a fresh empty `Map` rather than reusing the previous one, mirroring the Sherlock recommendation of resetting the stale variable once its owning session is truly finished.

### Proof of Concept
Conceptual reproduction (not fully verified end-to-end due to tool limits):
1. Start a merge of branch `feature-A` (attacker-controlled) which conflicts on `file.txt`; manually resolve it as "ours" — `manualResolutions` now has `{ 'file.txt': ours }`.
2. Abort the merge outside of Desktop (e.g., `git merge --abort` in a terminal) before Desktop's next status refresh reflects the abort.
3. Immediately merge branch `feature-B` (different attacker-controlled content) which also conflicts on `file.txt`.
4. Because `updateConflictState` sees both old and new state as `kind: 'merge'` and only checks `branchNameChanged` for stats, the stale `manualResolutions` entry for `file.txt` (`ours`) carries over, so Desktop treats `file.txt` as already resolved with the old choice — the user never reviews `feature-B`'s actual conflicting content for that file before it is committed. [5](#0-4)

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L178-198)
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
```

**File:** app/src/lib/stores/updates/changes-state.ts (L263-298)
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
```

**File:** app/src/lib/app-state.ts (L477-482)
```typescript
export type MergeConflictState = {
  readonly kind: 'merge'
  readonly currentBranch: string
  readonly currentTip: string
  readonly manualResolutions: Map<string, ManualConflictResolution>
}
```

**File:** app/src/lib/app-state.ts (L494-522)
```typescript
export type RebaseConflictState = {
  readonly kind: 'rebase'
  /**
   * This is the commit ID of the HEAD of the in-flight rebase
   */
  readonly currentTip: string
  /**
   * The branch chosen by the user to be rebased
   */
  readonly targetBranch: string
  /**
   * The branch chosen as the baseline for the rebase
   */
  readonly baseBranch?: string

  /**
   * The commit ID of the target branch before the rebase was initiated
   */
  readonly originalBranchTip: string
  /**
   * The commit ID of the base branch onto which the history will be applied
   */
  readonly baseBranchTip: string
  /**
   * Manual resolutions chosen by the user for conflicted files to be applied
   * before continuing the rebase.
   */
  readonly manualResolutions: Map<string, ManualConflictResolution>
}
```
