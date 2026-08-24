### Title
Stale `DiffSelection` line indices are silently re-applied to a freshly re-fetched diff during partial commits, causing wrong lines to be staged - (File: app/src/lib/git/apply.ts)

### Summary
GitHub Desktop's partial-commit ("stage some lines") feature stores the user's line selection as a set of *absolute indices* into a previously rendered diff (`DiffSelection`), not as content-addressed line identities. When the commit is actually performed, `applyPatchToIndex` re-fetches a **brand-new** diff from disk/index and blindly re-applies the old, index-based `DiffSelection` to it, with no re-validation that the indices still refer to the same logical lines. This mirrors the stETH bug class: a numeric value (`assets`, here "selected line index N") is captured at time T1 against one state of the world (share price / diff structure) and is later consumed at time T2 against a mutated state (new share price / new diff hunks) without being re-derived, silently producing a wrong result instead of erroring out.

### Finding Description
`formatPatch` (used to build the real commit patch) determines which lines to include purely by absolute position: [1](#0-0) 

`applyPatchToIndex` is the only place this patch is generated for staging, and it explicitly re-derives the diff from the working directory right before formatting/applying the patch: [2](#0-1) 

The `file.selection` object passed in, however, was computed earlier against whatever diff was loaded when the user interacted with the Changes list, and `DiffSelection` only stores line numbers, never content hashes: [3](#0-2) 

The application is aware that diffs can shift and that stale selections are a real hazard - but the mitigation for that is applied only to the *live UI re-render* path (`updateChangesWorkingDirectoryDiff`), which prunes lines that are no longer "includeable" from the selection, and its own comment admits the fix is incomplete ("we would be more clever about validating that any partial selection state is still valid... but for now we'll settle on just updating the selectable lines"): [4](#0-3) 

Crucially, this re-validation only runs when the diff panel is re-rendered for the *currently selected* file - it is not re-run, and there is no equivalent guard, immediately before `stageFiles`/`applyPatchToIndex` executes at commit time: [5](#0-4) 

If the working-tree content for the file changes between (a) the moment the user finishes selecting/deselecting hunks and (b) the moment the user clicks "Commit," the hunk boundaries and `unifiedDiffStart`/`unifiedDiffEnd` offsets recomputed in step (a) of `applyPatchToIndex` can differ from the ones the selection indices were computed against. Because `DiffSelection.isSelected(absoluteIndex)` only compares raw integers, an index that pointed at "the deleted line at position 12" in the old diff can silently now point at an unrelated context or addition line in the new diff. `formatPatch` will then either omit content the user intended to commit, or - more dangerously - include content the user explicitly deselected, without any error, confirmation dialog, or diff re-preview.

### Impact Explanation
This breaks the fundamental invariant of the "select lines to commit" feature: what the user visually approved is not necessarily what gets written into the commit. Silent inclusion of deselected lines can leak content the user intentionally excluded (e.g., a secret, a WIP snippet, or code from an unrelated concern) into a pushed commit; silent omission can drop intended changes without any indication of failure. Because this happens transparently as part of a routine "Commit" click, users have no reason to inspect the resulting commit diff before pushing, so corrupted commits can be pushed and become part of the shared history.

### Likelihood Explanation
The precondition is that the on-disk file content for the file being partially staged changes between the selection interaction and the commit click. This is plausible without any unusual user behavior: background operations Desktop itself performs (branch refresh, background fetch combined with automatic stash pop/restore, editor autosave, formatters/linters running on save, or another git client/process touching the file), any of which can occur during the window the user spends composing a commit message. No admin rights, local malware, or leaked credentials are required - only a normal editing/commit workflow with any concurrent modification of the same file, which is a routine occurrence in real repositories. The code's own inline comment acknowledges the selection-staleness problem exists and that the current handling ("update selectable lines") is a partial, not a complete, fix.

### Recommendation
- Re-validate (or fully recompute) the `DiffSelection` against a freshly fetched diff immediately before `applyPatchToIndex` builds the patch, rejecting/aborting the stage operation (with a user-facing warning) if the diff structure has changed since the selection was made, rather than silently reapplying stale indices.
- Prefer content-based line identity (e.g., hashing hunk content or line text plus a stable anchor) over raw absolute indices for `DiffSelection`, so a shifted diff cannot silently alias one line to another.
- As a stronger guarantee, have `createCommit`/`stageFiles` re-diff and re-render a final confirmation preview of the exact patch that will be applied, rather than trusting a UI-derived selection object computed at an earlier point in time.

### Proof of Concept
1. Open a tracked file with multiple hunks in GitHub Desktop and make several changes, producing 2+ diff hunks.
2. In the Changes view, select only a subset of lines (e.g., deselect an addition in the second hunk) - this computes a `DiffSelection` with `divergingLines` referencing absolute indices in the *current* diff snapshot.
3. Before clicking "Commit," modify the same file outside of Desktop (e.g., another editor auto-formats it, or a background script/hook appends/removes lines earlier in the file) such that hunk boundaries and line offsets shift, without triggering an in-app diff refresh (e.g., user has switched focus to the commit-message box, which does not re-run `updateChangesWorkingDirectoryDiff`).
4. Click "Commit." `stageFiles` → `applyPatchToIndex` ( [2](#0-1) ) fetches the new diff and calls `formatPatch` with the old `file.selection`. Because `isSelected` only compares integer indices ( [1](#0-0) ), the lines actually staged no longer correspond to what the user selected in step 2.
5. Inspect the resulting commit: it will contain lines the user never approved for inclusion, or omit lines the user did select - with no warning shown at any point.

### Citations

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

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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

**File:** app/src/lib/git/update-index.ts (L109-169)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
}
```
