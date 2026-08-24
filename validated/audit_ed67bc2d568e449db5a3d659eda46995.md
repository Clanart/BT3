### Title
Partial-commit staging re-fetches a fresh diff and re-applies stale UI line-selection indices, allowing silent corruption of what is actually committed - (File: `app/src/lib/git/apply.ts`)

### Summary
The reported Perennial bug is a stale-value TOCTOU: a security decision (`maintenanceInvariant`) is evaluated against a cached oracle price instead of the state at the moment of the action, letting a user withdraw against an outdated valuation. The Desktop analog is structurally identical: the user's *line-selection* for a partial commit is computed against one diff snapshot shown in the UI, but the actual patch that gets staged and committed is built from a **second, independently re-fetched diff** taken at staging time. If the tracked file's content on disk changes between those two reads, the old line-index selection is silently re-applied to different diff content, and the wrong hunks/lines get committed with no user awareness.

### Finding Description
When a user reviews a diff and selects individual lines/hunks to include in a commit, that selection (`DiffSelection`, a set of absolute line indices) is recorded against the diff object that was fetched and rendered at that time via `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts` (lines 3404-3513). The code there even acknowledges the staleness risk explicitly: [1](#0-0) 

That reconciliation only trims out lines that no longer exist; it does not guarantee the remaining indices still map to the same conceptual lines if the file has shifted.

When the user clicks Commit, `_commitIncludedChanges` synchronously reads the `WorkingDirectoryFileChange` objects (with their `selection`) from the cached state: [2](#0-1) 

That list is passed through `createCommit` → `stageFiles` → `applyPatchToIndex`. Critically, `applyPatchToIndex` does **not** reuse any diff that was already computed/reconciled — it fetches a brand-new diff from disk right before building the patch: [3](#0-2) 

That freshly fetched diff is then handed to `formatPatch`, which maps the *old* `file.selection.isSelected(absoluteIndex)` bitset onto the *new* diff's hunks by absolute line index: [4](#0-3) 

The broken invariant: the app assumes "the set of selected absolute line indices computed against diff snapshot A still faithfully identifies the same lines in diff snapshot B (fetched later, at staging time)". Nothing revalidates that assumption before `git apply --cached` runs. Just like `maintenanceInvariant` trusting a stale oracle price instead of re-syncing before permitting withdrawal, `applyPatchToIndex` trusts a stale selection instead of re-validating it against the diff it is about to apply.

### Impact Explanation
If the working tree file is modified between the moment the user reviews/selects lines in the Changes view and the moment `applyPatchToIndex` re-reads the file (e.g., by a build tool, an editor autosave, a `pre-commit`/other git hook, a symlinked/watched file, or content written by a malicious process spawned from something the user just did, such as opening a cloned/fetched repository that runs a build step or IDE plugin), the absolute line indices selected by the user can land on entirely different lines in the newly generated diff. This produces a commit whose actual content differs from what the user reviewed and approved — a direct instance of "silent corruption of what the user commits", one of the explicitly listed valid impact classes. Because Desktop shows no diff confirmation between "Commit" click and the actual `git commit`, the user has no way to notice the discrepancy before it is pushed to a remote (and potentially reviewed/merged by others), enabling injection of unintended or attacker-influenced content into the repository history under the user's authorship.

### Likelihood Explanation
This requires (a) a partial/line-level selection (a routine, encouraged Desktop workflow) and (b) the underlying tracked file changing on disk in the small window between the diff being rendered/selected and the commit's internal re-fetch of the diff during staging. That window is normally short, but many ordinary Desktop workflows widen it: large repos, slow disks, hook execution, or file watchers/build tools that legitimately touch files shortly after a user finishes editing and starts committing. An attacker who can influence a cloned/fetched repository to trigger any background regeneration of a tracked file (e.g., via a build script, generated file, or a hook the user did not consciously invoke) can increase the likelihood of hitting this window without any unusual user action beyond a normal "select some lines, click Commit."

### Recommendation
Do not re-fetch a brand-new diff at staging time and reapply an older selection to it. Instead:
- Persist the exact diff object (or its content hash/hunks) that was used to compute the user's selection, and pass that same diff through to `applyPatchToIndex`/`formatPatch` rather than calling `getWorkingDirectoryDiff` again.
- Alternatively, before staging, re-diff the file and compare it against the diff the selection was made from; if they differ, abort the partial commit and force the user to re-review/re-select rather than silently applying old indices to new content.
- Add a final verification step after `git apply --cached` (e.g., `git diff --cached` compared against the expected file selection) to catch mismatches before allowing `git commit` to proceed.

### Proof of Concept
1. Modify a tracked file and open it in Desktop's Changes view; a diff is computed and cached (`updateChangesWorkingDirectoryDiff`).
2. Select only specific lines (e.g., lines 1-3) for inclusion in the commit.
3. Before clicking Commit, have another process (simulating a build tool/hook/background writer triggered by the cloned repo) append or shift lines in the same file so the line numbering changes, without Desktop re-rendering the diff view for that selection.
4. Click Commit. `_commitIncludedChanges` passes the stale `selection` (indices for lines 1-3 of the *old* diff) into `createCommit` → `stageFiles` → `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` again at `app/src/lib/git/apply.ts:60` and builds `formatPatch` against the *new* diff using the *old* absolute indices.
5. Inspect the resulting commit (`git show`) — it can contain different, unreviewed content than what corresponded to lines 1-3 in the diff the user actually saw and approved before pressing Commit.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3479-3493)
```typescript
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

**File:** app/src/lib/git/apply.ts (L52-66)
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
