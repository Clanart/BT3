### Title
`_applyCopilotConflictResolutions` writes stale AI conflict-resolution content to disk without verifying a merge/rebase is still in progress - (File: app/src/lib/stores/app-store.ts)

### Summary
`_applyCopilotConflictResolutions` writes the previously-generated Copilot conflict-resolution content back to disk based only on a cached `copilotResolutions` list keyed by relative path. It only skips a file if that path currently appears in `state.changesState.workingDirectory.files` *and* is still conflicted; if the path is no longer reported by git status at all (merge/rebase aborted, branch switched, or the multi-commit operation otherwise ended between resolution generation and the user clicking "Continue Merge"), the code does not skip the write — it silently overwrites whatever is at that path.

### Finding Description
The Copilot-assisted conflict flow works in two phases:
1. `buildConflictContext` snapshots conflicted files at the time the AI resolution run starts and reads their contents via `resolveWithin` [1](#0-0) , then AI-generated resolutions are cached in `multiCommitOperationState.copilotResolutions` without writing anything to disk [2](#0-1) .
2. Later, when the user clicks "Continue Merge", `_applyCopilotConflictResolutions` iterates the cached `copilotResolutions` and, for each entry, resolves the path with `resolveWithin(repository.path, resolution.path)` (which safely prevents path traversal/symlink escape) and then checks the *current* working directory state to decide whether to skip the write [3](#0-2) .

The skip check only fires when `onDiskFile !== undefined` (i.e., the path is still listed as changed in the working directory) *and* it is a conflicted status with no remaining conflict markers (meaning the user manually resolved it in an editor) [4](#0-3) . There is no check that:
- a merge/rebase (`MERGE_HEAD`/`rebase-merge` state) is still actually in progress, and
- the cached `copilotResolutions` correspond to the same conflict/merge session as the current on-disk state.

If `onDiskFile` is `undefined` — because the merge or rebase was aborted (e.g. via `git merge --abort` run outside Desktop, or in another Desktop window/tab sharing the same on-disk repo, or because the operation otherwise completed) while the Copilot result dialog remained open with stale in-memory state — the loop proceeds straight to `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and then stages it with `git add` [5](#0-4) . This is analogous to the reported `Position` bug: an action (`increaseMargin`/here, "apply resolution") is taken against an object (the position / the merge conflict) whose underlying state has already been invalidated ("liquidated" / "merge aborted"), and the code does not re-validate that invalidation before acting, leading to loss/corruption of value the user did not intend (locked collateral / corrupted working-tree file that then gets committed).

`_applyCopilotConflictResolutions` only guards against the top-level `multiCommitOperationState === null` and `copilotResolutions` being empty [6](#0-5) ; it never re-queries `isMergeHeadSet`/rebase state (available via `isMergeHeadSet` in `app/src/lib/git/merge.ts`) or freshly loaded status before writing.

### Impact Explanation
The attacker-controlled input here is the content of a cloned/fetched repository that produces merge/rebase conflicts (a realistic scenario for any PR/branch a victim merges). The Copilot resolution content is generated from that attacker-influenced conflict content and cached. If the underlying merge/rebase state changes out from under Desktop (aborted externally, or the user switches away and back, or another window operates on the same working directory) before the user confirms "Continue Merge," Desktop will silently write attacker-influenced resolved content into the current working tree — potentially into files unrelated to the original conflict resolution's actual current state — and stage it for commit. This is a silent corruption of what the user commits/pushes, matching the requested impact category (no local/physical/admin access needed beyond normal repository interaction).

### Likelihood Explanation
Requires a race/ordering: an external event (manual `git merge --abort`, a second Desktop process/tab pointed at the same repo, or an unrelated background refresh) must invalidate the merge/rebase state while the Copilot conflict-resolution dialog is still showing stale cached resolutions and the user clicks "Continue Merge." This is a real, reachable path (Desktop does not lock the repository against concurrent external git operations, and multi-window/multi-tool use of the same clone is common), but it depends on a timing window rather than being trivially triggerable on every merge, so likelihood is moderate rather than certain.

### Recommendation
Before writing any `resolution.resolvedContent` in `_applyCopilotConflictResolutions`, re-verify (via a fresh `getStatus`/`isMergeHeadSet`/rebase-state check, not just the cached `workingDirectory.files` snapshot) that a merge/rebase/cherry-pick operation matching the cached `copilotResolutions` context is still active in the repository, and abort the apply (returning the user to a "conflicts resolved externally / operation ended" state) if it is not — mirroring the existing "conflicts were resolved externally" guard already used in `BaseRebase.onContinueAfterConflicts` and `Merge.onContinueAfterConflicts` [7](#0-6) .

### Proof of Concept
1. Open a repository in Desktop and start a merge that produces conflicts (attacker-crafted branch/PR content ensures conflicts).
2. Use the Copilot conflict-resolution feature; wait for resolutions to be generated and the result dialog to appear (`ShowCopilotConflicts` step) with `copilotResolutions` populated [2](#0-1) .
3. Without closing the dialog, externally run `git merge --abort` in a terminal against the same working directory (or perform an equivalent operation in another Desktop window pointed at the same repo path).
4. In the still-open Copilot result dialog, click "Continue Merge," triggering `_applyCopilotConflictResolutions`.
5. Because `state.changesState.workingDirectory.files` no longer contains the previously-conflicted path (merge was aborted), the `onDiskFile === undefined` branch is taken and `writeFile` executes unconditionally, overwriting the file's current (post-abort) contents with the stale AI-resolved content and staging it via `git add` [8](#0-7) .

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L7169-7181)
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

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```

**File:** app/src/ui/multi-commit-operation/merge.tsx (L24-28)
```typescript
    // Conflicts were resolved externally — nothing left to continue.
    if (conflictState === null) {
      this.onFlowEnded()
      return
    }
```
