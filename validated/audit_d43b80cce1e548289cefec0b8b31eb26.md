## Analysis

This confirms the analog: `DiffSelection` (`app/src/models/diff/diff-selection.ts`) is a purely positional data structure — it tracks a `Set<number>` of "diverging" line indices with no binding to the actual line content, hunk identity, or diff version they came from. [1](#0-0) [2](#0-1)  This is exactly the "index instead of identity" pattern from the `RewardForwarder` bug: the index (`absoluteIndex`/`lineIndex`) is meaningful only relative to the specific array (diff/hunks) it was produced against.

The index is created by the UI against one diff snapshot, [3](#0-2)  then later consumed by `applyPatchToIndex` against a **freshly re-fetched** diff at commit time: [4](#0-3) [5](#0-4)  `formatPatch` then re-applies `file.selection.isSelected(absoluteIndex)` against this new diff's hunks without any validation that the hunk/line layout is unchanged. [6](#0-5) 

The code even documents awareness of the general class of hazard for the *render* path (`updateChangesWorkingDirectoryDiff`), but only patches up the "selectable" mask, not the case of a diff refetched later inside `applyPatchToIndex`/`stageFiles`: [7](#0-6)  That guard runs in the *Changes* panel UI, not in the git staging path (`stageFiles` → `applyPatchToIndex`), which independently calls `getWorkingDirectoryDiff` again right before applying the patch. [8](#0-7) [9](#0-8) 

### Title
Partial-commit line selection is applied by positional index against a re-fetched diff, allowing wrong hunks/lines to be staged - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
`WorkingDirectoryFileChange.selection` (a `DiffSelection`) stores which lines the user chose to include in a partial commit purely as a `Set<number>` of positional indices (`hunk.unifiedDiffStart + lineIndex`) with no reference to the actual diff content that produced those indices. [10](#0-9)  When the user commits, `applyPatchToIndex` re-fetches the working-directory diff from disk via `getWorkingDirectoryDiff` and reuses those stale positional indices against the new diff's `hunks`/`lines` arrays to build the patch (`formatPatch`). [11](#0-10) [6](#0-5)  If the working tree content changes between the moment the user reviews/selects lines in the Changes view and the moment `stageFiles`/`applyPatchToIndex` run (e.g. a hunk gains/loses lines, hunks shift, or a hunk splits/merges), the index numbers no longer point at the same logical lines. This is structurally identical to the `RewardForwarder` flaw: an index computed against one array (`rewardTokens`/diff-at-selection-time) is blindly reused to index into a different array (`RewardController`'s tokens/diff-at-commit-time).

### Finding Description
`formatPatch` walks the diff passed to it and, for every additions/deletions line, computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and asks `file.selection.isSelected(absoluteIndex)` whether that positional slot is "on". [6](#0-5)  The `diff` fed into `formatPatch` is not the same diff object the user looked at when clicking checkboxes in the UI — `applyPatchToIndex` independently calls `getWorkingDirectoryDiff(repository, file)` right before formatting the patch. [11](#0-10)  `stageFiles` invokes `applyPatchToIndex` for every file with a partial selection as the final step of staging, after the index has already been reset for the other files. [8](#0-7) 

`DiffSelection` has no concept of a hunk/line's identity, only its integer offset in whatever `hunks` array it's applied to; `isSelected`, `isSelectable`, and `withSelectableLines` all operate purely on `Set<number>` membership. [2](#0-1) [12](#0-11)  The one place that re-validates selection against a fresh diff (`updateChangesWorkingDirectoryDiff` in `app-store.ts`) only recomputes `selectableLines` to drop indices that are no longer includeable — it explicitly does **not** attempt to verify the selected line content is still the same logical change, as the code comment states. [7](#0-6)  Crucially, this revalidation only runs on the render/selection path and is never invoked from `stageFiles`/`applyPatchToIndex`, which performs its own independent `getWorkingDirectoryDiff` fetch with none of this reconciliation.

### Impact Explanation
If the working-directory diff's hunk structure changes between when the partial selection was made and when `applyPatchToIndex` re-fetches the diff for staging, the numeric indices in the stale `DiffSelection` are matched against unrelated lines in the new diff. `formatPatch` will silently mark the wrong lines as "included" (or omit lines the user intended to include), producing a staged/committed diff that differs from what the user reviewed and approved. This is a silent corruption of what the user commits/pushes: the committed patch content can diverge from the on-screen selection without any error or warning, potentially staging attacker-influenced content that was not part of the user's actual review (e.g., a concurrently-modified file via a build tool, git hook, or editor auto-format triggered by a crafted repository) into the user's commit.

### Likelihood Explanation
Desktop already keeps the working directory diff live and reactively refreshed (`updateChangesWorkingDirectoryDiff` runs on file-system watcher events), meaning the diff genuinely can and does change while a partial selection is outstanding — the surrounding comment in `app-store.ts` acknowledges "the diff might have changed dramatically since last we loaded it." [13](#0-12)  Any process that touches the file between selection and clicking "Commit" (a build script, format-on-save, a git hook writing generated files, or a malicious repository shipping a script the user runs) can shift hunk boundaries. Because `applyPatchToIndex` performs its own second, independent diff fetch rather than reusing the diff the selection was validated against, this window is real and not merely theoretical, though it does require some external mutation of the working tree in between — making it a plausible but not trivially attacker-triggerable path without additional local activity (e.g., an editor plugin, watch/build task, or hook the user has configured).

### Recommendation
Stop keying partial-commit selections by pure positional index into whatever diff happens to be current. Instead:
- Pass the exact diff object the selection was computed against into `applyPatchToIndex`/`formatPatch` (or re-derive/refresh the selection against the newly-fetched diff, using the existing `withSelectableLines` reconciliation, immediately before staging) so the same diff instance is used for both selection and patch construction.
- Alternatively, bind selection state to a content-stable identity (e.g., `originalLineNumber`/line hash) rather than `hunk.unifiedDiffStart + lineIndex`, so a shifted hunk cannot silently remap the selection to different content.
- Have `stageFiles`/`applyPatchToIndex` detect when the on-disk diff has changed materially since the last known selection and abort/refresh rather than silently applying a mismatched index.

### Proof of Concept
1. Modify a tracked file so it has a multi-line diff hunk against `HEAD`.
2. In the Changes view, partially select specific added lines (e.g., select line at absolute index 5 within hunk 1), producing a `DiffSelection` with `divergingLines = {5}`. [14](#0-13) 
3. Before clicking "Commit", have an external process (build tool, format-on-save, git hook) insert/remove a line earlier in the file, shifting subsequent hunk offsets (`unifiedDiffStart`) without the user reselecting anything.
4. Click "Commit". `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff`, and `formatPatch` recomputes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` against the new hunk layout, then checks `file.selection.isSelected(absoluteIndex)` — now index `5` corresponds to a different line than what the user selected. [6](#0-5) [11](#0-10) 
5. The resulting commit includes a different line than the one the user visually selected and approved, with no warning shown to the user.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L41-52)
```typescript
/**
 * An immutable, efficient, storage object for tracking selections of indexable
 * lines. While general purpose by design this is currently used exclusively for
 * tracking selected lines in modified files in the working directory.
 *
 * This class starts out with an initial (or default) selection state, ie
 * either all lines are selected by default or no lines are selected by default.
 *
 * The selection can then be transformed by marking a line or a range of lines
 * as selected or not selected. Internally the class maintains a list of lines
 * whose selection state has diverged from the default selection state.
 */
```

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

**File:** app/src/models/diff/diff-selection.ts (L309-330)
```typescript
  /**
   * Returns a copy of this selection instance with a specified set of
   * selectable lines. By default a DiffSelection instance allows selecting
   * all lines (in fact, it has no notion of how many lines exists or what
   * it is that is being selected).
   *
   * If the selection instance lacks a set of selectable lines it can not
   * supply an accurate value from getSelectionType when the selection of
   * all lines have diverged from the default state (since it doesn't know
   * what all lines mean).
   */
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

**File:** app/src/lib/git/apply.ts (L52-61)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

```

**File:** app/src/lib/git/apply.ts (L80-81)
```typescript
  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/stores/app-store.ts (L3478-3492)
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
