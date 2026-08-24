Based on my investigation, the closest structural analog to the Solidity report — *unchecked arithmetic on attacker-influenced numeric inputs, with an implicit invariant that is assumed but never validated, leading to silently wrong results rather than a hard failure* — is the line-count/line-count arithmetic in GitHub Desktop's diff-hunk-expansion logic.

### Title
Unvalidated hunk-header arithmetic in diff expansion can produce corrupted hunk ranges used for partial-commit line selection - (File: `app/src/ui/diff/text-diff-expansion.ts`)

### Summary
`expandTextDiffHunk()` and `getTextDiffWithBottomDummyHunk()` compute new hunk boundaries and line counts by doing plain arithmetic (addition/subtraction) on numbers taken from the git-produced hunk header (`oldStartLine`, `oldLineCount`, `newStartLine`, `newLineCount`) and from the *separately* obtained current file content (`newContentLines`/`numberOfNewLines`/`numberOfOldLines`). There is no check that these two attacker/environment-influenced sources of truth are actually consistent with each other before the subtraction/addition is performed and used to build a new `DiffHunkHeader` and new `unifiedDiffStart`/`unifiedDiffEnd` ranges.

### Finding Description
The hunk header numbers (`oldStartLine`, `oldLineCount`, `newStartLine`, `newLineCount`) are parsed straight out of the raw diff text by `parseHunkHeader()` with only a regex/`parseInt` sanity check [1](#0-0) . Nothing cross-validates that these counts actually match the real length of the file content.

Later, when a user (or the UI, e.g. `expandWholeTextDiff`) expands hunk context, `expandTextDiffHunk` blindly performs subtraction/addition on these numbers against the actual current file's line array: [2](#0-1) 
and rebuilds the hunk header and the unified-diff line ranges from that arithmetic: [3](#0-2) 

Similarly, `getTextDiffWithBottomDummyHunk` computes a synthetic trailing hunk header using `numberOfOldLines - dummyOldStartLine + 1` / `numberOfNewLines - dummyNewStartLine + 1`, where `dummyOldStartLine`/`dummyNewStartLine` are derived from the parsed (git-supplied) header and `numberOfOldLines`/`numberOfNewLines` come from the actual file on disk read via `readPartialFile`/`getPartialBlobContents`: [4](#0-3) 

If the header-derived count (`dummyOldStartLine`/`dummyNewStartLine`) is larger than the true, freshly-read line count (`numberOfOldLines`/`numberOfNewLines`) — which can happen whenever the header the diff engine produced doesn't line up with what Desktop independently reads off disk (e.g. because the underlying blob/working file was concurrently changed, or a crafted file/encoding causes git's line count and Desktop's `split`-based line count in `newContentLines` to diverge) — the subtraction goes negative and the resulting `DiffHunkHeader` carries a bogus negative line count. This flows straight into `unifiedDiffStart`/`unifiedDiffEnd` for downstream hunks in `expandTextDiffHunk` [5](#0-4) , which are exactly the indices consumed by `formatPatch`/`formatPatchToDiscardChanges` (`hunk.unifiedDiffStart + lineIndex`, `file.selection.isSelected(absoluteIndex)`) to decide which lines are actually written into the commit or discard patch [6](#0-5) [7](#0-6) .

This mirrors the Solidity bug's structure: an arithmetic operation (`data[i] - mean`, here `numberOfOldLines - dummyOldStartLine`) is performed under an unvalidated assumption (that mean ≤ data[i]; here, that the parsed header counts never exceed the real file's line count) and there is no guard for the case where that assumption is violated.

### Impact Explanation
If the corrupted hunk ranges shift which absolute line index maps to which diff line, `DiffSelection.isSelected(absoluteIndex)` in `formatPatch`/`formatPatchToDiscardChanges` can silently select/deselect the wrong lines, producing a git patch that stages or discards content the user never intended — i.e. silent corruption of what the user commits/discards, without any error being raised (unlike the Solidity case, where at least the transaction reverts loudly; here the failure mode is worse because it's silent). This matches the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
I was not able to fully trace, from static code alone, a concrete scenario that reliably produces the header/file-content divergence (this would require reproducing a real desync between git's reported hunk counts and Desktop's independently-read `newContentLines`, e.g. via a crafted CRLF/BOM/line-ending file in a cloned repo, or a race between disk read and diff generation). This is a genuine gap in my verification — I can show the missing-validation pattern and the exact code paths that would propagate a bad result, but I could not confirm from the indexed code alone that the divergence is trivially triggerable by an attacker-controlled repository without further dynamic testing.

### Recommendation
Before using arithmetic like `numberOfOldLines - dummyOldStartLine + 1` / `numberOfNewLines - dummyNewStartLine + 1` in `getTextDiffWithBottomDummyHunk`, and before trusting `hunk.header.*` counts in `expandTextDiffHunk`, validate that the header-derived offsets never exceed the actual line counts read from disk; if they do, treat the diff as unparseable/refuse to expand rather than emit a `DiffHunkHeader` with negative line counts. Add an explicit invariant check (`assert(dummyOldStartLine <= numberOfOldLines)` etc.) analogous to casting to a signed type and validating sign in the Solidity fix, so a mismatch fails safely (falls back to the unexpanded diff) instead of producing a header that can propagate into `unifiedDiffStart`/`unifiedDiffEnd` and ultimately into `formatPatch`.

### Proof of Concept
Not independently verified end-to-end. The concrete reproduction would require constructing a repository/file such that the line count independently computed by Desktop from `readPartialFile`/`getPartialBlobContents` (used as `newContentLines`/`numberOfOldLines`/`numberOfNewLines`) is smaller than the counts implied by the hunk header that `git diff` emitted for that same file, then invoking "Expand whole file" in the Changes view (`expandWholeTextDiff` → `expandTextDiffHunk`/`getTextDiffWithBottomDummyHunk`) and checking whether the resulting hunk's `unifiedDiffStart`/`unifiedDiffEnd`/line count become negative or inconsistent, and whether that corrupts which lines `formatPatch` includes when partially staging. This would need to be executed with Desktop's test harness (`app/test/unit/text-diff-expansion-test.ts` already exercises adjacent code paths and would be the natural place to add such a regression test) rather than purely static analysis.

### Citations

**File:** app/src/lib/diff-parser.ts (L239-257)
```typescript
  private parseHunkHeader(line: string): DiffHunkHeader {
    const m = diffHeaderRe.exec(line)
    if (!m) {
      throw new Error(`Invalid hunk header format`)
    }

    // If endLines are missing default to 1, see diffHeaderRe docs
    const oldStartLine = this.numberFromGroup(m, 1)
    const oldLineCount = this.numberFromGroup(m, 2, 1)
    const newStartLine = this.numberFromGroup(m, 3)
    const newLineCount = this.numberFromGroup(m, 4, 1)

    return new DiffHunkHeader(
      oldStartLine,
      oldLineCount,
      newStartLine,
      newLineCount
    )
  }
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L204-243)
```typescript
  const newLineNumber = hunk.header.newStartLine
  const oldLineNumber = hunk.header.oldStartLine

  // Calculate the range of new lines to add to the diff. We could use new or
  // old line number indistinctly, so I chose the new lines.
  let [from, to] = isExpandingUp
    ? [newLineNumber - step, newLineNumber]
    : [
        newLineNumber + hunk.header.newLineCount,
        newLineNumber + hunk.header.newLineCount + step,
      ]

  // We will merge the current hunk with the adjacent only if the expansion
  // ends where the adjacent hunk begins (depending on the expansion direction).
  // In any case, never let the expanded hunk to overlap the adjacent hunk.
  let shouldMergeWithAdjacent = false

  if (adjacentHunk !== null) {
    if (isExpandingUp) {
      const upLimit =
        adjacentHunk.header.newStartLine + adjacentHunk.header.newLineCount
      from = Math.max(from, upLimit)
      shouldMergeWithAdjacent = from === upLimit
    } else {
      // Make sure we're not comparing against the dummy hunk at the bottom,
      // which is effectively taking all the undiscovered file contents and
      // would prevent us from expanding down the diff.
      if (isAdjacentDummyHunk === false) {
        const downLimit = adjacentHunk.header.newStartLine
        to = Math.min(to, downLimit)
        shouldMergeWithAdjacent = to === downLimit
      }
    }
  }

  const newLines = newContentLines.slice(
    Math.max(from - 1, 0),
    Math.min(to - 1, newContentLines.length)
  )
  const numberOfLinesToAdd = newLines.length
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L278-326)
```typescript
  const newHunkHeader = new DiffHunkHeader(
    isExpandingUp
      ? hunk.header.oldStartLine - numberOfLinesToAdd
      : hunk.header.oldStartLine,
    hunk.header.oldLineCount + numberOfLinesToAdd,
    isExpandingUp
      ? hunk.header.newStartLine - numberOfLinesToAdd
      : hunk.header.newStartLine,
    hunk.header.newLineCount + numberOfLinesToAdd
  )

  // Grab the header line of the hunk to expand
  const firstHunkLine = hunk.lines[0]

  // Create a new Hunk header line
  const newDiffHunkLine = new DiffLine(
    newHunkHeader.toDiffLineRepresentation(),
    DiffLineType.Hunk,
    null,
    firstHunkLine.oldLineNumber,
    firstHunkLine.newLineNumber,
    firstHunkLine.noTrailingNewLine
  )

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

**File:** app/src/ui/diff/text-diff-expansion.ts (L352-391)
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

  // Create the new list of hunks of the diff, and the new diff text
  const newHunks = [...previousHunks, updatedHunk, ...followingHunks]
  const newDiffText = getDiffTextFromHunks(newHunks)
```

**File:** app/src/ui/diff/text-diff-expansion.ts (L412-441)
```typescript
export function getTextDiffWithBottomDummyHunk(
  diff: ITextDiff,
  hunks: ReadonlyArray<DiffHunk>,
  numberOfOldLines: number,
  numberOfNewLines: number
): ITextDiff | null {
  const lastHunk = hunks.at(-1)

  if (lastHunk === undefined) {
    return null
  }

  // If the last hunk doesn't reach the end of the file, create a dummy hunk
  // at the end to allow expanding the diff down.
  const lastHunkNewLine =
    lastHunk.header.newStartLine + lastHunk.header.newLineCount

  if (lastHunkNewLine >= numberOfNewLines) {
    return null
  }
  const dummyOldStartLine =
    lastHunk.header.oldStartLine + lastHunk.header.oldLineCount
  const dummyNewStartLine =
    lastHunk.header.newStartLine + lastHunk.header.newLineCount
  const dummyHeader = new DiffHunkHeader(
    dummyOldStartLine,
    numberOfOldLines - dummyOldStartLine + 1,
    dummyNewStartLine,
    numberOfNewLines - dummyNewStartLine + 1
  )
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

**File:** app/src/lib/patch-formatter.ts (L266-280)
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
```
