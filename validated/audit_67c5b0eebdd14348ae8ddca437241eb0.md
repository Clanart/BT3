## Title
`applyPatchToIndex()` re-derives a fresh diff at commit-time but reuses a stale UI line-selection, letting a repo-controlled line-ending/content change silently commit unselected/unintended content - ([File: app/src/lib/git/apply.ts])

### Summary
`WithdrawProxy.claim()`'s bug was an incremental accounting update (`y0`) computed from stale/partial state that no longer matched the invariant it was supposed to track, silently corrupting a value users rely on. The Desktop analog is in the partial-commit pipeline: the line-selection bitmap the user builds in the UI is an index into a specific `IDiff` snapshot, but `applyPatchToIndex()` throws that snapshot away and re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff()` right before formatting the patch with `formatPatch()`, which blindly re-applies the old `file.selection` indices to the new hunks/lines. [1](#0-0) 

### Finding Description
`formatPatch()` walks the hunks/lines of whatever `ITextDiff` it is given and decides, line-by-line, whether to keep an addition/deletion based purely on `file.selection.isSelected(absoluteIndex)` - a positional index computed against the hunk layout of the diff that was on screen when the user made their selections: [2](#0-1) 

`applyPatchToIndex()` is the function that actually turns that selection into what gets staged/committed. It does **not** reuse the diff the selection was created against - it calls `getWorkingDirectoryDiff(repository, file)` itself, synchronously, at apply time: [3](#0-2) 

`getWorkingDirectoryDiff()` shells out to `git diff --no-ext-diff --patch-with-raw` against the working tree file on disk at that moment, and Git may normalize line endings (CRLF/LF) depending on `core.autocrlf`/`.gitattributes` `text=auto` settings, emitting a line-endings warning that Desktop parses but does not use to invalidate the selection: [4](#0-3) [5](#0-4) 

Because `DiffSelection.isSelected()` only knows about integer line indices (it has no hash, no content check, no line-count/shape validation against the diff it's being applied to), if the on-disk file changes between when the user made their line-level selections in the Changes view and when `_commitChanges` runs `applyPatchToIndex`/`createCommit`, the new diff can have a different number of hunks/lines or shifted line boundaries (e.g. because `.gitattributes` `text`/`eol` rules normalize CRLF↔LF, or because an editor autosave, background format-on-save, or a git hook mutated the file). The stale selection indices then land on the wrong lines of the new diff: [6](#0-5) 

The result: `formatPatch()` builds a patch that includes lines the user never selected, or drops lines the user did select, and that patch is applied straight to the index and committed - with no user-visible confirmation of which lines actually went in, exactly the "silent corruption of what the user commits" class described in the report.

`.gitattributes` is repository content, so a cloned/fetched repository fully controls whether `text=auto`/`eol=lf`/`eol=crlf` normalization kicks in for a given path, making the divergence between the diff the user reviewed and the diff `formatPatch` is applied against attacker-influenceable without any local access, admin rights, or social engineering beyond the user opening/committing in the cloned repo.

### Impact Explanation
This can cause a user to unknowingly commit and push lines/content they explicitly deselected (or omit lines they selected), silently corrupting the resulting commit content while the UI shows the selection the user believes they made. In the worst case this could be leveraged to smuggle unreviewed lines into a commit that a maintainer later merges, or to hide a deselected secret/credential line from the visible diff review while it still ends up staged.

### Likelihood Explanation
Requires a natural sequence of ordinary Desktop usage (open a cloned/fetched repo containing `.gitattributes` normalization rules or a file whose content changes between diff-render and commit, make a partial line selection, commit) with no special user action beyond normal partial-staging workflows Desktop explicitly supports. No admin rights, local file-system tampering, or leaked credentials are needed - only content that ships inside the repository the user cloned.

### Recommendation
Before calling `formatPatch()`/`applyPatchToIndex()`, re-validate that the diff being formatted still matches the shape (hunk boundaries/line count, and ideally content hash) of the diff the current `DiffSelection` was built against; if it doesn't match, refuse to apply the stale selection and force the UI to re-diff and let the user re-select. At minimum, `getWorkingDirectoryDiff()`'s `lineEndingsChange`/normalization signal should be surfaced to block or warn on partial commits rather than silently proceeding with mismatched line indices.

### Proof of Concept
1. Clone a repository whose `.gitattributes` sets `* text=auto` (or `eol=lf`) for a tracked text file.
2. Modify the file so it has mixed line endings; open it in Desktop's Changes view and deselect specific added lines (partial selection), building a `DiffSelection` keyed to the current diff's line indices.
3. Before committing, trigger any operation that causes Git to re-normalize the file's line endings in the working tree relative to what was shown (e.g., a checkout/reset elsewhere, or simply letting `core.autocrlf` differ from what generated the original diff) so that `getWorkingDirectoryDiff()` called from `applyPatchToIndex()` returns a diff with different line boundaries than the one the selection was made against.
4. Commit the partial selection; `formatPatch()` applies the old line-index selection to the new hunk layout, producing a patch that includes/excludes different lines than what the user selected in the UI - confirm via `git show` that the committed content differs from the intended selection.

### Citations

**File:** app/src/lib/git/apply.ts (L52-83)
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
```

**File:** app/src/lib/patch-formatter.ts (L143-206)
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
```

**File:** app/src/lib/git/diff.ts (L342-401)
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
  const successExitCodes = new Set([0])
  const isSubmodule = file.status.submoduleStatus !== undefined

  // For added submodules, we'll use the "default" parameters, which are able
  // to output the submodule commit.
  if (
    !isSubmodule &&
    (file.status.kind === AppFileStatusKind.New ||
      file.status.kind === AppFileStatusKind.Untracked)
  ) {
    // `git diff --no-index` seems to emulate the exit codes from `diff` irrespective of
    // whether you set --exit-code
    //
    // this is the behavior:
    // - 0 if no changes found
    // - 1 if changes found
    // -   and error otherwise
    //
    // citation in source:
    // https://github.com/git/git/blob/1f66975deb8402131fbf7c14330d0c7cdebaeaa2/diff-no-index.c#L300
    successExitCodes.add(1)
    args.push('--no-index', '--', '/dev/null', file.path)
  } else if (file.status.kind === AppFileStatusKind.Renamed) {
    // NB: Technically this is incorrect, the best kind of incorrect.
    // In order to show exactly what will end up in the commit we should
    // perform a diff between the new file and the old file as it appears
    // in HEAD. By diffing against the index we won't show any changes
    // already staged to the renamed file which differs from our other diffs.
    // The closest I got to that was running hash-object and then using
    // git diff <blob> <blob> but that seems a bit excessive.
    args.push('--', ensureRelativePath(file.path))
  } else {
    args.push('HEAD', '--', ensureRelativePath(file.path))
  }

  const { stdout, stderr } = await git(
    args,
    repository.path,
    'getWorkingDirectoryDiff',
    { successExitCodes, encoding: 'buffer' }
  )
  const lineEndingsChange = parseLineEndingsWarning(stderr)

  return buildDiff(stdout, repository, file, 'HEAD', 'HEAD', lineEndingsChange)
}
```

**File:** app/src/lib/git/diff.ts (L765-781)
```typescript
function parseLineEndingsWarning(error: Buffer): LineEndingsChange | undefined {
  if (error.length === 0) {
    return undefined
  }

  const errorText = error.toString('utf-8')
  const match = lineEndingsChangeRegex.exec(errorText)
  if (match) {
    const from = parseLineEndingText(match[1])
    const to = parseLineEndingText(match[2])
    if (from && to) {
      return { from, to }
    }
  }

  return undefined
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
