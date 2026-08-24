### Title
Partial-commit line selection is re-mapped onto a freshly re-fetched diff, allowing silent corruption of committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop's partial-file staging path suffers from the same class of bug as the reported Mailbox issue: a value is validated/derived against one snapshot of state, but consumed against a later, different snapshot of that same state, without re-checking consistency. In Desktop, the "snapshot" is the working-directory diff shown to the user when they select individual lines/hunks to commit; the "consumption" is a second, independently re-fetched diff used to build the actual patch that gets applied to the index and committed.

### Finding Description
When a user makes a partial selection of lines in a file (`DiffSelectionType.Range`), the selection state (`file.selection`) stores only line *indices*, not content. Those indices are meaningful only relative to the specific diff hunks that were on screen when the user made the selection.

At commit time, `stageFiles` → `applyPatchToIndex` is called for every partially-selected file: [1](#0-0) 

`applyPatchToIndex` does **not** reuse the diff the UI showed the user. It independently re-fetches the working directory diff right before building the patch: [2](#0-1) 

That freshly-fetched diff is then handed to `formatPatch`, which maps the stored line-index selection onto this new diff's `hunk.unifiedDiffStart + lineIndex` positions: [3](#0-2) 

If the on-disk file content changes between the moment the diff was rendered/selected in the Changes view and the moment `createCommit`/`applyPatchToIndex` actually runs (e.g. a background process, a git filter/hook, or another Desktop operation touches the file), the hunk boundaries and line indices in the newly-fetched diff no longer correspond to the same content the user reviewed. `formatPatch` will silently apply the user's old index-based selection to the new hunk layout, producing a patch that includes/excludes different lines than what the user actually intended and reviewed. There is no check anywhere in `createCommit` (`app/src/lib/git/commit.ts`) or `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts`) that the diff has not changed since the selection was made: [4](#0-3) [5](#0-4) 

This is structurally identical to the Zksync bug: the guard (`_verifyWithdrawalLimit`'s balance check / here, the user's line selection) is computed against one state snapshot, but the value actually used to decide the outcome (`address(this).balance` / here, the re-fetched diff's hunk-relative line numbering) is taken from a later, changed state, and the code proceeds without re-validating that the two snapshots still agree.

### Impact Explanation
The result is silent corruption of what the user commits: content the user did not select can be included, or content the user selected can be silently dropped, with no error surfaced (the operation succeeds, `formatPatch` only throws if the *entire* resulting patch is empty). This falls under the explicitly valid impact category "silent corruption of what the user commits or pushes." A committed/pushed artifact with unintended content is a meaningful integrity issue, particularly if it results in secrets, debug code, or unreviewed edits being pushed to a shared branch.

### Likelihood Explanation
The likelihood depends on how easily the working tree can change between diff-render time and stage time. This can occur without any unusual local access when: the repository defines content filters/attributes (`.gitattributes` clean/smudge filters, `ident`, EOL normalization) or hooks that rewrite files as part of ordinary git plumbing invoked by Desktop's own background refresh/status polling, or another in-flight Desktop operation (e.g. auto-fetch triggered checkout, discard, or file-watcher-driven refresh) touches the same file while the user is mid-selection. This is a narrower trigger than a purely network-attacker-controlled scenario, and I was not able to find a fully unattended, purely-remote trigger path in the indexed code — this is the main uncertainty in this analog.

### Recommendation
`applyPatchToIndex` (and `getFilesDiffText`) should not re-fetch a fresh diff to interpret a selection made against an earlier diff. Either:
- Persist and reuse the exact `ITextDiff`/`ILargeTextDiff` object that was shown to the user when the selection was made, passing it through to `stageFiles`/`applyPatchToIndex` instead of re-deriving it from disk, or
- Detect staleness explicitly (e.g. compare file mtime/hash or diff text) before applying the selection, and abort/refresh the selection if the underlying diff has changed since the user made it.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file so it has multiple hunks.
2. In the Changes view, select only specific lines (partial selection) of one hunk — Desktop stores this as index-based `DiffSelection` state, tied to the currently-rendered diff.
3. Before clicking "Commit", have the file's on-disk content change in a way that shifts hunk boundaries (e.g., a filter/hook triggered by another git operation, or a concurrent Desktop-initiated `git` command that touches the same file).
4. Click Commit. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches a new diff for the file and `formatPatch` (`app/src/lib/patch-formatter.ts:143-161`) applies the old line-index selection to the new hunk layout.
5. Inspect the resulting commit: it contains different line content than what was visually selected by the user in step 2, with no warning shown.

(Note: due to the difficulty of fully exercising git filter/hook timing through the indexed test files, this PoC path is described at the code level rather than confirmed end-to-end in this session; a Devin session with full repo/terminal access would be needed to reproduce the exact timing empirically.)

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

**File:** app/src/lib/git/apply.ts (L60-60)
```typescript
  const diff = await getWorkingDirectoryDiff(repository, file)
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

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/stores/app-store.ts (L3693-3711)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
```
