This confirms the analog: `_commitIncludedChanges` (`app-store.ts:3681`) passes the `WorkingDirectoryFileChange` objects — carrying whatever `DiffSelection` the UI last computed against a previously-rendered diff — straight through `createCommit` → `stageFiles` → `applyPatchToIndex`, which re-fetches a brand-new diff from disk at staging time and applies the old selection's line indices to it, with no re-validation that the indices still mean what the user saw. [1](#0-0) 

### Title
Stale line-index `DiffSelection` applied against a freshly re-fetched diff during partial commit staging silently commits unintended content - (File: app/src/lib/git/apply.ts)

### Summary
`DiffSelection` tracks which lines a user has chosen to stage using bare integer indices (`unifiedDiffStart + lineIndex`) computed against whatever `ITextDiff` was rendered in the UI at selection time. When staging occurs, `applyPatchToIndex` does not reuse that diff — it calls `getWorkingDirectoryDiff(repository, file)` again to obtain a **new** diff and then applies the stale selection's indices to it via `formatPatch`. If the file content on disk has shifted between when the user made their selection and when the commit is executed, the same integer index in the new diff refers to a different line than the one the user actually selected.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts:41-231`) is an index-based selection object with no notion of diff identity or content — it only stores `divergingLines: Set<number>`. [2](#0-1) 

`formatPatch` builds the patch to stage purely by checking `file.selection.isSelected(absoluteIndex)` where `absoluteIndex` is derived from the diff hunks passed in: [3](#0-2) 

`applyPatchToIndex` is the function invoked at commit time for any file with a partial selection. It explicitly re-fetches the diff from git rather than using the diff the user viewed, then feeds that fresh diff plus the (unrelated-in-time) `file.selection` into `formatPatch`: [4](#0-3) 

`stageFiles` (called from `createCommit`) routes any file whose selection is `Partial` through this exact path: [5](#0-4) 

The commit flow itself, `_commitIncludedChanges`, takes the working-directory files with whatever selection is currently attached and passes them straight to `createCommit` with no intermediate step that confirms the diff hasn't changed since the user last saw it: [6](#0-5) 

Existing protection only exists for the *display* path: `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and drops now-invalid indices when the diff is *re-rendered in the UI*. [7](#0-6) 
But this reconciliation only runs when the UI happens to reload the diff for the currently selected file; it is not run as a synchronous, guaranteed precondition of `applyPatchToIndex`, which independently re-diffs at staging time. There is no cross-check between the diff instance the user selected lines against and the diff instance `applyPatchToIndex` fetches moments later.

The comment in `formatPatch` even acknowledges the class of bug without treating it as unsafe: "If we get into this state we should never have been called in the first place. Someone gave us a faulty diff and/or faulty selection state." [8](#0-7) 

This is the same broken invariant as the `RubiconMarket` report: selection state (`_rank`/`_best` there, `DiffSelection` here) is created under one "mode" (a specific diff snapshot) and is later reused under a different mode (a newer diff snapshot) without validating that the indices are still meaningful, because the code assumes the state and the object it indexes into always stay in lockstep.

### Impact Explanation
If a tracked file is modified on disk between the time the user builds a partial line selection and the time Desktop stages/commits it, the integer line indices in `DiffSelection` can now point at different lines in the freshly-fetched diff. This causes the generated patch to include lines the user never selected and/or exclude lines the user did select — a silent corruption of what is actually staged and committed, without any error or warning to the user. Because git hunk boundaries shift on any insertion/deletion earlier in the file, even a single-line change elsewhere in the file can misalign every subsequent index.

An attacker who controls content that gets written into the working directory during the review-to-commit window (e.g., via a build/watch script bundled in the repository, a git smudge/clean filter, a pre-commit hook that mutates tracked files, or a CI/tooling process the user is instructed to run against the cloned repo) can cause the user's next partial commit to include or exclude different lines than what was visually reviewed and approved — meeting the "silent corruption of what the user commits or pushes" criterion.

### Likelihood Explanation
This requires only ordinary developer workflow: making a partial (line-by-line) selection in the Changes view, then committing, while the file is externally modified in between (a realistic scenario in repos with watch/build tooling, linters/formatters running on save, or git hooks/filters checked into the repository). No admin rights, local malware, or unnatural user steps are needed — the corrupting write can originate entirely from repository-supplied tooling the user is naturally expected to run.

### Recommendation
Before staging, `applyPatchToIndex`/`stageFiles` should validate that the diff used to build the patch is the same one the selection was computed against (e.g., by diffing content hashes or by re-deriving the diff synchronously right before building the `DiffSelection`, not independently re-fetching it later). At minimum, detect when the newly-fetched diff differs from the diff last shown to the user for that file and either abort the partial-stage with an error, or force a full-file stage/re-selection rather than silently applying stale indices to new content.

### Proof of Concept
1. Modify a tracked file so it has multiple hunks (e.g., unrelated changes near the top and bottom of the file).
2. In the Changes view, select for inclusion only specific lines in the second hunk (creating a `Partial` `DiffSelection` keyed to the current diff's line indices), as done in the equivalent test scenario: [9](#0-8) 
3. Before clicking Commit, have an external process (e.g., a watch script from the repo's `package.json`, or a pre-commit hook) insert/remove lines earlier in the same file, shifting all subsequent line numbers.
4. Trigger the commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff (now shifted) and applies the old `DiffSelection` indices to it via `formatPatch`, producing a patch that stages different lines than the ones the user visually selected — resulting in a commit whose content silently diverges from user intent.

### Citations

**File:** app/src/lib/git/apply.ts (L12-81)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
): Promise<void> {
  // If the file was a rename we have to recreate that rename since we've
  // just blown away the index. Think of this block of weird looking commands
  // as running `git mv`.
  if (file.status.kind === AppFileStatusKind.Renamed) {
    // Make sure the index knows of the removed file. We could use
    // update-index --force-remove here but we're not since it's
    // possible that someone staged a rename and then recreated the
    // original file and we don't have any guarantees for in which order
    // partial stages vs full-file stages happen. By using git add the
    // worst that could happen is that we re-stage a file already staged
    // by updateIndex.
    await git(
      ['add', '--update', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    // Figure out the blob oid of the removed file
    // <mode> SP <type> SP <object> TAB <file>
    const oldFile = await git(
      ['ls-tree', 'HEAD', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    const [info] = oldFile.stdout.split('\t', 1)
    const [mode, , oid] = info.split(' ', 3)

    // Add the old file blob to the index under the new name
    await git(
      ['update-index', '--add', '--cacheinfo', mode, oid, file.path],
      repository.path,
      'applyPatchToIndex'
    )
  }

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

**File:** app/src/lib/patch-formatter.ts (L222-227)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
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

**File:** app/test/unit/git/commit-test.ts (L307-332)
```typescript
    it('can commit multiple hunks from modified file', async t => {
      const testRepoPath = await setupFixtureRepository(t, 'repo-with-changes')
      const repository = new Repository(testRepoPath, -1, null, false)

      const previousTip = (await getCommits(repository, 'HEAD', 1))[0]

      const modifiedFile = 'modified-file.md'

      const unselectedFile = DiffSelection.fromInitialSelection(
        DiffSelectionType.None
      )
      const file = new WorkingDirectoryFileChange(
        modifiedFile,
        { kind: AppFileStatusKind.Modified },
        unselectedFile
      )

      const diff = await getTextDiff(repository, file)

      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.All
      ).withRangeSelection(
        diff.hunks[1].unifiedDiffStart,
        diff.hunks[1].unifiedDiffEnd - diff.hunks[1].unifiedDiffStart,
        false
      )
```
