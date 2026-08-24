## Analysis

The Solidity report's underlying flaw is a **time-of-check/time-of-use mismatch on a stateful accumulator**: `setDailyRewardRate` resets `lastReward` to "now" without first consuming (flushing) the value that had already accrued under the old rate, so that accrued state is silently discarded and reinterpreted under a new epoch.

The closest structural analog in GitHub Desktop is in the partial-commit staging pipeline: the app records a user's **line-index-based selection** against one snapshot of a file's diff, but at commit time it silently **re-fetches a fresh diff** and reapplies that same line-index selection to it — with no validation that the indices still refer to the same content. This is the same "stale accumulator reapplied to a new epoch" bug class.

### Title
Partial-commit line selection is applied to a re-fetched diff by index, allowing unselected content to be silently committed - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages only some lines of a modified file (a "partial commit"), Desktop stores the user's choice as a `DiffSelection` keyed by **absolute line index** within the diff that was rendered on screen [1](#0-0) . At actual staging time, `applyPatchToIndex` does not reuse that on-screen diff — it independently calls `getWorkingDirectoryDiff` again to build the patch that gets applied to the index [2](#0-1) . `formatPatch` then decides which lines to keep purely by asking `file.selection.isSelected(absoluteIndex)` against this newly fetched diff's hunks [3](#0-2) .

### Finding Description
The invariant that should hold is: "the lines the user visually selected are the exact lines that get committed." That invariant is enforced only by index equality, not by content equality. Desktop's own code acknowledges this gap when refreshing the on-screen diff: [4](#0-3) 

This comment shows the developers know that "the diff might have changed dramatically since last we loaded it" and that the code only patches up `selectableLines`, without validating that previously selected line indices still correspond to the same content. But that reconciliation only happens for the **UI-rendered diff** in `updateChangesWorkingDirectoryDiff`. The actual staging path (`_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex`) does not go through that reconciliation at all — it fetches a brand-new diff independently and reuses the stale `DiffSelection` object directly [5](#0-4) [6](#0-5) .

If the working-directory file's content changes between the moment the user made their line selection (viewing diff "A") and the moment the commit is executed (Desktop internally regenerates diff "B"), the hunk/line layout can shift. Because selection is matched by `absoluteIndex` (an integer offset into the diff, not a stable identifier tied to the actual text) [7](#0-6) , indices that pointed at one set of added/deleted lines in diff A can silently point at a completely different set of lines in diff B. `formatPatch` will happily build a patch out of whatever lines land on those indices in the new diff, with no error and no user-facing warning [8](#0-7)  (the only failure mode guarded against is an *empty* patch, not a *wrong* one).

This means content the user explicitly excluded from a partial commit can be silently included (or vice versa) if the file is modified out from under Desktop's index/selection state during the commit window — e.g. by a `post-checkout`/`post-merge`/`pre-commit` hook rewriting a tracked file, by a build tool, by a background fetch-triggered worktree refresh, or by any other process racing with the user clicking "Commit." None of the existing guards (`WarningBeforeReset`, `WarnLocalChangesBeforeUndo`, `OverwriteStash`) address this path, because those all protect *other* destructive git operations, not the partial-staging/commit flow.

### Impact Explanation
This is a silent corruption of what the user commits: a user who deliberately excluded a hunk (e.g., a secret, a debug statement, or an unwanted change) can end up committing and pushing it anyway without any indication that the wrong lines were included, because the mismatch is entirely internal to index bookkeeping and produces no error.

### Likelihood Explanation
Medium/Low: it requires the file to be modified (e.g. via a git hook shipped in a cloned/fetched repository, or another concurrent tool) in the narrow window between the user's on-screen partial selection and the actual `git apply --cached` staging call. A hook in an attacker-controlled repository is a realistic trigger, but the timing window is comparatively narrow and best-effort.

### Recommendation
Reuse the exact diff that produced the currently-displayed/selected line indices when staging (pass the loaded `ITextDiff` from `IChangesState` through to `applyPatchToIndex`/`formatPatch` instead of re-fetching), or fail the commit/re-validate selection against a freshly fetched diff by content (not just index) before applying the patch — mirroring the recommendation from the report to reconcile accumulated/selected state before it's consumed under a new "epoch."

### Proof of Concept
1. In a cloned repository, modify a tracked file with several independent hunks.
2. In Desktop's Changes view, deselect one hunk (e.g., lines containing a secret) so only the other hunks are checked for commit — this computes a `DiffSelection` keyed to line indices of the diff rendered at that moment (`app/src/lib/stores/app-store.ts:3478-3497`).
3. Before clicking "Commit", have a `post-checkout`/`pre-commit` hook or an external process append/remove lines earlier in the same file so hunk boundaries shift.
4. Click "Commit". `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` (`app/src/lib/git/apply.ts:60`) and `formatPatch` reapplies the old `DiffSelection`'s indices to the new hunk layout (`app/src/lib/patch-formatter.ts:143-161`).
5. Inspect the resulting commit: the content committed no longer matches what was visually deselected/selected in step 2, with no warning shown to the user.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L41-52)
```typescript
/**
 * An immutable, efficient, storage object for tracking selections of indexable
 * lines. While general purpose by design this is currently used exclusively for
 * tracking selected lines in modified files in the working directory.
 *
 * This class starts out with an initial (or default) selection state, ie
 * either all lines are selected by default or no lines are selected by default.
 *
 * The selection can then be transformed by marking a line or a range of lines
 * as selected or not selected. Internally the class maintains a list of lines
 * whose selection state has diverged from the default selection state.
 */
```

**File:** app/src/models/diff/diff-selection.ts (L186-193)
```typescript
  /**
   * Returns a value indicating wether the given line number is selectable.
   * A line not being selectable usually means it's a hunk header or a context
   * line.
   */
  public isSelectable(lineIndex: number): boolean {
    return this.selectableLines ? this.selectableLines.has(lineIndex) : true
  }
```

**File:** app/src/lib/git/apply.ts (L52-81)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/patch-formatter.ts (L143-161)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`
```

**File:** app/src/lib/patch-formatter.ts (L222-227)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }
```

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
```typescript
    const selectableLines = new Set<number>()
    if (diff.kind === DiffType.Text || diff.kind === DiffType.LargeText) {
      // The diff might have changed dramatically since last we loaded it.
      // Ideally we would be more clever about validating that any partial
      // selection state is still valid by ensuring that selected lines still
      // exist but for now we'll settle on just updating the selectable lines
      // such that any previously selected line which now no longer exists or
      // has been turned into a context line isn't still selected.
      diff.hunks.forEach(h => {
        h.lines.forEach((line, index) => {
          if (line.isIncludeableLine()) {
            selectableLines.add(h.unifiedDiffStart + index)
          }
        })
      })
    }
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
