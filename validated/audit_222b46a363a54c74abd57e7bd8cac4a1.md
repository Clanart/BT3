### Title
Stale-diff / positional line-selection mismatch in `stageFiles`/`applyPatchToIndex` can silently commit different lines than the ones the user selected - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
`StrategyMainnet`/`StrategyArb`/`StrategyOp` inflated `totalAssets` because the accounting value used to authorize a redemption was computed from stale/uncoverted state instead of the actual redeemable state, and nothing revalidated the two against each other before acting. The GitHub Desktop analog is the *partial-commit* pipeline: the value that decides "what gets written into the commit" (`file.selection`, a set of positional line indices) is captured against one diff snapshot, but `applyPatchToIndex` independently re-fetches a **fresh** diff at commit time and blindly re-applies those positional indices to it. There is no invariant enforced that the diff used to build the patch is the same diff the user actually reviewed and selected lines from.

### Finding Description
`DiffSelection` tracks selected lines purely as line-index integers (`divergingLines: Set<number>`), computed relative to `hunk.unifiedDiffStart` offsets of one particular `IDiff` snapshot: [1](#0-0) [2](#0-1) 

When a partial commit is staged, `stageFiles` calls `applyPatchToIndex(repository, file)` for every file with a partial selection: [3](#0-2) 

`applyPatchToIndex` does **not** use whatever diff the UI last displayed to the user. It re-runs `getWorkingDirectoryDiff` at the moment of commit, producing a brand-new `IDiff`/hunk layout: [4](#0-3) 

That freshly-fetched diff is then handed to `formatPatch(file, diff)`, which walks the **new** hunks and, for every line, computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and asks `file.selection.isSelected(absoluteIndex)` — i.e. it reuses the old positional selection indices against a diff that may have a totally different hunk/line layout: [5](#0-4) 

The only place in the codebase that reconciles a selection against a changed diff (`selectableLines` pruning) runs solely in the UI's diff-viewer refresh path (`updateChangesWorkingDirectoryDiff`), which is triggered by file selection, not by the commit action itself: [6](#0-5) 

That reconciliation is best-effort and explicitly acknowledged as incomplete ("we'll settle on just updating the selectable lines… isn't still selected" — it does not verify the *content* at each surviving index is unchanged), and, critically, it is never invoked as part of `_commitChanges`/`createCommit`/`stageFiles`/`applyPatchToIndex`. So even the partial safety net doesn't protect the actual git-apply code path.

The broken invariant: **"the set of selected line indices in `file.selection` refers to the same diff object used to build the patch."** Nothing in `applyPatchToIndex` → `formatPatch` checks this. If the working-tree content for that file changes between the time the user made their line selection and the moment `createCommit` runs `stageFiles`, the positional indices are silently reinterpreted against different content, additions/deletions/context lines shift, and the resulting patch (and therefore the commit) can contain a different set of changes than what the user saw and explicitly selected/deselected in the UI — without any error, warning, or diff re-confirmation.

### Impact Explanation
This is a silent-corruption-of-commit-content bug, matching the required impact class "silent corruption of what the user commits or pushes":
- The user believes they excluded specific lines (e.g. a secret, a debug statement, or unrelated hunk) from the commit, but because the diff shifted between review and commit, those lines can be included anyway (or vice versa — intended lines silently dropped).
- Because `formatPatch` operates purely on index arithmetic with no content-equality check, there is no validation error raised — the patch applies cleanly and the wrong commit is produced.
- The window between diff-generation for selection UI and the second independent diff-generation in `applyPatchToIndex` is entirely uncontrolled by Desktop and can be widened by anything that touches the working tree in that interval (build tools, linters/formatters-on-save, editors with auto-save, LFS/clean-smudge re-materialization triggered by another git operation running concurrently, etc.), none of which require the user to do anything "unnatural" — this is the ordinary "edit file, glance at diff, click Commit" workflow.

### Likelihood Explanation
Moderate. It requires the working copy of the selected file to change between the diff snapshot the selection UI captured and the moment `applyPatchToIndex` re-fetches the diff during `stageFiles`. This is entirely plausible in real developer workflows (auto-formatters, file watchers, build systems, or another tool writing to the file), and Desktop provides zero user-visible signal or reconciliation at the commit boundary, so any occurrence is silent rather than surfaced as a conflict or error.

### Recommendation
- Persist and reuse the exact `IDiff` object (with its hunk boundaries) that was used to build the current `DiffSelection` state, and pass that same diff through to `applyPatchToIndex`/`formatPatch` rather than re-fetching a new diff at commit time.
- If re-fetching is unavoidable (e.g., to guard against other race conditions), diff the newly-fetched hunks against the diff that produced the selection and abort/re-prompt the user (similar to a merge conflict) if the underlying content has changed rather than silently reapplying positional indices.
- At minimum, before calling `formatPatch` in `applyPatchToIndex`, verify that the `diff.text`/hash matches the diff that generated `file.selection`, and fail loudly (surface an error to the user to re-review the diff) if it doesn't.

### Proof of Concept
Conceptual reproduction based on the code paths above (not independently executed in this session — flagged as unverified dynamic behavior):
1. Open a repository in Desktop, modify a tracked file with two changes (hunk A, hunk B).
2. In the Changes view, select only hunk A's lines for the commit (deselect hunk B) — this computes a `DiffSelection` with positional indices against the diff snapshot at that time (`app/src/lib/stores/app-store.ts:3404-3497`).
3. Before clicking "Commit", have an external process (editor autosave, formatter-on-save, file watcher) modify the file such that hunk B's line count/position shifts (e.g., inserts/removes lines above hunk B), while conceptually "reactivating" hunk A's line count.
4. Click "Commit". `stageFiles` → `applyPatchToIndex` re-fetches the diff fresh (`app/src/lib/git/apply.ts:60`) and calls `formatPatch(file, diff)` (`app/src/lib/patch-formatter.ts:129-171`) using the *old* selection indices against the *new* hunk layout.
5. Inspect the resulting commit: it can include/exclude lines that do not correspond to what was visually selected in step 2, with no warning surfaced to the user. [7](#0-6) [8](#0-7) [3](#0-2)

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
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
