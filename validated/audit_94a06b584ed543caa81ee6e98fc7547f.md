## Analysis

The ERC-777 report's broken invariant is: **a value (token balance / amount transferred) is measured, an external/attacker-influenced step happens, and a second measurement is trusted to still correspond to the first without re-validation.** The GitHub Desktop analog I found follows the same pattern in the **partial-commit (line-by-line staging) pipeline**, where a line-index-based *selection* computed against one diff snapshot is later applied against a *second, independently re-fetched* diff, with no verification that the two diffs are identical.

### How the flow works

1. When a file is selected in the Changes list, Desktop fetches a diff and caches it in UI state; the user picks individual lines/hunks to include, which is stored purely as line **indices** (`DiffSelection`), not as content: [1](#0-0) 

2. When the user commits, `createCommit` → `stageFiles` splits files into fully-selected and "partial" files: [2](#0-1) 

3. For partially-selected files, `applyPatchToIndex` **re-fetches the diff from disk right before building the patch**, completely independent of whatever diff the selection indices were originally computed against: [3](#0-2) 

4. `formatPatch` then blindly applies the (stale) selection's line indices to this freshly-fetched diff's hunks/lines, assuming they line up 1:1: [4](#0-3) 

There is no check anywhere in this path that the diff used to build the selection is the same diff being patched — analogous to the Venus bug's missing re-validation between the "before" and "after" measurement.

### Title
Stale line-selection applied to a freshly re-fetched diff can silently commit unreviewed content - (File: `app/src/lib/git/apply.ts`)

### Summary
Desktop's partial-commit feature lets a user select individual diff lines to include in a commit. The selection is stored as line **indices**, decoupled from diff content. At commit time, `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` to obtain a *new* diff and reuses the old selection indices against it in `formatPatch`, without verifying the new diff matches the one the user actually reviewed.

### Finding Description
The UI diff shown to the user is computed and cached once [1](#0-0) , and the resulting `DiffSelection` only records *positional* line indices [5](#0-4) , with no content hash or fingerprint tying it to the specific diff/hunks the user actually saw.

When the commit is executed, `applyPatchToIndex` independently re-invokes `getWorkingDirectoryDiff(repository, file)` [6](#0-5) , and `formatPatch` naively walks the *new* diff's hunks, checking `file.selection.isSelected(absoluteIndex)` for each line [7](#0-6) . If the on-disk file content changes between the time the diff was shown to the user and the time the commit is executed (e.g., a clean/smudge/merge driver re-normalizing content, a background editor auto-format/auto-save, or any other process touching the file while the commit dialog is open), the hunk boundaries and line indices in the new diff no longer correspond to the lines the user actually reviewed and checked off. `formatPatch` will still happily build a patch using the old numeric indices against the new hunk structure, and `git apply --cached` will apply it — silently staging/committing content the user never selected or reviewed, with no error surfaced (analogous to how the ERC-777 bug silently corrupted internal accounting using stale/attacker-influenced measurements instead of re-validating against ground truth).

There is no guard comparing the newly fetched diff to the diff the selection was derived from (no hash, hunk-count check, or line-content check), so the "existing guard" (the `DiffType` check ensuring the diff is Text/LargeText) does not protect against a diff whose *shape* has changed but whose *type* is unchanged.

### Impact Explanation
If exploited (e.g., via a repository-configured filter/tooling that non-deterministically alters checked-out file content, or any external write racing with Desktop's commit flow), the practical effect is **silent corruption of what the user commits**: lines the user did not review or intentionally deselect can end up staged and committed instead of, or in addition to, the reviewed lines. This falls squarely into the valid impact category of "silent corruption of what the user commits or pushes," since the user believes they are committing exactly the reviewed diff.

### Likelihood Explanation
This requires the working directory content to change between the initial diff render and the commit-time re-diff — a narrow timing window, but one that is plausible any time content-changing tooling (filters, formatters, LFS smudge, editors with format-on-save, file watchers) is present, none of which require local/physical access, admin rights, or prior malware. The bug is a genuine logic gap (no re-validation) rather than a hypothetical; it exists unconditionally in the partial-commit code path for every partially-selected file.

### Recommendation
Before applying the cached `DiffSelection` in `applyPatchToIndex`/`formatPatch`, re-validate that the freshly fetched diff structurally matches the diff the selection was computed against (e.g., compare hunk headers/line counts or content hashes), and abort/re-prompt the user (similar to the existing "something changed while loading" bail-out pattern already used elsewhere, e.g. in `updateChangesWorkingDirectoryDiff` [8](#0-7) ) instead of silently applying stale indices to new content.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked text file so it has multiple hunks.
2. In the Changes view, select only specific lines/hunks for commit (partial selection), leaving the commit dialog open.
3. Before clicking "Commit", trigger a process that rewrites the file on disk in a way that shifts line numbers/hunks (e.g., an editor's auto-format, or a configured clean/smudge filter re-normalizing the file) without going back through Desktop's diff view.
4. Click "Commit". Observe (via `git show` on the resulting commit) that the staged/committed content corresponds to the old line indices applied against the new hunk layout in `formatPatch`/`applyPatchToIndex`, not the content the user actually reviewed and selected.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3448)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
```

**File:** app/src/lib/stores/app-store.ts (L3453-3464)
```typescript
    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }
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

**File:** app/src/lib/patch-formatter.ts (L129-161)
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
```

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
```
