## Title
Partial-commit line selection is applied by absolute index to a diff re-fetched at commit time, allowing silent inclusion of unintended (attacker-influenced) content in a commit - (File: `app/src/lib/patch-formatter.ts`, `app/src/lib/git/apply.ts`)

### Summary
`DiffSelection` records which lines a user wants staged as a pure set of numeric line indices (`absoluteIndex` / `unifiedDiffStart + lineIndex`), with no reference to the actual line content it was computed against. When a partial commit is created, `applyPatchToIndex` re-runs `git diff` (`getWorkingDirectoryDiff`) fresh at commit time and then blindly applies the old index-based selection to whatever lines now occupy those positions via `formatPatch`. If the working-tree file changes between the moment the user made their line selection (against diff snapshot A) and the moment the commit patch is generated (against diff snapshot B), the numeric indices no longer point at the same logical lines, so the wrong lines get included or excluded from the commit - without any user-visible warning. This mirrors the H02 pattern: a value ("which content is selected") is computed once against a snapshot and then reused as if the snapshot still matched current reality, and the code contains its own explicit acknowledgment of this drift (`app-store.ts:3480-3485`) but only partially mitigates it (asynchronously, and not synchronously at commit time).

### Finding Description
- `DiffSelection.isSelected(lineIndex)` (`app/src/models/diff/diff-selection.ts:122`) is a pure index-based lookup with no knowledge of line content.
- `formatPatch` (`app/src/lib/patch-formatter.ts:129-221`) walks `diff.hunks` and, for each line, calls `file.selection.isSelected(absoluteIndex)` (`patch-formatter.ts:157`) to decide whether to include it in the generated patch.
- `applyPatchToIndex` (`app/src/lib/git/apply.ts:60,80`) fetches a **brand-new** diff via `getWorkingDirectoryDiff(repository, file)` at the moment of staging/committing, and then calls `formatPatch(file, diff)` using the file's `selection` object that was set earlier (from the UI, potentially against an older diff).
- Desktop does have an explicit acknowledgment of this class of bug: in `app-store.ts:3478-3492` (`updateChangesWorkingDirectoryDiff`), the comment states: *"The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."* — i.e. the fix is a known partial mitigation, not a full one, and it only runs on the periodic/async "refresh working directory diff" path, not synchronously right before `_commitIncludedChanges` (`app-store.ts:3681-3760`) builds the patch.
- `_commitIncludedChanges` uses whatever `file.selection` is currently cached in `state.changesState.workingDirectory.files` at click time (`app-store.ts:3685-3698`) and passes it straight into `createCommit` → `applyPatchToIndex`, without forcing a fresh diff+selection reconciliation first.
- Because `withSelectableLines` (`diff-selection.ts:320-330`) is only invoked from the async `updateChangesWorkingDirectoryDiff` refresh, there is a window (bounded by however long that refresh takes / how often it is triggered) where the index-based selection can be stale relative to the file's real current content, yet is still used verbatim to build the commit patch from a *newly fetched* diff.

### Impact Explanation
If content shifts between the last diff/selection reconciliation and the commit action (e.g., lines inserted/removed above the user's selected region due to a background process modifying the tracked file - for instance a build/install script bundled in a cloned/fetched malicious repository that rewrites a tracked file, or a smudge/normalization pass triggered by attacker-controlled `.gitattributes`), `formatPatch` will apply the stale bit-selection to the new line positions. This can cause:
- Unintended lines being silently staged and committed (e.g., attacker-crafted lines that shifted into a previously-selected index range), or
- Intended lines being silently dropped from the commit.

This is a silent corruption of what the user believes they are committing/pushing, matching the impact category of "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Likelihood is limited by the narrow timing window between the last diff/selection refresh and the commit click, and by the fact that Desktop does perform periodic status/diff refreshes that usually keep `selectableLines` in sync. However, the mechanism has no synchronous guard immediately before generating the commit patch, and the code's own comment documents that the reconciliation is intentionally incomplete ("for now we'll settle on..."). Any external modification to a tracked file that occurs in that window (autosave, formatter-on-save, install script from a fetched/cloned repository, git attribute-driven renormalization) can trigger the mismatch without requiring elevated privileges, local malware, or unusual user steps beyond making a partial commit selection and having the file change shortly after.

### Recommendation
Before generating the commit patch (in `applyPatchToIndex`/`formatPatch`), revalidate that the file's `DiffSelection` was computed against the exact diff being applied - e.g., by comparing a content hash/hunk fingerprint of the diff used to build the selection against the diff fetched at commit time, and refusing/re-prompting instead of silently applying mismatched indices. Alternatively, synchronously re-run the `updateChangesWorkingDirectoryDiff`/`withSelectableLines` reconciliation immediately before `_commitIncludedChanges` builds the patch, and reject the commit (or fall back to selecting all/none with a warning) if the diff has structurally changed since the last reconciliation.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file so it has multiple hunks.
2. In the Changes view, partially select certain lines (e.g., lines 10-15) for a commit; this fixes a `DiffSelection` bit-set keyed to absolute indices in diff snapshot A.
3. Before clicking "Commit", have an external process (e.g., a script executed as part of the repository's own tooling, or an editor auto-format) insert/remove lines earlier in the same file, shifting line positions, producing diff snapshot B.
4. Click "Commit". `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches diff snapshot B fresh, but `formatPatch` (`app/src/lib/patch-formatter.ts:157`) still evaluates `file.selection.isSelected(absoluteIndex)` using the indices selected against snapshot A.
5. Inspect the resulting commit: it will include/exclude lines that do not correspond to what was visually selected by the user in the last diff they saw, confirming the silent corruption.

Note: I was not able to fully trace every code path that triggers `updateChangesWorkingDirectoryDiff` (e.g., exact polling interval/file-watcher debounce) within the available tool budget, so the precise size of the race window is not fully quantified from local code alone; a Devin session with full repo/runtime access would be needed to measure the exact timing window and confirm end-to-end exploitability with a live file-watcher trace. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** app/src/models/diff/diff-selection.ts (L122-136)
```typescript
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
