### Title
Stale line-index selection state is silently re-applied to a changed diff, corrupting partial-commit contents - ([File: app/src/lib/stores/app-store.ts])

### Summary
The Tigris `BondNFT.claim()` bug is fundamentally about an index-keyed accumulator (`accRewardsPerShare[epoch]`) being carried forward and reused across a changed state without re-validating that the index still corresponds to the same underlying value, silently producing wrong results for consumers who read it. `GitHub Desktop` has a structurally identical pattern in its partial-commit ("stage selected lines") flow: `DiffSelection` tracks which lines a user wants to commit purely by **line index**, and when a file's diff is reloaded, the old selection's diverging line indices are intersected with the *new* diff's selectable-line index set with no verification that a given index still refers to the same textual content, per `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts:3376-3513` and `DiffSelection.withSelectableLines` in `app/src/models/diff/diff-selection.ts:320-330`.

### Finding Description
When a single working-directory file is selected, `updateChangesWorkingDirectoryDiff` asynchronously loads a fresh diff via `getWorkingDirectoryDiff` and then reconciles the previously-selected line indices against it: [1](#0-0) 

The reconciliation logic is explicitly index-based and the code comment acknowledges the limitation: [2](#0-1) 

`withSelectableLines` then simply keeps any previously-diverging index that still happens to be a member of the new selectable-line set — it does not compare the line *text*, only its numeric index: [3](#0-2) 

Later, `formatPatch` builds the actual git patch to be committed strictly from `file.selection.isSelected(absoluteIndex)`, i.e. purely by index, trusting that index still means what the user intended when they clicked it: [4](#0-3) 

This is the same broken invariant as `BondNFT`: a value (there, `accRewardsPerShare[epoch]`; here, `DiffSelection.divergingLines` index set) is propagated forward across a state transition (there, epoch rollover; here, diff reload) using only positional/key identity, without recomputing or invalidating it against the new authoritative content. If the working tree changes between the time the diff was requested and the time it's re-rendered — e.g., a background `git status`/`_loadStatus` refresh, a checkout, a stash pop, a submodule update, or content rewritten by a smudge/clean filter or hook shipped in a cloned/fetched repository — the set of "selectable" indices shifts, but any surviving index in the intersection is treated as still representing the same change the user reviewed and checked. The staleness guard in `updateChangesWorkingDirectoryDiff` (lines 3456-3464) only bails out if the *file id* or *selection of files* changed, not if the diff content shifted while the same file stayed selected — which is exactly the scenario being reconciled by `withSelectableLines` two lines later.

### Impact Explanation
Because the final commit patch is generated purely from index membership (`formatPatch`), a shift in file content between diff-load and commit can cause Desktop to silently include a *different* line than the one the user visually reviewed and selected, or continue to include a line the user believes they deselected. This is a "silent corruption of what the user commits" scenario: no error is shown, the UI reflects a plausible (but stale) selection state, and the user pushes content they never intended to include (or excludes content they meant to keep), potentially exposing secrets, or committing attacker-influenced content originating from repository-controlled tooling (hooks/filters/build scripts) that rewrites tracked files during a partial-commit workflow.

### Likelihood Explanation
This requires no local/physical access, no admin rights, and no pre-existing malware — only a normal partial-commit workflow (select lines → begin composing a commit) occurring while the working tree is asynchronously mutated, which is a realistic and common Desktop usage pattern given its interval-based background refreshes (`_loadStatus`, ahead/behind checks) and its execution of repository-defined git hooks/filters during checkout, pull, and stash operations. The maintainers' own comment in `app-store.ts` line 3480-3485 explicitly acknowledges "the diff might have changed dramatically since last we loaded it" and states they deliberately chose the cheaper index-based reconciliation instead of validating that selected lines still exist — mirroring the Tigris team's own acknowledged/downgraded stance on the equivalent accumulator issue. However, exact exploitability (magnitude of practical impact, and whether an external/attacker-controlled repository can reliably trigger the race with attacker-chosen content) could not be fully confirmed from the indexed code alone; a live reproduction against `getWorkingDirectoryDiff`/hook execution timing would be needed to establish concrete severity.

### Recommendation
Validate line identity by content (or diff hunk anchor/hash), not purely by numeric index, when reconciling `DiffSelection` across a diff reload — analogous to snapshotting the full accumulator range instead of only the latest key in the Tigris bug. At minimum, `updateChangesWorkingDirectoryDiff` should invalidate the entire partial selection (fall back to `DiffSelectionType.None`/`All` at the file level) whenever the new diff's hunk boundaries/line contents differ from the diff that produced the current selection, rather than best-effort index-intersecting them.

### Proof of Concept
Conceptual PoC (could not be executed in this read-only environment):
1. Open a repository in Desktop, select a modified file, and use the side-by-side diff to check specific added lines for partial commit (`DiffSelection.divergingLines` now holds those exact indices).
2. Trigger a working-tree mutation while the file remains selected and before committing — e.g., another background refresh coincides with a filter/hook (from `.git/hooks` or `.gitattributes` clean/smudge filters shipped in the cloned repo) rewriting the file, or a stash/checkout operation runs concurrently, changing line counts/positions in the file.
3. `updateChangesWorkingDirectoryDiff` reloads the diff, recomputes `selectableLines`, and calls `currentlySelectedFile.selection.withSelectableLines(selectableLines)`, which keeps any previously diverging index that is still a member of the new selectable set — without checking whether that index still points at the same text (`app/src/models/diff/diff-selection.ts:320-330`).
4. The user, seeing the (now stale) check-marks rendered against the new diff, commits; `formatPatch` builds the patch strictly by `isSelected(absoluteIndex)` (`app/src/lib/patch-formatter.ts:157-171`), silently including/excluding lines that no longer correspond to what the user actually reviewed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3497)
```typescript
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
```

**File:** app/src/models/diff/diff-selection.ts (L320-330)
```typescript
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

**File:** app/src/lib/patch-formatter.ts (L153-171)
```typescript
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
