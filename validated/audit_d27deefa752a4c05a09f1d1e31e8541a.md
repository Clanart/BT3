### Title
Partial-commit patch is built against a re-fetched diff, letting line-selection indices apply to the wrong lines and silently commit unreviewed content - (File: app/src/lib/git/apply.ts)

### Summary
When a user stages only some lines/hunks of a file, GitHub Desktop stores a `DiffSelection` (a set of line-index numbers) computed against the diff that was rendered in the Changes view. At commit time, `stageFiles` (`app/src/lib/git/update-index.ts:166-168`) calls `applyPatchToIndex` for every partially-selected file, which does **not** reuse that reviewed diff — it re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff(repository, file)` [1](#0-0)  and then builds the patch by calling `file.selection.isSelected(absoluteIndex)` against that freshly fetched diff's line indices [2](#0-1) . If the working-directory file's content changes between the moment the user reviewed/selected lines in the UI and the moment the commit patch is generated, the old line-index selection is silently reinterpreted against different diff content, so the final commit can include lines the user never selected/reviewed (or omit lines they did select) without any error or warning.

### Finding Description
The broken invariant is: "the `DiffSelection` line indices are only valid for the exact diff snapshot they were computed against." `applyPatchToIndex` violates this by discarding the diff the selection was derived from and substituting a newly computed one: [3](#0-2) 

`formatPatch` then walks the hunks of this new diff and asks `file.selection.isSelected(absoluteIndex)` [4](#0-3) , trusting that index `N` in the new diff still corresponds to the same logical change the user selected in the old diff. There is no revalidation step comparable to what `updateChangesWorkingDirectoryDiff` does for the *rendering* path — that function explicitly re-derives `selectableLines` and drops stale selections when the diff has changed [5](#0-4)  — but that safeguard runs only for UI display, not for the commit/patch-generation path in `apply.ts`/`update-index.ts`.

The window between "diff shown to user, selection made" and "diff re-fetched to build the patch" can be filled by:
- An attacker-controlled `.gitattributes`/`clean`/`smudge` filter, or `post-checkout`/`pre-commit` hook shipped in a cloned/fetched repository, that rewrites the tracked file's on-disk content asynchronously (filters run on checkout/diff/add operations Desktop itself triggers).
- Any external process (build tool, formatter-on-save, sync client) touching the file during the (however short) interval between the UI diff render and the user pressing "Commit," which Desktop does not guard against for the partial-commit code path.

Existing guards (`unstageAll`/`stageFiles` reset semantics, the `stageManualConflictResolution` path, and the whole-file `--add`/`--remove` path used for `DiffSelectionType.All`) do not help here because they only apply to fully-selected files; the vulnerable code path is specifically the *partial* (`else { partial.push(file) }`) branch [6](#0-5) , which is exactly the case where index-based reinterpretation matters.

### Impact Explanation
This falls in the "silent corruption of what the user commits or pushes" impact category: Desktop can create and push a commit whose actual diff differs from what the user reviewed and explicitly (de)selected in the UI, with no warning, error, or diff re-confirmation. In a supply-chain context (a maintainer reviewing/partially staging changes from a cloned/fetched malicious repository containing crafted filters/hooks), this can cause unintended or attacker-influenced content to be silently committed and potentially pushed upstream.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires (a) the user to be doing a *partial* line/hunk selection (not "stage all"), and (b) some mechanism — a git filter/hook from the cloned repo, or an external tool — modifying the file content in the narrow window between diff review and clicking Commit. Desktop's own diff refresh logic (`updateChangesWorkingDirectoryDiff`) shows the project is aware that diffs can change underneath a selection, but that awareness/mitigation was applied only to the rendering path, not the patch-generation path used at commit time, which is the exact gap identified in the original report (a value trusted downstream without being refreshed/revalidated at the point of use).

### Recommendation
In `applyPatchToIndex`, do not silently re-fetch a new diff and reuse the old `DiffSelection` indices against it. Either:
1. Pass the diff that was actually shown to and selected by the user through to `stageFiles`/`applyPatchToIndex` (threading the reviewed `ITextDiff` object rather than re-deriving it), or
2. Re-fetch the diff, compare it (e.g., hash/hunk structure) against the diff the selection was computed from, and if they differ, abort the commit / re-prompt the user to re-review the selection instead of silently applying stale indices to new content — mirroring the invalidation logic already present in `updateChangesWorkingDirectoryDiff`.

### Proof of Concept
1. Clone/fetch an attacker-supplied repository containing a tracked file `foo.txt` and a `.gitattributes` entry wiring a `clean`/`smudge` or a `pre-commit`/`post-checkout` hook that rewrites `foo.txt` shortly after it is read (e.g. touches the file with different content on any git invocation).
2. In Desktop, open the Changes view; Desktop calls `getWorkingDirectoryDiff` to render the diff (Diff A) and the user selects only lines 1–3 for commit, populating `file.selection` with indices based on Diff A.
3. Before the user presses "Commit," the hook/filter rewrites `foo.txt`'s content (shifting or changing hunks) so a fresh diff (Diff B) has different line ordering/content at the same indices.
4. User clicks "Commit." `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches Diff B and applies the *old* selection indices from Diff A against it [7](#0-6) .
5. The resulting commit contains lines from Diff B at the positions the user thought they selected in Diff A — content the user never reviewed — with no error or warning from Desktop.

Note: I was unable to run this scenario end-to-end without live git/hook execution in this environment, so the PoC is a reasoned trace through the exact source paths (`apply.ts`, `update-index.ts`, `patch-formatter.ts`) rather than an executed reproduction; a Devin session with filesystem/terminal access would be needed to fully validate timing feasibility of the race in a real hook/filter scenario.

### Citations

**File:** app/src/lib/git/apply.ts (L52-84)
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

  return Promise.resolve()
}
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

**File:** app/src/lib/git/update-index.ts (L113-129)
```typescript
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }
```
