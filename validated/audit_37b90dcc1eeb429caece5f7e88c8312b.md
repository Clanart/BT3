Confirmed: `this.props.workingDirectory` in `base-rebase.tsx`/`cherry-pick.tsx`'s `onContinueAfterConflicts` is the component's live React props, which reflect the store's `changesState.workingDirectory` at render time — it is **not** re-fetched from disk between `applyCopilotConflictResolutions()` writing files and `continueRebase`/`continueCherryPick` reading `f.status.kind` to decide which files go into `otherFiles`. The classification is based on git-status metadata (conflict markers presence at the *last* status refresh), not a fresh read of file content, so a file whose Copilot write was silently skipped keeps stale/incorrect status bookkeeping while `stageFiles` blindly `git add`s it.

### Title
Silent skip in Copilot conflict-resolution write path lets files with unresolved conflict markers be staged and committed - ([File: app/src/lib/stores/app-store.ts])

### Summary
`AppStore._applyCopilotConflictResolutions` iterates every AI-generated resolution and silently `continue`s (skips) any file whose path fails the `resolveWithin` repo-containment check, logging only a `log.warn`, with no aggregate failure surfaced to the caller. The function returns normally even when one or more real conflicted files were never written. The caller (`CopilotConflictsDialog.onContinue`) treats a non-throwing return as full success and immediately proceeds to `continueRebase`/`continueCherryPick`/`finishConflictedMerge`, which stage "otherFiles" (anything not in `manualResolutions`) via `stageFiles` without re-checking whether conflict markers are still present on disk.

### Finding Description
`_applyCopilotConflictResolutions` ( [1](#0-0) ) writes each `IFileResolution.resolvedContent` to disk and pushes the path to `pathsToStage`, but only for resolutions that pass `resolveWithin(repository.path, resolution.path)`: [2](#0-1) 

If that check fails, the loop just logs a warning and moves to the next resolution — the function has no return value or exception to signal that a file was not actually resolved. Since the AI-produced `resolution.path` originates from model output over attacker-influenced content (conflicting file/PR/commit text fed into the prompt via `buildConflictContext`, see [3](#0-2) ), a prompt-injection payload embedded in a malicious commit/PR body could cause the model to emit a resolution whose `path` differs from the actual conflicted file (e.g., normalized/mismatched or attempting traversal), causing the real conflicted file to be skipped while `resolveWithin`'s protection only stops the *write*, not the downstream commit.

`CopilotConflictsDialog.onContinue` treats the awaited call as all-or-nothing: [4](#0-3) 

It then calls `onContinueAfterConflicts`, which for rebase/cherry-pick/squash/reorder flows through to `continueRebase`/`continueCherryPick` in `app/src/lib/git/rebase.ts` and `app/src/lib/git/cherry-pick.ts`. Both stage "everything else" unconditionally: [5](#0-4) [6](#0-5) 

`stageFiles`/`git add` does not inspect file content for leftover `<<<<<<<`/`=======`/`>>>>>>>` markers — it just adds whatever bytes are on disk. There is no equivalent of `hasUnresolvedConflicts` re-verification performed at this staging step (that check only exists inside `_applyCopilotConflictResolutions` itself, to avoid clobbering an externally-resolved file — not to block staging of an unresolved one).

### Impact Explanation
This matches "silent corruption of what the user commits" from the valid-impact list: a file that still contains raw Git conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>> branch`) can be staged and committed as part of a rebase/cherry-pick/squash/reorder continuation, corrupting the file's semantics (e.g., breaking code, or embedding both sides of a security-relevant change) without any error dialog, exit code, or warning visible to the user beyond a background log line. The attacker's primitive is a crafted commit/PR/file the user is resolving conflicts against (an attacker-controlled repository object), analogous to the underlying report's "swallowed error during a sequential action, and the caller proceeds as if nothing happened."

### Likelihood Explanation
Requires: (1) the user opts into "Resolve with Copilot" during a conflict, (2) the attacker successfully engineers a prompt-injection in conflicting content that shifts the model's reported `path` for at least one file resolution, and (3) the user clicks "Continue" without noticing the skipped file (the UI does show per-file resolution status, but the skip path via `resolveWithin` failure has no dedicated UI signal — it's not part of `ICopilotSkippedFile` reporting, only a console log). This is a narrower likelihood than a fully automatic exploit, but it does not require local/physical access, admin rights, or leaked credentials — only attacker-controlled repository content and normal user interaction with a shipped feature.

### Recommendation
Make `_applyCopilotConflictResolutions` return an explicit list of paths it failed to write/stage (or throw), and have `CopilotConflictsDialog.onContinue` block continuation (or force those files back into the manual "unresolved" set) rather than silently proceeding. Additionally, `continueRebase`/`continueCherryPick`/merge finalization should re-verify that files in `otherFiles` no longer contain conflict markers (mirroring `hasUnresolvedConflicts`) before staging them, rather than trusting prior status classification.

### Proof of Concept
Not independently runnable from static analysis alone — exploitation requires demonstrating that a crafted conflicting file/PR body reliably induces the Copilot model to emit a resolution `path` that resolves outside the target file (triggering the `resolveWithin` skip) while the corresponding real conflicted file is left with its markers. I could not execute the model or verify this specific triggering condition; this is the main unverified assumption in this analog. The code-level mechanics — silent skip with only a `log.warn`, no aggregated failure surfaced, and unconditional staging of "otherFiles" without content re-validation — are confirmed directly from the cited source.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7169-7269)
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

**File:** app/src/lib/copilot-conflict-context.ts (L376-461)
```typescript
): Promise<ICopilotConflictContext> {
  const results = await Promise.all(
    files.map(async (file): Promise<IFileConflictContext> => {
      // Delete-vs-modify conflicts have no text markers on disk. Include
      // them in the context with metadata so the model can recommend
      // keep or delete — no file content is needed.
      if (file.deletedSide !== undefined) {
        return {
          path: file.path,
          hunks: [],
          deleteConflict: { deletedSide: file.deletedSide },
        }
      }

      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
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

**File:** app/src/lib/git/rebase.ts (L448-462)
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

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))

  await stageFiles(repository, otherFiles)
```

**File:** app/src/lib/git/cherry-pick.ts (L389-402)
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

  const otherFiles = trackedFiles.filter(f => !manualResolutions.has(f.path))
  await stageFiles(repository, otherFiles)
```
