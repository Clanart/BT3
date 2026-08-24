## Title
Stale line-selection indices applied to a freshly re-fetched diff can silently mis-stage lines during partial commits — (`app/src/lib/git/apply.ts`)

## Summary
The reported Liquid Ron bug is a case of two values that are supposed to move in lockstep — `totalAssets()` and `operatorFeeAmount` — being computed/consumed at different points in time, so a value calculated *before* a state-changing operation is used *after* it, corrupting the result for someone else. The closest structural analog I could confirm in this codebase is in the partial-commit ("stage selected lines") pipeline: `applyPatchToIndex` re-computes a *fresh* diff of the working file at staging time and then reuses the file's `DiffSelection` (a set of absolute line indices) that was computed against a *different, earlier* diff shown to the user.

## Finding Description
When a user partially stages a file, the UI loads a diff once and lets the user pick lines/hunks; the resulting `DiffSelection` stores selections keyed by `absoluteIndex` positions in that specific diff (`app/src/lib/patch-formatter.ts:143-145`, `hunk.unifiedDiffStart + lineIndex`).

At commit time, `stageFiles` (`app/src/lib/git/update-index.ts:109-168`) hands each partially-selected file to `applyPatchToIndex`: [1](#0-0) 

Note that `applyPatchToIndex` calls `getWorkingDirectoryDiff(repository, file)` **again**, independent of whatever diff the UI last rendered/cached, and then builds the patch via `formatPatch(file, diff)` using `file.selection.isSelected(absoluteIndex)` — i.e., the *old* selection indices are matched against the *newly fetched* diff's hunk/line layout: [2](#0-1) 

There is a partial safeguard in `app-store.ts` that recomputes `selectableLines` whenever the on-screen diff is reloaded while the user is actively viewing the Changes tab: [3](#0-2) 

However, this reconciliation only runs as a side effect of `updateChangesWorkingDirectoryDiff`, which is triggered by UI selection changes — it is **not** re-run immediately before `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` executes. `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681-3689`) takes whatever `file.selection` is currently held in `repositoryStateCache` and passes it straight through to `createCommit`, with no re-validation against a fresh diff: [4](#0-3) [5](#0-4) 

So if the on-disk content of the file changes between the last diff render the user reviewed and the moment the commit button is pressed (e.g. an external editor autosave, a build/format tool, a background sync client, or content rewritten by a smudge/clean filter driven by `.gitattributes` that ships with a cloned/fetched repository), the line indices baked into `file.selection` no longer correspond to the same content in the freshly fetched diff used to build the actual patch.

## Impact Explanation
Because `git apply --cached --unidiff-zero` matches hunks by content plus positional line numbers, a shifted/rewritten file can cause the partial-commit patch to select and stage different lines than what the user visually reviewed and intended to include — silently corrupting what gets committed (and subsequently pushed), the same class of harm called out as in-scope ("silent corruption of what the user commits or pushes"). In the best case `git apply` fails outright (safe); in the worst case content additions/deletions the user never selected end up staged, or content the user did select is silently dropped, without any confirmation dialog reflecting the discrepancy.

## Likelihood Explanation
This requires the working-tree file to be mutated between the diff render and the commit click, and for `git apply --unidiff-zero` to still succeed against the mismatched structure (rather than reject). This is a timing/TOCTOU-style condition rather than a deterministic, attacker-triggered exploit; I was not able to find a concrete, deterministic attacker-controlled trigger (e.g., a hook or filter that is guaranteed to fire between diff-render and commit) purely from static code review, since git hooks are not transferred by `clone`/`fetch` and would require the user to already have hostile local tooling (which is out of scope per the rules). A `.gitattributes`-driven clean/smudge filter shipped in a cloned repo is the most plausible avenue, but I could not confirm from the indexed files that such a filter is invoked between the two `getWorkingDirectoryDiff` calls in a way a remote attacker fully controls.

## Recommendation
Before staging, re-validate (or re-derive) the diff used for `formatPatch` against the exact diff the currently-held `DiffSelection` was computed from, and abort/refresh the selection (prompting the user) if the working file has changed (e.g. compare content hash/mtime, or thread the already-loaded diff from `changesState.selection.diff` through `stageFiles`/`applyPatchToIndex` instead of re-fetching it independently).

## Proof of Concept
Not able to construct a fully deterministic, remote-attacker-triggerable PoC from static analysis alone — the discrepancy is real and demonstrable locally (modify the file after selecting lines but before committing, then observe `formatPatch` mapping stale indices onto the new diff), but exploiting it purely via a "cloned/fetched repository" without additional local tooling could not be confirmed with the code available in the index.

Given the uncertainty about a deterministic, unprivileged attacker trigger fitting the strict in-scope criteria, and that this fork already hardens the more classic Desktop bug classes (path traversal in clone paths, deep-link file-open, symlink escapes in `resolveWithin`, OAuth CSRF `state` validation, trampoline-based credential/askpass isolation) with explicit guards and tests, I present this as the most credible but **not fully confirmed** analog rather than a certain finding.

### Citations

**File:** app/src/lib/git/apply.ts (L52-61)
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

**File:** app/src/lib/stores/app-store.ts (L3681-3689)
```typescript
  public async _commitIncludedChanges(
    repository: Repository,
    context: ICommitContext
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```
