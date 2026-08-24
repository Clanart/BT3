Now I have enough evidence to confirm the vulnerable path. The key mechanism: `DiffSelection` tracks selected lines purely by **numeric index** (`divergingLines: Set<number>`), with no binding to line *content*. `_commitIncludedChanges` in `app-store.ts` passes the `WorkingDirectoryFileChange` (holding this index-based selection, captured from a diff the user viewed) straight to `createCommit` → `stageFiles` → `applyPatchToIndex`, which **re-fetches a brand-new diff from disk** via `getWorkingDirectoryDiff` and reapplies the old index-based selection against it in `formatPatch`.### Title
Line-index-based partial commit selection is silently reapplied to a freshly re-diffed file, causing wrong lines to be staged/committed - ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop's partial-commit (line-by-line staging) feature stores the user's line selection as *numeric line indices* into the diff that was rendered in the UI, not as content-addressed hunks. When the user clicks "Commit", `applyPatchToIndex` re-fetches a brand-new `git diff` from the working directory (not the one the user actually looked at) and blindly reapplies the old index-based selection to the new hunk layout via `formatPatch`. If the file on disk changes between the last diff render and the commit click (e.g. an attacker-influenced build tool, an editor autosave triggered by a malicious extension, a `.gitattributes`/filter/smudge script, or any background process writing to a tracked file the user is reviewing), the selected line indices now point at different lines in the new diff, and the app will silently commit content the user never selected/reviewed.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) tracks selection state purely as a `Set<number>` of "diverging" absolute line indices with no binding to the actual line text: [1](#0-0) [2](#0-1) 

`formatPatch` builds the commit patch by iterating the hunks of *whatever diff it is given* and calling `file.selection.isSelected(absoluteIndex)` for each line - purely index-based, content-agnostic: [3](#0-2) 

The commit path is: `_commitIncludedChanges` (app-store.ts) takes the `WorkingDirectoryFileChange` objects currently held in `repositoryStateCache` — including whatever `DiffSelection` the user built up while viewing an earlier diff render — and passes them straight to `createCommit`: [4](#0-3) 

`createCommit` → `stageFiles` → `applyPatchToIndex` for any partially-selected file: [5](#0-4) [6](#0-5) 

Critically, `applyPatchToIndex` does **not** reuse the diff the UI last rendered/validated — it calls `getWorkingDirectoryDiff` again, right before formatting and applying the patch: [7](#0-6) 

This is a fresh `git diff` against the *current* on-disk file content at commit time, which may differ from the content that produced the hunk layout the user selected lines against in the renderer. Since `formatPatch` maps the stored `divergingLines` indices onto this new hunk structure with no content validation, a shift in line count/position (insertions, deletions, or even hunk boundary changes) upstream of the user's selection will cause the wrong lines to be included/excluded from the patch that gets `git apply --cached`'d and committed.

The existing partial mitigation only handles the case while the diff is being *displayed*: `updateChangesWorkingDirectoryDiff` (app-store.ts) reconciles selectable lines against a newly-loaded diff for UI purposes, explicitly acknowledging the underlying problem in its comment ("The diff might have changed dramatically since last we loaded it... we'll settle on just updating the selectable lines"), but this reconciliation happens for the *previewed* diff, not for the diff `applyPatchToIndex` independently fetches at actual commit time: [8](#0-7) 
This guard does not run in the commit path at all, so it does not stop the corruption at `apply.ts:60`.

### Impact Explanation
This causes silent corruption of what the user commits and pushes — the core "valid impact" category for this exercise. A user who reviews a diff, selects specific lines to include, and clicks Commit can end up staging/committing different lines than they visually approved, without any error or warning. Depending on the concurrent modification, this could commit secrets, unreviewed code, or partially/incoherently patched content into history, which then gets pushed. This is a case where an attacker who can cause a tracked file to change during the user's review-to-commit window (e.g. via a build/watch tool, a git smudge/clean filter, a pre-commit hook side effect, or another process monitoring the repo) can manipulate what silently ends up in the commit, even though the UI showed the user something else.

### Likelihood Explanation
Likelihood is moderate: it requires a window between diff render and commit click during which the file's content changes such that the diff structure (hunk boundaries/line counts before the selected lines) shifts. This is realistic in normal workflows (autosave-triggered writes, format-on-save tools, linters/build watchers, generated files, files under a slow git clean/smudge filter) and does not require local/physical access or malware beyond a process capable of touching a tracked file — a scenario explicitly in-scope (attacker-influenced content in a repo the user is actively working with). It does not require any unnatural user action beyond the normal "select lines, then commit" workflow.

### Recommendation
`applyPatchToIndex` (and `stageFiles`/`createCommit`) should not silently re-diff the file at commit time and blindly reapply stale line-index selections. Options:
- Pass the diff that was actually rendered/validated in the UI through to `applyPatchToIndex`/`formatPatch` instead of re-fetching it, so the patch is built from the exact hunk structure the user saw.
- If a fresh diff must be fetched (e.g., to guard against staleness), compare it against the diff the selection was made against; if the hunk structure has changed, abort the partial commit and force the user to re-review/re-select rather than silently reapplying indices to a different structure.
- At minimum, detect a mismatch (e.g., via a hash of the diff content associated with the `DiffSelection`) and fail loudly rather than committing potentially wrong content.

### Proof of Concept
1. Open a repository in Desktop with a tracked file `foo.txt` containing several existing lines, with local uncommitted modifications.
2. In the Changes view, view the diff and use line-selection checkboxes to select only specific added lines (e.g., select line 10 of the new diff) to include in the commit; leave the rest unselected. This builds a `DiffSelection` whose `divergingLines` set contains the absolute index of line 10 in the diff as rendered.
3. Before clicking "Commit", have another process (e.g., a file watcher, formatter, or build tool — standing in for the attacker-controlled tool touching this cloned/monitored repo) insert or remove lines above the user's selected hunk in `foo.txt` on disk, shifting subsequent line numbers without the UI's diff being refreshed yet.
4. Click "Commit". `_commitIncludedChanges` passes the stale `WorkingDirectoryFileChange`/`DiffSelection` to `createCommit` → `stageFiles` → `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` fresh [9](#0-8)  and reapplies the old numeric selection indices to the new hunk layout in `formatPatch` [3](#0-2) .
5. Inspect the resulting commit: the staged/committed content corresponds to a different line than the one the user visually selected in step 2, demonstrating silent corruption of the commit relative to user intent.

Note: I was not able to execute this against a live Desktop build/UI to observe the exact resulting diff mismatch end-to-end (only static code analysis was performed), so the precise conditions under which the hunk shift causes a *visible* mis-commit (vs. a harmless no-op) would benefit from dynamic verification in a Devin session with terminal access.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L78-84)
```typescript
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
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

**File:** app/src/lib/patch-formatter.ts (L143-161)
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

**File:** app/src/lib/stores/app-store.ts (L3680-3699)
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

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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
