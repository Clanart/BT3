## Title
Partial-commit line selections are applied against a freshly fetched diff without revalidation, allowing silent corruption of committed content when file lines shift underneath a stale selection — (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

## Summary
The reported bug's broken invariant is: **a value computed against one state (raw borrow amount) is later applied to a different, already-mutated state (scaled/accrued debt), producing a silently wrong result** (inflated utilization → wrong interest rate). The same invariant break exists in GitHub Desktop's partial-commit ("stage-by-line") pipeline: a user's line-selection bitmap is computed against one version of a file's diff, but is applied — without revalidation — to a **freshly re-fetched diff** of the file at the moment of staging. If the file's on-disk content shifted between those two points, the positional (index-based) selection no longer refers to the same logical lines, and Desktop silently stages/commits the wrong hunks.

## Finding Description
`ReserveLibrary.updateInterestRatesAndLiquidity` in the report failed because it operated on `amount` (the value as it stood before state mutation) instead of `amountMinted` (the value reflecting the post-mutation, accrued state). GitHub Desktop has an structurally identical pattern in its partial-commit ("stage individual lines") feature:

- A user's line selection is stored as a `DiffSelection` bitmap keyed by **absolute line index** (`hunk.unifiedDiffStart + lineIndex`), computed against whatever diff was last loaded for a file, see `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts:3404-3513`, specifically the comment: *"The diff might have changed dramatically since last we loaded it... we'll settle on just updating the selectable lines"* [1](#0-0) .
- Critically, this revalidation only runs for the **single currently displayed file** (`selectedFileIDsBeforeLoad.length !== 1` bails out entirely) [2](#0-1) . Any other file that is checked for inclusion in the commit but not currently open in the diff viewer keeps its old selection bitmap, unrevalidated, indefinitely.
- When the commit is actually created, `_commitIncludedChanges` synchronously reads whichever files/selections are currently in the cached `IRepositoryState` — it does **not** force a status/diff refresh first [3](#0-2) .
- Staging then calls `applyPatchToIndex`, which **re-fetches a brand-new diff from disk** via `getWorkingDirectoryDiff` [4](#0-3)  and immediately feeds it, together with the (possibly stale) `file.selection`, into `formatPatch`.
- `formatPatch` walks the **new** diff's hunks and tests `file.selection.isSelected(absoluteIndex)` purely by index position [5](#0-4) . There is no check that the new diff's hunk shape/line content matches what the selection was originally computed against.

This is the exact same class of bug: state advanced (file content on disk changed / accrued more edits) between when a derived value was captured (the selection bitmap) and when that value was consumed (`updateInterestRatesAndLiquidity` / `formatPatch`), and the consumer uses the stale value as if it still corresponded to the current state.

## Impact Explanation
Because `formatPatch` builds the actual patch applied to the git index with `git apply --cached`, a positional mismatch directly determines what bytes land in the next commit. This is "silent corruption of what the user commits or pushes" — one of the explicitly valid impact categories: a user could believe they excluded a specific added line (e.g., a debug secret, or an unreviewed line from a fetched/merged branch) while Desktop actually stages a different line at that same index, or vice-versa includes content the user explicitly deselected. No error is raised (`git apply --unidiff-zero` with `--whitespace=nowarn` will happily reinterpret hunk headers written by `formatPatch` from whatever lines the fresh diff produced) unless the hunk content diverges so much that `git apply` rejects it outright — but partial shifts (e.g., inserted/removed line elsewhere in the same file, or a hunk being renumbered) are exactly the case where it silently "succeeds" with wrong content.

## Likelihood Explanation
This requires no local/admin access and no malware: any normal source of concurrent file mutation is sufficient — a file watcher/build tool (webpack/tsc `--watch`), an editor auto-save/format-on-save, a fetched-and-merged branch that touches the same file via `git pull` performed in another terminal while the multi-file commit is prepared in Desktop, or a background `git stash pop`/checkout. Desktop explicitly acknowledges the general problem ("The diff might have changed dramatically since last we loaded it") but only mitigates it for the single file currently displayed in the diff pane, not for every file that has a partial selection at commit time. Since Desktop's changes workflow encourages selecting/partially-staging multiple files and then switching focus between them before hitting "Commit," the window for this race is realistic and repeatedly exercised in normal use.

## Recommendation
Before staging (`stageFiles`/`applyPatchToIndex`), Desktop should either:
1. Re-validate every partially-selected file's selection against a freshly computed diff immediately prior to staging (not just the one currently displayed), collapsing to "select none"/"select all" or re-mapping by content/hunk identity instead of raw index when the underlying diff has changed, or
2. Perform the diff fetch used to build the patch and the diff used to render the last-known selection atomically (single source of truth) so `formatPatch` is guaranteed to operate on the same diff snapshot the user actually reviewed, failing/reprompting the user if the file changed since selection was made.

## Proof of Concept
1. In Desktop, open a repo with multiple modified files (A and B).
2. Select File A in the Changes view, open its diff, and make a partial line selection (e.g., include only line 10).
3. Without opening File B in Desktop (keep it unselected/unfocused in the diff viewer), select the checkbox to include File B fully or check some lines of B (partial selection stored as an index bitmap).
4. Externally (e.g., in a terminal or via an editor/build tool) modify File B such that lines shift (insert/remove a line above the previously selected line range) — this does not go through Desktop's `updateChangesWorkingDirectoryDiff` revalidation because File B isn't the currently open diff.
5. Click "Commit" in Desktop. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex(repository, fileB)` fetches File B's **new** diff and applies File B's **old** selection bitmap via `formatPatch`.
6. Inspect the resulting commit for File B: the staged hunk corresponds to the wrong lines relative to what the user intended when they made the selection, demonstrating silent corruption of the commit contents. [4](#0-3) [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** app/src/lib/stores/app-store.ts (L3404-3432)
```typescript
  private async updateChangesWorkingDirectoryDiff(
    repository: Repository
  ): Promise<void> {
    const stateBeforeLoad = this.repositoryStateCache.get(repository)
    const changesStateBeforeLoad = stateBeforeLoad.changesState

    if (
      changesStateBeforeLoad.selection.kind !==
      ChangesSelectionKind.WorkingDirectory
    ) {
      return
    }

    const selectionBeforeLoad = changesStateBeforeLoad.selection
    const selectedFileIDsBeforeLoad = selectionBeforeLoad.selectedFileIDs

    // We only render diffs when a single file is selected.
    if (selectedFileIDsBeforeLoad.length !== 1) {
      if (selectionBeforeLoad.diff !== null) {
        this.repositoryStateCache.updateChangesState(repository, () => ({
          selection: {
            ...selectionBeforeLoad,
            diff: null,
          },
        }))
        this.emitUpdate()
      }
      return
    }
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

**File:** app/src/lib/patch-formatter.ts (L129-168)
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

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }
```
