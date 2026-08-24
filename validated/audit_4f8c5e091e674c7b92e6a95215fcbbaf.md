### Title
Stale index-based diff-selection reuse can silently commit unintended lines after a working-directory diff refresh - ([File: app/src/lib/stores/app-store.ts])

### Summary
`updateChangesWorkingDirectoryDiff` reconciles a user's partial line-selection state against a freshly re-fetched diff purely by numeric line index, not by line content. If the tracked file's content changes between the moment the user made a partial selection and the moment the diff is reloaded, previously-diverging indices are blindly re-applied to whatever line now occupies that index, and the resulting patch is built from the wrong hunks/lines when the user commits.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) tracks partial selections as a `Set<number>` of "diverging" absolute line indices (`divergingLines`), with no association to the actual line content — only `isSelectable`/`isSelected` keyed by index: [1](#0-0) 

When the working-directory diff for the currently selected file is reloaded (e.g. after the file changes on disk), `updateChangesWorkingDirectoryDiff` in `app-store.ts` recomputes only the *set of selectable indices* from the new diff and calls `withSelectableLines`, which intersects the old `divergingLines` with the new `selectableLines` by index equality: [2](#0-1) [3](#0-2) 

The comment in `app-store.ts` explicitly acknowledges the limitation: the code only removes indices that no longer exist or turned into context lines, but does not verify that a still-selectable index at the same position still refers to the *same* content: [4](#0-3) 

Later, when the user commits, `formatPatch`/`applyPatchToIndex` build the `git apply --cached` patch strictly from `file.selection.isSelected(absoluteIndex)` against the *current* diff's hunks/lines — again purely index-driven, with no re-validation that the selected index corresponds to the line the user actually intended: [5](#0-4) [6](#0-5) 

This mirrors the report's broken invariant: a stale/aggregate reference (Reth's whole-balance price) is substituted for the correct, narrowly-scoped value (the deposit amount), producing an outcome that looks superficially valid but is silently wrong. Here the "wrong value" is the reused absolute-line-index selection applied to a diff whose hunk boundaries/content have shifted, so a selection the user made against version A of the file gets applied against version B without content verification.

### Impact Explanation
If exploited, this causes silent corruption of what the user commits: lines the user never intended to include (or exclude) end up staged and committed via `git apply --cached`, or a partial commit silently includes/omits unrelated changes. This falls squarely in the requested impact class of "silent corruption of what the user commits or pushes," without requiring elevated privileges — an attacker who controls repository tooling that mutates a tracked file between diff loads (e.g. a build step, formatter, or git hook triggered as part of normal repository workflows the user already runs, such as `postinstall`/`pre-commit`/`post-checkout` scripts shipped in a cloned/fetched malicious repository) can shift hunk boundaries so that previously diverging indices now point at attacker-chosen lines.

### Likelihood Explanation
Likelihood is moderate to low without further verification. It requires: (1) the user to have made a partial (line/hunk-level) selection on a file, (2) the file content changing on disk in a way that shifts line offsets before the user commits, and (3) GitHub Desktop reloading the diff for that file in between (this refresh path exists via `updateChangesWorkingDirectoryDiff`, but I was not able to fully confirm within the remaining iterations whether it is also triggered automatically by a filesystem watcher versus only by explicit UI actions such as re-selecting the file). This uncertainty should be resolved by tracing the callers of `_selectWorkingDirectoryFiles` / any filesystem-watch-triggered status refresh path before treating this as fully confirmed exploitable.

### Recommendation
Do not reconcile partial selection purely by absolute line index across diff reloads. Either (a) invalidate/clear the partial selection entirely whenever the underlying diff for a file changes in a way that alters hunk boundaries (safer default: fall back to "select all" or explicitly prompt the user to re-review), or (b) track selection by stable line-content fingerprints (e.g. hash of line text plus hunk anchor) so that `withSelectableLines` only preserves divergence for lines that are content-identical to what the user originally selected, and re-diffs the rest as unselected pending user confirmation.

### Proof of Concept
1. Modify a tracked file and open it in GitHub Desktop's Changes view; a text diff with multiple hunks is generated.
2. Select only specific lines within a hunk for partial commit (setting divergence in `DiffSelection.divergingLines` at those absolute indices) — see `withRangeSelection`: [7](#0-6) 
3. Before committing, have an external process (e.g. a formatter, linter with `--fix`, or a git hook run automatically by tooling shipped in the repository) rewrite the file such that lines are inserted/removed above the previously selected hunk, shifting subsequent line offsets while keeping the total number of includeable lines roughly constant.
4. Trigger a diff reload for the file (e.g. by reselecting it or via the app's normal refresh flow) — `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and calls `withSelectableLines`, which keeps any previously diverging index that is still "selectable" in the new diff, regardless of whether it maps to the same source line: [8](#0-7) 
5. Commit the partial selection. `formatPatch` builds the patch using `isSelected(absoluteIndex)` against the new hunks: [5](#0-4)  — the resulting commit silently contains different line changes than the ones the user visually reviewed and selected.

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

**File:** app/src/models/diff/diff-selection.ts (L232-282)
```typescript
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

**File:** app/src/lib/patch-formatter.ts (L143-161)
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
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`
```

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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
