The candidate analog centers on how GitHub Desktop tracks partial line-selection state for commits/discards purely by **positional index** in the diff, rather than by line content, and how that index-based state is preserved (not revalidated) across diff reloads.

### Title
Stale index-based partial-selection state silently applied to changed diff content, causing wrong lines to be committed or discarded - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
`DiffSelection` (the model backing "stage only these lines") tracks selected lines as a `Set<number>` of **absolute indices into the unified diff**, with no binding to the actual text/content of the line. When the working directory diff is reloaded (`updateChangesWorkingDirectoryDiff`), the code only recomputes which indices are *selectable*, but reuses the previously selected index set as-is instead of invalidating/remapping it against the new diff content. If the underlying file content changes between the time a user makes a partial selection and the time the patch is generated (`formatPatch` / `formatPatchToDiscardChanges`, which build the patch strictly via `file.selection.isSelected(absoluteIndex)`), a previously "selected" index can silently point at completely different added/removed content in the new diff, and it will still be treated as selected because it remains an "includeable" line at that index.

### Finding Description
`DiffSelection` explicitly documents that it is an index-based selection abstraction with no notion of the underlying content: [1](#0-0) 

When a new diff is loaded for the currently selected file, `updateChangesWorkingDirectoryDiff` recomputes the set of *selectable* line indices from the fresh diff, but the comment itself acknowledges the gap: it does not validate that previously selected lines still correspond to the same content, only that the index is still an includeable line: [2](#0-1) 

`withSelectableLines` merely intersects/keeps the existing `divergingLines` set, it does not clear indices whose underlying content changed: [3](#0-2) 

Finally, when the actual patch is generated for a partial commit, the check is purely positional — `file.selection.isSelected(absoluteIndex)` — with no comparison against the line text that was originally selected: [4](#0-3) 

The same positional-only trust model is used for building the inverse patch used to discard a selection of lines: [5](#0-4) 

This is structurally the same class of bug as the reported issue: a piece of state (`userWithdraw.managementFee` / here, `divergingLines`) that should be recomputed/invalidated in lockstep with another mutated value (`_shares` / here, the diff hunk content) is instead carried over unmodified and reapplied against a hunk/patch it no longer semantically corresponds to.

### Impact Explanation
If a repository's working tree changes between the user reviewing/selecting specific lines in the Changes view and the moment they click "Commit" or "Discard changes," (e.g., because of a filesystem watcher-triggered refresh after an external process — a build tool, an editor auto-format, a git hook such as `post-checkout`/`post-merge` embedded in a cloned/fetched repository, or another background operation — modifies the file on disk), Desktop can silently generate a patch that includes or discards content the user never reviewed or intended to touch. This falls under "silent corruption of what the user commits or pushes": a user who carefully partially-staged only certain lines can end up committing (or permanently discarding, via `git apply` in `discardChangesFromSelection`) unrelated content that merely happens to occupy the same line index in the new diff.

### Likelihood Explanation
This requires only that the working directory content change (which is entirely plausible for attacker-controlled repository content via git hooks executed during routine operations, or any background process modifying tracked files) while a partial-selection is active and diff refresh happens before the user acts on stale UI, without any local/admin access or leaked credentials, matching the allowed unprivileged-attacker-controls-repo-content threat model. It requires no unusual user steps beyond the ordinary partial-stage-then-commit workflow that Desktop explicitly supports.

### Recommendation
When reloading the diff for a file with an existing partial selection, validate selected indices against the actual line content (or a stable content-derived line identity) rather than only against index "includeability." Any index whose underlying content no longer matches what was selected should be dropped from `divergingLines`, and ideally the UI should surface that the selection was invalidated/reset rather than silently carrying it forward.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file with several add/delete hunks.
2. In the Changes view, partially select a handful of specific added/deleted lines (not "select all"), leaving the file diff open on screen — this is stored as index-based `divergingLines` in `DiffSelection`.
3. Before committing, have an external process (a git hook triggered by e.g. `git status`/`git fetch` performed by Desktop itself, or any other tool) rewrite the file so hunks shift/change content but the file remains modified and the same file stays selected.
4. Desktop's background refresh calls `updateChangesWorkingDirectoryDiff`, which reloads the diff and recomputes `selectableLines`, but keeps the previous `divergingLines` index set unchanged as shown in `app/src/lib/stores/app-store.ts:3478-3497`.
5. Click "Commit" — `formatPatch` in `app/src/lib/patch-formatter.ts:157` includes whatever content now sits at the previously-selected indices, which can be unrelated to what the user actually reviewed and intended to stage, resulting in silently committing (or discarding) unintended content.

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

**File:** app/src/models/diff/diff-selection.ts (L309-320)
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

**File:** app/src/lib/patch-formatter.ts (L266-292)
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
      } else if (selection.isSelected(absoluteIndex)) {
        // Reverse the change (if it was an added line, treat it as removed and vice versa).
        if (line.type === DiffLineType.Add) {
          hunkBuf += `-${line.text.substring(1)}\n`
          newCount++
        } else if (line.type === DiffLineType.Delete) {
          hunkBuf += `+${line.text.substring(1)}\n`
          oldCount++
        } else {
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }

        anyAdditionsOrDeletions = true
```
