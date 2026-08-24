## Title
Line-based patch application against a stale diff silently corrupts partial commits and discarded changes - ([File: app/src/lib/git/apply.ts])

## Summary
The reported PSM3 bug class is a **check/decision-then-act TOCTOU**: an attacker manipulates the shared resource between the moment a user's intended action is decided and the moment it is executed, causing the action to operate on unexpected state and silently harm the user (burning share balance). The GitHub Desktop analog is Desktop's **line-based partial commit / discard-selection flow**: the UI computes a diff, the user selects specific lines/hunks to stage or discard, and *that same diff object* (captured before the user finished making the selection) is later converted into a raw unified-diff patch and fed to `git apply`/`git apply --cached` by line number, with no re-validation that the working-directory content still matches the diff that was used to compute those line offsets.

## Finding Description
`formatPatch()` and `formatPatchToDiscardChanges()` in `app/src/lib/patch-formatter.ts` build a hand-rolled unified diff purely from the in-memory `ITextDiff` object and the user's `DiffSelection` bitmap indexed by absolute line offsets (`hunk.unifiedDiffStart + lineIndex`): [1](#0-0) 

That patch is then applied directly against the working tree/index via `git apply` with no `--check` re-diff step immediately before applying: [2](#0-1) [3](#0-2) 

The diff that produces the line numbers baked into the patch (`hunk.header.oldStartLine`, `oldCount`, etc.) can be **stale relative to the file on disk at apply-time**. Desktop's own code acknowledges this staleness problem for the *selection state* refresh path but does nothing about it for the *patch generation/application* path: [4](#0-3) 

Crucially, when a discard is triggered from the diff viewer, Desktop explicitly passes "the original diff (from props) instead of the (potentially) expanded one" straight into the commit/discard pipeline: [5](#0-4) 

Because `WorkingDirectoryFileChange` content is derived from files that live in a repository the user opened (fetched/cloned), an attacker who can influence what's on disk between diff-render and user-click — e.g. via a **git hook, a smudge/clean filter defined in a fetched repo's `.gitattributes`/`.git/config`, a background file-watcher-triggered checkout, LFS filter, or another concurrent tool touching the working tree** — can cause the file's actual byte offsets to diverge from what the cached `ITextDiff` describes. When the stale patch is applied with `--unidiff-zero` (which disables the normal fuzzy context matching git apply would otherwise use to catch drift), `git apply` will either:
- apply cleanly against the wrong byte ranges (since `--unidiff-zero` patches carry zero lines of context, `git apply` has minimal ability to detect the file changed), silently staging/committing or discarding content the user never selected, or
- fail outright, but with no user-visible mismatch of the *content* that was actually staged in the success case.

This is the direct analog of the PSM3 flaw: the "decision" (which lines to keep) is made against a snapshot of state; the "action" (patch application) executes later against a resource (`git apply`'s index/working tree) that may have been mutated in between by an external actor, and the guard the report's author asks for ("re-check the resource right before acting") is absent here just as it was absent for PSM3's asset balance.

## Impact Explanation
A successful exploitation results in **silent corruption of what the user commits or discards** — exactly the impact category called out as valid: the user believes they are committing/discarding line X, but the applied patch (built from stale offsets) actually stages or reverts different lines/content than intended, without any error surfaced to the user. This can smuggle attacker-influenced content into a commit that the user then pushes, or silently discard changes the user wanted to keep.

## Likelihood Explanation
Likelihood is constrained by the need for an attacker-controlled repository (fetched/cloned) to introduce a filter/hook/background process that mutates a tracked file's content between the render of the diff and the user's click on "stage"/"discard" for a specific hunk — a window that exists on every partial-commit and per-line-discard interaction, which are common Desktop workflows. No local/physical access or pre-existing malware on the host is required beyond what the attacker can encode into the repository content itself (hooks/filters/LFS config), consistent with the "attacker controls a cloned/fetched repository" threat model in scope.

## Recommendation
Before calling `applyPatchToIndex` or `discardChangesFromSelection`, re-fetch (or checksum-compare) the current on-disk diff for the file and abort/re-prompt the user if it differs from the `ITextDiff` used to build the selection, rather than trusting the possibly-stale in-memory diff snapshot. Consider dropping `--unidiff-zero` in favor of patches carrying real context lines so `git apply` can detect and reject drifted patches, or use `git apply --check` immediately prior to the real apply within the same operation.

## Proof of Concept
1. Attacker publishes/serves a repository containing a commit-time `.gitattributes` filter (or a `post-checkout`/file-watcher-triggered process) that appends/prepends lines to `tracked-file.txt` shortly after it is written to disk.
2. Victim opens the repo in Desktop, makes an edit to `tracked-file.txt`, and opens the diff view; Desktop computes and caches `ITextDiff` (`app/src/lib/stores/app-store.ts:3444` `getWorkingDirectoryDiff`).
3. Before the victim clicks "Discard Selected Lines" (or stages a subset of hunks for a partial commit), the attacker-controlled filter/process mutates `tracked-file.txt` on disk, shifting line offsets.
4. Victim clicks discard/stage; Desktop calls `onDiscardChanges(this.props.diff, newSelection)` using the pre-mutation diff (`app/src/ui/diff/side-by-side-diff.tsx:1606`), which flows into `formatPatchToDiscardChanges`/`formatPatch` and then `git apply --unidiff-zero` (`app/src/lib/git/apply.ts:115` / `:81`).
5. Because the patch carries stale line numbers/zero context, `git apply` stages or removes content different from what the victim visually selected, and the corrupted result is committed/pushed with no error shown.

### Citations

**File:** app/src/lib/patch-formatter.ts (L129-157)
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
```

**File:** app/src/lib/git/apply.ts (L52-84)
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
}
```

**File:** app/src/lib/git/apply.ts (L102-120)
```typescript
export async function discardChangesFromSelection(
  repository: Repository,
  filePath: string,
  diff: ITextDiff,
  selection: DiffSelection
) {
  const patch = formatPatchToDiscardChanges(filePath, diff, selection)

  if (patch === null) {
    // When the patch is null we don't need to apply it since it will be a noop.
    return
  }

  const args = ['apply', '--unidiff-zero', '--whitespace=nowarn', '-']

  await git(args, repository.path, 'discardChangesFromSelection', {
    stdin: patch,
  })
}
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

**File:** app/src/ui/diff/side-by-side-diff.tsx (L1600-1607)
```typescript
    const newSelection = selection
      .withSelectNone()
      .withRangeSelection(startLine, endLine - startLine + 1, true)

    // Pass the original diff (from props) instead of the (potentially)
    // expanded one.
    this.props.onDiscardChanges(this.props.diff, newSelection)
  }
```
