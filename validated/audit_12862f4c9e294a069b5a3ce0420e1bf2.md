## Title
Partial-commit line selection is applied against a freshly re-fetched diff, not the diff the user actually reviewed — potential silent corruption of committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
The reported contract bug is a classic "decision made from a stale state read" pattern: `ArmadaWindDown::triggerWindDown` reads `recognizedRevenueUsd` without first syncing it, so the wind-down decision can be based on data that no longer reflects reality. The Desktop analog of this bug class is a **decision (which lines to include in a commit/patch) made against a diff object that is not guaranteed to correspond to the actual on-disk file content at apply time**, because the line-selection indices are computed against one version of the diff while the patch is generated from an independently, freshly-fetched diff.

### Finding Description
When a user stages a partial selection of lines for a file, Desktop stores that selection as a set of line indices on `WorkingDirectoryFileChange.selection`, computed against the diff that was rendered in the UI (`updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts`, lines 3404-3513). That code path has explicit staleness guards for the *UI* diff (it discards diffs whose selection state no longer matches).

However, when the commit is actually created, `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:12-84`) does **not** reuse the diff that produced the selection. Instead it independently re-fetches the diff at commit time: [1](#0-0) 

```
const applyArgs: string[] = [ ... ]
const diff = await getWorkingDirectoryDiff(repository, file)
```

The `file.selection.isSelected(absoluteIndex)` calls in `formatPatch` (`app/src/lib/patch-formatter.ts:143-206`) then apply the *old* selection's line indices to *this newly-fetched* diff: [2](#0-1) 

If the working-directory file content changes between the moment the user made their line selection in the UI and the moment `_commitIncludedChanges` (`app/src/lib/stores/app-store.ts:3681-3760`) actually runs `createCommit` → `stageFiles` → `applyPatchToIndex`, the absolute line indices from the old selection no longer correspond to the same logical lines in the new diff. `git apply --cached --unidiff-zero` will either fail, or — worse — apply against a hunk whose context has shifted, silently including/excluding different lines than what the user actually reviewed and clicked on.

This is exactly the "stale figure used for an important decision" pattern from the report: the *decision* (which lines go into the commit) is made using data (`file.selection`) that was validated against one diff snapshot, but the operation that turns it into git state (`applyPatchToIndex`) resolves that data against a different, later snapshot without re-validating.

### Impact Explanation
An attacker who can cause file content to change between the user's diff review and the moment they click "Commit" (e.g., a build tool, IDE auto-formatter, a malicious pre-existing background process modifying files, or timing exploitation via a slow/large diff render) can cause Desktop to silently commit different code than what the user visually reviewed and explicitly selected. This is a "silent corruption of what the user commits" scenario — the qualifying impact category in this task's Valid Impact section — because the user believes they are committing lines A, B, C but the tool may commit lines that no longer match that selection, with no error or confirmation shown to the user.

### Likelihood Explanation
The `--unidiff-zero` mode makes `git apply` very strict about exact line offsets, so many changes would cause an outright apply failure rather than silent corruption. However, whitespace-only shifts, unrelated edits elsewhere in the file that don't change the total line count, or edits that shift by an amount that coincidentally realigns hunk boundaries, could allow the patch to apply "successfully" against unintended content. This requires a specific timing window and file-content precondition, making it a medium/lower likelihood but a plausible correctness bug rather than a hypothetical one, since the code paths are demonstrably decoupled (diff regenerated fresh in `apply.ts` vs. diff cached/validated in `app-store.ts`).

### Recommendation
- Pass the exact diff object that was used to compute `file.selection` through to `applyPatchToIndex`/`stageFiles`, instead of re-fetching a new diff at apply time.
- Alternatively, re-fetch the diff immediately before staging and re-validate/re-map the user's selection against it (similar to the existing `selectableLines` reconciliation logic already present in `updateChangesWorkingDirectoryDiff`), aborting or re-prompting the user if the file has changed since the selection was made.
- Add a check comparing the file's mtime/hash between selection time and commit time; if it changed, refresh the diff and ask the user to re-confirm their selection before committing.

### Proof of Concept
Exact reproduction was not verified against a live build (no filesystem/terminal access in this environment), so this is presented as a code-path analysis rather than a confirmed exploit trace:
1. Open a file with a multi-hunk diff in Desktop's Changes view; partially select some added/deleted lines (sets `file.selection` bit-per-line against diff snapshot D1).
2. Before clicking "Commit", have an external process (or Desktop's own auto-refresh/hooks) modify the file in a way that shifts line numbers but keeps the file open in the "modified" state (e.g., add/remove a blank line elsewhere).
3. Click "Commit". `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` fetches a new diff D2 via `getWorkingDirectoryDiff` (`app/src/lib/git/apply.ts:60`), and `formatPatch` applies the D1-derived `file.selection` indices against D2's hunks.
4. If the resulting patch still applies (no failure raised, since `--unidiff-zero` can succeed under certain offset-preserving edits), the resulting commit contains different line content than what the user visually reviewed and clicked when making the selection.

Because I could not execute Desktop locally to confirm whether `git apply --unidiff-zero` fails or silently mis-applies under the exact conditions in step 2-3, I recommend a background engineering session to reproduce this with actual file-edit timing and confirm whether corruption or a hard failure occurs.

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
