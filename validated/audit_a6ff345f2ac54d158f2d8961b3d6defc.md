## Title
Stale line-index diff selection reused against a freshly re-read working-tree diff at commit time can silently commit unintended content - (`File: app/src/lib/patch-formatter.ts`, `app/src/lib/git/apply.ts`)

### Summary
The Pyth report's underlying flaw is that a value used to compute how much the user commits to (SOL to spend) is determined by re-reading a live, attacker-influenceable oracle at execution time, while the amount the user actually approved was fixed earlier, with no check binding the two together. The same broken invariant exists in GitHub Desktop's partial-commit ("stage selected lines") pipeline: the user's line/hunk selection (`DiffSelection`) is captured against one diff snapshot shown in the UI, but at commit time the code re-fetches a brand-new diff from the working tree and blindly re-applies the old, index-based selection to it, with no verification that the diff is unchanged.

### Finding Description
When a user stages only some lines of a file, the UI selection is stored as a `DiffSelection` bitmap keyed by absolute line indices of the diff that was rendered at selection time. `Dispatcher.commitIncludedChanges` → `_commitIncludedChanges` takes `state.changesState.workingDirectory.files` (whose `.selection` was computed against a possibly-earlier diff) and passes it straight to `createCommit`: [1](#0-0) 

`createCommit` calls `stageFiles`, which for any partially-selected file calls `applyPatchToIndex`: [2](#0-1) 

`applyPatchToIndex` re-reads the file from disk via a **fresh** `getWorkingDirectoryDiff` call, not the diff that was displayed to the user, and then builds the patch from that fresh diff using the old selection indices: [3](#0-2) 

`formatPatch` maps `file.selection.isSelected(absoluteIndex)` onto the lines of this newly fetched diff with no check that the hunk layout, line count, or content still matches what the user reviewed: [4](#0-3) 

Desktop's own code acknowledges this class of staleness elsewhere but only patches it for the *live UI refresh* path (before a commit is triggered), not immediately before `stageFiles`/`applyPatchToIndex` execute: [5](#0-4) 

There is no analogous re-validation step inside the actual commit path (`_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex`), so the "approved selection" and the "diff acted upon" can diverge silently — exactly the missing bound/consent check pattern from the report (approve N tokens for price P, but the executed transaction can use a different P without limit).

### Impact Explanation
If the working-tree file content changes between the moment the user finalizes their line selection in the Changes view and the moment the commit is actually executed (e.g. because a build step, linter/formatter watcher, editor autosave, or a git filter/attribute (`clean`/`smudge`) driven by repository-supplied `.gitattributes` rewrites the file), `applyPatchToIndex` will index into the new diff using stale absolute-line offsets. This can select the wrong lines/hunks — silently including changes the user explicitly excluded, or excluding changes the user explicitly wanted to keep out of the commit — without any error or warning, and the resulting commit can then be pushed. This matches the "silent corruption of what the user commits or pushes" impact class, since the app proceeds to write a Git object and move `HEAD` based on a mismatched selection-to-diff mapping.

### Likelihood Explanation
The window between diff-selection and commit execution is realistic: any repository-controlled `.gitattributes` filter, checkout/smudge hook, background formatter, or IDE autosave running against files inside a repo the user has open can alter file content in that window without any user action beyond normal editing/repo use. No local/admin access or credential leakage is required — only that the attacker can influence content that gets processed by a filter/hook triggered by ordinary file operations on a cloned repository, which is within the allowed threat model (attacker controls repo content).

### Recommendation
Before applying a partial-selection patch in `applyPatchToIndex`/`stageFiles`, re-validate that the diff being acted upon (hunk boundaries, line count/content hashes) matches the diff the user's `DiffSelection` was computed against; if it differs, abort the partial stage/commit for that file and force the UI to reload the diff and let the user reconfirm their selection, rather than silently remapping indices onto new content.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file with multiple hunks.
2. In the Changes view, review the diff and select only specific lines/hunks for commit (leaving others unselected).
3. Before clicking "Commit", have an external process (e.g., a `.gitattributes`-driven clean/smudge filter, a watched build tool, or an editor autosave) modify the same file, changing hunk boundaries/line offsets without the UI diff being refreshed.
4. Click "Commit". `_commitIncludedChanges` passes the stale `DiffSelection` to `createCommit` → `stageFiles` → `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` fresh at `app/src/lib/git/apply.ts:60` and applies the stale line-index selection via `formatPatch` at `app/src/lib/patch-formatter.ts:157`.
5. Inspect the resulting commit: it can contain lines/hunks the user never intended to include (or omit lines the user intended to include), with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3492)
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

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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
