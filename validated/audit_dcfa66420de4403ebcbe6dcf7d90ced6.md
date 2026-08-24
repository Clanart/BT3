This confirms the mechanism. The `divergingLines` set stores raw absolute line indices (`hunk.unifiedDiffStart + lineIndex`) that are never remapped to content — only filtered by whether the same numeric index still exists in the newly computed `selectableLines` set. When the working tree changes externally (e.g. a git hook, a build step, or any process/tool that rewrites the tracked file after the user made their line selection but before the commit is created), `updateChangesWorkingDirectoryDiff` recomputes a brand-new diff and blindly reapplies the old numeric `divergingLines` via `withSelectableLines` at `app/src/lib/stores/app-store.ts:3486-3497`. If the new diff still has a line at that same absolute index (very likely for typical edits, since the diff is dense and only a few lines shift), the old selection silently now refers to a semantically different line. `formatPatch` in `app/src/lib/patch-formatter.ts:143-157` then trusts `file.selection.isSelected(absoluteIndex)` against the fresh diff to decide what enters the patch that is `git apply --cached`'d in `app/src/lib/git/apply.ts:80-81`, so the user can commit/stage content they never reviewed or reviewed-but-different content, with no re-confirmation.

### Title
Stale index-based diff line selection causes silent inclusion/exclusion of unreviewed lines in partial commits - (File: app/src/lib/stores/app-store.ts)

### Summary
GitHub Desktop tracks partial-commit line selections as raw numeric line indices (`DiffSelection.divergingLines`) rather than by line content/identity. When the working-directory diff is refreshed after the user has already made a partial selection (e.g. triggered by any filesystem change to the tracked file, including one made by an external tool, editor, formatter, or Git hook while Desktop is open), the old index-based selection is carried forward onto the new diff by simple set-intersection against `selectableLines`, without validating that the line at a given index still represents the same content the user selected.

### Finding Description
`updateChangesWorkingDirectoryDiff` (`app/src/lib/stores/app-store.ts:3404-3510`) recomputes the diff for the currently selected file whenever the working directory changes, and explicitly acknowledges the risk in its own comment: [1](#0-0) 
It only removes indices that are no longer part of the recomputed `selectableLines` set; it does not verify that a still-valid index still corresponds to the same line content the user originally selected. This is applied via `DiffSelection.withSelectableLines` (`app/src/models/diff/diff-selection.ts:320-330`), which merely filters `divergingLines` by set membership: [2](#0-1) 
When the commit is finally created, `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`) walks the *latest* diff and decides per-line inclusion purely from `file.selection.isSelected(absoluteIndex)`: [3](#0-2) 
The resulting patch is then applied straight to the index with `git apply --cached` in `applyPatchToIndex` (`app/src/lib/git/apply.ts:60-81`): [4](#0-3) 
Because `absoluteIndex` is purely positional (`hunk.unifiedDiffStart + lineIndex`, `app/src/lib/patch-formatter.ts:144`) and not tied to line hashes/content, any external modification to the file between the moment the user picks lines in the UI and the moment `_commitChanges` runs will silently remap "selected" to a different physical line if the new diff happens to still have a line at that same index (which is common for small edits, since most of a file's line-index space is unaffected by a localized change elsewhere).

### Impact Explanation
This is a silent corruption of what the user commits and pushes: content the user never saw or explicitly approved can be staged and committed (or vice versa — content the user intended to include can be silently dropped), without any error, warning, or re-confirmation dialog. An attacker who can trigger a filesystem write to a tracked file at the right moment (e.g. via a malicious `post-checkout`/`post-merge` hook shipped in a cloned repository, a build tool invoked as part of the repository's own tooling, or a background formatter/linter configured by the project) can cause a victim's partial-commit selection to diverge from what they reviewed, resulting in unintended data being committed and pushed under the victim's identity.

### Likelihood Explanation
Requires the victim to be actively using the partial-line-selection (stage-by-line) feature and for the tracked file's diff to be recomputed (any filesystem change notification) between selection and commit — a realistic timing window in normal Desktop usage, especially with repositories that ship hooks or run auto-formatting/build tools during active editing sessions. No local/admin access or pre-existing malware is required beyond what a normal cloned repository (with a hook) or a build/format tool already present in the project can provide.

### Recommendation
Track selection identity by line content/hash (or by hunk-relative old/new line numbers combined with a content fingerprint) instead of raw absolute diff-array index, and invalidate (rather than silently preserve) any selection whose underlying line content has changed since it was selected. At minimum, when `updateChangesWorkingDirectoryDiff` detects that the diff has changed for a file with a non-trivial (`Partial`) selection, force a full re-selection state (e.g. reset to `None`) and surface this to the user rather than carrying forward numeric indices across two structurally different diffs.

### Proof of Concept
1. In a repo containing a `post-checkout` (or any) hook, or alongside a formatter/build tool that rewrites a tracked file, open GitHub Desktop and modify `file.txt` so it has several hunks.
2. In the Changes view, select only specific lines (e.g., lines in hunk 2) for partial commit — this stores their absolute indices in `DiffSelection.divergingLines`.
3. Before committing, trigger a filesystem change to `file.txt` from outside Desktop (e.g., the hook/tool inserts/removes a line elsewhere in the file), causing Desktop's file watcher to call `updateChangesWorkingDirectoryDiff`, which recomputes the diff and calls `withSelectableLines` (`app/src/lib/stores/app-store.ts:3486-3497`).
4. If the new diff still has lines at the same absolute indices, they remain marked selected/deselected regardless of their new content.
5. Commit — `formatPatch`/`applyPatchToIndex` will include the line currently sitting at that index in the new diff, which is different content from what the user visually selected, and it is committed without any warning.

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
