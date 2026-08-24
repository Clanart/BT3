Based on my research, the strongest and most concrete analog to the "stale snapshot used after a state-changing side effect" bug class is the working-directory diff/selection race in the commit flow, which fits the "silent corruption of what the user commits" impact category.

### Title
Stale diff line-selection indices are applied to a freshly re-fetched diff during partial commit, causing silent corruption of committed content - ([File: app/src/lib/git/apply.ts])

### Summary
The reported bug's broken invariant is: a positional/index-based value (`sharesBefore`) is captured before a state-mutating step (fee deduction) and then reused after that step has invalidated it, causing the operation to act on the wrong quantity. GitHub Desktop has a structurally identical pattern in its partial-commit ("stage selected lines") pipeline: the user's line selection is stored as a set of **positional indices** into a diff that was rendered at selection time, but at commit time the diff is **re-fetched from disk** and the old positional selection is blindly re-applied to the new hunk structure without validating that the content at those indices still matches.

### Finding Description
When a user stages part of a file for commit, the selection is stored as index positions (`file.selection.isSelected(absoluteIndex)`) computed against a specific `ITextDiff` snapshot rendered in the UI [1](#0-0) .

At actual commit time, `applyPatchToIndex` does **not** reuse that diff snapshot — it re-fetches a brand-new diff from the working directory and then calls `formatPatch(file, diff)` with the *old* selection object against this *new* diff's hunk/line layout: [2](#0-1) 

`formatPatch` walks the new diff's hunks and, for each line, asks the stale selection object whether the *absolute index* is selected — with no verification that the line at that index is the same line the user actually clicked: [1](#0-0) 

Desktop is aware that diffs can go stale relative to selections — `updateChangesWorkingDirectoryDiff` explicitly reconciles `selectableLines` after a diff reload, but only to drop indices that no longer exist; it does not verify that indices which *do* still exist still refer to the same content: [3](#0-2) 

The commit path (`_commitIncludedChanges`) captures the file's selection state early, then does async work (`formatCommitMessage`, hook hand-off) before calling `createCommit` → `stageFiles` → `applyPatchToIndex`, all of which re-diffs against the then-current disk contents: [4](#0-3) [5](#0-4) 

This is the same class of bug as the report: a value computed and "frozen" before a state-changing event (file content changing on disk between selection and commit) is reused afterward as if it still reflected current state, with no guard rejecting the mismatch — only a partial best-effort reconciliation of index bounds, not content identity.

### Impact Explanation
If the working tree file changes between the moment the user visually selects lines to stage and the moment `applyPatchToIndex` regenerates the diff (e.g., because a build tool, linter/formatter, git hook, LFS smudge filter, or another background process rewrites the file), the positional selection can now point at *different* lines than what the user actually reviewed and intended to commit. The resulting patch, generated via `git apply --cached`, can silently include unintended additions/deletions or omit intended ones. This directly matches the "silent corruption of what the user commits" impact category, since the user believes they are committing exactly the lines they selected in the diff viewer, but the actual staged/committed content can diverge without any warning or error.

### Likelihood Explanation
Triggering requires a source of file mutation between diff-render time and commit-apply time that is plausible without local malware/admin access — e.g., a repository-configured pre-commit/smudge/clean filter or formatter hook that runs automatically during Desktop's commit flow and rewrites tracked file contents, or another process (editor autosave, file watcher/build tool) modifying the file while the Changes view diff is stale. Because Desktop does not gate `applyPatchToIndex` on the diff being identical to what was shown to the user, no explicit confirmation step exists to catch this divergence — the only mitigation (`updateChangesWorkingDirectoryDiff`'s selectable-line pruning) only prevents crashes from out-of-range indices, not semantic mismatches, so the corrupted-commit path is not actually blocked by any existing guard.

### Recommendation
Before staging partial selections, re-validate that the diff used to build the patch is byte-identical (or at least hunk/line-content identical) to the diff the selection was made against; if it has changed, either re-diff and remap the selection state to matching content, or abort the partial-stage operation and prompt the user to re-review the diff, analogous to how the fix for the reported bug reordered accounting to record shares/state only after all side effects are finalized.

### Proof of Concept
1. In Desktop, modify a tracked file and open the Changes view; select a subset of lines/hunks for a partial commit (`file.selection` now encodes indices against the currently-rendered diff).
2. Before clicking "Commit", cause the file on disk to change in a way that shifts line numbers but keeps the file "dirty" from git's perspective (e.g., a configured pre-commit formatter/hook, or an editor auto-format-on-save triggered by a keystroke) without Desktop re-rendering/refreshing the diff view for that selection.
3. Click "Commit". `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff (`getWorkingDirectoryDiff`) and applies the old `file.selection` indices to the new hunk layout via `formatPatch`.
4. Inspect the resulting commit: the staged/committed hunks correspond to different lines than what was highlighted/selected in the UI at click time, demonstrating silent corruption of the committed content with no error or warning surfaced to the user.

### Citations

**File:** app/src/lib/patch-formatter.ts (L143-161)
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

**File:** app/src/lib/git/update-index.ts (L109-168)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
