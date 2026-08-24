### Title
Partial-Commit Line Selection Applied Against a Freshly Re-Fetched Diff — Silent Commit Content Mismatch - ([File: app/src/lib/git/apply.ts])

### Summary
The reported UniswapV3 issue is fundamentally a **stale-state / TOCTOU** bug class: an operation is authorized against one observed state (the position's tick range), but is actually executed against a different, attacker-shifted state, and the missing piece is silently filled in by the attacker. The GitHub Desktop analog is in the partial-commit ("stage selected lines") pipeline: the user's line selection is computed against one diff, but `applyPatchToIndex` re-fetches a brand-new diff at apply-time and blindly re-applies the user's selection indices to it, with no check that the diff is still the one the user reviewed.

### Finding Description
When a user stages only some lines/hunks of a file, the UI builds a `DiffSelection` bit-set keyed by absolute line index against a specific `ITextDiff` object that was rendered on screen. When the commit is actually created, `stageFiles` (`app/src/lib/git/update-index.ts:109-168`) calls `applyPatchToIndex` for every file with a partial selection.

`applyPatchToIndex` does **not** reuse the diff the user looked at. Instead it re-fetches the diff from disk at apply time: [1](#0-0) 

That freshly-fetched diff is then handed to `formatPatch`, which walks the new diff's hunks and consults `file.selection.isSelected(absoluteIndex)` — an index computed against the *old* diff: [2](#0-1) 

If the on-disk file content changes between the moment the diff was rendered to the user and the moment the commit is executed, the new diff's hunks/line ordering can differ from what the user saw, yet the same absolute indices are treated as "selected." There is no revalidation that the new diff still matches the diff the selection was built from — no hash/etag check on the diff content, unlike the (partial, best-effort) mitigation that exists only in the *Changes list refresh* path: [3](#0-2) 

That code comment explicitly acknowledges the underlying invariant is fragile ("The diff might have changed dramatically since last we loaded it... for now we'll settle on just updating the selectable lines") but this reconciliation only touches the *live selection model* shown in the UI — it is not consulted by `applyPatchToIndex`, which independently re-diffs the file right before generating the patch that is fed into `git apply --cached`.

**Attacker primitive**: any mechanism that lets untrusted content mutate the working-directory file between "user reviews and selects lines" and "Desktop stages/commits" satisfies the precondition — e.g. a `.gitattributes` clean/smudge filter, or any other repository-defined command that runs during status/diff refreshes and rewrites file content non-deterministically. Since Desktop calls `git diff` fresh right before formatting the patch (rather than diffing against the exact same bytes the user selection was built from), an attacker-controlled filter can cause the bytes actually staged to differ from what the user visually approved, without git or Desktop raising any warning.

### Impact Explanation
This falls squarely under "silent corruption of what the user commits or pushes." A user believes they are committing only the lines they explicitly selected in the diff viewer; the actual bytes written to the index/commit can differ because the line-selection bitmask is applied to a different diff snapshot than the one displayed. This could be used to smuggle unreviewed content into a commit (e.g., reintroducing a line the user explicitly deselected, or silently including additional changes) while the UI shows the user's expected, "safe" selection.

### Likelihood Explanation
The likelihood is moderate and requires a trigger for the working tree to change between diff render and stage/commit — this is most plausible via a repository-supplied git attribute filter (clean/smudge) or a background process the repo itself can provoke (e.g. through .gitattributes filters that Desktop invokes during status refresh cycles). It is not a one-click exploit and depends on timing windows in Desktop's refresh/commit flow, so it's a real but narrower analog than the original Uniswap bug (which was directly and reliably triggerable in a single sandwich transaction). I was not able to fully verify, within available tool budget, whether Desktop's commit flow re-runs a status/diff refresh (which could trigger clean/smudge filters) concretely between selection and `applyPatchToIndex`'s internal re-diff — this would need to be validated with a live repro to confirm exploitability end-to-end.

### Recommendation
`applyPatchToIndex` (and `stageFiles`) should not silently re-diff the file at apply time. Instead:
- Pass the exact `ITextDiff` (and/or its content hash) that the user's `DiffSelection` was built against into `applyPatchToIndex`, and fail/re-prompt if the on-disk content no longer matches that diff (similar to an optimistic-concurrency check).
- Alternatively, compute the patch to apply directly from the diff object already held in application state (the one rendered to the user) rather than calling `getWorkingDirectoryDiff` again inside `apply.ts`.

### Proof of Concept
1. Set up a repository with a `.gitattributes` clean/smudge filter (or any git hook/filter mechanism the repo defines) that rewrites a tracked file's content non-deterministically on checkout/status operations.
2. Modify the file locally so it has multiple hunks; open it in Desktop's Changes view and select only specific lines/hunks to stage (leaving others unstaged).
3. Trigger a background refresh (e.g., switch away and back, or let Desktop's periodic status/diff refresh run) so that the filter rewrites the working-directory content, shifting line offsets/content in a way that doesn't get resurfaced to the user as "diff changed, re-review your selection."
4. Commit. `applyPatchToIndex` re-fetches the diff (now reflecting the filter-mutated content) and applies the stale `DiffSelection` indices against it via `formatPatch`, producing a patch/commit whose actual content differs from what the user selected and saw in the UI.

(Note: step 3's exact trigger path inside Desktop's refresh cycle was not conclusively traced in this investigation due to tool budget — a hands-on repro is recommended to confirm the precise timing window.)

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

**File:** app/src/lib/patch-formatter.ts (L143-161)
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
