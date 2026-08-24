Based on my investigation, I found a genuine analog to the reported bug class: a state flag/value (`copilotResolutions`) that is set when an operation starts but is not consistently cleared on all paths that should invalidate it, and a downstream function (`_applyCopilotConflictResolutions`) that trusts that value without re-validating the current step — mirroring the `pendingRevocation` flag that survives a branch that should have reset it.

### Title
Stale `copilotResolutions` state is written to disk without a step-kind guard, allowing corruption of merge/rebase/cherry-pick commits - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_applyCopilotConflictResolutions` writes AI-generated conflict resolution content to disk and stages it based solely on `multiCommitOperationState.copilotResolutions` being non-empty, without verifying that the operation is currently in the `ShowCopilotConflicts` step. Across the codebase, `copilotResolutions` is only explicitly cleared (`copilotResolutions: null`) in the error-catch path of `_startCopilotConflictResolution`; no other transition (e.g. returning to manual resolution) is confirmed to clear it.

### Finding Description
`_startCopilotConflictResolution` stores AI-generated `copilotResolutions` on `multiCommitOperationState` when a run completes successfully [1](#0-0) . The only place in `app-store.ts` that resets `copilotResolutions: null` is the `catch` block of that same function, triggered when the SDK call itself errors [2](#0-1) .

When the user reviews the AI suggestions and clicks "Back to Manual" in `CopilotConflictsDialog.onBackToManual`, the dispatcher only transitions the step back to `ShowConflicts` and flips `useCopilotConflictResolution` to `false` — it does not clear `copilotResolutions` [3](#0-2) , and the store-side handler is a thin pass-through with no visible clearing logic in what I could inspect [4](#0-3) .

Crucially, `_applyCopilotConflictResolutions` gates only on `copilotResolutions` being non-null/non-empty, and only uses `step.kind === ShowCopilotConflicts` to decide whether to honor `manualResolutions` overrides — it never requires that the *current* step actually be `ShowCopilotConflicts` before writing files: [5](#0-4) 

The write itself uses the stale, previously-cached `resolution.resolvedContent` per file: [6](#0-5) 

The function does contain a check to avoid clobbering conflicts that were resolved externally by comparing on-disk conflict-marker status [7](#0-6) , but this only protects files whose conflict markers were fully removed by hand — it does nothing to detect that the user explicitly rejected the AI resolutions and went back to manual mode while other files remain conflicted, nor does it re-validate that `copilotResolutions` corresponds to the *current* step/session.

### Impact Explanation
Because the AI resolution is computed from attacker-influenced content — an attacker who controls the merged/rebased branch (e.g. a malicious PR branch or fetched remote ref) controls the conflicting hunks that Copilot resolves — a stale, previously generated `copilotResolutions` entry could later be written to disk and `git add`-ed without the current dialog step confirming it. This is a silent-corruption-of-what-the-user-commits scenario: the user believes they are committing their manual resolution, but a leftover `copilotResolutions` payload from an earlier (possibly attacker-shaped) conflict is staged instead, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Under the currently-wired UI, `applyCopilotConflictResolutions` is only invoked from `CopilotConflictsDialog.onContinue`, which is only rendered while `step.kind === ShowCopilotConflicts` [8](#0-7) , so today's single call site happens to line up with the correct step. The risk is that `_applyCopilotConflictResolutions` itself has no internal invariant enforcing this — it is reachable from `Dispatcher.applyCopilotConflictResolutions` by any future or race-prone caller, and I was unable to fully confirm within the available tool budget that every path back to manual resolution (`_setMultiCommitOperationStepWithCopilotResolution`) actually nulls out `copilotResolutions`. Given only one `copilotResolutions: null` reset exists in the file, this is a plausible-but-not-fully-confirmed gap.

### Recommendation
Add an explicit guard in `_applyCopilotConflictResolutions` requiring `multiCommitOperationState.step.kind === MultiCommitOperationStepKind.ShowCopilotConflicts` before writing any file, and clear `copilotResolutions`/`copilotResolutionSummary`/`copilotSkippedFiles` on every transition away from the Copilot conflicts steps (including `onBackToManual`), not only on the SDK-error path.

### Proof of Concept
1. Fetch/merge a branch with conflicting files whose content is attacker-controlled.
2. Trigger "Resolve with Copilot"; wait for `copilotResolutions` to populate (`ShowCopilotConflicts` step).
3. Click "Back to Manual" (`onBackToManual`), which sets `step.kind = ShowConflicts` but — based on the single reset site found — does not clear `copilotResolutions`.
4. Manually resolve conflicts differently, then trigger any code path that calls `Dispatcher.applyCopilotConflictResolutions` again (a UI regression, retry, or race condition) — because `_applyCopilotConflictResolutions` does not check `step.kind`, it writes the stale AI-generated content over the user's manual resolution and stages it via `git add`.

**Confidence note:** I could not, within the available tool budget, retrieve the full body of `_setMultiCommitOperationStepWithCopilotResolution` in `app-store.ts` to conclusively confirm it never clears `copilotResolutions`. This finding is based on the fact that only one `copilotResolutions: null` reset exists in the searched file and the missing `step.kind` guard in `_applyCopilotConflictResolutions`, which is confirmed by direct code inspection. If a full-repo review shows `copilotResolutions` is reliably cleared elsewhere, this analog would be weakened.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7073-7086)
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
```

