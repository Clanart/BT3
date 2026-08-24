## Analysis: Index-based diff selection desync in GitHub Desktop (`patch-formatter.ts` / `app-store.ts`)

The underlying bug class in the report is: **a stateful index-to-identity mapping that is trusted after the indexed collection changes**, causing silent misapplication of state (rewards applied to the wrong emission). GitHub Desktop has a structurally identical pattern in its partial-commit ("stage selected lines") feature, where line selections are tracked by **absolute numeric index into the diff**, not by line identity/content, and that index is carried across diff re-computations.

### Title
Silent Mis-staging of Working Directory Changes via Stale Absolute-Line-Index Selection After Diff Refresh - (File: `app/src/lib/patch-formatter.ts`, `app/src/lib/stores/app-store.ts`)

### Summary
`DiffSelection` tracks which lines a user selected for partial staging using a `Set<number>` of **absolute line indexes** computed from the diff's hunk layout at the time of selection [1](#0-0) . When Desktop later regenerates the diff for the same file (`updateChangesWorkingDirectoryDiff`), it reuses the existing `DiffSelection` object and only filters it against a newly computed `selectableLines` set, still keyed by the same absolute-index scheme [2](#0-1) . There is no re-validation that the content at a given index is the same content the user actually selected — the comment in the code even acknowledges this: "the diff might have changed dramatically... any previously selected line which now no longer exists or has been turned into a context line isn't still selected" but does nothing for lines that still exist as includeable at that index but now represent **different content** [3](#0-2) .

### Finding Description
`formatPatch` builds the actual `git apply --cached` patch strictly from `hunk.unifiedDiffStart + lineIndex` and asks `file.selection.isSelected(absoluteIndex)` [4](#0-3) . This is the exact analog of `Position::ensure_trackers` matching `Reward` to `Emission` purely by array index — if the "array" (diff hunks/lines) shifts, the index no longer refers to the same logical entity, but the code has no independent check (like the `assert_eq!(reward.mint, emission.mint, ...)` in the Solana code) to catch the mismatch. It just blindly re-applies old index-based selection state to a newly parsed diff.

This is reachable purely by causing the working directory content Desktop is diffing to change between the moment a user picks specific lines to commit and the moment Desktop's next `updateChangesWorkingDirectoryDiff` refresh runs (triggered on file-watcher events, focus changes, or any status refresh) — a window an attacker can create through repository content Desktop executes automatically, such as clean/smudge filters or hooks defined by `core.hooksPath`/`.gitattributes` in a cloned/fetched repo, which can rewrite tracked files on disk as a side effect of routine git operations Desktop performs (checkout, status, LFS smudge, etc.). If the rewritten file inserts/removes lines above the user's previously-selected hunk, the hunk's `unifiedDiffStart` shifts, but the stale `divergingLines` indexes in `DiffSelection` remain and are reapplied to whatever line now happens to occupy that same absolute index [5](#0-4) .

### Impact Explanation
If the attacker can shift diff hunks between the user's selection and the next diff refresh, Desktop can silently stage (and the user can then commit/push) **content the user never reviewed or approved** — potentially including attacker-inserted lines that were never intended to be part of the commit, or excluding a security-relevant line the user did intend to include. This is a "silent corruption of what the user commits or pushes," matching the accepted impact category, because the UI's selection checkboxes and the generated patch can diverge from what's actually shown, with no integrity check tying a selection index back to specific line content.

### Likelihood Explanation
The DiffSelection→absolute-index model with no content-identity check is unconditional, first-class application logic (not a corner case), exercised any time a user does partial staging on a file whose diff is later recomputed while a selection is pending, which is common given Desktop's frequent background/file-watcher-driven diff refreshes. The remaining requirement — a mechanism to cause the working tree file to change out from under the diff between selection and re-diff — depends on filter/hook execution paths that are themselves part of Desktop's normal git-driven workflow on cloned/fetched repository content.

### Recommendation
Do not carry over `divergingLines` by absolute numeric index across diff regenerations. Instead, tie selection state to a content-derived identity (e.g., a hash of the hunk header + line text, or line content + local hunk offset) so that a mismatch between the old and new diff invalidates the stale selection rather than reapplying it to unrelated content, mirroring the `assert_eq!` sanity check pattern that the SyMeta fix relies on for its own index-based lookups.

### Proof of Concept
1. User opens a modified tracked file in Desktop and selects only specific added/removed lines (say lines at absolute indexes 10–12) for partial commit, leaving the rest unselected.
2. Before the user clicks "Commit," a git filter/hook driven by repository content (e.g., a `clean`/`smudge` filter declared in the repo's `.gitattributes`, or a `core.hooksPath` hook, both of which are attacker-controlled once cloned) rewrites the tracked file, inserting new lines above the user's selected hunk.
3. Desktop's periodic `updateChangesWorkingDirectoryDiff` reruns, recomputes hunks with new `unifiedDiffStart` offsets, and reapplies the old `divergingLines` `{10,11,12}` via `withSelectableLines` [6](#0-5) , now pointing at different lines than the user originally saw and clicked.
4. The user, seeing the same checkbox states rendered, commits; `formatPatch` builds the patch using the stale absolute indexes against the new hunk layout [4](#0-3) , silently staging/committing content the user never reviewed.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
```

**File:** app/src/models/diff/diff-selection.ts (L320-330)
```typescript
  public withSelectableLines(selectableLines: Set<number>) {
    const divergingLines = this.divergingLines
      ? new Set([...this.divergingLines].filter(x => selectableLines.has(x)))
      : null

    return new DiffSelection(
      this.defaultSelectionType,
      divergingLines,
      selectableLines
    )
  }
```

**File:** app/src/lib/stores/app-store.ts (L3478-3497)
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

    const newSelection =
      currentlySelectedFile.selection.withSelectableLines(selectableLines)
    const selectedFile = currentlySelectedFile.withSelection(newSelection)
```

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```
