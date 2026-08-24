## Analysis

I found a genuine analog: a divergence between the "reference" state that produces a value (the diff/selection shown and edited by the user) and the "reference" state actually used to enforce/apply it (a freshly re-fetched diff at commit time), exactly mirroring the TRST-M-3 pattern where a getter and a verifier compute proportions from different reference points.

### Title
Stale line-selection indices are silently re-applied against a freshly-fetched, differently-shaped diff during partial commit, causing wrong lines to be staged - (File: app/src/lib/git/apply.ts)

### Summary
`WorkingDirectoryFileChange.selection` stores *only* a set of abstract line indices (diverging from a default of All/None) computed against the hunk layout of whatever `ITextDiff` the UI happened to render when the user made selections [1](#0-0) . When the commit is actually executed, `applyPatchToIndex` does **not** reuse that diff — it calls `getWorkingDirectoryDiff(repository, file)` again, fetching a brand-new diff from git at commit time, and immediately feeds the *old* selection object into `formatPatch(file, diff)` against this new diff's hunks [2](#0-1) . `formatPatch` blindly maps `hunk.unifiedDiffStart + lineIndex` from the new hunks into `file.selection.isSelected(absoluteIndex)` [3](#0-2) , with no check that the line at that index is the same logical line the user actually saw and toggled.

### Finding Description
The invariant that should hold is: "the set of lines a user selected in the diff they reviewed is the same set of lines committed." This is broken because two different code paths compute/consume `absoluteIndex` against two different diff snapshots:

- The **UI/selection producer** (`Changes`/`SeamlessDiffSwitcher` → `changeFileLineSelection`) builds `divergingLines` indices against the hunk layout returned by whatever diff was loaded for display.
- The **committer/consumer** (`applyPatchToIndex`) re-diffs at apply-time and reuses the raw index set against the new hunk layout, exactly as `getAmtsNeededForDeposit()` used a different reference token than `ratiosMatch()`'s largest-balance reference.

There is a partial mitigation: `updateChangesWorkingDirectoryDiff` refreshes selectable lines whenever the UI diff is reloaded, dropping selections for indices that are no longer includeable [4](#0-3) . The code comment explicitly acknowledges the fix is incomplete: *"Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."* [5](#0-4) . Crucially, this reconciliation only prunes indices that are no longer selectable (context lines/out of range); it does **not** verify that an index which remains selectable still refers to the *same content*. If the working-directory diff shape changes between the moment the UI last reconciled the selection and the moment `applyPatchToIndex` performs its own independent re-fetch (which happens with zero reconciliation at all, since `apply.ts` never calls `withSelectableLines`), an index like `hunk.unifiedDiffStart + lineIndex` that was an "Add" line the user deliberately deselected can become a different "Add"/"Delete" line in the new diff and be silently included or excluded.

The trigger for the diff to change between review and commit does not require local/physical access or malware: an attacker-controlled repository can define a `.gitattributes` clean/smudge filter, a `core.autocrlf`-driven line-ending rewrite, or a checked-in pre-commit hook that rewrites file contents on `git add`/`git diff` invocations — all of which run automatically as part of normal Desktop workflows against a cloned/fetched repository. Because `applyPatchToIndex` always re-invokes `git diff` right before staging [6](#0-5) , any filter-driven or hook-driven content change that occurs between the diff the user reviewed and the diff used for staging will silently shift hunk boundaries while `file.selection`'s raw index set is reused unchanged.

### Impact Explanation
This falls squarely under "silent corruption of what the user commits" from a repository-controlled input (gitattributes filters/hooks are checked into and shipped with the repository, i.e., attacker-controlled). A user reviewing a diff, deliberately deselecting a malicious/unwanted hunk, could still have that content committed (and potentially pushed) because the index used to decide "is this line included" no longer corresponds to the line the user actually looked at. This is more severe than a UX inconvenience: it defeats the entire purpose of partial-file staging as a security/review control, and could be leveraged to smuggle unreviewed changes (e.g., backdoored config, credentials, build steps) into a commit the user believes they scoped down.

### Likelihood Explanation
Moderate-to-low without further validation of exact timing windows: `.gitattributes` filters and hooks are a well-documented mechanism by which a cloned/fetched repository can trigger content-mutating side effects during ordinary `git diff`/`git status`/`git add` calls, and Desktop's architecture (diff loaded once for review, then independently re-diffed at commit time in `applyPatchToIndex`) provides the necessary window. However, I could not fully confirm from static analysis alone whether Desktop's UI refreshes/reconciles the selection immediately before invoking `createCommit` such that the window is negligible in practice, or whether a realistic filter/hook could reliably and deterministically produce a hunk-shape change synchronized with a user's specific partial selection. This would benefit from dynamic testing in a live Desktop session.

### Recommendation
`applyPatchToIndex` should not blindly reuse `file.selection` against a freshly fetched diff. Either (a) pass through and reuse the exact `ITextDiff` object last reconciled with the user's selection (the same object used to render the Changes view) rather than re-fetching, or (b) if a re-fetch is unavoidable, run the same reconciliation logic used in `updateChangesWorkingDirectoryDiff` (or a stronger content-aware version that also validates line *type* and *text*, not just index existence) immediately before calling `formatPatch`, and abort/re-prompt the user if the diff has structurally changed since last review.

### Proof of Concept
Conceptual PoC (not executed, since I lack an interactive Desktop session):
1. Clone an attacker-controlled repository containing a `.gitattributes` clean filter (or a `pre-commit`/smudge hook) that appends or reorders lines in a tracked file during `git` operations, without altering what's visible in the working tree file itself before the filter runs.
2. In Desktop, open the file's diff, and deliberately deselect a specific hunk/line (e.g., an added suspicious line) via `withLineSelection`, producing a `DiffSelection` with `divergingLines` keyed to the current hunk layout [7](#0-6) .
3. Trigger the commit. `createCommit` → `stageFiles` → `applyPatchToIndex` re-invokes `getWorkingDirectoryDiff` [8](#0-7) ; if the filter/hook shifted hunk boundaries (e.g., added/removed a context line above the target hunk), `formatPatch`'s `absoluteIndex` computation [3](#0-2)  now maps the user's previously-deselected index onto a different line, and the previously-excluded content is included in the staged patch and subsequent commit.
4. Verify via `git show <sha>` that the committed content includes the change the user attempted to exclude. [2](#0-1) [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** app/src/models/diff/diff-selection.ts (L1-331)
```typescript
import { assertNever } from '../../lib/fatal-error'

/**
 * The state of a file's diff selection
 */
export enum DiffSelectionType {
  /** The entire file should be committed */
  All = 'All',
  /** A subset of lines in the file have been selected for committing */
  Partial = 'Partial',
  /** The file should be excluded from committing */
  None = 'None',
}

/**
 * Utility function which determines whether a boolean selection state
 * matches the given DiffSelectionType. A true selection state matches
 * DiffSelectionType.All, a false selection state matches
 * DiffSelectionType.None and if the selection type is partial there's
 * never a match.
 */
function typeMatchesSelection(
  selectionType: DiffSelectionType,
  selected: boolean
): boolean {
  switch (selectionType) {
    case DiffSelectionType.All:
      return selected
    case DiffSelectionType.None:
      return !selected
    case DiffSelectionType.Partial:
      return false
    default:
      return assertNever(
        selectionType,
        `Unknown selection type ${selectionType}`
      )
  }
}

/**
 * An immutable, efficient, storage object for tracking selections of indexable
 * lines. While general purpose by design this is currently used exclusively for
 * tracking selected lines in modified files in the working directory.
 *
 * This class starts out with an initial (or default) selection state, ie
 * either all lines are selected by default or no lines are selected by default.
 *
 * The selection can then be transformed by marking a line or a range of lines
 * as selected or not selected. Internally the class maintains a list of lines
 * whose selection state has diverged from the default selection state.
 */
export class DiffSelection {
  /**
   * Initialize a new selection instance where either all lines are selected by default
   * or not lines are selected by default.
   */
  public static fromInitialSelection(
    initialSelection: DiffSelectionType.All | DiffSelectionType.None
  ): DiffSelection {
    if (
      initialSelection !== DiffSelectionType.All &&
      initialSelection !== DiffSelectionType.None
    ) {
      return assertNever(
        initialSelection,
        'Can only instantiate a DiffSelection with All or None as the initial selection'
      )
    }

    return new DiffSelection(initialSelection, null, null)
  }

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

  /**
   * Returns a value indicating whether the range is all selected, partially
   * selected, or not selected.
   *
   * @param from     The line index (inclusive) from where to checking the range.
   *
   * @param length   The number of lines to check from the start point of
   *                  'from', Assumes positive number, returns None if length is <= 0.
   */
  public isRangeSelected(from: number, length: number): DiffSelectionType {
    if (length <= 0) {
      // This shouldn't happen? But if it does we'll log it and return None.
      return DiffSelectionType.None
    }

    const computedSelectionType = this.getSelectionType()
    if (computedSelectionType !== DiffSelectionType.Partial) {
      // Nothing for us to do here. If all lines are selected or none, then any
      // range of lines will be the same.
      return computedSelectionType
    }

    if (length === 1) {
      return this.isSelected(from)
        ? DiffSelectionType.All
        : DiffSelectionType.None
    }

    const to = from + length
    let foundSelected = false
    let foundDeselected = false
    for (let i = from; i < to; i++) {
      if (this.isSelected(i)) {
        foundSelected = true
      }

      if (!this.isSelected(i)) {
        foundDeselected = true
      }

      if (foundSelected && foundDeselected) {
        return DiffSelectionType.Partial
      }
    }

    return foundSelected ? DiffSelectionType.All : DiffSelectionType.None
  }

  /**
   * Returns a value indicating wether the given line number is selectable.
   * A line not being selectable usually means it's a hunk header or a context
   * line.
   */
  public isSelectable(lineIndex: number): boolean {
    return this.selectableLines ? this.selectableLines.has(lineIndex) : true
  }

  /**
   * Returns a copy of this selection instance with the provided
   * line selection update.
   *
   * @param lineIndex The index (line number) of the line which should
   *                 be selected or unselected.
   *
   * @param selected Whether the given line number should be marked
   *                 as selected or not.
   */
  public withLineSelection(
    lineIndex: number,
    selected: boolean
  ): DiffSelection {
    return this.withRangeSelection(lineIndex, 1, selected)
  }

  /**
   * Returns a copy of this selection instance with the provided
   * line selection update. This is similar to the withLineSelection
   * method except that it allows updating the selection state of
   * a range of lines at once. Use this if you ever need to modify
   * the selection state of more than one line at a time as it's
   * more efficient.
   *
   * @param from     The line index (inclusive) from where to start
   *                 updating the line selection state.
   *
   * @param to       The number of lines for which to update the
   *                 selection state. A value of zero means no lines
   *                 are updated and a value of 1 means only the
   *                 line given by lineIndex will be updated.
   *
   * @param selected Whether the lines should be marked as selected
   *                 or not.
   */
  // Lower inclusive, upper exclusive. Same as substring
  public withRangeSelection(
    from: number,
    length: number,
    selected: boolean
  ): DiffSelection {
    const computedSelectionType = this.getSelectionType()
    const to = from + length

    // Nothing for us to do here. This state is when all lines are already
    // selected and we're being asked to select more or when no lines are
    // selected and we're being asked to unselect something.
    if (typeMatchesSelection(computedSelectionType, selected)) {
      return this
    }

    if (computedSelectionType === DiffSelectionType.Partial) {
      const newDivergingLines = new Set<number>(this.divergingLines!)

      if (typeMatchesSelection(this.defaultSelectionType, selected)) {
        for (let i = from; i < to; i++) {
          newDivergingLines.delete(i)
        }
      } else {
        for (let i = from; i < to; i++) {
          // Ensure it's selectable
          if (this.isSelectable(i)) {
            newDivergingLines.add(i)
          }
        }
      }

      return new DiffSelection(
        this.defaultSelectionType,
        newDivergingLines.size === 0 ? null : newDivergingLines,
        this.selectableLines
      )
    } else {
      const newDivergingLines = new Set<number>()
      for (let i = from; i < to; i++) {
        if (this.isSelectable(i)) {
          newDivergingLines.add(i)
        }
      }

      return new DiffSelection(
        computedSelectionType,
        newDivergingLines,
        this.selectableLines
      )
    }
  }

  /**
   * Returns a copy of this selection instance where the selection state
   * of the specified line has been toggled (inverted).
   *
   * @param lineIndex The index (line number) of the line which should
   *                 be selected or unselected.
   */
  public withToggleLineSelection(lineIndex: number): DiffSelection {
    return this.withLineSelection(lineIndex, !this.isSelected(lineIndex))
  }

  /**
   * Returns a copy of this selection instance with all lines selected.
   */
  public withSelectAll(): DiffSelection {
    return new DiffSelection(DiffSelectionType.All, null, this.selectableLines)
  }

  /**
   * Returns a copy of this selection instance with no lines selected.
   */
  public withSelectNone(): DiffSelection {
    return new DiffSelection(DiffSelectionType.None, null, this.selectableLines)
  }

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

**File:** app/src/lib/patch-formatter.ts (L129-221)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

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
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })

    // Skip writing this hunk if all there is is context lines.
    if (!anyAdditionsOrDeletions) {
      return
    }

    patch += formatHunkHeader(
      hunk.header.oldStartLine,
      oldCount,
      hunk.header.newStartLine,
      newCount
    )
    patch += hunkBuf
  })

```

**File:** app/src/lib/stores/app-store.ts (L3404-3500)
```typescript
  private async updateChangesWorkingDirectoryDiff(
    repository: Repository
  ): Promise<void> {
    const stateBeforeLoad = this.repositoryStateCache.get(repository)
    const changesStateBeforeLoad = stateBeforeLoad.changesState

    if (
      changesStateBeforeLoad.selection.kind !==
      ChangesSelectionKind.WorkingDirectory
    ) {
      return
    }

    const selectionBeforeLoad = changesStateBeforeLoad.selection
    const selectedFileIDsBeforeLoad = selectionBeforeLoad.selectedFileIDs

    // We only render diffs when a single file is selected.
    if (selectedFileIDsBeforeLoad.length !== 1) {
      if (selectionBeforeLoad.diff !== null) {
        this.repositoryStateCache.updateChangesState(repository, () => ({
          selection: {
            ...selectionBeforeLoad,
            diff: null,
          },
        }))
        this.emitUpdate()
      }
      return
    }

    const selectedFileIdBeforeLoad = selectedFileIDsBeforeLoad[0]
    const selectedFileBeforeLoad =
      changesStateBeforeLoad.workingDirectory.findFileWithID(
        selectedFileIdBeforeLoad
      )

    if (selectedFileBeforeLoad === null) {
      return
    }

    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }

    const selectedFileID = changesState.selection.selectedFileIDs[0]

    if (selectedFileID !== selectedFileIdBeforeLoad) {
      return
    }

    const currentlySelectedFile =
      changesState.workingDirectory.findFileWithID(selectedFileID)
    if (currentlySelectedFile === null) {
      return
    }

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
    const updatedFiles = changesState.workingDirectory.files.map(f =>
      f.id === selectedFile.id ? selectedFile : f
    )
```

**File:** app/src/lib/git/diff.ts (L342-356)
```typescript
export async function getWorkingDirectoryDiff(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  hideWhitespaceInDiff: boolean = false
): Promise<IDiff> {
  // `--no-ext-diff` should be provided wherever we invoke `git diff` so that any
  // diff.external program configured by the user is ignored
  const args = [
    'diff',
    ...(hideWhitespaceInDiff ? ['-w'] : []),
    '--no-ext-diff',
    '--patch-with-raw',
    '-z',
    '--no-color',
  ]
```
