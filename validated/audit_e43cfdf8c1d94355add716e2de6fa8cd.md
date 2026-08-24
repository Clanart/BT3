Confirmed: `DiffSelection` stores selections purely by **positional line index** (`divergingLines: Set<number>`) with no binding to file content/hash [1](#0-0) , and `applyPatchToIndex` re-fetches a *fresh* diff from disk instead of reusing the diff the user actually reviewed when making that selection [2](#0-1) .

### Title
Partial-commit staging re-derives the diff from disk instead of the reviewed diff, letting a fast-mutating tracked file desync line-index selections from actual content - (File: app/src/lib/git/apply.ts)

### Summary
This mirrors the LPP bug class: a piece of previously-validated state (the user's line-selection, made against a specific rendered diff) is later re-applied against independently re-read/re-initialized data (a freshly fetched diff) without verifying the two are still consistent — producing an incorrect result that is committed silently.

### Finding Description
When the user partially stages a file, GitHub Desktop stores the selection as a set of **positional line indices** into whatever diff was rendered at selection time (`DiffSelection.divergingLines`), not tied to file content, hashes, or hunk identity [3](#0-2) .

At commit time, `stageFiles` routes any file with a partial selection to `applyPatchToIndex` [4](#0-3) . Crucially, `applyPatchToIndex` does **not** reuse the diff the UI displayed to the user; it independently re-invokes `getWorkingDirectoryDiff` against the working tree at staging time [5](#0-4) . The resulting (possibly different) hunk layout is then fed straight into `formatPatch`, which walks the new hunk lines and asks `file.selection.isSelected(absoluteIndex)` — using the **stale index positions** from the old, previously-reviewed diff [6](#0-5) .

If the tracked file's on-disk content changes between the moment the user reviews/selects lines in the Changes view and the moment they click Commit (e.g. background formatters, linters with autofix, build/watch tasks, or any other process writing to the file — all of which are routinely present and started by ordinary project tooling checked into a cloned/fetched repository, such as an npm `watch`/`dev` script), the hunk boundaries and line offsets in the fresh diff no longer correspond to the indices captured in `file.selection`. `formatPatch` will then select/deselect the wrong lines relative to what the user actually reviewed and approved, and `git apply --cached` will silently commit that mismatched patch [7](#0-6) . No revalidation step re-checks that the diff used for staging still matches the diff that was displayed when the selection was made — exactly the missing-invariant pattern in the original report (metadata reused/re-derived post-validation without a consistency check).

### Impact Explanation
This causes silent corruption of what the user actually commits: content the user never selected/approved can end up in the commit, or content they intended to include can be silently dropped — without any error, warning, or diff mismatch indication in the UI. This falls squarely under the stated valid-impact category "silent corruption of what the user commits or pushes," and the trigger condition (a tracked file mutating during the review→stage window because of ordinary repository tooling) is attacker-influenceable via a cloned/fetched repository without requiring local/physical access, admin rights, or prior malware.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: it requires a real race between file mutation and the user's click on "Commit," but this is a plausible and repository-triggerable scenario (e.g. checked-in `package.json` scripts commonly recommended to run in a background terminal while editing, format-on-save tooling, or generated files rewritten by a watch process) rather than something requiring unnatural user steps.

### Recommendation
Bind the selection to the diff it was made against (e.g., persist and reuse the exact `ITextDiff`/hunk snapshot alongside `DiffSelection`, or key selections by content hash) and have `applyPatchToIndex` consume that captured diff instead of re-fetching a new one from disk at staging time. If the file has changed since the diff was captured, refuse to stage/commit that file and surface an explicit "changed since you reviewed it" error to the user instead of silently applying a mismatched patch.

### Proof of Concept
Conceptual reproduction (not run, since this is a race-timing issue that needs the real Electron app):
1. Modify `modified-file.md` in a fixture repo, then compute its diff and select specific line indices via `DiffSelection.withRangeSelection(...)`, mirroring the pattern in the existing partial-commit tests [8](#0-7) .
2. Before calling `createCommit`, mutate the file on disk again (simulating a background formatter/watch task) so the hunk boundaries shift.
3. Call `createCommit(repository, 'title', [file])`; observe via `getChangedFiles`/`getTextDiff` on the resulting commit that the staged content does not correspond to the originally selected lines, because `applyPatchToIndex`/`formatPatch` used the newly fetched diff's hunk layout with the old index-based selection [7](#0-6) [6](#0-5) .

### Citations

**File:** app/src/models/diff/diff-selection.ts (L74-84)
```typescript
  /**
   * @param divergingLines Any line numbers where the selection differs from the default state.
   * @param selectableLines Optional set of line numbers which can be selected.
   */
  private constructor(
    private readonly defaultSelectionType:
      | DiffSelectionType.All
      | DiffSelectionType.None,
    private readonly divergingLines: Set<number> | null = null,
    private readonly selectableLines: Set<number> | null = null
  ) {}
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

**File:** app/src/lib/git/apply.ts (L52-83)
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

**File:** app/test/unit/git/commit-test.ts (L211-232)
```typescript
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
```
