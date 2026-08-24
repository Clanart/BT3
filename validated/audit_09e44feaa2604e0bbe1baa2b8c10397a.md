### Title
Silent Commit Corruption via TOCTOU Between Diff Rendering and Patch Generation in `applyPatchToIndex` - (File: `app/src/lib/git/apply.ts`)

### Summary
The reported SP1Blobstream bug is a case of using two logically-linked values (`trusted_block_height`/`target_block_height` and the actual header heights embedded in the light blocks) without cross-validating one against the other, letting an attacker desynchronize them and get incorrect state committed. GitHub Desktop has a structurally analogous pattern in its partial-commit ("stage selected lines") pipeline: the line-selection bitmap (`DiffSelection`) is computed by the user against one snapshot of a diff, but the patch that is actually applied to the index is built from a **second, independently re-fetched diff** with no check that the two are the same.

### Finding Description
When a user stages only some lines/hunks of a file, the UI records the selection as a set of *positional* line indices in `DiffSelection`, computed against the diff object rendered in `updateChangesWorkingDirectoryDiff` (`app/src/lib/stores/app-store.ts:3404-3513`). That diff is fetched once and cached in `changesState.selection.diff`.

When the user actually commits, staging goes through `stageFiles` (`app/src/lib/git/update-index.ts:109-168`), which for any partially-selected file calls `applyPatchToIndex`: [1](#0-0) 

Critically, `applyPatchToIndex` does **not** reuse the diff the user reviewed/selected against. It re-invokes `getWorkingDirectoryDiff(repository, file)` fresh, right before building the patch, and then blindly feeds `file.selection` (the positional bitmap captured from the *old* diff) into `formatPatch`: [2](#0-1) 

`formatPatch` resolves each diff line purely by `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and calls `file.selection.isSelected(absoluteIndex)` — a pure index lookup with no comparison against the line's actual text/content from the previously-reviewed diff: [3](#0-2) 

There is no OID/content/hash equivalence check anywhere in `applyPatchToIndex`, `stageFiles`, or `formatPatch` to confirm that the fresh diff's hunk/line layout still matches the diff on which the user's selection indices were based. The app already acknowledges line-index instability elsewhere — the comment in `updateChangesWorkingDirectoryDiff` states: "The diff might have changed dramatically since last we loaded it... for now we'll settle on just updating the selectable lines" (`app/src/lib/stores/app-store.ts:3480-3493`) — but that reconciliation only happens for the *UI's live diff view*, not for the diff actually used to build the applied patch in `apply.ts`.

If the on-disk file content changes between the time the diff was rendered (and the user selected specific lines) and the time `stageFiles`/`applyPatchToIndex` runs (e.g., because of a background build/watch/format tool bundled in the repository — a plausible "attacker-controlled repository" scenario since Desktop makes no attempt to freeze or verify the working tree during this window), the hunks and their `unifiedDiffStart` offsets can shift. The selection bitmap, keyed only by numeric position, will then be applied against a hunk structure it was never validated against, so `formatPatch` can silently select an unrelated line (add/drop content the user never intended) with no error surfaced to the user.

### Impact Explanation
This directly matches the "silent corruption of what the user commits or pushes" impact category: the generated patch, applied via `git apply --cached`, can stage content the user did not actually select, and the commit created from it silently differs from the user's intent. There is no error, warning, or diff-review step before the mismatched patch is applied to the index.

### Likelihood Explanation
Exploitation requires the working tree to change between diff-render and stage/commit time. This is realistic in projects (which an attacker fully controls, e.g., a project cloned by the victim) that ship auto-run tooling commonly present in developer workflows (file watchers, formatters-on-save, build scripts, linters with `--fix` triggered by editor integrations) — none of which require local malware, admin rights, or unnatural user steps; they are normal artifacts of "having this repository open and doing routine development" while making a partial commit. The window is the ordinary time it takes a user to review a diff and click "Commit". No existing guard (diff hash check, content verification, or re-validation of selection indices at staging time) closes this window in `apply.ts`.

### Recommendation
Before building/applying a partial-commit patch in `applyPatchToIndex`, verify that the diff used to build the patch is the same one the selection was made against — e.g., by comparing the diff text/hash (or per-line content, not just positional index) captured at selection time to the freshly fetched diff, and refuse/re-derive the selection (with a user-visible warning) if they differ, rather than silently applying a position-based selection to different diff content.

### Proof of Concept
Conceptual reproduction (concurrency-dependent, not a guaranteed one-shot PoC):
1. Attacker publishes a repository containing a file with an npm/task-runner "watch" script (or similar auto-run tooling) that rewrites the file's content shortly after any file save/checkout.
2. Victim clones the repo in GitHub Desktop, edits the file, and reviews the working-directory diff, selecting only specific lines/hunks to commit (`DiffSelection` computed against `diff` D1).
3. Before the victim clicks "Commit", the repository's background tooling modifies the file, shifting hunk boundaries.
4. Desktop's `stageFiles` → `applyPatchToIndex` re-fetches a new diff D2 (`app/src/lib/git/apply.ts:60`) and applies the *old* positional selection to D2 via `formatPatch` (`app/src/lib/patch-formatter.ts:143-171`), with no check that D1 == D2.
5. The resulting commit contains different content than what the victim visually selected, with no error shown.

### Citations

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
