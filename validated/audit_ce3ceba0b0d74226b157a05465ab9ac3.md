### Title
Line-index-based partial commit selection is re-applied to a freshly-fetched diff, allowing silent misselection of committed hunks - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop's partial-commit ("stage specific lines") feature represents the user's selection as a set of **absolute line indices** (`DiffSelection`) computed against one specific diff snapshot. When the commit is actually created, `applyPatchToIndex` re-fetches a brand-new diff of the working directory and blindly re-applies those same numeric indices to it via `formatPatch`, instead of re-using (or re-validating against) the diff the user actually looked at when selecting lines. If the diff shape changes between the moment the user made the selection and the moment `applyPatchToIndex` runs, the same indices can land on different lines, causing Desktop to silently stage/commit content the user never selected.

### Finding Description
`applyPatchToIndex` fetches a **new** diff at apply time and feeds it, together with the file's pre-existing `selection`, into `formatPatch`: [1](#0-0) 

`formatPatch` decides whether each line is included purely based on `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` of the **diff passed in**: [2](#0-1) 

The `selection` object, however, was built earlier in the UI against whatever diff was being displayed at the time the user clicked individual lines/hunks. Desktop's own code acknowledges that a diff's shape can change out from under a live selection: `updateChangesWorkingDirectoryDiff` explicitly re-derives which absolute indices are still selectable after a diff reload, noting that "[t]he diff might have changed dramatically since last we loaded it" and that it isn't fully validating whether previously selected lines still correspond to the same content: [3](#0-2) 

That reconciliation only runs when the Changes-view diff pane itself is refreshed. It is **not** run again inside `applyPatchToIndex`, which independently calls `getWorkingDirectoryDiff` a second time right before generating the patch. There is no check anywhere in the commit path (`_commitIncludedChanges` → `createCommit` → `applyPatchToIndex`) that the diff used to build `file.selection` is the same diff being patched — no diff identity/hash comparison, no re-validation of hunk boundaries, no user-facing warning if they diverge: [4](#0-3) 

The broken invariant mirrors the report's pattern exactly: two computations that must be evaluated against the *same* snapshot (the block height in the original report; the diff/line-numbering here) are instead evaluated against two different snapshots — one used to build the "vote"/selection, the other used for the "total"/patch — and nothing forces them to agree.

### Impact Explanation
If the working tree changes between selection and commit (e.g., a background process, editor autosave, LFS/clean-filter smudge triggered by attacker-supplied `.gitattributes` in a cloned repository, or any other write to the tracked file), `formatPatch` can attach the user's approved "include this line" flags to the wrong lines of the new diff. This produces a **silently corrupted commit**: the user believes they staged specific reviewed lines, but Desktop actually stages/commits different content, with no error and no additional confirmation — matching the "silent corruption of what the user commits" impact category.

### Likelihood Explanation
The bug requires the tracked file's diff shape to change between the time the selection is made in the UI and the time `applyPatchToIndex` runs — a narrow timing window, but one Desktop repeatedly creates on its own (periodic background `status`/`diff` refreshes, autosave-driven editors, or content-mutating filters defined in a cloned repository's `.gitattributes`). No git-side or Desktop-side guard currently prevents committing against a selection built from a stale diff, so once the timing condition occurs, the mis-staging is deterministic and undetected.

### Recommendation
Before generating the patch in `applyPatchToIndex`/`formatPatch`, verify that the diff being patched matches the diff the current `DiffSelection` was derived from (e.g., compare a content hash/identity captured when the selection was created), and if they differ, abort the partial commit and force the user to re-review the updated diff rather than silently reapplying stale line indices.

### Proof of Concept
Conceptual reproduction (exact trigger requires a controllable race, which could not be fully verified without running the app):
1. Open a repository in Desktop, select a file with multiple hunks in the Changes view, and select specific lines/hunks to commit (building `DiffSelection` against diff snapshot A).
2. Before clicking "Commit," have the tracked file's content change on disk in a way that shifts line offsets but keeps the file still modified (e.g., another process appends/removes a line, or a repository-defined clean/smudge filter mutates it) — producing diff snapshot B with different `unifiedDiffStart` offsets.
3. Click "Commit." `applyPatchToIndex` calls `getWorkingDirectoryDiff` and gets snapshot B, then `formatPatch` applies the old `DiffSelection` indices (built for snapshot A) to snapshot B via `file.selection.isSelected(absoluteIndex)` at `app/src/lib/patch-formatter.ts:157`.
4. The resulting commit contains different lines than what the user visually selected, with no warning from Desktop.

Given the difficulty of independently reproducing the exact race without running the application, this should be validated by an engineer with a running Desktop instance/test harness before treating it as fully confirmed; the code-level inconsistency (two independent diff fetches feeding one index-based selection with no reconciliation) is verified directly in the cited files.

### Citations

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
