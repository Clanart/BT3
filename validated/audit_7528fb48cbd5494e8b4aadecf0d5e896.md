### Title
Copilot conflict resolutions can be written to disk without being staged if the write loop aborts mid-way, letting unreviewed AI content be silently swept into a later commit - (File: app/src/lib/stores/app-store.ts)

### Summary
`_applyCopilotConflictResolutions` mirrors the reported `settleOptions()` pattern: it loops over `copilotResolutions`, performing a per-item side effect (`writeFile` to the working directory) while only recording the corresponding "settlement" action (`git add`) in an array (`pathsToStage`) that is executed in a single batch *after* the loop finishes. Just as `ShortCollateral` could run out of funds because insolvency reclamation was deferred to the end of the loop instead of happening per-iteration, Desktop's working tree can end up in an inconsistent state — files rewritten with AI-generated content but never staged — if the loop throws partway through.

### Finding Description [1](#0-0) 

The function iterates `copilotResolutions` and for each entry:
1. Resolves the path with `resolveWithin` (path-traversal guard).
2. Checks whether the on-disk file was resolved out-of-band.
3. Calls `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` — this happens immediately, inside the loop.
4. Pushes the path to `pathsToStage` — the array is only consumed with `git add` after the entire `for` loop completes: [2](#0-1) 

There is no `try/catch` around individual iterations, and no rollback of files already written by prior iterations if a later iteration throws (e.g. a disk-full/EACCES error from `writeFile`, or any unexpected exception). Because the corrective/settlement step (`git add`) is deferred to the very end of the loop rather than being applied immediately after each `writeFile`, an early abort leaves a **partial, inconsistent state**: some files have had their on-disk conflict markers overwritten with Copilot-generated content, but that content was never staged and the caller (`_applyCopilotConflictResolutions`) exits via exception without recording which paths were already written.

This is the same broken invariant as the report: a batch operation performs individual state-mutating actions (payouts / `writeFile`) but defers the reconciling/settlement action (insolvency reclaim / `git add`) to the end of the loop, so an early exit (or, in the contract case, a later insolvent iteration) leaves the system in a state where completed mutations are not backed by their expected consistency guarantee.

### Impact Explanation
The consequence for Desktop is not fund loss but **silent corruption of what the user commits**, which is explicitly in-scope. If a file has already been overwritten with Copilot's resolution but the loop aborts before `git add` runs, the working-directory file no longer contains conflict markers, so downstream flows that treat "no conflict markers" as "resolved" (e.g. `continueRebase`/`continueCherryPick`, which independently call `getStatus` and then `stageFiles(otherFiles)` on every tracked file not covered by `manualResolutions`) will pick up and commit that content: [3](#0-2) [4](#0-3) 

The user never explicitly reviewed/approved that specific file's content through the intended path (`pathsToStage` → `git add`), yet it ends up committed. An attacker who controls a cloned/fetched repository (a malicious branch/PR designed to produce conflicting files, including ones that are large, exotic-encoded, or otherwise likely to make a later `writeFile` in the batch fail) can increase the odds that this partial-write/no-stage race is hit for files earlier in `copilotResolutions`, and thereby get content merged into the user's repository/commit history without their conscious, itemized approval.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires an error occurring for one file *after* one or more earlier files in the same batch already succeeded (e.g., I/O error, permission error, an unusual `resolvedContent` causing an unexpected exception, or the user hitting resource limits during a large conflict resolution). This is analogous to the original report's own assessment that the triggering condition ("insolvent positions") was rare — the team explicitly noted 0 occurrences in 6 months — but the underlying code pattern (mutate-then-defer-settle) is the same order-of-operations flaw.

### Recommendation
Move the `git add` (staging) for each resolution immediately after its `writeFile` call inside the loop, or wrap the loop in a transaction-like pattern: stage (or roll back) each file's write before proceeding to the next iteration, and on any failure, revert previously-written files to their original conflicted content (or explicitly track which files were partially written so downstream flows do not silently absorb them as "resolved but unstaged"). This mirrors the mitigation recommended in the original report — perform the settlement/reconciliation step immediately per iteration rather than batching it at the end of the loop.

### Proof of Concept
1. Attacker publishes a branch/PR that, when merged/rebased/cherry-picked, produces several conflicting files, with at least one file crafted to be pathological for the write step used later (e.g., extremely large `resolvedContent`, or a path that will trigger an OS-level write failure such as exceeding path length limits on some platforms) positioned after other conflicting files in iteration order.
2. Victim uses Copilot conflict resolution; `copilotResolutions` is generated for all conflicting files and the result dialog is shown.
3. Victim clicks "Continue Merge", invoking `_applyCopilotConflictResolutions`.
4. The loop writes Copilot's content for files 1..N successfully (`pathsToStage` grows) but throws on file N+1's `writeFile` (or any other exception in that iteration) before the final `git add` call is ever reached — none of files 1..N are staged, but their on-disk content is now Copilot-resolved, no longer containing conflict markers.
5. The error propagates to the caller (`_applyCopilotConflictResolutions`'s outer catch, per lines 7107-7141) and the UI transitions back to `ShowConflicts` (manual resolution). The victim, seeing files 1..N no longer flagged as conflicted, proceeds with `continueRebase`/`continueCherryPick`, which stages and commits those files' AI-written content automatically via `stageFiles(otherFiles)` — without the victim ever having explicitly reviewed/staged that specific content through the intended `pathsToStage` → `git add` path. [5](#0-4)

### Citations

**File:** app/src/lib/stores/app-store.ts (L7162-7269)
```typescript
  /**
   * Write Copilot-resolved file contents to disk and stage them.
   * Called when the user clicks "Continue Merge" from the Copilot conflicts
   * result dialog.
   *
   * This shouldn't be called directly. See `Dispatcher`.
   */
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

    for (const resolution of copilotResolutions) {
      if (manualResolutions.has(resolution.path)) {
        continue
      }

      // Delete-vs-modify conflicts are resolved by setting a manual
      // resolution (ours/theirs) rather than writing file content.
      // The existing stageManualConflictResolution flow handles the
      // actual git checkout --ours/--theirs and staging at commit time.
      if (resolution.deleteConflictAction !== undefined) {
        const file = state.changesState.workingDirectory.files.find(
          f => f.path === resolution.path
        )
        if (file === undefined) {
          continue
        }
        const deletedSide = getDeletedSideFromStatus(file)
        if (deletedSide === undefined) {
          continue
        }
        // "keep" → choose the non-deleted side, "delete" → choose the deleted side
        const manualChoice =
          resolution.deleteConflictAction === 'keep'
            ? deletedSide === 'ours'
              ? ManualConflictResolution.theirs
              : ManualConflictResolution.ours
            : deletedSide === 'ours'
            ? ManualConflictResolution.ours
            : ManualConflictResolution.theirs
        this._updateManualConflictResolution(
          repository,
          resolution.path,
          manualChoice
        )
        continue
      }

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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
  }
```

**File:** app/src/lib/git/rebase.ts (L443-462)
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

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```

**File:** app/src/lib/git/cherry-pick.ts (L384-402)
```typescript
  // only stage files related to cherry pick
  const trackedFiles = files.filter(f => {
    return f.status.kind !== AppFileStatusKind.Untracked
  })

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

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))
  await stageFiles(repository, otherFiles)
```
