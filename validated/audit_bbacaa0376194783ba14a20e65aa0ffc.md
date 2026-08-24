### Title
Discard-selection uses a stale, path-only-validated diff object applied with zero-context `git apply`, allowing silent corruption of unrelated working-directory content - (File: `app/src/lib/git/apply.ts`)

### Summary
`discardChangesFromSelection` builds a reverse patch from a diff object that was computed at some earlier point in time and applies it with `git apply --unidiff-zero`, a mode that intentionally omits surrounding context lines. The only staleness check performed before the UI hands this diff back to the store is a match on the *selected file ID* (path/status), never a check that the on-disk content still corresponds to the line numbers/content embedded in the diff object. If the working tree changes between diff computation and the user's discard action (e.g. due to a filter/smudge process, an autocrlf re-normalization, or any asynchronous rewrite of the file triggered by repository content the attacker controls), the "discard changes" action will silently apply an outdated, line-number-based patch to different, more current, content.

### Finding Description
The diff shown to the user and stored in `IChangesState.selection.diff` is computed by `updateChangesWorkingDirectoryDiff`: [1](#0-0) 

The only re-validation that happens before this diff is trusted again is whether the *same file IDs* are still selected — there is no comparison of file content/hash between the time the diff was generated and the time it is later used: [2](#0-1) 

When the user clicks to discard a hunk/line, the *original* diff object captured earlier (not a freshly recomputed one) is passed straight through to the dispatcher and eventually to the git layer: [3](#0-2) [4](#0-3) [5](#0-4) 

`formatPatchToDiscardChanges` builds the reverse patch purely from that (possibly stale) diff's hunk line numbers and text, with zero verification against the file's current on-disk state: [6](#0-5) 

`discardChangesFromSelection` then applies this patch directly against the working tree using `--unidiff-zero`, which minimizes the amount of context git uses to locate/validate the hunk before rewriting lines: [7](#0-6) 

The codebase itself acknowledges elsewhere that file content can race with concurrent modification ("race condition when someone rapidly modifies the file on disk") when computing syntax highlighting line filters, showing the maintainers are aware line-based indexing is unsafe under concurrent mutation, but no such staleness guard exists in the discard-patch path: [8](#0-7) 

The broken invariant is structurally identical to the H-06 report's core problem: a piece of state (`usdsThatShouldBeBurned` vs. the diff snapshot) is allowed to diverge from the real, current resource (actual USDS balance vs. actual file content), and a privileged/destructive action (burning POL vs. `git apply` rewriting the working tree) is executed against the stale accounting value instead of re-verifying against the live resource immediately before acting.

### Impact Explanation
Because `--unidiff-zero` patches are applied primarily by line offset with minimal context validation, if repository-driven or filter-driven content changes shift line positions or replace text between diff snapshot and discard action, `git apply` can succeed while modifying the wrong lines or reintroducing/removing content the user never reviewed. This is a silent corruption of what the user commits: the working tree ends up with content the user did not consent to and did not see in the confirmation dialog, and the corrupted result can subsequently be committed and pushed without the user noticing, since the discard flow calls `_refreshRepository` afterward rather than diffing the actual result against the reviewed selection.

### Likelihood Explanation
The trigger requires the on-disk content of a tracked file to change between the moment Desktop computes the diff shown to the user and the moment the user confirms the discard. GitHub Desktop's threat model explicitly includes attacker-controlled cloned/fetched repositories and git remote/proxy responses; content-dependent filters (line-ending normalization via `core.autocrlf`, LFS smudge re-runs, or other repository-configured filters) driven by attacker-supplied repository content or timing can plausibly cause such asynchronous file rewrites while Desktop is holding a stale diff in its UI state, especially given Desktop's own admission of “race condition when someone rapidly modifies the file on disk” in adjacent code. Exploiting this reliably to target specific corruption would likely require some timing control, so likelihood is moderate rather than trivial, but the code path itself has no defense at all against this class of staleness.

### Recommendation
Before generating and applying a discard patch, re-fetch (or re-verify via hash/mtime) the current on-disk content of the file and confirm it is unchanged since the diff was generated; if it has changed, recompute the diff and re-prompt the user (or abort with an error) rather than silently applying a patch derived from stale line data. Consider avoiding `--unidiff-zero` in favor of patches that carry full context, so `git apply` fails safely instead of potentially misapplying when the underlying file has drifted.

### Proof of Concept
Not independently reproducible from static code alone — a live repro would require constructing a repository/filter setup where `git status`/file ID remain stable while file content changes asynchronously between `updateChangesWorkingDirectoryDiff` computing the diff and the user confirming `DiscardSelection`/`onDiscardChanges`, and then observing that `discardChangesFromSelection` applies the stale hunk against the new content. This cannot be fully confirmed without running the app; it is inferred from the code paths cited above, in particular the absence of any content-staleness check between diff snapshot (`app/src/lib/stores/app-store.ts:3444-3464`, `app/src/lib/stores/updates/changes-state.ts:90-95`) and patch application (`app/src/lib/git/apply.ts:102-120`).

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3464)
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
```

**File:** app/src/lib/stores/updates/changes-state.ts (L90-95)
```typescript
    const diff =
      selectedFileIDs.length === 1 &&
      state.selection.selectedFileIDs.length === 1 &&
      state.selection.selectedFileIDs[0] === selectedFileIDs[0]
        ? state.selection.diff
        : null
```

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1600-1607)
```typescript
    const newSelection = selection
      .withSelectNone()
      .withRangeSelection(startLine, endLine - startLine + 1, true)

    // Pass the original diff (from props) instead of the (potentially)
    // expanded one.
    this.props.onDiscardChanges(this.props.diff, newSelection)
  }
```

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

**File:** app/src/ui/diff/syntax-highlighting/index.ts (L165-177)
```typescript
    } else {
      // If there's a mix (meaning we'll have to read from both
      // anyway) we'll prioritize the old version since
      // that's immutable and less likely to be the subject of a
      // race condition when someone rapidly modifies the file on
      // disk.
      if (line.oldLineNumber !== null) {
        oldLineFilter.push(line.oldLineNumber - 1)
      } else if (line.newLineNumber !== null) {
        newLineFilter.push(line.newLineNumber - 1)
      }
    }
  }
```
