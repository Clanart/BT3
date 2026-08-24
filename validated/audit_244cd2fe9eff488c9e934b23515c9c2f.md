Based on the code I've examined, there's a concrete stale-selection / TOCTOU pattern in GitHub Desktop's partial-commit staging path that mirrors the smart-contract bug class (check computed against one state snapshot, then applied against a different one at execution time, silently producing a result the user never approved).

### Title
Stale line-selection applied against a freshly re-fetched diff causes silent corruption of partial commits - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages a subset of lines for a partial commit, Desktop stores the user's line selections as absolute indices into a specific, previously-rendered diff. At commit time, `applyPatchToIndex` independently re-fetches a brand-new diff of the same file and reinterprets those old indices against it, with no verification that the new diff still matches the one the user actually reviewed.

### Finding Description
The commit path is:

1. The UI renders a diff for the selected file and the user marks specific lines as included/excluded. This selection is stored as a `DiffSelection` keyed by absolute line index, tied to the diff that was on screen when the user acted [1](#0-0) .
2. When the user clicks Commit, `_commitIncludedChanges` reads the files currently held in `state.changesState.workingDirectory.files` — carrying whatever `DiffSelection` was last set — and passes them straight to `createCommit` without re-validating that the selection still corresponds to the file's current content [2](#0-1) .
3. `createCommit` calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex` [3](#0-2) .
4. `applyPatchToIndex` fetches a **new, independent** diff via `getWorkingDirectoryDiff(repository, file)` right before generating the patch — this is not the diff the user looked at, it is whatever the working tree looks like at that exact moment [4](#0-3) .
5. `formatPatch` then walks this brand-new diff's hunks and decides whether to keep each line using `file.selection.isSelected(absoluteIndex)` — i.e., the old index-based selection is blindly applied to the new diff's line numbering [5](#0-4) .

There is no check anywhere in this path that the diff fetched in step 4 has the same hunk structure/line count as the diff the selection in step 1 was built from. The only place Desktop ever reconciles selection state against a changed diff is `updateChangesWorkingDirectoryDiff`, and even that only prunes indices that no longer exist as "includeable" lines by index-set membership — it explicitly does not validate that surviving indices still refer to the same content, as the comment states: *"Ideally we would be more clever about validating that any partial selection state is still valid... but for now we'll settle on just updating the selectable lines"* [6](#0-5) . Critically, this reconciliation only runs on an explicit UI diff refresh — it is never invoked as a precondition to `_commitIncludedChanges`/`applyPatchToIndex`, so the second, independently-fetched diff in `apply.ts` can diverge from what was reconciled or reviewed at all.

### Impact Explanation
If the tracked file's content changes between when the user reviews/selects lines and when the commit executes (e.g., a repository-provided build/watch/format script, a git clean/smudge filter, or any other tooling shipped by a cloned/fetched repository that rewrites tracked files as part of the normal dev workflow), the index-based selection is silently reapplied to different diff content. This can cause:
- Lines the user explicitly excluded (e.g., debug output, secrets, an unfinished change) to be silently included in the commit.
- Lines the user selected to be silently dropped.
- In the worst case, `git apply --cached` succeeding against unrelated context, producing a commit whose staged content does not match either the diff the user saw or the working tree at commit time.

This is a silent corruption of what the user commits and subsequently pushes, without any error or warning surfaced to the user — matching the explicit "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Partial/line-level commit staging is a routinely used Desktop feature. The race window between reviewing a diff and clicking Commit is realistic in normal workflows involving file watchers, formatters, linters-on-save, or code generators — all of which are commonly bundled and auto-run by repository tooling (package.json scripts, git hooks, `.gitattributes` filters) that a malicious or compromised repository can ship. No special privileges, local access, or unnatural user steps are required beyond the user's ordinary partial-commit workflow.

### Recommendation
Before staging (in `applyPatchToIndex`/`stageFiles`), verify that the freshly-fetched diff used to build the patch structurally matches the diff the current `DiffSelection` was derived from (e.g., compare hunk headers/line counts, or better, key the selection to diff content hashes rather than raw indices). If a mismatch is detected, abort the partial-stage operation and force the user to re-review the diff instead of silently reapplying a stale selection.

### Proof of Concept
1. Open a repository in Desktop and modify `file.txt`, producing a diff with additions on lines 1–10.
2. In the Changes view, deselect lines 6–10 (leave the last section unstaged) but do not commit yet.
3. While Desktop is idle, run a script (simulating repository-provided tooling) that appends/removes lines earlier in `file.txt`, shifting the true diff's line numbering without the user re-reviewing it.
4. Click Commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff (now shifted) and applies the stale index-based `DiffSelection` from step 2 against it via `formatPatch`.
5. Inspect the resulting commit: the staged hunks correspond to different logical lines than what was deselected in step 2, demonstrating that the commit content silently diverged from user intent.

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

**File:** app/src/lib/stores/app-store.ts (L3681-3699)
```typescript
  public async _commitIncludedChanges(
    repository: Repository,
    context: ICommitContext
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })

    const gitStore = this.gitStoreCache.get(repository)

    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
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

**File:** app/src/lib/git/apply.ts (L58-61)
```typescript
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

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
