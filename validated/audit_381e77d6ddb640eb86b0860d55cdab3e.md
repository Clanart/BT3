Based on the evidence gathered, I can construct a plausible analog. The NFTX bug is fundamentally a **TOCTOU / stale-promise problem**: a value (locked token amount) is recorded and later acted upon without re-validating it against the actual current state of the resource it references. The closest structural analog in GitHub Desktop is the **line-selection-to-patch pipeline used for partial commits**, where a user's line selection (recorded as positional indices against one diff) is applied to a diff that is independently re-fetched at commit time, with no check that the two diffs are the same.

### Title
Partial-commit line selection is applied against a freshly re-fetched diff with no staleness check, allowing silent corruption of committed content - ([File: app/src/lib/git/apply.ts])

### Summary
When a user stages only some lines of a file for commit (a "partial commit"), Desktop stores the selection as positional line indices (`absoluteIndex = hunk.unifiedDiffStart + lineIndex`) computed against the diff that was rendered in the UI. At commit time, `applyPatchToIndex` does **not** reuse that diff — it calls `getWorkingDirectoryDiff` again to get a brand-new diff, and then applies the old, positionally-indexed selection to this new diff via `formatPatch`. [1](#0-0) 

There is no check that the newly fetched diff has the same hunk structure/content identity as the diff the user actually reviewed and checked lines in.

### Finding Description
`formatPatch` decides what to include purely by index:
```
const absoluteIndex = hunk.unifiedDiffStart + lineIndex
...
} else if (file.selection.isSelected(absoluteIndex)) {
``` [2](#0-1) 

The selection object carries no content hash or diff identity — it's just a bitset of numeric positions. The app is aware this is fragile: when the working-directory diff is reloaded for display, it explicitly prunes selections down to `selectableLines` because "the diff might have changed dramatically since last we loaded it," but this reconciliation only removes now-invalid indices — it does not detect the case where an index still exists but now maps to different content: [3](#0-2) 

Critically, that reconciliation happens in the UI/state layer (`_selectWorkingDirectoryFiles` / `updateChangesWorkingDirectoryDiff`), but `applyPatchToIndex` runs an entirely separate `getWorkingDirectoryDiff` call at commit time and never consults or revalidates against the diff the UI reconciled: [4](#0-3) [5](#0-4) 

If the tracked file's on-disk content changes between the moment the user reviews/selects lines in the UI and the moment `createCommit` → `stageFiles` → `applyPatchToIndex` actually re-diffs and applies the patch (e.g., due to a clone/checkout-triggered process such as a `post-checkout`/`post-merge` hook, a configured clean/smudge filter, or any other automated writer acting on a just-cloned/fetched repository), the numeric line positions from the stale selection will silently be reinterpreted against the new hunk layout. The result is a patch that includes/excludes lines the user never actually reviewed or intended — this is exactly the "broken invariant" from the report: a promise (the selection) is enforced without re-validating it against the actual current state (the new diff), and the existing safeguards (`selectableLines` pruning) don't close this gap because they operate on a different diff fetch than the one used to build the final patch.

### Impact Explanation
This can silently corrupt what the user commits and, subsequently, what they push — lines the user explicitly excluded from review could be committed, or content could be misattributed to the wrong hunk location, without any error or warning, since `git apply --cached` will happily apply a structurally valid patch built from mismatched indices as long as it parses.

### Likelihood Explanation
This requires a narrow timing window (file changes between UI selection and the final commit-time diff fetch) and a source of unattended writes to the working tree — most plausibly a hook or filter shipped with a cloned/fetched malicious repository, or another concurrent Desktop/background operation. It's a genuine race rather than a deterministic exploit, so likelihood is moderate rather than high, but the guard the code does have (`selectableLines`) demonstrates the developers know this diff-staleness scenario is a real, previously-considered risk — they just didn't close it for the actual commit path.

### Recommendation
Before calling `applyPatchToIndex`/`formatPatch`, re-validate that the diff being staged is the same one the selection was computed against (e.g., by comparing a content hash/identity of the diff, or by re-deriving the selection from line content rather than raw position), and abort/re-prompt the user if the working file has changed since the selection was made.

### Proof of Concept
1. Clone a malicious repository containing a tracked file `foo.txt` and a `post-checkout` hook (or equivalent) that, shortly after checkout, rewrites `foo.txt` inserting/removing lines above the region the user is likely to select.
2. In Desktop, open the diff for `foo.txt`, select a subset of lines to commit (selection stored as indices against the diff visible at that moment).
3. Trigger the hook's rewrite of `foo.txt` in the timing window between selection and clicking "Commit."
4. Call `createCommit` → `stageFiles` → `applyPatchToIndex`, which re-fetches the diff (now different due to the rewrite) and applies the old index-based selection via `formatPatch`, producing a patch whose "selected" lines no longer correspond to what the user checked, `git apply --cached`-ing unreviewed content into the commit with no error surfaced to the user.

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

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
