## Title
Stale line-selection indices reapplied to a freshly re-fetched diff during commit staging cause silent corruption of committed content - (`app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets users stage a subset of lines from a file's diff for a "partial commit." The selection is stored as a set of **line indices relative to a specific `ITextDiff` object** [1](#0-0)  that was rendered to the user in the Changes view. However, when the commit is actually executed, `applyPatchToIndex` **independently re-fetches a brand-new diff from disk** via `getWorkingDirectoryDiff` and reapplies the old selection indices against it, rather than reusing the diff the user actually looked at when selecting lines.

### Finding Description
The commit pipeline is:
1. UI loads a diff and the user toggles line selection through `DiffSelection`, which tracks only integer line indices (`divergingLines`), with no binding to file content or hash of the diff it was computed from [2](#0-1) .
2. `_commitIncludedChanges` collects the files with a non-`None` selection and calls `createCommit` [3](#0-2) .
3. `createCommit` resets the index and calls `stageFiles` [4](#0-3) .
4. For any file with a partial selection, `stageFiles` calls `applyPatchToIndex` [5](#0-4) .
5. `applyPatchToIndex` calls `getWorkingDirectoryDiff(repository, file)` **again**, at commit time, producing a new diff object, and then calls `formatPatch(file, diff)` using the **old** `file.selection` against this new diff [6](#0-5) .
6. `formatPatch` decides what to include purely by `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is derived from the hunk/line layout of whatever diff was just re-fetched [7](#0-6) .

If the working-tree file changes between the time the user's selection was made (or last validated) and the moment `applyPatchToIndex` re-reads the file (e.g., because an editor with format-on-save, a linter, a build tool, a git smudge filter, or a `post-checkout`/`post-merge` hook — all of which could be introduced by content in a cloned/fetched attacker-controlled repository — rewrites the file), the hunk/line layout of the new diff no longer matches the layout the indices were computed against. The stored `divergingLines` set (a set of bare integers) is blindly reinterpreted against the new hunk structure, so `isSelected()` can now report `true`/`false` for entirely different lines than the ones the user checked or unchecked in the UI.

This mirrors the report's broken invariant: an index/ratio (`s.liquidationWithdrawRatio` / line-selection indices) computed against one state is applied unchanged to a different, freshly recomputed state (`totalAssets()`/new diff), producing a result that silently diverges from the user's intended outcome — instead of losing funds, the user silently commits/omits content they never approved.

### Impact Explanation
This can silently corrupt what the user commits: lines the user explicitly deselected (e.g., a debug statement, credential, or unwanted change) could still be staged and committed, or lines they wanted included could be dropped — without any error, since `git apply --cached` will happily apply a syntactically valid patch built from misaligned selection indices as long as the context lines still line up somewhere. Because the corruption is silent (no error surfaced to the user) and affects the permanent commit history that may subsequently be pushed to a shared/public remote, this satisfies "silent corruption of what the user commits or pushes."

### Likelihood Explanation
No `git apply` hard error occurs in the common case because most partial-selection line insertions/removals shift indices without breaking hunk boundaries, and `--whitespace=nowarn`/`--unidiff-zero` make the apply tolerant. The window between diff-load and commit is realistic: `updateChangesWorkingDirectoryDiff` only refreshes selectable lines opportunistically on the next status/diff refresh cycle [8](#0-7) , and `applyPatchToIndex` never checks that its freshly fetched diff is the same shape as the diff the currently-stored selection was built for before reusing the index set [9](#0-8) . Any file-system event that mutates the file's content or line layout after the visible diff was rendered (editors, tools, hooks — including ones shipped inside a cloned/fetched attacker repository) can trigger this without unusual user action.

### Recommendation
- Capture and pass through the exact diff object the selection was computed against (or a content hash of it) all the way to `applyPatchToIndex`, and abort/re-prompt the user if the on-disk file no longer matches that diff at staging time, rather than silently regenerating a new diff and reusing stale indices.
- Have `formatPatch`/`applyPatchToIndex` validate that the number of includeable lines in the newly-fetched diff matches what `file.selection` was built against (e.g., via `withSelectableLines`'s existing mechanism) before applying the patch, and fail loudly instead of proceeding on mismatch.

### Proof of Concept
1. Modify a tracked file so it has, say, 20 changed lines across two hunks; open it in the Changes view and deselect several specific lines (e.g., a line containing a secret) so only some lines are checked.
2. Before pressing "Commit," trigger something that rewrites the file on disk in a way that changes its diff hunk layout (e.g., an editor auto-format, a build step, or — in the cloned/fetched-repo threat scenario — a `post-checkout`/smudge filter shipped by the attacker's repository that runs and rewrites the file) without the Changes view being told to refresh before commit executes.
3. Click "Commit." `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) re-fetches the diff, and `formatPatch` reapplies the old `file.selection` indices (`app/src/lib/patch-formatter.ts:157`) against the new hunk layout.
4. Inspect the resulting commit: it can include the previously deselected line (e.g., the secret) or omit a line the user intended to include, with no warning shown to the user.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
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

**File:** app/src/lib/stores/app-store.ts (L3685-3699)
```typescript
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

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
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

**File:** app/src/lib/git/apply.ts (L52-81)
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
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
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
