## Title
Modified-row line-selection checkbox always resolves to the same diff line, causing silent inclusion/exclusion mismatches in partial commits - (File: `app/src/ui/diff/side-by-side-diff.tsx`)

## Summary
The bug pattern in the Velodrome report is a typo where two conceptually different values (the "current" checkpoint index and the "previous" checkpoint index) collapse into the same value because the same index is used twice, silently corrupting persisted state (`voted`) that downstream logic trusts. The same structural typo exists in GitHub Desktop's side-by-side diff line-selection handler: `onLineNumberCheckedChanged` computes two values that are supposed to represent two different diff lines (the "before" line and the "after" line of a modified row) but calls the same function with the same argument twice, so both variables always resolve to the identical diff line.

## Finding Description
In `_onLineNumberCheckedChanged`, when a user (un)checks a line-number checkbox in the side-by-side diff view, the handler is supposed to determine the underlying diff-line indices for both the "before" (deleted) and "after" (added) sides of a modified row so that checking a single checkbox toggles the whole logical change: [1](#0-0) 

```ts
const lineBefore = this.getDiffLineNumber(row, column)
const lineAfter = this.getDiffLineNumber(row, column)
```

Both calls pass the same `column` parameter (the column that was actually clicked), so `lineBefore` and `lineAfter` are always identical — there is no distinct lookup for `DiffColumn.Before` vs `DiffColumn.After`. This mirrors the Velodrome typo where `checkpoints[account][_nCheckPoints]` should have been `checkpoints[account][_nCheckPoints - 1]`: a second, supposedly-different index/argument is a copy-paste of the first.

The resulting `DiffSelection` object (built via `selection.withLineSelection`) is what downstream patch-construction logic trusts to decide, line-by-line, what is written into the commit patch. `DiffSelection.isSelected()` is queried per diff-line index when building the patch to apply to the index: [2](#0-1) 

and the same selection-driven line filtering pattern is used to build patches for git operations, e.g. `formatPatchToDiscardChanges`, which decides per-line whether to keep or reverse a change based solely on `selection.isSelected(absoluteIndex)`: [3](#0-2) 

Because the checkbox handler never updates the "opposite" line of a modified row, only one side of what the UI visually presents as a single toggled row is actually updated in the `DiffSelection`. The line on the other side of the same modified row silently keeps its prior (often default = selected) state.

## Impact Explanation
This causes silent corruption of what is staged and committed: a user viewing a "modified" row and unchecking it (expecting the whole change — both the removed old line and the added new line — to be excluded) will only have one side excluded from the patch. The other side stays included, meaning `createCommit`/`_commitIncludedChanges` will generate a commit that partially applies a change the user believed they fully excluded, or vice versa: [4](#0-3) 

This fits the "silent corruption of what the user commits or pushes" impact category: no error, no warning, the UI checkbox state and the actual generated patch diverge for modified-row selections.

## Likelihood Explanation
This triggers on ordinary, expected interactive use of the side-by-side diff view (side-by-side mode, where "Modified" rows with distinct before/after columns exist) any time a user partially stages via checkboxes rather than staging the whole file — a very common Desktop workflow. No attacker-controlled repository content is strictly required to trigger the mismatch itself, though a crafted/malicious repository diff that maximizes the number of "Modified" (paired before/after) rows would make the divergence more consequential and harder for the user to visually notice, increasing the chance that unintended lines are silently included in a push.

## Recommendation
Fix the copy-paste typo so the two lookups use distinct columns, matching how modified rows pair a before-line with an after-line:

```ts
const lineBefore = this.getDiffLineNumber(row, DiffColumn.Before)
const lineAfter = this.getDiffLineNumber(row, DiffColumn.After)
```

Add a regression test (similar to the existing `SideBySideDiffRow` line-selection tests) asserting that toggling a modified row's checkbox updates both the before and after diff-line indices in the resulting `DiffSelection`.

## Proof of Concept
1. Open a file with a "Modified" row in side-by-side diff view (a line changed, shown as a before/after pair).
2. Uncheck the line checkbox for that modified row.
3. Inspect the resulting `DiffSelection` / generated patch: only the line matching the clicked `column` argument is deselected; the paired before/after line number is untouched because `getDiffLineNumber(row, column)` is invoked twice with the same `column` instead of `DiffColumn.Before` and `DiffColumn.After` respectively: [5](#0-4) 
4. Commit the partial selection — the generated patch (built from `DiffSelection.isSelected`) includes/excludes lines inconsistent with what the checkbox state visually implied to the user.

### Citations

**File:** app/src/ui/diff/side-by-side-diff.tsx (L935-961)
```typescript
  private onLineNumberCheckedChanged = (
    row: number,
    column: DiffColumn,
    isSelected: boolean
  ) => {
    if (this.props.onIncludeChanged === undefined) {
      return
    }

    let selection = this.getSelection()
    if (selection === undefined) {
      return
    }

    const lineBefore = this.getDiffLineNumber(row, column)
    const lineAfter = this.getDiffLineNumber(row, column)

    if (lineBefore !== null) {
      selection = selection.withLineSelection(lineBefore, isSelected)
    }

    if (lineAfter !== null) {
      selection = selection.withLineSelection(lineAfter, isSelected)
    }

    this.props.onIncludeChanged(selection)
  }
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

**File:** app/src/lib/patch-formatter.ts (L266-313)
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
      } else if (selection.isSelected(absoluteIndex)) {
        // Reverse the change (if it was an added line, treat it as removed and vice versa).
        if (line.type === DiffLineType.Add) {
          hunkBuf += `-${line.text.substring(1)}\n`
          newCount++
        } else if (line.type === DiffLineType.Delete) {
          hunkBuf += `+${line.text.substring(1)}\n`
          oldCount++
        } else {
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }

        anyAdditionsOrDeletions = true
      } else {
        if (line.type === DiffLineType.Add) {
          // An unselected added line will stay in the file after discarding the changes,
          // so we just print it untouched on the diff.
          oldCount++
          newCount++
          hunkBuf += ` ${line.text.substring(1)}\n`
        } else if (line.type === DiffLineType.Delete) {
          // An unselected removed line has no impact on this patch since it's not
          // found on the current working copy of the file, so we can ignore it.
          return
        } else {
          // Guarantee that we've covered all the line types.
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })
```

**File:** app/src/lib/stores/app-store.ts (L3681-3714)
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
        },
        { gitContext: { kind: 'commit' }, repository }
      )
```
