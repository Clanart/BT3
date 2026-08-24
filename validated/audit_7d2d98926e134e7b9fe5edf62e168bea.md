## Analysis

The external report's core primitive is: **a call whose success is assumed without checking its return value**, causing an operation to silently no-op while the caller proceeds as if it fully succeeded. The closest analog in GitHub Desktop is in `AppStore._applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7269`), which writes AI-resolved conflict content to disk and then stages it with a single unchecked `git add`, after which the multi-commit operation (merge/rebase/cherry-pick) proceeds to commit.

### Title
Unchecked `git add` after writing Copilot-resolved conflict content lets stale/partially-resolved content be silently committed - (`File: app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions` writes each AI-resolved file's content to disk via `writeFile` and collects the paths into `pathsToStage`, then invokes a single `git add -- <paths>` call whose result is never inspected [1](#0-0) . If that `git add` fails or silently ignores some paths (e.g. because of `.gitignore` rules, a case-collision, a path that git considers to not match any file, or a `core.filemode`/permission race), the function returns normally and the caller treats the whole operation as successful — statistics are incremented, the dialog is dismissed, and the operation continues toward `git commit`.

### Finding Description
The write path in `_applyCopilotConflictResolutions`:
1. Resolves and validates the destination path is inside the repo via `resolveWithin` [2](#0-1) .
2. Writes attacker/model-influenced content (`resolution.resolvedContent`, sourced from a Copilot session whose prompt is built from repository/PR/commit data — see `ConflictResolutionSystemPrompt` and `IFileResolution` [3](#0-2) ) straight to the working tree with `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [4](#0-3) .
3. Batches all such paths and stages them with one `git add`, but does not check the resulting exit code or `gitError`: [5](#0-4) 

Unlike other staging paths in the codebase (`updateIndex`/`stageFiles` in `app/src/lib/git/update-index.ts:67-99`, which use the shared `git()` helper that throws a `GitError` on unexpected exit codes [6](#0-5) ), this call sites relies on the same throwing behavior for hard failures, but git's `add` command does **not** fail (exit 0) for common no-op scenarios — most notably when a pathspec matches an ignored file, or when git treats the path as unchanged/untracked in a way that results in nothing being staged. In those cases `git add` returns success while doing nothing, and the code has no post-check (e.g. re-querying `getIndexChanges`/`getStatus` for the staged paths) to confirm the working-tree write actually made it into the index. The subsequent commit (triggered from the result dialog's "Continue Merge" flow) will then commit whatever the index already contained for that path — which, in an active merge/rebase/cherry-pick, is the **still-conflicted content with `<<<<<<<`/`=======`/`>>>>>>>` markers** or an unrelated stale index entry — while the UI has already told the user their conflicts were AI-resolved and staged.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the user believes Copilot's resolution was applied and staged (stats are incremented, the dialog proceeds), but the actual index/commit content can silently diverge from what was written to disk and reviewed in the diff. Because this happens during merge/rebase/cherry-pick continuation, the resulting commit could contain raw conflict markers or incorrect content that gets pushed upstream without the user noticing, since the app never re-verifies that `pathsToStage` were actually staged.

### Likelihood Explanation
Likelihood is moderate: the conditions under which `git add` succeeds without staging content (ignored paths, case-only path collisions on case-insensitive filesystems, or index modes such as assume-unchanged/skip-worktree set by other tooling) are edge cases, not attacker-arbitrary, so this is not trivially attacker-triggerable in the way the original ETH bug was. It requires a specific repository state (e.g. a conflicted file whose path is also covered by a `.gitignore` rule, or is affected by `assume-unchanged`/`skip-worktree`) combined with using the Copilot-conflict-resolution feature.

### Recommendation
- Check the result of the `git add` call in `_applyCopilotConflictResolutions` and treat a non-zero/unexpected outcome as an error surfaced to the user rather than silently proceeding.
- After staging, verify via `getIndexChanges`/`getStatus` that each path in `pathsToStage` is actually staged with no remaining conflict markers before allowing the multi-commit operation to continue, mirroring the "no remaining conflict markers" check already done for user-edited files at lines 7241-7256 [7](#0-6) .
- Fail the "Continue Merge" action and surface an error/log if verification does not match expectations, instead of allowing the commit step to proceed unconditionally.

### Proof of Concept
Not independently verified with a live repro in this environment (no filesystem/terminal access available); the finding is based on static code review of `app/src/lib/stores/app-store.ts:7169-7269`, `app/src/lib/git/update-index.ts`, and `app/src/lib/git/core.ts`. A concrete PoC would involve:
1. Start a merge/rebase with a conflicted file whose path is covered by a `.gitignore` rule (or has `assume-unchanged` set).
2. Trigger Copilot conflict resolution; let it produce a resolution for that path.
3. Accept the resolution ("Continue Merge") — `writeFile` updates the working tree, but `git add` is a no-op for the ignored/assume-unchanged path and exits 0 without an error being surfaced.
4. Observe that `_applyCopilotConflictResolutions` returns successfully and the flow proceeds to commit, while `git status`/`git diff --cached` shows the index still contains the pre-resolution (conflicted) content for that path. [8](#0-7)

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L26-41)
```typescript
/** Resolution suggestion for a single conflicted file. */
export interface IFileResolution {
  /** Repository-relative file path that was resolved. */
  readonly path: string
  /** The fully resolved file content (all conflict markers removed). */
  readonly resolvedContent: string
  /** Human-readable explanation of how and why conflicts were resolved this way. */
  readonly reasoning: string
  /**
   * For delete-vs-modify conflicts: the model's recommendation.
   * When present, `resolvedContent` is not meaningful — the resolution
   * is applied as a `ManualConflictResolution` (keep = non-deleted side,
   * delete = deleted side).
   */
  readonly deleteConflictAction?: 'keep' | 'delete'
}
```

**File:** app/src/lib/git/core.ts (L322-353)
```typescript
          const exitCode = result.exitCode

          let gitError: DugiteError | null = null
          const acceptableExitCode = opts.successExitCodes
            ? opts.successExitCodes.has(exitCode)
            : false
          if (!acceptableExitCode) {
            gitError = parseError(coerceToString(result.stderr))
            if (gitError === null) {
              gitError = parseError(coerceToString(result.stdout))
            }
          }

          const gitErrorDescription =
            gitError !== null
              ? getDescriptionForError(gitError, coerceToString(result.stderr))
              : null
          const gitResult = {
            ...result,
            gitError,
            gitErrorDescription,
            path,
          }

          let acceptableError = true
          if (gitError !== null && opts.expectedErrors) {
            acceptableError = opts.expectedErrors.has(gitError)
          }

          if ((gitError !== null && acceptableError) || acceptableExitCode) {
            return gitResult
          }
```
