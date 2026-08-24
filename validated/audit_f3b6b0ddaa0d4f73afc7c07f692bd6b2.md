## Title
Commit built from a stale line-selection bitmap applied against a freshly-recomputed diff, causing silent corruption of partially-committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user commit a *subset* of the lines in a modified file. The subset is represented as a `DiffSelection` bitmap keyed by the **absolute line index** of a specific `IRawDiff`/`ITextDiff` that was rendered in the Changes view. When the commit is actually created, Desktop does not reuse that same diff object — it re-fetches a brand-new diff from disk/git and replays the old bitmap against the new hunk structure. If the two diffs disagree on line offsets (because the working file content changed between the two computations), the wrong lines are staged and committed, with no error, warning, or re-validation.

### Finding Description
The UI selection state is produced against a diff loaded once and cached in `changesState.selection.diff`. Line selection indices are absolute offsets into that specific diff's hunks (`hunk.unifiedDiffStart + lineIndex`), as seen in the reconciliation logic in `app-store.ts`, which explicitly acknowledges that a diff "might have changed dramatically since last we loaded it": [1](#0-0) 

That reconciliation (`updateChangesWorkingDirectoryDiff`) only runs for the *currently selected/displayed* file, and only refreshes `selectableLines`/pruning — it does not re-validate every working-directory file whose selection was computed against an older diff snapshot.

When the user actually commits, `_commitIncludedChanges` takes the raw, possibly-stale `WorkingDirectoryFileChange.selection` objects straight from `changesState.workingDirectory.files` and hands them to `createCommit`: [2](#0-1) 

`createCommit` → `stageFiles` → `applyPatchToIndex` then **re-fetches a brand-new diff** for the file at commit time, independent of whatever diff produced the stored selection: [3](#0-2) 

That new diff is fed straight into `formatPatch`, which blindly reuses the *old* selection bitmap against the *new* hunk's absolute line indices: [4](#0-3) 

There is no verification that the diff passed to `formatPatch` is the same diff (or even structurally equivalent) to the one the selection bitmap was built from. This is exactly the same class of bug as the reported RToken issue: two code paths that are supposed to operate on the identical "conversion" data (here, the mapping from selection bits to concrete file lines) instead pull from two different sources — one from the cached UI diff, one from a freshly recomputed diff — and nothing enforces that they stay in sync.

### Impact Explanation
If the working file's content shifts between when the selection was made and when `applyPatchToIndex` recomputes the diff (e.g., the file is touched by a background tool, an editor autosave, a git filter/smudge step, or line-ending normalization triggered by `core.autocrlf`/`.gitattributes` from a freshly checked-out/fetched branch), the absolute line indices in the stored selection no longer point at the lines the user actually clicked. `formatPatch` will then stage/commit hunks that do not correspond to the user's intended selection — silently including content the user deliberately excluded, or excluding content the user wanted committed. This is a direct "silent corruption of what the user commits or pushes," matching the accepted impact category, without any indication to the user that the applied patch diverged from what was shown on screen.

### Likelihood Explanation
Desktop performs several operations between the time a diff is rendered and the time a commit is executed — status refreshes, background diff loading for other files, fetches/checkouts that can change `.gitattributes`-driven line-ending behavior, and file-watcher-triggered updates — none of which force re-validation of every file's stored selection bitmap against the diff that will actually be used at commit time. The reconciliation guard in `updateChangesWorkingDirectoryDiff` (`app-store.ts:3478-3497`) only covers the single file currently displayed in the Changes view, leaving all other files with partial selections vulnerable to this staleness window. Because `formatPatch`/`applyPatchToIndex` re-fetch the diff independently rather than threading through the exact diff object the selection was validated against, no additional trust boundary or guard prevents the mismatch once it occurs.

### Recommendation
Ensure the diff object used to build the commit patch (`formatPatch` in `app/src/lib/git/apply.ts` / `app/src/lib/patch-formatter.ts`) is either (a) the exact same diff instance that produced the stored `DiffSelection`, tagged with an identity/hash that is checked before use, or (b) re-validated at commit time by recomputing `selectableLines` against the fresh diff and rejecting/re-prompting when the previously selected line indices no longer map onto equivalent content, mirroring the safety check already present in `updateChangesWorkingDirectoryDiff` but applied to every file being committed, not just the currently displayed one.

### Proof of Concept
1. Modify a tracked file so it has multiple hunks; open it in the Changes view so Desktop loads and caches a diff (`D1`) and let the user select only specific lines from hunk 2 (bitmap keyed on `D1`'s absolute line indices).
2. Without reselecting/reviewing the file in Desktop, externally rewrite the file on disk (e.g., an editor autosave, a background formatter, or a line-ending normalization step) such that hunk boundaries or line counts shift, producing diff `D2` on next read — while leaving the file still selected for partial commit in the UI's cached state.
3. Click "Commit" without re-opening the file's diff. `_commitIncludedChanges` (`app-store.ts:3681`) passes the stale selection to `createCommit` → `stageFiles` → `applyPatchToIndex` (`apply.ts:60`), which fetches `D2` fresh and calls `formatPatch(file, D2)` (`patch-formatter.ts:129`) using the bitmap computed against `D1`.
4. Inspect the resulting commit: it contains different lines than what was visually checked/unchecked by the user in the last diff view they inspected, demonstrating silent corruption of the committed content.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3681-3690)
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
