## Title
Stale line-selection indices reused against a freshly-recomputed diff in partial commits — silent corruption of committed content (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

## Summary
The reported Alchemix bug is a "stale state reused instead of recalculated" defect: `Voter.poke()` re-applies a previously computed value (`_boost`) without recomputing it against the current context, silently producing a wrong result. The same bug class exists in GitHub Desktop's partial-commit path: a user's line-selection state, computed against a diff rendered *earlier*, is reapplied against a diff that is *freshly recomputed at commit time* — with no check that the two diffs still agree on line positions. This can cause Desktop to silently stage/commit different lines than the ones the user actually selected.

## Finding Description
When a user selects specific lines/hunks to include in a commit, Desktop stores that selection as a `DiffSelection` keyed by **absolute line indices** derived from the diff that was on screen at selection time (`hunk.unifiedDiffStart + lineIndex`), as seen throughout `formatPatch`: [1](#0-0) 

At actual commit time, `applyPatchToIndex` does **not** reuse that same diff. It calls `getWorkingDirectoryDiff` again, fetching a brand-new diff from disk, and then formats the patch using the file's *old* `selection` object against this *new* diff's hunks: [2](#0-1) 

`_commitIncludedChanges` (the top-level commit entry point) also does not refresh status/diff before staging — it simply filters `state.changesState.workingDirectory.files` by selection type and passes them straight to `createCommit`/`stageFiles`/`applyPatchToIndex`: [3](#0-2) 

The application does have staleness protection when the diff panel is showing (`updateChangesWorkingDirectoryDiff` recomputes `selectableLines` and bails out if the selection changed while loading), but that guard only runs on the interactive load path in the renderer's state cache, not immediately before `git apply --cached` executes: [4](#0-3) 

If the on-disk file content changes between the time the user's selection was computed (against diff A) and the moment `applyPatchToIndex` re-diffs the file (diff B) — for example due to a `.gitattributes` clean/smudge filter, a `post-checkout`/background process, an editor autosave, or any other content-mutating action tied to the repository — the hunk boundaries and `unifiedDiffStart` offsets in diff B no longer correspond to the same source lines the user selected in diff A. `DiffSelection.isSelected(absoluteIndex)` (used inside `formatPatch`) will then select/deselect the wrong lines of the *new* diff, exactly mirroring `Voter.poke()`'s error of reusing `votes[_tokenId][_poolVote[i]]` (the old weight) without recomputing the boost against the current context.

## Impact Explanation
This can result in **silent corruption of what the user commits**: lines the user explicitly excluded from a commit could be staged and pushed, or lines they meant to include could be silently dropped, without any error or warning. Because `git apply --cached` will typically succeed against a plausible-looking patch (the line offsets are usually still syntactically valid, just semantically wrong), the corruption is silent rather than a crash — matching the report's own framing that "the contract... is not delivering the expected returns but also not losing value" (i.e., not a crash/DoS, but wrong output).

## Likelihood Explanation
This requires the working file to change between the user's line-selection action (in the UI) and the "Commit" click, driven by content controlled through the cloned/fetched repository itself (e.g., filters configured via `.gitattributes`, or hooks). This is a plausible but narrow race window and doesn't require local/physical access, admin rights, or prior host malware — it can be triggered purely by an attacker-controlled repository the user interacts with normally (checkout, partial-stage, commit).

## Recommendation
Before staging a partial selection, re-validate that the diff used to build `DiffSelection` still matches the file's current on-disk diff (e.g., compare diff content/hash, not just file id) and abort/refresh the selection if it has drifted, similar to the existing guard in `updateChangesWorkingDirectoryDiff`. Alternatively, capture and reuse the exact diff object that was used to render the selection UI when calling `applyPatchToIndex`/`formatPatch`, instead of re-fetching a new diff at commit time, and refuse to build the patch if the file has changed since selection.

## Proof of Concept
1. Clone an attacker-controlled repository containing a `.gitattributes` filter (`clean`/`smudge`) or a background watcher script that rewrites a tracked file's content shortly after checkout/open.
2. In Desktop, open the file's diff and manually deselect some lines (partial selection) based on the diff currently shown.
3. Before clicking "Commit", let the attacker-controlled filter/process rewrite the file (shifting line numbers/hunks without necessarily being visible in time).
4. Click "Commit". `applyPatchToIndex` re-diffs the file (`getWorkingDirectoryDiff`) and applies the stale `DiffSelection` indices to the new hunk layout via `formatPatch`.
5. Inspect the resulting commit: lines that were deselected by the user are present, or lines that were selected are missing — the committed content silently diverges from user intent, with no error shown.

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
