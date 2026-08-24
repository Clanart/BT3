### Title
Silent skip in Copilot merge-conflict auto-resolution can commit files with unresolved conflict markers - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_applyCopilotConflictResolutions` mirrors the reported `_transferGroups` bug class: it iterates a list of per-file conflict "resolutions" in order, silently `continue`s past several categories of items it can't apply, and never verifies afterward that every conflicted file actually received a resolution before the caller proceeds to finalize the merge/rebase/cherry-pick. The staging index therefore records "the merge is resolved" while one or more files can still contain the tree's original conflict markers.

### Finding Description
`_applyCopilotConflictResolutions` builds `pathsToStage` from `copilotResolutions` and skips a resolution — without recording the skip anywhere the UI checks — in three cases: when `resolveWithin(repository.path, resolution.path)` returns `null` (path escapes the repo), when the matching working-directory file can't be found for a delete-conflict resolution, or when the on-disk file no longer looks conflicted. [1](#0-0) 

Crucially, none of these internal skips are added to `copilotSkippedFiles`, which is the *only* set the result dialog checks before enabling "Continue": [2](#0-1) [3](#0-2) 

Clicking Continue calls `applyCopilotConflictResolutions` and then unconditionally proceeds to `onContinueAfterConflicts` → `dispatcher.finishConflictedMerge` → `_finishConflictedMerge`, which re-reads `workingDirectory.files`, takes everything still flagged `AppFileStatusKind.Conflicted`, and stages/commits it via `createMergeCommit` with no re-check that a Copilot fix or manual resolution was actually applied: [4](#0-3) [5](#0-4) [6](#0-5) 

This is the same structural defect as the Solidity report: a FIFO/iteration loop that "skips" items under some condition (`groups[i].expiresAt <= _now` there vs. `resolveWithin(...) === null` / `deletedSide === undefined` here) with no terminal assertion that the whole set of items was actually accounted for (`debt === 0` there vs. "every previously-conflicted path is either staged with new content or has a manual resolution" here). In both cases the higher-level state (ERC1155 balance / "merge completed" banner) reports success while the lower-level per-item bookkeeping (group array / working tree content) is left inconsistent.

### Impact Explanation
If a resolution's `path` can be steered to fail `resolveWithin` (or otherwise fall into one of the silent-skip branches) while `copilotSkippedFiles` remains empty, the user is shown a fully "resolved" dialog, clicks Continue, and Desktop commits the merge with that file's original conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) baked into the resulting commit and pushed as-is. This is a silent corruption of "what the user commits" — the exact impact class called out as valid (silent corruption of what the user commits or pushes), sourced from content in a fetched/merged branch that an attacker controls (the conflicting file/branch feeding the Copilot resolution step).

### Likelihood Explanation
Requires: (1) the user has Copilot conflict resolution enabled and merges/rebases a branch containing attacker-influenced conflicting content, and (2) the AI resolution step emits a `path` that fails `resolveWithin` or a delete-conflict resolution whose file can no longer be matched in the current working-directory snapshot (e.g., a race with the file list refreshing). The `path` and `resolvedContent` fields come from a model response driven by the conflicting file content itself, so a maliciously crafted conflicting file is a plausible vector to influence what `path` is returned — but I could not verify from local code alone the exact schema/validation applied to the Copilot service's structured output before it reaches this function, which affects how easily this can be triggered. This should be verified in the resolution-generation code before treating likelihood as anything more than plausible-but-unconfirmed.

### Recommendation
- Short term: track every path in `copilotResolutions` that is skipped for *any* reason inside `_applyCopilotConflictResolutions` (not just paths the Copilot service itself reports skipping) into `copilotSkippedFiles`/an equivalent set, and keep the "Continue" action disabled (mirroring `hasUnresolvedSkippedFiles`) until each conflicted file is confirmed staged with resolved content or has an explicit manual resolution.
- Long term: before calling `_finishConflictedMerge`, assert that `getConflictedFiles(workingDirectory, manualResolutions)` is empty (or that every remaining conflicted file has a manual resolution), refusing to commit and surfacing an explicit error otherwise — the equivalent of checking `debt === 0` before treating the transfer/merge as complete.

### Proof of Concept
1. Attacker prepares a branch/PR whose conflicting file content is crafted such that Copilot's structured resolution response returns a `path` that fails `resolveWithin(repository.path, resolution.path)` (e.g., an absolute or traversal-style path), or targets a delete/modify conflict where the working-directory file lookup misses.
2. Victim merges that branch in Desktop with Copilot conflict resolution enabled; the result dialog shows all files "resolved" because `copilotSkippedFiles` is empty and `hasUnresolvedSkippedFiles()` returns false. [2](#0-1) 
3. Victim clicks "Continue merge"; `_applyCopilotConflictResolutions` silently skips the affected file (log-only warning) and stages the rest. [1](#0-0) 
4. `finishConflictedMerge`/`_finishConflictedMerge` stages and commits all files still marked `Conflicted`, including the skipped one, embedding raw conflict markers into the resulting commit, which Desktop reports via a "Successful Merge" banner. [4](#0-3)

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7541-7563)
```typescript
    /**
     *  The assumption made here is that all other files that were part of this merge
     *  have already been staged by git automatically (or manually by the user via CLI).
     *  When the user executes a merge and there are conflicts,
     *  git stages all files that are part of the merge that _don't_ have conflicts
     *  This means that we only need to stage the conflicted files
     *  (whether they are manual or markered) to get all changes related to
     *  this merge staged. This also means that any uncommitted changes in the index
     *  that were in place before the merge was started will _not_ be included, unless
     *  the user stages them manually via CLI.
     *
     *  Its also worth noting this method only used in the Merge Conflicts dialog flow, not committing a conflicted merge via the "Changes" pane.
     *
     *  *TLDR we only stage conflicts here because git will have already staged the rest of the changes related to this merge.*
     */
    const conflictedFiles = workingDirectory.files.filter(f => {
      return f.status.kind === AppFileStatusKind.Conflicted
    })
    const gitStore = this.gitStoreCache.get(repository)
    return await gitStore.performFailableOperation(() =>
      createMergeCommit(repository, conflictedFiles, manualResolutions)
    )
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L307-314)
```typescript
  /**
   * Whether any file Copilot skipped still lacks a resolution. Continue must
   * stay disabled while this is true, otherwise the file would be committed
   * with its conflict markers intact.
   */
  private hasUnresolvedSkippedFiles(): boolean {
    return this.skippedFiles.some(f => !this.isSkippedFileResolved(f.path))
  }
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L716-733)
```typescript
        <DialogFooter>
          <div className="copilot-conflicts-footer">
            <Button onClick={this.onBackToManual} disabled={isContinuing}>
              Switch to manual
            </Button>
            <OkCancelButtonGroup
              okButtonText={`Continue ${operation}`}
              okButtonDisabled={hasUnresolvedSkippedFiles || isContinuing}
              okButtonTitle={
                hasUnresolvedSkippedFiles
                  ? 'Some files were skipped by Copilot. Those need to be resolved manually.'
                  : undefined
              }
              cancelButtonText={`Abort ${operation}`}
              onCancelButtonClick={this.onAbort}
              cancelButtonDisabled={isContinuing}
            />
          </div>
