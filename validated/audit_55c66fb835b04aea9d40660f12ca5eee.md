### Title
Stale conflict-status snapshot lets Copilot overwrite externally-resolved merge conflicts, silently corrupting what gets committed - (File: app/src/lib/stores/app-store.ts)

### Summary
The Anvil report's bug class is: a value cached in memory (`creditedTokenAmount`) is not refreshed after an intervening operation (a partial redemption) before it is used to make a security-critical decision (liquidation eligibility), letting the system act on stale state and silently violate an invariant. The Desktop analog is `AppStore._applyCopilotConflictResolutions`, which uses a single, possibly-stale `IRepositoryState` snapshot to decide whether to overwrite a conflicted file with an AI-generated resolution, even though the SDK call that produced that resolution can run for well over 100 seconds - long enough for the on-disk conflict state to change without Desktop re-querying `git status` before the write.

### Finding Description
`_applyCopilotConflictResolutions` is invoked when the user clicks "Continue" after Copilot has resolved merge/rebase/cherry-pick conflicts: [1](#0-0) 

It captures the repository state once at function entry: [2](#0-1) 

Then, for every path Copilot resolved, it decides whether to overwrite the working directory file based on `onDiskFile.status` taken from that single captured snapshot: [3](#0-2) 

The comment explicitly documents the invariant this check is meant to protect - "if the user resolved this file externally ... git status will report it with no remaining conflict markers. Overwriting it with Copilot's stored content would silently clobber their work" - but the guard relies on `state.changesState.workingDirectory.files`, i.e., whatever `git status` last reported into the `repositoryStateCache`, not a fresh read taken immediately before `writeFile`. The Copilot resolution flow that populates `copilotResolutions` can run for a long time (the code tracks buckets up to and beyond two minutes): [4](#0-3) 

During that window (SDK thinking time, or the interval between the result dialog being shown and the user clicking "Continue"), git status is not guaranteed to be re-polled, so `onDiskFile.status.conflictMarkerCount`/`hasUnresolvedConflicts` used in the skip-check can be stale relative to the file actually on disk at write time - the same "value not updated before it's consulted" defect as `_markLOCPartiallyLiquidated` failing to update `creditedTokenAmount` before `_calculateLiquidationContext` reads it.

### Impact Explanation
If the guard's snapshot is stale, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` unconditionally overwrites the working-directory file with content generated earlier from conflict markers, and the result is `git add`-ed and staged: [5](#0-4) 

Because the underlying `resolution.resolvedContent` is derived by an LLM from both sides of a merge - content that can originate entirely from an attacker-controlled branch/remote that was fetched and merged - this is a path by which attacker-influenced conflict content silently ends up being committed and later pushed without the user reviewing the actual bytes written, or a legitimate manual fix made by the user in the interim is silently discarded. This matches the "silent corruption of what the user commits or pushes" impact category: the user believes they are continuing with a reviewed resolution, but the file actually staged does not correspond to the live disk/user intent.

### Likelihood Explanation
This requires no local/privileged access and no unnatural user steps: it is triggered by the completely ordinary Desktop workflow of merging/rebasing/cherry-picking a branch that has conflicts and choosing to use the built-in Copilot conflict resolution feature, which is exposed to any content coming from a cloned/fetched, attacker-influenced branch. The only variable is timing - the longer the SDK resolution takes (the code itself anticipates runs over 120 seconds) or the longer the result dialog stays open before the user clicks Continue, the larger the window during which the cached `workingDirectory.files` snapshot can diverge from the real on-disk conflict-marker count.

### Recommendation
- Short term: before writing `resolution.resolvedContent` for a given path, re-read the current on-disk file content (or re-run a scoped `git status`) instead of relying on the `IRepositoryState` snapshot captured when `_applyCopilotConflictResolutions` began, and skip/prompt if the live conflict-marker count differs from what was used to compute the resolution.
- Long term: treat the entire Copilot-conflict-resolution pipeline the way the LOC review recommended treating partial redemptions - audit every code path that consults cached working-directory/conflict state across an `await` boundary, and add invariant checks (e.g., hashing the conflicted content that was sent to Copilot and refusing to apply a resolution if the on-disk file no longer matches that hash) plus fuzz/property tests that interleave external file edits with in-flight Copilot resolutions.

### Proof of Concept
1. Attacker crafts a branch/PR with conflicting content designed to take a long time to resolve or to be resolved differently than the user expects, and gets the victim to fetch/merge it in Desktop.
2. Victim starts a merge, hits conflicts, and clicks "Resolve with Copilot"; the SDK call runs (potentially 30–120+ seconds per the app's own instrumentation) while computing `resolution.resolvedContent` from the original conflicted markers, as tracked in [4](#0-3) .
3. While Copilot is thinking (or while the result dialog is open), the victim manually fixes the conflict themselves in an editor (or a background process changes the file), removing all conflict markers - a legitimate, expected workflow explicitly anticipated by the code's own comment at [6](#0-5) .
4. If `repositoryStateCache` has not been refreshed with a fresh `git status` between that manual edit and the click on "Continue Merge," the stale `onDiskFile.status` snapshot used in `_applyCopilotConflictResolutions` [7](#0-6)  fails to detect the external resolution, and the victim's fix is silently overwritten by `writeFile` and staged via `git add` [5](#0-4) , after which the merge/rebase/cherry-pick is completed and committed with attacker-influenced content the user never reviewed.

Note: I was not able to fully confirm from the indexed code whether some other code path forces a `git status` refresh immediately before `_applyCopilotConflictResolutions` runs (which would narrow or close this window); this would need to be verified by tracing every call site of `_loadStatus`/`refreshChangesSection` relative to the Copilot conflict-resolution flow in a full checkout, since the indexer does not guarantee complete file coverage.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7094-7106)
```typescript
      const elapsedSeconds = (performance.now() - resolveStartTime) / 1000
      if (elapsedSeconds > 15) {
        this.statsStore.increment('copilotConflictResolutionOver15sCount')
      }
      if (elapsedSeconds > 30) {
        this.statsStore.increment('copilotConflictResolutionOver30sCount')
      }
      if (elapsedSeconds > 60) {
        this.statsStore.increment('copilotConflictResolutionOver60sCount')
      }
      if (elapsedSeconds > 120) {
        this.statsStore.increment('copilotConflictResolutionOver120sCount')
      }
```

**File:** app/src/lib/stores/app-store.ts (L7169-7182)
```typescript
  public async _applyCopilotConflictResolutions(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { copilotResolutions, step } = multiCommitOperationState
    if (copilotResolutions === null || copilotResolutions.length === 0) {
      return
    }

```

**File:** app/src/lib/stores/app-store.ts (L7241-7268)
```typescript
      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
