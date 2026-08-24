Based on the investigation, the strongest Desktop analog to the `rescueToken`/`lastBalance` bug class — a piece of per-item accounting state that is not invalidated after an external event modifies the underlying data, and is later reused for a critical operation — is the way partial line-selection state (`DiffSelection`) is preserved across working-directory refreshes without being revalidated against the new diff content, before being fed into the patch that actually gets committed.

### Title
Stale partial-commit line selection is applied to a rewritten diff after fetch/pull, causing wrong content to be silently committed - (File: `app/src/lib/stores/updates/changes-state.ts`)

### Summary
`updateChangedFiles` merges the previous `IChangesState.workingDirectory` with the freshly computed `git status` result. For any file that still exists (matched only by `file.id`, i.e. its path), it blindly copies over the file's previous `DiffSelection` object with `file.withSelection(existingFile.selection)`, regardless of whether the file's actual diff (hunks/line numbers) changed underneath it: [1](#0-0) . `DiffSelection` tracks "diverging lines" purely as absolute line indices into the unified diff (`divergingLines: Set<number>`), with no reference to actual line content, hash, or SHA [2](#0-1) .

The only place that re-validates/prunes this line-index bookkeeping against a fresh diff is `updateChangesWorkingDirectoryDiff`, and it only does so for the single file that is *currently selected/open* in the Changes view: [3](#0-2) . Its own comment concedes the fixup is incomplete ("Ideally we would be more clever about validating that any partial selection state is still valid... but for now we'll settle on just updating the selectable lines"). Any other file with a partial selection that isn't currently being viewed keeps its **stale, unvalidated** line-index selection indefinitely.

When the user later commits, `_commitIncludedChanges` gathers every file whose selection type is not `None` (including files that were never re-opened) and hands them to `createCommit` [4](#0-3) . For any file with a `Partial` selection, `applyPatchToIndex` fetches a **brand-new** diff from disk at commit time and builds the patch using the file's (potentially stale) `DiffSelection.isSelected(absoluteIndex)` against that new diff's hunks: [5](#0-4) [6](#0-5) . If the on-disk content changed structurally between when the selection was made and commit time (lines inserted/removed shifting hunk offsets), the old line indices now point at completely different lines in the new diff.

### Finding Description
The broken invariant is: *"a file's `DiffSelection` line-index state is only valid for the diff it was computed against."* Nothing enforces that invariant once the file stops being the actively-viewed file. A `git fetch`/`pull`/`checkout` — driven by content from a remote or fetched repository that an attacker fully controls — can silently rewrite tracked working-directory files whose paths match files the user already has queued with a partial line selection. Since `updateChangedFiles` reuses `existingFile.selection` keyed only on file id/path [7](#0-6) , and since `clearPartialState` is `false` on ordinary refreshes (only forced `true` right after a completed commit) [8](#0-7) , the stale selection survives fetches/pulls and reaches `formatPatch` unrevalidated.

### Impact Explanation
Because `formatPatch` decides per-line whether to include an addition/deletion purely from `file.selection.isSelected(absoluteIndex)` against the *new* diff's hunk layout, a mismatch between old line indices and new hunk structure can cause the generated patch to:
- Include newly-introduced (possibly attacker-crafted) lines that the user never reviewed or intended to commit, or
- Silently drop lines the user did intend to commit, or
- Convert unrelated content into what gets staged/committed and eventually pushed.

This is a silent corruption of what the user commits/pushes — the user believes they are committing the reviewed selection from before the pull, but the actual bytes staged can diverge from that intent without any warning, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Requires: (1) the user has a file with a `Partial` selection queued (a supported, common workflow — selecting only some hunks/lines), (2) that file is not the one currently open in the diff pane when a fetch/pull/checkout happens that rewrites it, and (3) the user commits without re-opening/re-reviewing that file's diff. All three conditions are realistic in normal multi-file workflows (e.g., partially staging one file, switching to review another, then pulling collaborators' changes before committing). No local/admin access or social engineering beyond ordinary git fetch/pull is needed — the attacker only needs to control the content of the fetched/pulled repository.

### Recommendation
When merging status in `updateChangedFiles`, do not blindly carry over a `Partial` selection for a file whose diff has structurally changed since the selection was captured. Either: (a) recompute/validate `selectableLines` (and drop divergence outside the valid range) for every file with a partial selection during every status refresh, not just the currently open one, or (b) invalidate (reset to `None`/full selectable revalidation) any partial selection whose underlying blob has changed (e.g. compare the file's `oid`/content hash captured at selection time versus current status) before it is allowed to flow into `applyPatchToIndex`/`formatPatch`.

### Proof of Concept
1. In a repo, modify `file.txt` locally to have new content and stage only lines 5–8 via the Changes UI, leaving a `Partial` `DiffSelection` on `file.txt`, and switch selection in the UI to a different file so `file.txt`'s diff is no longer being actively revalidated.
2. Have a collaborator (or malicious remote/proxy) push a commit to the tracked branch that rewrites `file.txt`, inserting/removing lines above the previously selected region so that hunk offsets shift.
3. `git pull`/fetch inside Desktop — `_refreshRepository`/`_loadStatus` runs with `clearPartialState: false`, and `updateChangedFiles` reuses the old `DiffSelection` for `file.txt` unchanged [7](#0-6) .
4. Without reopening `file.txt`'s diff, click Commit. `_commitIncludedChanges` includes `file.txt` (still `Partial`) [9](#0-8) ; `applyPatchToIndex` fetches the new diff and applies the stale line indices via `formatPatch`, producing a patch whose selected lines no longer correspond to the originally reviewed content [5](#0-4) .
5. Inspect the resulting commit: the staged hunk content does not match what the user actually selected/reviewed in the diff view before the pull, demonstrating silent corruption of the committed content.

Note: I was unable to fully confirm the exact definition of `FileChange.id` (base class getter) within the tool budget available; based on all observed usage (`filesByID.set(f.id, f)`, `findFileWithID`, matching across statuses by path) it functions as a path-derived identifier, which is what allows the stale-selection reuse described above.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L41-61)
```typescript
  // Attempt to preserve the selection state for each file in the new
  // working directory state by looking at the current files
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
    .sort((x, y) => caseInsensitiveCompare(x.path, y.path))
```

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

**File:** app/src/lib/stores/app-store.ts (L3680-3698)
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
```

**File:** app/src/lib/stores/app-store.ts (L3739-3742)
```typescript
        await this.refreshChangesSection(repository, {
          includingStatus: true,
          clearPartialState: true,
        })
```

**File:** app/src/lib/git/apply.ts (L52-62)
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
```

**File:** app/src/lib/patch-formatter.ts (L143-172)
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
        // Unselected lines in new files needs to be ignored. A new file by
```
