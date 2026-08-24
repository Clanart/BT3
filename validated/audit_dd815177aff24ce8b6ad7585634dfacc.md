## Title
Diff hunk expansion silently desynchronizes line-selection indices, causing unintended lines to be committed - (File: `app/src/ui/diff/text-diff-expansion.ts`)

### Summary
The staking bug is about a rounding/index calculation error that silently drops value the user should have kept. The closest verifiable analog in GitHub Desktop is in the diff-hunk-expansion logic: when a hunk is expanded "up" (revealing more context above a change), new context lines are spliced into the **front** of the hunk's line array while the hunk's `unifiedDiffStart` index is left unchanged. This shifts the absolute index of every pre-existing line in that hunk, but the file's `DiffSelection` (which records selected/deselected lines by absolute index) is never re-indexed to match. `formatPatch()` later trusts these stale indices to decide which lines go into the commit patch, so a partial commit can silently include/exclude the wrong lines after a hunk is expanded.

### Finding Description
`DiffSelection` tracks per-file line selection as a `Set<number>` of "diverging" absolute line indices (`app/src/models/diff/diff-selection.ts:82-136`), and `isSelected(lineIndex)` is a pure index lookup with no knowledge of diff content.

`expandTextDiffHunk()` in `app/src/ui/diff/text-diff-expansion.ts` builds the new hunk's line array like this when expanding upward: [1](#0-0) 

The updated hunk keeps the same `hunk.unifiedDiffStart` (line 322) even though `numberOfLinesToAdd` new context lines are now inserted **before** the hunk's original lines. Because the array positions of the pre-existing lines shift by `numberOfLinesToAdd` while `unifiedDiffStart` doesn't move, every pre-existing line's absolute index (`unifiedDiffStart + arrayIndex`, as computed in `formatPatch`) is different after expansion than it was before: [2](#0-1) 

The application does re-derive the *selectable* line set after a diff reload (`app-store.ts` `selectableLines` computation), but it only filters out indices that are no longer selectable — it does not remap or invalidate indices that happen to still be "selectable" after the shift: [3](#0-2) 

So if, before expansion, line index `N` was selected for commit, and after an "up" expansion inserts `k` new context lines into the same hunk, the line that now occupies index `N` is a *different* line (either a newly-added context line, which is not selectable and gets filtered, or — critically — a line whose real (pre-expansion) content has moved to `N+k`, while some other content, or a merged-in line from an adjacent hunk (`mergeDiffHunks`, lines 30-76), now sits at `N`). `DiffSelection.isSelected(N)` still reports the old boolean for index `N`, which is now checked against the wrong line's content.

### Impact Explanation
This falls under "silent corruption of what the user commits". A user reviewing and partially staging a diff (checking/unchecking individual lines), who then expands a hunk to see more context (a very common, encouraged workflow) and commits without re-verifying every previously-selected checkbox, may have the commit contain lines they explicitly excluded, or exclude lines they explicitly included — with no error, warning, or visual indication that the mapping shifted. Because `formatPatch` drives the actual patch given to `git apply --cached`, this directly changes what content is written to the user's repository history, matching the "silent corruption of what the user commits or pushes" impact category. No exploitation beyond normal UI interaction (expand hunk + commit) is required, and no elevated privileges or local access beyond normal app use.

### Likelihood Explanation
Hunk expansion followed by committing via partial line selection is an everyday Desktop feature; it does not require attacker control of repository content to trigger the bug class itself, though the practical consequence (unexpectedly committing "attacker-authored" lines from a fetched/malicious branch that a user thought they had excluded) is amplified whenever the diff being reviewed originates from a hostile source (e.g. reviewing/staging a partially-trusted merge or cherry-pick). The core defect — indices not being remapped on expansion — is deterministic and reproducible any time a hunk with an existing partial selection is expanded upward (or merged with an adjacent hunk during expansion, per `mergeDiffHunks`).

### Recommendation
When `expandTextDiffHunk` (and `mergeDiffHunks`) inserts lines before existing hunk content or merges hunks, the corresponding `DiffSelection` for the file must be re-indexed in lockstep — i.e., translate every diverging line index by the same offset applied to the lines it refers to, rather than leaving the `divergingLines` set keyed to now-stale absolute positions. Alternatively, `DiffSelection` should track selection by stable line identity (e.g., old/new line number pairs) instead of by ephemeral "absolute index in current hunks array," so that expansion/merging operations cannot desynchronize what's selected from what's rendered/committed. Any change to hunk layout should be accompanied by a matching transformation of `WorkingDirectoryFileChange.selection`, invoked at the same call site that produces the new `ITextDiff`.

### Proof of Concept
1. Open a modified file in Desktop's Changes view with at least two non-adjacent hunks, where hunk 2 has some added/removed lines.
2. In hunk 2, deselect (uncheck) one specific added line — e.g., the 3rd line of the hunk — leaving everything else selected (a Partial selection, storing that one absolute index in `divergingLines`).
3. Click "Expand up" on hunk 2's expansion handle enough times to pull in context lines from above (each click calls `expandTextDiffHunk(diff, hunk, 'up', ...)`, inserting new context lines ahead of the hunk's existing lines while `unifiedDiffStart` stays fixed).
4. Without touching the checkbox state, commit the partial selection via `formatPatch`, which computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` per line and checks `file.selection.isSelected(absoluteIndex)`.
5. Inspect the resulting commit: because the pre-existing lines shifted to higher indices while the stale selection index still points at index `N`, either the line the user intended to exclude is now included, or a different, previously-included line silently gets excluded — with no warning shown to the user before or after the commit.

### Citations

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

**File:** app/src/lib/patch-formatter.ts (L143-145)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

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
