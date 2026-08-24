### Title
Partial-commit line selection is re-applied to a freshly re-diffed working tree, allowing silent corruption of staged/committed content - (`app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, `app/src/lib/git/update-index.ts`)

### Summary
This is the closest local-code analog to the reported bug class: a computation (management-fee assessment) that used the wrong base of data because a "should-be-excluded/stale" component was not accounted for before the calculation ran. In Desktop the analogous broken invariant is that `applyPatchToIndex` re-fetches a *brand-new* diff of the working tree at staging time and then reapplies the user's previously computed, purely *positional* line selection (`DiffSelection`, keyed by absolute line index) to that new diff, with no check that the new diff's hunks/line layout still matches the diff the selection was computed against.

### Finding Description
When the user commits, `_commitIncludedChanges` in `app-store.ts` takes the currently cached `WorkingDirectoryFileChange` objects (each carrying a `DiffSelection` computed against whatever diff was rendered in the Changes view) and passes them straight to `createCommit` → `stageFiles` → `applyPatchToIndex`. [1](#0-0) 

`applyPatchToIndex` does not reuse that previously-rendered diff. It calls `getWorkingDirectoryDiff(repository, file)` again, fetching a fresh `git diff` of the current on-disk state, and then feeds that fresh diff plus the old `file.selection` into `formatPatch`: [2](#0-1) 

`formatPatch`/`formatPatchToDiscardChanges` decide whether a line is included purely by an `absoluteIndex = hunk.unifiedDiffStart + lineIndex` computed from the *new* diff's hunk layout, then ask `file.selection.isSelected(absoluteIndex)`: [3](#0-2) 

`DiffSelection` itself is a purely index-based bitset with no notion of line content/identity — `divergingLines`/`selectableLines` are just integer sets keyed to positions in whatever diff was in effect when the selection was built: [4](#0-3) 

There *is* a reconciliation step, but it only runs when the Changes view actively reloads a diff for display (`updateChangesWorkingDirectoryDiff`), where `selectableLines` is recomputed from the newly loaded diff and merged into the selection: [5](#0-4) 

That reconciliation is not re-run inside `applyPatchToIndex`/`stageFiles`/`createCommit` before the fresh diff is combined with the (possibly stale) selection. If the working-tree file changes between the last time the UI diff was loaded/validated and the moment the user clicks "Commit" — e.g. a build tool, formatter, linter, git hook, or any other process bundled with/triggered by a cloned or fetched repository rewrites the file, shifting hunk boundaries or line counts — `applyPatchToIndex` will apply the old numeric line selection to the new hunk layout. Because `formatPatch` blindly trusts `absoluteIndex`, this can select semantically different lines than what the user actually reviewed and checked in the UI, generating a patch that is silently different from user intent and applying it to the index via `git apply --cached`.

### Impact Explanation
This directly matches the "silent corruption of what the user commits or pushes" category. An attacker who controls a cloned/fetched repository can ship pre-commit/post-checkout/post-merge hooks, watch scripts, or build tooling (invoked automatically, e.g. via `core.hooksPath`, editor/IDE integration, or a file watcher started by opening the repo) that rewrites tracked files at the moment the user is about to commit. Because Desktop recomputes the diff at staging time without re-validating the user's line-based selection against it, the user could end up committing/pushing content they never selected or reviewed — including attacker-planted lines merged into a commit that appears, in the UI, to reflect only the lines the user checked off. This is a real corruption-of-commit-content primitive, not merely a display glitch.

### Likelihood Explanation
The race window is real but narrow: it requires the working-tree file to change between the diff being loaded for display and the "Commit" click actually reaching `applyPatchToIndex`. This is plausible in a repository that ships automated tooling (format-on-save daemons, hooks, file watchers) that the user has already trusted enough to clone/open, and Desktop's own hook interception list (`pre-commit`, `prepare-commit-msg`, etc.) confirms hooks are expected to run and can mutate state around commit time: [6](#0-5) 
Likelihood is moderate — it needs a specific timing condition or an intentionally malicious repo-bundled watcher/hook, not attacker control of arbitrary remote content alone. I could not fully verify from the index alone whether any additional guard (e.g. mtime/hash check before staging) exists elsewhere in the staging pipeline; none was found in `update-index.ts`, `apply.ts`, or `commit.ts`.

### Recommendation
Before calling `formatPatch` in `applyPatchToIndex`, either (a) reuse/require the exact diff the selection was validated against and fail/re-prompt if the on-disk file has changed (e.g. compare mtime/hash or diff hash) instead of silently re-diffing, or (b) re-run the same `selectableLines` reconciliation used in `updateChangesWorkingDirectoryDiff` against the freshly fetched diff immediately before formatting the patch, and abort/warn the user if the reconciled selection no longer maps cleanly onto the new hunk layout.

### Proof of Concept
Not independently executable from the code index alone (would require a live repository, a hook/watcher that rewrites a tracked file between diff-load and commit, and timing control), so this is a design/logic analysis based on the cited source rather than a verified runtime PoC:
1. Open a repo in Desktop with `modified-file.md` having two hunks; select only hunk 2's lines for commit (selection stored as absolute line indices against the currently loaded diff).
2. Before clicking "Commit", have an external process (simulating a malicious repo-bundled watcher/hook) insert or remove lines in hunk 1 of `modified-file.md`, shifting all subsequent line offsets.
3. Click "Commit". `applyPatchToIndex` re-diffs the file (now with shifted hunk offsets) and applies the old absolute-index selection via `formatPatch`, producing a patch that includes/excludes different lines than the user intended, which is what gets staged and committed.

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

**File:** app/src/models/diff/diff-selection.ts (L74-119)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}

  /** Returns a value indicating the computed overall state of the selection */
  public getSelectionType(): DiffSelectionType {
    const divergingLines = this.divergingLines
    const selectableLines = this.selectableLines

    // No diverging lines, happy path. Either all lines are selected or none are.
    if (!divergingLines) {
      return this.defaultSelectionType
    }
    if (divergingLines.size === 0) {
      return this.defaultSelectionType
    }

    // If we know which lines are selectable we need to check that
    // all lines are divergent and return the inverse of default selection.
    // To avoid looping through the set that often our happy path is
    // if there's a size mismatch.
    if (selectableLines && selectableLines.size === divergingLines.size) {
      const allSelectableLinesAreDivergent = [...selectableLines].every(i =>
        divergingLines.has(i)
      )

      if (allSelectableLinesAreDivergent) {
        return this.defaultSelectionType === DiffSelectionType.All
          ? DiffSelectionType.None
          : DiffSelectionType.All
      }
    }

    // Note that without any selectable lines we'll report partial selection
    // as long as we have any diverging lines since we have no way of knowing
    // if _all_ lines are divergent or not
    return DiffSelectionType.Partial
  }
```

**File:** app/src/lib/git/commit.ts (L56-65)
```typescript
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
```
