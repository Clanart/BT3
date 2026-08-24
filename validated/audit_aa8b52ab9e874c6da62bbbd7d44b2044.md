## Analog Found [1](#0-0) 

### Title
Partial-commit patch is built from a freshly re-diffed file but applied using a stale line-selection bitmap, silently staging the wrong lines - (File: `app/src/lib/git/apply.ts`)

### Summary
The Sherlock report's broken invariant is: a derived value (`params.quorumVotes`) is computed from state that is *later mutated* (`totalSupply()` before `sunsetCollection()` burns tokens), so the value used downstream no longer matches the state it's applied against. The same shape of bug exists in GitHub Desktop's partial-commit path: the user's line selection (a bitmap of "absolute diff line indices") is computed by the renderer against one snapshot of the working-directory diff, but `applyPatchToIndex` re-reads the file from disk and regenerates a brand-new diff (`getWorkingDirectoryDiff`) at commit time, then blindly indexes into that *new* diff using the *old* selection indices.

### Finding Description
When a user partially stages/commits a file, the UI computes an `ITextDiff` and lets the user pick specific lines via `DiffSelection`, which stores selected/unselected state keyed by `hunk.unifiedDiffStart + lineIndex` — i.e., positional indices into a specific diff snapshot [2](#0-1) .

That `WorkingDirectoryFileChange` (path + status + selection bitmap) is captured in `_commitIncludedChanges` from `state.changesState.workingDirectory.files` and passed down to `createCommit` → `stageFiles` → `applyPatchToIndex` [3](#0-2) , [4](#0-3) .

Crucially, `applyPatchToIndex` does **not** reuse the diff the user reviewed. It re-invokes `getWorkingDirectoryDiff(repository, file)` to fetch a fresh diff straight from disk right before formatting the patch:
```ts
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
``` [5](#0-4) 

`formatPatch` then walks the *new* diff's hunks and consults `file.selection.isSelected(absoluteIndex)` — an index that was only ever validated against the *old* diff snapshot [2](#0-1) . If the on-disk content of the tracked file changes between the moment the diff was rendered/selected in the UI and the moment `applyPatchToIndex` re-diffs it (e.g. line-ending normalization from a `.gitattributes`/`clean` filter, a slow git process, a build tool, or any other process writing to a tracked file in that window — including content delivered via a just-fetched/checked-out branch), the hunk boundaries and line ordering shift. The stale bitmap then silently maps onto different lines of the new diff: additions the user *never selected* can be included, deletions the user *did* select can be dropped or shifted, all without any hash/consistency check that the diff being staged is the one the user actually reviewed.

This directly mirrors the seed's root cause: `params.quorumVotes` is captured from `totalSupply()` and then applied *after* `sunsetCollection()` mutates that same supply, producing an inconsistency between the value and the state it's used against — a mismatch never re-validated by the code before it drives an unrecoverable financial/state operation.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A user can end up committing lines they explicitly deselected, or omitting lines they selected, without any warning, error, or diff re-confirmation. Because Desktop stages via `git apply --cached` directly against the newly computed patch, the resulting commit's content silently diverges from what the UI showed and what the user intended to commit — this can leak content the user meant to keep out of a commit (e.g. partially-redacted secrets/credentials in a hunk) or drop safety-relevant lines the user meant to keep.

### Likelihood Explanation
The window between rendering a diff/selection and executing `applyPatchToIndex` is real and not vanishingly small: it spans the round trip from “Commit” click through `formatCommitMessage`, hook execution and `stageFiles` iterating over potentially many files sequentially awaiting `git` subprocess calls [6](#0-5) . Anything that mutates a tracked file on disk during that window (background build/watch process, editor autosave, a `clean`/`smudge` filter triggered by another concurrent git invocation, etc.) is sufficient to desynchronize the old selection indices from the newly fetched diff — no local/admin access or malware is required, only a normal concurrently-running process touching the working tree, which is a common scenario for developers.

### Recommendation
`applyPatchToIndex` should not silently re-diff and reuse a selection computed for a different diff snapshot. Either:
- Pass the exact `ITextDiff` the user's selection was computed against all the way through `createCommit`/`stageFiles`/`applyPatchToIndex` instead of re-fetching it from disk, or
- If re-fetching is required, compare a content hash/blob id of the file at selection-time vs. commit-time and abort/re-prompt the user if they differ, ensuring `formatPatch` only ever indexes into the diff the selection was actually derived from.

### Proof of Concept
1. Modify a tracked file with several changed lines; open it in Desktop's Changes view so a diff with multiple hunks is rendered.
2. Deselect specific lines (e.g., uncheck a sensitive line in hunk 2) to stage only part of the file.
3. Before pressing "Commit," have another process (e.g., a running formatter/watcher, or simply `git stash`+`git stash pop`, or another concurrent `git` operation invoking a clean filter) alter the file's line layout on disk without changing what's displayed yet.
4. Click "Commit." `applyPatchToIndex` re-diffs the now-altered file and applies the pre-change selection bitmap against the new hunk layout.
5. Inspect the resulting commit with `git show`: it does not match the lines the user actually selected/deselected in the UI, demonstrating the same "value computed pre-mutation, applied post-mutation" flaw as the seed report's `quorumVotes`/`sunsetCollection` ordering bug.

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

**File:** app/src/lib/stores/app-store.ts (L3684-3699)
```typescript
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

**File:** app/src/lib/git/update-index.ts (L150-168)
```typescript
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
