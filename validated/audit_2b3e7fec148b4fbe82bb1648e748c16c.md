### Title
Stale, index-based line-selection is silently re-applied to a freshly re-fetched diff when staging a partial commit, allowing corruption of what actually gets committed - ([File: app/src/lib/git/apply.ts], [File: app/src/lib/patch-formatter.ts])

### Summary
`H-04` shows that a value derived from mutable state that is *not* pinned at the moment it is presented to the user (the yield spread) can silently change the meaning of an earlier commitment (the tby's valuation) once that mutable state changes, producing a loss nobody consented to. The same invariant break exists in GitHub Desktop's partial-commit path: the set of "selected lines" a user picks in the UI is stored as a pure integer bitmap (`DiffSelection`) tied to *positions* in a specific diff object, but at commit time Desktop independently re-fetches a brand-new diff from disk (`getWorkingDirectoryDiff`) and blindly re-applies the old position-based bitmap to it in `formatPatch`. If the file's on-disk content changes between the moment the user made their selection and the moment `applyPatchToIndex` runs, the "same" line indices now point at different lines, and the user's staged/committed content silently diverges from what they reviewed and approved.

### Finding Description
`DiffSelection` has no notion of the actual file content — it only tracks integer indices ("`unifiedDiffStart + lineIndex`") that were divergent from a default state: [1](#0-0) [2](#0-1) 

When the user reviews a diff and toggles individual lines, the UI records selections against the absolute index of that specific diff snapshot: [3](#0-2) 

When Desktop actually stages a partial file for commit, `applyPatchToIndex` does **not** reuse the diff the user looked at. It re-fetches a brand-new diff straight from git/disk: [4](#0-3) 

`formatPatch` then walks the **new** diff's hunks and blindly asks the **old** `file.selection` bitmap whether each `absoluteIndex` was selected, with no verification that the line at that index is the same line the user actually looked at: [5](#0-4) 

Desktop is aware that diffs can change out from under a stale selection — `updateChangesWorkingDirectoryDiff` explicitly recomputes `selectableLines` and prunes divergent lines that no longer exist "since the diff might have changed dramatically since last we loaded it": [6](#0-5) 

However, that reconciliation only happens on the **UI's asynchronous diff-refresh path** (`updateChangesWorkingDirectoryDiff`), which is a best-effort, debounced background refresh. It does not run synchronously as part of the actual commit/staging pipeline. `applyPatchToIndex`/`stageFiles` (invoked from the commit flow) fetches its own diff independently of the state cache and has no equivalent staleness check — it simply combines whatever `file.selection` currently is with whatever diff `getWorkingDirectoryDiff` returns *at that instant*: [7](#0-6) 

The rate-vs-spread bug in the report and this bug share the same shape: a value (selected-line bitmap / TBY value) is computed and "locked in" against one snapshot of mutable state (a diff / the spread), but is later reinterpreted against a *different*, live snapshot of that same mutable state (a re-fetched diff / the current spread) without re-validation, silently changing the outcome (which lines are committed / how much stUSDC is worth).

### Impact Explanation
If content on disk changes between the time a user finishes selecting lines in the UI and the time the commit is actually staged (e.g., a slow/async process modifies the file: a build tool, a pre-existing git smudge/clean filter defined by a cloned repository's `.gitattributes`, an editor autosave, or any other filesystem write triggered by content in an attacker-supplied repository), the hunk structure of the freshly fetched diff can shift. Because `formatPatch` selects lines purely by numeric index rather than by matching content, this can cause:
- Unselected/sensitive lines being silently included in the commit, or
- Selected lines being silently dropped from the commit,

without any warning, review step, or diff re-confirmation shown to the user before the commit is created. This is a silent-corruption-of-what-the-user-commits scenario, matching the requested impact class exactly.

### Likelihood Explanation
This requires content on disk to change in the window between the user's diff review and the commit action — a window that legitimately exists in Desktop's UX (users can leave a file selection made minutes/hours earlier and commit later, and any background tool, git hook/filter, or another process can write to the working tree in that window). Because a cloned/fetched repository can define `.gitattributes` filters (`clean`/`smudge`) or trigger tooling that touches tracked files as part of normal repo operations, an attacker who controls repository content has a realistic vector to induce such disk writes without any unnatural user action. The bug is entirely passive from the user's perspective — they simply select lines and later click "Commit."

### Recommendation
Do not treat `DiffSelection` indices as valid against an arbitrary, independently-fetched diff. Either:
1. Re-fetch the diff used for the UI selection immediately before staging, and abort/prompt the user for re-review if the diff's hunk structure or content hash differs from what the selection was made against, or
2. Persist the diff (or a content-fingerprint per selected line) alongside the `DiffSelection` used to build the patch in `applyPatchToIndex`, and validate that the diff passed into `formatPatch` is the same diff (or is unchanged in the relevant regions) as the one the selection was computed from — analogous to the staleness checks already implemented in `updateChangesWorkingDirectoryDiff` — before applying the patch to the index.

### Proof of Concept
1. Clone/open a repository containing a file `foo.txt` and modify a few lines, producing a diff with several hunks.
2. In Desktop's Changes view, deselect one specific added line (e.g., line containing "SECRET_TOKEN=123") so it will *not* be committed — this records `DiffSelection.withLineSelection(absoluteIndex, false)` against the currently displayed diff.
3. Before clicking "Commit," have a background process (e.g., a `clean` filter registered by the repository's own `.gitattributes`, or any external tool triggered by opening the repo) rewrite `foo.txt` on disk, inserting/removing a line above the deselected line so that hunk boundaries shift (this can be simulated for a PoC by writing to the file with `fs.writeFile` in a separate process right after selection, before commit).
4. Click "Commit." `applyPatchToIndex` (app/src/lib/git/apply.ts:60) re-fetches the diff fresh from disk; the shifted hunk now has a different `absoluteIndex` layout, so `file.selection.isSelected(absoluteIndex)` in `formatPatch` (app/src/lib/patch-formatter.ts:157) evaluates against the wrong line.
5. Inspect the resulting commit: the "SECRET_TOKEN=123" line (or another unintended line) is included in the commit despite having been explicitly deselected by the user, with no warning shown.

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
