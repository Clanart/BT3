## Confirmed Analog: Stale Line-Selection Indices Are Blindly Reapplied to a Freshly-Fetched Working-Directory Diff, Silently Corrupting Partial Commits

### Title
Partial-commit patch generation trusts line-index selections computed against a stale diff snapshot instead of the diff actually being staged - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, `app/src/models/diff/diff-selection.ts`)

### Summary
The vault report's broken invariant is "a value used for a security/financial decision (NAV) is sourced from data that does not correspond to the actual object being valued (spot price vs. this specific perpetual's mark price), and nothing re-validates the correspondence before it is used to move funds." The Desktop analog is structurally identical: `DiffSelection` records which lines a user wants staged as raw numeric **line indices** (`unifiedDiffStart`-relative absolute indices) taken from whatever diff was rendered in the UI at selection time. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) then fetches a **brand-new** diff from disk via `getWorkingDirectoryDiff` and calls `formatPatch(file, diff)` (`app/src/lib/patch-formatter.ts:129-232`), which applies those same numeric indices to the new diff via `file.selection.isSelected(absoluteIndex)` (`app/src/models/diff/diff-selection.ts:122-136`). Nothing ties the index back to the specific line content it was selected against.

### Finding Description
`DiffSelection.isSelected(lineIndex)` is a pure index-based lookup - it has no concept of the actual text of the line, only its position (`app/src/models/diff/diff-selection.ts:122-136`). The indices originate from hunk-relative offsets (`hunk.unifiedDiffStart` + `lineIndex`) computed by the UI against the diff object that was loaded when the user opened/reviewed the file (see `diffHunkForIndex`/`findInteractiveDiffRange` in `app/src/ui/diff/diff-explorer.ts:34-62,129-179`).

When the user actually commits, `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:12-84`) does **not** reuse the diff object the user reviewed. It recomputes a fresh diff from the current on-disk file (`getWorkingDirectoryDiff(repository, file)`, line 60) and immediately feeds that fresh diff plus the old `file.selection` object into `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`), which walks the *new* hunk lines and asks `file.selection.isSelected(absoluteIndex)` whether each new line — identified purely by position — was "selected." There is no check anywhere in `apply.ts`, `patch-formatter.ts`, or `diff-selection.ts` that the new diff's structure (hunk boundaries, line count, line content) matches the diff the indices were derived from. No content hash, mtime, or diff-shape comparison guards this path.

If the working-tree file changes between the moment the user reviews/selects lines in the diff view and the moment they click "Commit" (e.g. an auto-formatter, linter `--fix`, build tool, editor autosave, a file watcher, or any other process that rewrites the file — none of which require elevated privileges, just something already legitimately running while Desktop is open), the hunk boundaries and line offsets shift. The same numeric indices then land on **different lines** in the new diff. `formatPatch` will silently include unrelated added/deleted lines and/or drop lines the user actually intended to stage, because "context lines can always be let through" and unselected/selected status is decided purely by index (`app/src/lib/patch-formatter.ts:143-206`). The resulting patch is applied straight to the index with `git apply --cached` and then committed — this is exactly the class of impact explicitly called out as valid: **"silent corruption of what the user commits or pushes."**

### Impact Explanation
A user reviewing a partial diff and deliberately deselecting sensitive/dangerous lines (e.g. a debug print, a hardcoded secret, or an unwanted change) can end up committing and pushing content they never selected, or omitting content they did select — without any warning. This is analogous to the vault silently using the wrong valuation input: the commit/push output (the "share price" analog — what actually gets recorded in git history) no longer matches what the user reviewed and authorized (the analog of "true mark price"). Because Desktop performs no re-diff-consistency check before formatting/applying the patch, this corruption is silent and undetectable to the user in the UI at commit time.

### Likelihood Explanation
This does not require local/physical access, admin rights, prior malware, leaked credentials, or unnatural user steps — only an ordinary background process (already common in developer workflows: format-on-save, watch-mode builds, linters) mutating a tracked file after the user opens the diff and makes a partial selection but before they press "Commit." Given how common file-watchers/auto-formatters are in real developer setups, and that Desktop's partial-staging flow is a primary, frequently used feature, the race window is realistic, though it is a timing-dependent bug rather than a guaranteed trigger on every commit.

### Recommendation
Before generating the patch in `applyPatchToIndex`, verify that the fresh diff fetched via `getWorkingDirectoryDiff` structurally matches (same hunk headers/line count, or content hash) the diff the `DiffSelection` was built against; if it doesn't match, refuse to apply the stale selection and force the UI to reload the diff and require the user to re-confirm their selection instead of silently mapping old indices onto new content. Alternatively, key `DiffSelection` off of line content/context (or hunk header) rather than raw positional index, so a shifted diff cannot cause silent misapplication.

### Proof of Concept
1. Open GitHub Desktop, modify `foo.txt` with several separate hunks (e.g. lines added at the top and bottom of the file).
2. In the Changes diff view, deselect the added lines in the bottom hunk (keep only the top hunk selected) — this builds a `DiffSelection` with `divergingLines` keyed to indices from the *current* diff.
3. Before clicking "Commit," run/allow a background process (formatter, linter `--fix`, editor autosave) to insert/remove a few lines earlier in the file, shifting subsequent hunk offsets, without touching the file through Desktop's own diff view (so Desktop's cached selection is not invalidated).
4. Click "Commit" (partial selection triggers `stageFiles` → `applyPatchToIndex` in `app/src/lib/git/apply.ts`).
5. Inspect the resulting commit (`git show`): the staged/committed content will not match the lines the user actually selected in step 2 — either the deselected bottom-hunk lines get included, or intended top-hunk lines get dropped/shifted — because `formatPatch` mapped stale absolute line indices onto the newly fetched diff's hunks (`app/src/lib/patch-formatter.ts:143-206`, `app/src/models/diff/diff-selection.ts:122-136`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** app/src/ui/diff/diff-explorer.ts (L34-62)
```typescript
export function diffHunkForIndex(
  hunks: ReadonlyArray<DiffHunk>,
  index: number
): DiffHunk | null {
  const hunk = hunks.find(h => {
    return index >= h.unifiedDiffStart && index <= h.unifiedDiffEnd
  })
  return hunk || null
}

/**
 * Locate the diff line and hunk for the given (absolute) line number in the diff.
 */
function diffLineInfoForIndex(
  hunks: ReadonlyArray<DiffHunk>,
  index: number
): IDiffLineInfo | null {
  const hunk = diffHunkForIndex(hunks, index)
  if (!hunk) {
    return null
  }

  const line = hunk.lines[index - hunk.unifiedDiffStart]
  if (!line) {
    return null
  }

  return { hunk, line }
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
