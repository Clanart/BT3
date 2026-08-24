## Title
Stale line-index selection applied against a freshly re-fetched diff can silently commit unintended content - (File: `app/src/lib/git/apply.ts`)

## Summary
The origination-fee report's broken invariant is: a value is pre-computed against state S0 (fee rate at loan-creation time), then used unconditionally against state S1 (fee rate at lend time) without revalidation, producing an outcome the user never agreed to. The GitHub Desktop analog is `applyPatchToIndex`, which builds a commit patch by applying a user's line-index-based selection (computed against the diff the UI rendered, S0) to a diff that is *re-fetched from disk* at staging time (S1), with no check that the two diffs still match line-for-line.

## Finding Description
When a user partially selects lines to commit, the selection is stored as a `DiffSelection` keyed by absolute line index within a specific diff object [1](#0-0) . At commit time, `stageFiles` calls `applyPatchToIndex` for every partially-selected file [2](#0-1) .

Critically, `applyPatchToIndex` does **not** reuse the diff the user looked at in the UI. It re-fetches a brand-new diff from disk/git right before formatting the patch: [3](#0-2) 

`formatPatch` then walks this freshly-fetched diff and decides which lines to include purely by absolute index, using the *old* `file.selection` object: `file.selection.isSelected(absoluteIndex)` [1](#0-0) . There is no check that the new diff's hunks/line layout still corresponds to the layout the selection indices were computed against.

Desktop's own codebase acknowledges this exact class of staleness elsewhere, but only in the UI-refresh path, not in the commit/staging path: `updateChangesWorkingDirectoryDiff` explicitly notes "The diff might have changed dramatically since last we loaded it" and recomputes `selectableLines` before re-applying the old selection [4](#0-3) . This remapping is invoked only when the app proactively reloads the diff for display; it is not invoked as part of the commit/staging pipeline (`stageFiles` → `applyPatchToIndex`), which instead trusts the raw absolute indices against a diff it fetches for itself.

Content on disk (and therefore the diff) can change between the moment the user finishes selecting lines and the moment "Commit" is pressed via mechanisms fully within a cloned/fetched repository's control, e.g. non-deterministic or state-dependent Git `clean`/`smudge` filters declared in `.gitattributes`, or CRLF/`core.autocrlf` normalization differences triggered by filters bundled with the repo. Because `getWorkingDirectoryDiff` is invoked twice (once for display, once inside `applyPatchToIndex`) and filters run on each invocation, a filter that emits different output on subsequent runs (or reacts to file mtime/git index state) causes the second diff's hunk boundaries and absolute line indices to diverge from the first, while the stale `DiffSelection` is still applied against the new indices in `formatPatch`.

## Impact Explanation
This falls under "silent corruption of what the user commits or pushes." If the re-fetched diff has different hunk boundaries or a different number of lines than the diff the user selected against, `isSelected(absoluteIndex)` will resolve to different, unintended lines — including lines the user explicitly deselected, or excluding lines they intended to commit. The user is given no error or warning; the commit is created and can be pushed with content they never reviewed or approved (e.g. secrets, partially-reverted debug code, or attacker-influenced content emitted by a malicious repository-provided filter). Existing guards — the empty-patch check in `formatPatch` [5](#0-4)  and the deliberate selectable-line remap in `updateChangesWorkingDirectoryDiff` [4](#0-3)  — do not run inside the `stageFiles`/`applyPatchToIndex` code path, so they cannot catch this case.

## Likelihood Explanation
Exploitation requires a cloned/fetched repository to ship a `.gitattributes` filter or content-normalization rule whose output is not stable across repeated invocations of `git diff`/`git show :file` for the working tree (or that reacts to timing/index state), and requires the user to make a partial (line-level) commit rather than a whole-file commit. This is a real but narrower attack surface than a fully generic TOCTOU — it depends on Desktop invoking diff generation twice for the same commit operation without caching or diffing the two results, which is a genuine, verifiable code-level gap. I was not able to fully trace the exact caller (`_commitIncludedChanges`/`performCommit`) that invokes `stageFiles` before running out of tool budget, so I cannot state with certainty whether any additional validation exists between UI selection and `stageFiles` invocation; this should be verified directly in the code before treating the issue as fully confirmed.

## Recommendation
Cache and reuse the exact diff object that was used to render the selection UI when generating the staging patch, instead of re-fetching a new diff inside `applyPatchToIndex`. If a fresh diff must be fetched (e.g. to be resilient to concurrent external changes), it should be structurally compared against the diff the selection was computed from (same hunk count/boundaries/line count) and staging should be aborted or the user re-prompted if they differ — mirroring the remapping logic already implemented in `updateChangesWorkingDirectoryDiff`.

## Proof of Concept
Exact PoC execution could not be completed within the available tool budget; the concrete code path establishing the primitive is:
1. `getWorkingDirectoryDiff` is called once to render the UI diff, and the user builds a `DiffSelection` keyed to that diff's absolute line indices [1](#0-0) .
2. On commit, `stageFiles` → `applyPatchToIndex` calls `getWorkingDirectoryDiff` again [6](#0-5)  and formats a patch using the old selection against the new diff’s line layout without any structural equivalence check, then applies it via `git apply --cached` [3](#0-2) .
A full working exploit would require constructing a repository with a Git `clean`/`smudge` filter that yields different output on the two invocations of the diff and confirming the resulting staged content diverges from what the UI displayed — this last verification step is what remains unconfirmed.

### Citations

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
