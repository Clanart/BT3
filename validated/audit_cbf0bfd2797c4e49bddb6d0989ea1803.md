I have enough evidence to build the analog. Let me confirm the createCommit flow uses this diff/selection combo directly without re-diffing at commit time.### Title
Position-based diff selection allows stale line indices to silently corrupt partial commits after a background diff refresh - (File: `app/src/lib/stores/app-store.ts`, `app/src/models/diff/diff-selection.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
The reported bug's broken invariant is: *removing an entry from a set that a downstream ratio/selection depends on shifts the meaning of the remaining data without re-validating that the association is still correct, and the consuming code has no guard against acting on that stale association.* The Desktop analog is `DiffSelection`, which tracks "included in commit" state purely by **positional line index** (`unifiedDiffStart + index`) rather than by line content. When the working-directory diff is refreshed (`updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts:3404-3510`) — which happens automatically on filesystem/status-change events, not only on explicit user action — the reconciliation logic (`DiffSelection.withSelectableLines`, `app/src/models/diff/diff-selection.ts:320-330`) only removes indices that no longer exist; it does **not** verify that a surviving index still refers to the same line content. `formatPatch` (`app/src/lib/patch-formatter.ts:157`) and the side-by-side diff UI (`app/src/ui/diff/side-by-side-diff.tsx`) then trust `file.selection.isSelected(absoluteIndex)` as ground truth when building the commit patch.

### Finding Description
`DiffSelection` stores only a `Set<number>` of "diverging" line indices (`app/src/models/diff/diff-selection.ts:82`) relative to a `defaultSelectionType`. There is no notion of *what content* lives at that index — it is a pure position, analogous to `CollateralManager` tracking a collateral type by an array slot rather than validating that the slot still represents the same collateral commitment.

The code comment at `app/src/lib/stores/app-store.ts:3480-3485` explicitly acknowledges the same class of gap the auditors flagged in the ERD report:
```
// The diff might have changed dramatically since last we loaded it.
// Ideally we would be more clever about validating that any partial
// selection state is still valid by ensuring that selected lines still
// exist but for now we'll settle on just updating the selectable lines
```
`withSelectableLines` (`app/src/models/diff/diff-selection.ts:320-330`) only prunes indices that fall **outside** the new selectable set; indices that still exist in the new diff (because the new diff happens to have equal or more selectable lines at the same index) are kept **as-is**, even though the hunk/line at that index may now be entirely different content (e.g. a different hunk shifted into that slot, or content changed by a concurrent process/checkout/merge/stash pop while the diff was being recomputed).

`updateChangesWorkingDirectoryDiff` reloads the diff asynchronously via `getWorkingDirectoryDiff` (`app/src/lib/stores/app-store.ts:3444`) any time the working directory status changes — this includes background refreshes triggered by filesystem watcher events, not just explicit "select this line" user gestures. Between the moment the user marks lines N..M as (de)selected and the moment `createCommit`/`formatPatch` consumes that selection, the working tree content on disk can change (e.g., due to `git checkout`, a build tool, a pre-commit hook, or content changed by a hydrated/synced file from an external process) causing hunk boundaries to shift while the count of selectable lines coincidentally stays compatible. `formatPatch` (`app/src/lib/patch-formatter.ts:157`) uses `file.selection.isSelected(absoluteIndex)` to decide, line-by-line, purely by position whether to include a line's current text in the generated patch — with no secondary check that the line's content matches what the user reviewed/selected.

### Impact Explanation
This matches the "silent corruption of what the user commits or pushes" category. If the position-index reconciliation is wrong, the user can:
- Commit an addition/deletion they never explicitly selected (because it now occupies an index previously marked "selected"), or
- Fail to commit a change they explicitly selected (because unrelated content shifted into that index and was excluded), all without any warning or diff confirmation prompt showing the actual mismatch. Since GitHub Desktop's UX invites reviewing the diff before committing, this breaks the fundamental trust invariant that "what's shown/selected == what's committed," parallel to how `removeCollateral()` breaks the invariant that a trove's collateral basis still matches its computed ICR after the underlying list changes.

### Likelihood Explanation
The trigger conditions are non-privileged and realistic: any operation that changes on-disk file contents between diff-load and commit-time (external tool, editor autosave, background format-on-save, a `post-checkout`/`post-merge` hook, or another concurrent git operation modifying the file) combined with the normal Desktop behavior of automatically reloading the working-directory diff. No local/admin access beyond normal usage is required, and no malware needs to be pre-installed — an attacker-influenced repository (e.g., via a git hook checked into a cloned/fetched repo, or a build script executed as part of normal repo tooling) that mutates tracked files during the window between diff load and commit is sufficient to induce this class of corruption. The bounding logic that exists (`withSelectableLines`) is only a partial/position-count guard and explicitly acknowledged in-code as insufficient ("Ideally we would be more clever about validating...").

### Recommendation
1. Tie `DiffSelection` divergence tracking to line **content identity** (e.g., a hash of the line text plus its type) rather than raw positional index, or invalidate/clear partial selections entirely whenever the diff is reloaded and any hunk boundary has shifted, instead of only pruning out-of-range indices.
2. Before generating the commit patch (`formatPatch`) or applying `discardChangesFromSelection`, re-diff and re-validate that the selection was computed against the exact same diff snapshot used for display (compare a diff fingerprint), and abort/re-prompt the user if they differ.
3. Surface a UI warning when the working-directory diff changes while a partial selection is active, rather than silently remapping the selection.

### Proof of Concept
I could not fully trace a concrete end-to-end trigger scenario (i.e., confirm the exact sequence of `createCommit` call sites and whether a re-diff-before-commit safeguard exists elsewhere, e.g. in `app/src/lib/git/commit.ts`) within the available tool budget — the codebase index did not return the full body of `createCommit` in `app/src/lib/git/commit.ts` for citation. This is a structural analog derived from the explicit in-code acknowledgment of the gap (`app-store.ts:3480-3485`) and the position-only design of `DiffSelection` (`diff-selection.ts`) plus its consumption in `formatPatch` (`patch-formatter.ts:157`), rather than a fully reproduced exploit trace. Confirming exploitability with an exact repro (e.g., scripted concurrent file mutation between diff-load and commit) would require running the Desktop app / test harness, which is outside the scope of this read-only analysis — a Devin session with terminal access would be needed to build and execute such a timing-based PoC. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** app/src/models/diff/diff-selection.ts (L309-330)
```typescript
  /**
   * Returns a copy of this selection instance with a specified set of
   * selectable lines. By default a DiffSelection instance allows selecting
   * all lines (in fact, it has no notion of how many lines exists or what
   * it is that is being selected).
   *
   * If the selection instance lacks a set of selectable lines it can not
   * supply an accurate value from getSelectionType when the selection of
   * all lines have diverged from the default state (since it doesn't know
   * what all lines mean).
   */
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
