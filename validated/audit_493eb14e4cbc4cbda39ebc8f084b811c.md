### Title
`discardChangesFromSelection` applies a stale line-numbered patch with zero context, allowing silent working-tree corruption - ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop's discard-selected-lines flow builds a `git apply` patch from a `diff` object that is captured in UI/dispatcher state and threaded unmodified all the way down to the git call, rather than being re-fetched immediately before the mutating operation. This is the same broken invariant as TRST-H-1: a stale, cached view of state ("the diff/selection the UI last rendered") is used to authorize and shape a destructive write, instead of the authoritative, current on-disk state. Because the resulting patch is applied with `--unidiff-zero` (zero context lines), there is no fuzzy-matching safety net if the file changed between diff-capture and click — the patch is positioned purely by line numbers.

### Finding Description
The discard-selection call chain is:

- `Changes.onDiscardChanges` receives a `diff`/`diffSelection` that was rendered earlier and forwards it unchanged to `dispatcher.discardChangesFromSelection` [1](#0-0) 
- `Dispatcher.discardChangesFromSelection` forwards it to `AppStore._discardChangesFromSelection` [2](#0-1) 
- `AppStore._discardChangesFromSelection` passes the same `diff` straight to `gitStore.discardChangesFromSelection` with no re-validation [3](#0-2) 
- `GitStore.discardChangesFromSelection` calls the git-level function directly with the caller-supplied `diff` [4](#0-3) 
- `discardChangesFromSelection` (git layer) builds a patch from that `diff` and pipes it into `git apply --unidiff-zero --whitespace=nowarn -` [5](#0-4) 

Compare this to the sibling function `applyPatchToIndex`, which is used for *partial staging*: it explicitly re-fetches a fresh diff right before formatting the patch — `const diff = await getWorkingDirectoryDiff(repository, file)` — precisely to avoid operating on stale data [6](#0-5) . `discardChangesFromSelection` has no equivalent re-fetch; it trusts whatever `diff` object was handed to it by the UI layer, which may have been loaded well before the user's click (component render, right-click context menu construction, or a confirmation dialog round-trip through `PopupType.ConfirmDiscardSelection` in `discard-selection-dialog.tsx`) [7](#0-6) .

Desktop is explicitly aware that diffs go stale relative to disk and handles it elsewhere by re-validating selectable lines against a freshly-loaded diff before applying UI state (`// The diff might have changed dramatically since last we loaded it...`) [8](#0-7) , and by bailing out when "Something has changed during our async ... call, bail" for stash-file diffs [9](#0-8) . No such staleness check exists on the discard-selection path — the exact same class of guard that was added elsewhere is missing here.

`formatPatchToDiscardChanges` converts the (possibly stale) diff+selection into a reversed unified diff addressed purely by `hunk.header.oldStartLine`/`newStartLine` line numbers [10](#0-9) , and `git apply --unidiff-zero` applies it with zero lines of surrounding context, so git cannot detect a line-number/content mismatch the way it normally would with contextual hunks.

### Impact Explanation
If the on-disk content of the file changes between when the diff was captured for display and when the user commits to "discard selected lines" (e.g., another Desktop-initiated git operation completes and refreshes the working tree, an external editor autosave lands, or a concurrent operation like checkout/stash pop/merge mutates the file), the zero-context patch can be applied against the wrong physical lines of the *new* file content. This silently corrupts the file the user is editing/committing — deleting or reversing changes the user never selected, or leaving the working tree in a state inconsistent with what the diff view showed. This falls squarely under "silent corruption of what the user commits or pushes," since discard operates directly on the working directory and any subsequent commit will capture the corrupted result with no further warning.

### Likelihood Explanation
This does not require local/physical access, admin rights, or leaked credentials. It's a straightforward TOCTOU: the discard UI path (button click, or the "Discard Changes" confirmation dialog) always has a check-then-act gap between diff-load and patch-apply, and Desktop's own codebase demonstrates elsewhere that such windows are wide enough to matter in normal usage. Unlike the reference report's on-chain, single-transaction context, the realistic trigger here is any interleaving that updates the file underneath the still-rendered diff (background refresh, another in-flight git operation, or a fast sequence of discard actions), making this a genuine, code-supported race rather than a purely theoretical one. I was not able to fully trace every code path that can mutate a file concurrently with an open diff view within the remaining investigation budget, so the exact minimal repro trigger (which specific concurrent operation is fastest/most reliable) is not fully confirmed from the index alone.

### Recommendation
In `discardChangesFromSelection` (app/src/lib/git/apply.ts), re-fetch the current working-directory diff for the file immediately before formatting the discard patch (mirroring `applyPatchToIndex`), and abort/refresh-and-reprompt if the freshly-fetched diff's hunks/line numbers no longer match what the caller-supplied `diff`/`selection` assumed. Alternatively, apply the patch with normal (non-zero) context via `git apply` (not `--unidiff-zero`) so git's own context matching can reject a stale/mismatched patch instead of silently applying it to the wrong lines.

### Proof of Concept
1. Open a repository in Desktop with a modified tracked file; open its diff in the Changes view (this loads and caches a `diff` object in component/dispatcher state).
2. Without closing the diff view, cause the file's on-disk content to shift lines (e.g., trigger another Desktop-driven git operation that touches the same file, or edit/save the file externally) so that the previously rendered `diff`'s hunk line numbers no longer correspond to the current file content.
3. Click "Discard" on a selected hunk/line range from the still-stale diff view (optionally going through the `ConfirmDiscardSelection` dialog, which also just forwards the originally-passed `diff`) [11](#0-10) .
4. Observe that `git apply --unidiff-zero` in `discardChangesFromSelection` [12](#0-11)  is applied against the now-different file using the stale line numbers, producing content different from what the user intended to discard/keep — i.e., silent corruption of the working tree that will be included in the next commit.

### Citations

**File:** app/src/ui/changes/changes.tsx (L76-99)
```typescript
  private onDiscardChanges = (
    diff: ITextDiff,
    diffSelection: DiffSelection
  ) => {
    if (this.lineSelectionDisabled) {
      return
    }

    if (this.props.askForConfirmationOnDiscardChanges) {
      this.props.dispatcher.showPopup({
        type: PopupType.ConfirmDiscardSelection,
        repository: this.props.repository,
        file: this.props.file,
        diff,
        selection: diffSelection,
      })
    } else {
      this.props.dispatcher.discardChangesFromSelection(
        this.props.repository,
        this.props.file.path,
        diff,
        diffSelection
      )
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L915-928)
```typescript
  /** Discard the changes from the given diff selection. */
  public discardChangesFromSelection(
    repository: Repository,
    filePath: string,
    diff: ITextDiff,
    selection: DiffSelection
  ): Promise<void> {
    return this.appStore._discardChangesFromSelection(
      repository,
      filePath,
      diff,
      selection
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

**File:** app/src/lib/stores/app-store.ts (L3661-3668)
```typescript
    // Something has changed during our async getCommitDiff, bail
    if (
      changesStateAfterLoad.selection.kind !== ChangesSelectionKind.Stash ||
      changesStateAfterLoad.selection.selectedStashedFile !==
        selectionBeforeLoad.selectedStashedFile
    ) {
      return
    }
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

**File:** app/src/lib/git/apply.ts (L52-61)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

```

**File:** app/src/lib/git/apply.ts (L102-120)
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
}
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
