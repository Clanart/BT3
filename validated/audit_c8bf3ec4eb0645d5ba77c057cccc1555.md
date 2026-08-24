### Title
Partial-commit line selection is applied against a freshly re-read diff, not the diff the user reviewed, allowing silent inclusion of unintended content in a commit - (File: `app/src/lib/git/apply.ts`)

### Summary
The external report's core flaw is that a UI displays state derived from a point-in-time snapshot, but the underlying data is applied/interpreted later without re-validating that the snapshot still matches reality — causing the user to act on stale information. In GitHub Desktop, the analogous broken invariant is in the partial-commit ("stage selected lines") flow: the `DiffSelection` a user builds while reviewing a diff is a set of line indices tied to a specific `IRawDiff`/`ITextDiff` snapshot, but at commit time `applyPatchToIndex` re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` and blindly re-applies the old index-based selection to it.

### Finding Description
When a user partially stages a file, `DiffSelection` records selected/unselected line indices (`divergingLines`) relative to the specific diff hunks that were rendered in the UI at selection time [1](#0-0) . Selection state travels with the `WorkingDirectoryFileChange` object independent of the diff content itself.

At commit time, `stageFiles` iterates files with partial selections and calls `applyPatchToIndex(repository, file)` for each [2](#0-1) . Critically, `applyPatchToIndex` does **not** use the diff that was displayed to the user and against which the selection was built — it independently re-reads the file from the working directory and generates a brand-new diff right before staging: [3](#0-2) 

That fresh diff is then passed to `formatPatch`, which walks `diff.hunks` and calls `file.selection.isSelected(absoluteIndex)` using the (now stale) line indices recorded earlier [4](#0-3) . There is no check that the newly fetched diff's hunk structure/line count matches the diff that produced the selection.

Desktop does have partial mitigation for the *in-memory* UI state: when the working-directory diff is reloaded while a file is selected, `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and prunes selections that no longer correspond to includeable lines [5](#0-4) . However, this reconciliation only happens when the *UI* explicitly reloads the diff for display; it does not run again immediately before `_commitIncludedChanges` calls `createCommit` → `stageFiles` → `applyPatchToIndex`, and `applyPatchToIndex`'s own diff fetch is a completely separate, un-reconciled read. If the file content on disk changes between the last UI diff reconciliation and the commit action — e.g. via an asynchronous file watcher, a lint/format-on-save tool, a build step, a `smudge`/`clean` filter, or a git hook triggered by another Desktop-initiated operation — the absolute line indices in the old `DiffSelection` can now point at entirely different hunks/lines in the new diff.

### Impact Explanation
Because the same set of numeric indices is reinterpreted against different hunk contents, the resulting patch produced by `formatPatch`/`applyPatchToIndex` can select lines the user never intended to include (or exclude lines they did intend), and `git apply --cached` will happily stage whatever textually parses. This is a silent corruption of what the user commits: the commit that lands in history — and gets pushed — can contain content the user never reviewed or approved, without any error being surfaced (`formatPatch` only throws if the resulting patch is completely empty, not if it diverges from the reviewed selection) [6](#0-5) . This matches the report's underlying class: a cached/stale snapshot of state is trusted for a downstream action after the real state has moved on.

### Likelihood Explanation
This requires no local/admin access or leaked credentials — only a mechanism, present in many real developer workflows, that mutates a working-directory file shortly after the user opens the diff panel and starts selecting lines (auto-formatters, linters, git smudge filters from a cloned/fetched repository's `.gitattributes`, editor auto-save, or another async Desktop-initiated git operation touching the same file). Since Desktop already anticipates and partially guards against "the diff might have changed dramatically since last we loaded it" in the UI path [7](#0-6) , this confirms the underlying race is a known-but-incompletely-guarded condition; the guard simply does not cover the final commit-time re-fetch inside `apply.ts`.

### Recommendation
Before calling `git apply --cached` in `applyPatchToIndex`, compare the newly fetched diff's hunk boundaries/line composition against the diff snapshot the selection was built from (e.g., via a hash/fingerprint stored on `DiffSelection` or by threading the original diff object through `stageFiles`/`createCommit` instead of re-fetching). If they diverge, either abort the partial-stage for that file and prompt the user to re-review, or automatically fall back to re-deriving `selectableLines` from the fresh diff (as already done for UI display) before formatting the patch, and surface a visible warning rather than silently applying a possibly-mismatched patch.

### Proof of Concept
Conceptual reproduction (based on code paths, not executed):
1. Open a repository in Desktop and select a modified file so its diff is displayed; select only lines 3-4 for staging (`DiffSelection.divergingLines = {3,4}` relative to hunk layout A).
2. Before clicking "Commit", have an external process — e.g. an editor auto-format, a `clean` filter run by a background `git` op, or a file watcher script shipped in the repo — rewrite the file, shifting/adding hunks so that indices 3-4 in the new diff now correspond to different, attacker-influenced lines (hunk layout B). Desktop does not re-run `updateChangesWorkingDirectoryDiff`'s reconciliation before commit.
3. Click "Commit". `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` fetches diff B via `getWorkingDirectoryDiff` [8](#0-7) , then `formatPatch` applies the stale `{3,4}` selection to diff B's lines [4](#0-3) , producing a patch that stages content different from what the user reviewed and clicked "Include" on.
4. The resulting commit — and any subsequent push — silently contains unreviewed content, with no error shown to the user.

### Citations

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
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

**File:** app/src/lib/patch-formatter.ts (L222-227)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
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
