Confirmed analog. `_commitIncludedChanges` (`app-store.ts`) reads `file.selection` for each working-directory file from `state.changesState.workingDirectory.files` — a selection whose line-index mapping (`unifiedDiffStart`/absolute line index) was computed against whatever diff was rendered in the UI at some earlier point in time. [1](#0-0)  That selection is passed straight through `createCommit` → `stageFiles` → `applyPatchToIndex`, which re-fetches a **fresh** diff of the file at commit time via `getWorkingDirectoryDiff` and then reuses the old `file.selection.isSelected(absoluteIndex)` calls against the *new* hunk/line layout to build the patch that is `git apply --cached`'d into the index. [2](#0-1) [3](#0-2) 

### Title
Stale line-selection indices applied to a freshly re-diffed file silently stage/commit unintended content — (File: `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
`applyPatchToIndex` computes the patch to stage for a partially-selected file by re-reading the current working-directory diff and then testing each line's absolute index against `file.selection`, an object built earlier (potentially against a different diff of the same file). If the file's line layout changes between selection time and commit time, the index-based selection no longer refers to the lines the user actually selected/deselected, producing a patch that stages different content than what the user intended and reviewed — a case of using a value ("selected line indices") computed under one state ("denomination") and applying it against a different, incompatible state.

### Finding Description
`formatPatch` builds a unified diff by iterating the hunks of the diff passed to it, and for every non-context line calling `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` from that same diff object. [4](#0-3)  `DiffSelection` itself is an opaque bag of index → boolean state with no association to a specific diff/version of the file — nothing ties a selection to the diff it was derived from.

`applyPatchToIndex` does not use the diff that was used to build/display the user's selection. Instead, it independently re-fetches the diff at staging time:
```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
``` [5](#0-4) 

If the working-directory file's content differs between the time the user made line selections (in the Changes UI) and the time `stageFiles`/`applyPatchToIndex` runs (commit click), the hunk boundaries, `unifiedDiffStart`, and line ordering can shift. The old selection's absolute indices are then matched against different lines in the new diff. `getWorkingDirectoryDiff` diffs against `HEAD` (not the index/UI snapshot), so any change to the file on disk between selection and commit — from an editor autosave, an IDE format-on-save, a background tool, a git hook, or content written by another process reading a cloned/fetched repository — silently changes what `applyPatchToIndex` interprets the selection to mean. [6](#0-5) 

`stageFiles` calls `applyPatchToIndex` for every file with a partial selection, so this affects the normal partial-commit flow used constantly in Desktop. [7](#0-6)  Nothing in `_commitIncludedChanges` refreshes the diff or re-validates selections against current file state immediately before staging; it simply forwards `state.changesState.workingDirectory.files` (with their existing `selection`) to `createCommit`. [8](#0-7) 

### Impact Explanation
This can cause the index-based line selection to select the wrong lines relative to the newly-fetched diff, resulting in unselected (unreviewed) additions/deletions being silently staged and committed, or reviewed/selected changes being silently dropped from the commit — without any error or warning to the user. This is the "silent corruption of what the user commits" impact category: the user reviews and approves diff A, but diff B (with shifted line numbers) is what actually gets committed and (if auto-push or a subsequent push occurs) pushed to the remote.

### Likelihood Explanation
Requires the file to actually change between when its diff/selection is established in the UI and when the commit executes — plausible with editor autosave/format-on-save, external tools, background linters, or another process touching the working tree while Desktop's Changes view is open (a state easily reached without any local/admin/malware precondition, purely via normal Desktop usage plus a file-changing tool the user already runs). No warning is produced because `applyPatchToIndex` and `formatPatch` have no mechanism to detect that the selection's origin diff differs from the diff currently being formatted.

### Recommendation
Bind `DiffSelection` (or the underlying line data) to the exact diff/version it was computed from, e.g., include a hash of the diff or the source file content, and have `applyPatchToIndex`/`stageFiles` verify the freshly-fetched diff matches before reusing absolute-index-based selections; if it doesn't match, refuse to stage and force the UI to refresh the diff and selection instead of silently reinterpreting stale indices against new content.

### Proof of Concept
1. Modify a tracked file so it has multiple hunks; open it in Desktop's Changes view and deselect some lines in the middle hunk (leaving other hunks selected).
2. Before clicking "Commit," let an external process (e.g., an editor's format-on-save, or simply run `sed`/an autosave tool) change the file so that line counts before the deselected hunk shift (add/remove a line above it) — Desktop's UI still shows the stale diff/selection since it hasn't re-rendered.
3. Click "Commit." `_commitIncludedChanges` passes the stale `file.selection` through `createCommit` → `stageFiles` → `applyPatchToIndex`, which calls `getWorkingDirectoryDiff` and gets the new diff with shifted hunk offsets.
4. `formatPatch` evaluates `file.selection.isSelected(absoluteIndex)` against the new hunk's `unifiedDiffStart`, so the wrong lines are treated as selected — the resulting commit contains different content than what the user visually approved, with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3680-3698)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
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

**File:** app/src/lib/git/apply.ts (L52-82)
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

**File:** app/src/lib/patch-formatter.ts (L129-206)
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
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })
```

**File:** app/src/lib/git/diff.ts (L342-356)
```typescript
export async function getWorkingDirectoryDiff(
  repository: Repository,
  file: WorkingDirectoryFileChange,
  hideWhitespaceInDiff: boolean = false
): Promise<IDiff> {
  // `--no-ext-diff` should be provided wherever we invoke `git diff` so that any
  // diff.external program configured by the user is ignored
  const args = [
    'diff',
    ...(hideWhitespaceInDiff ? ['-w'] : []),
    '--no-ext-diff',
    '--patch-with-raw',
    '-z',
    '--no-color',
  ]
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
