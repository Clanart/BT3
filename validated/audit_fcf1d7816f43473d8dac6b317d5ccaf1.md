The `H‑01` report's underlying flaw is a classic "stale computed value" bug: a value (`totalValues`) is computed from state at time T0, an intervening step mutates the real state (fees), and a later check/action (`_isTargetWeightMet`) is performed against the outdated T0 snapshot instead of the mutated state, silently producing a wrong result. GitHub Desktop has a structurally identical pattern in its partial-commit ("stage-by-line") pipeline.

### Title
Line-selection state built from a stale diff is silently re-applied to a freshly re-fetched diff when staging partial commits, corrupting what actually gets committed - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user selects individual lines/hunks to include in a commit, Desktop records the selection as line-index offsets (`DiffSelection`) computed against a specific `IDiff` object that was fetched and rendered in the UI. When the commit is actually created, `stageFiles` calls `applyPatchToIndex`, which **re-fetches a brand-new diff** from disk via `getWorkingDirectoryDiff` and blindly reapplies the old line-index selection to it, then applies the resulting patch with `git apply --cached --unidiff-zero --whitespace=nowarn`. There is no check that the newly fetched diff is the same diff the selection was computed against.

### Finding Description
The staging pipeline is:
1. UI loads a diff and lets the user pick lines: `updateChangesWorkingDirectoryDiff` fetches a diff and stores `DiffSelection` indices against it [1](#0-0) .
2. On commit, `_commitIncludedChanges` takes the *cached* `workingDirectory.files` (with their previously computed `selection`) straight from `repositoryStateCache` and passes them to `createCommit` [2](#0-1) .
3. `createCommit` unstages everything and calls `stageFiles` [3](#0-2) .
4. For any file with a partial selection, `stageFiles` calls `applyPatchToIndex` [4](#0-3) .
5. `applyPatchToIndex` **re-fetches the diff again, independently, at this later point in time**, then calls `formatPatch(file, diff)` using the *old* `file.selection` line indices against this *new* diff, and applies it with reduced-context flags: [5](#0-4) [6](#0-5) .

`formatPatch` walks the (new) diff's hunks and simply asks `file.selection.isSelected(absoluteIndex)` for each line by its position — it has no knowledge of whether the diff's content still matches the diff the indices were computed from [7](#0-6) .

If the working-tree content of the selected file changes between step 1 (diff shown to the user, selection made) and step 5 (diff re-fetched for staging) — e.g. a file watcher, an editor's format/autosave-on-focus-loss, a build tool, or Git's own line-ending normalization (`core.autocrlf`, which Desktop explicitly detects and warns about via `parseLineEndingsWarning`) touches the file in that window — the hunk boundaries and line offsets shift. The stale `DiffSelection` indices then no longer point at the lines the user actually selected in the new diff, yet `formatPatch` still produces a syntactically valid patch, and `git apply --unidiff-zero --whitespace=nowarn` is deliberately configured to be lenient (zero required context lines, no whitespace validation), so it is applied without error. The commit ends up containing different content than what the user saw and approved in the UI.

There is no re-validation step anywhere in this path (no diff hash/etag comparison, no re-check that hunk headers still line up) analogous to the missing `totalValues` re-sync in the report — only a client-side "selectable lines" reconciliation exists, but that only prunes selection state for *currently displayed* diffs, it never runs at staging time nor is consulted by `applyPatchToIndex` [8](#0-7) .

### Impact Explanation
This causes silent corruption of what the user commits: lines they intended to exclude can be included, or lines they intended to include can be silently dropped, without any warning, error, or diff re-confirmation. This matches the "silent corruption of what the user commits or pushes" impact category — no malicious/local access is required, only ordinary background file activity (which is common with linters/formatters/build watchers) occurring between selecting lines and clicking "Commit".

### Likelihood Explanation
Medium: it requires the working-tree file to change between the moment its diff/selection is computed and the moment the commit button triggers `applyPatchToIndex`. This window is not trivial to hit deliberately by a remote attacker (repository content alone cannot force local file mutation timing), but it is realistic in normal Desktop usage with format-on-save tooling, autosave, or slow user interaction, and the guard rails that would normally catch a mismatch (`git apply` failing on context mismatch) are explicitly disabled via `--unidiff-zero --whitespace=nowarn`.

### Recommendation
Capture and thread through the exact `IDiff` object (or a content hash/mtime of the file at diff-load time) that was used to build the `DiffSelection`, and pass it into `stageFiles`/`applyPatchToIndex` instead of re-fetching a new diff at staging time. If the file has changed since the diff was loaded, abort staging that file and force the UI to refresh the diff/selection before allowing the commit, rather than silently applying stale line offsets to new content.

### Proof of Concept
1. Modify a tracked file with several lines, open Desktop, view the diff, and select only specific lines/hunks for the commit (leaving other hunks unselected).
2. Before clicking "Commit", have an external process (editor autosave, formatter, or a script) modify the same file in a way that shifts line numbers/hunk boundaries but keeps the file non-conflicting for `git apply --unidiff-zero`.
3. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches the new diff and `formatPatch` reapplies the old `DiffSelection` indices to it (`app/src/lib/patch-formatter.ts:143-171`), producing a patch applied via `git apply --unidiff-zero --whitespace=nowarn` (`app/src/lib/git/apply.ts:52-61`).
4. Inspect the resulting commit: it contains different lines than what was visually selected in step 1, with no error or warning shown to the user.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3449)
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

**File:** app/src/lib/stores/app-store.ts (L3685-3689)
```typescript
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
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

**File:** app/src/lib/git/apply.ts (L80-81)
```typescript
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
