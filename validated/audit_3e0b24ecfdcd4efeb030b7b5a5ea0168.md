This confirms it: `IStashEntry.branchName` is stored only as a string (parsed from the reflog message) and the "Stashed Changes" section in the Desktop UI is surfaced only via `currentBranchStashEntry`, which requires the branch to be currently checked out (`git-store.ts:1227-1231`). `BranchPruner` deletes local branches automatically in the background, with no check for a Desktop-created stash entry tied to that branch name, unlike `_renameBranch` and `deleteLocalBranchAndUpstreamBranch`/`_deleteBranch`, which have no such stash-migration logic either.

### Title
Background branch auto-pruner silently deletes branches without checking for or preserving associated Desktop stash entries - (File: app/src/lib/stores/helpers/branch-pruner.ts)

### Summary
`BranchPruner.pruneLocalBranches` (`app/src/lib/stores/helpers/branch-pruner.ts:136-259`) runs automatically every 24 hours (background timer, `BackgroundPruneMinimumInterval`) and force-deletes any local branch that GitHub reports as merged, via `deleteLocalBranch` (`git branch -D`). Unlike `_renameBranch` (`app/src/lib/stores/app-store.ts:4996-5013`), which explicitly checks `gitStore.desktopStashEntries.get(branch.name)` and migrates the stash with `moveStashEntry` before renaming, the pruner performs no equivalent lookup before deleting a branch.

### Finding Description
Desktop associates stash entries with branches purely by a string embedded in the stash message (`!!GitHub_Desktop<branchName>`, see `createDesktopStashMessage` in `app/src/lib/git/stash.ts:135-138`), not by any git ref linkage. The UI only exposes a stash entry for restore through `GitStore.currentBranchStashEntry` (`app/src/lib/stores/git-store.ts:1227-1231`), which is keyed off the *currently checked out* branch's tip. Once `BranchPruner` deletes the local branch ref that a stash entry's `branchName` refers to, that branch can never be checked out again by that name, so `currentBranchStashEntry` can never match it again — the stash entry becomes practically unreachable through the normal Desktop UI (no "Stashed Changes" affordance will ever be shown for it), even though the underlying `refs/stash@{n}` object is technically still present in the reflog.

The pruning filter (`branchesReadyForPruning`, lines 210-227) checks `ReservedRefs`, recently-checked-out branches (2 weeks), worktree branches, and whether the branch is missing on the remote — but it never checks `gitStore.desktopStashEntries` for a matching branch name before calling `deleteLocalBranch`.

### Impact Explanation
This mirrors the report's core invariant violation: a background/automated process (analogous to `resolveChallenge` → `removeListing`) irrecoverably destroys value (the user's stash of uncommitted work) attached to an entity (a branch) without giving the owner (the Desktop user) a chance to retrieve it first. The user loses access to legitimate uncommitted work they stashed via Desktop, with no warning specific to the stash, only a generic background log line (`log.info(...Pruned branch...)`), which the user is unlikely to ever see.

### Likelihood Explanation
This requires the user to stash changes on a branch via Desktop (`createStashForCurrentBranch`), then not check out that branch again for 14+ days (or fewer, if the pruning timer window elapses) while a merge to the default branch happens on GitHub. This is a plausible, unprivileged, ordinary usage pattern (no attacker input needed to trigger the invariant break, though a malicious/careless collaborator merging and deleting the remote counterpart could accelerate it) — it doesn't require local/physical access, admin rights, or social engineering, and it's a real, silent corruption/loss of user data path within Desktop's own automation.

### Recommendation
- **Short term:** In `BranchPruner.pruneLocalBranches`, before calling `deleteLocalBranch`, check `gitStore.desktopStashEntries.get(branchName)` (mirroring `_renameBranch`'s logic) and either skip pruning branches with an associated stash, or migrate/rename the stash entry to a recoverable location (e.g., a synthetic branch reference or a dedicated "orphaned stashes" list surfaced in the UI) before removal.
- **Long term:** Decouple the "restore stash" UI affordance from requiring the exact branch to still exist/be checked out; allow orphaned Desktop stash entries (by original branch name) to be listed and restored from a general stash management view regardless of whether the source branch still exists.

### Proof of Concept
1. In Desktop, check out branch `feature-x`, make uncommitted changes, and stash them (`dispatcher.createStashForCurrentBranch`), creating a `refs/stash@{0}` entry with message `!!GitHub_Desktop<feature-x>`.
2. Push `feature-x`, get it merged into the repository's default branch on GitHub, and don't check it out again in Desktop for the required window.
3. Wait for/trigger `BranchPruner.runOnce()` (runs automatically every 4 hours check / 24 hour threshold). Because `feature-x` is now reported by GitHub as merged and hasn't been checked out recently, it passes the `branchesReadyForPruning` filter and `deleteLocalBranch(repository, 'feature-x')` executes, deleting `refs/heads/feature-x`.
4. The `refs/stash@{0}` entry with `branchName: 'feature-x'` remains in the reflog, but since `feature-x` no longer exists as a checked-out branch, `GitStore.currentBranchStashEntry` never returns it again, and the Desktop UI never surfaces a "Restore" option for it — the user's uncommitted work is effectively lost from the app's perspective. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/stores/helpers/branch-pruner.ts (L236-254)
```typescript
    for (const branchCanonicalRef of branchesReadyForPruning) {
      if (!branchCanonicalRef.startsWith(branchRefPrefix)) {
        continue
      }

      const branchName = branchCanonicalRef.substring(branchRefPrefix.length)

      if (options.deleteBranch) {
        const isDeleted = await gitStore.performFailableOperation(() =>
          deleteLocalBranch(this.repository, branchName)
        )

        if (isDeleted) {
          log.info(
            `[BranchPruner] Pruned branch ${branchName} ((was ${mergedBranches.get(
              branchCanonicalRef
            )}))`
          )
        }
```

**File:** app/src/lib/stores/app-store.ts (L4995-5013)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _renameBranch(
    repository: Repository,
    branch: Branch,
    newName: string
  ): Promise<void> {
    const gitStore = this.gitStoreCache.get(repository)
    await gitStore.performFailableOperation(async () => {
      await renameBranch(repository, branch, newName)

      const stashEntry = gitStore.desktopStashEntries.get(branch.name)

      if (stashEntry) {
        await moveStashEntry(repository, stashEntry, newName)
      }
    })

    return this._refreshRepository(repository)
  }
```

**File:** app/src/lib/stores/git-store.ts (L1223-1235)
```typescript
  /**
   * A GitHub Desktop created stash entries for the current branch or
   * null if no entry exists
   */
  public get currentBranchStashEntry() {
    return this._tip && this._tip.kind === TipState.Valid
      ? this._desktopStashEntries.get(this._tip.branch.name) || null
      : null
  }

  public get desktopStashEntries(): ReadonlyMap<string, IStashEntry> {
    return this._desktopStashEntries
  }
```

**File:** app/src/lib/git/stash.ts (L135-138)
```typescript
/** Creates a stash entry message that indicates the entry was created by Desktop */
export function createDesktopStashMessage(branchName: string) {
  return `${DesktopStashEntryMarker}<${branchName}>`
}
```
