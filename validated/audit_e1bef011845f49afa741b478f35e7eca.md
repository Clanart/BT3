## Analog Found

### Title
Stale line-selection state applied against a freshly re-fetched diff causes silent corruption of partially-staged commits - ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop's partial-commit ("stage selected lines") feature stores the user's line selection as *positional indices* into a diff that was rendered earlier in the UI. When the commit is actually created, `applyPatchToIndex` re-fetches a brand-new `git diff` for the file and blindly reapplies those stale positional indices to it, with no verification that the diff is unchanged since the user made their selection. If the working-directory content of that file changes between the time the diff was rendered/selected and the time the commit patch is generated, the positional selection silently maps onto different logical lines, and unintended content can be included in (or excluded from) the resulting patch, with no warning, error, or diff refresh at commit time.

### Finding Description
The commit flow is:
1. The UI loads a diff for the selected file via `getWorkingDirectoryDiff` and lets the user choose lines to include, recorded in a `DiffSelection` keyed by absolute line index into that specific diff [1](#0-0) .
2. `app-store.ts`'s `updateChangesWorkingDirectoryDiff` does re-validate selectable lines when the *app itself* proactively reloads the diff (e.g., after a status refresh) [2](#0-1)  — but this reconciliation only happens on that specific refresh path, not at commit time.
3. When the user clicks "Commit," `_commitIncludedChanges` takes whatever `WorkingDirectoryFileChange` objects (with their `selection`) are currently cached in state and passes them straight to `createCommit` [3](#0-2) .
4. For files with a partial selection, `stageFiles` calls `applyPatchToIndex` [4](#0-3) .
5. `applyPatchToIndex` re-fetches the diff **again**, from scratch, at commit time (`getWorkingDirectoryDiff(repository, file)`), and feeds it straight into `formatPatch(file, diff)` [5](#0-4) .
6. `formatPatch` walks the *new* diff's hunks and decides what to include/exclude purely based on `file.selection.isSelected(absoluteIndex)` — i.e., the stale index recorded against the *old* diff [6](#0-5) .

There is no hash/content equality check, no re-diff-and-reconcile step, and no user-facing warning between steps 1 and 5. If the on-disk content of the file changes in that window — for example due to a smudge/clean filter or a `post-checkout`/`post-merge` hook shipped inside a cloned/fetched malicious repository that asynchronously rewrites tracked file content — the line offsets in the new diff will not correspond to what the user actually reviewed and selected. `formatPatch` will then silently select/deselect the wrong lines by absolute index, producing a patch that does not represent the user's actual intent, and `applyPatchToIndex` applies it directly with `git apply --cached` [7](#0-6) .

### Impact Explanation
This breaks the invariant that "what the user selected in the diff viewer is what gets committed." An attacker who controls a cloned/fetched repository (via a git filter driver defined in `.gitattributes`, or a hook) can arrange for tracked file content to change between diff render and patch application, causing Desktop to silently commit (and potentially push) content the user never selected, or to silently drop content the user intended to include — a silent corruption of what the user commits, without any error or confirmation dialog.

### Likelihood Explanation
Exploitation requires the attacker-controlled repository to trigger an asynchronous or hook/filter-driven modification of a tracked file precisely in the window between the diff being rendered for partial selection and the user clicking Commit. This is a race-condition-dependent path (not instantaneously reliable), which lowers likelihood somewhat, but the window can be widened by hook/filter behavior the attacker fully controls (e.g., a slow smudge filter invoked by a background `git status`/`lfs` operation), and no code anywhere in the commit path validates that the diff hasn't changed since selection.

### Recommendation
Before applying a partial-selection patch in `applyPatchToIndex`, re-validate that the freshly fetched diff is structurally identical (or at least that hunk boundaries/line content the selection depends on are unchanged) to the diff the selection was made against; if it differs, refuse to commit that file's partial selection and force the user to re-review the diff — mirroring the same reconciliation `updateChangesWorkingDirectoryDiff` already performs, but doing it authoritatively at commit time rather than opportunistically on refresh.

### Proof of Concept
1. Clone a malicious repository containing a `.gitattributes` filter (`clean`/`smudge`) or hook (`post-checkout`) that rewrites a tracked file's content shortly after it is touched.
2. In Desktop, open the Changes view for that file and select only specific lines to stage (leaving others unselected).
3. Trigger the filter/hook to rewrite the file in the background (e.g., checkout another branch and back, or let the hook fire on a timer) so the file's line layout shifts before the user clicks Commit.
4. Click Commit. `applyPatchToIndex` re-diffs the now-mutated file and `formatPatch` reapplies the old absolute-index selection to the new hunks, producing a patch whose staged lines no longer correspond to the user's actual selection — verify via `git show` on the resulting commit that it contains content the user did not select in the UI.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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

**File:** app/src/lib/stores/app-store.ts (L3686-3698)
```typescript
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

**File:** app/src/lib/git/apply.ts (L52-58)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]
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
