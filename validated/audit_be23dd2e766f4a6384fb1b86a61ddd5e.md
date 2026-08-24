### Title
Purely index-based diff line selection allows stale selections to be silently re-applied to different (attacker-influenced) content when creating a partial commit - ([File: app/src/lib/stores/app-store.ts, app/src/lib/patch-formatter.ts])

### Summary
The invariant broken here is analogous to the Origami report: `DiffSelection` tracks *which line indices* the user selected for inclusion in a commit, but this tracked selection is never re-validated against the *actual current content* of the working-directory diff at commit time — only against whether the same index still exists and is still an "includeable" line type. If the file content changes between the time the diff is rendered/selected and the time the commit patch is built, the (stale) index-based selection is applied to (fresh) content, producing a commit that does not match what the user actually reviewed and approved.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts:53-136`) stores selection state purely as a `Set<number>` of line indices (`divergingLines`), with `isSelected(lineIndex)` doing a pure index lookup — it has no concept of line content or identity beyond position.

When the diff for the selected file is reloaded, `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts:3400-3510` explicitly documents this limitation: [1](#0-0) 

only pruning indices that no longer exist or turned into context lines, then reapplying the old `divergingLines` set to the new diff via `withSelectableLines`: [2](#0-1) 

At commit time, `applyPatchToIndex` (`app/src/lib/git/apply.ts:60-81`) fetches a **fresh** diff independent of whatever diff the user was shown, and `formatPatch` (`app/src/lib/patch-formatter.ts:129-206`) decides what to include using `file.selection.isSelected(absoluteIndex)` computed from that fresh diff's hunk layout: [3](#0-2) 

The chain of trust is: **tracked value** = set of selected line indices from an old diff render; **actual value** = current on-disk diff content at commit time. There is no content hash, no diff-identity check, and no re-verification that a surviving index still corresponds to the same textual change the user approved — only that it is still "selectable" (same line *type*, not same line *text*). This mirrors the Origami bug class precisely: a cached/tracked quantity (selection-by-index) is trusted as if it always matches the live underlying state (diff content), and the code comment even acknowledges this ("Ideally we would be more clever about validating that any partial selection state is still valid... but for now we'll settle on just updating the selectable lines").

### Impact Explanation
If the working tree content shifts between diff render and commit — e.g., because a repository ships a git filter/attribute (`clean`/`smudge`), a hook, or a build/watch script that a malicious or compromised repository triggers on checkout/fetch — a line that was an "addition" the user explicitly selected for inclusion can, at commit time, correspond by index to different content (e.g., a different addition, or content shifted due to hunk restructuring). Because Desktop reads `line.text` from the fresh diff at the selected `absoluteIndex` (patch-formatter.ts:157-171) without confirming it's the *same* change the user reviewed, the resulting commit can silently include or exclude content the user never intended — i.e., silent corruption of what the user commits, matching the "Valid Impact" criterion for corruption of committed/pushed content triggered by a git remote/repository-controlled input (hook/filter).

### Likelihood Explanation
This requires a specific race window: content must change between the diff being rendered to the user and the commit being executed, and the change must preserve line count/type such that pruning by "selectable lines" doesn't invalidate the stale indices. This is a narrower condition than a simple direct exploit, and typically needs some external process modifying tracked files concurrently with the review/commit UI flow (e.g., a repository-provided hook or filter script). It does not require local malware, admin rights, or leaked credentials — only a malicious/compromised repository the user has cloned and is actively working in — but it does require timing, making exploitation non-trivial and likelihood moderate/low rather than high.

### Recommendation
Tie `DiffSelection` to a content-derived identity rather than pure line index — e.g., include a hash or snapshot of the diff (or of each hunk's underlying blob SHAs) that the selection was computed against, and invalidate/re-prompt the user if `getWorkingDirectoryDiff` returns a diff whose underlying blob(s) differ from what was last displayed, instead of silently re-mapping indices via `withSelectableLines`. At minimum, `applyPatchToIndex`/`_commitIncludedChanges` should re-fetch and re-render the diff immediately before committing and require the selection to be validated against that exact diff instance, refusing to commit (with a clear error) if the working directory changed since the last selection was made.

### Proof of Concept
Conceptual PoC (exact reproduction requires controlling timing of a file mutation between diff load and commit, which could not be fully exercised in this read-only review):
1. Open a repository containing a modified file with two addition hunks; view the diff and select only Hunk B for partial commit (deselect Hunk A) in `app/src/ui/diff`.
2. Before clicking "Commit", have an external process (e.g., a repository-provided pre-commit-adjacent script, git hook, or filter triggered by Desktop's background fetch/checkout activity) rewrite the file such that the line count/types at the same absolute indices are preserved but the actual added text differs (e.g., swap which hunk contains which content, or inject new content at the same line positions).
3. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches the new diff, and `formatPatch` (`app/src/lib/patch-formatter.ts:157`) includes/excludes lines by the previously computed indices (`file.selection`), which no longer correspond to the content the user actually reviewed.
4. Inspect the resulting commit via `getChangedFiles`: it will contain content the user did not explicitly review/select, verifying the corruption described above.

This could not be verified end-to-end with a live repository in this session; the analysis is based on direct reading of `app/src/models/diff/diff-selection.ts`, `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`, and `app/src/lib/git/apply.ts`, and the explicit code comment acknowledging the limitation.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3492)
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
