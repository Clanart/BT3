## Analog Found: Stale, Position‑Indexed Partial-Selection State Not Reset When a File's Diff Shifts

### Title
Partial commit line-selection state is preserved by file ID across diff refreshes instead of being reset, allowing silent corruption of what gets committed - (`app/src/lib/stores/updates/changes-state.ts`)

### Summary
The report's bug class is: a numeric/indexed piece of state ("bucket") keyed by an identity that can be reused is carried forward across an update instead of being reset, because the reset code path assumes identity-based reuse implies value validity. GitHub Desktop has a structurally identical pattern for partial commit line-selections: `WorkingDirectoryFileChange.id` is derived only from `status.kind + path` (not file content), and when working-directory status is refreshed, `updateChangedFiles` unconditionally carries over the *old* `DiffSelection` object (a `Set<number>` of line indices) onto the file with the *same id*, before the new diff content/hunks have even been fetched.

### Finding Description
`WorkingDirectoryFileChange`/`FileChange` compute a stable `id` purely from status kind and path: [1](#0-0) 

On every `git status` refresh, `updateChangedFiles` re-associates each new status entry with any *existing* `WorkingDirectoryFileChange` that shares the same `id`, and if it does, simply reuses the existing selection object wholesale: [2](#0-1) 

`DiffSelection` internally represents a partial selection as a `Set<number>` of *diverging line indices* relative to the diff's hunk-line numbering (`unifiedDiffStart + index`), not by line content: [3](#0-2) 

The only place where selection indices are reconciled against a *new* diff is `updateChangesWorkingDirectoryDiff` in `app-store.ts`, which recomputes the selectable index set and calls `withSelectableLines` to prune indices that no longer correspond to includeable lines: [4](#0-3) 

`withSelectableLines` only *filters* the diverging-line set against the new selectable set — it does not remap indices to the same textual content: [5](#0-4) 

This reconciliation runs asynchronously, *after* `getWorkingDirectoryDiff` returns (an async git subprocess call), while `updateChangedFiles` (called synchronously on every status poll/refresh) has already re-emitted state with the raw, un-reconciled old selection attached to the file with the reused `id`. The explicit code comment even acknowledges the underlying assumption is best-effort, not a real fix:
> "The diff might have changed dramatically since last we loaded it... but for now we'll settle on just updating the selectable lines..." (`app-store.ts:3480-3485`, see above citation).

If the diff's line layout shifts between the previous selection and the freshly reconciled one (e.g., lines inserted/removed above the previously selected block due to a rebase artifact, a merge, a background fetch triggering a working-tree change via hooks, or another concurrent git operation touching the same file while status keeps re-polling), the stale `Set<number>` of "diverging lines" still refers to the *old* line positions. Because the file `id` never changes (kind+path unchanged), the guard in `updateChangedFiles` treats the old selection as valid and reuses it directly, exactly like `applyDiffToCounters` reusing the old bucket's value instead of resetting it when bucket identity ("id") is unchanged but the underlying quantity being tracked has moved.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." If a commit is triggered in the window between the status-refresh reuse (`updateChangedFiles`) and the diff-reconciliation completing (`updateChangesWorkingDirectoryDiff`), or if the reconciliation's positional (not content-based) pruning fails to invalidate a still-"selectable" but now-different index, the partial-commit engine (`app/src/lib/git/patch-formatter` / stage-by-line commit path, which reads `DiffSelection.isSelected(lineIndex)`) will commit whichever lines currently sit at those numeric indices — not the content the user actually reviewed and checked. This can cause a user to unknowingly commit/push content they never selected (or omit content they intended to include), which is a legitimate Desktop-specific "silent corruption of a commit" class of bug.

### Likelihood Explanation
This requires a normal, unprivileged workflow: a partially-staged file plus a concurrent working-directory change (background status polling in Desktop runs continuously; any operation that updates a file's diff — a fetch-triggered hook, LFS smudge, or concurrent tool writing to the file — can shift line numbers) landing between the two update passes. No admin rights, local exploit primitives, or social engineering beyond normal usage are needed. That said, I could not fully trace the exact commit-trigger timing/IPC path to confirm a deterministic race window is reachable purely from remote/API-controlled content within the available indexed code, so likelihood should be treated as plausible-but-unverified rather than confirmed exploitable end-to-end.

### Recommendation
- Invalidate (reset to `None`/empty) any partial selection whenever the underlying diff/hunks for a file change, rather than best-effort filtering the diverging-line `Set<number>` by selectability.
- Key cached selection/diff state by a content-derived identity (e.g., blob hash or diff hash) in addition to `kind+path`, so identity is not considered "the same bucket" when content shifts — analogous to properly resetting a bucket's value when its logical index changes underneath an unchanged storage slot.
- Make diff reconciliation (`updateChangesWorkingDirectoryDiff`) block/gate commit actions until the reconciled diff+selection are confirmed consistent with the file id currently used for staging.

### Proof of Concept
1. Open a repository in Desktop and modify `foo.txt`, selecting only lines 5–6 for a partial commit (leaving other lines unselected).
2. While the partial selection is pending, trigger a concurrent change to `foo.txt`'s diff shape without changing its Desktop-tracked status kind/path — e.g., another local git process, an LFS smudge filter, or an autosave from an external tool inserts 3 new lines near the top of the file, shifting all later line numbers, right as Desktop's periodic background status refresh runs (`updateChangedFiles`).
3. Because `file.id` (`Modified+foo.txt`) is unchanged, `updateChangedFiles` reuses the old `DiffSelection` (`divergingLines = {5,6}`) immediately, before `updateChangesWorkingDirectoryDiff` finishes fetching/reconciling the new diff asynchronously.
4. If the user commits in that window (or if `withSelectableLines`'s filter fails to fully invalidate the stale indices because those numeric positions still happen to be "selectable" in the new diff), the commit stages whatever content now occupies index positions 5–6 in the new diff — not the lines the user actually reviewed and checked — silently corrupting the resulting commit.

Because I could not fully confirm the exact synchronization/commit-trigger code path guaranteeing this race is reliably reachable, this should be validated further with a live Desktop session before treating it as a confirmed, deterministic exploit; the underlying "identity-based reuse without content-based reset" flaw in `updateChangedFiles`/`DiffSelection`, however, is directly verifiable in the cited source.

### Citations

**File:** app/src/models/status.ts (L263-275)
```typescript
  public constructor(
    public readonly path: string,
    public readonly status: AppFileStatus
  ) {
    if (
      status.kind === AppFileStatusKind.Renamed ||
      status.kind === AppFileStatusKind.Copied
    ) {
      this.id = `${status.kind}+${path}+${status.oldPath}`
    } else {
      this.id = `${status.kind}+${path}`
    }
  }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L43-61)
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
    .sort((x, y) => caseInsensitiveCompare(x.path, y.path))
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
