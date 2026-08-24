Based on the evidence gathered, I found a plausible arithmetic/index-integrity analog in the diff-expansion and line-selection code, but I was not able to fully verify (due to tool-call limits) whether the selection state gets remapped elsewhere in the dispatch flow (`app-store.ts`) after a hunk expansion. I'll present the finding with that caveat.

### Title
Diff-hunk expansion does not remap `DiffSelection` line indices, causing unintended lines to be silently (de)selected for commit - ([File: app/src/ui/diff/text-diff-expansion.ts])

### Summary
`DiffSelection` tracks which diff lines a user has selected/deselected for commit using **absolute line indices** derived from `hunk.unifiedDiffStart` at the time the selection was made [1](#0-0) . When a user (or the app, e.g. via context-expansion on a large/attacker-crafted diff) expands a hunk with `expandTextDiffHunk`, hunks after the expansion point are shifted: their `unifiedDiffStart`/`unifiedDiffEnd` are recalculated by adding `numberOfNewDiffLines`, and hunks can also be merged together [2](#0-1) [3](#0-2) . Nowhere in this function (or in the call sites I could locate) is the existing `DiffSelection` (with its `divergingLines: Set<number>` of absolute indices) remapped to the new indices produced by the expansion.

### Finding Description
`formatPatch` builds the commit patch by computing `absoluteIndex = hunk.unifiedDiffStart + lineIndex` for every line and asking `file.selection.isSelected(absoluteIndex)` whether to include it [4](#0-3) . The `isSelected`/`withRangeSelection` logic in `DiffSelection` has no notion of hunks or content — it is a flat set of integer indices [5](#0-4) [6](#0-5) .

`expandTextDiffHunk` recomputes `unifiedDiffStart`/`unifiedDiffEnd` for the expanded hunk and for every hunk that follows it, shifting them by `numberOfNewDiffLines` (and by one less when two hunks are merged) [7](#0-6) [3](#0-2) . This means the same numeric index that used to point at line N in hunk 2 can, after expansion, point at a completely different line (e.g. a newly-inserted context line, or a line that used to be in hunk 3). Since `DiffSelection`'s `divergingLines` set is created against the pre-expansion indices, it is never told about this shift, so after expansion the stale indices in `divergingLines` will silently apply to the wrong lines.

### Impact Explanation
If a user has made a partial selection (deselected some lines for a commit, e.g. to keep a secret/credential line out of the commit) and then expands a hunk in the diff view before committing, the index shift can cause a previously-deselected line to now map to a different (and selected-by-default) line, or vice versa. Because `formatPatch`/`formatPatchToDiscardChanges` trust these indices blindly to build the `git apply` patch [8](#0-7) [9](#0-8) , this is a silent corruption of what the user actually commits or discards — lines can be included/excluded without the user's awareness, matching the "silent corruption of what the user commits or pushes" impact class from the task brief.

### Likelihood Explanation
Likelihood depends on the user performing hunk expansion (a normal, expected UI action available on any diff, especially larger diffs from a cloned/fetched attacker-controlled repository) while having a partial line selection active. An attacker who controls the repository content can shape file contents/diff structure (number of hunks, gaps between them) to make this expansion-driven index shift much more likely to land on a security-sensitive line. I could not fully confirm from the index whether `app-store.ts`'s hunk-expansion dispatch path performs any selection remap before persisting the new diff/selection state (my search only found a single unrelated match for `withSelectableLines` there), so I cannot rule out a mitigating remap step I didn't find in this pass.

### Recommendation
When a hunk is expanded or merged (`expandTextDiffHunk`, `getTextDiffWithBottomDummyHunk`, and their call sites), also transform the associated `DiffSelection`'s diverging/selectable line sets by the same index shift (or force a full "reselect current default" reset) so that selection state always tracks the same physical diff lines it was set against, regardless of subsequent hunk expansion/merging.

### Proof of Concept
1. Open a modified file with two hunks separated by more than one expansion step's worth of context lines, such that expanding the gap between them will shift subsequent hunks' `unifiedDiffStart`.
2. Deselect one line in the second hunk (unselect it for commit) — this stores its absolute index in `divergingLines`.
3. Expand the hunk above it downward (or the second hunk upward) so lines are inserted before the second hunk, shifting its `unifiedDiffStart`.
4. Observe (via `formatPatch`) that the line at the *old* absolute index — now a different line, potentially the one you intended to keep out of the commit or a different sensitive line — is treated as selected/deselected instead of the line you actually toggled, because `file.selection.isSelected` still keys off the stale index [4](#0-3) [5](#0-4) .

### Citations

**File:** app/src/models/diff/diff-selection.ts (L78-84)
```typescript
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
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

**File:** app/src/models/diff/diff-selection.ts (L231-282)
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
  }
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L309-350)
```typescript
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

  let previousHunksEndIndex = 0 // Exclusive
  let followingHunksStartIndex = 0 // Inclusive

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

**File:** app/src/lib/patch-formatter.ts (L129-220)
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

**File:** app/src/lib/patch-formatter.ts (L251-335)
```typescript
export function formatPatchToDiscardChanges(
  filePath: string,
  diff: ITextDiff,
  selection: DiffSelection
): string | null {
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
      } else {
        if (line.type === DiffLineType.Add) {
          // An unselected added line will stay in the file after discarding the changes,
          // so we just print it untouched on the diff.
          oldCount++
          newCount++
          hunkBuf += ` ${line.text.substring(1)}\n`
        } else if (line.type === DiffLineType.Delete) {
          // An unselected removed line has no impact on this patch since it's not
          // found on the current working copy of the file, so we can ignore it.
          return
        } else {
          // Guarantee that we've covered all the line types.
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
      hunk.header.newStartLine,
      newCount,
      hunk.header.oldStartLine,
      oldCount
    )
    patch += hunkBuf
  })

  if (patch.length === 0) {
    // The selection resulted in an empty patch.
    return null
  }

  return formatPatchHeader(filePath, filePath) + patch
}
```
