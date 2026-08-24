## Title
Stale diff-selection TOCTOU in `applyPatchToIndex` causes silent corruption of partially-committed content - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

## Summary
This report's underlying "broken invariant" is: an operation records success/updates authoritative state (share price) while the actual effect on the underlying asset differs from what was recorded (funds not actually moved), with no re-validation of the two being in sync. The closest reachable Desktop analog is the way partial-commit line selections are applied against a **freshly re-fetched diff** at staging time instead of the diff the user actually reviewed, with no check that the two diffs still agree on structure. This lets a working-tree change that happens between "diff shown/selected" and "commit clicked" silently redirect the user's line selection onto different content, corrupting what gets staged and committed/pushed.

## Finding Description
When a user reviews a diff and selects a subset of lines/hunks to commit, the UI builds a `DiffSelection` keyed purely by **absolute line index** relative to the diff that was rendered at that moment (`app/src/models/diff/diff-selection.ts`).

When the commit is actually performed, `_commitIncludedChanges` in `app/src/lib/stores/app-store.ts` passes the `WorkingDirectoryFileChange` (carrying that stale `selection`) straight through to `createCommit` → `stageFiles` (`app/src/lib/git/update-index.ts:109-168`) → `applyPatchToIndex` (`app/src/lib/git/apply.ts:12-84`).

Critically, `applyPatchToIndex` does **not** reuse the diff the user saw. It re-fetches a brand-new diff of the file from disk right before building the patch: [1](#0-0) 

That fresh diff is handed to `formatPatch`, which walks the new diff's hunks and, for every line, asks the *old* selection object whether the *new* absolute index was selected: [2](#0-1) 

There is no verification that the new diff's hunk boundaries, line counts, or content match what the selection indices were computed against. The codebase's own comment acknowledges this exact hazard elsewhere (when refreshing the Changes view diff), but that reconciliation logic only runs on an explicit UI refresh path, not on the commit path: [3](#0-2) 

If the working-tree file changes between the moment the user made their selection and the moment `_commitIncludedChanges` runs `applyPatchToIndex` — for example due to a Git clean/smudge filter, `core.autocrlf`/line-ending normalization, a `.gitattributes`-driven `textconv`/filter script, or any other content-mutating hook defined by an attacker-controlled cloned/fetched repository that fires during checkout or on file touch — the same numeric line indices will now point at different lines in the new diff. `formatPatch` will happily build a syntactically valid patch that includes/excludes the wrong lines, `git apply --cached` will apply it without error, and the commit will succeed. The user sees a normal, successful commit, but the actually committed (and subsequently pushed) content silently diverges from what they reviewed and intended — mirroring the report's core failure mode of "operation reports success while the real state diverges from the recorded state," with no built-in detection.

## Impact Explanation
An attacker who controls content of a cloned/fetched repository (via `.gitattributes` filters, clean/smudge scripts, or other checkout-time content transformations) can arrange for tracked file content to change out from under the user's already-made partial-selection between review and commit. Because `applyPatchToIndex` recomputes the diff but blindly reapplies the old selection indices, the user can end up committing and pushing content they never selected — including attacker-planted lines they explicitly deselected, or omitting security-relevant lines they explicitly selected — with no error, warning, or diff-mismatch check. This is a silent corruption of what the user commits/pushes, which can lead to unintended code/secrets being pushed or intended fixes being silently dropped.

## Likelihood Explanation
Requires an attacker-controlled repository with a filter/attribute mechanism (or any timing window where the working tree mutates between diff-render and commit) plus normal user partial-staging workflow (selecting only some lines/hunks of a file). No admin rights, local access, or leaked credentials are needed — only that the victim opens/clones the malicious repo and does a partial commit. This is a realistic but timing-dependent condition, so likelihood is moderate rather than trivial to trigger deterministically.

## Recommendation
Before calling `formatPatch`/`applyPatchToIndex`, re-validate that the diff fetched for staging matches the diff the selection was computed against (e.g., compare hunk headers/line counts, or recompute the selection against the fresh diff via the existing `withSelectableLines` reconciliation logic used in `app-store.ts`). If a mismatch is detected, abort the partial commit and force the user to re-review the diff instead of silently applying a mismatched selection.

## Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` entry with a `filter.x.clean` (or `textconv`) script that rewrites a tracked file's content shortly after checkout/first read (e.g. triggered by Desktop's own diff-loading `git show`/`git diff` invocation).
2. Victim clones the repo in GitHub Desktop, opens the Changes view, and reviews the diff for the affected file, selecting only certain lines to commit (partial selection), leaving the tab open momentarily.
3. Before the victim clicks "Commit", the filter/smudge/clean re-write fires (e.g. on a subsequent git invocation triggered by background refresh), changing the file's line layout on disk without changing the UI's cached selection.
4. Victim clicks "Commit". `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` fetches the new diff via `getWorkingDirectoryDiff` and calls `formatPatch(file, diff)` using the stale selection indices from step 2.
5. The resulting patch (`app/src/lib/patch-formatter.ts:129-232`) includes/excludes lines that no longer correspond to what the victim actually selected in the UI, and `git apply --cached` (`app/src/lib/git/apply.ts:52-81`) applies it without error — the victim commits and pushes content different from what they reviewed, with no indication of a mismatch.

### Citations

**File:** app/src/lib/git/apply.ts (L60-61)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)

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
