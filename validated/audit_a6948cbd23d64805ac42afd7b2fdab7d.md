## Analog Found: Partial-commit line selection is validated against one diff but applied against a different, unsynchronized diff

### Title
Diff-line selection indices computed against the whitespace‑hidden diff are replayed against a full (whitespace‑visible) diff when staging, silently altering which lines are committed — (File: `app/src/lib/git/apply.ts`)

### Summary
This mirrors the M‑03 pattern exactly: a piece of state (`DiffSelection`'s `divergingLines`, a `Set<number>` of "absolute line indices") is populated using one indexing space, but consumed elsewhere assuming a different, unrelated indexing space that is not guaranteed to be in sync. In the smart-contract report, `settlementHistory` is written keyed by NFT id but read elsewhere keyed by a monotonically incrementing `settlementId`. In Desktop, the working-directory diff used to compute per-line selection indices in the UI can differ from the diff used moments later to actually build the git patch, so the stored line indices point at the wrong lines.

### Finding Description
When the user views the Changes diff, the app loads it with the user's whitespace preference: [1](#0-0) 

`getWorkingDirectoryDiff(repository, file, hideWhitespaceInDiff)` will produce a diff whose hunks/line indices are computed after collapsing whitespace-only changes (`git diff -w`), so the number and layout of `hunk.lines` differs from a diff generated without that flag. The user's line-selection UI (`side-by-side-diff.tsx`) then calls `selection.withLineSelection(lineIndex, ...)` using indices from *this* diff: [2](#0-1) 

`DiffSelection` itself has no notion of what diff produced the indices — it just stores a bare `Set<number>`: [3](#0-2) 

When the user commits a partial selection, `applyPatchToIndex` re-fetches the working directory diff **without** passing the whitespace flag (it defaults to `false`): [4](#0-3) 

That freshly-fetched diff is then passed straight into `formatPatch`, which walks `diff.hunks` and tests `file.selection.isSelected(absoluteIndex)` where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` is computed from the *new* diff's hunk layout: [5](#0-4) 

If the file contains whitespace-only differences (which an attacker fully controls simply by crafting file content with such lines), the whitespace-hidden diff shown to the user and the full diff fetched for staging have different hunk boundaries and line counts. The `divergingLines` set built against the former is blindly reinterpreted against the latter's line numbering, so `formatPatch` selects/deselects the wrong lines — exactly the "settlementHistory accessed by a wrong value" class of bug: one array's index used as if it were another array's index.

### Impact Explanation
The consequence is silent corruption of what the user commits: lines the user explicitly deselected can end up included in the generated patch (and thus staged/committed), or lines the user intended to include can be silently dropped, with no error surfaced (`formatPatch` only throws if the resulting patch is completely empty). This falls squarely under the accepted impact category of "silent corruption of what the user commits or pushes," driven by content in a repository the attacker/author controls (whitespace-varying lines), requiring no local/privileged access.

### Likelihood Explanation
Likelihood is Medium: it requires (a) the "hide whitespace" diff option to be enabled when reviewing changes, and (b) the modified file to contain whitespace-only differences alongside substantive changes, and (c) the user to make a partial (line-level) selection rather than staging the whole file. All three conditions are plausible in normal workflows (e.g., reformatted files, tabs/spaces changes) and none require special privileges — an attacker only needs to shape the repository's file content, not the app or environment.

### Recommendation
Do not persist raw line indices independent of the diff that produced them. Either (1) always regenerate/re-validate the selection against the exact same diff object used for staging (pass the same `hideWhitespaceInDiff` value used to render the UI into `applyPatchToIndex`'s `getWorkingDirectoryDiff` call, or better, reuse the already-loaded diff instance instead of refetching), or (2) key `DiffSelection` by a content-stable identifier (e.g., line content hash or original-file line numbers) rather than a positional index into a specific diff rendering, and add a consistency check that rejects staging if the diff used to build the patch does not match the diff the selection was derived from.

### Proof of Concept
1. Modify a tracked file so it has both a substantive change and unrelated whitespace-only changes (e.g., trailing spaces added/removed on several lines), and enable "hide whitespace changes" in Desktop's diff view.
2. Desktop calls `getWorkingDirectoryDiff(repo, file, true)` to render the diff; the whitespace-only lines are collapsed, changing hunk boundaries relative to the raw diff.
3. In the UI, deselect the single substantive line (using `onLineNumberCheckedChanged` → `selection.withLineSelection(lineBefore/After, false)`), leaving everything else selected. The stored `divergingLines` set now contains an index that is only meaningful relative to the whitespace-hidden diff's hunk layout.
4. Click "Commit". `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff(repository, file)` with the whitespace flag defaulted to `false`, producing a diff with different hunk offsets/line counts (because whitespace-only lines are no longer collapsed).
5. `formatPatch` evaluates `file.selection.isSelected(absoluteIndex)` against this new hunk layout: the index that used to point at the deselected substantive line now points at a different line (e.g., a whitespace-only line), so the actually generated patch includes the line the user explicitly excluded and/or excludes a different line than intended.
6. Inspect the resulting commit/staged patch: it does not match what was shown/selected in the UI, demonstrating silent corruption of the commit contents.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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

**File:** app/src/models/diff/diff-selection.ts (L41-53)
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
export class DiffSelection {
```

**File:** app/src/lib/git/apply.ts (L59-61)
```typescript

  const diff = await getWorkingDirectoryDiff(repository, file)

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
