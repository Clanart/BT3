This confirms the vulnerability: `applyPatchToIndex` in `app/src/lib/git/apply.ts` re-fetches a fresh diff via `getWorkingDirectoryDiff` right before staging, but applies it against the `file.selection` object whose line-index bitmap was computed against a *previously loaded* diff (built earlier in `updateChangesWorkingDirectoryDiff`, `app/src/lib/stores/app-store.ts:3444`). There is no re-validation that the diff hasn't changed between when the selection was made and when the patch is generated — `formatPatch` blindly maps `file.selection.isSelected(absoluteIndex)` onto whatever hunks/lines the fresh diff produces.

### Title
Stale line-index selection is applied to a freshly re-diffed file, allowing a repo-side content change to silently commit unintended/attacker-influenced lines - ([File: app/src/lib/git/apply.ts])

### Summary
`applyPatchToIndex` re-runs `git diff` against the working tree at staging time (line 60) but reuses the `DiffSelection` bitmap the user built against an earlier diff snapshot rendered by the UI. If the file content on disk changes between the moment the user reviews/selects lines and the moment Desktop stages/commits (e.g., via a clean/smudge filter, a `post-checkout`/`post-merge`/`pre-commit` hook, a background file watcher, or any other process race touching the working directory of a cloned/fetched malicious repository), the line indices no longer correspond to the same content the user approved, yet `formatPatch` (`app/src/lib/patch-formatter.ts:129`) still honors the old bitmap positions against the new hunk layout.

### Finding Description
The commit flow is:
1. `updateChangesWorkingDirectoryDiff` (`app/src/lib/stores/app-store.ts:3404-3465`) loads a diff and stores it plus a `DiffSelection` (line-index bitset) in `IChangesState`.
2. When the user commits, `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681`) passes the selected `WorkingDirectoryFileChange` (carrying that same `DiffSelection`) to `createCommit` → `stageFiles` (`app/src/lib/git/update-index.ts:109`) → `applyPatchToIndex` for partially-selected files.
3. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) calls `getWorkingDirectoryDiff` again, **at staging time**, producing a brand-new `ITextDiff` from the current on-disk content.
4. `formatPatch(file, diff)` (`app/src/lib/patch-formatter.ts:129`) walks the **new** diff's hunks and, for each line, calls `file.selection.isSelected(absoluteIndex)` — an index computed against the **old** diff's layout.

There is no check that the newly fetched diff is structurally equivalent (same hunk boundaries/line counts) to the diff the selection was built from. A repository under attacker influence (e.g., a malicious clean/smudge filter defined in `.gitattributes`, or a hook installed via `core.hooksPath`/repo-provided hook that fires on `git status`/checkout/merge and rewrites tracked files) can shift line offsets in the working tree between the UI's diff render and the commit's re-diff, causing selected/unselected bits to map onto different, attacker-controlled lines. The result: the user believes they are committing lines A–B, but the actual staged patch includes different content chosen by the attacker-influenced file state, and this is done silently — no error, no diff mismatch warning is surfaced at staging time.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" — an explicitly valid impact category. A user could unknowingly commit/push attacker-favorable content (e.g., reintroducing a line the user meant to exclude, or omitting a security check the user meant to include) because the line-selection bitmap is honored against a diff object it wasn one built for.

### Likelihood Explanation
Exploitation requires the attacker to control content of a cloned/fetched repository that is rewritten between diff-render and stage time — realistic via `.gitattributes` clean/smudge filters, `core.hooksPath` hooks, or LFS smudge scripts shipped in the repo, none of which need local/admin access beyond the user opening/using the malicious repo in Desktop, consistent with the "attacker controls a cloned/fetched repository" profile. However, precisely timing a filter/hook to fire exactly in the narrow window between the last diff refresh and `applyPatchToIndex`'s re-diff, and to shift line offsets in a way that lands attacker content on the user's already-selected indices, is non-trivial to engineer reliably, which lowers real-world likelihood despite the mechanism being present in local code.

### Recommendation
Before applying the patch in `applyPatchToIndex`, verify that the diff used to build `file.selection` is still valid against the freshly-fetched diff (e.g., compare hunk headers/line counts, or better, use the `.diff` blob/OID captured at selection-render time rather than re-diffing at commit time), and fail/re-prompt the user rather than silently applying possibly-mismatched selection bits.

### Proof of Concept
1. Attacker publishes a repo with a `.gitattributes` `clean`/`smudge` filter (or a `post-checkout`/hook if hooks are enabled) that, when triggered, rewrites a tracked file's line structure to insert attacker content near a plausible line number.
2. Victim opens the file in Desktop, reviews the diff, and selects specific lines (partial commit) for staging.
3. Before the user clicks Commit, the filter/hook fires (e.g. due to an unrelated `git status`/checkout refresh Desktop performs in the background) and rewrites the file on disk, shifting hunk boundaries.
4. User clicks Commit; `applyPatchToIndex` re-diffs the now-modified file and calls `formatPatch` using the stale `DiffSelection` indices, producing a patch that stages different lines than what the user visually approved.
5. The resulting commit silently contains attacker-influenced content the user never explicitly reviewed/selected. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** app/src/lib/patch-formatter.ts (L129-161)
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
```

**File:** app/src/lib/stores/app-store.ts (L3444-3465)
```typescript
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

```

**File:** app/src/lib/stores/app-store.ts (L3681-3716)
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
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
        },
        { gitContext: { kind: 'commit' }, repository }
      )

      if (result !== undefined) {
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
