This confirms the vulnerable path. The line-selection state (`DiffSelection`) is stored as raw line indices, decoupled from actual diff content, and is only refreshed opportunistically by `updateChangesWorkingDirectoryDiff()` when the file remains selected in the UI — the code comment itself admits the mitigation is incomplete: [1](#0-0) 

At commit time, `applyPatchToIndex()` re-fetches a **fresh** diff (`getWorkingDirectoryDiff`) and blindly reapplies the **stale, index-based** `DiffSelection` from `WorkingDirectoryFileChange` without validating it still corresponds to the same line content: [2](#0-1) 

`formatPatch()` then walks the new diff's hunks and calls `file.selection.isSelected(absoluteIndex)` purely by numeric index — with no content/hash check tying the selection to the diff it was derived from: [3](#0-2) 

### Title
Stale line-index-based diff selection can cause silent commit of unintended file content - (File: app/src/lib/git/apply.ts, app/src/lib/patch-formatter.ts)

### Summary
Desktop's partial-commit ("stage individual lines") feature stores the user's line selection as an index-based `DiffSelection` bitmap, decoupled from the diff content it was derived from. If the working-directory file changes between the moment the user makes a line selection in the UI and the moment the commit is executed, `stageFiles()`/`applyPatchToIndex()` will regenerate a **new** diff against the now-changed file but apply the **old** index-based selection to it via `formatPatch()`. This is the same "signed-at-T0, used-at-T1" broken invariant as the Native report: state trusted at selection time is used unchecked at execution time.

### Finding Description
The commit pipeline works as follows:
1. When the user selects lines to include in a partial commit, the app stores a `DiffSelection` (a set of numeric line indices) against the `WorkingDirectoryFileChange`, computed relative to a specific `ITextDiff` shown in the UI at that time [4](#0-3) .
2. `updateChangesWorkingDirectoryDiff()` only rebases the selection against `selectableLines` when the same file remains the *sole selected file* and the diff is explicitly reloaded — the function's own comment concedes it doesn't validate that previously selected lines still exist in the new diff, only that they're marked non-selectable if the line count shrinks: "Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines" [5](#0-4) .
3. Multiple external actors can mutate a working-directory file between the time it's diffed for the UI and the time the commit executes, e.g. an editor auto-save, a build/format-on-save tool, a file watcher triggered by a symlink/submodule the attacker controls, or (in the CLI/deep-link flow) another `--cli-clone`/file-drop event that repopulates the working tree while a commit is queued.
4. At commit time, `_commitIncludedChanges()` passes the (possibly stale) `WorkingDirectoryFileChange.selection` straight into `createCommit` → `stageFiles` → `applyPatchToIndex` [6](#0-5) .
5. `applyPatchToIndex` fetches a **brand-new** diff from disk via `getWorkingDirectoryDiff` (reflecting the file's current, possibly attacker/tool-modified content) and formats a patch using the stale selection indices [2](#0-1) .
6. `formatPatch` walks the new diff's hunks and includes/excludes lines purely based on `file.selection.isSelected(absoluteIndex)` — an integer index lookup with no correlation to the actual text that occupied that index when the selection was made [3](#0-2) .

Because hunk/line indices shift whenever content is added/removed above them, a selection of "lines 10-15" made against diff-v1 can silently map to entirely different lines in diff-v2 (e.g., previously-unselected sensitive lines, or lines that didn't exist at selection time). The result: git stages and commits content the user never reviewed or intended to include, and Desktop shows a commit success message with no indication the committed diff differs from what was displayed.

### Impact Explanation
This maps to "silent corruption of what the user commits or pushes" in the Valid Impact list. A user reviewing a partial diff, deselecting sensitive lines (e.g., a secret, a debug flag, or code they don't want to ship), could have those lines silently included in the commit if the file is touched between selection and commit — with no error, warning, or diff re-confirmation shown by Desktop. This is a genuine trust invariant break: what the user saw and approved (diff-v1 + selection) does not match what was actually committed (diff-v2 + reused selection).

### Likelihood Explanation
Requires a window between diff display/selection and commit execution during which the file's content changes underneath the app — plausible via format-on-save extensions, build tools, linters with auto-fix, git hooks, or other processes writing to tracked files while Desktop is open, none of which require local/physical/admin access beyond normal developer workflows already anticipated by Desktop's own async refresh logic. The existing partial mitigation in `updateChangesWorkingDirectoryDiff` (recalculating `selectableLines`) demonstrates the Desktop team is aware diffs can change mid-session, but it only prunes indices that fall outside the new hunk bounds — it does not verify the selected lines still correspond to the same text, so many stale-selection scenarios pass through undetected.

### Recommendation
Bind `DiffSelection` to a content fingerprint of the diff it was computed against (e.g., a hash of the diff hunks or of the underlying file blob) rather than raw indices. At commit time in `applyPatchToIndex`, before formatting the patch, compare the fingerprint of the freshly-fetched diff to the one recorded when the selection was made; if they differ, abort the partial commit and force the UI to re-display the new diff for re-confirmation, following the report's own recommendation to validate current state before executing a signed/pre-computed action, and to fail closed rather than silently proceeding on stale data.

### Proof of Concept
1. Modify a tracked file with two independent hunks (A and B).
2. In Desktop's Changes view, deselect hunk B's lines (leave only hunk A selected) — this records `DiffSelection` indices relative to the currently-displayed diff.
3. Before clicking "Commit", externally modify the file so that new lines are inserted above hunk B (e.g., another tool appends content earlier in the file), shifting hunk B's line indices without Desktop reloading the diff (the Changes list may not immediately re-diff if focus/selection state doesn't trigger `updateChangesWorkingDirectoryDiff`, or a race exists between the file-watcher-triggered refresh and the user's click).
4. Click "Commit". Observe via `git show` that the resulting commit includes different line ranges than what hunk-B-deselected implied — content the user explicitly excluded from the diff view is included in the commit, or vice versa.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
```

**File:** app/src/lib/stores/app-store.ts (L3693-3711)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
```

**File:** app/src/lib/git/apply.ts (L52-68)
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
