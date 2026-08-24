### Title
Partial-commit patch is built against a freshly re-read working-directory diff while line selection indices come from a stale, previously-displayed diff, silently staging/committing unintended hunks - (File: `app/src/lib/git/apply.ts`)

### Summary
The Tapioca finding is a case of a security-relevant calculation (`utilization`) combining a value that has been freshly updated (`_totalBorrow.elastic` post-accrual) with a value derived from an earlier, un-refreshed snapshot (`fullAssetAmount`), producing a silently wrong result used to drive a downstream decision. The equivalent broken-invariant class exists in GitHub Desktop's partial-commit ("stage selected lines") pipeline: `applyPatchToIndex` re-fetches a brand-new working-directory diff at apply time and applies the user's previously captured line-selection indices to it, without re-validating that those indices still correspond to the same content the user saw when selecting them.

### Finding Description
`applyPatchToIndex` in `app/src/lib/git/apply.ts` fetches a *fresh* diff off disk immediately before building the patch: [1](#0-0) . It then hands that fresh diff, plus `file.selection` — the `DiffSelection` object carried on `WorkingDirectoryFileChange`, which was populated from indices computed against whatever diff was rendered in the UI when the user made their selections — into `formatPatch`: [2](#0-1) .

`formatPatch` resolves which lines to include purely by absolute index (`hunk.unifiedDiffStart + lineIndex`) against `file.selection.isSelected(absoluteIndex)`: [3](#0-2) . There is no check that the *content* at that index in the newly-fetched diff matches the content the user was looking at when they selected it — only the index number is used.

The app-store code itself acknowledges this exact class of staleness when refreshing selection state after a diff reload, but only reconciles *selectable lines* (turning previously selected lines that no longer exist into unselected), not the actual patch application path used at commit time: [4](#0-3) 

Critically, that reconciliation happens in the UI/diff-viewer refresh flow (`updateChangesStashDiff`/diff loading), but `applyPatchToIndex` (invoked from `stageFiles` at actual commit time) performs its own independent `getWorkingDirectoryDiff` call and does not go through that same selectable-lines reconciliation: [5](#0-4) . If the working tree changes (new lines added/removed, hunks shifted) between the moment the user finishes selecting lines in the diff viewer and the moment `git commit`/`stage` actually runs `applyPatchToIndex`, the index positions the user selected can now refer to entirely different lines in the newly re-read diff, and `formatPatch` will silently include/exclude the wrong lines with no error — precisely the "value calculated from a stale/mismatched snapshot silently corrupts the final result" defect pattern in the report, transplanted from a Solidity interest-rate calculation to a git patch-selection calculation.

### Impact Explanation
This is a "silent corruption of what the user commits" bug class, one of the explicitly valid impact categories. A file that is attacker-influenced (e.g., a build tool, a pre-commit hook, a background formatter, or a malicious file watcher triggered by opening a cloned repository) can rewrite working-tree content between the time a user visually selects lines to stage and the time Desktop executes the commit, causing Desktop to stage/commit content the user never reviewed or intended, without any warning. Because commit message/diff review is the primary control users rely on before pushing, this can lead to unreviewed or attacker-influenced content being committed and subsequently pushed under the user's identity.

### Likelihood Explanation
Requires no elevated privileges: only that content in the working directory changes between diff-render time and commit-apply time — plausible in normal workflows (auto-formatters, linters, git hooks, file watchers, or a malicious dependency/build script in a cloned repository modifying tracked files). The window is the time between viewing/selecting a diff and clicking "Commit," which for larger diffs or slower reviewers can be non-trivial. No user needs to take unnatural steps; this can occur as a side effect of ordinary editor/tooling behavior.

### Recommendation
Before applying the patch in `applyPatchToIndex`, validate that the diff used to build the patch is the same diff (or at least line-content-equivalent) the selection was made against — e.g., compare a content hash/line signature per hunk, or re-derive the selection against the newly-fetched diff's line contents rather than raw indices, aborting or re-prompting the user if a mismatch is detected instead of silently applying index-based selection to unrelated content.

### Proof of Concept
Conceptual repro (cannot be executed here, but follows directly from the code path):
1. Open a repository in Desktop, modify a tracked file, and in the Changes view select only specific added/removed lines for a partial commit (this sets `file.selection` with absolute line indices tied to the diff currently rendered).
2. Before triggering commit, have another process (e.g., a pre-existing file watcher, linter-on-save, or build step) rewrite the same file, shifting or altering hunks so that the previously selected line indices now correspond to different content.
3. Trigger the commit. `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` (`app/src/lib/git/apply.ts:60`) and calls `formatPatch(file, diff)` using the *old* `file.selection` object against the *new* diff's indices (`app/src/lib/patch-formatter.ts:143-170`), producing a patch that stages lines the user did not actually review/select.

### Citations

**File:** app/src/lib/git/apply.ts (L60-60)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)
```

**File:** app/src/lib/git/apply.ts (L80-81)
```typescript
  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
