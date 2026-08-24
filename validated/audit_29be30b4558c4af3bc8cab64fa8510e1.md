## Title
Stale line-based `DiffSelection` indices are reapplied against a freshly re-fetched diff during partial commit, silently corrupting staged content - (File: `app/src/lib/git/apply.ts`)

### Summary
Desktop lets users stage a subset of lines in a modified file for a commit. The selection the user makes in the UI is stored as a `DiffSelection` keyed by *absolute unified-diff line index*, computed against the diff that was rendered at selection time [1](#0-0) . When the commit is actually executed, `applyPatchToIndex()` re-runs `getWorkingDirectoryDiff()` to obtain a brand-new diff and then applies the *old* selection's line indices to that new diff via `formatPatch()`, with no check that the diff hasn't shifted in the meantime.

### Finding Description
`_commitIncludedChanges()` snapshots `state.changesState.workingDirectory.files` (which carries each file's `DiffSelection`, computed from whatever diff was last rendered in the Changes view) and passes it straight to `createCommit()` → `stageFiles()` → `applyPatchToIndex()`: [2](#0-1) 

Inside `applyPatchToIndex`, the diff used to build the patch is fetched fresh at commit time, not the one the selection was built against: [3](#0-2) 

`formatPatch()` then walks the hunks of *this new diff* and decides which lines to include purely by `file.selection.isSelected(absoluteIndex)` — i.e. by numeric position in the unified diff, not by content identity: [4](#0-3) 

The only place Desktop reconciles a selection against a changed diff is `updateChangesWorkingDirectoryDiff()`, and it only fires when the Changes view actively reloads the diff for the *currently selected* file (e.g. on a status refresh while that file is open): [5](#0-4) 

There is no equivalent reconciliation performed synchronously in the actual commit path (`_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex`). If the working-tree file's diff shape changes between the last UI diff load and the moment the commit executes — for example due to a `.gitattributes`-configured clean/smudge or `filter` driver that runs on `git diff`/`git status` and produces non-deterministic or shifting output, or a checkout/merge attribute normalizing line endings differently each invocation, or simply another process touching the file during the async gap between clicking "Commit" and the patch being generated — the hunk boundaries and absolute line offsets in the new diff will no longer line up with the offsets recorded in the stale `DiffSelection`. `formatPatch` will then select the wrong lines (by position, not by content), and `git apply --cached` will happily apply that patch to the index, producing a commit whose content differs from what the user intended, without any error or warning.

### Impact Explanation
This causes silent corruption of what the user actually commits: content the user explicitly excluded can end up staged and committed, or content they intended to include can be silently dropped. Since a cloned/fetched repository controls `.gitattributes` (filter/clean/smudge drivers) and any such driver runs automatically on `git diff`/`git status` invocations Desktop makes internally, a malicious repository author can engineer this diff-shifting behavior without any unnatural user action beyond the normal edit/stage/commit workflow — matching the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Likelihood is medium: it requires a window between the diff the user based their partial selection on and the diff `applyPatchToIndex` re-fetches at commit time, and it requires the diff output to change between those two invocations (e.g., via a crafted filter driver or a race with another process modifying the file). Partial/line-level commit selection is a commonly used Desktop feature, and repositories can ship attacker-controlled `.gitattributes` filters, so the precondition is plausible for any user who clones an untrusted repository and does partial-line commits within it.

### Recommendation
Before applying a file's `DiffSelection` in `applyPatchToIndex`/`stageFiles`, verify that the diff used to build the patch is unchanged from the diff the selection was computed against (e.g., compare hunk headers/content hashes or the diff text itself), and if it differs, abort the partial-commit for that file and force a re-diff/re-selection instead of silently applying stale line indices to new hunk boundaries.

### Proof of Concept
1. Clone/open a repository containing a `.gitattributes` entry with a `filter` (clean/smudge) driver whose output for a tracked file is not stable across invocations (e.g., embeds a counter or timestamp), affecting the resulting diff hunk layout.
2. Modify the file so it has multiple hunks; in Desktop's Changes view, select only specific lines for inclusion (partial commit) based on the diff currently rendered.
3. Trigger a delay before clicking "Commit" (or let a background status refresh happen) such that `git diff`/`git status` is invoked again and the filter driver produces slightly different output, shifting hunk boundaries.
4. Click "Commit". `_commitIncludedChanges` passes the stale `DiffSelection` (from step 2) to `createCommit` → `applyPatchToIndex`, which fetches the new diff (step 3's shape) and applies the old line indices via `formatPatch`, staging/committing different lines than the user selected — silently corrupting the resulting commit.

### Citations

**File:** app/src/models/diff/diff-selection.ts (L1-1)
```typescript
import { assertNever } from '../../lib/fatal-error'
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

**File:** app/src/lib/stores/app-store.ts (L3681-3698)
```typescript
  public async _commitIncludedChanges(
    repository: Repository,
    context: ICommitContext
  ): Promise<boolean> {
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })

    const gitStore = this.gitStoreCache.get(repository)

    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
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
