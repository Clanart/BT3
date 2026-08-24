## Analysis

The UMA bug's broken invariant is: **a value used to make a user-facing security decision is "snapshotted" lazily and re-derived at action time, so an attacker can make the value differ between the decision and the action.** In GitHub Desktop, the equivalent construct is the **partial/line-level commit selection workflow**, where the user reviews a diff, selects specific lines, and Desktop later re-reads the working tree to build the patch that is actually applied.

### The TOCTOU path

1. When a file is selected in the Changes list, Desktop renders a diff and the user picks specific lines/hunks to include. The result is stored as `file.selection`, which is **purely index-based** (line positions in a diff), not content-hashed: [1](#0-0) 

2. When the user clicks "Commit", `_commitIncludedChanges` passes the *same stale* `WorkingDirectoryFileChange` (with its old `file.selection`) straight into `createCommit`: [2](#0-1) 

3. `createCommit` clears the index and calls `stageFiles`, which for any partially-selected file calls `applyPatchToIndex`: [3](#0-2) [4](#0-3) 

4. `applyPatchToIndex` does **not** reuse the diff the user actually reviewed. It re-fetches a brand-new diff from the working directory at staging time: [5](#0-4) 

5. That freshly-fetched diff is fed to `formatPatch` together with the old, index-based `file.selection`. `formatPatch` blindly maps the stale line indices onto the new hunk/line content with no check that the underlying diff is unchanged from the one the user approved: [6](#0-5) 

There is no diff fingerprint/hash comparison anywhere between the "check" (diff rendered + selection made) and the "use" (`getWorkingDirectoryDiff` call inside `applyPatchToIndex`, followed by `git apply --cached`) — exactly the missing guard that UMA's `snapshotCurrentRound` was introduced to add for reveal/vote balances.

### Why this matters

If the tracked file's on-disk content changes between the moment the user reviews/selects lines and the moment the commit button's async chain reaches `applyPatchToIndex` (a window that can be arbitrarily long if hooks run, or if any other process/tool tied to the opened repository rewrites the file — e.g. a bundled formatter/linter/build watcher script from the repository's own tooling that a developer normally runs alongside Desktop), the line indices in `file.selection` are applied against different hunk content than what the user visually approved. Because `formatPatch` only reasons about **line positions**, not content, this can silently:
- include content the user never saw/approved (attacker-injected lines shifted into the "selected" index range), or
- silently drop lines the user intended to commit,

corrupting exactly the value the UMA-analog protects: what actually gets snapshotted/locked in before the "reveal" (here, the final `git apply --cached` / commit) versus what the user was shown.

### Title
Stale line-index diff selection is reapplied to a freshly re-read working-directory diff during commit, allowing silent corruption of partial commits — (File: `app/src/lib/git/apply.ts`)

### Summary
Desktop's partial-commit ("stage some lines") flow stores the user's line selection as positional indices against a diff rendered earlier in the UI. At commit time, `applyPatchToIndex` re-fetches the diff from disk instead of reusing the reviewed one, then reapplies the stale indices via `formatPatch` with no equality check between the two diffs.

### Finding Description
`file.selection` (`app/src/lib/stores/app-store.ts:3444-3448`) is computed once, when the Changes view renders a diff. This same object is threaded unchanged through `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex`. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) calls `getWorkingDirectoryDiff` again, producing a second, independently-read diff of the working tree. `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`) then walks this *new* diff's hunks and decides what to keep purely based on `file.selection.isSelected(absoluteIndex)` — a positional index carried over from the *old* diff. There is no verification that the two diffs are identical.

### Impact Explanation
If working-tree content for the file changes between "check" (diff render/selection) and "use" (`applyPatchToIndex`'s re-fetch), the final patch applied to the index (and thus committed/pushed) can silently differ from what the user reviewed and intended — either including unreviewed content or dropping content the user meant to keep. This is a silent corruption of what the user commits, matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The window between selection and staging is asynchronous and can be non-trivial (hook execution, UI interaction latency, background refresh cycles). Any process with write access to the tracked file during that window — including tooling that ships with/alongside the opened repository and runs automatically (watchers, formatters, build scripts) — can trigger the mismatch without any unusual user action beyond normal partial-commit usage, which is a core, everyday Desktop feature.

### Recommendation
Cache the exact diff object (or a content hash of it) used to build `file.selection`, and pass it through to `applyPatchToIndex`/`formatPatch` instead of re-fetching. If the working directory has changed since the diff was generated, abort and force Desktop to re-render the diff and require the user to re-confirm their selection before staging, mirroring the "snapshotCurrentRound"-style explicit freeze.

### Proof of Concept
1. Open a repo in Desktop, modify a tracked file with multiple hunks, and in the Changes view select only specific lines from hunk A for the next commit (leave hunk B unselected).
2. Before clicking "Commit", let another process (e.g., a project-bundled watch/format script) rewrite the file so line positions shift (e.g. insert/remove lines above hunk A).
3. Click "Commit". Because `applyPatchToIndex` re-reads the diff and reapplies the old positional selection, the resulting commit's actual hunk content will not match what was shown/selected in step 1 — demonstrated by comparing the diff rendered pre-commit to `git show` on the resulting commit.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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
