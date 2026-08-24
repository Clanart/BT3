## Finding

### Title
Stale/mismatched diff line indices between whitespace-hidden render diff and full commit-time patch diff can silently corrupt partial commits - ([File: app/src/lib/git/apply.ts])

### Summary
The Ajna bug is a case where a derived value (LUP) is computed once, a state mutation (kick penalty) is applied afterward, and the stale value is reused without recomputation, silently corrupting the pool's accounting. The Desktop analog is structurally the same "compute-then-mutate-then-reuse-stale-value" pattern applied to partial-commit line selection: the diff a user reviews and selects lines against (which can hide whitespace-only hunks) is not the same diff used to actually build the `git apply` patch that mutates the index/commit, so the line-selection bitmap is applied to a differently-shaped diff at commit time.

### Finding Description
When the user has "Hide Whitespace Changes" enabled in the Changes view, the working-directory diff is rendered via `getWorkingDirectoryDiff(repository, file, this.hideWhitespaceInChangesDiff)` [1](#0-0)  using the `-w` git flag, which changes hunk boundaries/line counts relative to a non-whitespace-suppressed diff. The user then makes per-line partial selections (`DiffSelection`) whose indices (`absoluteIndex`, `unifiedDiffStart`/`unifiedDiffEnd`) are tied to that specific whitespace-hidden diff's hunk layout.

When the commit is actually created, `applyPatchToIndex` re-fetches the diff to build the patch that is applied with `git apply --cached`, but calls `getWorkingDirectoryDiff(repository, file)` with no `hideWhitespaceInDiff` argument, defaulting to `false`: [2](#0-1)  This freshly-fetched diff is passed straight into `formatPatch(file, diff)`, which walks the *new* diff's hunks and looks up selection state via `file.selection.isSelected(absoluteIndex)` computed against the *old* (whitespace-hidden) diff's line numbering: [3](#0-2) 

This is exactly analogous to `_kick()` in the Ajna report: the LUP consumed by `kick()`/`kickWithDeposit()` is calculated before the penalty mutates `poolState_.debt`, and the stale value is returned/used without recomputation. Here, the `DiffSelection` bitmap is calculated against one diff structure, a "recalculation" occurs (`getWorkingDirectoryDiff` is called again in `applyPatchToIndex`) that can yield a structurally different diff (different hunk splits/line offsets due to `-w`), and the stale selection indices are reused against the new hunk layout with no reconciliation.

### Impact Explanation
Because whitespace-only lines are invisible to the user in the review, but present as distinct diff lines in the non-`-w` diff used for the actual patch, the index shift between the two diffs means `isSelected(absoluteIndex)` can silently:
- include content the user never selected/reviewed, or
- exclude content the user explicitly selected,

in the commit that is created and potentially pushed. This is a "silent corruption of what the user commits or pushes" — the committed tree state does not match what the UI showed and the user approved, with no error or warning surfaced.

### Likelihood Explanation
This triggers any time a user (a) enables "Hide Whitespace Changes" for the Changes diff, (b) performs a partial (line/hunk) selection rather than "select all," on a file whose diff contains whitespace-only differences interleaved with substantive changes. No special privileges are required, and the divergent behavior is deterministic given those repo/file contents (whitespace differences are trivially present in real-world files, e.g. mixed line endings, trailing whitespace, or hand-crafted content in a repository the user has cloned).

### Recommendation
`applyPatchToIndex` should never silently refetch a diff that can structurally diverge from the diff the caller's `DiffSelection` was built against. Either:
- Pass through the same `hideWhitespaceInDiff` value used when the selection was created (or better, always use the full, non-whitespace-suppressed diff consistently for both rendering and selection index computation, disabling "hide whitespace" for the purposes of `DiffSelection` indices), or
- Have `stageFiles`/`applyPatchToIndex` accept and use the exact `IDiff` object the selection state was computed from rather than re-deriving a new one from disk, so there is a single source of truth for hunk/line layout between "what the user selected" and "what gets applied."

### Proof of Concept
1. Enable "Hide Whitespace Changes" in Desktop's Changes view.
2. Open/clone a repository containing a file with an interleaved whitespace-only change and a substantive change (e.g., a trailing-whitespace removal on one line followed by an intentional content edit on a nearby line).
3. In the Changes diff (whitespace hidden), select only the substantive change's lines for a partial commit, leaving other lines unselected.
4. Commit. Internally: `updateChangesWorkingDirectoryDiff` built the `DiffSelection` against the `-w` diff [1](#0-0) , while `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` regenerates the diff without `-w` [4](#0-3)  and applies `file.selection.isSelected(absoluteIndex)` against this new hunk layout in `formatPatch` [5](#0-4) .
5. Inspect the resulting commit: the staged/committed hunks can differ from what was visually selected (extra whitespace-only lines committed, or intended lines dropped), depending on how the `-w` collapsing shifted hunk boundaries relative to the full diff.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/git/apply.ts (L52-62)
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
```

**File:** app/src/lib/patch-formatter.ts (L129-171)
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

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
```
