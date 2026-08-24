### Title
Stale line-based diff selection is silently re-applied against a freshly re-fetched diff during partial commits, allowing corrupted/unintended content to be committed - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
GitHub Desktop lets a user select individual diff lines/hunks for a partial commit. That selection is stored as an immutable `DiffSelection` keyed by *absolute line index* into a diff snapshot rendered at selection time [1](#0-0) . When the user actually commits, `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff(repository, file)` [2](#0-1)  and then builds the patch by indexing into that fresh diff with the old, possibly-stale line indices from `file.selection.isSelected(absoluteIndex)` [3](#0-2) . If the tracked file's on-disk content changed between the moment the selection was made (diff render time) and the moment the commit patch is generated, the index-to-content mapping desyncs and the wrong lines are silently staged/committed — with no re-validation against the file that was actually diffed.

### Finding Description
The commit pipeline is:
1. UI renders a diff and the user builds a `DiffSelection` of absolute line indices against that diff snapshot (`updateChangesWorkingDirectoryDiff` caches `selection.diff` in app state) [4](#0-3) .
2. `_commitIncludedChanges` reads the current `state.changesState.workingDirectory.files` (carrying that selection) and passes them straight to `createCommit` without re-diffing to confirm the selection is still valid against current disk content [5](#0-4) .
3. `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff fresh from git (`getWorkingDirectoryDiff`) and blindly builds a unified-diff patch from it using the stale `file.selection` indices [2](#0-1) [6](#0-5) .
4. That patch is applied with `git apply --cached --unidiff-zero --whitespace=nowarn` [7](#0-6) , and then `git commit` is run — meaning the (possibly mismatched) staged content becomes the permanent commit.

Only one existing guard partially touches this class of bug: `updateChangesWorkingDirectoryDiff` recomputes `selectableLines` when the diff is *actively reloaded while the same file remains selected* [8](#0-7) , and `DiffContentsWarning` only warns about Unicode bidi characters or line-ending conversion, not about diff/selection staleness [9](#0-8) . Neither guard fires on the direct commit path (`_commitIncludedChanges`), because that path does not re-fetch/re-render the diff before submitting the stale selection to `createCommit`. There is no content hash, mtime check, or "diff changed since selection, please re-review" prompt anywhere in `apply.ts`, `commit.ts`, or `update-index.ts`.

The unsafe primitive is functionally the same class as the reward-report's issue: a value used at "settlement time" (commit/patch generation) is computed from state captured earlier (line selection against an older diff) without validating that the underlying position hasn't shifted, so an attacker who can cause the tracked file to change during the window between diff-render and click-commit can make Desktop stage/commit different content than what the user actually reviewed and selected — a silent corruption of what the user commits.

### Impact Explanation
A repository that ships with build/dev tooling that legitimately runs while the user has the app open (watch-mode formatters/linters, generators, `postinstall`/`prepare` scripts, husky-managed file rewriters, editor "format on save" driven by repo-tracked config) can rewrite tracked files between the time the user reviews a diff and selects specific lines and the time they click "Commit." Because Desktop re-diffs at apply time but reuses the old absolute-line-index selection, hunks can shift, causing:
- Unselected/malicious lines being silently included in the commit, or
- Intentionally-selected security-relevant lines being silently dropped, or
- `git apply` failing to line up cleanly and producing a differently-shaped patch than the user reviewed (mitigated somewhat by `--unidiff-zero`, which increases brittleness rather than safety).

This is a silent corruption of what the user commits and, if pushed, of what is published to a shared remote/PR — matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Likelihood is moderate: it requires only that the tracked file content changes on disk in the narrow window between the diff snapshot used for line selection and the commit action — a condition attacker-controlled repositories can engineer via bundled watch/build tooling that is commonly present and auto-run in JS/TS/web repositories that Desktop users routinely clone and open. It does not require local/physical access, admin rights, pre-existing malware, or leaked credentials — the trigger is entirely repository-content-driven (a cloned/fetched repository with legitimate-looking build tooling).

### Recommendation
- Before generating the patch in `applyPatchToIndex`/`stageFiles`, re-validate that the diff used for `file.selection` still matches the file's current on-disk state (e.g., compare a content hash/mtime, or the hunk boundaries) and abort/re-prompt the user if it has changed.
- Alternatively, capture the exact diff content used at selection time and pass it through to `formatPatch`/`applyPatchToIndex` instead of re-fetching a new diff at commit time, so the applied patch is guaranteed to be generated from the same content the user reviewed.
- Surface a clear warning/re-confirmation dialog (similar to `DiffContentsWarning`) when Desktop detects the working tree changed since the last diff render for a file with an active partial selection, prior to allowing the commit to proceed.

### Proof of Concept
Conceptual reproduction (cannot be executed in this sandbox; requires a Devin session with git/file access to confirm dynamically):
1. Create a tracked file with several lines; open it in Desktop's Changes view so a diff renders.
2. In the UI, select only specific individual lines within one hunk for a partial commit (`DiffSelection` records those absolute indices).
3. Before clicking "Commit," have an external process (simulating a repo-bundled watch/build script) insert or remove lines above the selected hunk in the same file, shifting subsequent line numbers, without the user re-opening/re-reviewing the diff.
4. Click "Commit." Trace execution: `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681`) passes the stale-selection file object to `createCommit` (`app/src/lib/git/commit.ts:15`) → `stageFiles` (`app/src/lib/git/update-index.ts:109`) → `applyPatchToIndex` (`app/src/lib/git/apply.ts:12`), which calls `getWorkingDirectoryDiff` fresh at line 60 and applies the old selection indices via `formatPatch` (`app/src/lib/patch-formatter.ts:129`).
5. Inspect the resulting commit (`getChangedFiles`) and confirm it contains lines the user never selected, or is missing lines the user did select — demonstrating silent corruption of committed content.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L41-53)
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
export class DiffSelection {
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

**File:** app/src/lib/patch-formatter.ts (L129-157)
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
```

**File:** app/src/lib/stores/app-store.ts (L3444-3453)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

    // A different file (or files) could have been selected while we were
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

**File:** app/src/ui/diff/diff-contents-warning.tsx (L45-63)
```typescript
  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }

    if (diff.lineEndingsChange) {
      items.push({
        type: DiffContentsWarningType.LineEndingsChange,
        lineEndingsChange: diff.lineEndingsChange,
      })
    }

    return items
  }
```
