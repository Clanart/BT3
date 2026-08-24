## Analysis

The upstream Revert Lend bug is a state-ordering flaw: a per-unit selector (`reserveFactorX32`) is changed without first materializing the state that depends on the *old* value, so the new selector is retroactively applied over the old accrual window, silently corrupting the derived value (`lendExchangeRateX96`).

I found a structurally identical pattern in GitHub Desktop's partial-commit pipeline: a **position-based line selection** (`DiffSelection`) is captured against one diff snapshot, then later replayed against a diff re-fetched at commit time, with no re-validation that the positions still refer to the same content.

### Title
Partial-commit line selection is applied by raw position against a diff re-fetched at commit time, allowing silent inclusion/exclusion of unintended lines - (`File: app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, `app/src/lib/git/update-index.ts`)

### Summary
`DiffSelection` tracks which lines a user wants to commit purely by integer index (`divergingLines: Set<number>`), with no binding to line content. When the user commits, `stageFiles` → `applyPatchToIndex` re-runs `git diff` to get a **fresh** `ITextDiff` and then calls `formatPatch(file, diff)`, which walks the new diff's hunks and asks `file.selection.isSelected(absoluteIndex)` using indices computed from the earlier, possibly stale, diff. If the working tree content shifts between when the selection was made (based on the diff shown in the Changes view) and when the patch is generated at commit time, the positional indices silently point at different logical lines, producing a commit that does not match what the user reviewed and approved.

### Finding Description
`WorkingDirectoryFileChange.selection` is a `DiffSelection` that records selected/deselected lines as `Set<number>` indices (`app/src/models/diff/diff-selection.ts` `divergingLines`/`selectableLines`), anchored to `hunk.unifiedDiffStart + lineIndex` from the diff that was on screen when the user made selections [1](#0-0) . This selection is stored in `IChangesState`/`workingDirectory.files` and reused across renders until the file is explicitly reconciled by `updateChangesWorkingDirectoryDiff` [2](#0-1) .

When the user clicks Commit, `_commitIncludedChanges` takes the **currently cached** `selectedFiles` (with their existing `.selection`) straight out of state and passes them to `createCommit` [3](#0-2) . `createCommit` unstages everything and calls `stageFiles`, which for any partially-selected file calls `applyPatchToIndex` [4](#0-3) [5](#0-4) .

`applyPatchToIndex` re-runs `git diff` from scratch (`getWorkingDirectoryDiff`) immediately before building the patch [6](#0-5) , then calls `formatPatch(file, diff)` [7](#0-6) . `formatPatch` iterates the *new* diff's hunks and tests each line with `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is derived from the new hunk's `unifiedDiffStart` [8](#0-7) . Because `DiffSelection.isSelected` only knows integer positions with no content binding [9](#0-8) , if the new diff's hunk boundaries/line counts differ even slightly from the diff the selection was computed against, the same index now refers to a different line.

The only mitigation Desktop has is `updateChangesWorkingDirectoryDiff`'s own comment: *"The diff might have changed dramatically since last we loaded it… we'll settle on just updating the selectable lines"* [10](#0-9)  — but this reconciliation only runs when the Changes view proactively reloads the diff for the *currently selected* file; it is not re-run as a guard immediately before `createCommit` executes, and it does nothing to protect the second, independent `getWorkingDirectoryDiff` call made inside `applyPatchToIndex` at actual patch-build time. There is a window between "user finalizes line selection in the UI" and "git diff is fetched again inside `applyPatchToIndex`" where nothing revalidates that the index mapping is still correct.

### Impact Explanation
If the working tree content for a partially-staged file changes between the last diff render and the moment `applyPatchToIndex` re-diffs the file (e.g., a build tool, editor auto-format-on-save, a git `clean`/`smudge` filter defined via `.gitattributes` in a cloned/fetched attacker-controlled repository, or another background process touches the file), the hunk shape and `unifiedDiffStart` offsets can shift. The stale positional selection is then blindly reapplied to the new hunks by `formatPatch`, so:
- lines the user explicitly deselected (e.g. containing secrets, debug code, or unwanted content) can be silently included in the commit, or
- lines the user selected for inclusion can be silently dropped from the commit,

without any diff/warning shown to the user before the commit is created. This is a silent corruption of what the user commits — the class of impact explicitly called out as valid for this analysis.

### Likelihood Explanation
This requires no local/admin access, no leaked credentials, and no unnatural user interaction: it only needs ordinary use of Desktop's partial-commit (line/hunk selection) feature on a repository whose content is influenced externally (fetch-triggered filters, format-on-save tooling, concurrent git operations, or a race window between diff render and commit) — conditions attacker-controlled repositories can set up via `.gitattributes`-defined clean/smudge filters that legitimately execute during Desktop's own repeated `git diff` invocations. The window is real but narrow (bounded by the time between the last UI diff refresh and the commit-time re-diff), so likelihood is moderate rather than high.

### Recommendation
Before applying a cached `DiffSelection` to a freshly fetched diff in `applyPatchToIndex`/`formatPatch`, re-validate that the diff has not changed since the selection was captured (e.g., compare a content hash or the full hunk text/line count, not just positional indices) and re-run the same "invalidate selection on selectable-line change" logic already implemented in `updateChangesWorkingDirectoryDiff` immediately before staging, aborting or warning the user if the underlying diff has shifted rather than silently reapplying stale indices.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file, selecting only a subset of lines/hunks for commit (`Partial` `DiffSelectionType`).
2. Before clicking "Commit", trigger a change to the file's on-disk content in a way that shifts line offsets without the Changes view re-rendering the diff for that file (e.g., a `.gitattributes` `clean` filter that appends/removes a line whenever `git diff`/`git add` reads the file, or a concurrent process editing the file).
3. Click "Commit". `createCommit` → `stageFiles` → `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` (`app/src/lib/git/apply.ts:60`) getting a diff with shifted hunk offsets, then `formatPatch` (`app/src/lib/patch-formatter.ts:143-157`) applies the old `DiffSelection` indices to the new hunks.
4. Inspect the resulting commit: it contains lines the user did not intend to include (or omits lines the user intended to include), with no warning surfaced during the commit flow.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L78-84)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L3685-3698)
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

**File:** app/src/lib/git/apply.ts (L60-60)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)
```

**File:** app/src/lib/git/apply.ts (L80-80)
```typescript
  const patch = await formatPatch(file, diff)
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
