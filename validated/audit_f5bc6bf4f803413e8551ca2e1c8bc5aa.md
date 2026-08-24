## Analysis

The Swell bug's core pattern is: **a value computed once and trusted (`rateWhenCreated`, fixed at request time) is silently recomputed/overridden later using fresh state supplied by an untrusted caller, and the two values are then combined as if they still describe the same point in time**, corrupting the final payout.

The closest verifiable analog in this Desktop codebase is the way partial-commit staging recomputes the diff from disk instead of reusing the diff the user actually reviewed and selected lines against.

### Title
Partial-commit staging re-derives the diff from a fresh `git diff` instead of the one the user selected against, letting a repository-controlled content/line-ending change silently alter what gets staged - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user makes a partial-line selection in the Changes view, the selection (`DiffSelection`) records *absolute line indices* that only make sense relative to the specific `ITextDiff` object that was displayed. `applyPatchToIndex` does not reuse that diff object; it calls `getWorkingDirectoryDiff` again, fresh, immediately before formatting the patch. If the underlying working-directory content differs even slightly between the diff shown to the user and this second, silent diff (line-ending renormalization driven by the repository's own `.gitattributes`, an external tool touching the file, etc.), the hunk/line offsets shift while the previously computed `DiffSelection` indices stay the same, causing the wrong lines to be included or excluded from the generated patch.

### Finding Description
The diff a user reviews and clicks lines in is fetched once and cached in `changesState.selection.diff`, via `updateChangesWorkingDirectoryDiff`: [1](#0-0) 

The `DiffSelection` object built from that view only stores line indices (`withLineSelection`/`withRangeSelection`), with no reference back to the diff's content/hash that produced those indices - see how tests construct it purely from `hunk.unifiedDiffStart`/`unifiedDiffEnd` offsets: [2](#0-1) 

When the user commits, `stageFiles` calls `applyPatchToIndex` for every partially-selected file: [3](#0-2) 

Crucially, `applyPatchToIndex` does **not** reuse the diff object that produced the selection indices. It re-fetches the diff from disk right there, and hands the *old* selection to `formatPatch` against this *new* diff: [4](#0-3) 

`formatPatch` then walks the (new) diff's hunks and uses `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` is computed purely from the new diff's own hunk layout: [5](#0-4) 

If the two `git diff` invocations do not produce byte-identical hunk structures - e.g. because the cloned/attacker-authored repository ships a `.gitattributes` with `text=auto`/`eol=` normalization rules, or a clean/smudge filter that is invoked non-deterministically, or simply because the file was touched by another process between "show diff" and "commit" (a normal window in any GUI) - hunk boundaries and `unifiedDiffStart` offsets shift. The stale `DiffSelection` indices then point at different lines in the new hunk layout, so `isSelected()` returns selection decisions for the wrong lines. The resulting patch, generated purely from this mismatched pairing, is applied with `git apply --cached` and committed without any additional verification: no diff-of-diffs check, no hash comparison, no re-prompt to the user.

This mirrors the reported invariant break precisely: a trusted, "frozen at selection time" value (the diff/line mapping) is silently replaced by a freshly recomputed one, and the two are combined without validating they are still consistent - here the currency is *which lines end up in the commit* rather than a rate.

### Impact Explanation
This causes silent corruption of what the user commits: content the user explicitly deselected in the UI can be committed (and subsequently pushed) anyway, or content the user selected can be silently dropped, with the app reporting success and no warning. Since Desktop already emits a `lineEndingsChange`/`hasHiddenBidiChars` warning banner for exactly this class of repository-controlled content normalization, it confirms the underlying working-directory-vs-diff divergence is a reachable, repository-influenced condition — but that banner is purely informational for the *first* diff read and is never re-checked against the diff actually used to build the patch.

### Likelihood Explanation
Reaching this requires only normal usage: open a partial diff, select/deselect a subset of lines, and commit — the standard Desktop workflow. The divergence-triggering condition (repository-defined line-ending/`gitattributes` normalization, or a race with any other process touching the file, including editors/build watchers active while reviewing a freshly cloned/fetched repository) is entirely encoded in files the remote repository controls, requiring no special privileges or unnatural steps from the victim.

### Recommendation
`applyPatchToIndex` (and the equivalent `discardChangesFromSelection` path) should not silently refetch the diff. Either:
1. Thread the exact `ITextDiff` object the user's selection was computed against through to `stageFiles`/`applyPatchToIndex`, so the same hunk data used for `isSelected()` is guaranteed, or
2. Re-fetch the diff and compare its content/hash to the diff the selection was made against, refusing to stage (and surfacing an error/re-prompt) if they differ.

### Proof of Concept
1. Clone/open a repository whose tracked file content will be normalized differently between two successive `git diff` invocations for the same file (e.g., a repository shipping `.gitattributes` entries that interact with `core.autocrlf`/filters so that the second `git diff` call for the same path can legitimately produce a different hunk layout than the first).
2. In Desktop, select the file in Changes; the UI fetches diff #1 and renders hunks with certain `unifiedDiffStart` offsets.
3. Deselect a specific line (e.g., a line that leaks a secret or reverts a security check) using the line/range selection UI, producing a `DiffSelection` keyed to diff #1's offsets.
4. Before pressing commit, trigger the condition that changes the diff's hunk layout (external process modifies the file, or line-ending state changes) so a fresh `getWorkingDirectoryDiff` call now returns different hunk boundaries.
5. Click "Commit" — `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches diff #2, and `formatPatch` (`app/src/lib/patch-formatter.ts:143-171`) applies the stale selection indices to diff #2's hunks, producing a patch whose selected/deselected lines no longer match what was shown/clicked in step 3.
6. Verify via `git show HEAD` that the committed content differs from what the UI displayed as "selected" at commit time.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/test/unit/patch-formatter-test.ts (L65-77)
```typescript
      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.All
      ).withRangeSelection(
        second.unifiedDiffStart,
        second.unifiedDiffEnd - second.unifiedDiffStart,
        false
      )

      const updatedFile = new WorkingDirectoryFileChange(
        modifiedFile,
        { kind: AppFileStatusKind.Modified },
        selection
      )
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

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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
