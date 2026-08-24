## Finding

The Monero report's core broken invariant is: **the data a party reviews and approves is not guaranteed to be the data that actually gets finalized/broadcast** — there's a gap between "what I saw" and "what gets committed," and the software gives no way to detect the mismatch.

GitHub Desktop has a directly analogous, code-documented gap in how it renders the pre-commit diff for renamed files.

### Title
Diff shown for renamed files is computed against the index instead of HEAD, allowing already-staged content to be silently committed without ever being displayed to the user - (File: `app/src/lib/git/diff.ts`)

### Summary
`getWorkingDirectoryDiff` is the function that renders the "what will end up in the commit" preview the user reviews before clicking Commit. For files with `AppFileStatusKind.Renamed`, the function explicitly diffs the working file against the **index** rather than against **HEAD**, and the code comment acknowledges this is wrong: "By diffing against the index we won't show any changes already staged to the renamed file which differs from our other diffs." [1](#0-0) 

### Finding Description
For every other file status (`New`, `Untracked`, `Modified`, `Deleted`), Desktop diffs the working tree against `HEAD`, guaranteeing that what's rendered in the Changes view is exactly what will be committed. For `Renamed` files, the code takes a different path and diffs against the index instead: [2](#0-1) 

This means any content that is already staged in the index but differs from `HEAD` for that path is invisible in the rendered diff — the user only sees the delta between the index and the working directory, not the delta between `HEAD` and what will actually be committed. `createCommit` then commits exactly the staged/selected content: [3](#0-2) 

and `_commitIncludedChanges` in the store feeds whatever `WorkingDirectoryFileChange.selection` was computed off of this same (potentially incomplete) diff straight into the commit: [4](#0-3) 

This is structurally identical to the Monero flaw: the "signer" (the Desktop user clicking Commit) is shown a partial/misleading view of what they're about to sign off on (the diff), while the underlying object that gets finalized (the commit, later pushed) can contain data the user never reviewed. Just as the Monero multisig participant has no reliable way to see or pin the actual input being spent, the Desktop user has no reliable way to see the actual bytes about to be committed for a renamed file whose index differs from HEAD.

### Impact Explanation
A rename status combined with index content that differs from HEAD (e.g., produced through Desktop's own partial-staging/manual-conflict-resolution code paths, a mid-flight `git add` performed by a hook, or a repository/checkout sequence that leaves the index with a different blob than HEAD for the renamed path) causes silent corruption of what the user commits: content the user did not review and did not intend to include gets baked into the commit and can subsequently be pushed. This satisfies "silent corruption of what the user commits or pushes."

### Likelihood Explanation
This requires a specific but reachable state: a file with `AppFileStatusKind.Renamed` where the index and HEAD versions diverge. Desktop's own commit/staging pipeline (`stageFiles`/`unstageAll` cycles in `createCommit` and `_commitIncludedChanges`, plus manual conflict resolution via `stageManualConflictResolution`) routinely manipulates the index independently of the working tree, so this divergence is not a purely theoretical edge case — it's the exact scenario the code comment flags as "technically incorrect." The bug is a pre-existing, acknowledged gap rather than something requiring novel exploitation, which raises likelihood, though it's not the primary/most common commit path (most commits are not renames).

### Recommendation
For `AppFileStatusKind.Renamed` files, compute the diff against `HEAD` (e.g., via blob hashing/`git diff <blob> <blob>` as the comment suggests, or an equivalent that reconciles the rename with staged content) rather than against the index, so the diff shown to the user always matches what `createCommit` will actually persist. At minimum, surface a warning in the UI when the index and HEAD differ in a way not reflected in the rendered diff for a renamed file.

### Proof of Concept
1. Init a repo, commit `foo` with content `line1`.
2. `git mv foo bar`.
3. Stage additional divergent content into the index for `bar` that differs from what's on disk (e.g., via a manual conflict resolution stage step or a hook run during `git add`), so the index blob for `bar` ≠ HEAD blob and ≠ working tree content exactly.
4. Open Desktop's Changes view: the diff rendered for `bar` (per `getWorkingDirectoryDiff`) only reflects index→working-tree deltas, omitting the HEAD→index divergence.
5. Click Commit: `createCommit` (`app/src/lib/git/commit.ts`) stages exactly the files/selections passed in and commits — including the un-reviewed HEAD→index content — producing a commit whose actual diff differs from what the user saw and approved. [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/git/diff.ts (L342-390)
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
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/stores/app-store.ts (L3693-3711)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
```

**File:** app/test/unit/git/diff-test.ts (L316-333)
```typescript
    // A renamed file in the working directory is just two staged files
    // with high similarity. If we don't take the rename into account
    // when generating the diffs we'd be looking at a diff with only
    // additions.
    it('only shows modifications after move for a renamed and modified file', async t => {
      const repo = await setupEmptyRepository(t)

      await writeFile(path.join(repo.path, 'foo'), 'foo\n')

      await exec(['add', 'foo'], repo.path)
      await exec(['commit', '-m', 'Initial commit'], repo.path)
      await exec(['mv', 'foo', 'bar'], repo.path)

      await writeFile(path.join(repo.path, 'bar'), 'bar\n')

      const status = await getStatusOrThrow(repo)
      const files = status.workingDirectory.files

```

**File:** app/test/unit/git/commit-test.ts (L440-470)
```typescript
    // The scenario here is that the user has staged a rename (probably using git mv)
    // and then added some lines to the newly renamed file and they only want to
    // commit one of these lines.
    it('can commit renames with partially selected modifications', async t => {
      const repo = await setupEmptyRepository(t)

      await writeFile(path.join(repo.path, 'foo'), 'line1\n')

      await exec(['add', 'foo'], repo.path)
      await exec(['commit', '-m', 'Initial commit'], repo.path)
      await exec(['mv', 'foo', 'bar'], repo.path)

      await writeFile(path.join(repo.path, 'bar'), 'line1\nline2\nline3\n')

      const status = await getStatusOrThrow(repo)
      const files = status.workingDirectory.files

      assert.equal(files.length, 1)
      assert(files[0].path.includes('bar'))
      assert.equal(files[0].status.kind, AppFileStatusKind.Renamed)

      const selection = files[0].selection
        .withSelectNone()
        .withLineSelection(2, true)

      const partiallySelectedFile = files[0].withSelection(selection)

      const sha = await createCommit(repo, 'renamed a file', [
        partiallySelectedFile,
      ])
      assert.equal(sha.length, 7)
```
