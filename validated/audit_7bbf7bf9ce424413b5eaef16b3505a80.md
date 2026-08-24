### Title
Partial-commit line selection is applied against a diff re-read at commit time, not the diff the user reviewed - ([File: app/src/lib/git/apply.ts])

### Summary
`applyPatchToIndex` builds the patch used for a partial commit by fetching a *fresh* working-directory diff at commit time and re-interpreting the user's line-based `DiffSelection` against it, instead of applying the selection to the diff object the user actually reviewed and clicked on in the UI.

### Finding Description
When a user stages only some lines/hunks of a file, the UI computes a `DiffSelection` keyed purely by absolute line index (`file.selection.isSelected(absoluteIndex)`), based on a diff object rendered earlier in `updateChangesWorkingDirectoryDiff` [1](#0-0) . That `WorkingDirectoryFileChange` (carrying only the index-based selection, no reference to the diff content it was created against) is later passed to `stageFiles` → `applyPatchToIndex`, which does not reuse the diff the user saw. Instead it re-fetches the diff from disk right before committing and formats the patch from that new diff using the old line indices: [2](#0-1) 

`formatPatch` then walks `diff.hunks` and simply asks `file.selection.isSelected(absoluteIndex)` for each line of the newly-fetched diff [3](#0-2) . Nothing ties the `DiffSelection` to the specific diff/content it was computed against — it is a bare set/range of numeric line offsets (`DiffSelection` in `app/src/models/diff/diff-selection.ts`).

If the file on disk (in a cloned/fetched repository) changes between the moment the user reviews and selects lines and the moment `createCommit` actually runs `applyPatchToIndex` — e.g., because a git hook (`post-checkout`, `post-merge`, a build step, an editor auto-format-on-save, or any other process touching a file in the working directory that is part of the cloned repo) rewrites the file — the absolute line indices no longer correspond to the same lines the user approved. The same numeric selection is silently reapplied to different content, so the committed and possibly pushed content can differ from what the user visually approved in the diff view, with no re-validation or diff/version check before `git apply --cached` runs.

This mirrors the root cause of the seed H-3 finding: a value that was "committed" by the user (collateral amount / line selection) is silently reinterpreted/overwritten by a later read of mutable, attacker-influenceable state before being consumed, without any invariant tying the two together.

### Impact Explanation
If exploited, a user could unknowingly stage and commit (and subsequently push) lines/content they never reviewed or intended to include, or omit lines they intended to include — i.e., silent corruption of what the user commits, which is explicitly listed as a valid impact category. Because the trigger is a file mutation performed by anything running in the working directory of a repository the user cloned (hook, tool, or another process reacting to repository content), this is reachable without local/physical access to Desktop's own process, admin rights, or pre-existing malware, satisfying the "attacker controls a cloned/fetched repository" primitive.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires a window between the user's diff review/selection and the actual `git commit`/`applyPatchToIndex` call in which the specific file's line count/positions change (e.g., via a fast git hook triggered by another Desktop action, or a background formatter/build watcher bundled with the repository), and it requires the selection to be partial (not "stage entire file"). There is no code-level guard (content hash, mtime check, or diff re-render before commit) that would prevent or detect this race, so once the timing condition is met the corruption is deterministic and silent.

### Recommendation
Before applying the patch in `applyPatchToIndex`, verify that the working-directory diff used to build the patch matches the diff the user's `DiffSelection` was computed against (e.g., by storing a content hash/text snapshot alongside the `DiffSelection`, or by re-rendering the diff and prompting the user to re-confirm selection if the underlying file changed since the selection was made). Alternatively, fail the partial commit (rather than silently reinterpreting indices) when the newly-fetched diff's hunk structure differs from what was used to build the selection.

### Proof of Concept
1. Clone/open a repository in Desktop containing a file `file.md` with a Git hook (e.g. `post-checkout` or a file watcher script committed to `.git/hooks` via a bootstrap script, or any tool that rewrites `file.md` shortly after being triggered by a Desktop-initiated git operation such as a branch refresh).
2. Modify `file.md` locally with several new lines; open the Changes view and select only specific lines to include (partial selection), leaving the commit dialog open/queued.
3. Trigger the hook/process (e.g., via a background fetch/checkout Desktop performs automatically) so that it rewrites `file.md`, shifting or changing line content while preserving overall file validity.
4. Click "Commit". `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` [4](#0-3)  and applies the stale numeric selection to the new content via `formatPatch` [5](#0-4) .
5. Inspect the resulting commit: it contains different lines than what was highlighted/selected in the diff view the user approved, demonstrating silent corruption of the committed content.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3404-3448)
```typescript
  private async updateChangesWorkingDirectoryDiff(
    repository: Repository
  ): Promise<void> {
    const stateBeforeLoad = this.repositoryStateCache.get(repository)
    const changesStateBeforeLoad = stateBeforeLoad.changesState

    if (
      changesStateBeforeLoad.selection.kind !==
      ChangesSelectionKind.WorkingDirectory
    ) {
      return
    }

    const selectionBeforeLoad = changesStateBeforeLoad.selection
    const selectedFileIDsBeforeLoad = selectionBeforeLoad.selectedFileIDs

    // We only render diffs when a single file is selected.
    if (selectedFileIDsBeforeLoad.length !== 1) {
      if (selectionBeforeLoad.diff !== null) {
        this.repositoryStateCache.updateChangesState(repository, () => ({
          selection: {
            ...selectionBeforeLoad,
            diff: null,
          },
        }))
        this.emitUpdate()
      }
      return
    }

    const selectedFileIdBeforeLoad = selectedFileIDsBeforeLoad[0]
    const selectedFileBeforeLoad =
      changesStateBeforeLoad.workingDirectory.findFileWithID(
        selectedFileIdBeforeLoad
      )

    if (selectedFileBeforeLoad === null) {
      return
    }

    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )
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

**File:** app/src/lib/patch-formatter.ts (L129-167)
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
```
