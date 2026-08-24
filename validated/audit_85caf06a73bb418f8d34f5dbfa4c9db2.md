## Title
Stale partial-selection line indices applied against a freshly re-fetched diff during staging can silently commit different content than what the user selected - (`File: app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets users stage a *subset* of the lines/hunks in a changed file (“partial commit”). The line/hunk selection the user makes in the UI is captured as bit-indices (`DiffSelection`) against a specific `IDiff` object that was fetched when the Changes view rendered the file. When the user actually commits, `applyPatchToIndex` does **not** reuse that diff — it independently re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` and then reapplies the old selection's line indices to this new diff via `formatPatch`. If the working-tree content for that file differs at commit time from what it was when the selection was made (e.g. due to a `.gitattributes` clean/smudge or `core.autocrlf` filter that non-deterministically rewrites content on each checkout/diff pass, or an external process touching the file), the indices no longer point at the same logical lines, and the patch is applied with `--unidiff-zero` (zero lines of context), so `git apply` cannot detect the mismatch by context. This is the same class of bug as the reported Solidity issue: a cached value (the user's approved selection) is blindly reused after the underlying state it was derived from (the diff) has changed, and the guard that should prevent using stale state is missing at the point of the state-changing action.

### Finding Description
The staging pipeline is:
- `_commitIncludedChanges` in `app/src/lib/stores/app-store.ts` takes the currently selected `WorkingDirectoryFileChange[]` (with their `DiffSelection` computed against whatever diff was last shown in the UI) and passes them straight to `createCommit` → `stageFiles`. [1](#0-0) 
- `stageFiles` in `app/src/lib/git/update-index.ts` splits files into fully-selected and "partial" and calls `applyPatchToIndex` for every partially-selected file. [2](#0-1) 
- `applyPatchToIndex` fetches a **new** diff at this moment, independent from whatever diff the selection was computed against, then formats a patch using that new diff plus the old `file.selection` and applies it with `--unidiff-zero` (no context lines): [3](#0-2) 
- `formatPatch` decides which lines end up in the outgoing patch purely by calling `file.selection.isSelected(absoluteIndex)` where `absoluteIndex` is computed from the **freshly fetched** diff's hunk layout, not the diff the user actually looked at: [4](#0-3) 

Elsewhere in the app, the UI layer is aware that a diff can go stale between fetch and use and explicitly re-validates before acting on it — e.g. `updateChangesWorkingDirectoryDiff` discards a loaded diff if the selected file changed while it loaded, and `getRebaseSnapshot`/`performEffectsForRebaseStateChange` re-derive state rather than trusting a cached value: [5](#0-4) 
No equivalent check exists on the commit path: `applyPatchToIndex` never compares the diff it just fetched to the diff the selection was computed from, and `--unidiff-zero` removes the one safety net (context-line matching) that `git apply` would otherwise use to reject a patch that no longer lines up with the file.

### Impact Explanation
If the on-disk content of a file legitimately changes between the moment the user reviews/selects lines in the Changes view and the moment they press "Commit" (this window can be extended arbitrarily by leaving a partial selection pending), the final patch applied to the index can include or exclude the wrong lines relative to what the user saw and approved, with no error surfaced. Because commits are how source of truth is recorded and later pushed, this is a **silent corruption of what the user commits/pushes**: the user believes they excluded certain code (e.g. a debug secret, or an intentionally-reverted change) from the commit, but the stale-index application stages different content, or vice versa. A repository that ships a `.gitattributes` `filter`/`clean` rule (a versioned, repo-controlled mechanism) that produces different output on repeated invocations is a plausible trigger an attacker distributing a malicious/compromised repository could rely on to make this race far more likely to fire against unsuspecting contributors.

### Likelihood Explanation
This requires a timing window between diff-fetch-for-selection and diff-fetch-for-commit, which is realistic in normal usage (partial-commit workflows are a supported, documented Desktop feature, and users routinely leave a file partially selected while continuing to edit or while background tools/IDEs rewrite files). The `--unidiff-zero` flag actively removes the context-matching protection `git apply` would normally provide, so the bug is not merely theoretical: the patch will often apply "successfully" even against relocated line content, producing a divergent result without any warning to the user.

### Recommendation
- Capture and pass through the exact `IDiff` object the user's `DiffSelection` was computed against (instead of re-fetching a new one in `applyPatchToIndex`), and fail loudly (re-prompt the user to re-review the selection) if the working-tree file has changed since that diff was generated.
- Avoid `--unidiff-zero` for partial-commit patch application, or otherwise ensure enough context is included that a mismatched/staled hunk causes `git apply` to fail rather than silently apply against the wrong content.
- Before staging, compare a content hash/mtime of the file captured alongside the selection against the current on-disk state, and abort/refresh the selection UI if they differ.

### Proof of Concept
1. In a repository, modify `file.txt` with several hunks of changes.
2. In Desktop's Changes view, partially select a subset of lines/hunks (this computes `DiffSelection` against the diff fetched at that moment).
3. Before clicking "Commit", have an external process (or a repo-configured `.gitattributes` clean filter) rewrite `file.txt` so the line offsets of the existing hunks shift (e.g. insert/remove a blank line near the top of the file) without changing the file's status kind.
4. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:52-82`) fetches a fresh diff against the now-modified file and reuses the stale `DiffSelection` indices in `formatPatch` (`app/src/lib/patch-formatter.ts:143-171`), producing a `--unidiff-zero` patch that no longer corresponds to the reviewed selection; `git apply --cached` applies it without complaint because there is no context to validate against.
5. Inspect the resulting commit: it contains different line inclusions/exclusions than what was visually selected in step 2, with no error shown to the user.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3450-3464)
```typescript
    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3681-3696)
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
```

**File:** app/src/lib/git/update-index.ts (L109-169)
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
}
```

**File:** app/src/lib/git/apply.ts (L52-82)
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
