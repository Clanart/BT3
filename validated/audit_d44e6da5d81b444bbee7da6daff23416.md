## Analysis

The ERC777 report's core defect is: **a state-changing operation (`send`/`transfer`) consumed a value (token balance) that was computed at an earlier point in time and never re-validated/consolidated against the current true state before being used to authorize an irreversible action.**

The closest structural analog I found in GitHub Desktop is in the partial-commit ("Select individual lines") pipeline: the line-selection state a user builds while reviewing a diff is later replayed against a **freshly re-fetched diff** at commit time, with no check that the diff hasn't changed in between.

### Title
Partial-commit line selections are replayed against a freshly re-fetched diff without validating they still match, allowing silent corruption of committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages a *partial* selection of lines from a modified file (the "select individual lines to commit" feature), Desktop stores that selection as a set of **line indices** (`DiffSelection`) computed against the diff that was rendered in the UI. At actual commit time, `applyPatchToIndex` does not reuse or validate against that diff — it calls `getWorkingDirectoryDiff` a second time to get a brand-new diff, and then blindly applies the old line-index-based selection to the new diff's hunks via `formatPatch`. [1](#0-0) 

If the on-disk file content changes between when the user made their line selection and when `applyPatchToIndex` re-fetches the diff for staging, the numeric line indices no longer correspond to the lines the user actually reviewed and approved — `formatPatch` will apply `file.selection.isSelected(absoluteIndex)` to entirely different hunk content. [2](#0-1) 

### Finding Description
`_commitIncludedChanges` takes the currently cached `workingDirectory.files` (with each file's `selection` built from a previously-fetched diff) and passes them straight into `createCommit`, which calls `stageFiles`, which calls `applyPatchToIndex` for any partially-selected file: [3](#0-2) [4](#0-3) 

`applyPatchToIndex` re-derives the diff from disk at commit time rather than reusing the diff the user reviewed: [5](#0-4) 

Desktop is aware that diffs can change out from under a stale selection — but that awareness is only applied on the **display** path, not the **commit** path. `updateChangesWorkingDirectoryDiff` explicitly re-validates selectable/selected lines whenever the diff is reloaded for rendering in the Changes view: [6](#0-5) 

No equivalent re-validation exists in the `createCommit` → `stageFiles` → `applyPatchToIndex` path. The broken invariant is: *"a line-based partial selection is only meaningful relative to the exact diff it was computed against"* — this invariant is enforced for what the user **sees**, but not for what the user **commits**, exactly mirroring the ERC777 bug where balance-affecting operations bypassed the consolidation step that other paths already performed.

### Impact Explanation
If the underlying file's content is regenerated between the time the user selects specific lines and the time the commit executes (e.g., because the repository's own build/watch tooling — something an attacker-controlled repository's README/npm scripts can legitimately instruct the victim to run — regenerates a tracked file such as a lockfile or generated config while the user is reviewing/staging changes), Desktop will apply the user's old line indices to unrelated new hunk content. This can cause the commit (and any subsequent push) to silently include content the user never selected, or omit content the user thought they were committing — a silent corruption of what the user commits/pushes, without any warning shown to the user, since `formatPatch`/`applyPatchToIndex` has no diff-identity check.

### Likelihood Explanation
This requires the working-tree file to change between the diff render and the commit action — a window that exists on every partial commit due to the multiple `await` points between selection and `createCommit`. It does not require local/admin access or leaked credentials; it only requires an attacker-controlled repository whose normal, documented workflow causes a tracked file to be rewritten (build step, generator, formatter) while the user is in the middle of selecting lines to commit — a plausible but not fully "on rails" scenario, since it depends on timing and on the victim running repo-provided tooling concurrently with staging.

### Recommendation
Before applying a partial selection in `applyPatchToIndex`, compare a fingerprint (e.g., diff text/hash or hunk boundaries) of the diff the selection was computed against with the freshly-fetched diff; if they differ, abort the partial-stage operation for that file and force a re-render/re-selection, mirroring the safeguard already implemented in `updateChangesWorkingDirectoryDiff`.

### Proof of Concept
1. Attacker publishes/forks a repository with a tracked generated file (e.g. `config.generated.js`) and a documented `npm run watch` script that periodically rewrites that file's content deterministically differently.
2. Victim clones the repo in Desktop, runs the documented watch script, and edits the file, selecting only specific lines to commit in the Changes view (partial selection, `DiffSelection` built from diff at time T).
3. Before clicking "Commit", the watch script's regeneration cycle rewrites the file, shifting/altering hunks.
4. Victim clicks "Commit". `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` re-fetches the diff at time T' (`app/src/lib/git/apply.ts:60`) and applies the stale line-index selection from T via `formatPatch` (`app/src/lib/patch-formatter.ts:143-157`), staging/committing content that does not match what the user reviewed and approved at T.
5. The resulting commit (and any subsequent push) silently contains unintended content, with no error or warning surfaced.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3681-3711)
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
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
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
