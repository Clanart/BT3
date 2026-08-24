### Title
Diff selection is revalidated by line **index** rather than line **content** when the working directory diff refreshes, allowing silent inclusion of un-reviewed lines in a commit - (File: `app/src/lib/stores/app-store.ts`, `app/src/models/diff/diff-selection.ts`)

### Summary
The bug class in the Sherlock report is: a ranking/boundary value is recomputed for the *new* state, but the code that decides which entries changed status still keys off the *old* index range, so entries whose position now falls inside the boundary are silently left in the wrong bucket. The Desktop analog is the way partial commit line-selections survive a diff reload: when the working-directory diff for the selected file is refreshed, previously "selected"/"diverging" line numbers are carried forward and intersected against a freshly computed `selectableLines` set purely by **absolute line index**, never by verifying that the line at that index still represents the same content. If the diff shape shifts between the time a user selects lines and the time the diff is reloaded, a stale index can now point at a completely different line, and that line silently becomes selected (or deselected) without the user ever seeing or approving it.

### Finding Description
`DiffSelection` tracks a `divergingLines: Set<number>` of absolute line indices whose selection differs from the file's default selection state, plus an optional `selectableLines: Set<number>` describing which indices are currently selectable: [1](#0-0) 

When the working directory diff for the currently selected file is reloaded (e.g. because the file changed on disk), `updateChangesWorkingDirectoryDiff` recomputes the new set of selectable indices from the new diff and calls `withSelectableLines`: [2](#0-1) 

The comment on lines 3480-3485 explicitly acknowledges the limitation: the code does **not** verify that a previously selected line still corresponds to the same content — it only checks that the same absolute index is still "selectable" in the new diff: [3](#0-2) 

`withSelectableLines` implements exactly that: it filters the old `divergingLines` set by simple index membership in the new `selectableLines` set, with no reference to what the line at that index actually says: [4](#0-3) 

Just like in the Sherlock finding — where only the *old* boundary (`old_t1+old_t2`) was used to decide which ranks to re-check instead of the *new* boundary — Desktop's carried-over selection is validated against the *old* index identity instead of the *new* line's actual content/hunk membership. If a diff hunk's line count changes (lines added/removed above the previously selected range, hunks merging/splitting, whitespace-hiding toggling, EOL/`.gitattributes` filter normalization, etc.), a previously divergent index can land on a semantically unrelated line in the new diff and be silently treated as still selected, because both the old and new diff happen to mark that index as "selectable" (`line.isIncludeableLine()`), which is a very common state for adjacent add/delete/context lines. `formatPatch` then builds the actual commit patch straight from `file.selection.isSelected(absoluteIndex)`, so this stale index bleeds directly into what git actually commits: [5](#0-4) 

### Impact Explanation
This is a silent corruption of what the user commits/pushes — one of the explicitly valid impact categories. A user who has partially staged a file (e.g., intentionally including only certain added lines, common in security-conscious workflows) can end up committing a different line than the one they reviewed and approved, without any UI indication that the selection was recalculated on a false premise. This is attacker-influenceable because the working tree content backing the diff can change due to content the attacker controls in the cloned/fetched repository (e.g. `.gitattributes`-driven clean/smudge filters, merge/rebase/stash operations replaying attacker-authored history, or line-ending normalization) between the moment the user makes a partial selection and the moment Desktop reloads the diff (which happens automatically, e.g. on background status refresh) — all without the user taking any unusual action.

### Likelihood Explanation
`updateChangesWorkingDirectoryDiff` is called routinely any time the working directory status changes while a file is selected (background refresh, git operations, file system events), not just on explicit user action, so the vulnerable code path is exercised under ordinary use. The specific outcome (index collision landing on an unrelated but still-"selectable" line) requires a diff-shape change of the right shape, which is plausible in real editing/merge/filter scenarios but not guaranteed on every refresh, making this a realistic-but-not-always-triggered condition rather than a certainty on every diff reload.

### Recommendation
When recomputing `selectableLines` after a diff reload, do not carry forward selection state purely by index equality. Instead, revalidate each previously diverging index by comparing the underlying line content/type (or by mapping via a stable line-content diff/patience algorithm between old and new hunks) before deciding it still represents the user's original intent; when identity cannot be established with confidence, default to *dropping* the selection for that index (safer default: require re-confirmation) rather than silently keeping it selected.

### Proof of Concept
1. Open a modified file in Desktop's Changes view and choose "Discard unselected"/partial selection to select only specific added lines for commit (`DiffSelection.withRangeSelection`).
2. While that selection is in memory, cause the working tree diff to be recomputed at the same absolute indices but with different content — e.g. have the repository's `.gitattributes`/clean filter (attacker-controlled, checked into the repo) rewrite the file, or perform a git operation (stash pop, checkout, merge) that shifts hunk contents so that the same `unifiedDiffStart + index` value now lands on a different, unrelated addable line that is still `isIncludeableLine()`.
3. Observe via `app/src/lib/stores/app-store.ts`'s `updateChangesWorkingDirectoryDiff` → `withSelectableLines` (`app/src/models/diff/diff-selection.ts`) that the stale index remains in `divergingLines` and is thus reported as selected.
4. Commit: `formatPatch` (`app/src/lib/patch-formatter.ts`) includes the line at that index because `file.selection.isSelected(absoluteIndex)` returns true, even though the user never reviewed this specific new line — the committed patch silently differs from what the user intended.

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

**File:** app/src/models/diff/diff-selection.ts (L309-330)
```typescript
  /**
   * Returns a copy of this selection instance with a specified set of
   * selectable lines. By default a DiffSelection instance allows selecting
   * all lines (in fact, it has no notion of how many lines exists or what
   * it is that is being selected).
   *
   * If the selection instance lacks a set of selectable lines it can not
   * supply an accurate value from getSelectionType when the selection of
   * all lines have diverged from the default state (since it doesn't know
   * what all lines mean).
   */
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

**File:** app/src/lib/patch-formatter.ts (L153-171)
```typescript
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
```
