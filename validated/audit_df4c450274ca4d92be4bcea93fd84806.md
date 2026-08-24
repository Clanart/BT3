This confirms the vulnerability mechanism precisely: `DiffSelection` (app/src/models/diff/diff-selection.ts:53-136) is a purely positional/index-based data structure — it stores line selections as raw integer offsets (`divergingLines: Set<number>`) with no notion of file content or hash, and `isSelected(lineIndex)` blindly checks whether that offset is in the diverging set, regardless of which diff produced those offsets.

### Title
Stale line-selection indices are silently re-applied to a freshly re-fetched diff, causing Desktop to commit different content than the user reviewed and selected - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages a partial file (selecting individual lines/hunks to commit), Desktop stores the selection as an index-based `DiffSelection` bitmap computed against the diff that was rendered in the Changes view at that moment. At actual commit time, `applyPatchToIndex` does **not** reuse that diff — it re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` and then re-applies the old, positionally-indexed selection to the new diff's hunks in `formatPatch`. If the tracked file's on-disk content changes between the moment the user reviews/selects lines and the moment `_commitIncludedChanges` actually executes `stageFiles`/`applyPatchToIndex`, the hunk boundaries and line offsets shift, so the same numeric indices now point to unrelated lines. The user ends up committing content they never reviewed or intended, with no warning, diff refresh, or confirmation.

### Finding Description
- The UI computes `file.selection` (a `DiffSelection`) against a diff snapshot loaded earlier (e.g., via `getWorkingDirectoryDiff`, `app/src/lib/git/diff.ts:342-401`).
- `_commitIncludedChanges` (app/src/lib/stores/app-store.ts:3681-3711) captures `selectedFiles` from `state.changesState.workingDirectory.files` — the file objects with their (already computed) `.selection` — then performs an `await formatCommitMessage(...)` before calling `createCommit`. This is an async gap during which the working tree can change. [1](#0-0) 
- `createCommit` (app/src/lib/git/commit.ts:15-31) calls `stageFiles(repository, files)`, which for any partially-selected file calls `applyPatchToIndex`. [2](#0-1) 
- `applyPatchToIndex` (app/src/lib/git/apply.ts:52-81) **independently re-reads the current on-disk diff** with `getWorkingDirectoryDiff(repository, file)` — a fresh diff, not the one the selection was built against — and feeds it straight into `formatPatch`. [3](#0-2) 
- `formatPatch` (app/src/lib/patch-formatter.ts:129-220) walks the (new) diff's hunks and, for each line, computes `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and checks `file.selection.isSelected(absoluteIndex)` — i.e. it trusts that the old selection's numeric offsets still correspond to the same logical lines in the newly fetched diff. [4](#0-3) 
- `DiffSelection.isSelected` (app/src/models/diff/diff-selection.ts:122-136) has no concept of diff content or hashing — it is a pure integer-offset lookup, so it cannot detect that the diff underneath it has shifted. [5](#0-4) 

No guard exists anywhere in this path that re-validates the selection against the diff it is being applied to, or that aborts/re-prompts the user if the working-directory content changed since the selection was made. This mirrors the Plaza `PreDeposit` bug exactly: an approval/selection decision is made against one state snapshot, but the privileged action (staging + committing) is executed later against a *different*, attacker/environment-influenced state snapshot, with no check tying the two together.

### Impact Explanation
This is a silent-corruption-of-what-the-user-commits vulnerability. A tool, script, or watcher shipped in a cloned/fetched repository (e.g., a documented `npm run dev`/codegen/formatter/watch process that the project's own README instructs the user to run while working, or a build step triggered by opening the project in an editor) can rewrite tracked files between the time the user reviews a diff and clicks "Commit". Desktop will then apply the user's old line selection to the new hunk layout, staging and committing lines the user never saw or intended — potentially reintroducing removed secrets/lines, dropping intended lines, or including attacker-injected content in the resulting commit and any subsequent push, without any error or confirmation dialog.

### Likelihood Explanation
The window is any interval between the user selecting partial lines in the Changes view and the commit actually executing (`_commitIncludedChanges` performs an `await formatCommitMessage` and other async work first, and users naturally pause between staging and clicking Commit). Any file-mutating process the malicious repository encourages the user to run in parallel (dev server, watch-mode bundler, formatter-on-save, doc/codegen tool) is sufficient — no special privileges, hooks, or local exploit chain are required, only that the working tree changes underneath an outstanding partial selection.

### Recommendation
Before staging (`stageFiles`/`applyPatchToIndex`), re-fetch the diff and re-validate that the file's on-disk content (or its diff signature/hash) matches what was used to build the `DiffSelection`; if it has changed, abort the commit and force the user to re-review/re-select against the up-to-date diff rather than silently reinterpreting stale indices against new hunks.

### Proof of Concept
1. Open a tracked file with multiple hunks in Desktop's Changes view; partially select only specific lines within one hunk (e.g., lines 10–12) for commit, leaving the rest unstaged.
2. While Desktop is idle before you click "Commit" (or during the async gap in `_commitIncludedChanges` while `formatCommitMessage` runs), have an external process (e.g., a repo-provided watch/build script) rewrite the same file so the diff hunk boundaries shift (lines inserted/removed earlier in the file).
3. Click "Commit". `applyPatchToIndex` re-fetches the new diff and `formatPatch` maps the stale indices 10–12 onto the new hunk layout.
4. Inspect the resulting commit (`git show`): it contains different lines than what was visually selected in the UI, confirming silent corruption of committed content. (Not run against a live build in this session — derived directly from the code paths cited above; recommend validating with an actual Desktop build/test harness.)

### Citations

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

**File:** app/src/lib/patch-formatter.ts (L143-161)
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
```

**File:** app/src/models/diff/diff-selection.ts (L122-136)
```typescript
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
