### Title
Partial-commit line selection can silently include/exclude the wrong lines because staging re-fetches an independent diff whose line numbering may not match the diff the user selected against - ([File: app/src/lib/git/apply.ts])

### Summary
This is the closest Desktop analog to the Hyperdrive bug class: two values that are computed in different "coordinate systems" (there, base‑units vs. share‑units; here, line‑index numbering from two independently‑produced diffs) are combined as if they were interchangeable, silently producing a wrong result instead of an error.

### Finding Description
`DiffSelection` records which lines a user wants to stage as a flat set of integer indices (`absoluteIndex = hunk.unifiedDiffStart + lineIndex`), with no reference to *which* diff object those indices came from. [1](#0-0) 

The renderer builds these indices from whatever `ITextDiff`/`ILargeTextDiff` object it currently has in memory. That object's hunk boundaries are not fixed: `expandTextDiffHunk`/`mergeDiffHunks` in `app/src/ui/diff/text-diff-expansion.ts` rewrite `unifiedDiffStart`/`unifiedDiffEnd` and merge hunks when the user expands context, producing a different line-numbering scheme than a freshly-parsed diff for the same file would have. [2](#0-1) 

When the user actually commits, `stageFiles` → `applyPatchToIndex` does **not** reuse the diff the selection was computed against. It calls `getWorkingDirectoryDiff(repository, file)` again, independently, right before formatting the patch: [3](#0-2) 

`formatPatch(file, diff)` then walks this brand-new diff's hunks and tests `file.selection.isSelected(absoluteIndex)` using indices computed from *this* diff's `unifiedDiffStart` values: [4](#0-3) 

There is exactly one reconciliation path in the codebase for this mismatch — `updateChangesWorkingDirectoryDiff` in `app-store.ts`, which recomputes `selectableLines` when the Changes-view diff is reloaded and calls `withSelectableLines` to drop invalidated selections: [5](#0-4) 

That reconciliation only runs on the "viewing the working directory diff" path. The actual commit path (`createCommit` → `stageFiles` → `applyPatchToIndex`) performs no equivalent check — it never compares the freshly fetched diff's structure/text against the diff the `DiffSelection` was derived from, and never re-validates that the selected indices still point at the same logical lines. [6](#0-5) [7](#0-6) 

Just like the Hyperdrive report subtracted a base-amount from a share-amount and assumed the result was zero, `formatPatch` subtracts/aligns a "new-diff line index" against an "old/selection-time line index" and assumes they refer to the same line, when the two integers can come from structurally different hunk layouts.

### Impact Explanation
If the numbering diverges (e.g., because the working file changed between the moment the diff was rendered/selected and the moment the user commits — via an external editor, background formatter, file-watcher/build tool, or any other process that legitimately touches tracked files in the repo the user opened), the integer indices no longer point to the same logical lines in the freshly-fetched diff. `formatPatch`/`formatPatchToDiscardChanges` will then silently stage/discard the wrong hunks or lines — content the user did not intend to commit can be included, and content the user did intend to commit can be dropped, with no error surfaced (`formatPatch` only throws if the resulting patch is completely empty). This is a silent corruption of what the user commits, which is one of the explicitly valid impact categories.

### Likelihood Explanation
This requires no privilege escalation and is triggered purely through Desktop's normal partial-staging/commit workflow: select some lines/hunks (optionally after expanding hunk context, which is a supported, ordinary user action), let the working copy change, then commit. Because `applyPatchToIndex` always re-derives the diff from disk instead of reusing the one behind the user's selection, and because the only staleness check that exists (`updateChangesWorkingDirectoryDiff`) is confined to the live-viewing path and not invoked from the commit path, nothing currently prevents the divergence from reaching `git apply --cached`.

### Recommendation
Before staging a partially-selected file, `applyPatchToIndex` (or `stageFiles`) should validate that the diff it is about to build a patch from is structurally/textually equivalent to the diff the file's `DiffSelection` was computed against (e.g., compare diff text/hash, or recompute `selectableLines` the same way `updateChangesWorkingDirectoryDiff` does and reject/re-prompt on mismatch) instead of blindly trusting that indices from two independently-fetched diffs are interchangeable.

### Proof of Concept
Conceptual reproduction based on the code paths above (not independently executed):
1. Modify a tracked file so it has several hunks; open it in Desktop's Changes view, which fetches a diff and assigns it to `file.selection`'s coordinate space.
2. Expand a hunk's context (`expandTextDiffHunk`), which merges/renumbers hunks and shifts `unifiedDiffStart` for hunks after the expansion point.
3. Select only specific lines within/adjacent to the expanded region.
4. Before clicking "Commit," let the file change again on disk (e.g., an editor autosave, linter, or file watcher rewrites some lines) so that a freshly-computed `git diff` has a different hunk layout/line count than the expanded diff used to build the selection.
5. Click "Commit." `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff`, and `formatPatch` evaluates `file.selection.isSelected(absoluteIndex)` against this new hunk layout — because the indices no longer line up with the same logical lines, the resulting patch stages a different set of lines than what the user visually selected, with no warning shown.

### Citations

**File:** app/src/lib/patch-formatter.ts (L132-157)
```typescript
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

**File:** app/src/ui/diff/text-diff-expansion.ts (L29-76)
```typescript
/** Merges two consecutive hunks into one. */
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

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
