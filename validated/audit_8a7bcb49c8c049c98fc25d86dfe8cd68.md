## Analysis

The MasterChef bug's core pattern is: **a stale, index/position-based accounting value is carried forward and reapplied against updated state without being reconciled against the *content* it's supposed to represent**, causing silent under/over-accounting relative to what the caller expects. The closest concrete analog in GitHub Desktop is the way partial-commit line selections are tracked by **numeric line index** rather than by content, and that index-based selection is re-applied to a **freshly re-fetched diff** at commit time instead of the diff the user actually reviewed. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Line-index-based partial-commit selection is silently reapplied to a re-fetched diff at staging time, causing wrong content to be committed - (File: app/src/lib/git/apply.ts, app/src/models/diff/diff-selection.ts)

### Summary
`DiffSelection` tracks which lines are included in a partial commit purely by **numeric index** into the diff's unified line stream, not by line content or hunk identity. When a user reviews a diff and selects/deselects specific lines, that selection state (`divergingLines`, a `Set<number>`) is stored on the `WorkingDirectoryFileChange`. At commit time, `applyPatchToIndex` (used by `stageFiles`) does **not** reuse the diff the user looked at — it calls `getWorkingDirectoryDiff` again, fresh, and then feeds that fresh diff plus the old index-based selection into `formatPatch`.

### Finding Description
The known limitation is explicitly acknowledged in the code comment in `app-store.ts`:
> "The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..." [2](#0-1) 

This reconciliation only happens on the UI-refresh path (`updateChangesWorkingDirectoryDiff`), and even there it only drops selections whose index is no longer "includeable" — it does not verify the content at that index is the same content the user selected. Worse, the commit path is independent of this reconciliation entirely: `_commitIncludedChanges` passes the `WorkingDirectoryFileChange` objects (carrying their `DiffSelection`, i.e. the `divergingLines` index set) straight to `createCommit` → `stageFiles` → `applyPatchToIndex`. [4](#0-3) [5](#0-4) 

`applyPatchToIndex` fetches the diff **again at that moment**:
```
const diff = await getWorkingDirectoryDiff(repository, file)
```
and hands that fresh diff, together with the stale `file.selection` (index-based), to `formatPatch`, which walks the *new* hunks and asks `selection.isSelected(index)`/`isRangeSelected` per line index of the new diff. [6](#0-5) 

If the on-disk file content changes between when the user made their line selection (based on the diff rendered in the UI) and when the commit actually runs `applyPatchToIndex` — e.g. because a smudge/clean filter, a pre-commit-adjacent background tool, an editor autosave, or content written by another process modifies the file — the hunk boundaries and line ordering can shift while the selection's `divergingLines` set (pure integers) stays the same. The same numeric indices now point at different logical lines/content, so `formatPatch` will silently include or exclude the wrong lines relative to what the user actually reviewed and intended to select. No error is raised; the operation is not the "reject if selection is stale" behavior the M-20 report's mitigation calls for — it is treated as still-valid state, exactly analogous to `updatePool` treating a stale `lastRewardBlock` accounting window as still valid past `endBlock`.

### Impact Explanation
This can produce a **silent corruption of what the user commits and pushes**: content the user did not intend to stage/commit gets included (or content they intended to include gets dropped), without any warning dialog. Because Desktop re-fetches the diff independently at staging time rather than staging exactly the diff the user reviewed, a race between diff-load time and commit time is sufficient — no privilege escalation or local file tampering by the attacker is required beyond what's already reachable through normal repository content changes (e.g., a `.gitattributes`-driven filter, a build tool, or an editor watch/save cycle acting on files in a cloned/fetched repository).

### Likelihood Explanation
Moderate-to-low but plausible: the window is the time between the user's diff view rendering and clicking "Commit," during which some other process (editors with format-on-save, linters/build watchers, git filters triggered by unrelated git operations) touches the same file. Desktop's own code comments acknowledge this exact class of staleness is not fully handled, which increases confidence this is a genuine, currently-unaddressed gap rather than a purely theoretical one.

### Recommendation
- At staging/commit time, validate the file's `DiffSelection` against the diff that will actually be used to build the patch (the one fetched inside `applyPatchToIndex`), not just against the diff shown earlier in the UI.
- Prefer identifying selected content by hunk/line *content hash* or by re-diffing against the exact blob the selection was computed from, rather than by raw integer line index, so that a shifted line numbering cannot silently remap an old selection onto different content.
- If the diff has changed since the user's selection was made, refuse to commit (or re-prompt the user with the updated diff) rather than silently applying the old index-based selection to new content — mirroring the correct fix pattern from the MasterChef report ("finalize/validate before applying stale accounting, don't just proceed").

### Proof of Concept
1. Open a file with multiple hunks in Desktop's Changes view; use the "Discard/only include some lines" UI to select only specific lines for the commit (creating a `Partial` `DiffSelection` with specific `divergingLines` indices).
2. Before clicking "Commit," have another process (an editor auto-format, a build step, or a git `clean`/`smudge` filter triggered by another Desktop operation) rewrite the file so that hunk boundaries/line ordering shift but the file is still flagged as modified.
3. Click "Commit." `_commitIncludedChanges` passes the file (still carrying the old `divergingLines` index set) to `createCommit`, which calls `applyPatchToIndex`, which independently re-fetches the diff via `getWorkingDirectoryDiff` and applies the *old* index set to the *new* hunk structure in `formatPatch`.
4. Inspect the resulting commit: the staged/committed lines do not match what was visually selected in the UI before the underlying file changed — confirming silent corruption of the commit content without any error or confirmation from Desktop. [7](#0-6)

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3680-3699)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
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

**File:** app/src/lib/git/apply.ts (L52-83)
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

  return Promise.resolve()
```

**File:** app/src/lib/git/update-index.ts (L109-129)
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
```

**File:** app/src/lib/patch-formatter.ts (L113-133)
```typescript
/**
 * Creates a GNU unified diff based on the original diff and a number
 * of selected or unselected lines (from file.selection). The patch is
 * formatted with the intention of being used for applying against an index
 * with git apply.
 *
 * Note that the file must have at least one selected addition or deletion,
 * ie it's not supported to use this method as a general purpose diff
 * formatter.
 *
 * @param file  The file that the resulting patch will be applied to.
 *              This is used to determine the from and to paths for the
 *              patch header as well as retrieving the line selection state
 *
 * @param diff  The source diff
 */
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''
```
