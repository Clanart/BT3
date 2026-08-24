I have enough evidence now to write the analog finding.

### Title
Stale diff re-fetch at partial-commit staging time allows silent commit of unreviewed content - ([File: app/src/lib/git/apply.ts])

### Summary
The report's core invariant is that a value (price) read at one point in time is used later, in the same logical operation, without being refreshed/re-validated, allowing an attacker/user to combine an old and a new value inconsistently within one atomic action. In GitHub Desktop, the analogous invariant is: **the diff/line-selection a user reviews and clicks-to-stage must be the exact content that gets written into the index/commit**. `applyPatchToIndex` in `app/src/lib/git/apply.ts` re-reads the working-directory diff from disk at staging time via `getWorkingDirectoryDiff(repository, file)` [1](#0-0)  and then reuses the `file.selection` object (an "absolute line index" bitmap) that was computed against a **previously rendered** diff shown to the user in the Changes view [2](#0-1) . There is no check anywhere in the commit path that the diff fetched here is identical to the diff the user actually reviewed/selected against.

### Finding Description
The commit pipeline is:
1. UI renders a diff via `getWorkingDirectoryDiff` and lets the user select individual lines/hunks, producing a `DiffSelection` keyed by absolute line index into that diff [3](#0-2) .
2. On commit, `_commitIncludedChanges` passes the `WorkingDirectoryFileChange` (which carries that stale `DiffSelection`) straight to `createCommit` [4](#0-3) .
3. `createCommit` resets the index and calls `stageFiles`, which for partially-selected files calls `applyPatchToIndex` [5](#0-4) [6](#0-5) .
4. `applyPatchToIndex` calls `getWorkingDirectoryDiff` **again**, fetching a brand-new diff from the current on-disk content, and blindly formats a patch from it using the old `file.selection.isSelected(absoluteIndex)` bitmap [7](#0-6) [8](#0-7) .

There is a gap between when the diff is rendered/selected by the user and when this second diff fetch happens (status refresh, diff render, plus arbitrary UI dwell time before the user clicks "Commit"). If the file on disk changes in that window — e.g., a build tool, editor autosave, git hook, or a malicious background/CI process modifies the file, or the file is externally touched by a synced/cloned dependency — `getWorkingDirectoryDiff` returns hunks with different line counts, offsets, or content than what generated the selection. Because `formatPatch` indexes purely by `hunk.unifiedDiffStart + lineIndex` against the *new* diff [9](#0-8) , the same absolute indices can now resolve to different lines. This silently includes/excludes different content than the user selected, and `createCommit`/`git apply --cached` will proceed without any warning that the underlying diff shifted [10](#0-9) .

There is no guard comparing the diff fetched inside `applyPatchToIndex` against the one the user actually reviewed (unlike `updateChangesWorkingDirectoryDiff`, which does check for staleness of the *rendered* diff relative to file selection changes, but that check is UI-only and irrelevant to the staging path) [11](#0-10) .

### Impact Explanation
This is a "silent corruption of what the user commits" scenario. A user who carefully reviews a diff and selects only specific lines to stage can end up committing different, unreviewed lines/content if the file changes underneath them between review and click-to-commit. This could be leveraged by an attacker who controls something that touches the working tree in that window (a build script, an npm/postinstall hook, an editor plugin, or another automated process in the cloned repository) to inject unreviewed lines into a commit the user believes only contains their selected, reviewed changes — without any confirmation dialog or diff-integrity check.

### Likelihood Explanation
Medium-low likelihood: it requires something external to touch the exact file between diff render/selection and the commit click, but this is realistic in normal developer workflows (auto-formatters, file watchers/build tools, editor autosave, git hooks) and is deterministic once that race condition is hit — no attacker needs precise timing control from a remote foothold. It requires no admin rights, no local privileged access beyond running any process already permitted to touch files in the working directory.

### Recommendation
Do not re-fetch the diff at staging time; pass through and stage against the exact diff object that was rendered and used to build the `DiffSelection`, or re-validate (e.g., hash the file content) that the diff used to build the selection still matches current on-disk state before calling `git apply --cached`, aborting or re-prompting the user if it changed.

### Proof of Concept
1. Modify a tracked file, open Desktop's Changes view; it renders a diff via `getWorkingDirectoryDiff` and the user selects only a subset of lines (e.g., lines 1-5 of hunk 1).
2. Before clicking "Commit", have any process (a save-on-focus-loss editor, a watch script, a git hook) append/change lines in the same file, altering hunk boundaries without a fresh Desktop status refresh being consumed.
3. Click "Commit". `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` fetches a new diff of the now-modified file and formats a patch using the previously computed line-index selection [12](#0-11) .
4. Inspect the resulting commit: it contains lines the user never reviewed/selected, or omits lines they did select, demonstrating the diff used for review and the diff used for staging diverged silently.

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

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/stores/app-store.ts (L3453-3464)
```typescript
    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3693-3699)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
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
