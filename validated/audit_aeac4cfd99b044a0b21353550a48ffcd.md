Based on the investigation, I found a plausible Desktop analog: the same class of bug — an operation trusting a **stale internal accounting/selection state** instead of re-validating it against the authoritative source at the moment it is consumed — exists in how GitHub Desktop turns a user's line-level commit selection into an actual `git apply --cached` patch.

### Title
Stale line-selection indices can cause GitHub Desktop to silently stage/commit different lines than the user selected - (File: `app/src/lib/patch-formatter.ts`, `app/src/lib/git/apply.ts`)

### Summary
Desktop lets a user partially stage a file by selecting individual diff lines. The selection is stored as a set of *absolute line indices* into a previously-rendered diff (`DiffSelection`), not as a reference to the actual line content. When the user commits, `applyPatchToIndex` re-fetches a **fresh** diff from disk and replays the old, index-based `file.selection` against this new diff's hunks to build the patch that is applied to the index. [1](#0-0) [2](#0-1) 

If the on-disk diff at commit time (hunk boundaries/line ordering) differs even slightly from the diff that was displayed to the user when they made their selection — for example because a `.gitattributes` clean/smudge filter, `ident`, `autocrlf`, or an external diff/merge driver shipped in a cloned/fetched malicious repository produces non-deterministic or environment-dependent output between two invocations of `git diff` — the absolute indices in `file.selection` no longer correspond to the same logical lines. `formatPatch` will happily build a syntactically valid patch using those indices against the *wrong* lines of the new hunk structure, and `git apply --cached --unidiff-zero` applies it without error.

### Finding Description
`DiffSelection.isSelected(absoluteIndex)` is consulted purely by numeric position: [3](#0-2) 

The "broken invariant" mirrors the C4 report exactly: a value meant to represent the user's *intent* (which lines to include — analogous to `realCommitment`) is decoupled from the ground truth used at execution time (the newly fetched diff — analogous to the actual token balance used by `purchaseAndBurn`). The existing guard in `app-store.ts` only refreshes `selectableLines` for the file that is *currently displayed* in the Changes view when its diff changes: [4](#0-3) 

This reconciliation is heuristic (it marks lines unselectable if they're no longer "includeable"), is only triggered for the actively-viewed file, and does not re-run at the moment `applyPatchToIndex` performs its own independent `getWorkingDirectoryDiff` call immediately before applying the patch. There is no check that the diff used to build the patch is identical to the diff the selection was computed against.

### Impact Explanation
A malicious or compromised repository (attacker controls `.gitattributes`, clean/smudge filters, `ident` keywords, or relies on `autocrlf`/whitespace normalization differences) can cause the diff Desktop displays to a user to differ from the diff Desktop re-fetches internally at commit time. Since staging is driven purely by positional indices rather than content-addressed line identity, this can result in the user's commit silently including lines they explicitly deselected, or excluding lines they intended to commit — i.e., silent corruption of what the user commits and subsequently pushes to a shared remote. This is worse than a crash because it happens without any error or warning; `git apply` succeeds.

### Likelihood Explanation
This requires no unusual user action beyond the normal workflow of partially staging a file (selecting/deselecting individual lines) in a repository whose content can change git's diff output between two `git diff` invocations (filters, autocrlf, external tools) — the attacker only needs to control the cloned/fetched repository content, which fits the required attacker model. It does not require local/physical access, admin rights, or pre-existing malware.

### Recommendation
Do not encode partial-commit selections as raw positional indices resolved against a diff fetched independently at commit time. Instead, either (a) reuse the exact diff object the user selection was computed against when building the patch in `applyPatchToIndex`, refusing to commit and forcing a re-diff/re-selection if the file has changed on disk since that diff was generated, or (b) anchor selections to line content/hash rather than absolute position so a shifted hunk structure cannot silently remap the user's selection onto unrelated lines.

### Proof of Concept
1. Attacker publishes a repository containing a tracked file plus a `.gitattributes` entry configuring a clean/smudge filter (or relies on `core.autocrlf`) whose output for that file is not stable across two `git diff` invocations run moments apart (e.g., filter embeds environment/timestamp data, or line-ending normalization state flips due to a config change made through a `post-checkout`/`post-merge` hook shipped in the repo).
2. Victim clones the repo in Desktop, modifies the file, and opens the Changes view; Desktop calls `getWorkingDirectoryDiff` and renders hunks A.
3. Victim deselects certain lines shown in hunks A (`DiffSelection` records absolute indices for what should be excluded) and clicks Commit.
4. `applyPatchToIndex` re-calls `getWorkingDirectoryDiff` (`app/src/lib/git/apply.ts:60`), and — because the filter/normalization state has changed since step 2 — receives hunks B with a different line count/ordering.
5. `formatPatch` (`app/src/lib/patch-formatter.ts:143-171`) applies the victim's index-based selection to hunks B, producing a patch that includes/excludes different lines than what the victim reviewed and intended.
6. `git apply --cached` succeeds silently; the resulting commit (and any subsequent push) contains content the user never approved.

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
