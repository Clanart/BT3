### Title
Partial-commit line selection uses stale absolute-line indices against a freshly re-fetched diff, silently committing unreviewed content - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`, `app/src/lib/stores/app-store.ts`)

### Summary
This is the closest analog to the reported bug's broken invariant: *a value ("did anything actually happen since I last recorded state") is treated as still valid/consistent even though the underlying data it was computed against has since changed.* In BlueberryStaking the `lastUpdateTime`/`rewardPerTokenStored` bookkeeping is advanced even when the quantity it depends on (`totalSupply`) is stale/zero, silently discarding value. In GitHub Desktop, the analogous broken invariant is that a user's line-level commit `DiffSelection` (a set of absolute line indices chosen against a specific diff snapshot) is applied to a **different, independently re-fetched diff** at staging time, without verifying the two diffs are the same shape.

### Finding Description
`DiffSelection` records which lines a user wants to commit purely as a `Set<number>` of absolute indices (`app/src/models/diff/diff-selection.ts`, `isSelected`/`withRangeSelection`), with no binding to the actual line content, hunk boundaries, or a diff/content hash. [1](#0-0) 

The UI computes and stores this selection against whatever diff was rendered at selection time. When the diff is refreshed in the background, `updateChangesWorkingDirectoryDiff` only *narrows* the stale selection by intersecting it with the new set of selectable line indices — it does not verify that index `N` still refers to the same textual line, nor does it invalidate the selection when hunks have shifted: [2](#0-1) 
The comment even acknowledges the gap: *"The diff might have changed dramatically since last we loaded it... for now we'll settle on just updating the selectable lines."* [3](#0-2) 

At commit time, `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff **again**, independently, immediately before building the patch: [4](#0-3) 
`formatPatch` then walks this brand-new diff and calls `file.selection.isSelected(absoluteIndex)` for each line, using the stale index set from step one against the hunk/line layout of step three: [5](#0-4) 

If the working-directory file content changes between the diff the user visually reviewed/selected lines against and the diff used to build the actual `git apply --cached` patch, the hunk line offsets shift. The same absolute index can now correspond to a different, unrelated line (e.g., an unrelated addition inserted earlier in the file, or a line whose text has changed). Because `isSelected` only knows "index N is/isn't selected" and not "this specific text is/isn't selected," the resulting patch can include lines the user never saw or intended to commit, or exclude lines they did select — all with `git apply` succeeding silently. Nothing in `applyPatchToIndex`, `stageFiles`, or `createCommit` compares the diff generation/hash to detect this drift; there is no re-validation step analogous to fixing `lastUpdateTime` only when `totalSupply != 0`.

An attacker who can cause the working tree to change between the moment the user reviews/selects diff lines and the moment Desktop commits (e.g., a malicious repo shipping a `postCheckout`/editor auto-format hook, a file-watching build tool triggered by opening the repo, or a background task the repo's tooling spawns that rewrites tracked files) can shift line offsets in a targeted file so that the user's selected "safe" lines end up mapping to attacker-inserted lines instead, causing those lines to be silently staged and committed (and potentially pushed) without the user's knowledge.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the committed content can diverge from what the user visually reviewed and explicitly selected via line-level checkboxes, without any error, warning, or diff re-confirmation. Because Desktop's partial-commit feature is specifically marketed as letting users review and select exact lines before committing, this defeats the security/trust guarantee of that review step and could be leveraged to sneak attacker-controlled content into a commit that a careful user believed they had excluded.

### Likelihood Explanation
Exploitation requires a way to modify the working-directory file content in the small window between the diff render (selection state) and the second diff fetch in `applyPatchToIndex`. This is plausible via legitimate developer tooling already present in many repos (format-on-save extensions, file watchers, build scripts, git hooks) that a malicious repository could bundle to intentionally win this race — without needing local/physical access, admin rights, or pre-existing malware, satisfying the "attacker controls a cloned/fetched repository" condition. The race window is real but narrow (bounded by diff-fetch latency), so likelihood is moderate rather than trivial; it is not guaranteed on every commit, similar to how the original bug guarantees loss "for every ibToken" but with a magnitude dependent on timing.

### Recommendation
Bind `DiffSelection` to the diff it was computed from (e.g., store a content hash or hunk signature alongside the selection) and have `applyPatchToIndex`/`formatPatch` verify that the diff used to build the patch matches the diff the selection was derived from before applying it. If they differ, refuse to build the partial patch and force the UI to re-present the new diff for re-selection, rather than silently applying stale indices to new content.

### Proof of Concept
Conceptual reproduction (exact automation would need to win a timing race, so this describes the mechanism rather than a guaranteed script):
1. Open a repository containing a modified file with multiple hunks; select only specific "safe" lines for a partial commit in Desktop's Changes view (`DiffSelection` now holds absolute indices for that diff snapshot).
2. Before pressing Commit, have a background process (e.g., a file watcher/formatter triggered by opening the repo, or a repo-provided pre-commit script running earlier in the pipeline) insert or remove lines earlier in the same file, shifting subsequent hunk offsets.
3. Click Commit. `updateChangesWorkingDirectoryDiff`'s selectable-line narrowing does not detect the semantic shift (it only drops now-nonexistent indices), and `applyPatchToIndex` re-fetches the diff fresh, then `formatPatch` applies the old absolute indices to the new hunk layout.
4. Inspect the resulting commit: it contains different line content at the previously "selected" indices than what the user visually approved, with no error or warning shown, demonstrating silent corruption of the committed diff. [6](#0-5) [4](#0-3)

### Citations

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

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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
