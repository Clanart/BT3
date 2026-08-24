### Title
Stale line-selection indices applied against a re-fetched working directory diff can silently commit the wrong lines - ([File: app/src/lib/git/apply.ts])

### Summary
The Merkl report's broken invariant is "the amount recorded/expected does not match what is actually available/received, and the code acts on the stale value without re-validating it." The closest reachable Desktop analog is in the partial-commit ("stage some lines") pipeline: `WorkingDirectoryFileChange.selection` stores an absolute-line-index bitmap that was computed against a diff object obtained at some earlier point in time, but `applyPatchToIndex` re-fetches a brand-new diff from disk (`getWorkingDirectoryDiff`) at staging time and blindly re-applies that old bitmap to the new hunk layout via `formatPatch`.

### Finding Description
`applyPatchToIndex` fetches the diff freshly right before building the patch: [1](#0-0) 
and then calls `formatPatch(file, diff)`, which walks the **newly fetched** `diff.hunks` and, for every line, asks `file.selection.isSelected(absoluteIndex)` — an absolute index computed from the **new** hunk's `unifiedDiffStart` offset: [2](#0-1) 

The `file.selection` bitmap, however, was built by the user (or by automatic "stage all" plumbing) against a diff that was loaded earlier, in `updateChangesWorkingDirectoryDiff`. That code path explicitly acknowledges that "the diff might have changed dramatically since last we loaded it" and only trims out indices that no longer correspond to includeable lines — it does **not** re-derive fresh indices that correctly map to the semantic lines the user actually clicked on: [3](#0-2) 

If the on-disk file content changes between the moment the diff/selection was established and the moment `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` runs (a plausible race in an attacker-influenced tree: a build script, watched file, git smudge/clean filter, checkout hook, or any file-system event triggered by content coming from a cloned/fetched repository), the hunk boundaries and `unifiedDiffStart` offsets shift. The stale bitmap of "selected absolute indices" then aligns with different, semantically unrelated lines in the new hunk structure. Because `formatPatch` only fails when the *entire* patch is empty, not when individual line selections have silently shifted, `git apply --cached` will happily accept a syntactically valid but semantically wrong patch — this is analogous to the Merkl bug where `amounts[i]` (the stale/expected value) is blindly reused instead of validating it against the actual current state, and the mismatch is only caught when the numbers are grossly wrong enough to revert (in Desktop's case, nothing reverts at all — the commit just contains different lines than the user selected).

### Impact Explanation
This can cause **silent corruption of what the user actually commits and later pushes**: lines the user did not intend to stage (e.g., unrelated changes, or lines that were deleted/added by the race) get included, while lines the user did select get dropped, with no error surfaced to the UI. This matches the explicitly in-scope impact category "silent corruption of what the user commits or pushes," and requires no local/admin access — only a content-mutation race triggerable by ordinary git tooling operating on a cloned/fetched repository (hooks, filters, file watchers), which is within the unprivileged attacker model defined by the task.

### Likelihood Explanation
Medium-to-low likelihood in the general case (requires a timing window between diff load and commit execution), but realistically triggerable whenever the working tree is being actively mutated by tooling associated with the repository (pre-commit hooks that reformat/lint files, clean/smudge filters, build watchers) while the user is reviewing/selecting diff lines in Desktop before pressing "Commit." Desktop does not re-verify the loaded diff against the actual file content used for `git apply` at commit time, so the guard in `updateChangesWorkingDirectoryDiff` (which only prunes now-invalid indices) does not prevent misaligned-but-still-valid indices from producing a wrong patch.

### Recommendation
Before calling `applyPatchToIndex`/`formatPatch`, re-fetch the diff and compare a content hash/timestamp of the file against what was used when the selection was computed; if it has changed, refuse to commit that file (or re-derive selection from stable line content rather than absolute positional indices), rather than silently reusing a positional bitmap against a new diff structure. Alternatively, base `DiffSelection` on line-content fingerprints instead of purely positional indices so a shifted hunk cannot silently remap selection to unrelated lines.

### Proof of Concept
1. Modify a tracked file, select only a subset of lines for partial commit in Desktop (populating `file.selection` with absolute indices from the currently loaded diff).
2. While Desktop is still showing that diff (before the user clicks "Commit"), have an external process (e.g., a git `pre-commit`/`post-checkout` hook, editor autosave, or filter script — any actor able to touch files in the working tree, which is realistic for cloned repositories with configured hooks/filters) rewrite the same file, shifting hunk boundaries/`unifiedDiffStart` without the renderer re-loading the diff in time.
3. User clicks "Commit." `createCommit` → `stageFiles` → `applyPatchToIndex` fetches a fresh diff (`app/src/lib/git/apply.ts:60`) and calls `formatPatch` (`app/src/lib/patch-formatter.ts:143-157`) with the stale `file.selection` bitmap.
4. The resulting patch applies successfully via `git apply --cached` but stages different lines than the user selected, producing a commit whose content silently diverges from user intent, confirmed by inspecting the resulting commit diff versus what was displayed/selected in the UI. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/git/apply.ts (L52-61)
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

**File:** app/src/lib/patch-formatter.ts (L129-157)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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
