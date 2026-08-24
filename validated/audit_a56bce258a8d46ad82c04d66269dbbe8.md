## Analysis

Confirmed the exact TOCTOU: `WorkingDirectoryFileChange.selection` (the line/hunk selection the user made while reviewing a *previously fetched* diff in the Changes view, cached in `changesState.selection.diff` via `updateChangesWorkingDirectoryDiff`) is later replayed against a **freshly re-fetched diff** inside `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` again at commit time and feeds it straight into `formatPatch(file, diff)` using the old, absolute-line-index-based selection. [1](#0-0) [2](#0-1) 

`formatPatch` trusts `file.selection.isSelected(absoluteIndex)` against whatever hunks the newly-fetched diff produced — there is no check that the diff used to build the patch is the same diff the user actually reviewed/selected against. [3](#0-2) 

### Title
Partial-commit patch is built from a re-fetched working-directory diff, not the one the user reviewed and selected against - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user partially stages a file (selecting individual lines/hunks) in GitHub Desktop, the app records that selection as a set of *absolute line indices* against the diff that was shown in the Changes view at selection time. That diff is fetched and cached in `changesState.selection.diff`. At actual commit time, `stageFiles` → `applyPatchToIndex` does not reuse that cached/reviewed diff — it re-invokes `getWorkingDirectoryDiff` against the current on-disk file and hands the new diff, together with the *stale* selection indices, to `formatPatch`. If the on-disk content of the file changes between the time the user reviewed the diff and made their selection, and the time `createCommit`/`stageFiles` actually run, the hunk boundaries and line indices in the freshly-computed diff no longer correspond to what the user looked at, so `file.selection.isSelected(absoluteIndex)` can select/deselect the wrong lines relative to the user's real intent.

### Finding Description
- The Changes view loads a diff once (`updateChangesWorkingDirectoryDiff`) and caches it on `ChangesSelection.diff`; the user's line selection (`DiffSelection`) is keyed by absolute index into *that* diff's hunks. [4](#0-3) 
- The comment at `updateChangesWorkingDirectoryDiff` even acknowledges the diff can change "dramatically" between loads and only patches up which lines remain *selectable*, not whether the selection still points at the same conceptual lines. [5](#0-4) 
- When the user commits, `createCommit` → `stageFiles` routes any file with a partial (`DiffSelectionType.Partial`) selection to `applyPatchToIndex`. [6](#0-5) 
- `applyPatchToIndex` re-derives the diff from disk right before building the patch — this is a brand-new `git diff` invocation, independent of whatever diff/hunks the UI showed the user: [1](#0-0) 
- `formatPatch` then walks the *new* diff's hunks and asks the stale `file.selection` (absolute indices from the old diff) whether each line is selected: [3](#0-2) 

The invariant broken is analogous to the Tokemak bug's `totalAssets_cached` vs `totalAssets_actual`: one code path (the UI/selection logic) operates on a **cached** snapshot of the diff, while another path that actually mutates state (`applyPatchToIndex`/`formatPatch`, which writes to the git index and therefore the commit) operates on the **actual, freshly-recomputed** diff. Nothing reconciles the two before the “commit” action is taken — there is no re-validation that the cached selection is still semantically valid against the new hunks; the code only trims/repopulates *which indices exist*, not what content those indices now correspond to.

An attacker who controls the content the user is diffing against (e.g. a cloned/fetched repository combined with any mechanism that mutates the working tree between the diff being shown and the commit being executed — a git `post-checkout`/`post-merge`/`pre-commit` hook shipped in the repo, a configured smudge/clean or merge driver referenced from `.gitattributes`, or an LFS/other filter that rewrites file content on operations Desktop performs automatically such as checkout, stash, or hook execution during commit) can cause the file to look different at patch-build time than it did when the user selected lines to include. Because Desktop runs `pre-commit`/`prepare-commit-msg` hooks as part of `createCommit`, and `applyPatchToIndex`'s diff fetch happens as part of `stageFiles` (called before the commit is finalized but after any earlier hook-driven mutation), a repository-shipped hook or filter is a realistic trigger for the file changing between the user's review and the final patch construction. [7](#0-6) 

### Impact Explanation
This can cause **silent corruption of what the user commits**: the lines actually included in the commit (or excluded from a "discard changes" operation via the analogous `formatPatchToDiscardChanges`) may differ from what the user reviewed and explicitly selected in the UI. In the worst case, a hunk that the user deliberately excluded could be re-mapped (by shifted line numbers) onto content the user intended to keep, or vice versa, without any error or warning — the user believes they committed/discarded exactly what they saw, but git ends up with different content.

### Likelihood Explanation
Moderate-to-low but plausible for an attacker-controlled repository: it requires the working tree content to change between the diff being cached in the Changes view and `applyPatchToIndex`/`stageFiles` running for that file. Existing guards do not stop this — `stageFiles`/`applyPatchToIndex` never re-diffs against the cached diff or asserts hunk equivalence with what was previously shown; the only reconciliation in `updateChangesWorkingDirectoryDiff` recomputes *selectable* lines, not selection correctness, and that reconciliation happens on the UI's own periodic refresh, not right before staging.

### Recommendation
Before building the patch in `applyPatchToIndex`, compare the freshly fetched diff's hunks against the diff the selection was made against (e.g. via `textDiffEquals`, already used in `seamless-diff-switcher.tsx`); if they differ, refuse the partial commit for that file (or re-prompt the user) rather than silently applying the stale selection to new hunk boundaries. Alternatively, pass the already-cached, reviewed `ITextDiff`/`ILargeTextDiff` from the Changes view state directly into `applyPatchToIndex`/`formatPatch` instead of re-fetching it from disk.

### Proof of Concept
1. Open a repository containing a tracked file `foo.txt` with several lines.
2. In Desktop's Changes view, modify `foo.txt` and open the diff; select only specific lines/hunks to include in the commit (partial selection), leaving the diff cached in `changesState.selection.diff`.
3. Before pressing "Commit" (or via a repo-provided `pre-commit` hook that runs as part of `createCommit`, or a filter/driver triggered during `stageFiles`), have the file's content shift (e.g. lines inserted/removed) so the new `git diff` produces hunks with different line offsets than the diff the user reviewed.
4. Click commit. `stageFiles` calls `applyPatchToIndex`, which re-fetches the diff via `getWorkingDirectoryDiff` and calls `formatPatch(file, diff)` using the stale `file.selection` absolute indices from step 2.
5. Inspect the resulting commit: the staged content differs from what the user actually selected in the UI, with no error or warning shown.

*Note: I was not able to fully verify a concrete, unprivileged, remotely-triggerable mechanism (e.g., which exact hook/filter Desktop executes automatically without any user prompt) that mutates the working tree in the narrow window between diff display and `stageFiles` execution — this would need further investigation of Desktop's hook execution ordering (`onHookProgress`/`interceptHooks` in `commit.ts`) and any auto-run filters (LFS smudge, `.gitattributes` `filter=`) to fully confirm end-to-end exploitability versus a purely race-condition/local-tooling trigger.*

### Citations

**File:** app/src/lib/git/apply.ts (L52-62)
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
```

**File:** app/src/lib/stores/app-store.ts (L3404-3448)
```typescript
  private async updateChangesWorkingDirectoryDiff(
    repository: Repository
  ): Promise<void> {
    const stateBeforeLoad = this.repositoryStateCache.get(repository)
    const changesStateBeforeLoad = stateBeforeLoad.changesState

    if (
      changesStateBeforeLoad.selection.kind !==
      ChangesSelectionKind.WorkingDirectory
    ) {
      return
    }

    const selectionBeforeLoad = changesStateBeforeLoad.selection
    const selectedFileIDsBeforeLoad = selectionBeforeLoad.selectedFileIDs

    // We only render diffs when a single file is selected.
    if (selectedFileIDsBeforeLoad.length !== 1) {
      if (selectionBeforeLoad.diff !== null) {
        this.repositoryStateCache.updateChangesState(repository, () => ({
          selection: {
            ...selectionBeforeLoad,
            diff: null,
          },
        }))
        this.emitUpdate()
      }
      return
    }

    const selectedFileIdBeforeLoad = selectedFileIDsBeforeLoad[0]
    const selectedFileBeforeLoad =
      changesStateBeforeLoad.workingDirectory.findFileWithID(
        selectedFileIdBeforeLoad
      )

    if (selectedFileBeforeLoad === null) {
      return
    }

    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

**File:** app/src/lib/git/commit.ts (L26-70)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

  const args = ['-F', '-']

  if (options?.amend) {
    args.push('--amend')
  }

  if (options?.noVerify) {
    args.push('--no-verify')
  }

  if (options?.signOff) {
    args.push('--signoff')
  }

  if (options?.allowEmpty) {
    args.push('--allow-empty')
  }

  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
```
