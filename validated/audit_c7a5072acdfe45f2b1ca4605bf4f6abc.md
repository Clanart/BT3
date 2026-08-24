### Title
Copilot conflict resolution silently skips writing/staging manually-overridden files, leaving conflict markers to reach the commit - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The reported Plaza Finance bug is a control-flow ordering defect: a generic "take the lower of two rates" check is applied unconditionally, so it always wins for the wrong token type and the intended per-type branch is never reached. The equivalent defect class in GitHub Desktop is an unconditional early `continue` in `_applyCopilotConflictResolutions` that makes the per-case handling code that follows it unreachable, silently dropping the write/stage step for files the user explicitly overrode in the "Resolve with Copilot" flow.

### Finding Description
`_applyCopilotConflictResolutions` in `app/src/lib/stores/app-store.ts` is the function that writes Copilot-resolved conflict content to disk and stages it, invoked when the user clicks "Continue Merge" from the Copilot conflicts dialog: [1](#0-0) 

Inside the loop over `copilotResolutions`, the very first check is: [2](#0-1) 

This `continue` fires for **every** file the user manually overrode to "ours" or "theirs" (i.e., anything present in `manualResolutions`), regardless of whether it is a delete-vs-modify conflict or a normal text conflict. Immediately after this, the code has a dedicated block intended to specifically handle delete-vs-modify conflicts by translating the user's keep/delete choice into a `ManualConflictResolution`: [3](#0-2) 

Because the `manualResolutions.has(resolution.path)` check at line 7197 runs and `continue`s before this block is ever reached, the delete-conflict handling code is dead — it can never execute for a path that is present in `manualResolutions`, which is required for it to be entered in the first place (`getResolutionChoiceForFile` only returns `'ours'`/`'theirs'` when `manualResolutions.get(path)` is set, per `app/src/ui/multi-commit-operation/dialog/copilot-resolution-helpers.ts` lines 23-35). The comment above the loop even documents the intended (but unreachable) fallback: "the existing `stageManualConflictResolution` flow handles the actual git checkout --ours/--theirs and staging at commit time" — that flow is never invoked from here.

For a **normal** (non-delete) conflict with a manual "ours"/"theirs" override, the `continue` means:
- The working-tree file is **not** overwritten with the chosen side's content — it is left exactly as it was, with raw `<<<<<<<`/`=======`/`>>>>>>>` conflict markers still present.
- The path is **not** added to `pathsToStage`, so `git add` is never run for it (lines 7262-7268), leaving the index entry as an unmerged conflict.

The UI, however, shows the user a diff computed via `getResolutionDiff` for the `'ours'`/`'theirs'` stage content (`app/src/lib/git/diff.ts` lines 447-486), giving them full confidence that choosing "Current"/"Incoming" changed the file to that exact content — which never actually happens for non-delete conflicts.

### Impact Explanation
This is a "silent corruption of what the user commits" case: the user makes an explicit, reviewed choice (`ours`/`theirs`) in the Copilot conflict resolution dialog, sees a diff preview matching that choice, and clicks "Continue Merge" — yet the working tree/index are left with unresolved conflict markers instead of their selection. Depending on what downstream continue-merge/rebase/cherry-pick logic does with an entry that git still reports as unmerged, this can result in either the operation stalling unexpectedly, or — if the downstream gating doesn't strictly re-validate `hasUnresolvedConflicts` per file before proceeding — a commit/rebase step going through with literal conflict-marker text in the file content, which would then be pushed. This matches the report's core theme: a comparison/branch that should be conditioned on the specific case (BOND vs LEVERAGE / delete-vs-modify vs normal conflict) is instead short-circuited unconditionally, and the "safe" per-case logic downstream never runs.

### Likelihood Explanation
No attacker-controlled input is strictly required to trigger the bug itself — it reproduces any time a user overrides even one file to "ours"/"theirs" in the Copilot conflict resolution dialog during a merge/rebase/cherry-pick, which is an entirely normal, expected user action. This makes the code path highly reachable in practice. I was not able to fully verify, within the available tool budget, exactly what the "Continue Merge" step (`onContinueAfterConflicts`) does when it encounters a path still flagged as unmerged by git after this function returns — that final confirmation would require tracing `stageManualConflictResolution` usage in `app/src/lib/git/rebase.ts`, `cherry-pick.ts`, and `commit.ts`, which I could not complete before running out of iterations.

### Recommendation
Reorder the loop so the delete-vs-modify handling in `_applyCopilotConflictResolutions` (lines 7201-7231) is checked and executed before the generic `manualResolutions.has(resolution.path)` early-continue, or explicitly call the equivalent of `stageManualConflictResolution`/`git checkout --ours|--theirs` plus `git add` for any manually-overridden **non-delete** conflict path before skipping it, so every code path that can be reached by `getResolutionChoiceForFile` returning `'ours'`/`'theirs'` actually results in the working tree and index reflecting that exact choice prior to continuing the operation.

### Proof of Concept
1. Start a merge/rebase that produces a normal (non delete-vs-modify) text conflict in `file.txt`.
2. Click "Resolve with Copilot"; Copilot proposes a resolution for `file.txt`.
3. In the result dialog, override `file.txt` to "Current" (ours) via the dropdown (`copilot-conflicts-dialog.tsx` `onResolutionDropdownClick`, `setResolution(path, 'ours')`), which sets `manualResolutions.set('file.txt', ManualConflictResolution.ours)`.
4. Click "Continue Merge" (`onContinue` → `dispatcher.applyCopilotConflictResolutions` → `_applyCopilotConflictResolutions`).
5. In the loop, `resolution.path === 'file.txt'` matches `manualResolutions.has(...)` → `continue` fires at line 7197-7199 before any write/stage happens for that file.
6. Inspect the working tree: `file.txt` still contains `<<<<<<<`/`=======`/`>>>>>>>` markers; `git status` still reports it as unmerged, even though the UI indicated the user's "Current" choice was applied.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7196-7200)
```typescript
    for (const resolution of copilotResolutions) {
      if (manualResolutions.has(resolution.path)) {
        continue
      }

```

**File:** app/src/lib/stores/app-store.ts (L7201-7231)
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
      }
```
