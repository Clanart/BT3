### Title
Stale line-selection indices applied against a freshly-fetched diff during commit staging can silently commit unintended content - ([File: app/src/lib/git/apply.ts])

### Summary
This mirrors the report's core defect: a value computed from a previously-loaded state (share price / diff) is reused in a later, critical operation (deposit-withdraw / commit) without validating that the underlying state hasn't shifted. In Desktop, the equivalent "price" is the set of absolute line indices a user has marked as *selected* for partial commit. That selection is computed against a diff snapshot loaded at an earlier point in time, but it is later applied, unmodified, against a **freshly re-fetched diff** at staging time. If the file's content shifts between those two points (extra/removed lines elsewhere in the file changing hunk offsets), the same absolute index no longer maps to the same logical line, so the wrong lines get included/excluded from the commit — a silent corruption of what the user commits.

### Finding Description
`_commitIncludedChanges` reads the working-directory file list and each file's `selection` (line-index state) from the cached `repositoryStateCache`, computed at some earlier point: [1](#0-0) 

That `selection` is threaded straight into `createCommit` → `stageFiles` → `applyPatchToIndex`: [2](#0-1) [3](#0-2) 

Critically, `applyPatchToIndex` does **not** reuse a cached diff — it calls `getWorkingDirectoryDiff` again, immediately before staging, to build the patch: [4](#0-3) 

`formatPatch` then decides, line by line, whether to include a hunk line by checking `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is `hunk.unifiedDiffStart + lineIndex` **from the newly fetched diff**: [5](#0-4) 

So the "price" (selection state, tied to old hunk boundaries) is stale relative to the "value" it's applied to (the new diff's hunk boundaries/content). The app is aware this class of drift exists — there's explicit reconciliation logic when the diff viewer detects the diff changed, but it only prunes selected indices that no longer exist as includeable lines; it does not verify that a still-valid index still refers to the *same line content*: [6](#0-5) 

This reconciliation also only runs while the diff viewer is actively subscribed/refreshing for the selected file — it is not guaranteed to run for every file included in a multi-file commit between the moment the user finished making selections and the moment `_commitIncludedChanges` fires and re-diffs at staging time.

### Impact Explanation
If a tracked file's content changes between when the user visually selects lines to include and when Desktop stages the patch (e.g. a smudge/clean filter, a background build/format-on-save tool, a pre-commit hook that rewrites the file before the "prepare" stage, or another process/worktree touching the same file), the offset-based selection can silently apply to different lines than the user reviewed and approved. This can result in committing content the user never intended to include (or omitting content they meant to include), which is exactly the "silent corruption of what the user commits or pushes" impact category — without any error or warning to the user.

### Likelihood Explanation
Medium-to-low: it requires a timing window where file content mutates after diff/selection was established in the UI but before `_commitIncludedChanges` re-diffs at staging. This is plausible in real workflows (auto-formatters, editor autosave, filters, hooks) especially for large diffs/slow commits, but it is not trivially attacker-triggerable without some source of concurrent file modification (e.g. a malicious `clean`/`smudge` filter or hook shipped in a cloned/fetched repository, which is in-scope per the "attacker controls a cloned/fetched repository" criterion).

### Recommendation
Before applying `file.selection` to a freshly-fetched diff in `applyPatchToIndex`, re-validate that the diff content used to originally build the selection is unchanged (e.g. compare diff/hunk fingerprints, not just line-count/selectable-index membership), and abort or force a fresh selection prompt if it has drifted, rather than silently applying offset-based selection to new hunk boundaries.

### Proof of Concept
1. User modifies a file and selects specific lines/hunks for partial commit in Desktop's Changes view (selection stored as absolute indices tied to the diff loaded at that time).
2. Before the user clicks "Commit" (or during the async commit flow), an external process (e.g. a `clean` filter configured in `.gitattributes` of the cloned repo, or a pre-commit hook that rewrites file content) inserts/removes lines elsewhere in the same file, shifting hunk offsets.
3. `_commitIncludedChanges` → `applyPatchToIndex` re-fetches the diff (`getWorkingDirectoryDiff`) which now has different hunk boundaries, but `formatPatch` still applies the old `file.selection.isSelected(absoluteIndex)` mask from step 1.
4. The resulting patch includes/excludes different logical lines than what the user selected, and this commit is created and can subsequently be pushed, with no warning shown.

### Citations

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
