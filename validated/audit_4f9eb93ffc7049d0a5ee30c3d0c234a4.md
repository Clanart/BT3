### Title
Partial-commit selection uses raw line indices with no content/identity binding to the reviewed diff, allowing stale selection to silently stage different content than the user approved - (File: app/src/lib/git/apply.ts, app/src/lib/patch-formatter.ts, app/src/models/diff/diff-selection.ts)

### Summary
`WorkingDirectoryFileChange.selection` (`DiffSelection`) records which lines the user chose to include in a commit purely as a set of **absolute line indices**, with no hash, checksum, or version tag binding the selection to the exact diff content the user reviewed when making the selection. When the commit is actually executed, `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) does **not** reuse the diff the user looked at in the UI — it re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff(repository, file)` and then blindly re-applies the old, index-based `DiffSelection` (`file.selection.isSelected(absoluteIndex)` in `app/src/lib/patch-formatter.ts:157`) against this freshly generated diff.

### Finding Description
This mirrors the root cause of the LT bug: a piece of state (staker `balanceOf`) is captured/decided against one snapshot of reality but only "materializes" (re-applied) later against a different, more current snapshot, without re-validating that the two snapshots still agree. In Desktop:

1. The user opens the Changes view, is shown a diff (`ITextDiff`) for a file, and toggles line selections through `DiffSelection.withLineSelection`/`withRangeSelection` (`app/src/models/diff/diff-selection.ts:205-282`). These selections are stored as a `Set<number>` of line indices — `isSelected(lineIndex)` (`diff-selection.ts:122`) purely checks index membership, it has zero knowledge of the actual line content.
2. `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681-3699`) takes a snapshot of `state.changesState.workingDirectory.files` (which includes each file's stored `DiffSelection`) at the moment the user clicks "Commit."
3. During `withIsCommitting`, `createCommit` → `stageFiles` → `applyPatchToIndex` runs. Crucially, `applyPatchToIndex` calls `getWorkingDirectoryDiff(repository, file)` **again**, from scratch, right before staging (`app/src/lib/git/apply.ts:60`) — it never reuses or diff-checks against the diff object the user actually saw and selected lines from.
4. `formatPatch(file, diff)` then walks the *new* diff's hunks and, for every non-context line, checks `file.selection.isSelected(absoluteIndex)` (`patch-formatter.ts:143-201`) using the stale line-index selection against the new hunk/line layout.

If the on-disk content of the file changes between the time the user made their line selection and the moment `applyPatchToIndex` re-diffs the file (a window that can span an arbitrary amount of time — the user can leave a commit message half-typed, switch tabs, etc.), the hunk boundaries and absolute line indices shift. The stale selection is then reinterpreted against unrelated lines in the new diff, so the actual staged/committed content can silently diverge from what the user visually reviewed and approved — including or excluding content the user never intended, without any error, warning, or diff-consistency check.

The `_recordCommitStats`/rest of the commit path also passes the same original `selectedFiles` object as if it were still valid (`app-store.ts:3716-3724`), meaning telemetry and audit paths all still believe the original (invalid) selection is what got committed.

Contrast this with `updateChangesStashDiff` (`app-store.ts:3656-3668`) and `updateStatus` in `merge-choose-branch-dialog.tsx:114-122`, which both explicitly compare "before" vs. "after" state and bail out if something changed underneath the async operation — the exact kind of guard that is missing from the commit path.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A user reviewing a partial diff to intentionally exclude specific lines (e.g., excluding a secret, a debug statement, or an unwanted change) can end up committing (and subsequently pushing) different content than what they saw and approved, with no indication anything went wrong. Because Desktop is often used to review and selectively stage changes precisely to avoid committing unwanted lines, this directly undermines the core trust guarantee of the partial-commit feature.

### Likelihood Explanation
Likelihood is bounded by needing the working-directory file to change on disk between the user's line selection and the actual `git apply --cached` call during commit — Desktop does not offer a direct "attacker fetch causes file rewrite mid-selection" primitive that I could confirm from the indexed code (e.g., no evidence found that a `smudge`/`clean` filter or hook fires in that specific window before staging). This is the main reason I cannot fully verify an end-to-end unprivileged trigger purely from a hostile repository/remote without any local process modifying the working tree concurrently — I was unable to locate, within the indexed files, a mechanism by which content controlled by a remote/cloned repository alone (without some other local writer) rewrites a tracked file in that narrow window. This is a gap in what I could confirm via the available index, not a claim that no such path exists.

### Recommendation
Bind the `DiffSelection` to the diff it was derived from (e.g., by hashing the diff/hunks or storing a content fingerprint alongside the selection), and have `applyPatchToIndex`/`formatPatch` verify that the freshly retrieved diff still matches the diff that the selection was made against before applying it. If it doesn't match, abort the commit and force the UI to refresh and re-prompt the user to re-review/re-select, following the same "bail out if state changed underneath us" pattern already used in `updateChangesStashDiff` and `merge-choose-branch-dialog.updateStatus`.

### Proof of Concept
Not fully reproducible from the indexed code alone (see Likelihood Explanation). The mechanically demonstrable part of the flaw:
1. Open a file with a multi-line diff, use partial line selection to select lines 10-15 (`DiffSelection.withRangeSelection`).
2. Before clicking "Commit," modify the file on disk such that lines shift (e.g., insert 5 lines above line 10) — e.g., via any external process that touches the tracked file.
3. Click "Commit." Observe that `applyPatchToIndex` re-diffs the file and stages/commits whatever now sits at indices 10-15 in the new diff — not the content the user actually selected in the UI.
4. `git show` the resulting commit and compare it against what was visually highlighted as "selected" before step 2 — content differs with no error surfaced to the user. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** app/src/lib/patch-formatter.ts (L129-161)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
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

**File:** app/src/lib/stores/app-store.ts (L3656-3668)
```typescript
    const diff = await getCommitDiff(repository, file, file.commitish)

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesStateAfterLoad = stateAfterLoad.changesState

    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
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
