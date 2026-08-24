## Confirmed data flow

`applyPatchToIndex()` re-fetches the working directory diff at *commit time* rather than using the diff the user reviewed when they made a partial line selection: [1](#0-0) 

```
const diff = await getWorkingDirectoryDiff(repository, file)
```

That freshly-fetched `diff` (new hunks, new `unifiedDiffStart` offsets) is then combined with `file.selection`, which stores *only absolute line indices* chosen against the diff that was on screen when the user clicked checkboxes: [2](#0-1) 

The selection object has no notion of line content or hunk identity — it is a pure index/offset structure (`withRangeSelection`, `isSelected(absoluteIndex)`), and its own comment in `app-store.ts` admits this is a known-weak invariant: [3](#0-2) 

### Title
Stale line-index diff selection re-applied to a freshly-fetched diff at commit time can silently include/exclude unreviewed content - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user makes a **partial** (line-level) selection on a file's diff and then commits, Desktop does not commit against the diff the user actually reviewed. Instead, `applyPatchToIndex()` re-runs `getWorkingDirectoryDiff()` immediately before staging [4](#0-3) , and applies the old `DiffSelection` (a set of absolute line indices) to this new diff via `formatPatch()` [5](#0-4) . `DiffSelection` tracks *positions*, not *content*, so if the underlying diff hunks shift between review and commit, the selected indices silently line up with different, unreviewed lines.

### Finding Description
The invariant being violated is the same class as the Beanstalk bug: a per-item selection/accounting structure (`file.selection`) is computed against one version of the data (the diff shown in the UI) but is later consumed against a different, re-derived version of that data (the diff fetched fresh at staging time) without re-validating that the two align. `app-store.ts` explicitly documents that only "selectability" of the same index is checked, not whether it's still the same logical line: [6](#0-5) 

This reconciliation only happens when the *currently selected/displayed* file's diff is reloaded through `updateChangesWorkingDirectoryDiff` — a completely separate code path from the one used at commit time. `applyPatchToIndex` performs **no reconciliation at all**; it just re-fetches the diff and reuses `file.selection` as-is [1](#0-0) .

An attacker who controls a cloned/fetched repository can arrange for the working tree content backing a tracked file to change between the moment Desktop renders a diff and the moment the user presses "Commit," e.g. via `.gitattributes` clean/smudge filters, `core.autocrlf`, or content normalized on checkout/merge that is re-materialized by a background git operation Desktop triggers (status refresh, LFS smudge, submodule hook) while the Changes view is open. Because hunk boundaries and `unifiedDiffStart` offsets are derived from the diff text itself, even a one-line shift changes which indices map to which lines.

### Impact Explanation
If hunk layout shifts between the reviewed diff and the re-fetched diff at staging time, `isSelected(absoluteIndex)` in `formatPatch()` will silently select/deselect lines the user never looked at [7](#0-6) . This is a silent corruption of what the user actually commits/pushes: content the user explicitly excluded could be staged and committed, or content they intended to include could be dropped, with no warning shown before the commit is created. Given this repo's classification of "silent corruption of what the user commits" as high-severity, this qualifies.

### Likelihood Explanation
Exploitation requires the attacker-controlled repository to cause the working tree/diff for a specific file to change (via filters/attributes/checkout side effects processed by git) in the narrow window between diff display and the user's commit action — a race that is plausible but not trivially deterministic from a remote attacker's perspective without local timing control. This keeps likelihood moderate rather than certain, but the code path itself (`applyPatchToIndex` blindly re-fetching and re-applying stale index-based selections) is a real, unguarded weakness independent of how the race is triggered.

### Recommendation
Before staging a partially-selected file, `applyPatchToIndex` should verify that the diff it just fetched is structurally equivalent (same hunk count/boundaries/content) to the diff the selection was computed against, or it should carry the diff alongside the selection through the commit pipeline instead of re-fetching it. If the diffs diverge, abort staging that file and surface a re-review prompt to the user rather than silently applying possibly-mismatched line selections.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file so it has multiple hunks.
2. In the Changes view, partially select only some lines/hunks for commit (leave others unchecked).
3. Before pressing Commit, cause the working copy content for that file to be rewritten with different hunk boundaries while keeping the same file selected (e.g., trigger a filter/attribute-driven rewrite, or externally edit the file to shift line numbers) so that `getWorkingDirectoryDiff` at commit time returns hunks with different offsets than what was displayed.
4. Click Commit. `applyPatchToIndex` re-fetches the diff [4](#0-3)  and `formatPatch` applies the old absolute-index selection to it [8](#0-7) ; inspect the resulting commit and observe that it does not match the lines the user actually checked in step 2.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
```
