## Analysis

Confirmed the mechanism: `_commitIncludedChanges` in `app-store.ts` reads `file.selection` from state (the line-index selection the user made against the diff *last displayed* in the UI) and passes it straight to `createCommit` → `stageFiles` → `applyPatchToIndex`. `applyPatchToIndex` (`app/src/lib/git/apply.ts`) does **not** reuse the diff the user actually looked at — it re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff(repository, file)` at staging time and then reinterprets the *old* selection's absolute line indices against that *fresh* diff to build the patch (`formatPatch(file, diff)`). [1](#0-0) [2](#0-1) [3](#0-2) 

Desktop is aware that a diff "might have changed dramatically since last we loaded it" and re-clamps selectable lines — but only in the UI refresh path (`updateChangesWorkingDirectoryDiff`), not at commit time: [4](#0-3) 

### Title
Partial-commit staging re-derives the patch from a re-fetched diff, letting stale line-index selections silently commit unintended content - (File: app/src/lib/git/apply.ts)

### Summary
When a user stages only *some* lines of a file (a partial/interactive commit), Desktop stores the selection as a set of absolute line indices tied to the diff that was rendered in the UI at selection time. At actual commit time, `applyPatchToIndex` throws away that diff and re-runs `git diff` against the current working directory, then re-applies the old index-based selection to the newly fetched hunks to build the patch that is `git apply --cached`'d into the index. There is no check that the new diff is structurally identical to the one the user reviewed.

### Finding Description
This is the same class of bug as the report: a value (here, "which lines get committed") is computed against state that is fixed at one point in time (the diff/selection shown to the user) but the actual operation is executed against a different, later-read state (the fresh `git diff` result), with no bound/consistency check tying the two together. In the smart-contract report the "price" could move between quote and execution; here the "diff content/line offsets" can move between the moment the user selects lines to stage and the moment `createCommit`/`stageFiles`/`applyPatchToIndex` actually runs.

Any process that touches the file between "user finishes selecting lines" and "user clicks Commit" — a formatter/linter/build watcher, an editor autosave, a `post-checkout`/file-watching tool shipped in the cloned repository's tooling, or simply the user leaving the app open — will shift line numbers and hunk boundaries. Because `formatPatch`/`applyPatchToIndex` blindly reapplies the old `DiffSelection`'s absolute indices to the newly fetched hunks, the resulting patch can select entirely different, unrelated lines than the ones the user visually checked in the UI, and `git apply --cached` will happily apply it as long as it's syntactically valid against the new content. [5](#0-4) [6](#0-5) 

Existing guards do not stop this path:
- The staleness re-validation only exists in `updateChangesWorkingDirectoryDiff`, which runs when the UI reloads a diff for *display*, not immediately before `createCommit` stages files.
- `createCommit`/`stageFiles`/`applyPatchToIndex` perform no comparison between the diff used to build the selection and the diff fetched right before applying it.

### Impact Explanation
This matches the "silent corruption of what the user commits" impact category: the user could believe they are committing only reviewed, approved lines, but a working-directory change they didn't cause (e.g., a build/format tool bundled in an untrusted cloned repository, or a git hook/tool that runs in the background) shifts the file content between selection and commit, causing different content — potentially attacker-influenced content from a hostile fetched/cloned repository's tooling — to be silently included in or excluded from the commit, without any warning to the user.

### Likelihood Explanation
Requires a specific timing window (file modified between line-selection and clicking Commit) and typically some tooling running in the workspace that rewrites files (very common — linters/formatters/dev servers, some shipped by the repository itself, e.g. via `package.json` scripts or watch tasks a user might run alongside Desktop). No local/physical access or credential leakage is required beyond normal use of a cloned/fetched repository, but it does depend on an external file-modifying event coinciding with the review-to-commit window, so likelihood is moderate rather than high.

### Recommendation
Bind the on-disk diff used for staging to the diff instance the user actually reviewed/selected against:
- Persist a hash/fingerprint (or the diff object itself) alongside the `DiffSelection` and, in `applyPatchToIndex`/`stageFiles`, compare the newly fetched diff against it before generating the patch.
- If they differ, refuse to stage the stale selection and instead force the UI to re-show the current diff for re-selection/re-confirmation, analogous to enforcing a caller-supplied bound (`maxReturnAmount`-style check) before executing an action based on a value that may have changed.

### Proof of Concept
1. In Desktop, modify a tracked file with several independent changes/hunks.
2. Open the Changes view, select the file, and manually deselect specific lines/hunks for partial commit (this fixes a `DiffSelection` based on line indices from the diff rendered at that moment).
3. Before clicking "Commit," have an external process (e.g., a formatter/watcher running in the repo, or simply editing the file in another editor) insert/remove lines above the hunks you selected, shifting line numbers but not opening the Desktop UI again to refresh the diff.
4. Click Commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` and reapplies your old `DiffSelection` indices to it.
5. Inspect the resulting commit: it stages/omits lines that do not correspond to what was visually selected in the last-rendered diff, because the line offsets moved.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L3681-3698)
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

**File:** app/src/lib/patch-formatter.ts (L132-170)
```typescript
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

        anyAdditionsOrDeletions = true
```
