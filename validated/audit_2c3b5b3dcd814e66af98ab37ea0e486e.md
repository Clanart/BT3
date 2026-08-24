## Title
Stale working-directory snapshot lets `_applyCopilotConflictResolutions` silently overwrite a user's already-resolved file - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The reported pattern — a guard condition is evaluated against one value, but the code then commits a *different*, potentially-stale value once the guard passes — has a direct analog in Desktop's Copilot conflict-resolution write path. `_applyCopilotConflictResolutions` decides whether to overwrite a conflicted file on disk using a `state` snapshot that was captured once, at function entry, rather than a fresh read of the file/working-directory status taken immediately before the write.

### Finding Description
`_applyCopilotConflictResolutions` grabs `const state = this.repositoryStateCache.get(repository)` a single time at the top of the method [1](#0-0) , and then loops over `copilotResolutions`, using that same cached `state.changesState.workingDirectory.files` snapshot for every iteration to decide whether a file is still conflicted: [2](#0-1) 

The comment explicitly documents the intent of the check — "If the user resolved this file externally … while the result dialog was open, git status will report it with no remaining conflict markers. Overwriting it with Copilot's stored content would silently clobber their work, so skip it" — but the implementation checks a `RepositoryStateCache` snapshot rather than performing a fresh, synchronous re-check (e.g., `git status`) right before the `writeFile` call. That cache entry is only refreshed by the app's periodic/background status-refresh flow (`onGitStoreUpdated`, `updateChangesStashDiff`, etc. — see `app/src/lib/stores/app-store.ts:3619-3679`), not deterministically before this function executes. Because the Copilot resolution round trip preceding this call can run well past a minute (the code tracks `copilotConflictResolutionOver15sCount` through `...Over120sCount` at [3](#0-2) ), and the user can edit/resolve the file externally at any point up to the instant they click "Continue Merge", there is a realistic window where the on-disk file is already resolved but the cached `state` object still reports it as conflicted with unresolved markers.

The content that gets written, `resolution.resolvedContent`, is itself derived from an even earlier snapshot: it was reassembled via `reassembleResolutions`/`reassembleResolvedFile` from `ctx.rawContent` gathered *before* the Copilot API call was made [4](#0-3) . So both the guard (whether to write) and the payload (what to write) can be stale relative to the true current disk state, exactly mirroring the reported invariant break: a check based on one input is used to justify committing a different, no-longer-accurate value.

### Impact Explanation
If the stale-status guard passes incorrectly, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` executes and the result is immediately staged with `git add` [5](#0-4) . This silently discards the user's own conflict resolution and replaces it with Copilot's earlier-computed (and possibly obsolete) content, which is then committed/pushed as part of the merge/rebase/cherry-pick — a silent corruption of what the user commits, without any error or warning surfaced.

### Likelihood Explanation
This requires no attacker action beyond normal usage of a repository that triggers merge conflicts (which can be shaped by content in a remote/PR the user merges). The staleness window is realistic given Copilot resolution can take over 60–120 seconds and the working-directory cache is refreshed asynchronously/opportunistically rather than being force-refreshed right before the decisive write.

### Recommendation
Before writing each resolved file, re-verify the file's current on-disk conflict status directly (e.g., re-run `getStatus`/`git status` for that path, or hash/compare `ctx.rawContent` against the file's current content) rather than trusting the `RepositoryStateCache` snapshot captured at function entry. Only overwrite when the fresh check confirms the file is still in the exact conflicted state that was used to compute the resolution.

### Proof of Concept
1. Create a merge conflict, invoke "Resolve with Copilot", and let the request run (intentionally slow/large diff to extend duration).
2. While the loading/result dialog is active, manually resolve the conflicted file in an external editor and save it, but do not trigger a Desktop status refresh (e.g., do it in a very tight window right as you click "Continue Merge", or in an environment where the file watcher is briefly debounced/delayed).
3. Click "Continue Merge" — `_applyCopilotConflictResolutions` reads the stale cached `state`, sees the file still marked as containing unresolved conflicts, and overwrites the user's manually resolved file with the earlier Copilot-computed `resolvedContent`, then stages it via `git add`.
4. The user's actual resolution is silently lost and the Copilot content (based on the pre-resolution snapshot) is committed instead.

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

**File:** app/src/lib/stores/app-store.ts (L7169-7176)
```typescript
  public async _applyCopilotConflictResolutions(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L7241-7267)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
```
