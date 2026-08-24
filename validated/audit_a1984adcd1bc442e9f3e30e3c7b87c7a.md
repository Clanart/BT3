## Title
Stale diff-line selection is used to build the partial-commit patch, allowing silent corruption of what the user commits - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user commit only a subset of the lines in a modified file ("partial commit"). The set of selected lines is stored as a bit-vector (`DiffSelection`) keyed by absolute line index into the diff that was rendered when the user made their selection. When the commit is actually executed, `applyPatchToIndex` re-fetches a brand new diff from disk and re-applies that same stale bit-vector to the new diff's line indices, without validating that the diff has not changed since the selection was made. If the tracked file's content changes between the time the user selects lines and the time the commit runs, the resulting patch can select the wrong lines (or fail to apply cleanly), silently changing what actually gets committed relative to what the user saw and intended.

### Finding Description
The partial-commit flow is:

1. The UI computes a diff and lets the user pick lines to include via `DiffSelection`, which records selection by `absoluteIndex = hunk.unifiedDiffStart + lineIndex` [1](#0-0) .
2. When the user hits commit, `_commitIncludedChanges` reads whatever `file.selection` is currently attached to the in-memory `WorkingDirectoryFileChange` objects and passes them straight to `createCommit` [2](#0-1) .
3. `createCommit` calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex` [3](#0-2) .
4. `applyPatchToIndex` fetches a **fresh** diff straight from the working directory at staging time (`getWorkingDirectoryDiff(repository, file)`), then builds the actual patch to `git apply --cached` from that fresh diff using the (potentially stale) `file.selection` [4](#0-3) .
5. `formatPatch` decides what to include per-line purely via `file.selection.isSelected(absoluteIndex)` on the newly-fetched diff's hunks [5](#0-4) , with no verification that this selection was actually derived from *this* diff.

Desktop does have a reconciliation path for the case where the diff is reloaded through the normal "selected diff" flow — `updateChangesWorkingDirectoryDiff` explicitly notes "The diff might have changed dramatically since last we loaded it" and recomputes `selectableLines` to drop selections on lines that no longer exist [6](#0-5) . However, that reconciliation only fires when the Changes-tab diff viewer is explicitly refreshed (e.g. on selection change or a full repository refresh); it is **not** invoked, and there is no equivalent check, at the moment `applyPatchToIndex` independently re-fetches the diff during the actual commit. This creates a TOCTOU (time-of-check/time-of-use) gap: the diff used to build the line-selection bitmap (check) and the diff used to build the actual git patch (use) can diverge, and nothing in `stageFiles` / `applyPatchToIndex` / `formatPatch` detects or blocks that divergence.

### Impact Explanation
If a tracked file is modified on disk between "user selects lines to commit" and "Desktop executes the commit" (a window of arbitrary length — the user can review a diff, type a commit message, and click Commit any time later), the absolute-index-based selection bitmap no longer corresponds 1:1 with the same source lines in the newly fetched diff. Depending on how the file drifted:
- `git apply` may fail outright (denial of the intended commit — an availability regression), or
- more critically, if the drift doesn't break the unified-diff context, unintended hunks/lines can be included or excluded, producing a commit whose content differs from what the user reviewed and approved in the UI, i.e. **silent corruption of what the user commits**, which can then be pushed to a shared remote.

This is realistic when the working tree is subject to another actor's write activity while GitHub Desktop is open, e.g. a repository-provided build/watch script, formatter-on-save tooling, or a git hook (`post-checkout`, `post-merge`, etc.) shipped inside a cloned/fetched repository that rewrites tracked files in the background. None of this requires local/physical access, admin rights, or prior malware — it only requires the user to open/interact with an attacker-authored repository that ships such tooling and to use Desktop's partial-line commit feature.

### Likelihood Explanation
The partial commit / line-selection feature is a core, commonly used part of Desktop's UX, and the gap exists on every partial-selection commit — it does not require a race with tight timing, since the window between line selection and clicking "Commit" is user-paced and can be seconds to minutes, ample time for a background process shipped by the repository to modify the file. The bug requires no explicit UI warning or unusual navigation; existing guards (`updateChangesWorkingDirectoryDiff`'s reconciliation) only cover the "diff panel refresh" path, not the "actual staging" path, so they do not stop this specific route.

### Recommendation
Before `applyPatchToIndex` builds and applies the patch, it should not silently trust the in-memory `file.selection`. At minimum:
- Compare a content hash/mtime or the newly-fetched diff's hunk structure against the diff that was used to build the current selection, and if they differ, abort the partial-commit for that file and force Desktop to refresh the diff/selection and require user reconfirmation rather than applying a bitmap computed against an old diff to a new diff.
- Alternatively, always drive `stageFiles`/`applyPatchToIndex` off of the same "check-then-use" diff object that produced the selection (compute the patch once, immediately before staging, from the exact diff object still in state, and re-validate that no refresh has invalidated it — mirroring the `stateAfterLoad`/`stateBeforeLoad` bail-out pattern already used elsewhere, e.g. in `updateChangesStashDiff` and `_changeFileSelection`) [7](#0-6) [8](#0-7) .

### Proof of Concept
1. In a repository opened in Desktop, modify a tracked file to have multiple independent hunks.
2. In the Changes view, select only specific lines within one hunk for the commit (partial selection), leaving the commit message box open (not yet clicked "Commit").
3. While the commit dialog is open, have a background process (e.g. a `post-checkout`/file-watcher script bundled in the repository, or manually for demonstration purposes) append/insert lines earlier in the same file, shifting subsequent line numbers, without going through the Desktop UI.
4. Click "Commit changes."
5. Observe: `applyPatchToIndex` calls `getWorkingDirectoryDiff` fresh [9](#0-8) , and `formatPatch` reuses the old absolute-index selection bitmap against this new diff [1](#0-0) . Either the `git apply --cached` step fails, or the committed content includes/excludes different lines than what the user actually selected in the UI, without any warning being shown to the user.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L2099-2107)
```typescript
    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const { shas: shasAfter } = stateAfterLoad.commitSelection
    // A whole bunch of things could have happened since we initiated the diff load
    if (
      shasAfter.length !== shas.length ||
      !shas.every((sha, i) => sha === shasAfter[i])
    ) {
      return
    }
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

**File:** app/src/lib/stores/app-store.ts (L3656-3668)
```typescript
    const diff = await getCommitDiff(repository, file, file.commitish)

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesStateAfterLoad = stateAfterLoad.changesState

    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3685-3699)
```typescript
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
