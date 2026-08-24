## Title
Position-based (not content-based) line-selection identity allows silent inclusion of attacker-controlled content in a partial commit - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`, `app/src/lib/git/apply.ts`)

### Summary
The original report's broken invariant is: an auto-incrementing index that is trusted as a stable identity is reused/overwritten when the underlying data changes, silently discarding or misapplying data tied to that index. GitHub Desktop has the same class of bug in its partial-commit ("stage selected lines") feature: a line's identity inside a diff is a purely positional `absoluteIndex` (`hunk.unifiedDiffStart + lineIndex`), not a content hash. When the working-directory file changes between the moment the user selects lines to stage and the moment the patch is actually built and applied to the index, Desktop reconciles the old selection against the new diff purely by index arithmetic, without validating that the line at that index still represents the content the user reviewed and selected.

### Finding Description
Selection state is stored as a set of "selected" numeric indices in `DiffSelection`, keyed by `absoluteIndex = hunk.unifiedDiffStart + lineIndex`: [1](#0-0) 

When Desktop reloads a diff for a file that is already selected (e.g. because the working directory changed), it explicitly acknowledges it does not validate that previously selected lines still correspond to the same content — it only intersects the old index set with the new set of "selectable" indices: [2](#0-1) 

The comment in that code is the tell: *"The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."*. This is precisely the same class of flaw as the Deposit contract: identity (index) is preserved and reused across a change of underlying content, with no check that the “thing” at that index is still the same thing.

That stale, index-based selection is then used to build the actual git patch that gets staged and committed. `applyPatchToIndex` re-fetches the diff fresh from disk at commit time and formats a patch purely by testing `file.selection.isSelected(absoluteIndex)` against the new diff's lines: [3](#0-2) [4](#0-3) 

There is no re-validation anywhere in this path that the line text at a given absolute index is the same line the user visually selected in the UI. If the file's line layout shifts (lines inserted/removed) between the render of the diff shown to the user and the `git apply --cached` call, index N will silently refer to different content, and Desktop will stage/commit that different content as if it were the user-approved change.

### Impact Explanation
This is a "silent corruption of what the user commits" bug. A repository whose accompanying tooling the developer runs locally (e.g. `npm run watch`, `npm start`, or any build/lint/codegen script that ships inside the cloned repository and rewrites tracked source files) is fully attacker-controlled content per the accepted threat model ("attacker controls a cloned/fetched repository"). If such a script intentionally times a rewrite of a file's lines while the developer is in Desktop's Changes view doing a partial-line stage/commit, the previously-selected line indices can end up pointing at attacker-inserted lines instead of the originally reviewed ones. Because Desktop only intersects the selection with "still-selectable" indices (not content-equality), the attacker's inserted lines can be silently staged and committed — and, if the developer immediately pushes, silently pushed — without any warning that the selection no longer matches what was visually reviewed.

### Likelihood Explanation
The precondition (a background process from the cloned repo modifying tracked files while the Changes view has a stale diff/selection) is a normal, unprompted developer workflow — running project build/watch tooling while reviewing/staging changes in Desktop is standard practice and requires no unnatural steps from the user. Desktop's own file-system watcher will trigger a diff refresh, and the code path explicitly documents that it does not fully validate selection integrity across refreshes, which makes exploitation a timing problem for the attacker script rather than a logic problem to defeat.

### Recommendation
Anchor line selection to content identity rather than pure position:
- Compute a stable per-line identity (e.g., hash of the line text plus its old/new line numbers, or diff against the previous diff to map old absolute indices to new ones only when content matches) instead of reusing `unifiedDiffStart + lineIndex` verbatim across diff reloads.
- When `updateChangesWorkingDirectoryDiff` detects that the diff changed for a file with an existing partial selection, drop the selection (or the affected hunk's selection) instead of silently remapping it by index, unless line content can be proven identical.
- At the moment of `applyPatchToIndex`/`stageFiles`, re-diff and compare against the diff the selection was made against; if they differ, force the user to re-confirm the selection instead of applying it blindly.

### Proof of Concept
1. Clone a malicious repository containing a normal-looking `package.json` script, e.g. `"watch": "node ./scripts/watch.js"`.
2. `watch.js` monitors `tracked-file.ts` for `fs` write events and, once it detects the file has an in-progress uncommitted change, waits for a short idle period (simulating "user is reviewing diff") and then rewrites a specific line range in `tracked-file.ts` with attacker payload while keeping the total line count and hunk boundaries similar enough to remain "selectable" (`isIncludeableLine()` still true) at the same absolute indices used by the victim's earlier selection.
3. Developer opens Desktop, edits `tracked-file.ts`, selects specific lines to stage via partial-line selection (`DiffSelection.withLineSelection`), leaves `npm run watch` running.
4. Between the diff render and the click on "Commit", `watch.js` fires and Desktop's watcher triggers `updateChangesWorkingDirectoryDiff`, which recomputes `selectableLines` from the new diff and intersects with the old selection purely by index (`app-store.ts:3478-3497`).
5. Developer clicks "Commit"; `applyPatchToIndex` re-fetches the diff and `formatPatch` builds the patch using `file.selection.isSelected(absoluteIndex)` against the new (attacker-modified) diff content (`apply.ts:60-81`, `patch-formatter.ts:143-171`).
6. The resulting commit contains the attacker's rewritten lines at the indices the developer believed corresponded to their own reviewed change, with no diff-mismatch warning shown. [2](#0-1) [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/patch-formatter.ts (L129-171)
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
