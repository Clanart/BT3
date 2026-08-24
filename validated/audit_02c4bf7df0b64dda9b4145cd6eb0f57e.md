### Title
Partial-commit line selection is validated against a stale diff, allowing silently wrong hunks to be staged - ([File: app/src/lib/git/apply.ts])

### Summary
The report's underlying pattern is that a value (interest) is derived incrementally, opportunistically, and only "as often as possible," so its accuracy silently drifts based on unrelated activity happening between two checkpoints. The closest analog in GitHub Desktop is the partial-commit ("stage selected lines") flow: the user's line/hunk selection is recorded as a set of *absolute line indices* against one diff snapshot, but is later re-applied against a **freshly re-fetched diff** at commit time, with no atomic guarantee that the two diffs describe the same content. If the working tree changes between the two, the selection silently maps to the wrong lines.

### Finding Description
When a user partially selects lines/hunks to commit, that selection is stored as a `DiffSelection` keyed by absolute line index within the diff that was rendered in the UI, computed in `updateChangesWorkingDirectoryDiff` [1](#0-0) .

At actual commit time, `stageFiles` routes any file with a `Partial` selection to `applyPatchToIndex` [2](#0-1) . Critically, `applyPatchToIndex` does **not** reuse the diff the user reviewed — it calls `getWorkingDirectoryDiff` again, fetching a brand-new diff from the current on-disk state, and then builds the patch by testing `file.selection.isSelected(absoluteIndex)` against that new diff's hunk/line structure: [3](#0-2) [4](#0-3) 

The broken invariant: `DiffSelection.isSelected(absoluteIndex)` is only meaningful relative to the exact diff/hunk layout it was computed against. If the file content changes between (a) the diff render that produced the selection and (b) the `getWorkingDirectoryDiff` call inside `applyPatchToIndex`, the hunk boundaries and `unifiedDiffStart` offsets shift, so the same absolute indices now point at different lines. There is no hash/content check tying the selection to the diff it was derived from — only a best-effort background refresh (`updateChangesWorkingDirectoryDiff`) that prunes indices no longer "includeable," which:
1. only runs when the background poll happens to fire before the click, and
2. only removes stale selections — it does not re-validate that a *still-valid* index in the new diff means the same line the user actually approved.

`_commitIncludedChanges` then takes whatever is currently in `state.changesState.workingDirectory.files` and passes it straight to `createCommit`/`stageFiles` without any final verification against a fresh diff at commit time: [5](#0-4) .

### Impact Explanation
This is a "silent corruption of what the user commits" scenario. A repository whose content the attacker can influence right after clone/fetch/checkout/merge (e.g., via a `post-checkout`/`post-merge`/build-tool side effect that rewrites a tracked file shortly after the user opens the diff) can cause Desktop to commit different lines than the ones the user visually selected and approved — without any warning, diff mismatch dialog, or commit-time re-confirmation. Since the discrepancy is silent, the user has no indication their approved partial commit doesn't match what was actually staged and pushed, mirroring exactly the "computed vs. theoretical value" mismatch called out in the source report, except here the corrupted value is the set of staged git-apply hunks rather than an interest rate.

### Likelihood Explanation
The vulnerable window (time between diff rendering/selection and the actual `git apply --cached` in `applyPatchToIndex`) is real and not bounded by any lock or content hash check, but exploiting it reliably requires the attacker to time a file mutation into a narrow race window between the user reviewing/selecting hunks and clicking Commit, and by a mechanism (hook, editor autosave, background tool) that isn't itself blocked by Desktop. This is plausible but not trivially reliable, and I was not able to find within the indexed code any explicit content-hash/staleness check that would definitively rule the race in or out at commit time (e.g., I could not verify whether `withIsCommitting` or any pre-commit re-diff step exists beyond what's shown above). This uncertainty should be resolved with a live/dynamic test rather than static reading alone.

### Recommendation
Before applying a partial patch in `applyPatchToIndex`, verify that the diff used to build `file.selection` still matches the working tree (e.g., by comparing the diff's underlying blob/hash or re-validating that the selection's absolute indices correspond to the exact same hunk content), and abort/re-prompt the user if a mismatch is detected instead of silently applying the possibly-mismatched selection.

### Proof of Concept
Not independently verified end-to-end in this session (no execution/dynamic environment available). Conceptual PoC based on code paths cited above:
1. Modify a tracked file, open it in Desktop's Changes view; Desktop computes a diff and the user selects only "hunk A" lines via `DiffSelection`.
2. Before clicking Commit, have an external process (e.g., a git hook or editor auto-format triggered on file save) alter the file so that hunk boundaries shift (add/remove lines earlier in the file) — this can be simulated by directly editing the file on disk between selection and commit.
3. Click Commit. `stageFiles` → `applyPatchToIndex` re-fetches the diff fresh and applies `file.selection.isSelected(absoluteIndex)` against the new hunk layout.
4. Inspect the resulting commit's diff (`git show`) and confirm it differs from what the UI displayed as selected before step 2, demonstrating silent mis-staging.

This PoC could not be executed in this ask-only session; it should be validated with an actual Desktop test harness (e.g., extending `app/test/unit/git/commit-test.ts`'s `createCommit partials` suite) to confirm the exact behavior before treating this as a confirmed exploit rather than a plausible analog.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3497)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

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

    const selectedFileID = changesState.selection.selectedFileIDs[0]

    if (selectedFileID !== selectedFileIdBeforeLoad) {
      return
    }

    const currentlySelectedFile =
      changesState.workingDirectory.findFileWithID(selectedFileID)
    if (currentlySelectedFile === null) {
      return
    }

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
