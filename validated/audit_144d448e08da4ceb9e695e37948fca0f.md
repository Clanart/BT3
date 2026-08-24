This confirms the exploitable path. Let me trace the exact scenario: `createCommit` calls `unstageAll` (index reset) then `stageFiles`, which for `AppFileStatusKind.Renamed` partial selections calls `applyPatchToIndex`, which itself calls `getWorkingDirectoryDiff` — the same function whose comment admits it diffs against the index rather than HEAD for renamed files, hiding already-staged content.### Title
Renamed-file diffs are computed against the index instead of HEAD, silently dropping already-staged content from partial commits - ([File: app/src/lib/git/diff.ts])

### Summary
For files with `AppFileStatusKind.Renamed`, `getWorkingDirectoryDiff` intentionally diffs the working tree against the index rather than against `HEAD`. The developers themselves flag this as "technically incorrect" because it hides changes already staged on the renamed file. When a user then makes a *partial* selection commit on that same file, `createCommit` blows away the index (`unstageAll`) and rebuilds it purely from the (incomplete) diff via `applyPatchToIndex`/`formatPatch`. Any content that was already staged but invisible in the diff is never reproduced in the patch, so it is silently excluded from the resulting commit even though the user never chose to exclude it and the UI gave no indication anything was missing.

### Finding Description
`getWorkingDirectoryDiff` (`app/src/lib/git/diff.ts:342-401`) branches on file status:
- New/Untracked files: diff `--no-index` against `/dev/null`.
- Renamed files: diff **working tree vs. index** (no `HEAD` argument), with an inline comment acknowledging the mismatch:
  > "NB: Technically this is incorrect... By diffing against the index we won't show any changes already staged to the renamed file which differs from our other diffs."
- Everything else: diff working tree vs. `HEAD`.

This diff is the sole data source used to build the interactive Changes view *and* the basis for constructing a commit patch when the user doesn't select "all" for that file. The commit flow is:
1. `createCommit` (`app/src/lib/git/commit.ts:15-31`) calls `unstageAll` (`git reset -- .`), which destroys any pre-existing index state (including whatever was already staged for the renamed file), then calls `stageFiles`.
2. `stageFiles` (`app/src/lib/git/update-index.ts:109-168`) sends files with a full ("All") selection through `updateIndex` (a plain `git add`-equivalent from the working tree, which is safe), but sends any file with a partial selection through `applyPatchToIndex`.
3. `applyPatchToIndex` (`app/src/lib/git/apply.ts:12-84`) re-fetches the diff via `getWorkingDirectoryDiff` and builds a patch with `formatPatch` using only the hunks/lines present in *that* diff, then applies it with `git apply --cached`.

Because the index was already wiped in step 1, and the rename-path diff in step 2/3 never includes content that was staged prior to the reset, that content has no representation anywhere in the patch. The final commit is built strictly from `HEAD` + the (incomplete) patch, so the already-staged bytes are permanently absent from the resulting commit and left in limbo relative to the user's mental model — the user believed the diff/checkbox selection they viewed and interacted with fully represented "what will end up in the commit," but it did not.

### Impact Explanation
This is a silent corruption of what the user commits, matching the requested bug class (the accounting basis used for "what will be included" doesn't match what's actually written), without needing any special permissions:
- Content the user (or an external tool, e.g. IDE integration, `git mv` combined with `git add`, LFS, or another Git GUI) has already staged on a renamed file can vanish from a commit made through Desktop, with no error, warning, or diff indication.
- Because this happens during a normal, unprivileged commit flow on the user's own repository, it directly violates the "silent corruption of what the user commits or pushes" impact category. It is not merely a UI cosmetic bug — the git object graph (blob content of the committed rename) differs from what was actually staged and intended.
- Severity is bounded by requiring a rename + partial-selection commit + pre-existing staged changes; it does not enable remote code execution or credential theft, so it sits at a moderate integrity-loss level rather than a critical one.

### Likelihood Explanation
Requires: (1) the file is renamed, (2) it has been (partially) modified further and some of that modification is staged in the index prior to opening/committing via Desktop, and (3) the user chooses a partial line/hunk selection for that file when committing. Renames plus mixed manual `git`/Desktop usage are a realistic combination for developers who alternate between CLI and GUI. Full-selection commits are unaffected, since `updateIndex`/`git add` for `All`-selection stages the working tree directly, bypassing the flawed diff. The bug is also explicitly documented in a code comment, indicating engineers were aware of it and consciously deferred fixing it — increasing confidence that the described mismatch is real and reachable, though it only manifests for the narrower partial-selection + prior-staged-content combination (not on every rename).

### Recommendation
For renamed files, compute the diff against `HEAD` (comparing the renamed path in the working tree to the old path's blob at `HEAD`, e.g., via `git hash-object`/`git diff <blob> <blob>` as the code comment already suggests) instead of against the index, so the diff — and any partial-selection patch derived from it — reflects everything that would actually change relative to `HEAD`. At minimum, before building a partial-commit patch for a renamed file, verify (pre/post check, analogous to the token balance check requested in the original report) that the object produced by `git apply --cached` matches the expected result of `working tree minus deselected hunks relative to HEAD`, and fail loudly if it doesn't, rather than silently applying an incomplete patch.

### Proof of Concept
1. In a Desktop-managed repository, commit a file `foo` with content `line1\n`.
2. Rename it: `git mv foo bar`.
3. Modify `bar` to `line1\nline2\n` and stage it externally: `git add bar` (index now has `line1\nline2\n` for `bar`).
4. Further modify `bar` in the working tree to `line1\nline2\nline3\n` (unstaged change on top).
5. Open Desktop: `getWorkingDirectoryDiff` for `bar` (Renamed) diffs working tree vs. index, so it only shows the `+line3` hunk — the previously staged `+line2` change is invisible in the UI.
6. Select only the shown hunk (`+line3`) for a partial commit and commit via Desktop.
7. `createCommit` runs `unstageAll` (destroying the `+line2` staged state), then `applyPatchToIndex` builds a patch containing only `+line3` (since that's all the diff exposed) and applies it on top of `HEAD`'s `foo` content.
8. Result: the new commit contains `bar` = `line1\nline3\n` — the `line2` content that was genuinely staged and that the user never asked to exclude is permanently missing from the commit, with no warning shown anywhere in the app. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/git/diff.ts (L379-390)
```typescript
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
```

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/update-index.ts (L109-168)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
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

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

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
