## Analysis

The Sherlock report's broken invariant is: **a decision (vote tally) is computed from a fresh read of mutable state at action-time, but the fields used to combine/verify that state were fixed at an earlier point, so state changes made in the window between "decision" and "action" are silently mis-applied.** The GitHub Desktop analog for this exists in the **partial-commit / line-selection flow**, not in a batching loop, but in the same class of TOCTOU: an index-based selection is computed against one diff snapshot, then blindly re-applied to a second, independently-fetched diff snapshot at commit time.

### The mechanism

When a user makes a partial selection of lines to commit, the app stores it as an index-based `DiffSelection` bitmask (`divergingLines: Set<number>`), keyed purely by numeric line offsets into a diff — it has **no binding to diff content/hash**: [1](#0-0) [2](#0-1) 

That selection is produced against the diff the user was shown in the Changes view at the time they clicked/dragged line checkboxes.

When the commit is actually executed, `_commitIncludedChanges` takes the currently-selected files as-is and calls `createCommit`, with no re-validation step against a fresh diff: [3](#0-2) 

`createCommit` → `stageFiles` → `applyPatchToIndex` for any file with a partial selection: [4](#0-3) [5](#0-4) 

Critically, `applyPatchToIndex` **re-fetches the working directory diff from disk at this later point in time**, rather than reusing the diff the selection was computed against: [6](#0-5) 

That freshly-fetched diff is then combined with the *old* selection bitmap in `formatPatch`, which walks the new diff's hunks and tests `file.selection.isSelected(absoluteIndex)` using the new hunk's `unifiedDiffStart` offsets: [7](#0-6) 

If the tracked file's content changed between when the user made the selection and when the commit executes (e.g. content rewritten by a git `clean`/`smudge` filter declared in a malicious repo's `.gitattributes` and already configured globally — Git LFS being the most common real-world example — or by any other repo-triggered working-directory mutation such as a submodule update or generated file), the hunk boundaries and `unifiedDiffStart` values shift. The stale line-index selection is then reapplied against the wrong lines of the new diff, so `formatPatch` silently includes/excludes different lines than what the user actually reviewed and checked in the UI.

### Existing guard, but not on this path

Desktop is aware that diffs can go stale under a selection and has a mitigation — but it is only wired into the **UI diff-refresh path**, not the **commit-execution path**: [8](#0-7) 

That reconciliation (`withSelectableLines`, pruning selections to lines that still exist) runs when the Changes view reloads a diff for display. It is never invoked between the moment `_commitIncludedChanges` reads `state.changesState.workingDirectory.files` and the moment `applyPatchToIndex` re-fetches its own diff via `getWorkingDirectoryDiff`. The guard exists in the codebase, demonstrating the team is aware content can change under a selection, but it doesn't protect the actual git write path.

### Title
Stale line-index diff selection is reapplied against a freshly re-fetched working-directory diff during partial commit staging, allowing off-disk content drift to silently corrupt what the user commits - (File: app/src/lib/git/apply.ts)

### Summary
`applyPatchToIndex` fetches a brand-new working-directory diff at staging time and applies the user's previously-computed `DiffSelection` (a set of raw line indices) against it, instead of reusing the diff the selection was actually built from. Because `DiffSelection` carries no reference to the diff/content it was computed against, and no equivalent of the UI's `withSelectableLines` reconciliation runs on the commit path, any change to a tracked file's on-disk content between the time the user selects lines to commit and the time Desktop executes `git commit` causes the wrong lines to be staged.

### Finding Description
The chain is: UI selection built from diff `D1` at time `T1` → `_commitIncludedChanges` passes the file (with `D1`-relative selection) straight to `createCommit` → `stageFiles` → `applyPatchToIndex`, which fetches diff `D2` at time `T2` ( [9](#0-8) ) → `formatPatch` walks `D2`'s hunks and tests the `D1`-relative selection indices against `D2` ( [10](#0-9) ). If `D1 != D2` in hunk layout, the index-based selection maps to different lines than the user intended. This is structurally the same class of bug as the report: a value snapshotted at decision time (`userVoteWeight`/here, the selection) is combined with a live value read later (`SBF_BMX.balanceOf`/here, the freshly re-fetched diff), and no invariant ties the two together.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A repository an attacker controls (via clone/fetch) can arrange for tracked-file content to change through mechanisms already trusted by the victim's git configuration and declared entirely from repo content (e.g. `.gitattributes` invoking a pre-configured clean/smudge filter such as Git LFS, which many developers have globally enabled). If that content mutation lands inside the window between the user reviewing/selecting diff hunks and clicking "Commit" (a window the user themselves controls the length of, e.g. by typing a commit message), the resulting commit can contain attacker-influenced or simply incorrect content that the user never reviewed or approved, while the commit message and UI gave no indication of a problem.

### Likelihood Explanation
The window between hunk selection and commit execution is arbitrarily long and entirely normal — Desktop keeps the selection state live across message composition, tab switches, and co-author edits, all before `_commitIncludedChanges` is invoked. No local/admin access or prior malware is required; only that the file being partially committed is affected by a filter/mechanism the user's git installation already runs on checkout/merge, driven purely by cloned repo content (`.gitattributes`).

### Recommendation
On the commit-execution path, either (a) re-fetch the diff immediately before staging and reconcile the stored `DiffSelection` against it the same way `withSelectableLines` does for the UI (dropping/re-validating divergent line indices, and refusing to stage/warning the user if the diff has materially changed), or (b) bind `DiffSelection` to a content hash / diff snapshot and refuse to apply it against a diff that doesn't match, similar in spirit to the recommendation in the source report to snapshot state rather than trust a live read taken at a different point in the process.

### Proof of Concept
1. Attacker publishes a repository containing a tracked file `secret.psd` (or any extension already routed through a smudge/clean filter globally configured on the victim's machine, e.g. Git LFS `*.psd filter=lfs`).
2. Victim clones the repo in GitHub Desktop, modifies `secret.psd` with two hunks, and opens the Changes view; Desktop computes diff `D1` and the user checks only hunk 1's lines for inclusion (selection is `D1`-relative line indices via `DiffSelection`, see [11](#0-10) ).
3. Before the victim clicks "Commit," a background process this repo caused to run (the configured LFS smudge/clean filter reacting to a `git status`/checkout triggered by Desktop's own background refresh) rewrites `secret.psd`'s on-disk bytes, shifting hunk boundaries to produce diff `D2`.
4. Victim clicks "Commit." `_commitIncludedChanges` passes the stale `D1`-relative selection through unchanged ( [12](#0-11) ); `applyPatchToIndex` fetches `D2` ( [9](#0-8) ) and `formatPatch` applies the `D1` selection indices to `D2`'s hunks ( [10](#0-9) ), staging different lines than what the user visually selected and approved in the UI.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L41-52)
```typescript
/**
 * An immutable, efficient, storage object for tracking selections of indexable
 * lines. While general purpose by design this is currently used exclusively for
 * tracking selected lines in modified files in the working directory.
 *
 * This class starts out with an initial (or default) selection state, ie
 * either all lines are selected by default or no lines are selected by default.
 *
 * The selection can then be transformed by marking a line or a range of lines
 * as selected or not selected. Internally the class maintains a list of lines
 * whose selection state has diverged from the default selection state.
 */
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

**File:** app/src/models/diff/diff-selection.ts (L205-236)
```typescript
  public withLineSelection(
    lineIndex: number,
    selected: boolean
  ): DiffSelection {
    return this.withRangeSelection(lineIndex, 1, selected)
  }

  /**
   * Returns a copy of this selection instance with the provided
   * line selection update. This is similar to the withLineSelection
   * method except that it allows updating the selection state of
   * a range of lines at once. Use this if you ever need to modify
   * the selection state of more than one line at a time as it's
   * more efficient.
   *
   * @param from     The line index (inclusive) from where to start
   *                 updating the line selection state.
   *
   * @param to       The number of lines for which to update the
   *                 selection state. A value of zero means no lines
   *                 are updated and a value of 1 means only the
   *                 line given by lineIndex will be updated.
   *
   * @param selected Whether the lines should be marked as selected
   *                 or not.
   */
  // Lower inclusive, upper exclusive. Same as substring
  public withRangeSelection(
    from: number,
    length: number,
    selected: boolean
  ): DiffSelection {
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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
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

**File:** app/src/lib/patch-formatter.ts (L135-157)
```typescript
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
```
