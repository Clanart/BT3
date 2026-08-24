## Analysis

The Overlay `_totalSupply` bug is a case of **parallel functions that should maintain the same invariant, where only some of them enforce it** — `_mint`/`_burn` update the tracked total, but the structurally similar `_transferMint`/`_transferBurn` silently skip it, letting downstream consumers trust a stale/incorrect value.

GitHub Desktop's Copilot-assisted conflict resolution flow has the same class of defect: within a single function that resolves multiple kinds of conflicts, one branch defends against overwriting a resolution the user already performed on disk, and the sibling branch — handling a different conflict type — omits the equivalent guard. [1](#0-0) 

### Title
Copilot conflict-resolution flow silently overwrites the user's manual delete/modify conflict resolution - (File: app/src/lib/stores/app-store.ts)

### Summary
In `_applyCopilotConflictResolutions`, ordinary content conflicts are protected by a check that skips writing Copilot's stored resolution if the file was already resolved externally, but the delete-vs-modify conflict branch has no equivalent check and unconditionally applies the Copilot-suggested `ours`/`theirs` choice, overriding whatever the user had already resolved on disk or via the CLI.

### Finding Description
`_applyCopilotConflictResolutions` iterates the `copilotResolutions` computed earlier and, for each unresolved file, decides how to finalize it:

- For **delete-vs-modify conflicts** (`resolution.deleteConflictAction !== undefined`), it looks up the file's current status, derives `deletedSide`, computes a `manualChoice`, and immediately calls `_updateManualConflictResolution` to force that resolution — with no check of whether the user has already resolved the conflict some other way (e.g. by staging a deletion via `git rm`, or resolving it in an editor/CLI while the Copilot dialog was open): [2](#0-1) 

- For **ordinary content conflicts** (the very next code path, handling the same overall problem), the same function explicitly re-checks the on-disk file state and skips overwriting if the conflict has already been resolved, with an inline comment stating exactly why — to avoid "silently clobber[ing]" the user's own resolution: [3](#0-2) 

The comment at lines 7241-7246 documents the intended invariant for *all* conflict types ("mirrors how the manual conflicts dialog determines a file is resolved") but that invariant is only implemented for the content-conflict branch. The delete-conflict branch calls `_updateManualConflictResolution`, which unconditionally sets the manual resolution map entry used at commit time by `createMergeCommit`/`continueCherryPick` via `stageManualConflictResolution`: [4](#0-3) 

### Impact Explanation
A delete-vs-modify conflict typically arises when merging, rebasing, or cherry-picking a branch derived from a fetched/cloned remote whose history intentionally deletes a file that the local branch modified (or vice versa) — content fully controlled by whoever supplied the remote/branch. If the Copilot flow proposes a resolution ("keep" or "delete") that differs from what the user already resolved manually (staged their own choice, or fixed it via CLI/editor while the result dialog was still open), clicking "Continue Merge" silently reverts the user's actual choice to the Copilot-derived one, with no warning that anything changed. This is a silent corruption of what gets committed and subsequently pushed — the exact category of harm called out as in-scope ("silent corruption of what the user commits or pushes"), since the resulting commit can either resurrect a file the user deliberately deleted or drop a file the user deliberately kept, without any indication in the UI that the manual resolution was overridden.

### Likelihood Explanation
The precondition (a delete/modify conflict during a Desktop-driven merge/rebase/cherry-pick against attacker-influenced branch content, combined with the user resolving it independently before accepting the Copilot suggestion) is a normal Desktop workflow, not an unnatural attacker-orchestrated sequence — Desktop explicitly supports resolving conflicts outside its own UI and re-reads status to detect that. The bug is a straightforward code-review-detectable omission (the guard exists two branches away in the same function), making it moderately likely to be hit in practice whenever a delete conflict is involved.

### Recommendation
Apply the same "already resolved externally" guard used for content conflicts to the delete-conflict branch before calling `_updateManualConflictResolution`: re-check the current on-disk/status state of the file and skip forcing a Copilot-derived resolution if the user has already resolved it (e.g., staged a deletion, or the file is no longer reported as conflicted). Only fall back to the Copilot suggestion when the file is still genuinely unresolved.

### Proof of Concept
1. Set up a repository where merging/rebasing branch B into A produces a delete-vs-modify conflict on `important-file`.
2. Trigger Desktop's Copilot conflict resolution flow; it computes `copilotResolutions` including a `deleteConflictAction` entry for `important-file` (e.g. "keep").
3. Before clicking "Continue Merge", resolve the conflict manually outside Desktop's suggestion path — e.g. run `git rm important-file` in a terminal to stage the deletion, reflecting the user's actual intended resolution.
4. Click "Continue Merge" in the Copilot result dialog.
5. Observe that `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7205-7230`) unconditionally calls `_updateManualConflictResolution` with the Copilot-derived choice ("keep"), and the resulting commit restores `important-file` instead of respecting the user's manual `git rm`, with no warning surfaced — silently corrupting the committed changeset relative to what the user actually resolved.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7201-7230)
```typescript
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
```

**File:** app/src/lib/stores/app-store.ts (L7241-7259)
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
```

**File:** app/src/lib/git/commit.ts (L82-97)
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
```
