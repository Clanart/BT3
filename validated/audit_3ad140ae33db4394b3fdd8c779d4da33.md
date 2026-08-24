### Title
Stale in-memory `changesState` snapshot allows Copilot merge-conflict resolution to silently overwrite externally-resolved file content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions()` decides whether to overwrite a conflicted file on disk by comparing against a `changesState.workingDirectory.files` snapshot that was captured once at function entry, rather than re-reading the current git status at write time. This mirrors the reported Solidity bug class: a value that is supposed to represent "the current state" (`feeGrowthInside*LastX128` in the report; here, the conflict-marker state of a file) is actually a stale cached copy that does not reflect state changes that occurred between when the cache was populated and when it is consumed to gate a state-changing action.

### Finding Description
`_startCopilotConflictResolution()` [1](#0-0)  kicks off a potentially long-running AI conflict-resolution call (the code itself tracks buckets for resolutions taking over 15s/30s/60s/120s [2](#0-1) ) and stores the returned `copilotResolutions` in `multiCommitOperationState`, explicitly deferring file writes until the user clicks "Continue Merge" [3](#0-2) .

When the user clicks "Continue Merge", `_applyCopilotConflictResolutions()` runs and grabs a single snapshot of repository state up front: [4](#0-3) 

For each resolution it explicitly tries to guard against the user having resolved the conflict externally in the meantime: [5](#0-4) 

The comment states the intent plainly: *"If the user resolved this file externally... while the result dialog was open, git status will report it with no remaining conflict markers. Overwriting it with Copilot's stored content would silently clobber their work, so skip it."* However, the check is performed against `state.changesState.workingDirectory.files` — the same `state` object captured at the very top of the function via `this.repositoryStateCache.get(repository)` — not a freshly invoked `getStatus()`/`_loadStatus()` call. `changesState` is only updated when the app's file-watcher-driven status refresh happens to fire; it is not guaranteed to reflect edits the user made to the working directory in the seconds immediately preceding "Continue Merge" being clicked. If the cached conflict status for a file is stale (still shows unresolved conflict markers even though the user just finished editing the file on disk), the guard fails to trigger, and `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` unconditionally overwrites the user's just-completed manual resolution: [6](#0-5) 

The corrupted value is effectively the same class as the report's stale `feeGrowthInside*LastX128`: a cached "current state" field (`onDiskFile.status` derived from a stale `changesState` snapshot) that is trusted as authoritative when making a decision that mutates persistent state (writing to the file that will subsequently be staged and committed via `git add`, line 7262-7267), instead of re-querying the authoritative source (`git status`) immediately before acting.

### Impact Explanation
This can result in silent corruption of what the user commits: content the user believed they had manually and correctly resolved in a merge conflict can be silently discarded and replaced by the AI-generated resolution, without any warning, error, or diff review, because the safety check that is supposed to detect and prevent exactly this scenario relies on stale state. A repository (or fork/PR) crafted by an untrusted party to produce numerous or complex conflicts — increasing Copilot resolution latency (which the code itself tracks as commonly exceeding 15–120+ seconds) — widens the window during which the user's own concurrent manual edits can be clobbered by stale-cache-driven overwrites, and the resulting bad merge commit can then be pushed upstream.

### Likelihood Explanation
Moderate-to-low. The race requires: (1) a merge/rebase with Copilot-assisted conflict resolution in progress, (2) the user editing a conflicted file externally while the result dialog is open, and (3) the app's cached `changesState` not having refreshed to reflect that edit by the time "Continue Merge" is clicked. Since `changesState` refresh timing depends on filesystem-watcher events and background status polling rather than being synchronized with the moment of the write, this window realistically exists, especially for longer-running Copilot resolutions as evidenced by the app's own timing telemetry. Existing guards (the `hasUnresolvedConflicts`/`isConflictedFileStatus` check) do not close this gap because they consult the same stale snapshot rather than the live filesystem/git-index state.

### Recommendation
Before writing any Copilot-resolved file content in `_applyCopilotConflictResolutions()`, re-invoke `getStatus()`/`gitStore.loadStatus()` to obtain a fresh working-directory status immediately prior to the per-file conflict check, rather than reusing the `state` object captured at function entry. Apply the "already externally resolved" skip logic against this freshly-fetched status so it reflects any edits the user made during the (potentially long) Copilot resolution and review period.

### Proof of Concept
1. Start a merge/rebase that produces a conflicted file, and enable Copilot conflict resolution.
2. While Copilot's resolution is running (or right after it completes and the result dialog is shown), manually edit the conflicted file to resolve it correctly and save it, without triggering a status refresh in Desktop (e.g., edit rapidly, or in an environment where the file-watcher/status refresh has some latency).
3. Click "Continue Merge" before the app's cached `changesState.workingDirectory.files` has been refreshed to reflect your manual edit.
4. Observe that `_applyCopilotConflictResolutions()` finds `onDiskFile` in the stale `state.changesState.workingDirectory.files` still marked as having unresolved conflicts, so the skip-guard at lines 7250-7256 does not trigger, and it overwrites your manually resolved file with `resolution.resolvedContent`, which then gets staged and committed via `git add`.

Note: I was unable to fully trace the exact timing/synchronization guarantees between the file-watcher-driven `changesState` refresh and UI actions like "Continue Merge" within the indexed portion of the codebase; confirming the precise race window would require tracing the file-watcher and `RepositoryStateCache` update paths in a running Desktop session (e.g., via a Devin session with full repo/runtime access).

### Citations

**File:** app/src/lib/stores/app-store.ts (L6912-6995)
```typescript
  public async _startCopilotConflictResolution(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { step } = multiCommitOperationState
    if (
      step.kind !== MultiCommitOperationStepKind.ShowCopilotConflictsLoading
    ) {
      return
    }

    const { conflictState } = step
    const account = getAccountForCopilotConflictResolution(
      this.accounts,
      repository
    )
    if (!account) {
      return
    }

    // Controller used to actually cancel the in-flight SDK turn when the user
    // clicks "Stop" (see _abortCopilotConflictResolution).
    const abortController = new AbortController()
    const copilotModels =
      this.copilotModelsByAccount.get(getCopilotAccountCacheKey(account)) ??
      null
    const copilotResolutionModel = getConflictResolutionModelDisplay(
      this.getSelectedCopilotModels(account)['conflict-resolution'] ?? null,
      copilotModels,
      this.byokProviders
    )
    this.repositoryStateCache.updateMultiCommitOperationState(
      repository,
      () => ({
        copilotResolutionAbortController: abortController,
        copilotResolutionModel,
      })
    )

    // Only the run that owns this controller may mutate Copilot resolution
    // state. Guards against a stale run (still unwinding after the user
    // cancelled and restarted) clobbering the controller, progress, or result
    // of the newer run.
    const ownsCurrentRun = () =>
      this.repositoryStateCache.get(repository).multiCommitOperationState
        ?.copilotResolutionAbortController === abortController

    this.statsStore.increment('initiateResolveConflictsWithCopilotCount')
    const resolveStartTime = performance.now()

    try {
      const result = await this._resolveConflictsWithCopilot(
        repository,
        progress => {
          // Bail if user cancelled while the request was in-flight, or if a
          // newer run has taken over.
          const current = this.repositoryStateCache.get(repository)
          const mcoState = current.multiCommitOperationState
          if (
            mcoState === null ||
            mcoState.step.kind !==
              MultiCommitOperationStepKind.ShowCopilotConflictsLoading ||
            !ownsCurrentRun()
          ) {
            return
          }
          if (__DEV__ && progress.reasoningSnippet !== undefined) {
            log.info(
              `[Copilot SDK] app-store progress snippet: ${progress.reasoningSnippet}`
            )
          }
          this.repositoryStateCache.updateMultiCommitOperationState(
            repository,
            () => ({ copilotResolutionProgress: progress })
          )
          this.emitUpdate()
        },
        abortController.signal
      )
```

**File:** app/src/lib/stores/app-store.ts (L7073-7089)
```typescript
      // Store resolutions and transition to the result dialog.
      // Files are NOT written to disk yet — that happens when the user
      // clicks "Continue Merge" (see _applyCopilotConflictResolutions).
      this.repositoryStateCache.updateMultiCommitOperationState(
        repository,
        () => ({
          step: {
            kind: MultiCommitOperationStepKind.ShowCopilotConflicts,
            conflictState,
          },
          copilotResolutions: result.resolutions,
          copilotResolutionSummary: result.summary,
          copilotSkippedFiles: result.skippedFiles,
          copilotResolutionProgress: null,
          copilotResolutionAbortController: null,
        })
      )
```

**File:** app/src/lib/stores/app-store.ts (L7093-7106)
```typescript
      // Record resolution timing buckets
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

**File:** app/src/lib/stores/app-store.ts (L7241-7256)
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
```

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
