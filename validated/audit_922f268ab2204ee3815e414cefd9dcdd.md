### Title
Whitespace-hidden diff selection indices are applied to a freshly-fetched, whitespace-included diff at commit time, silently staging unintended lines - (File: `app/src/lib/git/apply.ts`)

### Summary
This is the strongest local analog to the oracle bug: a status/derived-state snapshot (validator status) is trusted for classification even though the underlying data (effective balance / diff shape) can have moved on by the time it is used, causing the wrong content to be attributed (reward vs principal / staged vs not-staged). In Desktop, the equivalent broken invariant is that `WorkingDirectoryFileChange.selection` stores line intent as **absolute line indices into a specific diff rendering**, but the diff used to render that selection to the user (`hideWhitespaceInChangesDiff` may be `true`) can differ in hunk/line layout from the diff that is re-fetched and used to build the actual patch at commit time.

### Finding Description
The user builds up a partial-commit `DiffSelection` against the diff shown in the Changes view, which may be computed with whitespace hidden: [1](#0-0) 
This diff's hunks (and therefore the `unifiedDiffStart`/absolute line indices used by `DiffSelection.isSelected`) are recorded relative to `getWorkingDirectoryDiff(repository, selectedFileBeforeLoad, this.hideWhitespaceInChangesDiff)`.

When the commit is actually built, `stageFiles` routes any file with a `Partial` selection to `applyPatchToIndex`: [2](#0-1) 

`applyPatchToIndex` then re-fetches the diff **without** the `hideWhitespaceInChangesDiff` flag used for display, and immediately formats a patch against it using the file's existing `selection`: [3](#0-2) 

`formatPatch` walks this newly fetched diff and asks `file.selection.isSelected(absoluteIndex)` for each line, where `absoluteIndex` is computed from the *new* diff's hunk offsets: [4](#0-3) 

If hiding whitespace collapses/removes hunks or shifts line counts relative to the full (whitespace-included) diff, the set of absolute indices the user selected while looking at the whitespace-hidden view no longer corresponds to the same physical lines in the diff used to build the patch. `DiffSelectionType` classification (`All`/`None`/`Partial`) and the specific `divergingLines` set are index-based and have no knowledge of *which lines* they were originally computed against — exactly analogous to `ComputeWithdrawals` trusting a validator's `WithdrawalDone` status without checking whether the underlying (effective balance / diff shape) has already changed. There is a partial mitigating reconciliation step (`updateChangesWorkingDirectoryDiff` calls `withSelectableLines` to drop divergent indices that no longer exist), but that reconciliation runs against the *cached* diff for UI redisplay — it is never invoked again inside `applyPatchToIndex`/`stageFiles` right before staging, so the freshly re-fetched (non-whitespace-hidden) diff used for the actual `git apply --cached` is never revalidated against the selection.

### Impact Explanation
This can cause the app to silently stage/commit different hunks/lines than what the user selected in the UI — including lines the user explicitly deselected (e.g. deliberately excluding a change introduced by a malicious/altered file from a fetched branch, or lines from an attacker-controlled file whose whitespace-only diff noise was designed to desynchronize hunk offsets between the "preview" and "apply" diffs). This is a silent corruption of what the user commits/pushes, which the report's "Valid Impact" section explicitly calls out as in-scope (attacker controls a cloned/fetched repository content, resulting in silent corruption of what the user commits).

### Likelihood Explanation
`hideWhitespaceInChangesDiff` is a normal, user-toggleable Desktop setting (not an edge configuration), and partial-selection commits are a core, frequently used feature. An attacker who controls the content of a fetched/checked-out file (e.g. via a branch, PR, or repository the victim clones) can craft whitespace-only edits interleaved with substantive changes specifically to make the whitespace-hidden hunk layout diverge from the full hunk layout, increasing the chance that a partial selection maps onto the wrong lines when staged. No existing guard revalidates the selection against the diff actually used for staging; the only revalidation path (`withSelectableLines` in `updateChangesWorkingDirectoryDiff`) operates on the display diff, not the diff fetched anew in `applyPatchToIndex`.

### Recommendation
- Pass the same `hideWhitespaceInChangesDiff`-independent (canonical) diff used for staging as the one selection line indices are validated against, or always compute/apply selections against a diff fetched without whitespace hiding.
- In `applyPatchToIndex`, after fetching the diff, re-validate `file.selection` against the new diff's actual selectable line set (mirroring the `withSelectableLines` reconciliation already done for the UI) before calling `formatPatch`, and fail/re-prompt if the mapping is ambiguous rather than silently applying stale indices.
- Add regression tests staging a partial selection captured under `hideWhitespaceInChangesDiff = true` and verifying the resulting commit content exactly matches the intended (visually selected) hunks even when whitespace-only hunks exist.

### Proof of Concept
1. Enable "Hide whitespace" in the Changes diff view (`hideWhitespaceInChangesDiff = true`).
2. Check out/pull a file from an attacker-influenced branch containing both a whitespace-only change (e.g., trailing spaces added on several lines) and a substantive one-line change, arranged so that hiding whitespace collapses/removes a hunk that exists in the full diff.
3. In the whitespace-hidden UI diff, select only the substantive change line for staging (leave everything else unselected) — this determines an absolute line index recorded in `DiffSelection`.
4. Commit. `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff(repository, file)` **without** whitespace hiding, producing different hunk offsets than the one the selection indices were derived from.
5. Inspect the resulting commit (`git show`): due to the offset shift, the staged patch includes/excludes different lines than the ones visually selected in step 3 — e.g. the whitespace-only lines get silently committed while the intended substantive line is skipped, or vice versa. [5](#0-4) [1](#0-0)

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/git/update-index.ts (L109-129)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }
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

**File:** app/src/lib/patch-formatter.ts (L143-170)
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
```