**File:** app/src/lib/stores/app-store.ts (L7107-7137)
```typescript
    } catch (e) {
      log.warn('AppStore: Copilot conflict resolution flow failed', e)

      // A stale run shouldn't surface errors or reset a newer run's state.
      if (!ownsCurrentRun()) {
        return
      }

      this.statsStore.increment('copilotConflictResolutionErrorCount')

      // Surface the error to the user so they understand why they were
      // routed back to manual conflict resolution. Mirrors the pattern
      // used by `_generateCommitMessage`.
      this.emitError(new ErrorWithMetadata(e, { repository }))

      // Transition back to manual conflict resolution
      this.repositoryStateCache.updateMultiCommitOperationState(
        repository,
        () => ({
          step: {
            kind: MultiCommitOperationStepKind.ShowConflicts,
            conflictState,
          },
          useCopilotConflictResolution: false,
          copilotResolutions: null,
          copilotResolutionSummary: null,
          copilotSkippedFiles: null,
          copilotResolutionProgress: null,
          copilotResolutionAbortController: null,
        })
      )
```

**File:** app/src/lib/stores/app-store.ts (L7169-7194)
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

    // Respect any manual overrides the user chose in the result dialog
    const manualResolutions =
      step.kind === MultiCommitOperationStepKind.ShowCopilotConflicts
        ? step.conflictState.manualResolutions
        : new Map<string, ManualConflictResolution>()

    this.statsStore.increment('copilotConflictResolutionAcceptedCount')
    if (manualResolutions.size > 0) {
      this.statsStore.increment('copilotConflictResolutionWithOverridesCount')
    }

    const pathsToStage: string[] = []
```

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

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
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L108-119)
```typescript
  private onBackToManual = () => {
    const { dispatcher, repository, conflictState } = this.props

    dispatcher.setMultiCommitOperationStepWithCopilotResolution(
      repository,
      {
        kind: MultiCommitOperationStepKind.ShowConflicts,
        conflictState,
      },
      false
    )
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L128-141)
```typescript
  private onContinue = async () => {
    this.setState({ isContinuing: true })
    try {
      // Write Copilot resolutions to disk before continuing the operation.
      // Done here (shared) so it works for merge, rebase, and cherry-pick.
      await this.props.dispatcher.applyCopilotConflictResolutions(
        this.props.repository
      )
      await this.props.onContinueAfterConflicts()
    } catch (e) {
      this.setState({ isContinuing: false })
      throw e
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L3937-3951)
```typescript
  /**
   * Atomically transition the multi commit operation step and set the
   * useCopilotConflictResolution flag in a single store update.
   */
  public setMultiCommitOperationStepWithCopilotResolution(
    repository: Repository,
    step: MultiCommitOperationStep,
    useCopilotConflictResolution: boolean
  ): void {
    this.appStore._setMultiCommitOperationStepWithCopilotResolution(
      repository,
      step,
      useCopilotConflictResolution
    )
  }
```
