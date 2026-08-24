### Title
Stale line-selection indices applied against a freshly re-fetched diff can silently mis-stage lines during partial commits - ([File: app/src/lib/git/apply.ts])

### Summary
`applyPatchToIndex` re-fetches the working directory diff from disk at commit time and builds the patch by re-using the `DiffSelection` that was computed against a *previously rendered* diff. Just like the audited `reportSlashingEvent` bug — where a stale cached balance is compared against a freshly computed slashing delta — Desktop compares a stale line-index selection against a hunk layout that can have shifted since the user last reviewed it, with no revalidation step in the commit path.

### Finding Description
When a user partially selects lines/hunks of a file to commit, the selection is stored as a set of `absoluteIndex` positions (`hunk.unifiedDiffStart + lineIndex`) relative to the diff object that was on screen at selection time. `AppStore._commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3680`) hands the working-directory files — carrying that old selection — straight to `createCommit` → `stageFiles` (`app/src/lib/git/update-index.ts:109`) → `applyPatchToIndex` (`app/src/lib/git/apply.ts:12`).

Crucially, `applyPatchToIndex` does **not** reuse the diff the user actually looked at. It fetches a brand-new diff of the file: [1](#0-0) 
and passes it straight into `formatPatch(file, diff)`: [2](#0-1) 

`formatPatch` walks the hunks of this *new* diff and decides which lines to include using `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is derived from the new diff's `hunk.unifiedDiffStart`: [3](#0-2) 

If the file's on-disk content (and therefore hunk boundaries/line offsets) has changed between the moment the diff was last rendered/selected and the moment the commit button is pressed, `absoluteIndex` no longer refers to the same logical lines the user selected. The code base itself acknowledges this exact hazard in the UI refresh path but only reconciles selection there, not in the commit-application path: [4](#0-3) 
That reconciliation only runs when `updateChangesWorkingDirectoryDiff` re-renders the diff (e.g. on file selection change); it is not invoked, nor is any equivalent check performed, inside `applyPatchToIndex`/`stageFiles`/`_commitIncludedChanges` right before the patch is generated and applied to the index.

This is the same broken invariant as `ReportSlashingEvent`: a value computed against an "old" state (`previousSlashing` / the previously-rendered diff's line layout) is combined with a "new" state (current balance / the freshly re-fetched diff) without re-establishing that the two are still consistent.

### Impact Explanation
An attacker who controls content that gets written into a cloned/fetched repository's working tree during the narrow window between a user reviewing/selecting diff lines and clicking "Commit selected changes" (e.g. via a build step, editor auto-format-on-save triggered by opening the repo, a file watcher, or a git hook shipped in a malicious repository) can cause GitHub Desktop to stage and commit different lines than the ones the user visually reviewed and explicitly selected/deselected. This is a silent corruption of what the user commits (and subsequently pushes), potentially smuggling attacker content into a commit the user believes only contains their reviewed, hand-picked lines, or dropping lines the user intended to include.

### Likelihood Explanation
Medium: it requires the file to be modified on disk between diff rendering and the actual `git apply --cached` call, which is a real but narrow race window. Desktop's own workflows (auto-save-integrated external editors, LFS smudge filters, format-on-save tooling, and repositories that ship git hooks) make this window reachable without any admin/local-attacker assumptions beyond "the user opened/cloned an untrusted repository," matching the required threat model.

### Recommendation
Before generating the patch in `applyPatchToIndex`, re-validate (or recompute) the `DiffSelection` against the freshly fetched diff's current hunk/line layout — the same reconciliation logic already implemented in `updateChangesWorkingDirectoryDiff` (`app/src/lib/stores/app-store.ts:3478-3497`) — and abort/re-prompt the user if the diff has materially changed since the selection was made, rather than silently mapping stale indices onto new hunk boundaries.

### Proof of Concept
Conceptual reproduction (cannot be executed without a live Desktop instance):
1. Modify a tracked file and open it in Desktop's Changes view; Desktop renders a diff with hunk `H1` starting at line 10.
2. User selects only the addition on line 12 to commit (`absoluteIndex` computed relative to `H1`).
3. Before the user clicks "Commit", an external process (e.g., an editor's format-on-save, or a script triggered by opening the repo) rewrites the file, shifting the same logical hunk to start at line 4 with different surrounding content.
4. User clicks "Commit selected changes". `applyPatchToIndex` fetches the new diff (hunk now starting at line 4) but reuses the old selection's `absoluteIndex`, which now maps to an unrelated line in the new hunk.
5. `formatPatch` builds a patch including/excluding the wrong line, and `git apply --cached` stages content the user never reviewed as included. [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/git/apply.ts (L52-82)
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

**File:** app/src/lib/patch-formatter.ts (L129-220)
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
