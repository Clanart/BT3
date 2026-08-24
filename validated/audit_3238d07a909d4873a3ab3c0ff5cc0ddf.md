### Title
Stale line-index diff selection can silently commit unreviewed lines after concurrent working-directory mutation - ([File: app/src/lib/stores/app-store.ts], [File: app/src/models/diff/diff-selection.ts])

### Summary
This is the closest structural analog to the reported `_cachedTotalUnderlying` bug class: a cached derived value (`DiffSelection`'s per-line "diverging lines" index set) is carried forward and reapplied to a *new* underlying dataset without being recomputed from scratch, so it can silently diverge from what is actually on disk/in the new diff, the same way `_cachedTotalUnderlying` silently diverged from the real underlying balance after slippage.

### Finding Description
`updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts` reloads a file's diff asynchronously and then patches the *existing* `DiffSelection` object with the new diff's selectable-line set, rather than rebuilding the selection from the new diff content: [1](#0-0) 

The code comment explicitly documents the invariant weakness being exploited: "The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..." [2](#0-1) 

`DiffSelection.withSelectableLines` only *filters* the previously "diverging" line-index set down to indices that still exist in the new selectable set — it does not re-derive selection state from actual line content: [3](#0-2) 

Because selection is tracked purely by **numeric line index** (not by content hash or stable anchor), if the new diff has the same *number* of includeable lines at the same indices but different *content* (e.g., a hunk shifted, or new lines were inserted/removed elsewhere and hunks recomputed to the same indices), the previously selected indices are silently reapplied to different actual content. The same broken invariant appears in `updateChangedFiles` (`app/src/lib/stores/updates/changes-state.ts:32-116`), which reuses `existingFile.selection` keyed only by file `id` (path) across a `git status` refresh, with `clearPartialState` only clearing this on specific flows (post-commit) and not on ordinary background polling: [4](#0-3) 

The staleness window is real: `_loadStatus` / `updateChangesWorkingDirectoryDiff` run asynchronously (`await getWorkingDirectoryDiff(...)` and `await gitStore.loadStatus()`), during which an attacker-influenced process (e.g., a malicious `post-checkout`/`post-merge`/`smudge` filter or file-watcher-triggering script embedded in a cloned/fetched repository) can mutate the working tree files between the diff computation and the user's click on "Commit". The guard clauses in the code (`shasAfter`, `selectedFileID !== selectedFileIdBeforeLoad`, etc.) only protect against *selection* changes, not against the *file content itself* changing while indices/positions coincidentally line up.

### Impact Explanation
If exploited, a user could commit (and subsequently push) content different from what they visually reviewed and approved in the diff/selection UI — this is a silent corruption of what the user commits, one of the explicitly valid impact classes (silent corruption of what the user commits or pushes), driven entirely by an attacker-controlled repository artifact (a git hook/filter shipped in a cloned or fetched repo), with no need for local/physical access, admin rights, or pre-existing malware beyond what the cloned repository itself supplies.

### Likelihood Explanation
Exploitation requires precise timing/content control (attacker must predict hunk boundaries so identical line indices remain "includeable" while content differs) and a way to trigger a filesystem write during the status/diff-reload window (e.g., a git hook or smudge filter run implicitly by Desktop's own git operations, such as background fetch/prune or status polling). This is a real but narrow race condition — the maintainers' own comment acknowledges the selection-validity gap is a known, intentionally deferred limitation rather than a hardened guarantee, which raises plausibility above a purely theoretical race.

### Recommendation
Recompute `DiffSelection` state from the new diff's actual hunk/line *content* (not just index membership) whenever a diff or status refresh completes — e.g., hash the previous line content at each selected index and only preserve selection if the underlying line text is unchanged, invalidating (or defaulting to none/all with an explicit re-review prompt) selections whose backing content changed. Additionally, consider re-verifying working-directory file hashes immediately before invoking `createCommit` in `_commitIncludedChanges`, aborting/warning if files changed since the diff was last displayed to the user.

### Proof of Concept
Conceptual PoC (requires a background Devin/engineering session to fully implement and verify against the live app, since this needs Electron runtime + git hook execution, which is outside static analysis capability):
1. Clone a malicious repository containing a `post-checkout` (or similar) hook that, when triggered by Desktop's background git operations, rewrites a tracked file such that the new diff has the same number/index of includeable lines as before but different textual content (e.g., swaps two lines of equal count within the same hunk boundaries).
2. In GitHub Desktop, open the repo, select a subset of lines via partial `DiffSelection` in the Changes view.
3. While the selection is displayed, let a background status/diff refresh (`_loadStatus` → `updateChangesWorkingDirectoryDiff`) fire concurrently with the hook-triggered file mutation.
4. Click Commit before manually re-reviewing the diff; observe that `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681-3760`) commits the lines at the previously selected indices, which now correspond to different, attacker-modified content than what was last visually reviewed.

Note: this PoC could not be executed in this read-only analysis; a Devin session with terminal/Electron access would be needed to confirm the exact hook-timing feasibility and whether other guards (e.g., debounced diff invalidation) prevent it in practice.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3496)
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

**File:** app/src/lib/stores/updates/changes-state.ts (L43-60)
```typescript
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
    })
```
