### Title
Line-selection checkbox always toggles the same column, causing wrong lines to be silently included/excluded from commits - (File: `app/src/ui/diff/side-by-side-diff.tsx`)

### Summary
In `SideBySideDiff.onLineNumberCheckedChanged`, the handler that updates line-selection state when a user (un)checks a diff-line checkbox computes two values, `lineBefore` and `lineAfter`, that are supposed to represent the "before" (deleted) and "after" (added) line numbers of a modified row. Both are computed by calling `this.getDiffLineNumber(row, column)` with the exact same `row`/`column` arguments — a copy-paste error identical in shape to the Leverager `amountOut0`/`amountOut1` mix-up: two branches that should use two distinct inputs collapse into one. [1](#0-0) 

### Finding Description
`onLineNumberCheckedChanged` is invoked from `SideBySideDiffRow` whenever the user clicks a checkbox next to either the "before" or "after" line of a diff row:
```ts
private onLineNumberCheckedChanged = (
  row: number,
  column: DiffColumn,
  isSelected: boolean
) => {
  ...
  const lineBefore = this.getDiffLineNumber(row, column)
  const lineAfter = this.getDiffLineNumber(row, column)

  if (lineBefore !== null) {
    selection = selection.withLineSelection(lineBefore, isSelected)
  }
  if (lineAfter !== null) {
    selection = selection.withLineSelection(lineAfter, isSelected)
  }

  this.props.onIncludeChanged(selection)
}
``` [2](#0-1) 

`getDiffLineNumber(rowNumber, column)` returns a line number that depends on both `rowNumber` and the `column` (`DiffColumn.Before` vs `DiffColumn.After`) — the two columns index into different underlying diff-line numbers (`oldLineNumber` vs `newLineNumber`) for `Modified` rows, which pair a deleted line and an added line on the same visual row. [3](#0-2) [4](#0-3) 

Because `lineBefore` and `lineAfter` are computed with identical `(row, column)` inputs, they always evaluate to the same line number — whichever line corresponds to the column the user actually clicked. The intended behavior (calling `getDiffLineNumber(row, DiffColumn.Before)` and `getDiffLineNumber(row, DiffColumn.After)` respectively) never occurs. Existing selection-tracking guards (`DiffSelection.withLineSelection`, `isInSelection`) operate purely on whatever line number they're given — they have no way to detect that the wrong/duplicate line number was passed in, so nothing downstream catches the mistake. [5](#0-4) 

### Impact Explanation
The final committed patch is built by `formatPatch`, which walks each diff line and checks `file.selection.isSelected(absoluteIndex)` to decide whether to include an addition/deletion or fold it back into context. [6](#0-5) 
If the checkbox handler never actually updates the counterpart line's selection state for `Modified` rows (paired add/delete lines on one visual row), a user can check/uncheck a box in the UI believing both the old and new line are toggled together, while in reality only the line tied to the clicked column changes. This produces a patch whose content silently diverges from what the user saw/intended in the UI — i.e., silent corruption of what gets committed or pushed, which is squarely within the accepted impact category (unprivileged, no local access needed, purely from using the app's own diff-selection UI).

### Likelihood Explanation
This path triggers on ordinary, expected user interaction — clicking a per-line inclusion checkbox in the side-by-side diff view on any "Modified" row (a line replaced by another line), which is one of the most common diff shapes. No attacker-controlled repository content is strictly required to trigger the bug (it's a UI logic bug), but a malicious/crafted diff (e.g., from a fetched branch or PR) that produces many adjacent Modified rows increases the chance a user relies on partial-selection commit and unknowingly commits/pushes unintended hunks. This is a deterministic bug in normal control flow, not a race condition, so likelihood of triggering is high whenever partial line selection is used on modified rows.

### Recommendation
Fix the copy-paste error by passing the correct column to each call:
```ts
const lineBefore = this.getDiffLineNumber(row, DiffColumn.Before)
const lineAfter = this.getDiffLineNumber(row, DiffColumn.After)
```
Add a regression test asserting that toggling a checkbox on a `Modified` row's before/after checkbox independently updates only the corresponding line's selection state, and that both states can be set independently (since a `Modified` row visually pairs two distinct lines that may need independent inclusion/exclusion).

### Proof of Concept
1. Open a repository with a working-directory change that produces a "replaced line" (delete + add on the same visual row in side-by-side view), e.g. change `foo` to `bar` on a single line.
2. In the Changes view, switch to side-by-side diff and partially stage: uncheck only the "before" (deleted) checkbox on the modified row.
3. Expected: only the before-line's selection state changes; the after-line (added) selection is untouched by this specific click.
4. Because `getDiffLineNumber(row, column)` is called twice with the same `column` (the one actually clicked), `lineBefore` and `lineAfter` resolve to the same line number — the counterpart line is never touched at all through this code path, meaning its selection can silently retain a stale state from a prior operation.
5. Commit with the partial selection; inspect the generated patch via `formatPatch`/`git diff --cached`. The staged hunk's before/after line-count and content can differ from what the checkboxes visually indicated, since only one of the two paired lines actually had its independent selection state updated by the click. [7](#0-6)

### Citations

**File:** app/src/ui/diff/side-by-side-diff.tsx (L935-961)
```typescript
  private onLineNumberCheckedChanged = (
    row: number,
    column: DiffColumn,
    isSelected: boolean
  ) => {
    if (this.props.onIncludeChanged === undefined) {
      return
    }

    let selection = this.getSelection()
    if (selection === undefined) {
      return
    }

    const lineBefore = this.getDiffLineNumber(row, column)
    const lineAfter = this.getDiffLineNumber(row, column)

    if (lineBefore !== null) {
      selection = selection.withLineSelection(lineBefore, isSelected)
    }

    if (lineAfter !== null) {
      selection = selection.withLineSelection(lineAfter, isSelected)
    }

    this.props.onIncludeChanged(selection)
  }
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1050-1066)
```typescript
    if (row.type === DiffRowType.Modified) {
      return {
        ...row,
        beforeData: this.getRowDataPopulated(
          row.beforeData,
          numRow,
          DiffColumn.Before,
          this.state.beforeTokens
        ),
        afterData: this.getRowDataPopulated(
          row.afterData,
          numRow,
          DiffColumn.After,
          this.state.afterTokens
        ),
      }
    }
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1157-1160)
```typescript
  private getDiffLineNumber(
    rowNumber: number,
    column: DiffColumn
  ): number | null {
```

**File:** app/src/lib/patch-formatter.ts (L143-201)
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
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }
```
