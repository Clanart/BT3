### Title
Stale line-based `DiffSelection` carried across working-directory refreshes causes silent corruption of committed content - ([File: app/src/lib/stores/updates/changes-state.ts])

### Summary
`updateChangedFiles` preserves a file's partial-selection state (`DiffSelection`, keyed only by `file.id` = path+status) across every working-directory refresh, without validating that the underlying diff content the selection was computed against is still the same. This mirrors the reported Solidity bug: a piece of derived state (`slot0` / here, line-index-based selection) is carried forward as a persistent side effect and is only invalidated in one specific code path, not universally, so it can be applied against different underlying data than it was computed for.

### Finding Description
`WorkingDirectoryFileChange.id` is derived from path and status kind, not from file content [1](#0-0) . `DiffSelection` tracks selected lines as a `Set<number>` of absolute line indices within a specific diff's hunks [2](#0-1) .

On every status refresh, `updateChangedFiles` matches files by `id` and blindly re-attaches the previous `DiffSelection` to the new file object representing the freshly computed working-directory status, with no check that the diff content behind that selection is unchanged: [3](#0-2) 

The *only* place that reconciles line indices against a possibly different diff is `updateChangesWorkingDirectoryDiff`, and it does so only for the single file currently selected/rendered in the UI, by recomputing `selectableLines` and calling `withSelectableLines`: [4](#0-3) 

For every other file with a partial selection that isn't the actively displayed one, no such reconciliation happens — the stale `divergingLines` (absolute line indices) survive unchanged into the next refresh cycle, even if the file's actual diff hunks have shifted (lines added/removed above the previously selected range, e.g. from a smudge/clean filter, a `post-checkout`/`post-merge` hook, or any repository-controlled mechanism that rewrites tracked files after a fetch/checkout/merge — all of which are attacker-controllable in a cloned/fetched repository via `.gitattributes` filters or hooks).

When the user finally commits, `stageFiles` routes any file with `DiffSelectionType.Partial` through `applyPatchToIndex`, which regenerates the diff fresh from disk and then calls `formatPatch`, which decides what to include purely by `file.selection.isSelected(absoluteIndex)` against the *new* diff's line positions: [5](#0-4) [6](#0-5) 

Because the selection was computed against the old line layout but applied to the new one, the indices no longer refer to the lines the user actually reviewed and intended to include/exclude. There is no content hash, hunk-position, or line-count comparison guarding this reuse — the exact class of bug in the report: state persisted from one code path (viewing/selecting File A while File B's content changes underneath) is never invalidated for the other path (committing File B with its stale selection).

### Impact Explanation
This can cause GitHub Desktop to silently stage and commit (and subsequently push) different lines than the ones the user selected in the diff review UI. Since the corruption happens transparently — the UI shows the file as "partially selected" without any indication that the selection is stale — a user could unknowingly commit/push unintended content (e.g. secrets, unreviewed lines, or lines from a different logical change) to a shared remote. This falls squarely under "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Triggering requires: (1) a file with a partial (line-level) selection, and (2) that file's on-disk diff changing between two status refreshes without the user re-opening/re-reviewing that file's diff (which is the normal state when working with more than one changed file). Repository-controlled content-rewriting mechanisms (`.gitattributes` clean/smudge filters, `post-checkout`/`post-merge` hooks in a cloned/fetched repo) are a natural, attacker-influenceable trigger for changing file content between Desktop's periodic status refreshes and a user's later commit action, without requiring any unusual user behavior beyond normal partial-commit workflows. This is a plausible but not certain path — exploitation depends on the app performing an intervening status refresh (which it does frequently, e.g. via file-watcher events and periodic refresh) while the file is not the one being actively viewed.

### Recommendation
Invalidate or re-validate partial `DiffSelection` state whenever the underlying diff for a file changes, not only for the currently selected/rendered file. Concretely, `updateChangedFiles` should compare a content-derived signal (e.g. blob SHA, mtime+size, or a hash of the diff hunks) rather than relying solely on `file.id`, and drop/re-map any `Partial` selection whose backing diff no longer matches — analogous to how the `_getTargetOutput`-style fix would explicitly re-derive the current state for every affected path instead of reusing a persisted value that was valid only for a different precondition.

### Proof of Concept
Conceptual reproduction (not independently executed in this session, since it requires live git hook/filter behavior — recommend validating with a background Devin session):
1. Create/track two files, A and B, each with multiple hunks.
2. In Desktop, select File A for viewing and mark only specific lines of File B as included (partial selection) without opening File B's diff.
3. Externally (simulating an attacker-controlled repo mechanism, e.g. a `post-checkout` hook or a smudge filter defined in `.gitattributes`) rewrite File B's content so that lines shift position (e.g., insert lines above the previously selected range) — this is a change a malicious/compromised repository can trigger on checkout/merge.
4. Let Desktop's automatic status refresh run (`_loadStatus`/`updateChangedFiles`) while File B is still not the active diff selection.
5. Commit the included changes. Verify via `git show` on the resulting commit that the staged/committed content for File B corresponds to different logical lines than what was originally marked as selected before the rewrite — demonstrating the stale-selection-to-new-diff mismatch described above.

### Citations

**File:** app/src/models/status.ts (L294-322)
```typescript
/** encapsulate the changes to a file in the working directory */
export class WorkingDirectoryFileChange extends FileChange {
  /**
   * @param path The relative path to the file in the repository.
   * @param status The status of the change to the file.
   * @param selection Contains the selection details for this file - all, nothing or partial.
   * @param oldPath The original path in the case of a renamed file.
   */
  public constructor(
    path: string,
    status: AppFileStatus,
    public readonly selection: DiffSelection
  ) {
    super(path, status)
  }

  /** Create a new WorkingDirectoryFileChange with the given includedness. */
  public withIncludeAll(include: boolean): WorkingDirectoryFileChange {
    const newSelection = include
      ? this.selection.withSelectAll()
      : this.selection.withSelectNone()

    return this.withSelection(newSelection)
  }

  /** Create a new WorkingDirectoryFileChange with the given diff selection. */
  public withSelection(selection: DiffSelection): WorkingDirectoryFileChange {
    return new WorkingDirectoryFileChange(this.path, this.status, selection)
  }
```

**File:** app/src/models/diff/diff-selection.ts (L41-84)
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
export class DiffSelection {
  /**
   * Initialize a new selection instance where either all lines are selected by default
   * or not lines are selected by default.
   */
  public static fromInitialSelection(
    initialSelection: DiffSelectionType.All | DiffSelectionType.None
  ): DiffSelection {
    if (
      initialSelection !== DiffSelectionType.All &&
      initialSelection !== DiffSelectionType.None
    ) {
      return assertNever(
        initialSelection,
        'Can only instantiate a DiffSelection with All or None as the initial selection'
      )
    }

    return new DiffSelection(initialSelection, null, null)
  }

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

**File:** app/src/lib/stores/updates/changes-state.ts (L43-60)
```typescript
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
    })
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

**File:** app/src/lib/git/update-index.ts (L109-168)
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
```

**File:** app/src/lib/patch-formatter.ts (L129-157)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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
