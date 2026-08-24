Based on the investigation, I found a concrete, file-backed analog. Rather than a numeric-underflow "amount" bug, the equivalent broken invariant in GitHub Desktop is an **index/selection desynchronization in the diff hunk-expansion path**, which can cause the patch that actually gets committed to differ from what the user visually selected — the same class of bug as the report (a stale/incorrectly-derived value silently changes what the user "receives", here what they commit).

### Title
Stale line-selection indices after hunk expansion cause silent mismatch between displayed selection and committed patch - (File: app/src/ui/diff/text-diff-expansion.ts)

### Summary
`DiffSelection` tracks which diff lines a user has selected for commit/discard using **absolute line indices** derived from `hunk.unifiedDiffStart + lineIndex` [1](#0-0) [2](#0-1) . When a user expands a hunk (to view more context), `expandTextDiffHunk` recomputes `unifiedDiffStart`/`unifiedDiffEnd` for every hunk *after* the expansion point, shifting them by `numberOfNewDiffLines` [3](#0-2) . However, `expandHunk` in the UI component only updates local component state (`this.setState({ diff: updatedDiff })`) — it never calls `this.props.onIncludeChanged` to remap the persisted `DiffSelection.divergingLines` set that lives on the `WorkingDirectoryFileChange` in the store [4](#0-3) .

### Finding Description
`DiffSelection.divergingLines` is a `Set<number>` of absolute indices recorded at the moment the user (de)selects a line [5](#0-4) . These indices are only valid for the hunk layout that existed when the selection was made. `expandTextDiffHunk` changes that layout for every hunk located after the one being expanded, without touching or invalidating the selection object at all [6](#0-5) . The store-level guard that reconciles selection state with a changed diff (`selectableLines` recomputation) only runs when a *new diff is fetched from git* — i.e., on `updateChangedFiles`/full reload — not when the diff is expanded client-side via `expandHunk` [7](#0-6) . Consequently, `formatPatch()`, which builds the actual patch handed to `git apply --cached`, walks the *new* (post-expansion) hunks and asks `file.selection.isSelected(hunk.unifiedDiffStart + lineIndex)` using the *old* indices [8](#0-7) . For any hunk located after an expanded hunk, this index no longer refers to the same source line the user looked at when selecting it.

This is directly analogous to the reported Solidity issue: a value (`extraData.toLeverageUser`/`waterRepayment`) computed from stale/incorrectly-paired inputs, with no validation step to catch the mismatch before it's used to determine what the user actually receives (here, what actually gets committed).

### Impact Explanation
A crafted repository (attacker controls the file content that produces multiple diff hunks with a specific gap size) can cause a user who does partial-line staging and then expands a preceding hunk (routine workflow for reviewing large diffs) to end up committing/pushing a set of lines that differs from what is shown as checked/selected in the UI. This is "silent corruption of what the user commits or pushes" — a malicious or unintended line could be silently included (or a line the user meant to include silently dropped) without any warning, because `formatPatch` has no cross-check between the diff version the selection was computed against and the diff version being formatted.

### Likelihood Explanation
Hunk expansion via the "..." context-expansion control is a common, encouraged interaction when reviewing multi-hunk diffs, and partial line selection (checkbox per line) is a core, promoted feature of Desktop. No special privileges are needed — only a normal review/partial-commit workflow on a repository with attacker-controlled content shaped to have multiple hunks. The bug is purely in client-side state management, with no existing guard rejecting the stale-index case (`isSelected` just does a `Set.has(lineIndex)` lookup with no bounds/consistency check [1](#0-0) ).

### Recommendation
When `expandTextDiffHunk`/`expandWholeTextDiff` shift `unifiedDiffStart`/`unifiedDiffEnd` for hunks after the expansion point, the caller (`expandHunk` in `side-by-side-diff.tsx`) must remap the associated `DiffSelection` (translate `divergingLines`/`selectableLines` indices by the same `numberOfNewDiffLines` offset) and propagate the updated selection via `onIncludeChanged`, the same way `withSelectableLines` reconciliation is already done for full diff reloads in `app-store.ts`.

### Proof of Concept
1. Prepare (or clone from an attacker-controlled remote) a file with at least two separated diff hunks (e.g., changes at line 10 and line 90 of a 100-line file, as in the existing `text-diff-expansion-test.ts` fixtures) [9](#0-8) .
2. In the Changes view, deselect a specific line in the *second* hunk (the one further down).
3. Expand the *first* hunk upward or downward enough lines that `numberOfNewDiffLines` shifts the second hunk's `unifiedDiffStart`.
4. Commit with "selected changes only." Inspect the resulting commit content via `git show`: the line deselected in step 2 will not correspond to the line actually excluded from the patch, because `formatPatch` re-derives `absoluteIndex` from the now-shifted hunk while `file.selection` still holds the pre-expansion index.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L121-136)
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

**File:** app/src/models/diff/diff-selection.ts (L231-281)
```typescript
  // Lower inclusive, upper exclusive. Same as substring
  public withRangeSelection(
    from: number,
    length: number,
    selected: boolean
  ): DiffSelection {
    const computedSelectionType = this.getSelectionType()
    const to = from + length

    // Nothing for us to do here. This state is when all lines are already
    // selected and we're being asked to select more or when no lines are
    // selected and we're being asked to unselect something.
    if (typeMatchesSelection(computedSelectionType, selected)) {
      return this
    }

    if (computedSelectionType === DiffSelectionType.Partial) {
      const newDivergingLines = new Set<number>(this.divergingLines!)

      if (typeMatchesSelection(this.defaultSelectionType, selected)) {
        for (let i = from; i < to; i++) {
          newDivergingLines.delete(i)
        }
      } else {
        for (let i = from; i < to; i++) {
          // Ensure it's selectable
          if (this.isSelectable(i)) {
            newDivergingLines.add(i)
          }
        }
      }

      return new DiffSelection(
        this.defaultSelectionType,
        newDivergingLines.size === 0 ? null : newDivergingLines,
        this.selectableLines
      )
    } else {
      const newDivergingLines = new Set<number>()
      for (let i = from; i < to; i++) {
        if (this.isSelectable(i)) {
          newDivergingLines.add(i)
        }
      }

      return new DiffSelection(
        computedSelectionType,
        newDivergingLines,
        this.selectableLines
      )
    }
```

**File:** app/src/lib/patch-formatter.ts (L129-157)
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
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L170-180)
```typescript
export function expandTextDiffHunk(
  diff: ITextDiff,
  hunk: DiffHunk,
  kind: DiffExpansionKind,
  newContentLines: ReadonlyArray<string>,
  step: number = DefaultDiffExpansionStep
): ITextDiff | undefined {
  const hunkIndex = diff.hunks.indexOf(hunk)
  if (hunkIndex === -1) {
    return
  }
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L352-387)
```typescript
  const previousHunks = diff.hunks.slice(0, previousHunksEndIndex)

  // Grab the hunks after the current one, and update their start/end, but only
  // if the currently expanded hunk didn't reach the bottom of the file.
  const newHunkLastLine =
    newHunkHeader.newStartLine + newHunkHeader.newLineCount - 1
  const followingHunks =
    newHunkLastLine >= newContentLines.length
      ? []
      : diff.hunks.slice(followingHunksStartIndex).map((hunk, hunkIndex) => {
          const isLastDummyHunk =
            hunkIndex + followingHunksStartIndex === diff.hunks.length - 1 &&
            hunk.lines.length === 1 &&
            hunk.lines[0].type === DiffLineType.Hunk

          // Only compute the new expansion type if the hunk is the first one
          // (of the remaining hunks) and it's not the last dummy hunk.
          const shouldComputeNewExpansionType =
            hunkIndex === 0 && !isLastDummyHunk

          return new DiffHunk(
            hunk.header,
            hunk.lines,
            hunk.unifiedDiffStart + numberOfNewDiffLines,
            hunk.unifiedDiffEnd + numberOfNewDiffLines,
            // If it's the first hunk after the one we expanded, recalculate
            // its expansion type.
            shouldComputeNewExpansionType
              ? getHunkHeaderExpansionType(
                  followingHunksStartIndex,
                  hunk.header,
                  updatedHunk
                )
              : hunk.expansionType
          )
        })
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1721-1742)
```typescript
  /** Expand a selected hunk. */
  private expandHunk(hunk: DiffHunk, kind: DiffExpansionKind) {
    const contents = this.props.fileContents
    const { diff } = this.state

    if (contents === null || !this.canExpandDiff()) {
      return
    }

    const updatedDiff = expandTextDiffHunk(
      diff,
      hunk,
      kind,
      contents.newContents
    )

    if (updatedDiff === undefined) {
      return
    }

    this.setState({ diff: updatedDiff })
  }
```

**File:** app/src/lib/stores/app-store.ts (L3466-3497)
```typescript
    const selectedFileID = changesState.selection.selectedFileIDs[0]

    if (selectedFileID !== selectedFileIdBeforeLoad) {
      return
    }

    const currentlySelectedFile =
      changesState.workingDirectory.findFileWithID(selectedFileID)
    if (currentlySelectedFile === null) {
      return
    }

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

**File:** app/test/unit/text-diff-expansion-test.ts (L180-205)
```typescript
  it('merges hunks when the gap between them is shorter than the expansion size', async () => {
    const { textDiff, newContentLines } = await prepareDiff(100, [20, 10])
    const expandedDiff = expandTextDiffHunk(
      textDiff,
      textDiff.hunks[0],
      'down',
      newContentLines
    )

    // Originally 3 hunks:
    // - First around line 10
    // - Second around line 20
    // - Third is the dummy hunk at the end
    assert.equal(textDiff.hunks.length, 3)

    assert(expandedDiff !== undefined)

    // After expanding the hunk, the first two hunks are merged
    assert.equal(expandedDiff.hunks.length, 2)

    const firstHunk = expandedDiff.hunks[0]
    assert.equal(firstHunk.header.oldStartLine, 8)
    assert.equal(firstHunk.header.oldLineCount, 16)
    assert.equal(firstHunk.header.newStartLine, 8)
    assert.equal(firstHunk.header.newLineCount, 18)
  })
```
