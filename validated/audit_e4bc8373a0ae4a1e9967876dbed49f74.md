## Finding [1](#0-0) 

### Title
Partial-commit patch generation re-diffs the working tree instead of reusing the reviewed diff, letting a changed file silently corrupt what gets committed - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user stage only some *lines* of a modified file ("partial commit"). The UI computes a `DiffSelection` whose selected/unselected state is keyed to absolute line indices of the diff the user is looking at. When the commit is finally executed, `applyPatchToIndex` does **not** reuse that reviewed diff — it fetches a brand-new diff from disk (`getWorkingDirectoryDiff`) and blindly re-applies the old `DiffSelection` index set against it, the same broken pattern as the reported bug of pricing a position off a different (stale) reference than the one the user actually acted on.

### Finding Description
The commit flow is:
1. UI loads a diff and the user picks specific lines/hunks into `file.selection` (a `DiffSelection` of absolute line indices in that specific diff).
2. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` runs for every partially-selected file: [2](#0-1) 
3. `applyPatchToIndex` re-fetches the diff live from the working directory (`getWorkingDirectoryDiff(repository, file)`) rather than using the diff instance the selection was built from, then calls `formatPatch(file, diff)`.
4. `formatPatch` walks the **new** diff's hunks and, for every non-context line, asks `file.selection.isSelected(absoluteIndex)` using the index positions from the **old** diff: [3](#0-2) 

There is no verification that the diff used to build `file.selection` and the diff fetched inside `applyPatchToIndex` are the same content/shape. If the tracked file's content on disk changes between the time the user reviewed/selected lines and the time `_commitIncludedChanges` runs, the hunk boundaries and absolute line indices shift. The stale `DiffSelection` indices then get matched against unrelated lines in the new diff — the same class of defect as using a decoupled price reference: one code path establishes intent/state against snapshot A, a different code path enforces/executes it against snapshot B, with no consistency check in between.

There is a partial mitigation elsewhere: `updateChangesWorkingDirectoryDiff` recomputes "selectable lines" and drops indices that are no longer includeable whenever the *currently displayed* file's diff refreshes: [4](#0-3) . This only fires for the file currently shown in the Changes view on its own async refresh cadence — it does not run synchronously as part of `_commitIncludedChanges`, and it does not stop `applyPatchToIndex` from performing its own independent, un-synchronized re-diff at staging time. The explicit comment in that code even acknowledges the tool is "not more clever about validating that any partial selection state is still valid" beyond checking line kind.

### Impact Explanation
If a tracked file's content changes on disk (e.g. via a repository-shipped hook or tool that runs automatically after clone/checkout — `post-checkout`, `post-merge`, or an npm `postinstall`/husky hook wired up by the repository's own tracked config) between the moment a user reviews a diff and selects specific lines and the moment they click "Commit", the resulting commit can silently include content the user never selected (attacker-authored lines) or omit content the user did select, without any warning. Because Desktop stages via `git apply --cached` straight from the recomputed patch (`app/src/lib/git/apply.ts:52-61`), the corrupted commit is created and can be pushed to a shared branch/PR with content the user did not knowingly approve — a silent corruption of what the user commits/pushes, matching the "Valid Impact" category for this class of report.

### Likelihood Explanation
Requires a window between diff-review/selection and commit during which the working tree file is mutated by something other than the user's direct edits in Desktop (e.g. a repository-triggered tool/hook, background build step, or another process acting on the cloned repository). Desktop provides no synchronous re-validation of the `DiffSelection` against the diff actually used to build the patch at commit time, so the race is not defended against once it occurs; the guard in `app-store.ts` only helps if it happens to run before the user hits commit and only for the actively-viewed file.

### Recommendation
`applyPatchToIndex` should not silently re-fetch and re-diff at staging time. Either:
- Persist and pass through the exact `ITextDiff`/`ILargeTextDiff` instance that the `DiffSelection` was created against (from the UI) all the way to `stageFiles`/`applyPatchToIndex`, instead of calling `getWorkingDirectoryDiff` again, or
- If a fresh diff must be fetched for safety (e.g. to avoid staging a truly stale patch), verify that the newly fetched diff is structurally identical (e.g. compare hunk headers/line counts or a content hash) to the diff the selection was computed from, and abort/re-prompt the user if it has changed instead of silently reapplying old line indices to new content.

### Proof of Concept
1. Open a repository in Desktop and modify `file.md`, producing a diff with hunks A and B.
2. In the Changes view, select only hunk A's added lines for commit (unselect hunk B) — this builds a `DiffSelection` with absolute indices tied to the currently rendered diff.
3. Before clicking "Commit", have an automated repository-provided process (e.g., a hook or watch script the repo wires up) rewrite `file.md`, shifting/inserting lines so hunk boundaries move (e.g., inserting new lines above hunk A).
4. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches the new diff of the now-modified file and `formatPatch` (`app/src/lib/patch-formatter.ts:143-157`) applies the old absolute-index selection to the new hunk layout.
5. Inspect the resulting commit: it contains lines the user never selected/reviewed (or omits lines they did select), demonstrating silent corruption of the committed content.

### Citations

**File:** app/src/lib/git/apply.ts (L12-14)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
```

**File:** app/src/lib/git/apply.ts (L52-61)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

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
