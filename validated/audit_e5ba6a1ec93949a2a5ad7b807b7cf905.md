This confirms the mechanism: `DiffSelection` tracks selected lines purely by positional index (`divergingLines: Set<number>`), with zero awareness of line *content*. When a fresh diff is refetched (`getWorkingDirectoryDiff`), the old positional selection is blindly reapplied to new hunk positions.

### Title
Partial-commit line selection is applied against a freshly-fetched, unsynchronized diff, silently staging/discarding the wrong content - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop's partial-commit ("stage selected lines") feature stores which lines a user wants committed as pure positional indices (`DiffSelection.divergingLines`), decoupled from the actual diff content. When staging occurs, `applyPatchToIndex` re-fetches a brand-new diff from the working directory at commit time and reapplies the old positional selection to it, without re-validating that the positions still refer to the same lines the user reviewed.

### Finding Description
The flow is:
1. The user opens the Changes view; `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts` (around line 3404-3513) fetches a diff and lets the user select/deselect individual lines via `DiffSelection`, which records selections purely as line-index numbers (`app/src/models/diff/diff-selection.ts`, `isSelected(lineIndex)`, `withLineSelection`).
2. The code's own comment acknowledges the risk: "The diff might have changed dramatically since last we loaded it... we'll settle on just updating the selectable lines" [1](#0-0) . But this reconciliation only runs when `updateChangesWorkingDirectoryDiff` is explicitly re-invoked (e.g. on file (re)selection or a `_loadStatus` refresh) — not synchronously guaranteed to run immediately before every commit.
3. When the user clicks Commit, `_commitIncludedChanges` takes whatever `file.selection` is currently sitting in `state.changesState.workingDirectory.files` [2](#0-1)  and passes it straight into `createCommit` → `stageFiles` → `applyPatchToIndex`.
4. `applyPatchToIndex` then calls `getWorkingDirectoryDiff(repository, file)` **again**, fetching a completely fresh diff of the current on-disk content [3](#0-2) , and immediately builds the patch with `formatPatch(file, diff)`, which walks the new diff's hunks and calls `file.selection.isSelected(absoluteIndex)` using the *stale* positional selection against the *fresh* hunk line indices [4](#0-3) .

If the file's on-disk content shifts (lines added/removed) between the last diff render/selection-reconciliation and the commit — e.g. because a `.gitattributes`-driven `clean`/`smudge` filter runs, an editor autosaves, a build step regenerates the file, or another Desktop action (discarding a different file, switching branches) triggers a working-directory mutation without a full diff-selection reconciliation pass on this file — the line indices in `divergingLines` no longer correspond to the same logical lines. `formatPatch` will then silently select/deselect the wrong lines: content the user never opted into can be committed, and content the user did select can be silently dropped, with no error and no warning to the user.

This is structurally identical to the Panoptic bug class: one value (asset/collateral valuation, here: the user's line selection) is derived from stale state, while the paired value (debt, here: the actual diff) is recomputed fresh, and the two are combined without reconciliation — producing a corrupted result that the code has no invariant to catch.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." The user believes they reviewed and selected specific lines; the actual committed patch can differ from what was shown, without any diagnostic. In the worst case this can result in unintended data being committed (e.g., partially-reverted secrets, or lines that were supposed to stay uncommitted) or an intended fix being silently dropped from the commit while the UI shows success.

### Likelihood Explanation
The window for this race is narrow but real: it requires (a) a partial/line-level selection on a file, and (b) the working-directory content of that specific file changing between the last diff refresh and the moment `stageFiles` runs, without an intervening call to `updateChangesWorkingDirectoryDiff` for that file. Desktop already runs many operations that mutate the working directory independent of the currently selected file's diff (e.g., discarding changes to another file can trigger broader refreshes, checkouts, stash pops, or externally-triggered filter/hook executions on the repository). No user "unnatural" action is required beyond normal use of partial-line staging.

### Recommendation
Before building the patch in `applyPatchToIndex`, compare the freshly-fetched diff's hunk signature (e.g. hashed content, not just positions) to the diff the user's `DiffSelection` was computed against, and refuse/re-derive the selection (falling back to safe defaults, e.g., "select nothing" or re-prompt) if they don't match, rather than blindly reapplying stale positional indices to new hunk positions.

### Proof of Concept
1. In Desktop, modify a tracked file with several distinct hunks and open the Changes view; make a **partial** line selection (e.g., select only the first hunk's added lines) via `DiffSelection`.
2. Before clicking "Commit", cause the file's on-disk content to shift lines outside the app (e.g., an external process/editor inserts/removes lines above the selected hunk, or a `.gitattributes` filter reformats the file) without triggering `updateChangesWorkingDirectoryDiff` for that file (this can be simulated in a unit test by directly calling `applyPatchToIndex`/`stageFiles` with a `WorkingDirectoryFileChange` whose `selection` was computed against an old diff, while the working tree already reflects a newer diff — analogous to the existing test harness patterns in `app/test/unit/git/commit-test.ts`).
3. Click Commit / run `createCommit`; inspect the resulting commit via `git show` and compare it against the lines the user actually selected in the UI — they will differ because `formatPatch` applied `divergingLines` computed for the old hunk layout to the new one.

Note: I was not able to execute this end-to-end in a live Desktop instance (no filesystem/terminal access here), so the exact reproduction steps for triggering the out-of-band file mutation (filter/hook vs. concurrent Desktop operation) would need to be validated by a developer with a running build; the code-level mismatch (stale positional `DiffSelection` vs. freshly re-fetched diff in `applyPatchToIndex`) is confirmed directly from source.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3685-3689)
```typescript
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
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
