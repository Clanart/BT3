### Title
Stale line-index selection is silently re-applied to a freshly re-diffed file when staging a partial commit, causing wrong lines to be committed/pushed - (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
GitHub Desktop lets a user select specific lines/hunks of a modified file to include in a commit. That selection (`DiffSelection`) is stored purely as a set of **absolute line indices** into whatever `ITextDiff` was loaded when the user made the selection. When the commit is actually created, `applyPatchToIndex` re-fetches a brand-new diff of the file from disk (`getWorkingDirectoryDiff`) and blindly re-applies the old index-based selection to that new diff via `formatPatch`, without verifying the new diff has the same shape as the one the user actually looked at. This is structurally the same "value computed at time T1, consumed unchecked at time T2 after external state changed" pattern as the Taiko report (fee computed once, applied later after `block.basefee` moved), just applied to line indices instead of a fee.

### Finding Description
The selection the user makes while reviewing a diff is stored on `WorkingDirectoryFileChange.selection`, an `IndexSet`/`DiffSelection` keyed by absolute line index, decoupled from the diff's actual text content: [1](#0-0) 

When staging for a commit, `stageFiles` calls `applyPatchToIndex` for every file with a partial selection: [2](#0-1) 

`applyPatchToIndex` then **independently recomputes the diff from the working directory** at commit time, and feeds that fresh diff plus the (possibly stale) selection straight into `formatPatch`: [3](#0-2) 

`formatPatch` trusts `file.selection.isSelected(absoluteIndex)` against the hunks of this newly fetched diff, with no check that the new diff's hunk layout matches the one the selection was computed against: [4](#0-3) 

The application is aware that diffs can go stale relative to a selection — there is explicit reconciliation logic for the *UI* diff-refresh path, which recomputes `selectableLines` when the working-directory diff is reloaded: [5](#0-4) 

But that reconciliation only runs on the UI's periodic diff-refresh path (`updateChangesWorkingDirectoryDiff`), it does not run — and cannot run — inside the actual staging/commit path (`createCommit` → `stageFiles` → `applyPatchToIndex`), which re-diffs the file a second, independent time right before generating the patch: [6](#0-5) 

If the on-disk content of the file changes between the moment the user reviews/selects lines in the UI and the moment `createCommit` runs (e.g. a repository-provided build/lint/format tool, a `pre-commit`-adjacent file watcher, an IDE "format on save", or any other process racing with Desktop that rewrites the file), the absolute line indices captured in the old selection will land on different lines in the new diff. `formatPatch` has no way to detect this and will happily construct a patch that includes/excludes the wrong lines — silently corrupting exactly what gets committed and later pushed, without any error or warning to the user.

### Impact Explanation
This breaks the core invariant "what the user visually selected is what gets committed." A file whose content is manipulated between diff-render time and commit time (a narrow but non-zero window, and one that is trivially widened by any background process touching tracked files in the repository) can cause Desktop to stage attacker-influenced lines instead of the ones the user intended, or drop lines the user meant to include. Since the corrupted commit is subsequently pushed, this is silent corruption of the user's commit/push content — a category explicitly called out as valid impact in this scan's scope.

### Likelihood Explanation
Moderate-to-high, mirroring the judge's assessment on the original report ("impact likely small per-event, but likelihood of the race occurring is relatively high" over the lifetime of many commits). Any workflow where files are modified by tools outside of direct, atomic user keystrokes while a partial-selection commit is in flight (auto-formatters, linters run via editor save hooks, generated files rewritten by build watchers, git hooks doing in-place rewrites before `pre-commit` fires) creates the race window. No local/admin access or pre-existing malware is required — only a repository/tooling setup that legitimately rewrites tracked files, which is common (Prettier-on-save, codegen watchers, etc.).

### Recommendation
Before generating the patch in `applyPatchToIndex`/`formatPatch`, validate that the diff used to build the selection is still structurally consistent with the freshly-fetched diff (e.g., compare hunk headers/line counts or content hashes), and fail the staging operation (or re-derive the selection safely) rather than silently applying stale line indices to new content. At minimum, surface an error to the user instead of proceeding when the diff has changed since the selection was captured.

### Proof of Concept
1. User opens a modified file in Desktop's Changes view and selects specific lines/hunks for a partial commit (`DiffSelection` built against diff `D1`, per `patch-formatter-test.ts` patterns).
2. Before clicking "Commit", a background process (an on-save formatter/linter/codegen tool configured in the repo, or a `pre-commit`/`prepare-commit-msg` hook artifact) rewrites the same file on disk, shifting line numbers/hunks.
3. User clicks "Commit". `createCommit` → `stageFiles` → `applyPatchToIndex` runs `getWorkingDirectoryDiff` again, producing diff `D2` (different from `D1`).
4. `formatPatch(file, D2)` applies the `D1`-based absolute-index selection to `D2`'s hunks/lines: [7](#0-6)  — resulting in a patch that stages different content than what the user actually selected/reviewed, which is then committed and can be pushed without any indication to the user that the staged content diverged from their selection.

### Citations

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
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

**File:** app/src/lib/git/commit.ts (L15-32)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

```
