## Analog Identified

The Solidity report's broken invariant — *using a cached/outdated interest-bearing value (`cumulativeInterestRate`) in `collectFees()` without first calling `updateBalances()` to refresh it* — maps to a concrete Desktop pattern: **a `DiffSelection` (a bitset of line indices) computed against one snapshot of a file's diff is later re-applied against a freshly re-fetched diff of that same file, without re-validating that the line-index mapping still corresponds to the same content**, when the actual commit patch is built.

### Title
Stale line-index `DiffSelection` applied to freshly re-fetched diff can silently commit different content than the user reviewed - (File: `app/src/lib/git/apply.ts`)

### Summary
When Desktop stages a partially-selected file for a commit, `applyPatchToIndex` re-fetches the working-directory diff from disk at commit time and re-applies the `WorkingDirectoryFileChange.selection` (built earlier from a different diff snapshot shown in the Changes UI) purely by numeric line index, with no verification that the line contents still match what the user actually selected.

### Finding Description
`applyPatchToIndex` fetches a brand-new diff right before building the patch to stage: [1](#0-0) 

That diff is handed to `formatPatch`, which decides what to include per hunk line purely via `file.selection.isSelected(absoluteIndex)` — an index-based lookup into a bitset (`DiffSelection`), not a content-based comparison: [2](#0-1) 

The `file.selection` passed in, however, was produced earlier in the UI against a *different* diff object — the one rendered when the user reviewed and checked/unchecked lines in the Changes view. Desktop is aware that diffs can go stale between renders: `updateChangesWorkingDirectoryDiff` explicitly re-derives `selectableLines` from a newly loaded diff and prunes now-invalid selections: [3](#0-2) 

But that reconciliation only happens when the Changes panel diff is reloaded (e.g., on `_loadStatus`/`refreshChangesSection`) — it is not re-run immediately before `_commitIncludedChanges` invokes `createCommit` → `stageFiles` → `applyPatchToIndex`, which performs its own independent `getWorkingDirectoryDiff` fetch: [4](#0-3) [5](#0-4) 

If the file's on-disk content changes (e.g. via a repo-provided pre-commit/prepare-commit-msg hook that runs *before* staging is finalized in some flows, an editor auto-save, a build/watch tool, or any other process touching the working tree) between the last UI diff snapshot and the `applyPatchToIndex` re-fetch, the hunk layout/line indices shift. Because `formatPatch` trusts raw numeric indices from the stale selection against the new hunk structure, it can select entirely different logical lines than the ones the user reviewed and explicitly chose to include — with no error, since `git apply --cached` will happily accept a patch that is syntactically consistent with the new tree even if its semantic content diverges from user intent.

### Impact Explanation
This breaks the core trust invariant of partial/line-based staging: what the user visually selected in the diff view is not guaranteed to be what actually gets staged and committed. In an attacker-influenced repository (e.g. via a checked-out branch containing a hook or a build script that mutates tracked files as a side effect of routine repository operations Desktop triggers, such as status refreshes), this can result in silent corruption of commit content — the user believes they excluded certain lines/secrets/changes, but a shifted selection stages and commits different content, which may then be pushed. This matches the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Exploitation requires a realistic window where the working tree changes between diff display and commit execution, and a mechanism (attacker-controlled hook, watch/build tool, or another benign process) to trigger that mutation reliably within a normal user commit workflow. This is a timing/TOCTOU-style condition rather than a single deterministic trigger, so likelihood is moderate rather than high — it depends on the attacker being able to arrange a file mutation between selection and commit in the victim's environment.

### Recommendation
Before constructing the commit patch, re-fetch or re-validate the diff for each partially-selected file at the exact point `stageFiles`/`applyPatchToIndex` runs (not only during Changes-panel refreshes), and abort or force full-file staging with a user-visible warning if the underlying diff content has changed since the selection was made — mirroring how `updateChangesWorkingDirectoryDiff` already reconciles `selectableLines`, but performed synchronously immediately before staging rather than opportunistically on unrelated refresh events.

### Proof of Concept
1. Open a repository in Desktop with a tracked file `foo.txt` containing multiple hunks of changes.
2. In the Changes view, partially select only specific "safe" lines/hunks to commit (leave other, sensitive lines unselected). This selection is stored as line-index ranges in `DiffSelection`.
3. Before clicking "Commit", have another process (a git hook, editor auto-format-on-save, or a background watcher — as could exist in a repository with a `.git/hooks/pre-commit` shipped in a malicious/compromised clone, or a configured tool) modify `foo.txt`, shifting line numbers/hunks without the Changes view being refreshed again.
4. Click "Commit". `_commitIncludedChanges` calls `createCommit` → `stageFiles` → `applyPatchToIndex`, which fetches a *new* diff via `getWorkingDirectoryDiff` and applies the old index-based `DiffSelection` (from step 2) against it via `formatPatch`.
5. Inspect the resulting commit: it contains different line content than what was visually checked/unchecked by the user in step 2, demonstrating silent corruption of the intended commit.

### Citations

**File:** app/src/lib/git/apply.ts (L60-61)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)

```

**File:** app/src/lib/patch-formatter.ts (L143-171)
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

        anyAdditionsOrDeletions = true
      } else {
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
