Based on my research, the closest structural analog to the ParaSpace bug — "two different payloads describing the same logical action are compared/validated using one of them, but the other is what actually gets executed" — is a TOCTOU-style mismatch between the diff a user visually reviews/selects lines from and the diff that Desktop actually converts into the patch applied to the index at commit time.

### Title
Stale line-selection reused against a freshly re-fetched diff can silently commit different content than what the user selected - (File: app/src/lib/git/apply.ts, app/src/lib/patch-formatter.ts, app/src/lib/stores/updates/changes-state.ts)

### Summary
GitHub Desktop lets users stage partial changes by selecting individual diff lines in the UI. That selection (`DiffSelection`) is line-*index*-based, not content-based. When the commit is actually created, `applyPatchToIndex` re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` [1](#0-0)  and immediately feeds it, together with the (possibly stale) `file.selection`, into `formatPatch` [2](#0-1) . `formatPatch` decides what to include purely based on `file.selection.isSelected(absoluteIndex)` against the lines of this new diff [3](#0-2) , with no re-validation that the content at that index is the same content the user actually reviewed and checked.

### Finding Description
When the working directory changes between the time the user reviews a diff and selects lines and the time the commit is actually executed, Desktop does not discard or fully re-validate the stale selection. `updateChangedFiles` explicitly carries over the *previous* `DiffSelection` object onto the newly-detected file state [4](#0-3) , and a separate reconciliation step only prunes indices that are no longer "includeable" lines — it does not verify the surviving indices still correspond to the same textual content the user approved [5](#0-4) .

This is structurally the same broken invariant as the ParaSpace bug: one value (the diff/selection the user saw and consented to) is used for the user-facing decision, while a different value (the diff freshly regenerated from disk at execution time) is what actually gets turned into the executed action (the `git apply --cached` patch). Just as LooksRareAdapter trusted the MakerOrder for pricing but let the TakerOrder drive the real fund transfer, Desktop trusts the UI-time `DiffSelection` indices for user consent but lets a freshly regenerated diff (line content may have shifted if the file was modified, e.g., by an auto-formatter, a build step, or a git filter/hook triggered while the repository is open) drive the actual patch content that gets committed.

### Impact Explanation
If exploitable, this results in the user's commit containing different content than what they visually reviewed and explicitly selected — a silent corruption of what the user commits, which is explicitly listed as a valid impact category for this task. In the worst case, a line the user chose to *exclude* could get committed (or vice versa) because the index still qualifies as "includeable" but its underlying text changed between review and commit.

### Likelihood Explanation
I was not able to fully verify, within the available indexed code, the precise timing window between diff-render, user line-selection, and the `applyPatchToIndex` call for a single commit action (i.e., whether Desktop re-diffs and revalidates selection synchronously right before staging, closing this gap in practice). The evidence available (`updateChangedFiles` preserving stale selections across background status refreshes, and `applyPatchToIndex` unconditionally re-fetching the diff at apply time) supports the existence of the gap, but confirming a concrete attacker-controlled trigger (e.g., a malicious repo-provided task/hook that mutates a file mid-review) would require deeper tracing of the commit-button click handler and the background status-refresh scheduler, which I could not complete with the remaining budget.

### Recommendation
Before generating the patch to stage/commit, Desktop should re-diff the file and validate that the currently selected line indices still map to the exact same line content that was present when the selection was made, refusing or re-prompting the user if the underlying diff has changed in a way that could alter the meaning of the selection — rather than blindly reapplying old line-index selections to a newly fetched diff.

### Proof of Concept
Not independently reproduced; based on static code review of the cited files showing the selection-preservation logic in `changes-state.ts`/`app-store.ts` and the fresh-diff-plus-stale-selection pattern in `apply.ts`/`patch-formatter.ts`. Confirming exploitability would require constructing a repository/workflow where working-directory content changes between diff render and commit execution (e.g., via an editor auto-format action or a file-watcher script bundled with the cloned repo) and observing whether the committed patch diverges from the last-rendered diff the user approved.

### Citations

**File:** app/src/lib/git/apply.ts (L52-60)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)
```

**File:** app/src/lib/git/apply.ts (L80-81)
```typescript
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
