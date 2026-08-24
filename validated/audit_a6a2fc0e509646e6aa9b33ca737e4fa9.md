### Title
Selective staging trusts a freshly re-fetched diff without validating it against the diff the user actually reviewed, allowing silent corruption of partial commits - ([File: app/src/lib/git/apply.ts])

### Summary
The external report's broken invariant is: an external component (the router) is trusted to have performed an action (a token transfer) exactly as claimed, and the caller proceeds without an independent "before/after" check to confirm it actually happened. The Desktop analog is the "partial commit" / selective-line-staging pipeline: the UI computes a `DiffSelection` (a set of *line indices*) against one `IDiff` object that was fetched at some earlier point in time, but `applyPatchToIndex` re-fetches a brand-new diff of the file from disk at staging time via `getWorkingDirectoryDiff` and blindly re-applies the old index-based selection to that new diff [1](#0-0) , with no hash/identity check that the new diff is the same one the selection was computed against.

### Finding Description
`stageFiles` builds the index to reflect exactly "what the user has selected in the app" [2](#0-1) . For files with a partial selection, it calls `applyPatchToIndex(repository, file)` for each file [3](#0-2) .

Inside `applyPatchToIndex`, the diff used to build the actual patch that gets applied to the index is **not** the diff object the UI displayed to the user and against which the user made line-level selections. Instead it is fetched fresh, right before staging:
```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
``` [1](#0-0) 

`formatPatch` decides whether each line is included by calling `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is computed purely from the *newly fetched* diff's hunk offsets (`hunk.unifiedDiffStart + lineIndex`) [4](#0-3) . The `DiffSelection` itself carries no reference to which diff/version it was derived from — it is just a bitset of indices [5](#0-4) .

Desktop is aware that a diff can change between the time it is shown and the time it's used, and it has a "staleness" resync path — but only for the UI's *own* previously-loaded diff, done when the user is actively re-selecting lines in the Changes view:
```
// The diff might have changed dramatically since last we loaded it.
// Ideally we would be more clever about validating that any partial
// selection state is still valid by ensuring that selected lines still
// exist but for now we'll settle on just updating the selectable lines...
``` [6](#0-5) 

That comment explicitly documents the acknowledged gap: line-index-based selections aren't validated against diff content. Critically, **no equivalent reconciliation happens on the commit path**. `applyPatchToIndex` does not compare the diff it just fetched against the one the selection was built from, does not diff-hash-check, and does not re-render the selection against the new hunks before generating the patch — it just re-applies raw indices to whatever hunks come back from `git diff` at that instant.

If the working-tree file content changes between the moment the user reviews/selects lines and the moment `_commitIncludedChanges`/`createCommit` actually calls `stageFiles` (e.g., because a background process, a file watcher-triggered rewrite, an LFS/clean filter, or any other write to the file races the click of "Commit"), the hunk boundaries and line offsets in the new diff can shift relative to the diff the user saw. Because `isSelected()` operates purely on positional indices with no content anchor, the same indices can now point at different lines/hunks than what the user actually clicked to include. The result: lines the user did not intend to commit get silently staged (or vice versa) — an analogous "missing balance check" to the report, except the corrupted value is the actual staged patch content rather than a token balance.

### Impact Explanation
This falls squarely under "silent corruption of what the user commits or pushes." A user could believe they are committing only reviewed/selected lines from a diff, while the actual commit silently includes different or unreviewed content (potentially attacker-influenced content from a cloned/fetched malicious repository whose tracked files or filters mutate on write). Because there is no error and no confirmation mismatch surfaced to the user, this corruption is silent by design of the current code path.

### Likelihood Explanation
The window for the race is real but narrow: it requires the working-tree file to change between the diff render and the `git apply --cached` staging call, which typically happens on the order of the time between opening Changes and clicking "Commit." This can be triggered by anything that touches the file on disk during that window (editors autosaving, build tools, git hooks/filters reacting to earlier repository operations, or another process in a multi-process workflow). It does not require local/physical access beyond normal use of Desktop on an untrusted or actively-changing repository, and does not require credentials or malware already present — only content/timing that an attacker-controlled repository or associated tooling (e.g. a smudge/clean filter defined in `.gitattributes` of a cloned malicious repo) could influence. Confidence in the exact end-to-end exploitability is moderate — I was not able to execute the code to confirm the precise conditions (e.g., whether `git apply --cached` would fail with a fuzz/context mismatch and be caught by the `git()` wrapper's non-zero exit handling) versus silently succeeding by adjusting hunk offsets, since `apply.ts` uses `--unidiff-zero` and relies on git's own patch-context fuzzing.

### Recommendation
Bind the `DiffSelection` to an identity/hash of the diff it was computed from (or store the selection keyed by hunk content, not raw line index), and have `applyPatchToIndex` verify that the diff it fetches immediately before staging matches the diff the selection was derived from. If they differ, refuse to stage and force the UI to re-present the new diff for re-selection, mirroring (and extending to the commit path) the reconciliation Desktop already partially does when the Changes view diff staleness is detected.

### Proof of Concept
Not independently verifiable via the code index alone (index size limits prevent extracting full runtime harnesses); the mechanism is demonstrated in existing code/tests:
1. Select a subset of lines in a file's diff in the Changes pane (`DiffSelection.withLineSelection`), as exercised in `app/test/unit/git/commit-test.ts` [7](#0-6) .
2. Before Desktop calls `createCommit`, externally modify the same file (e.g. append/rearrange lines) so that `git diff` now produces hunks with different `unifiedDiffStart` offsets than those the selection indices were computed against.
3. Trigger the commit; `applyPatchToIndex` re-fetches the diff (`getWorkingDirectoryDiff`) and calls `formatPatch(file, diff)`, which maps the stale selection indices onto the new hunk layout with no consistency check [8](#0-7) [4](#0-3) .
4. The resulting commit can include/exclude different lines than the user visually selected, with no error surfaced.

### Citations

**File:** app/src/lib/git/apply.ts (L52-81)
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
```

**File:** app/src/lib/git/update-index.ts (L101-112)
```typescript
/**
 * Stage all the given files by either staging the entire path or by applying
 * a patch.
 *
 * Note that prior to stageFiles the index has been completely reset,
 * the job of this function is to set up the index in such a way that it
 * reflects what the user has selected in the app.
 */
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
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

**File:** app/src/lib/patch-formatter.ts (L113-132)
```typescript
/**
 * Creates a GNU unified diff based on the original diff and a number
 * of selected or unselected lines (from file.selection). The patch is
 * formatted with the intention of being used for applying against an index
 * with git apply.
 *
 * Note that the file must have at least one selected addition or deletion,
 * ie it's not supported to use this method as a general purpose diff
 * formatter.
 *
 * @param file  The file that the resulting patch will be applied to.
 *              This is used to determine the from and to paths for the
 *              patch header as well as retrieving the line selection state
 *
 * @param diff  The source diff
 */
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
```

**File:** app/src/lib/patch-formatter.ts (L143-157)
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

**File:** app/test/unit/git/commit-test.ts (L205-236)
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
```
