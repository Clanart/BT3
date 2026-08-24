Based on the investigation, I found a plausible Desktop analog to the "late users suffer more loss due to stale distorted state" pattern: stale, index-based diff-selection state being silently reapplied against a changed diff at commit time.

### Title
Stale index-based partial-commit line selections can be silently reapplied to a changed diff, causing wrong content to be staged/committed - (File: `app/src/lib/stores/updates/changes-state.ts`)

### Summary
`DiffSelection` tracks which lines of a file to include in a commit purely by **line index** (`Set<number>` of unified-diff line numbers), with no binding to the actual line content. When the working directory status is refreshed, `updateChangedFiles` blindly carries the previous `DiffSelection` forward onto the new `WorkingDirectoryFileChange` object as long as the file `id` (essentially path/status) matches, without re-validating that the diff content behind those indices is unchanged.

### Finding Description
On every status refresh, `updateChangedFiles` merges old and new file lists by `id` and reuses the *old* selection object verbatim unless `clearPartialState` forces a full reset for `Partial` selections: [1](#0-0) 

The only place that attempts to reconcile a stale selection against a new diff is `_loadChangedFilesForCurrentSelection`/file-selection diff loading path, and even there the code explicitly acknowledges the diff can change dramatically and only prunes indices that are no longer "includeable" lines — it does not verify that a still-includeable index at the same position still corresponds to the same logical change: [2](#0-1) 

This reconciliation only runs for the file currently open in the Changes view (`_changeFileSelection`), not for every file in the working directory. Any file that is not currently selected in the UI keeps its raw, unpruned, index-based `DiffSelection` from before, carried through `updateChangedFiles` unchanged.

At commit time, `_commitIncludedChanges` takes whatever `selection` is sitting on `state.changesState.workingDirectory.files` — regardless of whether it was ever revalidated against the file's current diff — and hands it straight to `createCommit`: [3](#0-2) 

`createCommit` → `stageFiles` routes any file with a `Partial` selection to `applyPatchToIndex`, which is responsible for turning the abstract line-index selection into an actual patch applied to the git index: [4](#0-3) 

Because the selection carries only line indices and not content fingerprints, if the underlying file content in the working directory shifts between when the user made their partial selection and when they click "Commit" — e.g. due to a background fetch triggering a smudge/clean filter, an LFS pointer resolution, a submodule update, or any git hook/process that rewrites the file without changing its path — the same index set now maps to different, unrelated lines of the new diff. `stageFiles`/`applyPatchToIndex` will faithfully apply that mismatched selection, silently staging and committing content the user never intended to include (or omitting content they did intend to include).

### Impact Explanation
This breaks the invariant "what the user visually selected for inclusion is what gets committed." An attacker who controls repository content that gets exercised by a filter/hook/LFS smudge process (i.e., they control the cloned/fetched repository's `.gitattributes`, filters, or LFS objects) can cause a file's on-disk content to shift between selection and commit, causing the victim to silently commit/push different code than what they reviewed and selected in the diff view — a form of silent corruption of what the user commits, potentially smuggling in unwanted or malicious content that then gets pushed to a shared branch.

### Likelihood Explanation
Requires a background content change to occur between the user selecting specific lines and pressing Commit. GitHub Desktop's periodic background fetch, LFS smudge filters, and clean/smudge attribute filters are legitimate, unprompted mechanisms that can alter working-tree content without direct user action, so this does not require local/physical access, admin rights, or social engineering — only that the victim leaves a partial selection pending while background repo operations run, which is a normal Desktop usage pattern. I was not able to fully confirm from the index whether `applyPatchToIndex` (in `app/src/lib/git/apply.ts`) recomputes the diff fresh at commit time or reuses a cached hunk structure, which affects exact severity; this file's contents were not fully available in the index.

### Recommendation
Bind `DiffSelection` state to a content fingerprint (e.g. hash of the hunk's old/new line ranges) rather than raw indices, and invalidate/re-derive selections whenever the underlying diff for a file changes, not just for the currently displayed file — extend the pruning logic in `app-store.ts` (currently scoped to `_loadChangedFilesForCurrentSelection`) to run for every file in `updateChangedFiles`, and re-diff/validate selections for all `Partial`-selection files immediately before `_commitIncludedChanges` calls `createCommit`.

### Proof of Concept
1. Modify a tracked file with several separate hunks.
2. In Desktop's Changes view, partially select specific lines in hunk #2 for commit, leaving the file open unselected in the UI (switch focus away, e.g. to another file) so it is no longer the "currently displayed" diff.
3. Trigger a background operation that rewrites the file's content without changing its path — e.g. a `.gitattributes` clean/smudge filter re-normalizing line endings, or an LFS pointer resolving to different content, or a fetch that runs a `post-merge`/`post-checkout` hook touching the file.
4. Because `updateChangedFiles` (`changes-state.ts:43-56`) merges the new file status with the *old* selection object unconditionally (no `clearPartialState` reset for this refresh path), the stale line-index selection is preserved on the updated `WorkingDirectoryFileChange`.
5. Commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` applies the stale indices against the now-different diff, staging/committing lines that do not correspond to what the user originally selected.

Due to the size limits on the indexed codebase, I could not inspect the full implementation of `app/src/lib/git/apply.ts` (`applyPatchToIndex`) to confirm exactly how it reconstructs the patch from `DiffSelection` at commit time; a Devin session with full repository access would be needed to verify the precise mechanics of the patch construction and confirm the end-to-end exploitability of this path.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L43-56)
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
```

**File:** app/src/lib/stores/app-store.ts (L3479-3493)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L3686-3699)
```typescript
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
