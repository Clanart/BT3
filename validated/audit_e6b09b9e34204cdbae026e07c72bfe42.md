## Analysis

The reentrancy report's core invariant is: *a value computed from mutable shared state can go stale during an `await`/external-call gap, and a caller that trusts the stale value causes an inconsistent, unreviewed state change.* Most of the async flows I found in `app-store.ts` (`_changeFileSelection`, `updateChangesWorkingDirectoryDiff`, `updateChangesStashDiff`) explicitly re-check state after the `await` and bail out if it changed — i.e., they implement the exact guard-pattern the report recommends. However, one flow does **not** re-validate before acting on stale data: partial-commit staging.

`applyPatchToIndex` re-fetches a **fresh** diff of the working-directory file at commit time and then reuses the `DiffSelection` (line indices) that the user chose against an **earlier** diff shown in the UI, without checking that the file's diff/hunks haven't changed in between: [1](#0-0) [2](#0-1) 

`formatPatch` blindly maps the file's `selection.isSelected(absoluteIndex)` bitset onto whatever hunks the newly-fetched diff contains: [3](#0-2) 

This is invoked from `stageFiles`, which is the function used to build the index right before `git commit`: [4](#0-3) 

### Title
Silent commit-content corruption via stale line-selection reuse against a freshly re-fetched diff - (File: `app/src/lib/git/apply.ts`)

### Summary
When committing a partially-selected file, Desktop re-fetches the working-directory diff inside `applyPatchToIndex` and reuses the user's previously-recorded line-index selection (`DiffSelection`) against that new diff. There is no check that the new diff's hunk layout still matches what the user reviewed. If file content changes between the moment the user made their line selection in the Changes view and the moment `stageFiles`/`applyPatchToIndex` runs, the line indices are silently reinterpreted against different content, and unreviewed lines can be committed (or reviewed lines silently dropped).

### Finding Description
`DiffSelection` records *inclusion state per absolute diff-line-index*, not per semantic content. The commit path performs two separate diff computations against the same file:
1. An earlier diff, shown in the UI, from which the user picks which lines/hunks to include (`app-store.ts` `updateChangesWorkingDirectoryDiff`, which does track staleness for UI display via before/after SHA comparisons of the *selection list*, but the guard only protects the *rendered* diff/selection view — not the eventual staged content).
2. A second, independent diff fetched inside `applyPatchToIndex` at actual commit time via `getWorkingDirectoryDiff(repository, file)` [5](#0-4) , whose hunks are fed straight into `formatPatch(file, diff)` together with the *old* `file.selection` object.

`formatPatch` walks the **new** diff's lines and asks `file.selection.isSelected(absoluteIndex)` [6](#0-5)  — it has no way to know that index `N` in this new diff no longer corresponds to the same line the user saw/selected. If content on disk shifts between hunks (e.g., a line is added/removed earlier in the file by a background process, editor autosave, file-system watcher, or a repository-side git filter/smudge/clean driver or hook triggered by checkout/merge activity from an attacker-controlled repository), the recomputed hunk boundaries and `unifiedDiffStart` offsets shift, and the same absolute index now points at different, unreviewed content. Because the index/list of "selected" line numbers is opaque integers with no content binding, git happily applies whatever text lands at those offsets — silently including content the user never approved, or excluding content the user intended to commit.

Existing UI-level guards (`updateChangesWorkingDirectoryDiff`, `_changeFileSelection`) only prevent *stale renders in the UI*; they compare selection-state-before vs. selection-state-after an `await` to avoid showing wrong diffs, but they do nothing to protect the actual commit-time re-diff/re-stage path in `apply.ts`/`update-index.ts`, which is a completely separate code path invoked later during `createCommit`.

### Impact Explanation
This breaks the fundamental commit-integrity guarantee that "what the user selected/reviewed is exactly what gets committed and pushed." A repository that can trigger asynchronous, disk-visible content changes on the tracked file between diff-render time and commit time (via checkout-time smudge/clean filters, hooks, or any watched external process acting on files from that repo) can cause Desktop to silently commit lines the user never reviewed or omit lines they intended to include — a silent corruption of committed/pushed content, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The race window (between UI diff render / user selection and the moment `createCommit` → `stageFiles` → `applyPatchToIndex` re-diffs the file) is real but requires the file content to actually change in that window through some mechanism outside direct user action — e.g., an active git smudge/clean filter, a build/watch process, or another background writer tied to the repository. It does not require local/physical access beyond normal repo use, but it does require a specific timing condition and an external content-mutation trigger, so likelihood is moderate rather than trivially reproducible on every commit.

### Recommendation
Bind the `DiffSelection` to the diff it was computed from (e.g., store a content hash or the diff object itself alongside the selection) and have `applyPatchToIndex`/`stageFiles` verify that the diff re-fetched at staging time is identical to the one the selection was derived from; if it differs, abort the partial-stage operation and force the UI to refresh the diff and require the user to re-confirm their selection before committing, rather than silently remapping indices onto new content.

### Proof of Concept
Not independently verified by execution — this is derived from static code-path analysis of `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, and `app/src/lib/git/update-index.ts`. Conceptual repro:
1. Open a repository containing a large tracked text file with a configured (locally-trusted) smudge/clean filter or a file that a background watcher process rewrites shortly after checkout.
2. In Desktop's Changes view, select only specific lines/hunks of that file for the commit (partial selection), leaving the rest unselected.
3. Trigger the filter/watcher to rewrite earlier lines of the file (shifting subsequent line offsets) in the brief window before the user clicks "Commit."
4. Click Commit; `applyPatchToIndex` re-fetches the diff and applies `file.selection` against the new hunk offsets via `formatPatch`, producing a patch that includes/excludes different content than what was shown and approved in the UI, which is then committed with `git apply --cached`.

I was unable to fully trace the exact `_performCommit`/dispatcher call chain from UI click to `createCommit` due to running out of tool iterations, so the precise timing window (how long it stays open, and whether any last-instant `getStatus` re-check exists elsewhere in `app-store.ts` before invoking `createCommit`) could not be confirmed from the index alone — a full Devin session with repo access would be needed to trace `_performCommit`/`commit.ts`'s `createCommit` call site end-to-end and confirm there is no last-moment diff/selection re-validation before staging.

### Citations

**File:** app/src/lib/git/apply.ts (L52-66)
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
```

**File:** app/src/lib/git/apply.ts (L80-83)
```typescript
  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })

  return Promise.resolve()
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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
