## Analysis

The Beanstalk finding is a specific instance of a general bug class: **a value is computed and shown/logged for one purpose, but a different (base/stale) value is what actually gets applied**, silently breaking an invariant the user relies on ("what I'm shown is what I get").

The closest real analog in this GitHub Desktop fork is in the diff-rendering code for renamed files, which has a documented, intentional discrepancy between the diff shown to the user and the diff basis actually used elsewhere in the commit pipeline.

### Title
Renamed-file diff is computed against the index instead of HEAD, so the Changes view can silently omit already-staged content that still gets committed - (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryDiff` decides which git revision to diff a working-directory file against based on its status. For ordinary modified files it diffs `HEAD` vs. working tree [1](#0-0) . For renamed files, however, it diffs the **index** vs. working tree with no revision argument, and the code comment explicitly documents that this is wrong: [2](#0-1) 

### Finding Description
`getWorkingDirectoryDiff` is the single source of truth for the diff rendered in the Changes/History review pane [3](#0-2) . For every file status except `Renamed`, the diff is taken against `HEAD`, so "what the user reviews" matches "what a `git commit` of the current index+working tree will actually produce." For `Renamed` files, the diff is computed against the index instead, meaning any content that is already staged for that path (but not yet committed) is invisible in the diff the user reviews - the comment in the code acknowledges this directly ("we won't show any changes already staged to the renamed file which differs from our other diffs").

This value (the rendered diff/hunks) is then the basis for two security/integrity-relevant actions:
- Full commits, which commit the index regardless of the diff shown (so a full "Commit" is unaffected), and
- Partial/line-level operations that are built directly from this same diff object: `applyPatchToIndex` (partial staging) and `discardChangesFromSelection` (discard-by-line), both of which call `formatPatch`/`formatPatchToDiscardChanges` against the exact hunks returned by `getWorkingDirectoryDiff` [4](#0-3) [5](#0-4) [6](#0-5) .

Because the hunks fed into these line-selection code paths are computed relative to the index rather than `HEAD`, a user reviewing and selectively staging/discarding lines of a renamed file can be shown an incomplete picture of the file's real delta from `HEAD` whenever the index already differs from `HEAD` for that path independent of the rename (e.g. content staged by an external tool, a hook, `git add -p` outside Desktop, or a previous partial-stage operation that didn't go through the "blow away and re-add" reset that `applyPatchToIndex` performs specifically for its own use). What the UI displays as "the diff" is not the full set of changes that will end up in the next commit for that path - the same "displayed value ≠ applied value" mismatch pattern as the Beanstalk `incentivize()`/`mint()` bug, just applied to commit content instead of token amount.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": a user can end up committing/pushing content for a renamed file that they never saw in the diff review UI, because Desktop's own diff computation deliberately (per the code comment) omits already-staged deltas for that specific status kind. This is not a hypothetical - the code author flagged the exact defect and left it unresolved rather than fixing it (via the noted but rejected `hash-object` + blob-diff approach).

### Likelihood Explanation
Likelihood is **low-to-moderate** and situational: it requires the index to already hold changes for a path beyond the pure rename before the user opens the Changes view for that file (e.g., partially staged content from a mixed workflow, a Git hook, or another tool operating on the same working copy). It does not require attacker network access; it's a local state-consistency bug rather than a directly attacker-triggerable remote exploit, which is the main reason this is a weaker analog to the original Beanstalk finding than a true remote-attacker path.

### Recommendation
Short term: for `Renamed` files, diff against `HEAD` (using the two-path rename diff, as already done in `getCommitDiff`/`getBranchMergeBaseDiff`) instead of the index, so `getWorkingDirectoryDiff` always reflects the full delta from `HEAD` regardless of file status, matching the invariant "the diff shown is the diff that will be committed." Long term: add unit tests asserting that `getWorkingDirectoryDiff` output for every `AppFileStatusKind` is consistent with the union of staged + unstaged changes relative to `HEAD`, and document this as an explicit invariant of the diff/patch pipeline.

### Proof of Concept
1. In a repository, stage a modification to `foo` (`git add foo`) without committing.
2. Rename `foo` to `bar` on disk (`mv foo bar`) so Desktop's status now reports `bar` as `Renamed` with `renameIncludesModifications` possibly false depending on score, while the index still holds the staged content change under the new path.
3. Open the Changes view for `bar` in Desktop - the diff pane calls `getWorkingDirectoryDiff`, which for `Renamed` status diffs index vs. working tree [2](#0-1) , so the previously staged modification is not shown.
4. Perform a full commit - the committed snapshot includes the staged modification that was never shown in the reviewed diff, demonstrating the mismatch between the displayed and actually-committed content.

### Citations

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
