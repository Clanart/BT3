### Title
Stale line-selection replay lets a mid-review working-tree change silently alter what a partial commit actually stages - ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop lets a user select individual diff lines/hunks for a partial commit. The line selection (`DiffSelection`) is captured as **absolute line indices** against a diff snapshot rendered earlier in the UI. When the commit is actually created, Desktop does not reuse that reviewed diff — it re-fetches a brand-new diff of the file and blindly replays the old index-based selection against it, with no check that the diff structure the user reviewed still matches the one being staged.

### Finding Description
The commit path works as follows:

1. The UI renders a diff and lets the user toggle lines via `DiffSelection.withLineSelection`/`withRangeSelection`, storing selection state as absolute indices into that diff's hunks [1](#0-0) .
2. On commit, `_commitIncludedChanges` passes the working-directory files (each carrying its `file.selection`) straight into `createCommit` [2](#0-1) .
3. `createCommit` calls `stageFiles`, which for any file with a `Partial` selection calls `applyPatchToIndex` [3](#0-2) [4](#0-3) .
4. `applyPatchToIndex` **fetches a fresh diff of the working directory at commit time** — not the diff the user reviewed — via `getWorkingDirectoryDiff(repository, file)` [5](#0-4) .
5. `formatPatch` then reapplies the old `file.selection.isSelected(absoluteIndex)` decisions against this newly-fetched diff's hunks with no validation that hunk boundaries, line counts, or content still correspond to what was originally shown to the user [6](#0-5) .

There is no hash, ETag, mtime, or content comparison anywhere in this path to detect that the working-directory file changed between the time the user reviewed/selected lines and the time `applyPatchToIndex` regenerates the diff and applies the stale selection. If the file's content changes in that window (e.g., a build/watch/format tool, a `post-checkout`/`pre-commit`-style hook enabled via a tracked `core.hooksPath` config that npm-installed tooling wires up, or any other background process writing to the tracked file), the absolute line indices from the old selection no longer line up with the same logical lines in the new diff. `formatPatch` will silently select/deselect different hunks/lines than the ones the user visually reviewed and approved, and `git apply --cached` will commit that mismatched patch without complaint (as long as it's a valid patch against the current index).

### Impact Explanation
This breaks the fundamental invariant of partial/hunk-based staging: "what I visually selected is exactly what gets committed." Because the check is entirely offset/index-based and re-derived against a freshly regenerated diff rather than the one shown to the user, the commit can silently include lines the user never selected (or exclude lines they did select) whenever the working tree changes between diff render and stage time. This is a silent corruption of what the user commits — potentially staging unreviewed/unintended content (including attacker- or tool-injected changes) under a commit message the user believed corresponded to their manual selection.

### Likelihood Explanation
The window between "user reviews diff and selects lines" and "user clicks Commit" is UI-driven and can be arbitrarily long (the user can review, get distracted, then commit later). Any concurrent writer to the working directory during that window — file watchers, formatters/linters running on save, editor auto-save, or any other tooling triggered while the repo is open — reliably falls into this gap since there is no revalidation step at all before `applyPatchToIndex` regenerates the diff. No special privileges or race-winning timing precision are required beyond a normal "file changes while a commit dialog is open" scenario, which is common in real development workflows.

### Recommendation
Before staging a `Partial` selection, Desktop should validate that the diff being applied in `applyPatchToIndex`/`formatPatch` is the same one the selection was computed against — e.g. by comparing a content hash/blob OID of the file (or the full raw diff text) captured at selection time to the one obtained immediately before staging, and if they differ, refuse to commit and force the user to re-review the diff (similar in spirit to requiring `maxRewardPerVote` to only move in a safe direction instead of blindly trusting a stale value). At minimum, surface a clear error/refresh instead of silently applying an index-based selection to structurally different hunk data.

### Proof of Concept
1. Open a repository in GitHub Desktop with a modified tracked file containing multiple hunks.
2. In the Changes view, review the diff and select only specific lines/hunks for the commit (leaving others unselected) — this captures `DiffSelection` as absolute indices against the currently rendered diff.
3. Before clicking "Commit," have a background process (editor auto-save, a watch/format task, or any other writer with access to the working directory) modify the same file, changing the number/position of lines in a way that shifts hunk boundaries (e.g., adds/removes a line above the hunks you deselected).
4. Click "Commit." `applyPatchToIndex` calls `getWorkingDirectoryDiff` again [5](#0-4)  and `formatPatch` reapplies the old absolute-index selection to the new hunk layout [6](#0-5) .
5. Inspect the resulting commit: it will contain a different set of included/excluded lines than what was visually selected in step 2, with no warning to the user that the diff had changed.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L1-13)
```typescript
import { assertNever } from '../../lib/fatal-error'

/**
 * The state of a file's diff selection
 */
export enum DiffSelectionType {
  /** The entire file should be committed */
  All = 'All',
  /** A subset of lines in the file have been selected for committing */
  Partial = 'Partial',
  /** The file should be excluded from committing */
  None = 'None',
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

**File:** app/src/lib/git/apply.ts (L52-60)
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
