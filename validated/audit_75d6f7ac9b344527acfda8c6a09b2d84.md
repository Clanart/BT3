### Title
Silent commit corruption via stale per-line diff selection re-applied to a re-fetched working directory diff - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user select individual diff lines to include in a commit (a "partial commit"). The selection state (`DiffSelection`) is a bitmap of **absolute line indices** computed against a specific diff snapshot shown in the UI. When the commit is actually staged, Desktop does **not** reuse that reviewed diff snapshot — `applyPatchToIndex` re-fetches a brand-new diff from disk and blindly re-applies the old index-based selection to it. If the tracked file's content changes between the moment the diff was rendered/selected and the moment `git commit` is triggered, the line indices no longer correspond to the same logical lines, so the final patch silently includes/excludes different content than what the user reviewed and approved.

### Finding Description
The selection object is a positional bitmap, not content-addressed: [1](#0-0) 
It exposes `isSelected(lineIndex)` purely by absolute index, with no notion of the underlying line's content or identity.

When staging a partially-selected file, Desktop re-derives the diff from disk at that moment rather than using the diff object the user actually looked at: [2](#0-1) 

`formatPatch` then walks the hunks of this **freshly fetched** diff and asks the **old** selection object whether each line's absolute index is selected: [3](#0-2) 

The app itself acknowledges this class of staleness in `updateChangesWorkingDirectoryDiff`, but the mitigation is incomplete — it only prunes selections that land on now-nonexistent/context lines; it does not re-anchor surviving indices to the same logical content: [4](#0-3) 

The commit path itself never re-validates that the diff underlying `state.changesState.workingDirectory.files` selection still matches disk before staging: [5](#0-4) 

This is the same broken invariant as H-15: a piece of derived/cached state (the checkpoint in the contract; the line-selection bitmap in Desktop) is not recomputed/invalidated when the value it depends on (total supply; file content) changes, and a later privileged action (interest calculation; committing) consumes the stale derived state as if it were still valid. In Desktop's case the "attacker-controlled input" is the content of a tracked file inside a cloned/fetched repository — e.g., a file whose content is mutated asynchronously after checkout via a git smudge/clean filter, a `post-checkout`/`post-merge` hook, or any other repo-provided tooling that runs after Desktop has already computed and displayed a diff for that file but before the user clicks "Commit". Because `applyPatchToIndex` regenerates the diff from disk at staging time rather than from the reviewed diff, the stale index-based selection is silently misapplied to the new content.

### Impact Explanation
This results in **silent corruption of what the user commits**: lines the user explicitly deselected (e.g., a secret, a debug statement, or malicious code inserted by the attacker) can end up committed anyway if they happen to land on an absolute index the user had marked "included," and conversely lines the user intended to include can be silently dropped. Since Desktop shows the "Undo" summary and file list after commit but not a byte-for-byte re-diff against what the user actually reviewed pre-commit, the user has no visual signal that the committed content diverged from the reviewed selection. This can be leveraged by a malicious repository to get unreviewed/attacker content committed and subsequently pushed under the victim's identity.

### Likelihood Explanation
Requires the file content to change on disk between diff-render and stage/commit — a narrow timing window, but one that is naturally reachable through legitimate Desktop-triggered git operations (filters, hooks) that run without any unusual user action, satisfying the "unprivileged, repo-controlled" threat model. It does not require local/physical access, admin rights, or social engineering beyond the normal act of cloning and committing in a malicious repository. Likelihood is moderate: the race window exists but is not trivially wide, and the corruption is probabilistic (depends on where the content shift lands relative to selected indices) rather than deterministic.

### Recommendation
Re-validate (or fully recompute) the working-directory diff for any file with a non-`All`/non-`None` selection immediately before staging in `_commitIncludedChanges`/`applyPatchToIndex`, comparing it against the diff snapshot the selection was built from (e.g., by content hash or by diffing hunk headers), and abort/re-prompt the user if the file has changed rather than silently re-applying stale line indices to new hunk content.

### Proof of Concept
Conceptual repro (cannot be executed from this read-only environment, so treat as a code-path trace, not a validated exploit):
1. Victim clones a malicious repository containing a tracked file `payload.txt` and a mechanism that mutates it shortly after checkout (e.g., a `post-checkout` hook, or a filter/tool the repo instructs the victim to run).
2. Victim opens the repo in Desktop, sees a diff for `payload.txt`, and deselects a specific malicious line before committing (`DiffSelection.withLineSelection(N, false)` internally, per `app/src/models/diff/diff-selection.ts:74-136`).
3. Between the diff render and the click on "Commit", the hook/filter shifts the file's line layout so that index `N` now maps to a different (still includeable) line.
4. `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3680-3699`) calls `createCommit` → `stageFiles` → `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` fresh (`app/src/lib/git/apply.ts:60`) and runs `formatPatch` against the **old** selection object (`app/src/lib/patch-formatter.ts:143-157`).
5. The resulting patch includes/excludes lines that do not match what the victim visually reviewed and chose, and this is committed and can subsequently be pushed. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
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

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
```

**File:** app/src/lib/stores/app-store.ts (L3680-3699)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
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
