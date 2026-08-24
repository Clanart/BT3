## Title
Partial-commit staging applies stale line-selection indices to a freshly re-read diff, allowing silent inclusion/exclusion of unreviewed content - (File: `app/src/lib/git/apply.ts`)

## Summary
GitHub Desktop lets a user stage a subset of lines from a file (`DiffSelectionType.Partial`). The line selection is index-based and is computed by the renderer against the diff that was displayed in the "Changes" view at the time the user clicked individual lines/hunks. When the commit is actually created, `applyPatchToIndex` re-fetches the diff from disk (`getWorkingDirectoryDiff`) instead of reusing the diff the selection was made against, and then blindly applies the old `file.selection` line indices to this newly-fetched diff via `formatPatch`. If the on-disk content changed between the moment the diff was rendered/selected and the moment the commit executes, the indices no longer correspond to the same lines, so the wrong hunks/lines are silently staged and committed - without any re-validation or error to the user.

## Finding Description
The staging pipeline for partial file selections is:

1. `stageFiles` in `app/src/lib/git/update-index.ts:109-168` separates files into "normal" and "partial", then for every partially-selected file calls `applyPatchToIndex(repository, file)`. [1](#0-0) 

2. `applyPatchToIndex` (`app/src/lib/git/apply.ts:12-84`) does **not** take the diff the UI displayed to the user as input. Instead it re-derives the diff from disk right there:
```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
``` [2](#0-1) 

3. `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`) walks the (fresh) diff's hunks and, for each line, decides whether to include it purely by calling `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is `hunk.unifiedDiffStart + lineIndex` from the *current* diff, but `file.selection` was populated by the renderer against a *previous* diff snapshot:
```
hunk.lines.forEach((line, lineIndex) => {
  const absoluteIndex = hunk.unifiedDiffStart + lineIndex
  ...
  } else if (file.selection.isSelected(absoluteIndex)) {
``` [3](#0-2) 

4. `WorkingDirectoryFileChange.selection` is created and cached in Redux-like app state and is only invalidated/rebuilt when a new `IStatusResult` is loaded (`updateChangedFiles` in `app/src/lib/stores/updates/changes-state.ts`), which is a background/periodic or user-triggered operation, not something guaranteed to run immediately before `_commitIncludedChanges` executes:
```
return file.withSelection(existingFile.selection)
``` [4](#0-3) 

5. When the user commits, `_commitIncludedChanges` reads the selection straight from the (potentially stale relative to disk) `repositoryStateCache` and passes those `WorkingDirectoryFileChange` objects — carrying old index-based `selection` — into `createCommit` → `stageFiles` → `applyPatchToIndex`:
```
const state = this.repositoryStateCache.get(repository)
const files = state.changesState.workingDirectory.files
const selectedFiles = files.filter(file => file.selection.getSelectionType() !== DiffSelectionType.None)
...
return createCommit(repository, message, selectedFiles, {...})
``` [5](#0-4) 

This is structurally the same class of bug as the Malt `_distributeProfit` finding: a security/consistency-relevant computation (`isSelected(absoluteIndex)` here, `swingTraderCollateralDeficit()` there) combines one piece of freshly-read state (the newly fetched diff) with another piece of state that is derived from an earlier snapshot (the cached selection indices) and never re-synchronized before use, so the two no longer describe the same reality. Neither `applyPatchToIndex` nor `formatPatch` verifies that the diff it just fetched still matches the diff the selection was computed from (e.g. by comparing hunk headers, file OIDs, or a content hash) before trusting the index mapping.

The developers were aware selection/diff mismatches are possible but only guard against the degenerate "no lines selected at all" case, not a genuine hunk-shift mismatch:
```
if (!patch.length) {
  log.debug(`formatPatch: empty path for ${file.path}`)
  throw new Error(`Could not generate a patch, no changes`)
}
``` [6](#0-5) 

## Impact Explanation
This falls under "silent corruption of what the user commits" from the impact list. Concretely, an attacker who can cause the working tree to change between the time a diff is rendered/selected in the UI and the time the user presses "Commit" (for example via a build tool/watcher, a smudge/clean filter, a merge/rebase continuation, or any tracked script the repository induces the user to run while Desktop is open) can cause GitHub Desktop to silently commit lines the user never reviewed or selected, or drop lines the user intended to keep, while the UI still displays the old, "safe-looking" diff. This can be used to smuggle unintended/malicious changes into a commit that a user believes only contains the reviewed content, undermining the core trust guarantee of partial/line-level staging in the app.

## Likelihood Explanation
Moderate. The renderer keeps selection state alive across background refreshes as long as the file id doesn't change (`file.withSelection(existingFile.selection)`), so a partial selection can persist across multiple status refreshes while the user is still deciding what to commit — a realistic window during which on-disk content legitimately changes (autosave, another tool, a filter/hook, a second working process). No local/privileged access is required beyond what is already inherent to using Desktop with a repository whose build/edit tooling can modify tracked files; the corruption happens purely through the normal commit UI flow with no unusual user steps.

## Recommendation
Before applying `file.selection` to a freshly fetched diff, verify that the diff has not changed since the selection was computed (e.g., compare hunk headers/line counts or a content hash of the pre-image), and if it has changed, refuse to auto-map the old selection and instead force the UI to refresh the diff and require the user to re-confirm the selection. Alternatively, capture and pass through the exact `ITextDiff` object the selection was made against (instead of re-deriving it in `applyPatchToIndex`), and independently detect drift against the on-disk file before generating the patch.

## Proof of Concept
Conceptual sequence (cannot be executed in this read-only environment, but derivable from the code paths cited above):
1. User opens a modified tracked file in Desktop's Changes view; Desktop computes `diff` via `getWorkingDirectoryDiff` and the user selects a subset of lines, producing a `DiffSelection` keyed by absolute line indices into that specific diff.
2. Before the user clicks "Commit", something modifies the file on disk in a way that shifts line numbers/hunks (e.g., an editor autosave, a file watcher/build step, or a git filter run as part of another background git command Desktop itself triggers, such as a status refresh after a fetch).
3. `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681-3699`) still carries the old `DiffSelection` for that file because `updateChangedFiles` (`app/src/lib/stores/updates/changes-state.ts:56`) preserves prior selection by file id across status refreshes.
4. `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:60-81`) re-fetches the diff (now different from what was reviewed) and calls `formatPatch(file, diff)`, which applies the old index-based selection to the new hunk layout (`app/src/lib/patch-formatter.ts:143-157`).
5. `git apply --cached` stages whatever lines land at those indices in the new diff — which may be entirely different lines from what the user visually reviewed and intended to include — and the resulting commit silently contains unreviewed content.

### Citations

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

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

**File:** app/src/lib/stores/updates/changes-state.ts (L43-60)
```typescript
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
    })
```

**File:** app/src/lib/stores/app-store.ts (L3685-3699)
```typescript
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
