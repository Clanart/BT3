## Title
Stale line-index diff selections are reused unvalidated when building partial-commit patches, silently altering what gets committed - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, `app/src/lib/stores/updates/changes-state.ts`)

### Summary
The external report's root cause is a decision (fee direction) being computed from a cached value (`zkusdAmounts[_token]`) that is not refreshed to match the current, real state before the decision is used. The same class of bug exists in GitHub Desktop's partial-commit mechanism: a `WorkingDirectoryFileChange.selection` (a set of *absolute line indices* chosen by the user against one specific diff snapshot) is carried forward and reused to build the actual git patch **against a freshly re-fetched, potentially structurally different diff**, without validating that the indices still correspond to the same content.

### Finding Description
When staging a partially-selected file, `applyPatchToIndex` re-fetches the diff fresh at commit time and formats a patch using the file's cached `selection`: [1](#0-0) 

`formatPatch` walks the *new* diff's hunks and asks `file.selection.isSelected(absoluteIndex)` for each line, where `absoluteIndex` is simply `hunk.unifiedDiffStart + lineIndex` of the **new** diff: [2](#0-1) 

`DiffSelection` itself has no notion of file content — it only tracks a set of "diverging" numeric line indices relative to whatever diff produced them: [3](#0-2) [4](#0-3) 

Crucially, when the working-directory status is refreshed, the existing (possibly stale) selection is carried over to the new `WorkingDirectoryFileChange` **without any revalidation against the new diff structure**: [5](#0-4) 

The only place selection indices are reconciled against a fresh diff (`withSelectableLines`, dropping selections on lines that no longer exist) is `updateChangesWorkingDirectoryDiff` in `app-store.ts`, and that reconciliation happens **only for the single file currently being rendered in the Changes diff pane**: [6](#0-5) 

When the user actually commits, `_commitIncludedChanges` just takes whatever is currently in `state.changesState.workingDirectory.files` — i.e., every selected file's cached `selection`, not just the one being viewed — and hands it straight to `createCommit`/`stageFiles`/`applyPatchToIndex`: [7](#0-6) 

So for any file that is *not* the one actively displayed in the diff viewer at commit time, its `selection` can be based on a hunk layout that no longer matches the working tree content (e.g. because the file was changed on disk — by an external tool, a background submodule/branch operation, or content pulled in by a fetch/checkout — between when the user made line selections and when Commit was pressed). `applyPatchToIndex` re-fetches the diff, but blindly reapplies the old, positionally-defined selection to the new hunk layout, so the wrong lines end up interpreted as "selected"/"unselected" — exactly the "cached value drives a decision on stale/shifted current state" pattern from the report, applied to what actually gets written into the commit.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." If the underlying content shifts (line insertions/deletions elsewhere in the same hunk range, or hunk boundaries changing) between selection and commit for a file not currently shown in the diff pane, `formatPatch` can select the wrong lines by index, producing a patch that does not match the user's intended selection. Depending on the shift, this can silently include unintended content (e.g., lines the user explicitly deselected) or exclude content the user meant to include, without any error or warning — the commit succeeds and reports success.

### Likelihood Explanation
This requires: (1) a file with a partial (line-level) selection, (2) that file's on-disk/staged content changing after the diff was last computed for it but the app not re-rendering that file's diff (e.g., user switches to another file, or the change originates from something other than direct manual edits, such as a background operation on the repository), and (3) the user then committing without re-opening that file's diff. This is a plausible but non-trivial sequence — it depends on timing and on the file not being the actively-focused diff, so likelihood is Medium-Low rather than trivially reproducible on every commit.

### Recommendation
Before staging a partially-selected file (in `applyPatchToIndex`/`stageFiles`), revalidate the file's `DiffSelection` against the diff freshly fetched immediately before `formatPatch` is invoked — the same `withSelectableLines` reconciliation already used for the actively-viewed file in `updateChangesWorkingDirectoryDiff` should be applied to *every* file about to be committed, not only the one currently rendered. If the diff has changed since the selection was captured (e.g., compare a content hash/hunk signature, not just render state), the safer behavior is to fail the partial-stage operation for that file and force the user to re-review the diff, rather than silently applying indices from a stale diff to a new one.

### Proof of Concept
1. In Desktop, modify a tracked file so it has multiple hunks; open the Changes view and select only specific lines in one hunk (partial selection) for file `A`, leaving file `A`'s diff pane open initially, then switch focus to inspect file `B`'s diff (so file `A` is still included/selected for commit but is no longer the actively rendered diff).
2. While file `A`'s diff is not being actively re-rendered, alter its on-disk content in a way that shifts line/hunk boundaries without changing `A`'s selection object — e.g., a background repository operation (submodule sync, stash pop, checkout performed by another git client sharing the same working directory) touches `A`.
3. Return to the Changes list, keep file `A`'s previously computed `selection` (Desktop does not re-diff `A` because it isn't the focused file — see `updateChangesWorkingDirectoryDiff` short-circuiting on `selectedFileIDsBeforeLoad.length !== 1` / file-id checks), and click Commit.
4. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches `A`'s diff (now structurally different) and calls `formatPatch(file, diff)`, which applies the old absolute-line-index `selection` to the new hunk layout.
5. Inspect the resulting commit for file `A`: the staged lines do not correspond to what the user visually selected before step 2, demonstrating silent corruption of the commit content. [8](#0-7) [9](#0-8)

### Citations

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

**File:** app/src/models/diff/diff-selection.ts (L41-52)
```typescript
/**
 * An immutable, efficient, storage object for tracking selections of indexable
 * lines. While general purpose by design this is currently used exclusively for
 * tracking selected lines in modified files in the working directory.
 *
 * This class starts out with an initial (or default) selection state, ie
 * either all lines are selected by default or no lines are selected by default.
 *
 * The selection can then be transformed by marking a line or a range of lines
 * as selected or not selected. Internally the class maintains a list of lines
 * whose selection state has diverged from the default selection state.
 */
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

**File:** app/src/lib/stores/updates/changes-state.ts (L41-59)
```typescript
  // Attempt to preserve the selection state for each file in the new
  // working directory state by looking at the current files
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
```

**File:** app/src/lib/stores/app-store.ts (L3410-3497)
```typescript
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

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }

    const selectedFileID = changesState.selection.selectedFileIDs[0]

    if (selectedFileID !== selectedFileIdBeforeLoad) {
      return
    }

    const currentlySelectedFile =
      changesState.workingDirectory.findFileWithID(selectedFileID)
    if (currentlySelectedFile === null) {
      return
    }

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
