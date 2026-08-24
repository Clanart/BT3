### Title
Stale line-based `DiffSelection` re-applied to a fresh working-directory diff during partial commit staging can silently commit unintended lines - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
GitHub Desktop lets a user select individual lines of a file's diff for a partial commit (`DiffSelectionType.Partial`). That selection is stored as a set of absolute line indices tied to a specific diff snapshot the UI previously fetched. When the commit is actually staged, `applyPatchToIndex` independently re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` and then feeds the *old* line-index selection into `formatPatch` against this *new* diff, with no check that the two diffs are the same shape. If the working tree changes between the time the user made their line selection and the moment staging happens (e.g. a background fetch triggers `fastForwardBranches`, or any other concurrent process touches the file), the indices silently point at different lines, corrupting what actually gets committed.

### Finding Description
- The user's partial selection is index-based and immutable once created: `DiffSelection` tracks `divergingLines` as raw line numbers with no diff/content fingerprint, see the class definition [1](#0-0) .
- When committing, `_commitIncludedChanges` reads the currently selected files straight from the in-memory `WorkingDirectoryStatus` (which carries this stale selection) and passes them to `createCommit`/`stageFiles` [2](#0-1) .
- `stageFiles` routes any file with a partial selection into `applyPatchToIndex` [3](#0-2) .
- `applyPatchToIndex` re-fetches the diff **at staging time** — not the diff the user visually reviewed and selected lines against — and hands it straight to `formatPatch` along with the (older) selection object: [4](#0-3) .
- `formatPatch` builds the actual GNU patch purely by calling `file.selection.isSelected(absoluteIndex)` against the hunks of whatever diff it was given, with no validation that the hunk layout matches what the selection was computed for [5](#0-4) .

This mirrors the "period skipped" bug class exactly: a piece of derived state (the SPL "end of period" value / here, the line-index selection) is computed against one snapshot of underlying data (a period / a diff), but is later reused against a different, updated snapshot without invalidation, because the intermediate update event was silently skipped. Desktop does have a partial safety net for the *actively viewed* file — `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and clears now-invalid lines whenever the diff for the *currently selected* file is reloaded [6](#0-5)  — but that reconciliation only fires through the UI diff-loading flow for one selected file, not for the diff `applyPatchToIndex` independently fetches at commit time for *all* files being staged. There is no re-validation immediately before `formatPatch` is invoked.

### Impact Explanation
This is a "silent corruption of what the user commits" bug: line-level intent (what the user explicitly opted in/out of) can be re-mapped onto unrelated diff hunks and pushed to a remote without any error or warning. In the worst case a user could exclude a sensitive line (e.g. a secret, a debug flag, an unfinished change) and have that same numeric offset match up with different content by the time the patch is built, exposing or omitting the wrong lines in the resulting commit — and since Desktop routinely does background auto-fetch + fast-forward of tracking branches while the app is open, the window for the underlying file layout to shift (via a hook, an editor autosave, or concurrent git operation) while a commit is queued is realistic. This is a correctness/integrity bug in the commit pipeline rather than a memory-safety or RCE bug, so impact is scoped to unintended commit content rather than code execution.

### Likelihood Explanation
The race window is short but not contrived: `_commitIncludedChanges` is only guarded against *other commits* via `withIsCommitting` [7](#0-6) ; it is **not** mutually exclusive with `withPushPullFetch`, which guards fetch/pull/push (including the periodic background fetch that calls `fastForwardBranches`) [8](#0-7) . A background fetch that fast-forwards local branches, or any file-system event that alters the working copy between the diff shown to the user and the moment `applyPatchToIndex` re-reads the file, is enough to trigger the mismatch — no privileged access or social engineering is required, only normal concurrent Desktop operation on an active repository.

### Recommendation
Before calling `formatPatch`, `applyPatchToIndex` should verify that the diff it just fetched is structurally equivalent (same hunk boundaries/line count) to the diff the selection was computed against, and abort/refresh the selection (falling back to "no changes for this file" or re-prompting the user) if it has changed, similar to the staleness checks already used elsewhere in `app-store.ts` (e.g. `updateChangesWorkingDirectoryDiff`'s selectable-lines recomputation). A stronger fix would be to store a content hash/oid of the diff alongside `DiffSelection` and refuse to stage using a selection whose fingerprint no longer matches.

### Proof of Concept
1. Modify a tracked file so it has multiple hunks; open it in Desktop's Changes view and select only specific lines for a partial commit (`DiffSelectionType.Partial`), leaving the commit dialog open but not yet clicking "Commit".
2. While the dialog is open, trigger (or wait for) a background fetch that runs `fastForwardBranches`/updates the working tree indirectly (e.g., via a `post-checkout`/`post-merge` hook that rewrites the same file, or simply edit the file externally to shift line numbers within the same hunk region) so that the on-disk diff shape changes but the file id/path stays the same (so Desktop's `updateChangedFiles` preserves the old `selection` object, per [9](#0-8) ).
3. Click "Commit". `applyPatchToIndex` re-fetches the diff fresh [10](#0-9)  and `formatPatch` applies the old absolute-line-index selection to the new hunk layout.
4. Inspect the resulting commit: it contains different line content than what was visually selected in step 1, with no warning shown to the user.

Note: I was not able to execute this end-to-end in a live Desktop instance (no filesystem/terminal access in this environment), so the PoC is derived from static code-path analysis of the cited functions rather than an observed runtime reproduction — this should be validated dynamically before treating it as confirmed.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L53-84)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L5364-5391)
```typescript
  private async withIsCommitting(
    repository: Repository,
    fn: () => Promise<boolean>
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    // ensure the user doesn't try and commit again
    if (state.isCommitting) {
      return false
    }

    this.repositoryStateCache.update(repository, () => ({
      isCommitting: true,
      hookProgress: null,
      subscribeToCommitOutput: null,
    }))
    this.emitUpdate()

    try {
      return await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isCommitting: false,
        hookProgress: null,
        subscribeToCommitOutput: null,
      }))
      this.emitUpdate()
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L5427-5450)
```typescript
  private async withPushPullFetch(
    repository: Repository,
    fn: () => Promise<void>
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    // Don't allow concurrent network operations.
    if (state.isPushPullFetchInProgress) {
      return
    }

    this.repositoryStateCache.update(repository, () => ({
      isPushPullFetchInProgress: true,
    }))
    this.emitUpdate()

    try {
      await fn()
    } finally {
      this.repositoryStateCache.update(repository, () => ({
        isPushPullFetchInProgress: false,
      }))
      this.emitUpdate()
    }
  }
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

**File:** app/src/lib/patch-formatter.ts (L129-206)
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
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })
```

**File:** app/src/lib/stores/updates/changes-state.ts (L41-61)
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
    })
    .sort((x, y) => caseInsensitiveCompare(x.path, y.path))
```
