## Title
Hunk expansion re-anchors diff-line indices without revalidating an in-progress partial-selection, allowing a deliberately excluded line to be silently re-included in a commit — (File: app/src/ui/diff/text-diff-expansion.ts)

### Summary
This mirrors the Weiroll `writeTuple()` flaw: an index into a persistent "state" structure (there, `state[idx]`; here, `DiffSelection`'s `divergingLines` set of absolute line indices) is computed and consumed without re-validating/re-deriving it against the structure it is meant to describe once that structure has been rewritten. In Weiroll, `idx` wasn't masked with `IDX_VALUE_MASK` before indexing `state`. In Desktop, `hunk.unifiedDiffStart + arrayIndex` is used as the stable "address" for a selected/deselected diff line inside `DiffSelection`, but `expandTextDiffHunk` rewrites `hunk.lines` (inserting new lines) while leaving `unifiedDiffStart` unchanged, so previously-recorded indices in `DiffSelection` silently point at different lines after expansion.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts:41-332`) tracks user (de)selected lines purely as a `Set<number>` of absolute indices, with no reference back to line content or identity: [1](#0-0) 

Those absolute indices are computed everywhere as `hunk.unifiedDiffStart + <position within hunk.lines>`, e.g. in `formatPatch`: [2](#0-1) 

and in the UI when applying a selection change: [3](#0-2) 

The hunk expansion logic in `expandTextDiffHunk` inserts new context lines into `hunk.lines` at the front (when expanding up) or splices them into the middle when merging with an adjacent hunk, but keeps `hunk.unifiedDiffStart` the same for the (up-)expanded hunk: [4](#0-3) [5](#0-4) 

Because `unifiedDiffStart` is unchanged while new lines are prepended/inserted before existing ones, every existing line's absolute index (`unifiedDiffStart + arrayIndex`) shifts by the number of newly-inserted lines. `DiffSelection`'s `divergingLines` set, however, is never recomputed when the diff is expanded — the caller only does `this.setState({ diff: updatedDiff })` in `expandHunk`, with no corresponding update to `file.selection`: [6](#0-5) 

The only place indices are revalidated is `populateChangesForFile`/similar code in `app-store.ts` that recomputes `selectableLines` from a freshly-loaded diff (on git-status refresh), not on hunk expansion: [7](#0-6) 

That revalidation only filters out indices that fall outside the new `selectableLines` set — it does not detect the case where an old index still coincidentally maps to a *different but still selectable* line after the shift (e.g. an adjacent `Add`/`Delete` line that took over that slot), which is exactly the silent-corruption case (analogous to `idx` accidentally landing on a different, still-valid `state` slot in Weiroll rather than reverting or erroring).

### Impact Explanation
If a user opens the diff for a file modified by a possibly-malicious commit/PR in a cloned or fetched repository, deselects a specific `Add` line they do not want to stage (e.g., a suspicious script fragment, secret, or backdoor line embedded by the attacker among otherwise-legitimate changes), and then expands the same hunk upward/downward for extra context before committing, the stale absolute index recorded in `DiffSelection.divergingLines` can now point to a different line in the re-numbered `hunk.lines` array. Depending on `defaultSelectionType`, this can cause the exact line the user explicitly excluded to be silently re-included in the outgoing patch (`formatPatch`) and staged/committed, while the user's UI/mental model still believes it is excluded. This is a silent corruption of what the user commits, driven entirely by content the attacker controls (the diff/hunk layout of the file they pushed), matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Requires: (1) a file whose diff has hunks eligible for "expand" (not uncommon — any diff not already showing full file context), (2) the user performing a partial line selection (deselecting/selecting specific lines) before expanding a hunk, which is a normal, expected Desktop workflow for careful reviewers deciding what to stage. No special privileges, no local/physical access, and no social engineering beyond ordinary code review are needed beyond the attacker crafting the diff content that a careful reviewer would want to selectively exclude. The main uncertainty (not fully verifiable from the indexed code alone) is the exact frequency with which `divergingLines` values collide with a *different valid* selectable line versus simply becoming out-of-range and filtered by `withSelectableLines`; this depends on hunk shapes and expansion direction and would need to be confirmed by building/running Desktop.

### Recommendation
Re-anchor selection state to something stable across hunk rewrites (e.g., original file line numbers per side, or a stable per-`DiffLine` identity) instead of an absolute position computed from `unifiedDiffStart + arrayIndex`. At minimum, whenever `expandTextDiffHunk`/`mergeDiffHunks` mutate `hunk.lines` and/or shift positions, the call sites (`expandHunk` in `app/src/ui/diff/side-by-side-diff.tsx`) must remap the existing `DiffSelection.divergingLines` indices by the same offset/merge transformation applied to the lines, rather than leaving the selection untouched. This is directly analogous to the Weiroll fix: don't let a raw offset be trusted across a rewrite of the addressed structure — always recompute/re-mask it consistently with the structure's current shape.

### Proof of Concept
Conceptual reproduction (verification of exact runtime behavior requires building/running Desktop, which is outside static-index analysis):
1. Clone/open a repository containing a file with a diff hunk of the form `[header, ctx, Add(malicious-line), Add(benign-line), ctx]` at `unifiedDiffStart = N`.
2. In the Changes view, deselect `Add(malicious-line)` (absolute index `N+2`), leaving `Add(benign-line)` (index `N+3`) selected.
3. Trigger "expand hunk up" so that `expandTextDiffHunk` inserts `k` new context lines before `ctx`, producing `[header, new1..newk, ctx, Add(malicious-line), Add(benign-line), ctx]`, still reporting `unifiedDiffStart = N`.
4. Now the array positions shift: what used to be at position 2 (`Add(malicious-line)`, absolute index `N+2`) is now at array position `2+k`, so its "current" absolute index would need to be `N+2+k`, but `DiffSelection.divergingLines` still contains the stale value `N+2`, which after expansion aligns with a *different* line (one of `new1..newk` or, in bigger hunks, a genuinely different Add/Delete line).
5. Commit the file. `formatPatch` computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` for the new line array and calls `file.selection.isSelected(absoluteIndex)`; because the stale index `N+2` no longer identifies `Add(malicious-line)`, that line is now treated according to the *default* selection state (frequently "selected"), and gets included in the generated patch/commit despite the user's explicit deselection.

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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1335-1354)
```typescript
  private onEndSelection = () => {
    let selection = this.getSelection()
    const { temporarySelection } = this.state

    if (selection === undefined || temporarySelection === undefined) {
      return
    }

    const { from: tmpFrom, to: tmpTo, isSelected } = temporarySelection

    const fromLine = Math.min(tmpFrom, tmpTo)
    const toLine = Math.max(tmpFrom, tmpTo)

    for (let line = fromLine; line <= toLine; line++) {
      selection = selection.withLineSelection(line, isSelected)
    }

    this.props.onIncludeChanged?.(selection)
    this.setState({ temporarySelection: undefined })
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

**File:** app/src/ui/diff/text-diff-expansion.ts (L302-325)
```typescript
  const allHunkLinesButFirst = hunk.lines.slice(1)

  // Update the diff lines of the hunk with the new lines
  const updatedHunkLines = isExpandingUp
    ? [newDiffHunkLine, ...newLineDiffs, ...allHunkLinesButFirst]
    : [newDiffHunkLine, ...allHunkLinesButFirst, ...newLineDiffs]

  let numberOfNewDiffLines = updatedHunkLines.length - hunk.lines.length

  const previousHunk = hunkIndex === 0 ? null : diff.hunks[hunkIndex - 1]
  const expansionType = getHunkHeaderExpansionType(
    hunkIndex,
    newHunkHeader,
    previousHunk
  )

  // Update the hunk with all the new info (header, lines, start/end...)
  let updatedHunk = new DiffHunk(
    newHunkHeader,
    updatedHunkLines,
    hunk.unifiedDiffStart,
    hunk.unifiedDiffEnd + numberOfNewDiffLines,
    expansionType
  )
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L356-391)
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

  // Create the new list of hunks of the diff, and the new diff text
  const newHunks = [...previousHunks, updatedHunk, ...followingHunks]
  const newDiffText = getDiffTextFromHunks(newHunks)
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
