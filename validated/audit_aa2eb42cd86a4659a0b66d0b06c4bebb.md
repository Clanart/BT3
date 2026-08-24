### Title
Stale-diff line-index reuse lets a concurrently-modified working file silently corrupt what gets committed - (File: `app/src/lib/git/apply.ts`)

### Summary
The yAxis bug is a broken invariant between a value that was computed against one state (the strategy's live balance) and a bookkeeping delta that is applied against a different, stale state (the vault's tracked balance), with no re-validation before the subtraction is applied — corrupting accounting and locking funds. The GitHub Desktop analog is the same class of bug applied to partial-commit line selection: `DiffSelection` line indices are computed against one snapshot of a file's diff, but `applyPatchToIndex` re-fetches a *fresh* diff of the same file at commit time and blindly reapplies the old index-based selection to it, with no check that the two diffs still agree.

### Finding Description
`DiffSelection` tracks selected lines purely by numeric index into a diff's hunks (`divergingLines: Set<number>`), computed as `hunk.unifiedDiffStart + lineIndex`, as seen throughout the test/patch code: [1](#0-0) . This selection is built by the renderer against whatever diff was last loaded for that file, e.g. in `updateChangesWorkingDirectoryDiff` [2](#0-1) .

When the user commits, `_commitIncludedChanges` takes the currently selected files (with their currently-held `selection` object, tied to the diff that was loaded whenever the Changes view last refreshed) and passes them straight into `createCommit` without re-diffing: [3](#0-2) . `createCommit` then unstages everything and calls `stageFiles`, which for partially-selected files eventually calls `applyPatchToIndex`: [4](#0-3) .

Critically, `applyPatchToIndex` does **not** reuse the diff the selection was built against. It fetches a brand-new diff of the file from disk at that exact moment: [5](#0-4) , and then feeds that *new* diff plus the *old* selection indices into `formatPatch`: [6](#0-5) . `formatPatch` walks the new diff's hunks and, for each line, asks `file.selection.isSelected(absoluteIndex)` using positions from the new hunk layout [1](#0-0) .

If the file on disk changes between the time the diff was loaded for line-selection purposes and the moment `applyPatchToIndex` re-diffs it (a window that is not synchronized/locked in any way — no mtime, hash, or diff-identity check exists between selection state and the diff used to build the patch), the hunk boundaries and `unifiedDiffStart` offsets shift. The old `divergingLines` index set no longer maps to the same logical lines: an index the user intended to deselect (e.g. a malicious line they explicitly unchecked) can now land on a completely different, unreviewed line, and vice versa. There is a partial correction elsewhere: `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and drops indices that no longer exist whenever the UI reloads a diff and updates the store [7](#0-6) , but this reconciliation only runs on the UI's own periodic diff refresh — it is never invoked, or awaited, immediately before `applyPatchToIndex` performs its independent re-diff at commit time. So the guard that exists does not cover the actual window where the corruption occurs.

### Impact Explanation
This breaks the core Desktop guarantee that "what you selected in the diff view is exactly what gets committed." An attacker who can cause the working file to change during the narrow window between diff-load (selection) and commit — e.g. a build tool, watch script, editor autosave, git hook, or (per the report's scoping) content originating from a cloned/fetched repository that triggers a filesystem write via a build step or `post-checkout`/`post-merge` hook right before the user commits — can cause Desktop to silently include lines the user never selected/reviewed (or drop lines the user explicitly selected) into the commit that gets created and potentially pushed. This is exactly the "silent corruption of what the user commits or pushes" impact class called out as valid in the task.

### Likelihood Explanation
Partial/line-level commit selection is a heavily used core feature of Desktop, and the time between a user reviewing a diff and clicking "Commit" (or the commit being triggered by automation) is realistically long enough for an external process (build tooling, watchers, hooks triggered by repository content) to rewrite the file. No integrity check (hash/mtime/diff-shape comparison) exists to detect this drift before applying the stale selection to the freshly-fetched diff, so the corruption is silent — the commit succeeds without error, matching the "assertion doesn't fire, state is just wrong" shape of the original report.

### Recommendation
Before generating the patch in `applyPatchToIndex`, verify that the diff used to build the file's `DiffSelection` is still valid for the file's current on-disk content (e.g., compare against a captured diff identity/hash, or re-derive/refresh the selection's `selectableLines` against the newly fetched diff and reject/abort the commit if the file changed since selection, prompting the user to re-review). At minimum, `applyPatchToIndex` should use the same diff object the UI selection was computed from rather than independently re-fetching one, or must fail loudly (not silently apply a possibly-mismatched selection) if the two diverge.

### Proof of Concept
Conceptual reproduction (cannot be executed here, but follows directly from the code paths cited):
1. Open a modified tracked file in Desktop's Changes view; Desktop loads a diff and the user deselects a specific line (e.g. an added malicious line) via `DiffSelection.withLineSelection(i, false)`.
2. Before the user clicks Commit, an external process (build script, git hook triggered on file save, or content from a just-fetched branch) appends/removes lines earlier in the file, shifting all subsequent hunk offsets.
3. User clicks Commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff (`app/src/lib/git/apply.ts:60`), which now has different `unifiedDiffStart` values.
4. `formatPatch` applies the old selection indices to the new hunk layout (`app/src/lib/patch-formatter.ts:143-157`), so the line the user deselected is no longer at that index — the previously-unwanted/malicious line gets included in the resulting patch/commit despite having been explicitly unchecked, with no error or warning shown to the user.

### Citations

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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

**File:** app/src/lib/stores/app-store.ts (L3686-3698)
```typescript
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })

    const gitStore = this.gitStoreCache.get(repository)

    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/apply.ts (L60-60)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)
```

**File:** app/src/lib/git/apply.ts (L80-81)
```typescript
  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```
