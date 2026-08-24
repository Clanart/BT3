## Title
Stale absolute-line-index selection reused against a re-fetched diff can silently commit/discard the wrong lines - ([File: app/src/lib/git/apply.ts])

### Summary
`DiffSelection` tracks which lines a user wants to stage, discard, or exclude from a commit as a `Set<number>` of *absolute diff-line indices* (`hunk.unifiedDiffStart + lineIndex`), not as references to actual line content. `applyPatchToIndex` and `formatPatchToDiscardChanges` re-fetch a **fresh** diff and then blindly reuse a `DiffSelection` object that may have been computed against a **different, earlier** diff, trusting `selection.isSelected(absoluteIndex)` to still point at the same logical line. This mirrors the smart-contract bug: an index derived from one version/length of a data structure is applied against a different, mismatched version, silently returning the wrong element instead of failing loudly.

### Finding Description
`DiffSelection.isSelected(lineIndex)` looks up a bare integer index in `divergingLines`, with no knowledge of what content that index used to represent: [1](#0-0) 

The index space (`hunk.unifiedDiffStart + lineIndex`) is derived purely from the hunk layout of a *specific* diff snapshot: [2](#0-1) 

`app-store.ts` itself documents that this invariant is fragile: when a diff is reloaded, "the diff might have changed dramatically since last we loaded it," and the code only prunes indices that are no longer selectable — it explicitly does **not** attempt to verify that a surviving index still refers to the same line: [3](#0-2) 

The actual commit-to-index path re-fetches the diff independently of the `DiffSelection` that is applied to it. `applyPatchToIndex` calls `getWorkingDirectoryDiff` to get a **current** diff, then hands that fresh diff, together with the file's (possibly stale) `selection`, straight to `formatPatch`: [4](#0-3) 

`formatPatch` (and the discard-changes analog `formatPatchToDiscardChanges`) then walks the **new** hunks and, for every non-context line, asks the stale selection whether `hunk.unifiedDiffStart + lineIndex` was selected — with no cross-check that this index still corresponds to the same source line it did when the user made the selection: [5](#0-4) [6](#0-5) 

If the on-disk file content shifts between the moment a user makes a partial line selection and the moment the patch is generated (e.g. lines are added/removed above the selected hunk by any process touching the working tree — an auto-formatter, a build step, a smudge/clean filter defined by the repository's `.gitattributes` combined with a locally-configured filter such as Git LFS, or a background git operation Desktop itself triggers), the hunk boundaries and `unifiedDiffStart` offsets shift. The previously recorded `divergingLines` indices are then silently reinterpreted against unrelated lines in the new hunk layout — exactly analogous to using `winningSegment` (computed from an aggregate count) to index the *last* (differently-sized) `rewardIdOfSegment` mapping in the reported Solidity bug: an index valid for one version of a structure is blindly applied to a structurally different version of the same-shaped structure.

### Impact Explanation
This can cause GitHub Desktop to commit or discard content the user did not intend:
- A line the user explicitly deselected (e.g. a debug statement, credential, or unwanted change) can be silently included in the commit if it now lands on an index previously marked "selected."
- Conversely, an intended change can be silently dropped from the commit, or the wrong region of the file can be reverted via "discard changes," corrupting the user's history without any error or warning.

This falls squarely into "silent corruption of what the user commits or pushes" since no exception is thrown — `formatPatch`/`formatPatchToDiscardChanges` always produce a syntactically valid patch, and `git apply --cached` will happily apply it.

### Likelihood Explanation
The window between diff generation and commit-time patch generation is real (`applyPatchToIndex` re-diffs from disk rather than reusing the diff the UI displayed), and Desktop's own comments in `app-store.ts` acknowledge the diff "might have changed dramatically" between loads, with only partial (selectability-based) mitigation, not content-based remapping. However, reliably and remotely forcing a *precise* shift in file content at exactly the right moment (without any local process, editor, or filter already running) is non-trivial to characterize as purely repository-controlled; a fully deterministic, network-only reproduction (e.g., purely via a crafted fetched ref with no assisting local tool) could not be confirmed from the available code. This uncertainty affects confidence in the precise attacker-controlled trigger, though the underlying broken invariant (blind reuse of stale absolute-line indices across different diff snapshots) is clearly present and unguarded in the code shown.

### Recommendation
Do not persist bare integer line indices independent of diff content. Either:
- Re-derive/re-validate the full `DiffSelection` against the exact diff object that will be used to build the patch (reject/re-map instead of silently reusing indices when the diff object differs), or
- Key selections by stable line content/hash (e.g. combination of `originalLineNumber` + line text) rather than positional index, or
- Have `applyPatchToIndex`/`formatPatch` refuse to build a patch (and surface an error/refresh prompt to the user) if the diff passed in does not match the diff the selection was computed against, instead of assuming index compatibility.

### Proof of Concept
Cannot be fully demonstrated without runtime access to reproduce the precise timing race (modifying the working file between `_selectWorkingDirectoryFiles`'s diff load and `applyPatchToIndex`'s independent re-diff). Conceptually:
1. Open a file with a multi-hunk diff in Desktop; partially select a subset of lines to include for commit (`DiffSelection.withLineSelection` records absolute indices such as `12`, `13`).
2. Before the commit is finalized, cause the file's line layout to shift (e.g. a formatter/save hook adds 5 lines above the selected hunk) so that indices `12`/`13` now correspond to entirely different lines.
3. Trigger the commit: `applyPatchToIndex` re-fetches the diff (`getWorkingDirectoryDiff`, `app/src/lib/git/apply.ts:60`) and calls `formatPatch(file, diff)` using the old `file.selection`.
4. `isSelected(absoluteIndex)` in `patch-formatter.ts:157` matches against the new hunk layout, so the resulting patch stages the new lines at those index positions instead of the lines the user actually selected — an incorrect commit is created silently.

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

**File:** app/src/ui/diff/diff-explorer.ts (L34-42)
```typescript
export function diffHunkForIndex(
  hunks: ReadonlyArray<DiffHunk>,
  index: number
): DiffHunk | null {
  const hunk = hunks.find(h => {
    return index >= h.unifiedDiffStart && index <= h.unifiedDiffEnd
  })
  return hunk || null
}
```

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
