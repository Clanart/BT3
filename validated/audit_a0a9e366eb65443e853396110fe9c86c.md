### Title
Line-selection indices become stale (unmapped) after diff hunk expansion, silently changing which lines get staged/committed - (File: `app/src/ui/diff/text-diff-expansion.ts` / `app/src/models/diff/diff-selection.ts`)

### Summary
The Solidity report's core defect is an accounting mismatch: a value computed for one purpose (`amountLeft`, the *unallocated remainder*) is reused as if it were a different value (*the amount to withdraw*), so downstream logic silently acts on the wrong quantity. GitHub Desktop has a structurally identical class of bug in the line-selection model that backs partial commits: `DiffSelection` stores per-line selection state keyed by an **absolute line index** (`hunk.unifiedDiffStart + lineIndex`) [1](#0-0) , and `formatPatch` later reads that same absolute index to decide what to include in the generated patch [2](#0-1) . When a hunk is expanded (`expandTextDiffHunk`/`expandWholeTextDiff`), the function recomputes `unifiedDiffStart`/`unifiedDiffEnd` for every hunk *after* the expanded one by shifting them by `numberOfNewDiffLines` [3](#0-2) , but it returns only a new `diff` object — it never touches or remaps the `DiffSelection` that the file's `divergingLines` set is stored in.

### Finding Description
`DiffSelection` is an opaque set of absolute integer indices with no knowledge of hunk boundaries [4](#0-3) ; its `isSelected`/`isSelectable` calls are pure index lookups [5](#0-4) . The mapping from "index" to "which diff line it represents" is entirely defined by `hunk.unifiedDiffStart + lineIndex` at the time the selection was made.

`expandTextDiffHunk` inserts new context lines into a hunk and then shifts every *following* hunk's `unifiedDiffStart`/`unifiedDiffEnd` by `numberOfNewDiffLines` [6](#0-5) [7](#0-6) . `onExpandHunk`/`onExpandWholeFile` in `side-by-side-diff.tsx` take this new diff and put it directly into component state via `this.setState({ diff: updatedDiff })` [8](#0-7) [9](#0-8) . Nowhere in this path is `file.selection` (the actual `DiffSelection` used later by `formatPatch`) re-derived or shifted to match the new indices — the only place selection indices are ever revalidated is `withSelectableLines`, which is invoked from `app-store.ts` only when the diff is reloaded from disk after a file-watcher event, and it merely *filters* existing diverging indices against a freshly computed `selectableLines` set; it does not *remap* them [10](#0-9) [11](#0-10) .

Concretely: a user partially selects a file for commit (some added/deleted lines checked, others not), giving a `DiffSelection.divergingLines` set of absolute indices computed against the *pre-expansion* hunk layout. The user then expands an earlier hunk (adds context lines) via the "Expand" gutter control or "Expand Whole File." Every hunk after the expanded one now has its lines shifted to new absolute indices, but the `DiffSelection` object attached to the `WorkingDirectoryFileChange` still contains the *old* indices. Line N that used to be "the deleted line the user unchecked" is now, at index N, a completely different line (often a newly-inserted context line, or a shifted add/delete line from the following hunk) — while the line the user actually meant to (de)select is now at a different index that isn't in `divergingLines` at all. Because `formatPatch` looks up selection purely by `absoluteIndex = hunk.unifiedDiffStart + lineIndex` [2](#0-1) , the patch fed into `git apply --cached` now stages/excludes the wrong lines with no error or warning — an exact structural analog of the report's `positionAmounts[numPositions - 1] = amountLeft` bug: a value that shifted meaning is fed unmodified into a consumer that assumes it's still aligned.

### Impact Explanation
This falls squarely into "silent corruption of what the user commits" from the given valid-impact list. A user could uncheck a sensitive line (secret, debug code, unfinished change) in one hunk, expand a different hunk to review more context, and then commit — with the checked/unchecked state now applying to different lines than intended, silently including content the user explicitly excluded (or vice versa, silently dropping content the user wanted committed). There's no attacker/remote input required to trigger it — normal Desktop UI usage (expand hunk → adjust selection → commit) is sufficient, though the report's threat model here is about correctness/integrity of the tool's core "what you select is what you commit" invariant.

### Likelihood Explanation
Expanding hunks and doing partial-line staging are both first-class, commonly used Desktop features (the diff viewer explicitly supports per-line checkboxes and "Expand whole file"/"Expand Up/Down" — see `onExpandHunk`, `onExpandWholeFile`, `onLineNumberCheckedChanged` in `side-by-side-diff.tsx`). Any partial-selection workflow that also uses hunk expansion on the same file before finalizing the commit will hit this path; no unusual repository content or timing is needed.

### Recommendation
When `expandTextDiffHunk`/`expandWholeTextDiff` produce a new diff with shifted `unifiedDiffStart`/`unifiedDiffEnd` values, the caller must remap the associated `DiffSelection.divergingLines` set by the same per-hunk offsets (or, more robustly, key `DiffSelection` off a stable line identity — e.g., old/new line number pairs — rather than a position that depends on volatile hunk layout). At minimum, `onExpandHunk`/`onExpandWholeFile` should translate every existing diverging index greater than the expansion point by `numberOfNewDiffLines` before committing the new diff to state, mirroring exactly how the hunks themselves are shifted.

### Proof of Concept
1. Open a modified file with at least two hunks in Desktop's diff view; partially select lines in the second hunk (e.g., uncheck one deleted line), leaving `DiffSelection.divergingLines` populated with absolute indices belonging to hunk 2.
2. Expand hunk 1 upward or downward (`onExpandHunk` → `expandTextDiffHunk`), which inserts new context lines into hunk 1 and shifts hunk 2's `unifiedDiffStart`/`unifiedDiffEnd` forward by `numberOfNewDiffLines` [7](#0-6) .
3. The `WorkingDirectoryFileChange.selection` object is untouched — it still has the pre-expansion absolute indices.
4. Commit the file. `applyPatchToIndex`/`formatPatch` computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` using the *new* (shifted) hunk positions and checks `file.selection.isSelected(absoluteIndex)` [2](#0-1) , which now resolves against the wrong lines: the line the user unchecked gets committed, and/or a different, unintended line gets excluded.

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

**File:** app/src/ui/diff/text-diff-expansion.ts (L309-309)
```typescript
  let numberOfNewDiffLines = updatedHunkLines.length - hunk.lines.length
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L356-387)
```typescript
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1498-1519)
```typescript
  private onExpandWholeFile = () => {
    const contents = this.props.fileContents
    const { diff } = this.state

    if (contents === null || !this.canExpandDiff()) {
      return
    }

    const updatedDiff = expandWholeTextDiff(diff, contents.newContents)

    if (updatedDiff === undefined) {
      return
    }

    this.diffToRestore = diff

    this.ariaLiveChangeSignal = !this.ariaLiveChangeSignal
    this.setState({
      diff: updatedDiff,
      ariaLiveMessage: 'Expanded',
    })
  }
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
