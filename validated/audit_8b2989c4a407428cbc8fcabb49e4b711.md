Based on my investigation, I found the key analog: `_discardChangesFromSelection` in `app-store.ts` never re-fetches or validates the `diff` object against the current on-disk file state before building and applying a discard patch — unlike its sibling functions (`updateChangesWorkingDirectoryDiff`, `updateChangesStashDiff`, `_changeFileSelection`) which explicitly capture `stateBeforeLoad`/`stateAfterLoad` snapshots and bail out if the state changed underneath them.

### Title
Stale-diff discard applies wrong hunks to working-tree file, silently corrupting uncommitted changes - (File: app/src/lib/stores/app-store.ts)

### Summary
`_discardChangesFromSelection` [1](#0-0)  takes a `diff` object and a `DiffSelection` computed from the UI at some earlier point in time and, without re-validating that the `diff` still matches the file's current on-disk content, hands both straight to `gitStore.discardChangesFromSelection` [2](#0-1)  which builds a reverse patch via `formatPatchToDiscardChanges` [3](#0-2)  and applies it with `git apply --unidiff-zero` [4](#0-3) .

### Finding Description
This mirrors the reported bug-class exactly: a value ("the diff at addition time" / here, "the diff snapshot the selection was computed against") is captured once and reused later for a destructive operation, with no mechanism to detect that the underlying state has drifted since the snapshot was taken.

Elsewhere in the same file, the pattern of capturing a `stateBeforeLoad` and comparing it to `stateAfterLoad` before committing a diff into the UI is followed rigorously — e.g. `updateChangesWorkingDirectoryDiff` bails if the file selection changed while the diff was loading [5](#0-4) , and `_changeFileSelection` performs the same check before storing a loaded diff [6](#0-5) . But `_discardChangesFromSelection` — the one operation that actually mutates the working tree — has no equivalent staleness guard: it never re-fetches the current diff for `filePath` or compares it against the `diff` argument's line offsets before generating the reverse patch [1](#0-0) .

The patch generator (`formatPatchToDiscardChanges`) computes hunk headers and line reversals purely from the `diff` argument's `hunk.header.oldStartLine`/`newStartLine` and the file's line indices [7](#0-6) , then applies it via `git apply --unidiff-zero`, which trusts the provided line numbers rather than doing context-based fuzzy matching. If the working-tree file changes between the moment the diff was rendered (e.g. via an external editor, another tool, or an asynchronous refresh triggered by a background `_refreshRepository` or file-watcher) and the moment the user confirms "Discard changes", the stale `diff`'s line numbers no longer correspond to the file's actual current content. `git apply --unidiff-zero` with zero context can then apply the reversal at the wrong location, silently modifying/removing content the user never intended to discard — analogous to how the OI removal used a stale price/state snapshot instead of the current one.

Note that `updateChangesWorkingDirectoryDiff` does attempt to invalidate stale *selectable lines* on a subsequent diff refresh [8](#0-7) , but this reconciliation only happens the *next* time the diff is refreshed — it does not run synchronously before `_discardChangesFromSelection` executes, and there is no guarantee a refresh happens between the diff being rendered and the user clicking "Discard".

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": a discard-selection action, triggered by the user's own click but operating on data that has silently gone stale, can destroy or corrupt unrelated working-tree content without any git-level warning (since `git apply` succeeds as long as the patch context matches *something* nearby). The user has no way to detect the corruption until they inspect the file's actual diff. Given the low-friction trigger (any operation that changes file content concurrently with an open diff view, such as external editors, build tools/formatters, `.gitignore`-aware watchers, or IDE auto-save), Impact is best characterized as Medium.

### Likelihood Explanation
Likelihood is Medium: it requires the file to change between diff render and discard-click, which is common in normal developer workflows (editors autosaving, code formatters running on save, git hooks). No special attacker control over a remote/API is required, but the race window is realistic and reproducible in everyday use of Desktop's Changes/discard-selection UI.

### Recommendation
Before generating the discard patch, re-fetch the current diff (or at minimum the current file's mtime/hash) for `filePath` inside `_discardChangesFromSelection`/`discardChangesFromSelection`, and abort (or reload the diff and ask the user to re-select) if it no longer matches the `diff` argument that was captured when the discard UI was rendered — following the same before/after state-comparison pattern already used by `updateChangesWorkingDirectoryDiff` and `_changeFileSelection`.

### Proof of Concept
1. Open GitHub Desktop, modify a tracked text file so it shows a multi-hunk diff in the Changes view.
2. In the side-by-side diff, select a subset of added lines and choose "Discard changes" but do not yet confirm (or trigger the flow via the `DiscardSelection` dialog which holds the `diff`/`selection` in component state [9](#0-8) ).
3. While the confirmation dialog is open, modify the same file externally (e.g., via a text editor or `sed`) so that line offsets shift.
4. Confirm the discard. `formatPatchToDiscardChanges` builds the reverse patch using the stale line numbers from step 2's `diff`, and `git apply --unidiff-zero` applies it against the file as it now exists on disk, potentially discarding/corrupting different lines than the ones the user selected — with no error surfaced to the user (`git apply` succeeds silently).

### Citations

**File:** app/src/lib/stores/app-store.ts (L2099-2114)
```typescript
    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const { shas: shasAfter } = stateAfterLoad.commitSelection
    // A whole bunch of things could have happened since we initiated the diff load
    if (
      shasAfter.length !== shas.length ||
      !shas.every((sha, i) => sha === shasAfter[i])
    ) {
      return
    }

    if (!stateAfterLoad.commitSelection.file) {
      return
    }
    if (stateAfterLoad.commitSelection.file.id !== file.id) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L3453-3464)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L5723-5733)
```typescript
  public async _discardChangesFromSelection(
    repository: Repository,
    filePath: string,
    diff: ITextDiff,
    selection: DiffSelection
  ) {
    const gitStore = this.gitStoreCache.get(repository)
    await gitStore.discardChangesFromSelection(filePath, diff, selection)

    return this._refreshRepository(repository)
  }
```

**File:** app/src/lib/stores/git-store.ts (L1651-1659)
```typescript
  public async discardChangesFromSelection(
    filePath: string,
    diff: ITextDiff,
    selection: DiffSelection
  ) {
    await this.performFailableOperation(() =>
      discardChangesFromSelection(this.repository, filePath, diff, selection)
    )
  }
```

**File:** app/src/lib/patch-formatter.ts (L251-335)
```typescript
export function formatPatchToDiscardChanges(
  filePath: string,
  diff: ITextDiff,
  selection: DiffSelection
): string | null {
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

    // Skip writing this hunk if all there is is context lines.
    if (!anyAdditionsOrDeletions) {
      return
    }

    patch += formatHunkHeader(
      hunk.header.newStartLine,
      newCount,
      hunk.header.oldStartLine,
      oldCount
    )
    patch += hunkBuf
  })

  if (patch.length === 0) {
    // The selection resulted in an empty patch.
    return null
  }

  return formatPatchHeader(filePath, filePath) + patch
}
```

**File:** app/src/lib/git/apply.ts (L102-119)
```typescript
export async function discardChangesFromSelection(
  repository: Repository,
  filePath: string,
  diff: ITextDiff,
  selection: DiffSelection
) {
  const patch = formatPatchToDiscardChanges(filePath, diff, selection)

  if (patch === null) {
    // When the patch is null we don't need to apply it since it will be a noop.
    return
  }

  const args = ['apply', '--unidiff-zero', '--whitespace=nowarn', '-']

  await git(args, repository.path, 'discardChangesFromSelection', {
    stdin: patch,
  })
```

**File:** app/src/ui/discard-changes/discard-selection-dialog.tsx (L113-126)
```typescript
  private discard = async () => {
    this.setState({ isDiscardingSelection: true })

    await this.props.dispatcher.discardChangesFromSelection(
      this.props.repository,
      this.props.file.path,
      this.props.diff,
      this.props.selection
    )
    this.props.dispatcher.setConfirmDiscardChangesSetting(
      this.state.confirmDiscardSelection
    )
    this.props.onDismissed()
  }
```
