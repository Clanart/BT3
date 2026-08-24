### Title
Stale, index-based diff-selection state can silently commit the wrong lines after the diff is refreshed - ([File: app/src/lib/stores/app-store.ts])

### Summary
The external report's broken invariant is: a security‑relevant calculation trusts a positional/ordinal relationship (fee‑growth checkpoints keyed by tick order) that is not actually guaranteed to hold, and the consequence is that a dependent, safety‑critical operation (liquidation) behaves incorrectly. The Desktop analog is the working‑directory diff/selection system: line selections for a *partial commit* are stored as bare numeric indices into the current diff (`DiffSelection`), and when the diff is silently reloaded after the working tree changes, the code only prunes indices that no longer exist — it never verifies that a surviving index still points at the same logical line of content. If the shape (line count) of the new diff happens to line up, a previously "selected" index can now refer to a completely different line, and that wrong line gets included in the patch that is applied to the index and ultimately committed, with no indication to the user.

### Finding Description
`DiffSelection` tracks partial-commit line selection purely by absolute line index (`divergingLines: Set<number>`), not by line content or any stable identity: [1](#0-0) [2](#0-1) 

When the working-directory diff for the currently selected file is reloaded (e.g., because the file changed on disk while the user was reviewing/selecting lines), `updateChangesWorkingDirectoryDiff` recomputes the new diff and reconciles the selection purely by set-membership of indices, explicitly acknowledging it does **not** validate that a surviving selected index still corresponds to the same line: [3](#0-2) 

The comment at lines 3480-3485 states the actual invariant that is *not* enforced: "The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines such that any previously selected line which now no longer exists or has been turned into a context line isn't still selected." This mirrors the Uniswap report's underlying issue: an assumed-but-unverified positional invariant (there, tick-outside fee-growth ordering; here, index-to-content stability) is relied upon by the "commit" code path.

That reconciled selection is later consumed verbatim by the patch generator, which again works purely off absolute indices, with no cross-check against the previous diff's content: [4](#0-3) 

The resulting patch is fed straight into `git apply --cached` and then committed: [5](#0-4) 

### Impact Explanation
Because selection state survives a diff reload as long as the *count* of selectable lines lines up (or an index still happens to exist), a file that is modified between the time the user marks specific lines for inclusion and the time the commit patch is actually generated can cause Desktop to silently stage/commit different lines than the ones the user visually selected. This is exactly the "silent corruption of what the user commits or pushes" impact class: the user could believe they are committing line N (e.g., a config value) while the tool commits a different line at the same index in the refreshed diff (e.g., a line that still contains a secret, or omits an intended fix), because `isSelected()`/`isSelectable()` only reason about index membership, never content identity.

### Likelihood Explanation
Exploitation requires a change to the working tree file happening concurrently with (or right after) the user marking a partial selection but before the commit patch is generated — for example a build watcher, formatter-on-save, linter `--fix`, or a git hook triggered by another Desktop action, any of which can be configured by content shipped in a cloned/fetched repository (e.g. `package.json` scripts, `.vscode` tasks, git hooks templates) and would run as part of normal developer workflow, not because of "malware" or admin access. Because Desktop already re-diffs and reconciles the selection automatically and asynchronously (`updateChangesWorkingDirectoryDiff`), the race window is inherent to the app's design rather than requiring unnatural user steps; the acknowledging comment in the code confirms the maintainers are aware the current reconciliation is not content-safe.

### Recommendation
Track partial-commit selections by content-derived identity (e.g., hash of the line text plus its relative position within a hunk) rather than raw absolute indices, or, at minimum, invalidate/re-map the entire partial selection (falling back to "select all" or requiring explicit user reconfirmation) whenever the underlying diff text changes rather than only pruning indices that no longer exist. `formatPatch`/`applyPatchToIndex` should refuse to proceed (or should re-derive selection against the diff actually used to build the patch) if the diff has changed since the selection was captured.

### Proof of Concept
1. In Desktop, open a repository containing a file with partial changes already staged for review.
2. Select only specific added/removed lines for a partial commit via `DiffSelection.withLineSelection` (as exercised by `app/test/unit/patch-formatter-test.ts`).
3. Before committing, have an external process (a repository-provided watcher/formatter script, or a git hook) rewrite the file so that the number of selectable lines stays the same but their content differs (e.g., reordering/replacing lines at the same positions).
4. Desktop's `updateChangesWorkingDirectoryDiff` reloads the diff and calls `withSelectableLines`, which keeps the previously diverging indices intact because they still "exist" as selectable lines — see `app/src/lib/stores/app-store.ts:3478-3497`.
5. Commit the file; `formatPatch` (`app/src/lib/patch-formatter.ts:143-157`) builds the patch using the stale indices against the new hunk content, and `applyPatchToIndex`/`git apply --cached` (`app/src/lib/git/apply.ts:52-81`) stages/commits the wrong lines without any warning to the user.

Note: I was not able to execute the actual Desktop test suite/UI to observe end-to-end behavior in this environment; this analysis is based on static reading of the cited source and its own acknowledging comment about the unvalidated invariant, not a fully reproduced live commit.

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
