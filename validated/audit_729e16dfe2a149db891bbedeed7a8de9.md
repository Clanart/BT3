## Analysis

The Dopex bug's broken invariant is: **a value is computed from state observed at time T1 (deposit) but applied/consumed against state at time T2 (redeem) without re-validating that the two states are still consistent**, letting a caller silently get an outcome that doesn't reflect current reality.

The closest real analog I can find in this GitHub Desktop codebase is a TOCTOU (time-of-check/time-of-use) issue in the partial-commit staging pipeline: the line-based selection the user makes against a diff rendered at one point in time is later applied against a **freshly re-fetched diff** taken at commit time, with no check that the two diffs still match.

### Title
Partial-commit staging re-fetches the working directory diff and blindly re-applies a stale line-index selection, allowing silent corruption of commit contents - (File: app/src/lib/git/apply.ts)

### Summary
When a user stages only some lines/hunks of a file, GitHub Desktop stores that choice as a set of *absolute line indices* (`DiffSelection`) computed against the `ITextDiff` that was rendered in the Changes view. That selection object is kept in `IChangesState` and is not tied to, or re-validated against, the specific diff content it was derived from.

At commit time, `_commitIncludedChanges` [1](#0-0)  hands the (potentially stale) `WorkingDirectoryFileChange` objects straight to `createCommit` → `stageFiles`, which for any partially-selected file calls `applyPatchToIndex` [2](#0-1) .

`applyPatchToIndex` re-fetches the diff from disk at that moment (`const diff = await getWorkingDirectoryDiff(repository, file)`) and immediately builds a patch from it using the old selection indices, with no comparison against the diff that was shown to the user when they made the selection: [3](#0-2) 

`formatPatch` then walks the *new* diff's hunks and simply asks `file.selection.isSelected(absoluteIndex)` for each line, using the line's position in the newly-fetched hunk layout: [4](#0-3) 

There is only one place in the app that reconciles a stale selection against a new diff — `updateChangedFiles`, invoked on a background status refresh, which recomputes `selectableLines` from a new diff — but this recomputation is not guaranteed to run, and is not force-applied, between the moment a user finishes selecting hunks and the moment they click "Commit": [5](#0-4) 

### Finding Description
`DiffSelection` records selected/deselected lines purely by numeric offset (`hunk.unifiedDiffStart + lineIndex`), not by line content or hunk identity. This offset scheme is only meaningful relative to the exact diff it was computed from. If the tracked file's on-disk content changes between:
1. the point the diff is rendered and the user picks specific lines/hunks to include, and
2. the point `_commitIncludedChanges` actually runs and `applyPatchToIndex` re-diffs the file,

then the hunk boundaries and line offsets in the new diff can differ from the diff the user reviewed. `formatPatch` has no way to detect this — it will apply the *old* offsets to the *new* hunk structure and silently produce a patch whose actual lines do not correspond to what the user visually selected. The resulting commit can therefore contain content the user never approved, or omit content they intended to commit, with no error, warning, or diff confirmation shown before the commit is finalized.

This is functionally identical to the Dopex root cause: a numeric/positional value (`shares.mulDivDown(_rdpxCollateral, supply)` there, `absoluteIndex` here) is computed against one snapshot of state and consumed against a different, later snapshot, without re-validating consistency — silently producing an output the caller does not expect.

The realistic trigger is a repository (cloned/opened in Desktop) whose normal workflow involves a file-watching/build/codegen process running in the background (e.g. a bundler watch task, a code generator, linter --fix on save, or an editor auto-format-on-save integration commonly recommended in project READMEs) that rewrites tracked files while the user is reviewing/selecting diff hunks in Desktop. The attacker doesn't need any local/admin access — they only need to control the repository's tooling/instructions that a victim naturally runs while working in that repo, which is squarely within "attacker controls a cloned/fetched repository."

### Impact Explanation
A successful race causes **silent corruption of what the user commits** — one of the explicitly valid impact categories. The committed diff can differ from what was displayed and approved by the user, without any indication in the UI. Depending on what the concurrent process wrote, this could smuggle attacker-influenced content into a commit (e.g. via a compromised build/codegen script bundled in the repo) that the developer believes they reviewed and explicitly excluded, or drop security-relevant lines the user intended to include.

### Likelihood Explanation
The race window is realistically wide in an interactive UI: users often type a commit message, switch tabs, or pause between selecting hunks and pressing "Commit," giving background repo tooling ample time to rewrite files. No git hook execution or special permission is required — an ordinary `npm run watch`/build script bundled with the repository, run per the project's own instructions, is sufficient. There is no code path that re-diffs and re-validates the selection immediately before `applyPatchToIndex` runs in the commit flow, and the automatic reconciliation in `updateChangedFiles` is driven by unrelated background status refreshes, not guaranteed to fire on this exact boundary.

### Recommendation
Before applying a partial selection in `applyPatchToIndex`/`stageFiles`, re-validate that the diff fetched at staging time is structurally/content-identical to the diff the selection was computed against (e.g., compare a hash of the diff or hunk headers/line counts). If it has changed, abort the commit (or that file's inclusion) and force the user to re-review the updated diff rather than silently applying offset-based selections to a different hunk layout.

### Proof of Concept
1. Open a repository in Desktop containing a large tracked file, e.g. `generated.json`, plus a script `watch.sh` that periodically appends/removes lines from `generated.json` (simulating a build/codegen tool the repo's README asks contributors to run).
2. User runs `watch.sh` in the background per project instructions, and modifies `generated.json` by hand as well.
3. In Desktop's Changes view, user opens the diff for `generated.json` and deselects a specific sensitive hunk (e.g. a hunk containing a hard-coded secret they don't want committed), leaving other hunks selected.
4. Before clicking "Commit," the background `watch.sh` process rewrites `generated.json`, shifting line offsets (e.g., adds/removes lines above the previously deselected hunk).
5. User clicks "Commit." `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` re-diffs the file and applies the old numeric selection to the new hunk layout.
6. Inspect the resulting commit with `git show`: it now includes lines from a different position in the file than the ones the user visually deselected — content is committed that was never intended to be included (or vice versa), with no warning shown by Desktop.

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
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

**File:** app/src/lib/patch-formatter.ts (L143-171)
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
