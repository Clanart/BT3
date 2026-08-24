### Title
`getFilesDiffText` leaves the Git index in a corrupted staged state on error, causing unintended files to be committed - (File: `app/src/lib/git/diff.ts`)

### Summary
`getFilesDiffText` mutates the repository's index (staging area) as a side effect of computing a diff for AI-assisted commit-message generation. It resets the index, stages a caller-provided subset of files, runs `git diff`, and only restores the index (`unstageAll`) afterward. There is no `try/finally` around this sequence, so if any step after the initial `unstageAll`/`stageFiles` throws, the working index is left in the temporary "all selected files staged" state instead of the user's original staging selection. A subsequent commit action then commits whatever was left staged, silently diverging from what the user actually selected/intended.

### Finding Description
`getFilesDiffText` in `app/src/lib/git/diff.ts` implements this sequence with no error-handling to guarantee restoration: [1](#0-0) 

Specifically:
1. `await unstageAll(repository)` — wipes the current index (runs `git reset -- .`). [2](#0-1) 
2. `await stageFiles(repository, files)` — stages the caller-selected files (which may not correspond 1:1 to what the user had staged before, e.g. when generating a commit message for only some files). [3](#0-2) 
3. `await git(args, ...)` — invokes `git diff --staged` to compute the diff.
4. `await unstageAll(repository)` — the code path that is supposed to restore the "clean" index state, called only if step 3 succeeds without throwing.

Because steps 2–4 are not wrapped in `try/finally`, any exception thrown by `stageFiles` (e.g. `applyPatchToIndex` failing on a maliciously crafted patch/hunk from an attacker-influenced file, a path with unusual characters causing `git update-index` to fail, or the `git diff` invocation throwing due to `GitError`) will propagate out of `getFilesDiffText` while the index still reflects the temporary "all selected files staged" state rather than the user's real staging selection. The broken invariant is: *the index must return to the user's original staged/unstaged selection regardless of success or failure of the diff computation*. Nothing else in the call path (`app-store.ts`, which calls `getFilesDiffText` when generating AI commit messages/summaries) re-synchronizes the index after a failure — it simply surfaces the error.

This mirrors the root cause of the original report: an operation that stages/moves value into a temporary location assumes the "restore" step will always run, without guarding against early termination — leaving persistent, exploitable side effects (there, locked tokens; here, a corrupted index) instead of restoring the pre-operation state.

### Impact Explanation
If the diff/staging step is interrupted by an error (which can be triggered by content in a cloned/fetched or checked-out repository that an attacker controls — e.g., unusual file content/permissions that cause `git update-index`/`applyPatchToIndex`/`git diff` to fail), the user's index silently ends up containing exactly the (attacker-influenceable) subset of files that `getFilesDiffText` staged, rather than what the user had staged themselves. If the user proceeds to commit afterward without re-checking the Changes list carefully, they can unknowingly commit files/content that were not part of their intended selection — a silent corruption of what gets committed and potentially pushed to a remote. This satisfies the "silent corruption of what the user commits or pushes" impact category from an attacker-influenced repository state.

### Likelihood Explanation
This requires a concrete failure inside `stageFiles`/`applyPatchToIndex`/`git diff --staged` to be reliably triggerable by repository content under attacker control (e.g., a crafted diff hunk that fails to apply, or a pathological file causing a git error) — I was not able to confirm within the available context a guaranteed, deterministic trigger for such a failure from the code alone. The precondition (feature is only invoked opportunistically, during AI commit-message/description generation flows in `app-store.ts`) and the need for a specific git failure mode make this a plausible but not fully verified/demonstrated path; likelihood is therefore assessed as low-to-moderate and would benefit from further investigation of `applyPatchToIndex` failure modes to build a concrete reproduction.

### Recommendation
Wrap the staging/diff/unstage sequence in `getFilesDiffText` in a `try/finally` block, ensuring `unstageAll(repository)` (or an equivalent restoration of the original index state) always executes regardless of whether `stageFiles` or the `git diff` call throws, mirroring the cleanup pattern already used elsewhere in the codebase (e.g., `reorder.ts`, `squash.ts`, `diff.ts`'s own `getResolutionDiff` temp-file cleanup): [4](#0-3) [5](#0-4) 

### Proof of Concept
Not independently reproduced/confirmed in this session — the exact malicious repository content needed to force `stageFiles`/`applyPatchToIndex`/`git diff --staged` to throw mid-`getFilesDiffText` was not verified against the local index code. This finding should be treated as a strong structural/code-review-level lead (missing `try/finally` around an index-mutating operation) rather than a demonstrated exploit chain; a background engineering session with access to run the app/tests would be needed to construct a concrete failing patch/file that reproduces the mid-sequence exception and confirm the resulting stuck-staged-file corruption end-to-end.

### Citations

**File:** app/src/lib/git/diff.ts (L488-551)
```typescript
  const tempBase = getTempFilePath('resolution-diff-base')
  const tempTarget = getTempFilePath('resolution-diff-target')

  try {
    await writeFile(tempBase, baseContent, 'utf8')
    await writeFile(tempTarget, targetContent, 'utf8')

    const args = [
      'diff',
      ...(hideWhitespaceInDiff ? ['-w'] : []),
      '--no-ext-diff',
      '--patch-with-raw',
      '-z',
      '--no-color',
      '--no-index',
      '--',
      tempBase,
      tempTarget,
    ]

    const { stdout } = await git(args, repository.path, 'getResolutionDiff', {
      successExitCodes: new Set([0, 1]),
      encoding: 'buffer',
    })

    if (!isValidBuffer(stdout)) {
      return {
        diff: { kind: DiffType.Unrenderable },
        oldContents: baseContent,
        newContents: targetContent,
      }
    }

    const diff = diffFromRawDiffOutput(stdout)

    if (isDiffTooLarge(diff)) {
      return {
        diff: {
          kind: DiffType.LargeText,
          text: diff.contents,
          hunks: diff.hunks,
          maxLineNumber: diff.maxLineNumber,
          hasHiddenBidiChars: diff.hasHiddenBidiChars,
        },
        oldContents: baseContent,
        newContents: targetContent,
      }
    }

    return {
      diff: {
        kind: DiffType.Text,
        text: diff.contents,
        hunks: diff.hunks,
        maxLineNumber: diff.maxLineNumber,
        hasHiddenBidiChars: diff.hasHiddenBidiChars,
      },
      oldContents: baseContent,
      newContents: targetContent,
    }
  } finally {
    await unlink(tempBase).catch(() => {})
    await unlink(tempTarget).catch(() => {})
  }
```

**File:** app/src/lib/git/diff.ts (L569-608)
```typescript
export async function getFilesDiffText(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  commitish?: string
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

  // `--no-ext-diff` should be provided wherever we invoke `git diff` so that any
  // diff.external program configured by the user is ignored
  const args = [
    'diff',
    '--no-ext-diff',
    '--patch-with-raw',
    '--no-color',
    '--staged',
    ...(commitish ? [commitish] : []),
  ]
  const successExitCodes = new Set([0])

  const { stdout } = await git(args, repository.path, 'getFilesDiffText', {
    successExitCodes,
    encoding: 'buffer',
  })

  await unstageAll(repository)

  // No more than 10MB
  if (stdout.length > 10 * 1024 * 1024) {
    throw new Error('Diff is too large to render')
  }

  // `.toString()` in a promise in case its a large buffer
  const outputString = await (async () => stdout.toString('utf8'))()
  return outputString
}
```

**File:** app/src/lib/git/reset.ts (L97-101)
```typescript
/** Unstage all paths. */
export async function unstageAll(repository: Repository): Promise<true> {
  await git(['reset', '--', '.'], repository.path, 'unstageAll')
  return true
}
```

**File:** app/src/lib/git/update-index.ts (L109-169)
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
}
```

**File:** app/src/lib/git/reorder.ts (L143-150)
```typescript
  } catch (e) {
    log.error(e)
    return RebaseResult.Error
  } finally {
    if (todoPath !== undefined) {
      await rm(todoPath, { recursive: true, force: true })
    }
  }
```
