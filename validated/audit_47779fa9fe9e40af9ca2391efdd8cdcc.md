Based on my research, I found a plausible analog but could not fully verify one linking step due to running out of tool budget. I'll present the strongest candidate along with an explicit statement of what remains unverified, as instructed for the final iteration.

### Title
Silent line-selection misalignment after diff hunk expansion/merge can cause wrong lines to be committed - (File: app/src/ui/diff/text-diff-expansion.ts, app/src/lib/patch-formatter.ts)

### Summary
GitHub Desktop lets users partially stage a file by checking/unchecking individual diff lines. Selection state (`DiffSelection`) is keyed purely by an *absolute line index* (`hunk.unifiedDiffStart + lineIndex`) computed at diff-parse time. Desktop also lets users expand hidden context ("Load More") via `expandTextDiffHunk`, which can merge two hunks together and re-numbers/­shifts every hunk that comes after the expanded one by `numberOfNewDiffLines`. Nothing in this code path remaps or invalidates a `DiffSelection` that was built against the pre-expansion index space, so a stale selection applied to a post-expansion diff can point at different lines than the ones the user visually checked.

### Finding Description
`expandTextDiffHunk` computes a `numberOfNewDiffLines` delta and shifts every downstream hunk's `unifiedDiffStart`/`unifiedDiffEnd` by that amount: [1](#0-0) 

When the expanded hunk is close enough to a neighboring hunk (`distanceToPrevious <= DefaultDiffExpansionStep`), the two hunks get merged via `mergeDiffHunks`, and `numberOfNewDiffLines` is further adjusted for the removed header line: [2](#0-1) 

`formatPatch` (the function that actually generates the git patch used to build a commit) trusts `hunk.unifiedDiffStart + lineIndex` as the absolute index to query the user's selection: [3](#0-2) 

and `DiffSelection.isSelected` / `isRangeSelected` operate purely on that numeric index with no knowledge of the diff structure that produced it: [4](#0-3) 

I found one place where the developers appear to be aware that expansion invalidates indices — the discard-changes handler explicitly avoids using the expanded diff: [5](#0-4) 

However, I was not able to fully confirm (within the remaining tool budget) whether the analogous "commit with partial selection" path (`app-store.ts` → `formatPatch`) always re-derives `DiffSelection` fresh from the *un-expanded* diff, or whether it can, in some flow, be handed a `DiffSelection` that was built/updated while the on-screen diff was in an expanded/merged state. If the latter is possible, the absolute indices recorded by the UI's `onLineNumberCheckedChanged`/`onDiscardChanges` handlers (`app/src/ui/diff/side-by-side-diff.tsx:935-961`) would no longer line up with the hunk layout used when `formatPatch` runs, because hunk boundaries shifted during merge.

### Impact Explanation
If the selection-to-hunk index correspondence is not preserved end-to-end, the practical effect is a **broken invariant**: the checkboxes the user sees and interacts with no longer correspond to the lines actually written into the outgoing patch. This is exactly the "silent corruption of what the user commits or pushes" impact called out as in-scope — the app would silently include unintended lines (e.g., lines the user thought were deselected) or drop intended ones, with no error, no revert prompt, and no diff-vs-patch consistency check before `git apply`/commit.

An attacker who controls the *content* of a fetched/cloned repository (specifically the shape/size of a tracked file, e.g., one engineered to produce many small hunks separated by short gaps that Desktop will want to merge on expansion) can increase the likelihood that a normal partial-staging workflow crosses this merge boundary, without needing any unnatural user action — expanding a diff and partially staging lines is standard Desktop usage.

### Likelihood Explanation
Medium-low, and explicitly uncertain due to incomplete verification. The arithmetic bug in `expandTextDiffHunk`'s index bookkeeping is real and directly analogous to the report's "denominator missing parentheses" class of bug — a silent, non-crashing miscalculation that corrupts a downstream value (here, line indices instead of a token amount). What I could not confirm is whether Desktop's actual commit flow always discards/rebuilds the selection before calling `formatPatch`, which would fully mitigate the issue in practice (similar to the discard-changes code explicitly doing so). This gap should be resolved by inspecting `app-store.ts`'s commit-creation code path and `changes-list`/`selectable-list` selection lifecycle, which I did not have remaining budget to trace.

### Recommendation
- Ensure every code path that builds a patch for `git apply`/commit (not just discard-changes) always uses the diff object whose hunk indices match the `DiffSelection` being queried — never a diff that has since been expanded/merged.
- Add an invariant check in `formatPatch`/`formatPatchToDiscardChanges` that asserts the total selectable line count of the diff matches what the `DiffSelection` was built against, throwing rather than silently proceeding on mismatch.
- Alternatively, remap `DiffSelection` indices whenever `expandTextDiffHunk` shifts/merges hunks, instead of leaving index continuity as an implicit, undocumented contract between UI state and diff state.

### Proof of Concept
Not executed — this requires exercising the UI expand-then-partial-stage-then-commit flow end to end, which needs the app runtime. Conceptually:
1. Clone/fetch a malicious repo containing a tracked file modified with several small change hunks separated by short (<20 line) gaps of unchanged context.
2. In Desktop's changes view, expand one of the hunks so it merges with its neighbor (`DiffHunkExpansionType.Short` path in `getHunkHeaderExpansionType`, `app/src/ui/diff/text-diff-expansion.ts:113-114`).
3. Deselect a specific line in the (now shifted) hunk region and commit.
4. Inspect the generated patch/commit content versus what was visually deselected in the UI to check for a mismatch — this is the step I was unable to run to confirm exploitability.

Given the unresolved verification gap, treat this as a **candidate** finding requiring runtime confirmation of the commit code path, rather than a fully proven vulnerability.

### Citations

**File:** app/src/ui/diff/text-diff-expansion.ts (L329-350)
```typescript

  // Merge hunks if needed. Depending on whether we need to merge the current
  // hunk and the adjacent, we will strip (or not) the adjacent from the list
  // of hunks, and replace the current one with the merged version.
  if (shouldMergeWithAdjacent && adjacentHunk !== null) {
    if (isExpandingUp) {
      updatedHunk = mergeDiffHunks(adjacentHunk, updatedHunk)
      previousHunksEndIndex = hunkIndex - 1
      followingHunksStartIndex = hunkIndex + 1
    } else {
      previousHunksEndIndex = hunkIndex
      followingHunksStartIndex = hunkIndex + 2
      updatedHunk = mergeDiffHunks(updatedHunk, adjacentHunk)
    }

    // After merging, there is one line less (the Hunk header line from one
    // of the merged hunks).
    numberOfNewDiffLines = numberOfNewDiffLines - 1
  } else {
    previousHunksEndIndex = hunkIndex
    followingHunksStartIndex = hunkIndex + 1
  }
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L352-376)
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1600-1607)
```typescript
    const newSelection = selection
      .withSelectNone()
      .withRangeSelection(startLine, endLine - startLine + 1, true)

    // Pass the original diff (from props) instead of the (potentially)
    // expanded one.
    this.props.onDiscardChanges(this.props.diff, newSelection)
  }
```