```

**File:** app/src/ui/multi-commit-operation/merge.tsx (L14-61)
```typescript
  protected onContinueAfterConflicts = async (): Promise<void> => {
    const {
      repository,
      dispatcher,
      workingDirectory,
      state,
      conflictState,
      state: { operationDetail },
    } = this.props

    // Conflicts were resolved externally — nothing left to continue.
    if (conflictState === null) {
      this.onFlowEnded()
      return
    }

    if (
      (state.step.kind !== MultiCommitOperationStepKind.ShowConflicts &&
        state.step.kind !==
          MultiCommitOperationStepKind.ShowCopilotConflicts) ||
      !isMergeConflictState(conflictState) ||
      operationDetail.kind !== MultiCommitOperationKind.Merge
    ) {
      this.endFlowInvalidState()
      return
    }

    const { theirBranch } = state.step.conflictState
    const { currentBranch: ourBranch } = conflictState
    await dispatcher.finishConflictedMerge(
      repository,
      workingDirectory,
      {
        type: BannerType.SuccessfulMerge,
        ourBranch,
        theirBranch,
      },
      operationDetail.isSquash
    )

    await dispatcher.setCommitMessage(repository, DefaultCommitMessage)
    await this.props.dispatcher.changeRepositorySection(
      repository,
      RepositorySectionTab.Changes
    )
    this.onFlowEnded()
    dispatcher.incrementMetric('guidedConflictedMergeCompletionCount')
  }
```

**File:** app/src/lib/git/commit.ts (L82-101)
```typescript
export async function createMergeCommit(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  manualResolutions: ReadonlyMap<string, ManualConflictResolution> = new Map()
): Promise<string> {
  // apply manual conflict resolutions
  for (const [path, resolution] of manualResolutions) {
    const file = files.find(f => f.path === path)
    if (file !== undefined) {
      await stageManualConflictResolution(repository, file, resolution)
    } else {
      log.error(
        `couldn't find file ${path} even though there's a manual resolution for it`
      )
    }
  }

  const otherFiles = files.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```
