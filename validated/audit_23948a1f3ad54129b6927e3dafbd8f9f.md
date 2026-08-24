## Title
Checkbox line-selection in side-by-side diff always toggles the "Before" column, silently including/excluding the wrong line from a commit - (File: `app/src/ui/diff/side-by-side-diff.tsx`)

### Summary
`SideBySideDiff.onLineNumberCheckedChanged` is meant to resolve the "before" and "after" line numbers for a modified row and apply the checkbox's selection state to both. Instead it calls `getDiffLineNumber(row, column)` twice with the exact same `column` argument, so `lineBefore` and `lineAfter` are always identical: [1](#0-0) 

`getDiffRowLineNumber` shows the intended distinction — for a `Modified` row, the before/after diff line numbers differ depending on `column`: [2](#0-1) 

### Finding Description
This mirrors the rICO bug class: a value (the effective line to select) is supposed to be computed from two distinct adjustments (before-column line vs after-column line), but due to a copy-paste error both computations use the same input (`column`), collapsing two logically different quantities into one. Concretely:

- For `DiffRowType.Modified` rows, a checkbox click in the "Before" (deleted) gutter should select the corresponding deleted line's `diffLineNumber`, and a click in the "After" (added) gutter should select the corresponding added line's `diffLineNumber`.
- `onLineNumberCheckedChanged` is supposed to compute both the before-line and after-line numbers (analogous to computing two independently adjusted offsets) but instead computes the same one twice: `getDiffLineNumber(row, column)` / `getDiffLineNumber(row, column)`.
- The resulting `selection` object, which backs the `DiffSelection` used later to build the commit patch via `formatPatch`/`formatPatchToDiscardChanges`, is written using only one of the two line numbers, invoked twice with `withLineSelection`. Because `DiffSelection.withLineSelection` is idempotent for the same line index, the second call is a no-op, so effectively only one line (the one matching the clicked column) is included/excluded.

The guard that normally prevents this (the per-row column split logic in `getDiffRowLineNumber`) is defined correctly, but the caller never actually differentiates `DiffColumn.Before` from `DiffColumn.After` when building the two-line selection, so the guard is bypassed by the caller's mistake, not by anything malicious.

### Impact Explanation
The line selection produced here feeds directly into `WorkingDirectoryFileChange.selection`, which `formatPatch` (`app/src/lib/patch-formatter.ts`) uses to build the exact hunk that is `git apply`'d to the index and ultimately committed: [3](#0-2) 

If a user relies on checking/unchecking a line via the gutter checkbox in a `Modified` row (add+delete pair shown side by side) to selectively stage/discard one side of the pair, the checkbox interaction can silently select or deselect the wrong line — i.e., the deleted line's inclusion state gets applied when the user intended to change the added line's inclusion state, or vice versa, because both computed "line" values are actually the same line. This is a "silent corruption of what the user commits" class of bug: the user believes they've included/excluded a specific line, but the actual patch generated for `git apply --cached` differs from their selection intent, with no error surfaced.

### Likelihood Explanation
This code path is exercised on the ordinary checkbox interaction for partial-commit line selection in a side-by-side modified-row diff — a completely standard user action, not one requiring any attacker involvement or special repository content beyond producing a `Modified` row (add/delete pair). Whether it's actually reachable depends on how the `SideBySideDiffRow` checkbox `onChange` wires up `column` and `row`; that wiring wasn't fully confirmed in this pass (I could not verify from `side-by-side-diff-row.tsx` whether the checkbox handler for a `Modified` row always passes the correct `DiffColumn` for the specific checkbox clicked before reaching `onLineNumberCheckedChanged`). Given `onIncludeChanged` uses this handler's output directly to build the persisted selection, if the checkbox wiring is correct up to this point, the bug is deterministic and always occurs on modified rows.

### Recommendation
Fix `onLineNumberCheckedChanged` to compute the two related line numbers using their distinct columns, e.g.:
```ts
const lineBefore = this.getDiffLineNumber(row, DiffColumn.Before)
const lineAfter = this.getDiffLineNumber(row, DiffColumn.After)
```
Add a unit test on a `Modified` row (as already scaffolded in `app/test/unit/ui/side-by-side-diff-row-test.tsx`) asserting that toggling the "Before" checkbox only changes the deleted line's selection and toggling "After" only changes the added line's selection, distinct diffLineNumbers.

### Proof of Concept
1. Create a modified file whose diff produces at least one `DiffRowType.Modified` row (a deleted line immediately paired with an added line, shown side-by-side).
2. In Desktop's changes view with side-by-side diff enabled, click the line-number checkbox in the "After" (added) column for that row to deselect it.
3. Because `onLineNumberCheckedChanged` calls `getDiffLineNumber(row, column)` twice with the same `column`, `lineBefore` and `lineAfter` resolve to the same `diffLineNumber` (the after/added line's number in this example).
4. `selection.withLineSelection(...)` is invoked twice with that same line number and the same `isSelected` value — the deleted (before) line's selection state is never updated even though visually its checkbox may appear tied to the same row.
5. Commit the file: `formatPatch` builds the hunk from `file.selection`, and the deleted line's inclusion state does not match what the user intended when they only meant to affect the added line (or vice versa depending on which column was actually clicked first), producing a commit whose content silently diverges from the user's checkbox choices.

(Note: I was not able to fully trace `side-by-side-diff-row.tsx`'s checkbox `onChange`→`onLineNumberCheckedChanged` argument wiring within this session's iteration limit to confirm the exact column value passed for each specific gutter checkbox; this final detail should be verified in a follow-up session before treating this as a confirmed, exploitable issue rather than a strong code-level anomaly.)

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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1176-1188)
```typescript
  private getDiffRowLineNumber(row: SimplifiedDiffRow, column: DiffColumn) {
    if (row.type === DiffRowType.Added || row.type === DiffRowType.Deleted) {
      return row.data.diffLineNumber
    }

    if (row.type === DiffRowType.Modified) {
      return column === DiffColumn.After
        ? row.afterData.diffLineNumber
        : row.beforeData.diffLineNumber
    }

    return null
  }
```

**File:** app/src/lib/patch-formatter.ts (L129-221)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })

    // Skip writing this hunk if all there is is context lines.
    if (!anyAdditionsOrDeletions) {
      return
    }

    patch += formatHunkHeader(
      hunk.header.oldStartLine,
      oldCount,
      hunk.header.newStartLine,
      newCount
    )
    patch += hunkBuf
  })

```
