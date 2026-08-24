## Analysis

The Tapioca bug's essence: a value used to compute a payout (`totalUsdoDebt - usdoSupply`) is derived by **re-querying live state at action time**, while the decision to act was based on an **earlier snapshot** of related state. Anything that changes the underlying state between the snapshot and the re-query silently produces a wrong result with no validation that the two states still agree.

The closest analog in GitHub Desktop's local code is the partial-commit ("stage selected lines") pipeline.

### Title
Partial-commit line selection is replayed against a freshly re-read diff, letting working-tree drift (e.g. from a repo-provided build/watch script) silently commit unreviewed content - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages only some lines/hunks of a file, Desktop stores that selection as a set of **absolute line indices** computed against the diff that was rendered in the Changes view. At actual commit time, `applyPatchToIndex` does not reuse that diff — it re-fetches a brand-new diff from disk via `getWorkingDirectoryDiff` and replays the old index-based selection against it.

### Finding Description
The flow is: `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts`) passes the `WorkingDirectoryFileChange[]` (each carrying a `DiffSelection` computed against the diff the user reviewed) to `createCommit`, which calls `stageFiles`, which for any partially-selected file calls `applyPatchToIndex`: [1](#0-0) 

Here, `getWorkingDirectoryDiff(repository, file)` recomputes the diff from the current on-disk content, and `formatPatch(file, diff)` then interprets `file.selection.isSelected(absoluteIndex)` (indices captured earlier from the *rendered* diff) against this *new* diff's hunks: [2](#0-1) 

There is no check that the diff used to build the selection is the same diff being patched. If the file's content on disk changes between when the user reviewed/selected lines in the Changes view and when they click "Commit" (`createCommit` → `unstageAll` → `stageFiles`): [3](#0-2) 

the hunk boundaries and absolute line offsets shift, so the same numeric indices now point at different lines. The resulting patch can silently include lines the user never selected/reviewed, or drop lines they explicitly chose to commit — and `git apply --cached` will happily accept it as long as it's structurally valid.

### Impact Explanation
This is a "silent corruption of what the user commits" scenario, explicitly in scope. A repository can ship build/dev tooling (npm scripts, a bundler in `--watch` mode, a formatter-on-save config, etc.) that a developer runs as part of normal workflow while using Desktop to review and commit. If that tooling rewrites a tracked source file shortly after the Changes-view diff is rendered but before the user's click on "Commit" is processed, the user ends up committing (and potentially pushing) content they never saw or approved, with git reporting success and no warning shown by Desktop. This can be used to smuggle unreviewed lines (backdoors, secrets removal/insertion, license changes, etc.) into a commit that a human believed they carefully hand-picked.

### Likelihood Explanation
Medium-Low. It requires a window between diff review and the commit action during which the working tree is mutated by a process the repository itself encourages the developer to run (watch/build/format tooling), which is a common, natural developer workflow rather than an unnatural user step. No local/physical access, admin rights, or pre-existing malware is required — only that the user runs the cloned repository's own build tooling while using Desktop's partial-line commit UI, which is a documented, frequently used feature.

### Recommendation
Capture the diff content hash/identity (or the diff object itself) alongside the `DiffSelection` at selection time, and pass that exact diff through to `applyPatchToIndex`/`formatPatch` instead of re-fetching from disk. If the working tree has changed since the selection was made (e.g. compare file mtime/hash or diff hunk headers), Desktop should refuse the partial commit and force the user to re-review the updated diff rather than silently reconciling stale indices against new content.

### Proof of Concept
1. Clone an attacker-provided repository that includes a normal-looking `npm run dev`/watch script.
2. Edit `file.txt`, open Desktop's Changes view, and select only lines 1–5 of the shown diff for a partial commit (leave "commit" unclicked yet).
3. Before clicking "Commit", the repository's watch/build script (already running as part of the dev workflow) rewrites `file.txt`, shifting/adding lines beyond what was reviewed.
4. Click "Commit". `applyPatchToIndex` re-diffs `file.txt` against HEAD and applies the same absolute-index selection to the new diff via `formatPatch`, staging different hunks/lines than what the user visually selected.
5. The resulting commit — validated via `getChangedFiles`/`getStatusOrThrow` as in existing tests such as `app/test/unit/git/commit-test.ts` (lines 158–367) — contains content the user never reviewed, with no error or warning surfaced by Desktop. [4](#0-3)

### Citations

**File:** app/src/lib/git/apply.ts (L52-61)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

```

**File:** app/src/lib/patch-formatter.ts (L143-171)
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
```

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/test/unit/git/commit-test.ts (L205-258)
```typescript
    it('can commit second hunk from modified file', async t => {
      const testRepoPath = await setupFixtureRepository(t, 'repo-with-changes')
      const repository = new Repository(testRepoPath, -1, null, false)

      const previousTip = (await getCommits(repository, 'HEAD', 1))[0]

      const modifiedFile = 'modified-file.md'

      const unselectedFile = DiffSelection.fromInitialSelection(
        DiffSelectionType.None
      )
      const file = new WorkingDirectoryFileChange(
        modifiedFile,
        { kind: AppFileStatusKind.Modified },
        unselectedFile
      )

      const diff = await getTextDiff(repository, file)

      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.All
      ).withRangeSelection(
        diff.hunks[0].unifiedDiffStart,
        diff.hunks[0].unifiedDiffEnd - diff.hunks[0].unifiedDiffStart,
        false
      )

      const updatedFile = file.withSelection(selection)

      // commit just this change, ignore everything else
      const sha = await createCommit(repository, 'title', [updatedFile])
      assert.equal(sha.length, 7)

      // verify that the HEAD of the repository has moved
      const newTip = (await getCommits(repository, 'HEAD', 1))[0]
      assert.notEqual(newTip.sha, previousTip.sha)
      assert.equal(newTip.summary, 'title')

      // verify that the contents of this new commit are just the modified file
      const changesetData = await getChangedFiles(repository, newTip.sha)
      assert.equal(changesetData.files.length, 1)
      assert.equal(changesetData.files[0].path, modifiedFile)

      // verify that changes remain for this modified file
      const status = await getStatusOrThrow(repository)
      assert.equal(status.workingDirectory.files.length, 4)

      // verify that the file is still marked as modified
      const fileChange = status.workingDirectory.files.find(
        f => f.path === modifiedFile
      )
      assert(fileChange !== undefined)
      assert.equal(fileChange.status.kind, AppFileStatusKind.Modified)
    })
```
