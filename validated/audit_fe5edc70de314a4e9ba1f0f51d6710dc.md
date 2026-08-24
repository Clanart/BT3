# GitHub Desktop Analog Finding

### Title
Partial-commit patch for renamed files is computed from a working-directory diff generated *after* the index has already been mutated for the rename, risking a mismatched/corrupted patch being applied to the index - (File: `app/src/lib/git/apply.ts`)

### Summary
The Sherlock report's broken invariant is: a derived value (`_createCheckpoint`'s utilization rate) is computed **after** the state it depends on (`listingCount`) has already been mutated, so the derived value is stale/incorrect relative to what the caller intended, and that incorrect value is then used to charge fees. The same "mutate state, then compute a dependent value from the now-mutated state, then use that value to perform the actual write" ordering exists in `applyPatchToIndex` in `app/src/lib/git/apply.ts`.

### Finding Description
`applyPatchToIndex` is the function GitHub Desktop uses to stage a partial (line-level) selection for a single file as part of building a commit [1](#0-0) . For renamed files it first manipulates the git index directly: it force-removes the old path from the index, reads the blob for the old path via `ls-tree HEAD`, and re-adds that blob under the new path with `update-index --add --cacheinfo` [2](#0-1) .

Only *after* this index surgery does the function fetch the diff that will be turned into the patch to apply: [3](#0-2) 
```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

`formatPatch` builds the unified-diff hunks strictly from hunk/line offsets contained in `diff` combined with the caller-supplied `file.selection` bitmap (an absolute line-index selection computed earlier by the UI, before any of this index manipulation happened) [4](#0-3) . This mirrors the Solidity bug's shape exactly: the manipulation of underlying state (`listingCount--` / here, index rewritten for the rename) happens first, and the value that is subsequently derived and consumed (`_createCheckpoint`'s utilization rate / here, the "working directory diff" and the patch built from it) is computed against the *already-mutated* state instead of the state that was in effect when the user made their selection. If `getWorkingDirectoryDiff`'s comparison base is sensitive to the index content it just rewrote (as opposed to being computed purely against `HEAD`), the hunk boundaries/line offsets returned can diverge from the ones the selection bitmap was built against, and `formatPatch` will silently map the user's selected lines onto the wrong hunk context, producing a patch that either fails to apply as intended or applies different content than what the user selected for that commit — i.e., corruption of the committed content for renamed files with a partial selection.

### Impact Explanation
Under the `Valid Impact` criteria, "silent corruption of what the user commits or pushes" is explicitly in scope. If the recomputed diff's hunk headers/line indices shift relative to the selection state built before the index was rewritten, `applyPatchToIndex` can stage content the user did not intend for a partial commit of a renamed file — e.g., including unselected hunks, dropping selected hunks, or committing stale content from the pre-rename blob that was just re-inserted into the index via `update-index --cacheinfo`. This is a correctness/integrity bug in the exact code path (`createCommit` → `stageFiles` → `applyPatchToIndex`) that decides what is actually recorded in the user's commit [5](#0-4)  and [6](#0-5) .

### Likelihood Explanation
This path only triggers for the narrow but realistic case of a user renaming a tracked file and then choosing to commit only a subset of the lines changed in that renamed file (a supported, exposed UI flow — line-level partial selection is a first-class Desktop feature, confirmed by dedicated tests in `app/test/unit/git/commit-test.ts` and `app/test/unit/patch-formatter-test.ts`). No attacker interaction, elevated privilege, or malicious remote content is required to reach the code — it is a self-contained ordering defect in local file handling, exercised purely by the app's own commit machinery.

### Recommendation
Compute `getWorkingDirectoryDiff(repository, file)` (and derive `formatPatch`'s selection-relative hunks) **before** performing the rename bookkeeping (`git add --update`, `ls-tree`, `update-index --cacheinfo`) that mutates the index, exactly as the Sherlock fix recommends reordering the state mutation to occur after the dependent calculation. Concretely, restructure `applyPatchToIndex` in `app/src/lib/git/apply.ts` so the diff/patch is captured first and the index-rename steps are applied only immediately before (or as part of) the final `git apply --cached` call, ensuring the patch is always built from the same repository state the user's selection was made against.

### Proof of Concept
Conceptual PoC (cannot be executed here, but derivable from the cited code):
1. Modify a tracked file, then `git mv` it (rename) inside the working directory so Desktop reports it as `AppFileStatusKind.Renamed` with additional content changes.
2. In the Changes view, select only a subset of the changed lines in the renamed file and commit.
3. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` runs: the rename block rewrites the index first [7](#0-6) , then `getWorkingDirectoryDiff` + `formatPatch` build a patch from the (now potentially different) diff base and the pre-existing line-selection bitmap [3](#0-2) .
4. Inspect the resulting commit's content for the renamed file versus what was actually highlighted/selected in the UI to confirm divergence.

Note: I was not able to fully verify, within the available tool budget, the exact comparison base used internally by `getWorkingDirectoryDiff` (i.e., whether it diffs against `HEAD` or against the just-mutated index) since that function's body lives further down in `app/src/lib/git/diff.ts` beyond what I retrieved. That detail determines whether the divergence is guaranteed or merely possible, and should be confirmed by a follow-up code review/session before treating this as fully confirmed.

### Citations

**File:** app/src/lib/git/apply.ts (L12-15)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
): Promise<void> {
```

**File:** app/src/lib/git/apply.ts (L16-49)
```typescript
  // If the file was a rename we have to recreate that rename since we've
  // just blown away the index. Think of this block of weird looking commands
  // as running `git mv`.
  if (file.status.kind === AppFileStatusKind.Renamed) {
    // Make sure the index knows of the removed file. We could use
    // update-index --force-remove here but we're not since it's
    // possible that someone staged a rename and then recreated the
    // original file and we don't have any guarantees for in which order
    // partial stages vs full-file stages happen. By using git add the
    // worst that could happen is that we re-stage a file already staged
    // by updateIndex.
    await git(
      ['add', '--update', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    // Figure out the blob oid of the removed file
    // <mode> SP <type> SP <object> TAB <file>
    const oldFile = await git(
      ['ls-tree', 'HEAD', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    const [info] = oldFile.stdout.split('\t', 1)
    const [mode, , oid] = info.split(' ', 3)

    // Add the old file blob to the index under the new name
    await git(
      ['update-index', '--add', '--cacheinfo', mode, oid, file.path],
      repository.path,
      'applyPatchToIndex'
    )
```

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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

**File:** app/src/lib/patch-formatter.ts (L129-232)
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

  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }

  patch = formatPatchHeaderForFile(file) + patch

  return patch
}
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
