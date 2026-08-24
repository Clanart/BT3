## Finding [1](#0-0) 

The diff-selection model in GitHub Desktop tracks which lines a user has selected for partial commit **purely by numeric line index**, not by line content. When a file's diff is reloaded (which Desktop does automatically after almost any git/filesystem event while a file is open in the Changes view), the code only reconciles the set of *selectable* indices — it does not verify that a previously *selected* index still refers to the same line content.

### Title
Stale index-based diff-line selection can silently commit unintended content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`updateChangesWorkingDirectoryDiff` reloads a file's diff (e.g. after any working-directory change is detected) and preserves the user's existing partial line selection by intersecting it with the new diff's `selectableLines` set: [1](#0-0) 

`DiffSelection.withSelectableLines` only removes previously-diverging indices that are no longer selectable at all — it does not check whether the *content* at a still-selectable index has changed: [2](#0-1) 

`DiffSelection.isSelected`/`isRangeSelected` and ultimately `formatPatch` consume these raw indices to decide which lines go into the generated patch that is staged/committed: [3](#0-2) 

### Finding Description
This is structurally the same class of bug as the Yieldy `transferFrom` issue: one quantity is validated (`selectableLines` membership — "is this index still an includeable line at all?") while a *different, more consequential* quantity is what actually gets acted upon (the semantic content of the line at that index, used to build the real patch that is applied to the git index). The code comment in `app-store.ts` itself acknowledges the gap:

> "The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."

Because `divergingLines` is a bare `Set<number>` with no association to line text/hash, if the diff is reloaded and hunks shift (lines inserted/removed above a previously-selected hunk, or a hunk's content changes while line count/position coincidentally stays "selectable"), the same index can now point at an entirely different line. `formatPatch`/`formatPatchToDiscardChanges` will then include or exclude that different line based on the stale selection, with no error and no additional user confirmation.

### Impact Explanation
If the working-directory file content changes between the time the diff is rendered/selected and the time the user clicks "Commit" (e.g. via a build tool, watcher-triggered normalization, or content rewritten as a side effect of another git operation Desktop performs against the repository — merge/rebase/checkout hooks, LFS smudge filters, etc., all of which can be influenced by a cloned/fetched repository's tracked configuration), the resulting commit/patch can silently contain lines the user never reviewed or intended to stage, or omit lines they did intend to include. This is exactly the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Medium/low-likelihood but plausible: it requires the working tree content to change concurrently with an open partial selection, which is realistic for repositories with filters/hooks (`.gitattributes` smudge/clean filters, `core.autocrlf`, husky-style hooks installed via a repo's own `package.json`) that a hostile repository author can configure. Desktop's own comment confirms this is a recognized, unresolved gap rather than a hardened invariant.

### Recommendation
Associate selection state with line content (e.g. hash of line text plus position) rather than raw numeric index, or invalidate/collapse any partial selection whenever the underlying diff changes in a way that alters line positions, prompting the user to re-review before committing.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file so it has a multi-hunk diff.
2. Select only specific lines within a hunk for partial commit (`DiffSelectionType.Partial`).
3. Before committing, trigger a change to the same file's content that keeps the same set of "selectable" indices but shifts what each index represents (e.g. via a configured clean/smudge filter or a script hook that rewrites the file).
4. Commit: `formatPatch` uses the stale `divergingLines` indices against the new hunk content, staging different lines than what the user selected/reviewed, with no warning. [1](#0-0) [4](#0-3)

### Citations

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

**File:** app/src/models/diff/diff-selection.ts (L120-136)
```typescript

  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
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

**File:** app/src/lib/patch-formatter.ts (L143-171)
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

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
```
