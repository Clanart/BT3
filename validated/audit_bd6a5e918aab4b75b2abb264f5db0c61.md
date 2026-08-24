This confirms the vulnerability mechanism: `DiffSelection` tracks selected/deselected state purely by **line index position**, with no content hash or line-identity check. When a diff is reloaded (e.g., `updateChangesWorkingDirectoryDiff` in `app-store.ts`), the code only filters `divergingLines` down to indices still present in the new `selectableLines` set — it never verifies that the *content* at a given index is the same content the user originally selected.

### Title
Stale line-index-based `DiffSelection` causes silent corruption of partially committed content when working-directory file changes between diff render and commit - (File: `app/src/models/diff/diff-selection.ts`, `app/src/lib/stores/app-store.ts`)

### Summary
`DiffSelection` records which lines a user has selected/deselected for a partial commit purely by numeric line index [1](#0-0) . `updateChangesWorkingDirectoryDiff` reconciles a stale selection against a freshly-fetched diff by recomputing only the set of *selectable* indices and intersecting it with the previously diverging indices — it never re-validates that the line text at each index still matches what the user actually selected [2](#0-1) . `withSelectableLines` performs the same purely-index-based pruning [3](#0-2) . At commit time, `applyPatchToIndex` fetches a brand-new diff and calls `formatPatch`, which walks the new diff's hunks and, for each line, calls `file.selection.isSelected(absoluteIndex)` on the (potentially stale) selection object to decide whether to include that line in the generated patch [4](#0-3) [5](#0-4) .

### Finding Description
This mirrors the stNXM bug class: a tracking structure (`tokenIdToTranches` mapping tranche IDs to positions) is not properly re-synchronized after the underlying state changes, so a later read (`stakedNxm()`) operates on stale/misaligned data until an explicit reset. Here, the "tracking structure" is `DiffSelection`'s `divergingLines`/`selectableLines` sets, keyed by absolute line index into a specific diff snapshot. If the working-directory file content changes (lines added/removed, causing hunk boundaries and `unifiedDiffStart` offsets to shift) between the moment the user made their selection and the moment `applyPatchToIndex`/`formatPatch` regenerates the diff for staging, the index-based selection silently applies to the *new* diff's line-at-that-index, which is now different content than what the user selected.

The reconciliation logic in `updateChangesWorkingDirectoryDiff` only guards against *out-of-range* indices (lines that no longer exist as includeable lines) [2](#0-1) ; it does not detect that index N in the old diff and index N in the new diff represent different content. There is no content hash, line text comparison, or hunk anchor validation — exactly analogous to `extendDeposit()` failing to update `tokenIdToTranches[_tokenId]`, leaving stale index-based state to be consumed by a downstream read (`stakedNxm()` / `formatPatch`) as if it were still valid.

An attacker who controls a git remote, a fetched ref, a background process spawned by repository tooling (e.g., a build/watch script invoked as part of opening the repo), or timing around a `pull`/`fetch` that Desktop performs concurrently with the user reviewing a diff, could cause the working-directory content to shift lines at the exact moment the user has partially selected specific lines for a commit. Desktop would then generate and apply a patch whose line selection no longer corresponds to what the user saw and intended, silently including different content in the commit than what the UI displayed to the user just before they clicked "Commit".

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" — a listed valid impact. Because the corruption is silent (no error, no diff re-confirmation before the final `git apply`/`git commit`), a user could unknowingly commit and push unintended lines (e.g., reverting a security fix, re-including a line they deliberately excluded, or including content from a different version of a hunk) based on a diff snapshot that is no longer representative of disk state.

### Likelihood Explanation
The window is narrow (between diff load/selection and the click of "Commit"), and requires the working directory to change in a way that shifts line/hunk positions during that window (e.g., a concurrent fetch+checkout, a build tool triggered by opening the repo, or another process modifying tracked files) without the app's status-refresh cycle catching it first. This is a genuine TOCTOU race rather than a deterministic bug, similar in spirit to the temporary/self-correcting nature of the original stNXM report (it "self corrects" once `refreshChangesSection`/full status refresh runs), and is comparable in severity to the medium-severity classification of the original finding.

### Recommendation
Bind `DiffSelection` divergence tracking to line *content* (e.g., a hash of the line text) rather than purely positional index, or re-validate at `applyPatchToIndex`/`formatPatch` time that the line text at each selected index matches the line text captured when the selection was made, aborting/re-prompting the user if a mismatch is detected instead of silently regenerating the patch against a newer diff.

### Proof of Concept
Conceptual repro (cannot be fully executed without live app + timing control, but the code path is traceable):
1. Open a repository with a modified file containing multiple hunks; select only specific lines within hunk 2 via `DiffSelection.withRangeSelection` (as exercised in `app/test/unit/git/commit-test.ts:307-365`, which shows selection is purely index-based) [6](#0-5) .
2. Before clicking Commit, have an external process (or an attacker-influenced fetch/merge/checkout that Desktop triggers) insert/remove lines above the selected hunk in the same file, shifting `unifiedDiffStart` offsets without triggering a full selection re-derivation from content.
3. Click Commit — `applyPatchToIndex` re-fetches the diff fresh via `getWorkingDirectoryDiff` [7](#0-6)  and `formatPatch` applies the old `DiffSelection.isSelected(absoluteIndex)` against the new hunk layout [5](#0-4) , silently including/excluding different content than the user reviewed.

**Uncertainty**: I could not fully verify the exact timing feasibility (e.g., whether the app's `updateChangesWorkingDirectoryDiff`/status-poll cycle would reliably intercept and re-derive selection before a commit click in practice), since that depends on runtime scheduling not observable via static code search alone. This would need to be validated in a running Devin session with the actual app to confirm exploitability and precise race window.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
```

**File:** app/src/models/diff/diff-selection.ts (L320-330)
```typescript
  public withSelectableLines(selectableLines: Set<number>) {
    const divergingLines = this.divergingLines
      ? new Set([...this.divergingLines].filter(x => selectableLines.has(x)))
      : null

    return new DiffSelection(
      this.defaultSelectionType,
      divergingLines,
      selectableLines
    )
  }
```

**File:** app/src/lib/stores/app-store.ts (L3478-3499)
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
    const updatedFiles = changesState.workingDirectory.files.map(f =>
      f.id === selectedFile.id ? selectedFile : f
```

**File:** app/src/lib/git/apply.ts (L52-63)
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

**File:** app/test/unit/git/commit-test.ts (L307-332)
```typescript
    it('can commit multiple hunks from modified file', async t => {
      const testRepoPath = await setupFixtureRepository(t, 'repo-with-changes')
      const repository = new Repository(testRepoPath, -1, null, false)

      const previousTip = (await getCommits(repository, 'HEAD', 1))[0]

      const modifiedFile = 'modified-file.md'

      const unselectedFile = DiffSelection.fromInitialSelection(
        DiffSelectionType.None
      )
      const file = new WorkingDirectoryFileChange(
        modifiedFile,
        { kind: AppFileStatusKind.Modified },
        unselectedFile
      )

      const diff = await getTextDiff(repository, file)

      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.All
      ).withRangeSelection(
        diff.hunks[1].unifiedDiffStart,
        diff.hunks[1].unifiedDiffEnd - diff.hunks[1].unifiedDiffStart,
        false
      )
```
