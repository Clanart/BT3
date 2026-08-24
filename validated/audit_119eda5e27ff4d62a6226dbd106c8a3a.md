This confirms `DiffSelection` is a purely index-based bitset — `isSelected(lineIndex)` and `withRangeSelection` only track integer line positions, with no binding to line content or hash [1](#0-0) . The class does not know "what all lines mean" and only prunes indices that are no longer *selectable* (`withSelectableLines`), not indices whose *content* has shifted while remaining structurally valid [2](#0-1) .

### Title
Position-indexed partial-commit line selection is not revalidated against content changes, allowing silent inclusion of unreviewed content in a commit - (File: app/src/lib/stores/app-store.ts)

### Summary
`DiffSelection` records which lines a user has chosen to stage using integer line indices into a specific diff snapshot, never a content hash. When the working directory diff is refreshed, GitHub Desktop only prunes indices that are no longer "includable" (`withSelectableLines`), but keeps any index that still falls within a valid line range even if the underlying content at that index has changed. Since `applyPatchToIndex` re-fetches a *fresh* diff at commit time and then applies the (potentially stale) selection object to that fresh diff by index alone, a file whose content shifts between the last diff review and the moment of commit can cause the wrong lines to be staged and committed — without the user seeing what was actually included. This mirrors the BathBuddy defect: a derived data structure (`userRewardsPerTokenPaid` / `DiffSelection`) is keyed to a snapshot of mutable state (`totalSupply`/`balanceOf` / diff hunks) and is not invalidated when that underlying state changes through a path the accounting logic doesn't observe.

### Finding Description
The commit flow works as follows:
1. The Changes view loads a diff and, on every reload, updates the file's `DiffSelection` with `withSelectableLines`, which is explicitly documented as incomplete: "The diff might have changed dramatically since last we loaded it... for now we'll settle on just updating the selectable lines such that any previously selected line which now no longer exists or has been turned into a context line isn't still selected" [3](#0-2) .
2. When the user commits, `_commitIncludedChanges` takes whatever `DiffSelection` is currently attached to each `WorkingDirectoryFileChange` in `state.changesState.workingDirectory.files` and passes it straight to `createCommit` [4](#0-3) .
3. `stageFiles`/`applyPatchToIndex` fetches a brand-new diff via `getWorkingDirectoryDiff` immediately before formatting the patch [5](#0-4) .
4. `formatPatch` walks the *new* diff's hunks/lines and asks the *old* selection object whether each `absoluteIndex` is selected — purely by position, with no check that the content at that index matches what the user actually reviewed [6](#0-5) .
5. `DiffSelection.isSelected` is a pure index/bitset lookup with no notion of the underlying line content [1](#0-0) .

If the file content is mutated between the last diff render and the commit click (e.g., by a git hook belonging to a cloned/fetched attacker repository that runs during an intermediate git operation Desktop performs in the background — `loadStatus`, `loadRemotes`, `refreshTags`, `loadStashEntries`, etc. in `_refreshRepository` [7](#0-6)  — or by any other process writing to the tracked file), the hunk boundaries and line contents at the same absolute indices can differ from what the user saw and selected. Because indices, not content, drive `isSelected`, lines the user never reviewed can be silently included (or lines they intended to include can be silently dropped), while the UI still shows the old, stale checkbox state from before the reload.

### Impact Explanation
This breaks the core invariant of partial-commit staging: "what the user visually selected is what gets committed." An attacker who controls content that lands in the user's working tree (a hook or generated file from a cloned/fetched repository) can cause unreviewed attacker-controlled content to be silently folded into a commit the user believes only contains their own reviewed hunk — and that commit can subsequently be pushed. This is exactly the "silent corruption of what the user commits or pushes" impact called out as valid.

### Likelihood Explanation
The window is real but requires precise timing: the file must change between a diff load and the commit action, and the change must be structured to align with previously selected indices in a way that produces attacker-favorable content, which needs some knowledge of the file's structure. This makes exploitation non-trivial to weaponize reliably, but the underlying safety gap — position-based reuse of a stale selection against a freshly re-diffed file — is unambiguously present in the code and is explicitly acknowledged as an unresolved limitation in the source comment cited above.

### Recommendation
Bind `DiffSelection` (or an accompanying integrity token) to the exact diff/content snapshot it was computed from — e.g., a hash of the hunk's line content or the diff's overall hash — and reject or force a full re-review flow if the file content at commit time doesn't match the diff that produced the current selection, rather than only pruning by index existence.

### Proof of Concept
Conceptual reproduction (cannot be executed here, but derivable directly from the cited code paths):
1. Open a repository with a modified file and select only specific lines via the Changes view (partial `DiffSelection`).
2. Before clicking "Commit", have another process (e.g., a git hook triggered by a background fetch/status operation Desktop performs automatically) rewrite the file so that new content occupies the same absolute line indices previously marked "selected," while genuinely new/different lines occupy indices previously marked "unselected."
3. Click "Commit". `applyPatchToIndex` re-diffs the file fresh [8](#0-7)  and `formatPatch` applies the old, stale `DiffSelection` indices to this new diff [9](#0-8) , producing a patch whose content the user never reviewed on screen.
4. Inspect the resulting commit: it contains different content than what was visually checked in the Changes view immediately before committing.

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

**File:** app/src/models/diff/diff-selection.ts (L309-330)
```typescript
  /**
   * Returns a copy of this selection instance with a specified set of
   * selectable lines. By default a DiffSelection instance allows selecting
   * all lines (in fact, it has no notion of how many lines exists or what
   * it is that is being selected).
   *
   * If the selection instance lacks a set of selectable lines it can not
   * supply an accurate value from getSelectionType when the selection of
   * all lines have diverged from the default state (since it doesn't know
   * what all lines mean).
   */
  public withSelectableLines(selectableLines: Set<number>) {
    const divergingLines = this.divergingLines
      ? new Set([...this.divergingLines].filter(x => selectableLines.has(x)))
      : null

    return new DiffSelection(
      this.defaultSelectionType,
      divergingLines,
      selectableLines
    )
  }
```

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

**File:** app/src/lib/stores/app-store.ts (L4093-4127)
```typescript
    const status = await this._loadStatus(repository)
    this.updateSidebarIndicator(repository, status)

    if (status === null) {
      await this._updateRepositoryMissing(repository, true)
      return
    }

    // loadBranches needs the default remote to determine the default branch
    await gitStore.loadRemotes()
    await gitStore.loadBranches()

    const section = state.selectedSection
    let refreshSectionPromise: Promise<void>

    if (section === RepositorySectionTab.History) {
      refreshSectionPromise = this.refreshHistorySection(repository)
    } else if (section === RepositorySectionTab.Changes) {
      refreshSectionPromise = this.refreshChangesSection(repository, {
        includingStatus: false,
        clearPartialState: false,
      })
    } else {
      return assertNever(section, `Unknown section: ${section}`)
    }

    await Promise.all([
      gitStore.updateLastFetched(),
      gitStore.loadStashEntries(),
      this._refreshAuthor(repository),
      this._refreshWorktrees(repository),
      refreshSectionPromise,
    ])

    await gitStore.refreshTags()
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
