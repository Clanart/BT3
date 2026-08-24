### Title
Partial-commit line selection is applied against a freshly re-fetched diff, allowing committed content to silently diverge from what the user reviewed and approved - ([File: app/src/lib/git/apply.ts])

### Summary
Like the Uniswap report — where `populateTransaction` fetches remote data and mutates the transaction after the user confirmed it, without re-showing the final content — GitHub Desktop's partial-commit flow lets the user select individual lines/hunks from a diff shown in the sidebar, but at actual commit time it re-derives a brand-new diff of the working directory and re-applies the user's line-index-based selection to it, rather than committing exactly the content the user visually reviewed.

### Finding Description
When a user stages part of a file (line/hunk selection) via the Changes sidebar, the app stores a `DiffSelection` keyed by absolute line index into the diff that was rendered on screen at that moment [1](#0-0) . That diff is loaded asynchronously and, per the app's own comments, "might have changed dramatically since last we loaded it" — the code only prunes now-invalid indices from being selectable, it does not invalidate or reconfirm the user's existing selection against new content [2](#0-1) .

When the user commits, `_commitIncludedChanges` takes whatever `WorkingDirectoryFileChange` objects (with their `selection`) currently live in app state and passes them straight to `createCommit` [3](#0-2) . `createCommit` calls `stageFiles`, which for any partially-selected file calls `applyPatchToIndex` [4](#0-3) .

Critically, `applyPatchToIndex` does **not** use the diff the user actually looked at. It re-fetches the diff from git on the spot — `const diff = await getWorkingDirectoryDiff(repository, file)` — and then builds the patch to apply to the index by calling `formatPatch(file, diff)`, which walks the **freshly-fetched** hunk lines and decides inclusion via `file.selection.isSelected(absoluteIndex)` [5](#0-4) [6](#0-5) . The `absoluteIndex`/`unifiedDiffStart` values are positional offsets into a diff structure, not stable identifiers tied to file content [7](#0-6) . If the file content on disk changes between when the diff was rendered/selected in the UI and when the commit button is pressed (e.g., an editor autosave, a background formatter, a build step, or any external/malicious process that touches the working tree — none of which requires local/admin access, just something writing to the repo the user already has open), the same numeric indices in `file.selection` now point at different lines in the newly computed hunks. The user's on-screen "confirmed" selection therefore gets silently reinterpreted against different content, and `git commit` produces a result that was never actually shown to the user before signing off.

This mirrors the report's exact defect pattern: data (line selection) is validated/confirmed by the user against one snapshot, but the actual operation (patch application via `git apply --cached`) is executed against separately, later-fetched data, with no step that re-displays or re-confirms the final patch content to the user.

### Impact Explanation
This can cause the user to commit (and subsequently push) content they never reviewed or approved — a silent corruption of what the user commits, which is explicitly listed as valid impact. Because commits are cryptographically/historically permanent and often pushed immediately, unintended lines (e.g., reverted security fixes, leftover debug code, or unintended reintroduction of previously-removed secrets/lines) could be committed and shared without the user's knowledge, especially in fast-changing files or when other tooling (linters, formatters, git hooks writing back to files) touches the working directory concurrently with normal Desktop usage.

### Likelihood Explanation
Requires a normal, unprivileged working-directory content change between diff render and commit action — no local/admin access or leaked credentials needed, just a plausible timing race with any process that modifies files in the repo the user has open (auto-formatters on save, linters, background build scripts, or another instance of git/an editor). Desktop's own code acknowledges this exact race ("The diff might have changed dramatically since last we loaded it") but only partially mitigates it for UI selectability, not for the actual patch content applied at commit time.

### Recommendation
- **Short term:** Before invoking `stageFiles`/`applyPatchToIndex` in `createCommit`, re-fetch the diff for each partially-selected file and compare it against the diff the selection was computed from (e.g., via content hash or `isSameDiff`, already used elsewhere in the diff switcher [8](#0-7) ). If they differ, abort the commit and force the user to re-review the updated diff and re-confirm their selection before proceeding.
- **Long term:** Make `applyPatchToIndex` operate on the exact diff snapshot the user selected against (pass the diff through explicitly, rather than re-fetching), and treat any drift between snapshot and current working tree as a hard stop requiring re-confirmation, consistent with the "show populated data before confirming" principle from the original report.

### Proof of Concept
1. Modify a large tracked file with a few sizeable hunks; open Desktop and select only certain lines from hunk 1 for commit, leaving hunk 2 unstaged/unselected.
2. Before pressing "Commit", have an external process (e.g., an editor auto-save, formatter, or a script simulating a background tool) insert/remove lines earlier in the same file, shifting subsequent line numbers without changing the diff shown in Desktop's already-rendered sidebar view.
3. Press "Commit". Desktop calls `createCommit` → `stageFiles` → `applyPatchToIndex`, which re-fetches the diff via `getWorkingDirectoryDiff` and reinterprets the stale `file.selection` indices against the new hunk layout [9](#0-8) .
4. Inspect the resulting commit (`git show`): it includes different lines than those visually checked in the sidebar prior to commit, confirming the displayed-vs-signed divergence.

### Citations

**File:** app/src/models/status.ts (L294-309)
```typescript
/** encapsulate the changes to a file in the working directory */
export class WorkingDirectoryFileChange extends FileChange {
  /**
   * @param path The relative path to the file in the repository.
   * @param status The status of the change to the file.
   * @param selection Contains the selection details for this file - all, nothing or partial.
   * @param oldPath The original path in the case of a renamed file.
   */
  public constructor(
    path: string,
    status: AppFileStatus,
    public readonly selection: DiffSelection
  ) {
    super(path, status)
  }

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

**File:** app/src/lib/git/update-index.ts (L109-168)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
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

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```

**File:** app/src/ui/diff/seamless-diff-switcher.tsx (L278-285)
```typescript
    if (
      currentFileContents !== null &&
      isSameFile(currentFileContents.file, fileToLoad) &&
      prevDiff !== null &&
      isSameDiff(prevDiff, diff)
    ) {
      return
    }
```
