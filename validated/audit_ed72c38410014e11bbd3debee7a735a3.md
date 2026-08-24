### Title
Line-selection indices desynchronize between the expanded diff view and the diff re-parsed at commit time, causing partial commits/discards to include the wrong lines - ([File: app/src/ui/diff/text-diff-expansion.ts])

### Summary
`DiffSelection` tracks which lines a user wants committed by storing **absolute line indices** (`divergingLines`) that are only meaningful relative to a specific `hunk.unifiedDiffStart`/`unifiedDiffEnd` layout [1](#0-0) . When a user expands a hunk in the UI ("Expand Up/Down/Whole File"), `expandTextDiffHunk`/`expandWholeTextDiff` rebuild the hunk list, inserting new context lines and shifting the `unifiedDiffStart`/`unifiedDiffEnd` of every subsequent hunk by `numberOfNewDiffLines` [2](#0-1) [3](#0-2) . This expanded diff only lives in the UI component's local state (`this.setState({ diff: updatedDiff })` in `expandHunk`/`onExpandWholeFile`) [4](#0-3) [5](#0-4) .

Selections the user makes while viewing this expanded diff are recorded through `onLineNumberCheckedChanged`/`onClickHunk`, which call `withLineSelection`/`withRangeSelection` using the **expanded** hunks' absolute indices [6](#0-5) [7](#0-6) . However, when the commit is actually created, the patch is built from a **freshly re-fetched, non-expanded** diff: `applyPatchToIndex` calls `getWorkingDirectoryDiff` again and feeds that raw diff into `formatPatch`, which re-derives `absoluteIndex = hunk.unifiedDiffStart + lineIndex` from this fresh diff and asks `file.selection.isSelected(absoluteIndex)` [8](#0-7) [9](#0-8) .

Because the fresh diff's hunk boundaries do **not** contain the extra context lines that were inserted during expansion, its `unifiedDiffStart` numbering for hunks after the expanded one differs from the numbering that was in effect when the user selected/deselected lines. The `divergingLines` set is therefore applied against the wrong coordinate system, and `formatPatch`/`applyPatchToIndex` will resolve `isSelected()` for indices that now point to different lines than the ones the user actually clicked.

### Finding Description
The broken invariant is: *"the absolute line index used to record a selection must be the same absolute index used to look up that selection when building the commit patch."* This invariant silently breaks whenever a hunk is expanded in the UI, because:
- `expandTextDiffHunk` recomputes `unifiedDiffStart`/`unifiedDiffEnd` for all hunks following the expanded one [10](#0-9) , and `mergeDiffHunks` further collapses two hunks into one, discarding one hunk-header line and shifting everything below [11](#0-10) .
- The `DiffSelection.divergingLines` set is never remapped to the new indices — there is no code path that translates old absolute indices to new ones after an expansion; the only remapping that exists (`withSelectableLines`) runs during a full working-directory refresh, not during hunk expansion [12](#0-11) .
- At commit time, `createCommit`/`applyPatchToIndex` re-fetch the diff from git (unexpanded) and build the patch from that fresh diff's hunk boundaries, combined with the (stale, expansion-tainted) selection object [8](#0-7) [13](#0-12) .

Existing guards do not stop this: `formatPatch` only guards against an *empty* result (`if (!patch.length) throw`) [14](#0-13) , not against a *shifted* selection producing a non-empty but wrong patch. There is no validation that the selection's line indices were computed against the same hunk layout used to build the patch.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A user reviewing a diff from an attacker-influenced clone/fetch (e.g., a file whose hunks are structured so expansion triggers a merge — controlled entirely by the content of the tracked file, which the attacker fully controls since it's just source text) could expand a hunk, deliberately deselect a malicious/hidden line they noticed, and then commit — believing that line is excluded — while the actual patch generated from the re-fetched diff includes a different line (because indices shifted), silently committing/pushing attacker-influenced content the user explicitly tried to exclude, or vice versa (excluding content the user meant to include).

### Likelihood Explanation
Triggering requires ordinary partial-commit workflow (expand a hunk, then select/deselect specific lines, then commit) on a diff whose shape makes hunk expansion merge with an adjacent hunk or add enough context lines to shift indices — a common occurrence for files with many small hunks close together, i.e., something an attacker can freely engineer into a tracked file without needing any elevated access, purely by shaping the file content the victim later diffs. No admin rights, local access, or pre-existing malware are needed.

### Recommendation
- When a hunk is expanded/merged in `expandTextDiffHunk`/`mergeDiffHunks`/`expandWholeTextDiff`, remap the associated `DiffSelection`'s `divergingLines` to the new absolute indices (or store selections keyed by a stable identity such as original line numbers/content hash instead of shifting absolute positions).
- Alternatively, ensure the diff used to build the commit patch (`applyPatchToIndex`/`formatPatch`) is always the exact same hunk/index layout that was displayed and used to record the selection, rather than a diff independently re-fetched from git at commit time.
- Add a consistency check before committing: verify that `file.selection`'s recorded indices are still valid against the hunk layout about to be patched, and fail loudly (rather than silently building a possibly-wrong patch) if they diverge.

### Proof of Concept
1. Prepare a tracked file whose diff contains multiple small hunks close together so that expanding the first hunk merges it with the next (per `getHunkHeaderExpansionType`'s `Short`/merge logic) [15](#0-14) .
2. In Desktop, open the Changes view for this file and click "Expand" on a hunk that will merge with an adjacent hunk, via `onExpandHunk` → `expandHunk` → `expandTextDiffHunk` [16](#0-15) .
3. Deselect one specific added/deleted line in the now-expanded/merged hunk using the line checkbox (`onLineNumberCheckedChanged`) [6](#0-5) ; this records the absolute index against the merged hunk's `unifiedDiffStart`.
4. Commit the partial change. `_commitIncludedChanges` → `createCommit` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` (unexpanded) and calls `formatPatch` against this fresh diff [8](#0-7) , using the stale selection's indices which were computed relative to the merged/expanded hunk layout, not the fresh one.
5. Inspect the resulting commit: the line that ends up included/excluded differs from the line the user actually toggled in the UI, demonstrating silent divergence between displayed intent and committed content.

Note: I was not able to execute this scenario end-to-end (no runtime/browser access in this environment); the finding is based on static analysis of the index-shifting logic in `text-diff-expansion.ts` versus the independent diff re-fetch in `apply.ts`/`patch-formatter.ts`. A Devin session with repo/terminal access would be needed to build the exact file/diff shape and confirm the concrete mismatch empirically.

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

**File:** app/src/ui/diff/text-diff-expansion.ts (L30-76)
```typescript
function mergeDiffHunks(hunk1: DiffHunk, hunk2: DiffHunk): DiffHunk {
  // Remove the first line in both hunks, because those are hunk header lines
  // that will be replaced by a new one for the resulting hunk.
  const allHunk1LinesButFirst = hunk1.lines.slice(1)
  const allHunk2LinesButFirst = hunk2.lines.slice(1)

  const newHunkHeader = new DiffHunkHeader(
    hunk1.header.oldStartLine,
    hunk1.header.oldLineCount + hunk2.header.oldLineCount,
    hunk1.header.newStartLine,
    hunk1.header.newLineCount + hunk2.header.newLineCount
  )

  // Create a new hunk header line for the resulting hunk
  const newFirstHunkLine = new DiffLine(
    newHunkHeader.toDiffLineRepresentation(),
    DiffLineType.Hunk,
    null,
    null,
    null,
    false
  )

  const newHunkLines = [
    newFirstHunkLine,
    ...allHunk1LinesButFirst,
    ...allHunk2LinesButFirst,
  ]

  return new DiffHunk(
    newHunkHeader,
    newHunkLines,
    hunk1.unifiedDiffStart,
    hunk1.unifiedDiffStart + newHunkLines.length - 1,
    // The expansion type of the resulting hunk will match the expansion type
    // of the first hunk:
    // - If the first hunk can be expanded up, it means it's the very first
    //   hunk, so the resulting hunk will be the first too.
    // - If the first hunk can be expanded but short, that doesn't change after
    //   merging it with the second one.
    // - If it can be expanded up and down (meaning it's a long gap), that
    //   doesn't change after merging it with the second one.
    // - It can never be expanded down exclusively, because only the last dummy
    //   hunk can do that, and that will never be the first hunk in a merge.
    hunk1.expansionType
  )
}
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L88-118)
```typescript
export function getHunkHeaderExpansionType(
  hunkIndex: number,
  hunkHeader: DiffHunkHeader,
  previousHunk: DiffHunk | null
): DiffHunkExpansionType {
  const distanceToPrevious =
    previousHunk === null
      ? Infinity
      : hunkHeader.oldStartLine -
        previousHunk.header.oldStartLine -
        previousHunk.header.oldLineCount

  // In order to simplify the whole logic around expansion, only the hunk at the
  // top can be expanded up exclusively, and only the hunk at the bottom (the
  // dummy one, see getTextDiffWithBottomDummyHunk) can be expanded down
  // exclusively.
  // The rest of the hunks can be expanded both ways, except those which are too
  // short and therefore the direction of expansion doesn't matter.
  if (hunkIndex === 0) {
    // The top hunk can only be expanded if there is content above it
    if (hunkHeader.oldStartLine > 1 && hunkHeader.newStartLine > 1) {
      return DiffHunkExpansionType.Up
    } else {
      return DiffHunkExpansionType.None
    }
  } else if (distanceToPrevious <= DefaultDiffExpansionStep) {
    return DiffHunkExpansionType.Short
  } else {
    return DiffHunkExpansionType.Both
  }
}
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L309-325)
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1366-1384)
```typescript
  private onExpandHunk = (
    hunkIndex: number,
    expansionType: DiffHunkExpansionType
  ) => {
    const { diff } = this.state

    if (hunkIndex === -1 || hunkIndex >= diff.hunks.length) {
      return
    }

    this.setState({ lastExpandedHunk: { hunkIndex, expansionType } })

    const kind = expansionType === DiffHunkExpansionType.Down ? 'down' : 'up'

    this.expandHunk(diff.hunks[hunkIndex], kind)

    this.ariaLiveChangeSignal = !this.ariaLiveChangeSignal
    this.setState({ ariaLiveMessage: 'Expanded' })
  }
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1386-1402)
```typescript
  private onClickHunk = (hunkStartLine: number, select: boolean) => {
    if (this.props.onIncludeChanged === undefined) {
      return
    }

    const { diff } = this.state
    const selection = this.getSelection()

    if (selection !== undefined) {
      const range = findInteractiveOriginalDiffRange(diff.hunks, hunkStartLine)
      if (range !== null) {
        const { from, to } = range
        const sel = selection.withRangeSelection(from, to - from + 1, select)
        this.props.onIncludeChanged(sel)
      }
    }
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1721-1741)
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
```

**File:** app/src/lib/git/apply.ts (L52-81)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/patch-formatter.ts (L129-232)
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

  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }

  patch = formatPatchHeaderForFile(file) + patch

  return patch
}
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
