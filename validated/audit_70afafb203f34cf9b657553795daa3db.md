### Title
Stale line-index reuse in partial-commit patch generation can silently stage unreviewed content - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
The reported Solidity bug is a pure "miscalculation" class issue: a function combines a stale/incorrectly-derived value (a percentage) with a selection state (referrer set or not) and silently drops part of the expected output. The closest reachable analog in GitHub Desktop is the partial-commit ("stage some lines") pipeline, where `WorkingDirectoryFileChange.selection` stores *line indices* computed against one version of a file's diff, and `formatPatch()` later re-applies those same indices against a diff object that is not guaranteed to still correspond to the file's current on-disk bytes.

### Finding Description
`DiffSelection` tracks selected/deselected lines purely as **absolute line indices** (`divergingLines: Set<number>`), decoupled from the actual line text [1](#0-0) . When the working-directory diff is reloaded (e.g. after any change to the file), `updateChangesWorkingDirectoryDiff()` explicitly acknowledges that the diff "might have changed dramatically since last we loaded it" and only prunes selection entries that are no longer marked `isIncludeableLine()` — it does **not** verify that a previously-selected index still refers to the same textual content [2](#0-1) .

`formatPatch()` then blindly consumes those indices: for every hunk line it calls `file.selection.isSelected(absoluteIndex)` and includes/excludes the line based solely on the index, with no comparison against what the user actually saw/approved when they set that selection [3](#0-2) . The resulting patch is fed to `git apply --cached` (via `stageFiles`), which validates only diff *context* (with fuzz), not that the index-to-content mapping the UI displayed to the user is the one being staged.

Because Desktop diffs are computed from `git diff` output run against whatever is on disk at the moment of computation, any process that mutates the working tree between "user reviews/selects lines in the diff panel" and "user clicks Commit" — a build tool, a linter/formatter running on save, or (most relevant to the attacker model) a `post-checkout`/`post-merge`/smudge filter shipped in a cloned or fetched attacker-controlled repository — can shift hunk content while leaving line *offsets* superficially compatible. The stale `divergingLines` indices from the old diff are then reapplied against new hunk content, so the line the user thought they approved (e.g., a specific added line) no longer corresponds to what actually gets included in `hunkBuf`. This lets a repository-shipped hook silently swap in attacker content that ends up in the user's commit without any additional selection or warning, i.e., silent corruption of what the user commits.

### Impact Explanation
This is Low/Medium impact in the same class as the referral-fee report: no crash, no privilege escalation, but a quiet loss of guarantee — specifically the guarantee that partial-commit selection reflects the content the user visually reviewed. If exploited via a malicious repository hook that races the diff-review/commit window, it can cause a developer to unknowingly commit and later push attacker-controlled content, which is one of the explicitly in-scope impacts ("silent corruption of what the user commits or pushes").

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires precise timing (the working tree must change between diff load and stage) and depends on git hooks or tooling execution that Desktop already runs on checkout/merge for repositories it clones/fetches. Desktop does not sandbox or diff-verify content at staging time, so any repo that ships hooks capable of touching tracked files in this window can trigger it; the developer does not need to take any unusual action beyond the ordinary "select lines, then commit" workflow.

### Recommendation
Before generating the patch in `formatPatch`/`stageFiles`, re-derive (or hash-verify) that the diff object used matches the file's current on-disk content, and invalidate/re-request selection state (not just prune non-includeable lines) whenever the diff's hunk content differs from what was last rendered. Alternatively, store selections keyed by content hash of the line rather than by raw index, and refuse to stage a partial selection if the underlying hunk content has changed since the selection was made.

### Proof of Concept
1. Attacker crafts a repository containing a `post-checkout` (or similarly triggered) hook that appends/edits lines in a tracked file shortly after checkout/fetch.
2. Victim clones/fetches the repo in Desktop, makes changes, opens the Changes view, and partially selects specific added/removed lines in the diff panel (`DiffSelection.withLineSelection`) [4](#0-3) .
3. Before the victim clicks "Commit," the hook mutates the working tree file, shifting hunk content while keeping enough surrounding context for `git apply --cached` to still succeed with fuzz.
4. Desktop reloads the diff (`updateChangesWorkingDirectoryDiff`) but only prunes non-includeable indices, leaving stale `divergingLines` in place [5](#0-4) .
5. `formatPatch` reuses the stale indices against the new hunk lines, producing a patch that stages content the victim never reviewed [3](#0-2) ; `createCommit` stages and commits it without further verification [6](#0-5) .

Note: I was not able to fully trace `stageFiles`/`apply.ts`'s exact fuzz/context-matching tolerance from the available index (only partial contents were retrievable), so the precise conditions under which `git apply` would silently accept mismatched context rather than fail outright are not fully confirmed here. If deeper verification of `app/src/lib/git/apply.ts` and `app/src/lib/git/update-index.ts` is needed, a Devin session with full repository access would be required to confirm the exact fuzz-matching behavior.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3496)
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
```

**File:** app/src/lib/patch-formatter.ts (L143-170)
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
```

**File:** app/src/models/diff/diff-selection.ts (L205-210)
```typescript
  public withLineSelection(
    lineIndex: number,
    selected: boolean
  ): DiffSelection {
    return this.withRangeSelection(lineIndex, 1, selected)
  }
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```
