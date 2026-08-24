### Title
Partial-commit patch generation re-fetches the working-directory diff and blindly re-applies stale line-index selections, allowing a repository-controlled content change to make Desktop stage/commit lines the user never selected - ([File: app/src/lib/git/apply.ts])

### Summary
When a user stages a *subset* of lines/hunks in a modified file ("partial commit"), GitHub Desktop stores the selection as a set of **absolute line indices** (`DiffSelection.divergingLines`) computed against the specific `ITextDiff` object that was rendered in the Changes view. At commit time, however, `applyPatchToIndex` does **not** reuse that diff object — it independently re-fetches a brand-new diff via `getWorkingDirectoryDiff(repository, file)` and then calls `formatPatch(file, diff)`, which reinterprets the *old* index-based selection against the *new* diff's hunk layout. If the on-disk file content changes between the moment the diff was displayed/selected and the moment staging occurs (e.g. because the repository ships a git `clean`/`smudge` filter, a checkout-time hook, or any other repository-controlled mechanism that rewrites the working tree), the recomputed hunk boundaries and `unifiedDiffStart` offsets shift, but the selection's `divergingLines` set is reused verbatim. This is the same class of bug as the BigBang report: a value (`part`, here "which lines are selected") that is valid only relative to one snapshot of shared/mutable state (`totalBorrow` / here, the diff) is later reused against a different, drifted snapshot of that same state, producing an incorrect result that the caller trusts blindly.

### Finding Description
- The UI computes and caches a `DiffSelection` whose semantics ("is line index N included?") only make sense relative to the exact `ITextDiff.hunks` array that was current when the selection was made: [1](#0-0) 
- `formatPatch` walks a **diff object** and, for every line, asks the **selection** whether the line's `absoluteIndex` (`hunk.unifiedDiffStart + lineIndex`) is selected: [2](#0-1) 
- The Changes UI does contain logic that reconciles a *displayed* diff refresh with the current selection by recomputing `selectableLines` and pruning divergent lines that no longer exist — but this reconciliation only happens in `updateChangesWorkingDirectoryDiff`, the code path that refreshes the UI's diff panel: [3](#0-2) 
- Critically, this reconciliation is **not** what runs at commit time. `_commitIncludedChanges` simply passes the `WorkingDirectoryFileChange` objects (carrying whatever `selection` is currently attached, which may be older than the file on disk) straight into `createCommit`: [4](#0-3) 
- `createCommit` → `stageFiles` → `applyPatchToIndex` for any partially-selected file: [5](#0-4) 
- `applyPatchToIndex` re-fetches the diff **independently**, right before formatting the patch, rather than using the diff instance the user actually reviewed: [6](#0-5) 

Because `git apply --cached` is then invoked against the index using a patch built from mismatched (stale-selection, fresh-hunk) data, there is no cross-check that the hunk content/line count the selection was computed for still matches the hunk content/line count actually being patched. `git apply` will either (a) succeed and silently stage a different set of lines than the checkboxes the user ticked, or (b) fail with a context-mismatch error that the user must diagnose — but the dangerous case is (a), a **silent** divergence between what the UI displayed as selected and what actually lands in the commit/staged index.

### Impact Explanation
This falls squarely in the "silent corruption of what the user commits or pushes" impact bucket. A user could carefully review and select only a safe subset of changes to commit (e.g., in a large file where they deliberately excluded certain lines), while a repository-supplied mechanism (checkout/clean/smudge filter, or any process the repo can trigger that rewrites tracked files) alters the working copy in a way that shifts line numbers between diff-render time and commit time. The result: Desktop stages/commits lines the user explicitly chose to exclude (or drops lines they intended to include), and the mismatch is never surfaced to the user — the commit dialog reports success. This can leak unintended content into a commit/push (e.g. secrets or debug code the user deliberately deselected) or drop intended fixes, with no error shown.

### Likelihood Explanation
Likelihood is moderate-to-low but plausible without any local/physical access or social engineering: it requires (1) a partial/line-level selection (a routine, encouraged Desktop workflow) and (2) the working-tree content of that specific file changing between diff load and the "Commit" click — a window that can be widened by normal UI dwell time (the user reviewing/typing a commit message) combined with any content-mutating mechanism defined by the repository (e.g., git attributes-driven filters, editor auto-format-on-save triggered by repo-provided tooling configs, or a background watcher started by opening the repo). No existing guard in the commit path re-validates the selection against the diff actually used to build the patch, so the described mismatch is not caught anywhere before staging.

### Recommendation
- Thread the exact `ITextDiff` instance (or a content hash/fingerprint of the diff, e.g. hash of `diff.text`/hunk structure) that the selection was made against all the way from the UI selection state through `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex`, instead of having `applyPatchToIndex` silently refetch a new diff.
- Before formatting/applying the patch, compare the freshly-fetched diff against the diff the selection was computed for (e.g. via the existing `textDiffEquals`/`selectableLines` machinery already used in `updateChangesWorkingDirectoryDiff`). If they differ, abort the commit for that file and force the UI to re-present the updated diff for re-selection rather than proceeding with a mismatched patch.
- Where a stale diff is unavoidable, fail closed: only allow `git apply --cached` to proceed when hunk headers/line counts match exactly what the selection's `selectableLines` were computed against.

### Proof of Concept
Not independently executable from static analysis alone (would require constructing a repository with a checkout/clean filter or background process timed to rewrite the target file between diff-load and commit), but the mechanism is directly traceable in code:
1. Open a modified file in Desktop's Changes view; the diff is loaded and cached (`selection.diff`), and the user deselects specific lines, producing a `DiffSelection` with `divergingLines` keyed to that diff's hunk `unifiedDiffStart` offsets — see `app/src/lib/stores/app-store.ts:3444-3497` and `app/src/models/diff/diff-selection.ts:205-282`.
2. Before the user presses "Commit," a repository-controlled mechanism (e.g. a `.gitattributes` clean/smudge filter or checkout hook bundled in the cloned repo) rewrites the tracked file on disk, inserting/removing lines and shifting hunk boundaries.
3. On commit, `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3680-3699`) calls `createCommit` → `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/update-index.ts:109-168`, `app/src/lib/git/apply.ts:52-81`), which calls `getWorkingDirectoryDiff` again — fetching the *new*, shifted diff — and passes it to `formatPatch` together with the *old* `file.selection`.
4. `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`) reinterprets the stale `divergingLines` indices against the new hunk layout via `file.selection.isSelected(absoluteIndex)`, producing a patch whose selected/excluded lines no longer correspond to what the user visually reviewed, which `git apply --cached` then applies without any content-consistency check against the original selection intent.

### Citations

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
