### Title
Position-based (not content-anchored) partial commit selection allows externally-modified file content to be silently included/excluded from a commit - ([File: app/src/lib/patch-formatter.ts])

### Summary
GitHub Desktop's partial-staging feature lets a user select individual diff lines to include in a commit. The selection is stored as a set of **absolute line indices** (`DiffSelection`), computed against a diff snapshot that was rendered in the UI at some earlier point in time. When the user actually commits, `applyPatchToIndex` re-fetches a **fresh** diff straight from disk via `getWorkingDirectoryDiff` and then reapplies the old index-based selection to that fresh diff in `formatPatch`. There is no verification that the fresh diff's hunks/lines still correspond to what the user actually reviewed. If the working-tree file changes between the time the user made their line selections and the time of commit (e.g. because of a filter/formatter/build step that runs on files inside a freshly cloned/fetched repository), the positional selection silently maps onto different content, corrupting what gets committed without any user-visible warning.

### Finding Description
The partial-commit pipeline is:

1. UI renders a diff and lets the user select individual lines/hunks, recorded as absolute indices in `DiffSelection` (`app/src/models/diff/diff-selection.ts`).
2. `updateChangesWorkingDirectoryDiff` in `app-store.ts` explicitly acknowledges that the diff can drift and only patches up "selectable lines," not the actual mapping of previously selected content: [1](#0-0) 
   The comment states: *"The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."* — i.e. a known, accepted gap.
3. When the commit actually happens, `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` is invoked: [2](#0-1) 
4. `applyPatchToIndex` re-fetches the diff **fresh from disk** at commit time (not the diff that was shown to the user): [3](#0-2) 
5. `formatPatch` then walks the *new* diff's hunks and, for every line, calls `file.selection.isSelected(absoluteIndex)` — a purely positional lookup with no content/hash check that the line at that index is the same line the user actually reviewed and selected: [4](#0-3) 

Because the selection is keyed only by line index and not by content, any change to file content between diff-render time and commit time (insertions/removals shifting hunk boundaries) causes the positional selection to be silently reapplied to different content. The resulting `git apply --cached` patch (`app/src/lib/git/apply.ts` lines 52-83) is applied to the index without any diff-equality check against what the user actually reviewed.

### Impact Explanation
This breaks the invariant that "what the user selected to commit is what actually gets committed." An attacker who can cause the working-tree file to change between the diff render and the click on "Commit" (e.g. a build/watch/format tool that is part of the normal workflow of a freshly cloned/fetched repository, such as a linter-on-save, a bundler dev server, or any tool invoked from repository-provided npm scripts) can cause:
- Lines the user never reviewed or intended to commit (potentially attacker-authored/malicious code) to be silently folded into the commit as "selected," or
- Lines the user did intend to commit to be silently dropped/turned into no-ops.

This is a silent corruption of what the user commits and, subsequently, pushes to a shared remote — matching the "silent corruption of what the user commits or pushes" impact class. It does not require local malware, admin rights, or physical access; it only requires the normal, expected behavior of external tooling operating on the working directory of an already-cloned/fetched repository during the window between diff render and commit action.

### Likelihood Explanation
Likelihood is moderate. Exploitation requires a timing window where the working file changes between the last diff refresh and the commit click — this is plausible in real developer workflows (auto-formatters, file watchers, build systems triggered by files supplied in the repository) but is not deterministically triggerable by the attacker alone; it depends on coincidental or workflow-driven file mutation during that window. There is no explicit exploit primitive (e.g. remote-triggered timing) confirmed in the code beyond the acknowledged gap in `app-store.ts`, so I could not verify a fully deterministic, attacker-controlled trigger path using only the indexed code — this should be validated further with a live Devin session that can trace exact timing windows and any additional guards (e.g., stat/mtime checks) that may exist elsewhere in the diff-refresh/staging pipeline but were not found in the indexed portions of the codebase.

### Recommendation
Before generating/applying a partial-commit patch, verify that the diff fetched at commit time is structurally/content-identical (e.g., via hashing hunk content or the underlying blob OIDs) to the diff the user's selection was computed against. If a mismatch is detected, either recompute the selection against the new diff (mapping by content rather than absolute index) or refuse to commit and force the user to re-review the diff, rather than silently reapplying positional selections to changed content.

### Proof of Concept
Not independently reproducible from the indexed code alone (no local/execution environment available). Based on the code path traced:
1. Open a file with partial modifications in Desktop's Changes view; the diff is rendered and the user selects specific lines to include (`DiffSelection` records absolute indices).
2. Before clicking "Commit," an external process (e.g., a formatter/build tool run as part of the repository's normal dev workflow) modifies the same file, shifting line numbers/hunks without the UI being made aware in time.
3. Click "Commit." `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` re-fetches the diff fresh (`app/src/lib/git/apply.ts:60`) and calls `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`), which reapplies the stale index-based selection to the new hunks.
4. The resulting commit contains different content than what the user visually selected, with no warning shown.

This should be confirmed with an actual reproduction in a Devin session (able to run Desktop, modify files at the precise timing window, and inspect the resulting commit diff) since the indexed code review alone cannot execute the app to confirm the exact race window and rule out unseen guards.

### Citations

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

**File:** app/src/lib/git/apply.ts (L52-83)
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

  return Promise.resolve()
```

**File:** app/src/lib/patch-formatter.ts (L143-170)
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
```
