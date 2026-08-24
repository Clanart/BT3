### Title
Partial line-selection integrity relies on numeric diff offsets, allowing an externally-changed working tree to silently redirect a staged selection to unrelated diff content - ([File: app/src/lib/stores/app-store.ts])

### Summary
`DiffSelection` tracks which lines of a file a user has chosen to stage/commit as a `Set<number>` of *positional* indexes into the unified diff (`hunk.unifiedDiffStart + lineIndex`), not by the actual line content or a stable identity. When the working-directory diff is reloaded (e.g. after the file changes on disk from a fetch/checkout/merge/external process while a partial selection is pending), the code only re-validates that previously selected indexes are still "includeable" — it does not verify they still correspond to the same content the user selected.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) stores a `divergingLines: Set<number>` of absolute line indexes and exposes `isSelected(lineIndex)` purely as a membership test on that set [1](#0-0) . Indexes are computed as `hunk.unifiedDiffStart + lineIndex`, i.e. a raw offset into the unified diff, not tied to the actual text of the line.

When the diff for the currently selected file is reloaded — which happens whenever `updateChangesWorkingDirectoryDiff` re-runs `getWorkingDirectoryDiff` (e.g., triggered by any status/diff refresh while the Changes view is open) — the code explicitly acknowledges it does not validate that previously selected lines still represent the same content: [2](#0-1) 

It only rebuilds the `selectableLines` set (lines still capable of being included/excluded) and calls `withSelectableLines`, which merely intersects the *existing* diverging-line index set with the new selectable set: [3](#0-2) 

Because `divergingLines` is preserved by index and only filtered (not content-checked), if the underlying diff changes such that hunks shift (lines added/removed earlier in the file, or the diff is regenerated after the working tree file was modified out-of-band), a previously selected index can survive the filter yet now point at a *different* addition/deletion than what the user visually reviewed and clicked. The staged/committed content (`applyPatchToIndex` / `formatPatch` in `app/src/lib/patch-formatter.ts` at lines 132-221 uses exactly the same `isSelected(absoluteIndex)` check) is therefore driven by stale numeric offsets rather than validated content identity.

This directly mirrors the report's broken invariant: storing an *index* into a mutable collection (participants array / diff line array) instead of the *value/identity* itself, so that when the collection is mutated concurrently, the index silently resolves to different data.

### Impact Explanation
If a working-directory file is modified between the time a user makes a partial-line selection (staging only some lines/hunks) and the time they actually click "Commit" — for example because a background process, a git hook from a cloned/fetched malicious repository, an auto-merge/rebase continuation, or another tool concurrently rewrites the file — the app will silently keep the old numeric line-selection state (only intersected against "is this index still includeable", not "is this the same line"). The result can be that the user's commit/stage action includes lines they never reviewed or excludes lines they intended to include, i.e. silent corruption of what the user commits/pushes without any warning. This is the closest Desktop analog of "duplicate/incorrect index selection due to mutation of the underlying collection" described in the report.

### Likelihood Explanation
Exploitation requires: (1) the user has a partially-selected (not all/none) file open in the Changes view, and (2) the working tree diff for that file gets recomputed while that partial selection is still pending, driven by content the attacker can influence (a malicious repository's hook output, merge/rebase continuation, or any process that alters tracked files during an open Desktop session). The comment at `app-store.ts:3480-3485` explicitly documents this as a known, un-hardened gap ("Ideally we would be more clever… but for now we'll settle on just updating the selectable lines"), which increases confidence this is a real, currently-unaddressed weakness rather than a false positive. However, I could not fully trace every automatic trigger path for diff refresh (e.g., filesystem watcher callbacks) within the indexed portion of the codebase, so the exact frequency/ease of hitting the race in normal usage is not fully confirmed from the available code.

### Recommendation
Do not persist partial selection state as raw positional indexes across diff reloads. Instead, key selection state by a stable identity of the line (e.g., a content hash of the line text plus its type, or the before/after line numbers reported by git, similar to how files are tracked by `id` in `WorkingDirectoryStatus`/`filesByID`). When a diff is reloaded, remap or drop selections whose underlying content no longer matches what was originally selected, rather than only checking whether the index is still "includeable." At minimum, warn the user and reset partial selections when the diff has structurally changed since the last load.

### Proof of Concept
Conceptual sequence (not verified end-to-end due to inability to execute the app):
1. User opens a large modified file in Changes and selects only specific lines within a hunk (e.g., lines 20-22) for a partial commit, leaving the commit dialog open without committing yet.
2. While the dialog is open, an external actor/process (e.g., a git hook triggered by fetch/merge from a malicious remote, or another tool) modifies the same file, adding/removing lines earlier in the file so hunk offsets shift.
3. Desktop reloads the diff via `updateChangesWorkingDirectoryDiff` (`app-store.ts:3404-3497`), rebuilding `selectableLines` but reusing the old `divergingLines` index set via `withSelectableLines` (`diff-selection.ts:320-330`).
4. If the previously selected indexes (e.g., 20-22) are still "includeable" lines in the new diff (now representing different content due to the shift), they remain selected.
5. User commits; `createCommit`/`applyPatchToIndex` uses `file.selection.isSelected(absoluteIndex)` (`patch-formatter.ts:157`) against the new diff, silently staging/committing content the user never reviewed or approved.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L41-53)
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
export class DiffSelection {
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
