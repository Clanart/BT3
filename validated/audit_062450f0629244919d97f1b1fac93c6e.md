## Title
Partial-commit patch application re-reads the working-directory diff instead of using the diff the user actually reviewed, letting stale line-index selections silently stage unintended content - (`app/src/lib/git/apply.ts`)

### Summary
The Symbiotic report describes a system where one function (`coverage`) computes a security-critical metric against the *current* state of collateral, while a second function (`slashTimestamp`) that is supposed to act on the same collateral uses a *different, stale* time boundary. The mismatch lets an attacker present the protocol with one reality (health) while a different reality (what can actually be slashed) is acted upon. The same class of bug — a decision (user's line-by-line diff selection) computed against one snapshot of data being enforced against a **different, independently-fetched snapshot** — exists in GitHub Desktop's partial-commit ("stage selected lines") pipeline.

### Finding Description
When a user stages a subset of lines from a modified file for a commit, the UI records the choice as index-based `DiffSelection` state (`app/src/models/diff/diff-selection.ts`) computed against the `ITextDiff` that was rendered on screen at review time (populated by `updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts:3404-3513`).

At actual staging time, however, `applyPatchToIndex` does **not** reuse that reviewed diff. It independently re-fetches a brand-new diff from disk right before building the patch: [1](#0-0) 

```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

`formatPatch` then walks the *new* diff's hunks and decides which lines to include purely by absolute line index, via `file.selection.isSelected(absoluteIndex)`: [2](#0-1) 

The `DiffSelection` object carries no reference to the diff it was computed against — it is a bare set of line indices (`app/src/models/diff/diff-selection.ts:41-136`). The only place Desktop invalidates/reconciles a selection against a changed diff is the UI-refresh path (`updateChangesWorkingDirectoryDiff`, which recomputes `selectableLines` when the diff shown to the user changes), not the commit/staging path that actually writes to the git index.

This is exactly the invariant break in the report: the value used to make a security/consistency-relevant decision (what the user *saw and approved* to commit) is computed from one time base (the diff at review time), while the value that is *actually acted upon* (what is staged into the index and committed) is computed from a second, later time base (the diff fetched fresh inside `applyPatchToIndex`) — with no reconciliation between the two beyond raw line-index arithmetic.

### Impact Explanation
If the working-directory file content changes between the moment the user reviews/selects lines in the Changes view and the moment `_commitIncludedChanges` triggers `stageFiles` → `applyPatchToIndex` (e.g., a build tool, editor autosave, linter/formatter, git hook, or any external/background process modifying the file — all plausible in a cloned repository whose tooling the user runs), the line indices the user approved no longer correspond to the same content in the freshly fetched diff. `formatPatch` will apply the selection to different lines than the ones the user actually reviewed, silently staging and committing content the user never intended to include (or excluding a change they meant to keep), directly matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The window is the interval between diff render and clicking "Commit"; anything writing to the working tree file in that window (formatters run on save, git hooks, IDEs, other processes) triggers the mismatch without any unusual user action, unprompted steps, or elevated privileges. No existing guard compares diff identity/hash between the reviewed diff and the diff used at apply time — the only reconciliation logic (`updateChangesWorkingDirectoryDiff`) operates on the UI-facing selection, not the one consumed by `applyPatchToIndex`.

### Recommendation
- Short term: Pass the exact `ITextDiff` the user reviewed/selected against into `applyPatchToIndex` instead of re-fetching a new diff at apply time, or verify the freshly-fetched diff is unchanged (e.g., by hash) before trusting the pre-existing line-index selection; if it has changed, abort and force the user to re-review.
- Long term: Make `DiffSelection` diff-content-aware (e.g., tie divergent line state to line content/hash rather than raw index) so a stale selection cannot be silently reapplied to different content.

### Proof of Concept
1. Modify a tracked file and open it in Desktop's Changes view; deselect some lines so only specific lines will be committed.
2. While the commit box is focused (before clicking Commit), have an external process (editor autosave, formatter-on-save, git hook, or any script) rewrite the file so lines shift (e.g., insert lines above the hunk).
3. Click "Commit" — `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) fetches a new diff and applies the old `DiffSelection` indices via `formatPatch` (`app/src/lib/patch-formatter.ts:129-171`), producing a patch that includes/excludes different lines than what was visually reviewed and approved, which is then applied to the index and committed without any warning.

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
