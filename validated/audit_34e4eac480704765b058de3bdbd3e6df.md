### Title
`getWorkingDirectoryDiff` diffs renamed files against the index instead of HEAD, silently dropping already-staged hunks from what the user commits - ([File: app/src/lib/git/diff.ts])

### Summary
The reported PoolTogether bug is a class of "read-path/write-path divergence": a getter (`getRewardsAmount`) omits a check that the corresponding mutator (`claimRewards`) performs, so the value shown to the user does not match what actually happens on-chain. GitHub Desktop has a structurally identical divergence in the diff-preview path used to build partial commits: for renamed files, `getWorkingDirectoryDiff` computes the diff the user sees and selects lines from by comparing the working tree to the **index**, while every other file-status diff in the same function is computed against **HEAD**. The code itself flags this as incorrect.

### Finding Description
`getWorkingDirectoryDiff` in `app/src/lib/git/diff.ts` builds the `git diff` arguments differently depending on file status: [1](#0-0) 

For `AppFileStatusKind.Renamed`, the function runs `git diff -- <path>` with no `HEAD` argument, which diffs the working tree against the index. Every other branch (the `else` case) explicitly diffs against `HEAD`: [2](#0-1) 

The comment directly above this code admits the inconsistency:
> "NB: Technically this is incorrect, the best kind of incorrect. In order to show exactly what will end up in the commit we should perform a diff between the new file and the old file as it appears in HEAD. By diffing against the index we won't show any changes already staged to the renamed file which differs from our other diffs."

This diff is not just used for display — it is the source of truth for partial-commit line selection. `applyPatchToIndex` (`app/src/lib/git/apply.ts`) calls `getWorkingDirectoryDiff`, then `formatPatch` (`app/src/lib/patch-formatter.ts`) turns the user's line selection over that diff into a patch that is applied with `git apply --cached`: [3](#0-2) 

`stageFiles` (`app/src/lib/git/update-index.ts`) is invoked after the whole index has effectively been rebuilt to reflect exactly the user's checked/unchecked selection state, treating this diff as authoritative for what will end up staged: [4](#0-3) 

Because the renamed-file diff is computed against the index rather than HEAD, any content that was already staged for that path (e.g., staged via the CLI, a prior partial-commit operation, or a tool that manipulates the index outside Desktop) before Desktop reads the working directory state is invisible in the diff Desktop shows and lets the user select from. Since the commit machinery reconstructs the index purely from the file's `DiffSelection` over this diff, hunks that exist in the index-vs-HEAD delta but not in the working-tree-vs-index delta are never re-created — they are dropped from the eventual commit even though the user never had a chance to see or deselect them.

### Impact Explanation
This is a "silent corruption of what the user commits" bug: the user reviews a diff, selects "all" (or specific) lines believing that reflects everything that will be committed, but the actual commit can omit already-index-staged content for renamed files without any warning, error, or visual indication. Unlike the PoolTogether bug (which merely overstates a claimable amount), this can cause real data/history loss in the user's repository — changes the user believed were part of a commit silently vanish from HEAD, and the working directory may end up in a state where recovering the dropped hunk is non-obvious. This matches the "silent corruption of what the user commits or pushes" impact category from an unprivileged, attacker-uncontrolled context (any workflow that ends up with index content for a renamed path diverging from what's in the working tree, e.g. mixed CLI/Desktop use, hooks, or tooling that stages a rename plus content changes).

### Likelihood Explanation
The trigger condition — a renamed file whose index content already differs from HEAD before Desktop computes the diff — is not exotic: it occurs whenever a user mixes `git mv`/staged edits via CLI or another tool with GitHub Desktop's partial-commit UI, or when Desktop's own selective-staging flow leaves index state from a previous partial operation. The bug is also explicitly acknowledged by the code's own comment, indicating it's a known, reachable edge case rather than a theoretical one, though it requires a specific rename+prior-staging sequence rather than being triggered on every commit.

### Recommendation
Change the renamed-file branch of `getWorkingDirectoryDiff` to diff against `HEAD` (consistent with the other branches), e.g. `args.push('HEAD', '--', ensureRelativePath(file.path))`, so the diff/patch/selection pipeline always reflects the true HEAD-to-working-tree delta that will end up in the commit. If a rename-specific reason exists for diffing against the index, at minimum surface the discrepancy to the user (e.g., detect and warn when index content differs from HEAD for the renamed path) before finalizing the commit, mirroring how `claimRewards` explicitly re-checks state that `getRewardsAmount` failed to check.

### Proof of Concept
1. In a Desktop-managed repo, create `foo` with content `line1`, commit it.
2. Outside Desktop (or via a prior Desktop partial-commit operation), stage a rename `foo -> bar` **and** stage additional content changes to `bar` in the index (e.g. `git mv foo bar && git add bar` after editing `bar`).
3. In the working tree, make a further edit to `bar` (e.g. append another line) without staging it.
4. Open GitHub Desktop: the changed-file view for `bar` (status `Renamed`) will run `getWorkingDirectoryDiff`, which does `git diff -- bar` (working tree vs index) — the diff hunk(s) representing the already-staged content changes (index vs HEAD) are not shown at all; only the newest unstaged edit appears.
5. Select "all" shown lines and commit via Desktop.
6. Inspect the resulting commit (`git show <sha>`): the already-staged content changes from step 2 are missing from the commit despite never being deselected by the user, because Desktop rebuilt the index solely from the incomplete diff shown in step 4 (`app/src/lib/git/apply.ts` lines 52-81, `app/src/lib/git/update-index.ts` lines 109-168). [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** app/src/lib/git/apply.ts (L12-84)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
): Promise<void> {
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
  }

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
