## Analysis

The Maple report's core issue is a **broken invariant between two different "views" of the same state used at different times** (deposit-time share price vs. redeem-time share price), letting an attacker exploit the window where one view is stale relative to the other, causing the victim to commit to an action based on data that no longer matches reality.

The closest verified analog in GitHub Desktop is the **partial-commit patch generation flow**, where the UI computes a diff once and lets the user select individual lines by *absolute line index*, but the actual patch applied to the git index at commit time is built from a **freshly re-fetched diff** of the working directory — not the diff object the user actually reviewed and selected against.

### Title
Partial-commit selection is applied against a re-fetched diff, allowing committed content to silently diverge from what the user reviewed - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
When a user stages a partial selection of lines for commit, Desktop stores the selection as a set of **absolute line indices** relative to the diff that was rendered in the UI at review time. At commit time, `applyPatchToIndex` does not reuse that reviewed diff; it re-invokes `getWorkingDirectoryDiff` to fetch a brand-new diff from disk and then reconstructs the patch by indexing into the *new* hunks with the *old* line-index selection. [1](#0-0) 

### Finding Description
`formatPatch` builds the patch to `git apply --cached` purely from `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is `hunk.unifiedDiffStart + lineIndex` computed from whatever diff is passed in: [2](#0-1) 

`applyPatchToIndex` (used by `stageFiles` for every partially-selected file at commit time) fetches the diff itself, independent of whatever diff the renderer/user last saw: [3](#0-2) [4](#0-3) 

The app store even documents this exact hazard for diff *rendering* (adjusting selectable lines when a reload happens) but this hazard is not addressed for the *commit* codepath: `app-store.ts` explicitly acknowledges "The diff might have changed dramatically since last we loaded it" and attempts to patch up the selection state used for rendering — but that reconciliation only happens on an explicit diff reload while the user is looking at the Changes view, not immediately before `_commitIncludedChanges`/`stageFiles` runs: [5](#0-4) 

`_commitIncludedChanges` takes the `WorkingDirectoryFileChange` objects straight from cached repository state (with their UI-set `selection`) and hands them to `createCommit` → `stageFiles` → `applyPatchToIndex`, with no re-validation that the file content on disk still matches what the selection was computed against: [6](#0-5) 

If the working-directory file content changes between the moment the user reviews the diff/selects lines and the moment `_commitIncludedChanges` executes (e.g. a `smudge`/`clean` filter defined in a malicious repo's `.gitattributes`, or an external tool/background process touching the file), the hunk boundaries and `unifiedDiffStart` offsets recomputed by `getWorkingDirectoryDiff` can shift. The same absolute index the user "checked" in the UI can now land on a different line of different content, causing Desktop to stage/commit lines the user never reviewed or explicitly deselected — while the UI shows the commit as having succeeded with the message the user typed for the reviewed diff. This is analogous to the Maple bug: an operation (deposit / partial-commit) is validated against one snapshot of state (share price / diff+selection), but executed against a different, possibly attacker-influenced, snapshot (post-loss share price / re-fetched diff), and the discrepancy silently corrupts the outcome for the user.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" — an explicitly valid impact category. A victim could unknowingly commit and push content different from what they reviewed and approved via line-level selection, which is a serious integrity issue for code review and audit trails (e.g., committing secrets, backdoored code, or reverting a security fix that the user believed they excluded).

### Likelihood Explanation
Exploitation requires the attacker to control content executed as part of git's filter pipeline (`.gitattributes` `filter=`/`clean`/`smudge`) or otherwise cause the working tree file to change between diff review and the click of "Commit" — a window that is normally short but is not bounded or re-validated by Desktop. This requires a git operation (`checkout-index`, `apply`, `add`) to trigger the filter on the same file in that window, which is a non-trivial but plausible attacker-influenced condition rather than a guaranteed exploit, hence a moderate rather than high likelihood.

### Recommendation
Before applying a partial-selection patch at commit time, Desktop should either: (1) reuse the exact diff object that the selection was computed against (persisting hunk content, not just indices, alongside the selection) and detect/reject staleness if the file's on-disk content or mtime has changed since the diff was generated, or (2) re-diff immediately before staging and re-validate that the previously selected line content is unchanged, aborting/warning the user if it has. This mirrors how `app-store.ts` already reconciles selectable lines on diff reload — that same reconciliation needs to be enforced immediately before `stageFiles`/`applyPatchToIndex` runs, not just when the UI happens to reload the diff.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` entry with a `filter=evil` `clean`/`smudge` filter (`git config filter.evil.smudge`/`clean` configured via a tracked `.git/config`-adjacent mechanism the victim is tricked into trusting, or via a submodule hook) that rewrites a tracked file's content when git touches it.
2. Victim opens the file in Desktop, reviews the diff, and deselects a sensitive line (e.g. a credential rollback) via `onLineNumberCheckedChanged` (`app/src/ui/diff/side-by-side-diff.tsx`). [7](#0-6) 
3. Before the victim clicks "Commit," the filter fires (e.g., due to a background `git status`/`add` triggered by another Desktop feature, or a race with an external tool), rewriting the file so the git-recomputed diff's hunk boundaries shift.
4. Victim clicks "Commit." `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` re-fetches the diff and applies the victim's old absolute-index selection against the new hunks, staging/committing the sensitive line the victim believed was excluded. [8](#0-7)

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

**File:** app/src/lib/patch-formatter.ts (L129-171)
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

**File:** app/src/lib/stores/app-store.ts (L3680-3712)
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
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L935-961)
```typescript
  private onLineNumberCheckedChanged = (
    row: number,
    column: DiffColumn,
    isSelected: boolean
  ) => {
    if (this.props.onIncludeChanged === undefined) {
      return
    }

    let selection = this.getSelection()
    if (selection === undefined) {
      return
    }

    const lineBefore = this.getDiffLineNumber(row, column)
    const lineAfter = this.getDiffLineNumber(row, column)

    if (lineBefore !== null) {
      selection = selection.withLineSelection(lineBefore, isSelected)
    }

    if (lineAfter !== null) {
      selection = selection.withLineSelection(lineAfter, isSelected)
    }

    this.props.onIncludeChanged(selection)
  }
```
