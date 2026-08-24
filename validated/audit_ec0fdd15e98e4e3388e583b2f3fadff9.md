### Title
Partial-commit line selection is applied against a freshly re-read working-directory diff without revalidating the selection is still positionally valid — silently committing the wrong hunks/lines - ([File: app/src/lib/git/apply.ts])

### Summary
The Bitcoin-Staking-Indexer report's root cause is a derived/cached decision (`isOverflow`) that is reused against new ground truth (a post-reorg chain) without being re-validated, so the stored answer silently diverges from what a fresh evaluation would produce. GitHub Desktop has a structurally identical pattern in its partial-commit ("stage part of a file") flow: a line-index-based `DiffSelection` computed from one diff snapshot is later applied against a *different, freshly regenerated* diff without a full positional re-validation, so `git apply --cached` can silently stage/commit different lines than the ones the user actually selected.

### Finding Description
When staging a partially-selected file, `stageFiles` calls `applyPatchToIndex`, which re-reads the diff from disk at commit time via `getWorkingDirectoryDiff(repository, file)` and then builds the patch from `file.selection` (a `DiffSelection` bit-set keyed by absolute line index) using `formatPatch`: [1](#0-0) 

`formatPatch` walks the *new* diff's hunks and asks `file.selection.isSelected(absoluteIndex)` for each line, based purely on numeric line-index bookkeeping (`hunk.unifiedDiffStart + lineIndex`): [2](#0-1) 

There is no check that the diff used to build `file.selection` and the diff now used to build the patch are the same diff/same hunk structure. The application's own code acknowledges this reconciliation is incomplete: when a diff is reloaded, Desktop only prunes selection bits that reference lines outside the new set of "selectable" lines — it does not verify that the *meaning* of each surviving index still matches the same logical line: [3](#0-2) 

So the invariant that is silently violated here is: *"the bit at index N in `file.selection` refers to the same source line it referred to when the user clicked it."* If the working tree content changes between the time the user makes line selections in the UI (against diff A) and the time `stageFiles`/`applyPatchToIndex` regenerates the diff for staging (diff B) — e.g. because the repository defines a `clean`/`smudge` filter, a checkout hook, LFS smudge, or any other git-native content-mutation mechanism triggered by opening/refreshing a maliciously crafted cloned/fetched repository — hunk boundaries and `unifiedDiffStart` offsets shift. The same absolute indices in `file.selection` now point at different, unrelated lines in diff B. `formatPatch` and `applyPatchToIndex` do not detect this: they proceed to build a patch and hand it to `git apply --cached`, which will apply it if the surrounding context still parses, silently staging/committing lines the user never selected (or omitting lines they did select) — exactly analogous to the Indexer reusing `storedStakingTx.IsOverflow` without re-validating it against the reorganized chain.

### Impact Explanation
This falls into the "silent corruption of what the user commits or pushes" category. A user who reviews and selects specific lines for a partial commit can end up committing different content than what they approved, without any error surfaced by Desktop. Because the commit is created and can subsequently be pushed, this can propagate unintended or attacker-influenced content changes into the user's history under their own authorship, with no indication anything went wrong.

### Likelihood Explanation
This requires the working-directory diff to change between the UI's diff/selection snapshot and the commit-time re-diff — a narrow timing window that depends on an external content-mutating mechanism (e.g., filter/smudge scripts, external tools, or hooks defined by a cloned/fetched repository) firing in that window. This is a real but non-trivial condition to trigger reliably, and Desktop's partial reconciliation (pruning out-of-range selections on reload) mitigates the most obvious cases (e.g., file shrinking). It is more likely to manifest as a subtle correctness bug in edge cases (e.g., filters that reorder/insert lines) rather than a broadly exploitable primitive, so I'd rate likelihood **Low-to-Medium** and impact **Medium-to-High**, contingent on reliably controlling a content-mutation trigger within the selection→apply window — which was not confirmed to be reachable through automated indexing alone.

### Recommendation
Bind `DiffSelection` to an identity/version of the diff it was computed against (e.g., a hash of the diff's hunks/content or the diff's `unifiedDiffStart`/line-content pairs), and have `applyPatchToIndex`/`formatPatch` refuse to apply a selection whose diff-identity does not match the diff generated at staging time — forcing a full re-diff and re-validated selection (or erroring out) instead of silently applying stale line indices, mirroring the recommendation in the source report to overwrite/recompute rather than reuse stale derived state.

### Proof of Concept
Conceptual PoC (not independently executed against the live app, since this required a filter/hook trigger outside the scope of static indexing):
1. Clone/open a malicious repository containing a `.gitattributes`-declared `clean`/`smudge` filter (or another content-mutating mechanism) on a tracked file.
2. In Desktop, open the Changes view for that file; the diff is computed once and the user selects specific lines for a partial commit.
3. Before the user commits, trigger the filter/mutation (e.g., via a background git operation Desktop itself performs, such as a status refresh that invokes the filter) so the file's diff structure/hunk offsets change.
4. Commit the partial selection. `applyPatchToIndex` re-reads the diff (now different) via `getWorkingDirectoryDiff`, and `formatPatch` applies the old absolute-index selection bit-set to the new hunks, producing a patch that stages different lines than the ones the user visually selected.

### Citations

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

**File:** app/src/lib/patch-formatter.ts (L129-161)
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
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`
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
