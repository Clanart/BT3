## Title
Renamed-file diff shown to user is computed against the index while the actually-applied partial-commit patch is re-derived independently — causing silent mismatch between what is displayed and what gets committed - (File: `app/src/lib/git/apply.ts`, `app/src/lib/git/diff.ts`)

### Summary
`BunniQuoter::quoteSwap` and `BunniHookLogic::beforeSwap` were meant to implement identical fee math but diverged, so what the user was shown (quote) differed from what was actually executed. The analogous invariant in GitHub Desktop is: **the diff a user reviews before committing must be the exact same diff that is turned into the patch actually applied to the index/commit.** In `getWorkingDirectoryDiff` (`app/src/lib/git/diff.ts:342-401`), Desktop explicitly computes the diff for a renamed file *against the index* rather than against `HEAD`, with a comment acknowledging the discrepancy: [1](#0-0) 

This same function is re-invoked inside `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) to build the actual patch that gets `git apply --cached`'d, after the index has already been mutated (old path re-added, new path re-created via `update-index --add --cacheinfo`) a few lines earlier: [2](#0-1) 

### Finding Description
For normal files, `getWorkingDirectoryDiff` diffs the working tree against `HEAD` (`args.push('HEAD', '--', ...)`), which matches what the user's Changes list shows before staging. For renamed files, the code takes a different path — it diffs against the *index*, not `HEAD` — with the author's own comment stating this is "the best kind of incorrect" and that a correct implementation "should perform a diff between the new file and the old file as it appears in HEAD," but instead diffs "against the index," meaning any changes already staged to the renamed file (e.g., from a prior partial commit, a hook, or from `applyPatchToIndex`'s own `git add --update`/`update-index --add --cacheinfo` calls) will not show up in the diff: [1](#0-0) 

The bug-class parallel: just as `quoteSwap` and `beforeSwap` both compute "the fee" but from inconsistent bases (one including hook fee, one not), `getWorkingDirectoryDiff`'s renamed-file branch computes "the diff" from an inconsistent base (index) compared to what the rest of the app assumes (HEAD-relative working tree diff). Because `applyPatchToIndex` first mutates the index (re-adds the old path via `add --update`, restores the old blob under the new path via `update-index --add --cacheinfo`) and only *then* calls `getWorkingDirectoryDiff` to compute the patch it will apply, the diff used to build the actual patch is computed against a moving target (the just-mutated index), not against the pristine `HEAD` state the user reviewed on screen. Since the same `getWorkingDirectoryDiff` result is also what feeds the UI's diff view (via `IChangesetData`/`ICommitSelection.diff`, `app/src/lib/app-state.ts:770-778`), a repository state that causes divergent index vs. HEAD content for a renamed path (attacker-controlled via crafted commit history / merge state a victim checks out, or via a rename with a lingering staged blob) can result in a partial-commit patch built from different hunk content/line offsets than what the user selected lines against, because `formatPatch` (`app/src/lib/patch-formatter.ts:129-232`) walks `diff.hunks` and `absoluteIndex` positions from *whatever diff object it's handed* and blindly maps the user's prior `DiffSelection` (recorded against the on-screen HEAD-relative diff) onto it.

### Impact Explanation
If the diff basis silently shifts between what's rendered (in the changes list, based on an earlier working-directory diff) and what's used to build the `git apply --cached` patch for a renamed file with a partial line selection, the user's selection indices can be applied to a different set of hunks/line offsets than intended. This can silently include or exclude lines the user did not select, corrupting the content of a commit relative to what the user saw and approved — the same "silent corruption of what the user commits" class flagged as valid impact.

### Likelihood Explanation
This requires no local/admin access and no prior malware: it is triggered purely by ordinary repository state (a rename with partial-selection staging, combined with any prior operation — hook, previous partial commit, or a fetched/checked-out repository state — that leaves the renamed path's index content different from `HEAD`). The code path is reachable through the normal "stage some lines of a renamed file, then commit" UI flow, which is core, frequently used Desktop functionality (`stageFiles` → `applyPatchToIndex` → `formatPatch`). No existing guard revalidates that the diff used to build the patch matches the diff the user actually reviewed and selected lines against.

### Recommendation
Compute the diff used for `formatPatch` in `applyPatchToIndex` from the same `HEAD`-relative basis that populates the UI's diff view, or explicitly re-fetch/re-validate the diff immediately before building the patch and reject/re-prompt if the underlying hunks have shifted since the user made their line selection. At minimum, remove the special-cased index-relative diffing for renamed files noted in `app/src/lib/git/diff.ts:379-390` and instead diff the renamed file's working copy against its `HEAD` blob (via `git diff <blob> <blob>` as the existing comment suggests), so the diff basis used for display and for patch construction is always consistent.

### Proof of Concept
Conceptual PoC (would require running the app against a crafted repo, not verifiable purely from static review):
1. Create a repo, commit `foo`.
2. `git mv foo bar`.
3. Stage a partial (line-level) selection of `bar` for commit in Desktop but do not commit yet — this leaves a diff computed against the index at that moment.
4. Externally (e.g., via a pre-commit hook the repo ships, or another partial-commit action for a different file that also touches `bar`'s index entry) mutate the index content for `bar` without changing `HEAD` or the working tree.
5. Complete the commit of the previously-selected partial lines for `bar` in Desktop.
6. Because `applyPatchToIndex` recomputes `getWorkingDirectoryDiff` (index-relative for renamed files) *after* mutating the index, the hunks/offsets used to build the patch no longer match the hunks the user's `DiffSelection` was recorded against in step 3, potentially producing a commit whose content differs from what the user selected in the UI. [3](#0-2) [4](#0-3)

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

**File:** app/src/lib/patch-formatter.ts (L129-146)
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
```
