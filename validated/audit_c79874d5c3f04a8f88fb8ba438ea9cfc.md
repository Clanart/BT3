No safeguards (content hash/diff comparison) exist confirming the selection's diff still matches the on-disk file when `applyPatchToIndex` re-fetches the diff at staging time. This confirms the TOCTOU pattern is unguarded.

### Title
Partial-commit line selection is applied against a re-fetched diff, causing silent staging of unintended lines - (File: app/src/lib/git/apply.ts)

### Summary
The Sherlock report's root cause is structurally simple: a value computed at "decision time" (`latestPositionLocal`) is stale relative to the value actually used at "execution time" (`currentPositionLocal`), so an index-based calculation silently uses the wrong underlying data. The same *pattern*—index/selection state computed against one version of data, then applied against a freshly re-fetched version of that data—exists in GitHub Desktop's partial-commit staging path.

### Finding Description
When a user stages only some lines of a modified file, GitHub Desktop stores that selection as a set of **line indices** (`DiffSelection.divergingLines`) relative to a specific diff object shown in the UI, via `WorkingDirectoryFileChange.selection` [1](#0-0) .

At commit time, `_commitIncludedChanges` snapshots the currently selected files (and their `.selection`) from the in-memory app state [2](#0-1) , then, after an `await formatCommitMessage(...)` gap, calls `createCommit` → `stageFiles` [3](#0-2) .

For files with a partial selection, `stageFiles` calls `applyPatchToIndex(repository, file)` [4](#0-3) . Critically, `applyPatchToIndex` does **not** reuse the diff object the user actually looked at when making the selection — it independently re-reads the file from disk via `getWorkingDirectoryDiff(repository, file)` [5](#0-4) , and then builds the patch by walking this *new* diff's hunks and testing `file.selection.isSelected(absoluteIndex)` for each line, where `absoluteIndex` is derived purely from position (`hunk.unifiedDiffStart + lineIndex`) [6](#0-5) .

There is no verification anywhere in this path (no hash/content/hunk-count comparison) that the diff fetched inside `applyPatchToIndex` is the same diff the selection indices were computed against. If the file's on-disk content or diff shape changes between the time the user made their partial selection and the time `applyPatchToIndex` re-reads the file — e.g., because a git hook (`post-checkout`, `pre-commit`), an `.gitattributes` smudge/clean filter, a background `fsmonitor`/watcher-triggered process, or any other file-system side effect from an attacker-controlled cloned repository rewrites the file in that window — the same numeric line indices in `divergingLines` will now point to entirely different lines in the new diff. `formatPatch`/`applyPatchToIndex` will then stage whatever lines happen to occupy those index positions, not the lines the user actually reviewed and selected.

### Impact Explanation
This causes silent corruption of what the user commits: content the user never reviewed or intended to include can be staged and committed (or conversely, content they intended to include can be silently dropped), because the index-based selection has no binding to the actual line content across the re-fetch. This falls squarely under "silent corruption of what the user commits or pushes," driven by an attacker-controlled repository (via committed hooks or `.gitattributes` filters that execute during normal Desktop operations like checkout/refresh) without requiring any unusual user action.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: it requires a window where the tracked file's content or diff hunk layout changes between the user's selection action (in the UI) and the actual `git apply --cached` call, and requires the attacker's repository to embed a mechanism (hook, smudge filter, or similar) that can be triggered by ordinary Desktop operations to rewrite tracked files at just the right time. Desktop does not appear to run arbitrary hooks automatically outside commit/checkout, so the primary realistic vector is a smudge/clean filter or a background refresh combined with an external file watcher process defined in the repo. This narrows exploitability but the underlying design flaw (unguarded index re-application against a freshly fetched diff) is real and unmitigated in the reviewed code.

### Recommendation
Bind the diff selection to the content it was computed against instead of relying purely on positional indices:
- Before staging, re-fetch the diff via `getWorkingDirectoryDiff` and compare it (e.g., by hash of `diff.text`/hunk structure) against the diff that was current when the selection was last computed; if they differ, abort/refresh the selection and warn the user rather than silently applying stale indices.
- Alternatively, recompute/re-validate `file.selection` against the newly fetched diff immediately before calling `formatPatch` in `applyPatchToIndex`, discarding indices that no longer correspond to the same line content.

### Proof of Concept
Conceptual sequence (not executed, based on code paths above):
1. User opens Desktop on a cloned malicious repository containing a tracked file `f` and a `.gitattributes` clean/smudge filter (or another mechanism triggered by ordinary Desktop refreshes) that rewrites `f`'s content on disk shortly after checkout/refresh.
2. User partially selects lines in `f` in the Changes view; the selection (`divergingLines`) is stored as line indices against the diff currently rendered.
3. Before the user clicks "Commit", the filter/hook rewrites `f`, changing its diff hunk layout (line counts/positions shift).
4. User commits. `_commitIncludedChanges` snapshots `file.selection` [2](#0-1) , then `applyPatchToIndex` re-fetches the diff fresh from disk and applies the old index-based selection to it [5](#0-4) [6](#0-5) .
5. The resulting patch stages lines that the user never actually reviewed/selected, and the commit silently contains unintended content.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L231-245)
```typescript
  // Lower inclusive, upper exclusive. Same as substring
  public withRangeSelection(
    from: number,
    length: number,
    selected: boolean
  ): DiffSelection {
    const computedSelectionType = this.getSelectionType()
    const to = from + length

    // Nothing for us to do here. This state is when all lines are already
    // selected and we're being asked to select more or when no lines are
    // selected and we're being asked to unselect something.
    if (typeMatchesSelection(computedSelectionType, selected)) {
      return this
    }
```

**File:** app/src/lib/stores/app-store.ts (L3685-3689)
```typescript
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
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

**File:** app/src/lib/git/apply.ts (L58-61)
```typescript
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

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
