This confirms the exact mechanism: `DiffSelection.isSelected(lineIndex)` at `app/src/models/diff/diff-selection.ts:122-136` is a purely positional/index-based lookup with no knowledge of diff content — it only knows line *numbers* (`divergingLines: Set<number>`), never validated against the diff that generated them once passed downstream to staging.

### Title
Partial-commit line selection is applied against a re-fetched, possibly-changed diff, silently corrupting committed content - (File: app/src/lib/git/apply.ts)

### Summary
GitHub Desktop lets a user select individual lines of a modified file for a partial commit. The selection is stored as a set of numeric line indices (`DiffSelection`), computed against the diff that was rendered in the Changes view at selection time. When the commit is actually created, Desktop does not reuse that diff — it re-fetches a brand-new diff from disk/index and blindly re-applies the old numeric selection to it.

### Finding Description
`_commitIncludedChanges` reads the currently selected `WorkingDirectoryFileChange` objects straight from cached state and hands them to `createCommit` → `stageFiles` → `applyPatchToIndex`, all synchronously with no re-validation step: [1](#0-0) 

`stageFiles` routes any file with `DiffSelectionType.Partial` to `applyPatchToIndex`: [2](#0-1) 

`applyPatchToIndex` then calls `getWorkingDirectoryDiff(repository, file)` again, right before staging — a **fresh** diff, independent of whatever diff was on screen when the user made their selections: [3](#0-2) 

That fresh diff is fed into `formatPatch`, which walks the new diff's hunks and decides whether to include each line purely by looking up `file.selection.isSelected(absoluteIndex)` — a numeric index computed relative to the *new* diff's line layout: [4](#0-3) 

`DiffSelection.isSelected` has no concept of diff content — it only tracks a `Set<number>` of line numbers that diverged from the default selection state: [5](#0-4) 

If the file's on-disk content changes between the moment the user finishes making their line selection (in the UI, against diff-at-time-T1) and the moment the commit button triggers staging (diff re-fetched at time T2), the hunk boundaries and per-line `absoluteIndex` values shift. The same numeric indices from T1 now point at *different* lines in the T2 diff. There is a partial mitigation for this class of problem, but it only runs when the diff panel itself reloads (e.g., user navigates away and back), not synchronously before staging: [6](#0-5) 

That reconciliation path is not on the commit code path at all — `_commitIncludedChanges` never calls it, so nothing rebinds `file.selection` to the diff that `applyPatchToIndex` is about to re-fetch. This is structurally the same broken invariant as the reported stETH bug: a **point-in-time value** (selection indices bound to a diff snapshot) is later combined with a **different, authoritative snapshot** (fresh diff at apply time) without reconciliation, silently producing an incorrect result — here, wrong lines staged/committed instead of an over-credited refund.

### Impact Explanation
If lines shift between selection and commit, the user's approved partial commit can silently include lines they explicitly deselected (potentially attacker-controlled or unreviewed content introduced via a build tool, formatter, git filter/smudge script, or any file-watching process bundled with a cloned repository) or silently drop lines the user intended to commit. This is exactly the "silent corruption of what the user commits" impact class: the user believes they reviewed and approved a specific hunk, but the actual commit content diverges from what was shown and approved, with no warning to the user.

### Likelihood Explanation
The window between diff render/selection and clicking "Commit" is user-controlled (typing a commit message, reviewing multiple files) and can be arbitrarily long, giving any concurrent file-modifying process (auto-formatters, linters-on-save, build watchers, git hooks configured within a fetched/cloned repository) ample opportunity to mutate the file. No special privileges are required beyond something in the repository/workspace already being able to write to tracked files, which is a normal, expected capability of dev-tooling shipped with a cloned project. I was not able to fully verify from the index alone whether any other guard exists elsewhere in the app (e.g., a check that re-diffs immediately before `createCommit`) beyond the `updateChangesWorkingDirectoryDiff` reconciliation shown, since not all call sites/state-update triggers were exhaustively inspected; a Devin session with full repo access would be needed to rule out an additional guard.

### Recommendation
Before staging, re-fetch the diff for each file with a partial selection and re-validate/rebase the stored `divergingLines` against the new diff's line content (not just numeric offsets) — e.g., using the same content-based reconciliation used in `updateChangesWorkingDirectoryDiff`, but invoked synchronously as part of `_commitIncludedChanges`/`stageFiles`, and abort or warn the user if the diff changed since the last selection render.

### Proof of Concept
1. Modify a tracked file with several lines; open it in the Changes view so Desktop computes a diff (T1) and select only a subset of the changed lines for a partial commit.
2. While the commit message is being typed (before clicking "Commit"), have a background process (e.g., a watch-mode formatter/linter defined in the repo's own tooling, or a filesystem write from another process) insert/remove a line above the hunk you selected, shifting subsequent line numbers.
3. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches a new diff (T2) with shifted `absoluteIndex` values; `formatPatch` (`app/src/lib/patch-formatter.ts:157`) applies the stale `divergingLines` set from T1 against the T2 line layout.
4. Inspect the resulting commit: it includes/excludes different lines than what was visually selected in the UI, with no error or confirmation shown to the user.

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

**File:** app/src/lib/stores/app-store.ts (L3681-3699)
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

    const gitStore = this.gitStoreCache.get(repository)

    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
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

**File:** app/src/lib/git/apply.ts (L59-61)
```typescript

  const diff = await getWorkingDirectoryDiff(repository, file)

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
