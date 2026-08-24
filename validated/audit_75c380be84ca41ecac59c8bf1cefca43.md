### Title
Stale index-based diff-selection bitmap can silently commit unreviewed lines when the working-directory diff changes between load and commit - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`updateChangesWorkingDirectoryDiff()` re-validates a partial commit selection against a freshly-loaded diff only by *pruning indices that no longer exist* (`withSelectableLines`), never by checking whether the *content* at a surviving index actually still matches what the user reviewed when they made the selection. The per-line "selected/deselected" bitmap is purely index-based (`DiffSelection.divergingLines`), so if the working-directory file changes (e.g. via a repository hook or background process that mutates a tracked file after checkout/merge) while a partial selection exists, previously-reviewed selection bits get silently re-applied to different line content at the same index, and `formatPatch()` will stage that unreviewed content into the commit/push without any additional confirmation.

### Finding Description
The diff-selection model tracks selection purely by absolute line index, not content identity: [1](#0-0) 

When the working directory diff is refreshed (triggered any time Desktop reloads status/diff for the currently selected file — after a background refresh, a file-watcher event, a hook running, etc.), the code explicitly acknowledges it does *not* verify the previously selected lines still represent the same content, it only prunes indices that no longer exist: [2](#0-1) 

The comment on lines 3480-3485 states this directly: *"The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."*

`withSelectableLines()` only filters the `divergingLines` set by whether the index is still "includeable" — it never compares old vs. new line text at that index: [3](#0-2) 

When the commit is finally created, `formatPatch()` builds the actual git patch by checking `file.selection.isSelected(absoluteIndex)` against the **current** diff's hunks — using the stale bitmap against whatever content now occupies that index: [4](#0-3) 

This is the structural analog of the PoolTogether bug: two representations of the same state (the user's reviewed intent vs. the underlying diff content) are supposed to stay in lock-step, but one side (the index bitmap) is updated/reused without re-deriving it from the authoritative source (line content), so a value computed from stale state (the "selected lines" set) is combined with fresh state (the new diff) to produce an inconsistent result — exactly like `directlyContributedReserve` needing to be tracked/reconciled whenever `_reserve` changes out from under it.

### Impact Explanation
If the working tree of a file is modified between the time Desktop loads/caches the diff and the time the user clicks "Commit" (e.g. a malicious `post-checkout`/`post-merge` hook shipped in a cloned/fetched repository rewrites a tracked file, or any other process touches the file while Desktop's file watcher triggers a background diff refresh), a user who had partially selected lines for a commit can end up silently committing and pushing content they never reviewed or explicitly selected. This is a silent corruption of what the user commits/pushes — the class of impact explicitly listed as in-scope.

### Likelihood Explanation
Requires no local/physical access, no admin rights, and no pre-existing host malware — only a crafted repository (with a hook, submodule init script, or other benign-looking automation) that rewrites tracked file content asynchronously to the file system while the app has it open with a partial selection. Desktop already refreshes status/diffs automatically via file watchers and periodic refresh (`_refreshRepository`, `updateChangesWorkingDirectoryDiff`), so the race window is realistically triggerable without unnatural user steps; the user's normal workflow (select some lines, then commit) is enough.

### Recommendation
When re-validating a cached `DiffSelection` against a freshly loaded diff, don't just filter indices by existence — compare line content (text/type) at each surviving index between the old and new diff, and drop (or force to unselected, with a warning) any diverging line whose content changed rather than assuming the previous select/deselect intent still applies. Alternatively, clear partial selections (similar to `clearPartialState`) whenever the underlying diff text hash for the file changes unexpectedly outside of the user's own edits, and surface a re-confirmation UI before committing if the diff used to build the patch differs from the diff that was last shown to the user.

### Proof of Concept
1. Open a repository in Desktop, modify a tracked file so it produces two hunks, and partially select only hunk #2 for commit (leaving hunk #1 unselected).
2. While the Changes view is open, have an external process (e.g. a repository-embedded `post-checkout`/`post-merge` hook, or a background CI-like script triggered by cloning a crafted repo) rewrite the same file so that new attacker-controlled content lands at the *same absolute diff line indices* previously occupied by the unselected hunk #1 lines — for example, shrinking hunk #1 and inserting new "addition" lines that shift into the index range that used to be hunk #2's selected lines.
3. Desktop's `updateChangesWorkingDirectoryDiff` reloads the diff and calls `withSelectableLines` — since those indices are still "includeable," the old `divergingLines` bitmap carries over unchanged (see `app-store.ts:3478-3497` and `diff-selection.ts:320-330`).
4. The user clicks Commit; `formatPatch` (`patch-formatter.ts:143-171`) uses `file.selection.isSelected(absoluteIndex)` on the *new* diff, staging whatever content now sits at the previously-selected indices — potentially the attacker-authored lines — without the user ever reviewing them.
5. The resulting commit, once pushed, silently contains lines the user did not consciously select.

Note: I was not able to fully trace every intermediate call path (e.g. exactly which background triggers call `updateChangesWorkingDirectoryDiff` versus a full commit-time diff reload, and whether `_commitIncludedChanges` always uses the most recently displayed diff or re-fetches one immediately before staging) within the available tool budget; a Devin session with full repository/file access would be needed to confirm the exact timing window and whether any additional guard (e.g. a hash comparison) exists elsewhere in the commit pipeline that isn't indexed here.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
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
