### Title
Stale line-index diff selection is silently re-applied to a freshly re-diffed file during commit staging - ([File: app/src/lib/git/apply.ts])

### Summary
This is a Desktop analog of the "front-run/stale-index" bug class in the report: the Alchemix bug lets an attacker race a privileged index-based update so an operation validated against one state is executed against a different, shifted state. In Desktop, `WorkingDirectoryFileChange.selection` records which diff *lines* the user chose to include in a commit as absolute line indices ` [1](#0-0) `. When the commit is actually staged, `applyPatchToIndex` re-fetches a brand-new diff of the file from disk (`getWorkingDirectoryDiff`) rather than reusing the diff the user reviewed, and then reuses the old, index-based `selection` against that new diff to build the patch that is applied to the index [2](#0-1) .

### Finding Description
The commit flow is:
1. UI renders a diff and the user selects/deselects specific lines (line-index based) — `DiffSelection`/`formatPatch` operate purely on `absoluteIndex` positions within a hunk [3](#0-2) .
2. `_commitIncludedChanges` reads the current `workingDirectory.files` from repository state and filters by `file.selection` [4](#0-3) , then calls `createCommit`.
3. `createCommit` unstages everything and calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex` [5](#0-4) [6](#0-5) .
4. `applyPatchToIndex` does **not** reuse the diff the user actually reviewed. It calls `getWorkingDirectoryDiff(repository, file)` again at staging time and builds `formatPatch(file, diff)` using the stale `file.selection` indices against this newly computed diff [2](#0-1) .

Because `DiffSelection` is keyed by absolute line position in a hunk and not by line content/hash, if the on-disk file content changes between the time the user made their selection in the UI and the time `applyPatchToIndex` re-diffs it, the "same" indices now point at different lines. This is exactly analogous to `swapOutRewardToken`'s `tokenIndex` being applied against an array whose contents shifted between validation and execution — the operation is executed against the wrong element with no invariant check that the underlying data hasn't changed.

An attacker who controls content that lands in the working directory of a cloned/fetched repository (e.g. via a smudge/clean filter defined in a tracked `.gitattributes`, an LFS filter, or any tracked script capable of rewriting file contents when checked out or refreshed in the background) can cause the file to be rewritten between the user's diff review and the click of "Commit". Desktop performs no re-validation (no hash/oid comparison of the diff) before reusing the stale selection, so the final patch silently includes/excludes different lines than the user intended.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes" — an explicitly listed valid impact category. A user could believe they are only committing an innocuous hunk while the actually-staged patch (computed against attacker-modified content) includes different, attacker-influenced or unintended lines, or omits a security-relevant line the user thought they excluded (e.g. a secret they explicitly deselected). Since this corrupted commit can subsequently be pushed, this can leak content the user never intended to include, or exclude changes the user thought were included — all without any error or warning surfaced to the user.

### Likelihood Explanation
The likelihood is bounded by the difficulty of controlling *when* the working tree file changes relative to the UI-review-to-click-commit window, and by the need for some tracked/attacker-influenced mechanism (e.g. filters, background refresh interacting with file content, or another concurrently running tool) to rewrite the exact file between diff render and staging. It does not require local/physical access, admin rights, or leaked credentials — only a cloned/fetched repository whose tracked configuration can influence working-directory content, plus normal timing (the commit message composition window gives an attacker-influenced background process a realistic window). This makes it a plausible but timing-dependent (medium-likelihood) race, structurally identical to the front-run in the source report.

### Recommendation
Before reusing a previously computed `DiffSelection` to build a patch in `applyPatchToIndex`, validate that the newly fetched diff is unchanged from the one the selection was computed against (e.g., compare line content/hash or diff length, not just positional index), and abort/re-prompt the user if the underlying file changed. Alternatively, capture and reuse the exact same diff object used for line selection throughout the commit pipeline instead of re-fetching it at staging time.

### Proof of Concept
Not independently reproducible from static analysis alone. The conceptual repro is:
1. Open a repository containing a large modified file and select only some lines to commit via `DiffSelection.withRangeSelection` in the UI.
2. Before pressing "Commit", have an external, repository-influenced mechanism (e.g. a clean/smudge filter driven by tracked `.gitattributes`, or a concurrent `git checkout`/filter re-run) rewrite the same lines of the file, shifting line positions.
3. Click Commit — `applyPatchToIndex` re-diffs the file (`app/src/lib/git/apply.ts:60`) and applies the old `file.selection` indices to the new diff (`app/src/lib/patch-formatter.ts:129-157`), producing a patch that stages different content than what the user reviewed and selected, with no warning to the user.

Because this repro depends on precise timing/an external content-rewriting mechanism that I could not fully validate exists end-to-end in this codebase snapshot (e.g., confirmation of a specific attacker-controllable filter that fires in this exact window), this should be treated as a code-path-supported hypothesis requiring dynamic verification rather than a confirmed exploit chain.

### Citations

**File:** app/src/models/status.ts (L294-308)
```typescript
/** encapsulate the changes to a file in the working directory */
export class WorkingDirectoryFileChange extends FileChange {
  /**
   * @param path The relative path to the file in the repository.
   * @param status The status of the change to the file.
   * @param selection Contains the selection details for this file - all, nothing or partial.
   * @param oldPath The original path in the case of a renamed file.
   */
  public constructor(
    path: string,
    status: AppFileStatus,
    public readonly selection: DiffSelection
  ) {
    super(path, status)
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

**File:** app/src/lib/stores/app-store.ts (L3681-3691)
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
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
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
