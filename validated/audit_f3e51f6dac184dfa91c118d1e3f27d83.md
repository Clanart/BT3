Based on my investigation, the closest structurally-analogous bug in GitHub Desktop is a "checkbox line-selection" logic error in the side-by-side diff renderer, which — like the Sherlock report — is a case where a per-line ledger/state value is computed incorrectly (both "before" and "after" indices collapse to the same value instead of tracking two independent counters), leading to state values the rest of the pipeline (patch generation → partial commit) does not expect.

### Title
Incorrect line-selection for modified rows in side-by-side diff causes wrong lines to be staged/committed - (File: `app/src/ui/diff/side-by-side-diff.tsx`)

### Summary
`onLineNumberCheckedChanged` is supposed to resolve two independent diff-line indices for a modified row shown side-by-side — the "before" (deletion) line and the "after" (addition) line — and then toggle selection on each independently. Instead it calls the same lookup with the same arguments for both, so `lineBefore` and `lineAfter` are always identical.

### Finding Description
`onLineNumberCheckedChanged` computes both values from `getDiffLineNumber(row, column)` using the *same* `column` argument twice, rather than resolving the before-column and after-column line numbers separately: [1](#0-0) 

For a `Modified` diff row rendered side-by-side, the deletion (before) line and addition (after) line are two distinct entries in the unified diff with two distinct `diffLineNumber` values (the absolute index used by `DiffSelection`), as described in the row-data model: [2](#0-1) 

Because the code never queries the "other" column, checking or unchecking a checkbox on a modified row only ever resolves and toggles a single absolute diff-line index, applied twice via `withLineSelection`, rather than correctly updating both the deletion-side and addition-side entries for that displayed row. That selection state (`DiffSelection`) is exactly what feeds `formatPatch`, which walks the diff and only includes lines whose `absoluteIndex` `file.selection.isSelected(...)` reports true: [3](#0-2) 

The generated patch is then applied directly to the git index for the partial commit: [4](#0-3) 

### Impact Explanation
This is a "silent corruption of what the user commits" scenario: on a modified row, a user believes they are including/excluding a specific side (deletion or addition) of a change, but the underlying selection state does not track the two sides independently, so the resulting partial commit can silently include or exclude content the user did not intend to stage. Because `git apply --cached` trusts the generated patch without further confirmation of line-level intent, there is no downstream guard that catches a mismatch between UI checkbox state and the actual committed hunk content.

### Likelihood Explanation
This path is reached through completely ordinary, unprivileged UI interaction — checking/unchecking a line checkbox on a modified row in the side-by-side diff view during partial staging — which is a core, frequently used Desktop feature (partial commits). No special repository content or attacker capability beyond a normal modified-line diff is required to reach the code path, only for the row to be a two-column ("Modified") row.

### Recommendation
Fix `onLineNumberCheckedChanged` to resolve the before/after diff-line numbers independently — e.g. `getDiffLineNumber(row, DiffColumn.Before)` and `getDiffLineNumber(row, DiffColumn.After)` — so that toggling a checkbox correctly updates only the intended side's `diffLineNumber` in the `DiffSelection`, matching what `formatPatch` will subsequently stage.

### Proof of Concept
1. Open a file with a `Modified` line displayed side-by-side (one deletion + one differing addition on the same row).
2. Toggle only the "after" (addition) checkbox for that row while leaving the "before" checkbox in its previous state.
3. Because `getDiffLineNumber(row, column)` is invoked identically for `lineBefore` and `lineAfter`, both calls resolve to the same absolute diff index tied to whichever `column` was clicked; the other side's selection state is never independently updated.
4. Commit the partial change — the produced patch (via `formatPatch`) and staged index content do not match what the checkboxes visually indicated to the user, corrupting the intended partial commit. [5](#0-4) 

**Note on confidence**: I could not fully trace the exact runtime semantics of `getDiffLineNumber` in this session (its full body was not returned by search), so I cannot conclusively rule out that `column` internally disambiguates before/after in a way that mitigates the apparent duplication. This should be verified against the full body of `getDiffLineNumber` in `side-by-side-diff.tsx` before treating this as confirmed exploitable — I recommend a follow-up Devin session with full file access to confirm.

### Citations

**File:** app/src/ui/diff/side-by-side-diff.tsx (L935-960)
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
```

**File:** app/src/ui/diff/diff-helpers.tsx (L49-53)
```typescript
  /**
   * The line number on the original diff (without expansion).
   * This is used for discarding lines and for partial committing lines.
   */
  readonly diffLineNumber: number | null
```

**File:** app/src/lib/patch-formatter.ts (L143-168)
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

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }
```

**File:** app/src/lib/git/apply.ts (L60-83)
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

  return Promise.resolve()
```
