## Title
Line-selection indices applied against a freshly re-fetched diff can silently stage unintended content - (File: `app/src/lib/git/apply.ts`)

### Summary
The Optimism report's broken invariant is that an index-derived value (`disputedPos.traceIndex`) is combined with a *starting* context to compute a downstream value (`DISPUTED_L2_BLOCK_NUMBER`), but that computed value is fed into a deterministic engine (`op-program`) without validating it's still consistent with the *current* game/claim context — so the same VM inputs can be legitimately valid in one context and invalid in another. The Desktop analog is the partial-commit ("stage some lines") pipeline: line selections are stored purely as **numeric absolute indices** into a diff, and those indices are later replayed against a **diff fetched independently and later in time**, with no correlation to line content.

### Finding Description
When a user stages individual lines/hunks, GitHub Desktop tracks the choice in `DiffSelection`, which is index-based only: [1](#0-0) 

`isSelected(lineIndex)` never looks at line content or a content hash — it just tests whether a numeric index is in the `divergingLines` set.

`formatPatch` builds the partial patch by walking the diff's hunks and testing `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex = hunk.unifiedDiffStart + lineIndex`: [2](#0-1) 

Crucially, `applyPatchToIndex` — the function invoked at actual staging/commit time — does **not** reuse the diff the user looked at when selecting lines. It fetches a brand-new diff right before formatting the patch: [3](#0-2) 

The UI layer (`updateChangesWorkingDirectoryDiff`) is aware that a diff can go stale relative to the stored selection and explicitly re-derives `selectableLines` to drop indices that no longer make sense — but this reconciliation only happens on the polled/foreground diff refresh path, not synchronously as part of `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex`: [4](#0-3) [5](#0-4) [6](#0-5) 

So exactly like the report's core contradiction — "all VM inputs above are identical … but the claimed block number is not passed to the VM, so the VM cannot differentiate context between the two games" — Desktop's `formatPatch` cannot differentiate whether `absoluteIndex` still refers to the same logical line it did when the user clicked it, because the diff used to interpret that index at commit time (`apply.ts:60`) can differ in hunk layout/line counts from the diff shown to the user when the selection was made.

### Impact Explanation
If the working tree file changes between the moment the user selects specific lines to include in a commit and the moment `applyPatchToIndex` regenerates the diff (e.g. a build tool, formatter-on-save, linter, file watcher, or any other process writing to a tracked file — plausible in a malicious repo that ships an npm `postinstall`/watch script, editor task, or git hook that rewrites a file shortly after checkout), the hunk boundaries and `unifiedDiffStart` offsets can shift. The stored `divergingLines` indices from the old diff are then blindly reapplied to the new diff's line positions in `formatPatch`, so:
- Lines the user did **not** select can be silently staged/committed.
- Lines the user explicitly selected can be silently dropped from the commit.

This is a silent corruption of what the user commits/pushes — the exact impact class called out as valid ("silent corruption of what the user commits or pushes"), since there is no error, no diff re-verification, and no confirmation before `git apply --cached` runs against a state selections were never validated against.

### Likelihood Explanation
This requires the tracked file content to actually change between diff-render time and stage time, and this repository-controlled process is not something Desktop runs automatically by itself — a bystander process (editor autosave, IDE extension, watch/build task, external tool) writing to the working copy is the realistic trigger, which keeps the likelihood moderate rather than trivially remote. However, no code path re-validates that the previously computed `absoluteIndex` values still correspond to the same lines against the diff actually used to build the patch, so the flaw is structural rather than probabilistic once such a race occurs — it is the same "index is trusted across two different underlying contexts" pattern as the source report, just manifesting as silent commit corruption instead of dispute-game resolution corruption.

### Recommendation
`applyPatchToIndex` should reuse (or re-validate against) the exact diff the selection was computed from — e.g., accept the diff as a parameter from the caller instead of independently refetching it, or, if a fresh diff must be fetched, compare hunk headers/line hashes against the diff used to build `file.selection` and abort/re-prompt the user if they diverge, mirroring the pattern already used in `updateChangesWorkingDirectoryDiff` to recompute `selectableLines`. In general, `DiffSelection.isSelected` should not be trusted purely by numeric index across two independently generated diffs.

### Proof of Concept
1. Open a repo in Desktop and modify `file.txt`, adding lines A, B, C in a single hunk.
2. In the Changes view, select only line B for the commit (deselect A and C). Desktop stores index positions such as `divergingLines = {2}` relative to the diff currently rendered.
3. Before pressing "Commit", have an external process (editor autosave, formatter-on-save, watch task) rewrite `file.txt` such that the hunk this line lived in shifts (e.g., inserts/removes an earlier line elsewhere in the file, or the diff is regenerated with different `unifiedDiffStart` offsets) without the Changes pane's diff view having refreshed/reconciled selection yet.
4. Press "Commit". `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` calls `getWorkingDirectoryDiff` again (`app/src/lib/git/apply.ts:60`) producing a diff with shifted hunks, then `formatPatch` reapplies the old numeric index `2` against the new hunk layout (`app/src/lib/patch-formatter.ts:144-157`), staging/committing a different line than the one the user selected — with no warning to the user.

### Citations

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
