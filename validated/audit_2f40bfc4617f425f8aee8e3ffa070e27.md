### Title
Partial-commit staging re-fetches a fresh diff and applies stale line-index selections, allowing a working-directory content swap between review and commit to silently corrupt what is staged/committed - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets users stage a *subset* of lines from a file's diff for a partial commit. The line-selection (`DiffSelection`) that the user makes is keyed purely by positional line index within a specific diff snapshot the user reviewed in the UI. When the commit actually happens, `applyPatchToIndex` does not reuse that reviewed diff — it re-runs `git diff` against the working tree at commit time and builds the patch from that fresh result, applying the old index-based selection to it. [1](#0-0) 

### Finding Description
`stageFiles` iterates over files with a partial selection and calls `applyPatchToIndex` for each: [2](#0-1) 

Inside `applyPatchToIndex`, the diff used to build the actual patch that is `git apply --cached`'d into the index is obtained by calling `getWorkingDirectoryDiff(repository, file)` *at commit time*, not by reusing the diff object the user looked at when they made their line selections in the Changes view: [1](#0-0) 

`getWorkingDirectoryDiff` runs `git diff ... HEAD -- <path>` fresh against whatever is currently on disk: [3](#0-2) 

`formatPatch` then walks this *freshly generated* diff's hunks and lines, and decides whether to include each line purely by an `absoluteIndex` computed from the hunk's `unifiedDiffStart` plus the position within the hunk, checked against `file.selection.isSelected(absoluteIndex)`: [4](#0-3) 

Critically, `DiffSelection.isSelected` has no notion of *which diff* it was computed against — it is just a set of numeric line indices: [5](#0-4) 

Additionally, the app's state layer explicitly preserves this stale, diff-agnostic selection across working-directory refreshes rather than invalidating it when the underlying diff shape changes: [6](#0-5) 

**Broken invariant:** the set of selected *positions* is treated as if it always maps to the same *lines of content*, but that mapping is only valid for the exact diff snapshot it was derived from. If the working-tree file content changes between the moment the user reviews/selects lines and the moment `applyPatchToIndex` re-fetches the diff, hunk boundaries and line ordering shift, so the old numeric indices now point at different lines in the new diff. The result: `formatPatch` will silently include content the user never selected, or drop content the user did select, when building the patch that is staged and eventually committed — the exact same class of bug as `_incrementCumulativeRewards` recording state against `block.number` (the value at "submission time") instead of `endBlock` (the value that was actually intended/reviewed).

### Impact Explanation
This causes **silent corruption of what the user commits**: the staged/committed diff no longer matches either (a) what the user saw and explicitly selected in the UI, or (b) a coherent state of the file. Because Desktop trusts a git remote/fetched repository's working-tree content and any tooling that touches files under the repo root (editor autosave, formatter-on-save, a build watcher, or a malicious `post-checkout`/`pre-commit` hook shipped by a cloned/fetched malicious repository) can alter file content during this window, an attacker who controls repository tooling that runs against the working tree can cause a user to unknowingly commit and push content they never reviewed or intended (e.g. reintroducing a line they deliberately deselected, or omitting a security-relevant line they thought they staged). This matches the valid-impact class of "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Likelihood is limited by needing some other process/hook to touch the working-tree file in the narrow window between the user's line-selection in the diff view and clicking "Commit" (this can be automatic — editors/formatters/build tools/lint-on-save routinely rewrite files, and a malicious repo can ship a `.git/hooks` script or an editor-integration file that reformats on save). It is not a purely local/attacker-triggered exploit requiring physical access; it can be seeded by a cloned/fetched malicious repository that installs such tooling and relies on ordinary developer workflows (auto-format, background linting) to trigger the race. I could not verify from the index whether Desktop revalidates the diff/selection state immediately before staging (e.g., a hash check) — this would need to be confirmed against the full source in a live session.

### Recommendation
Do not re-derive the diff used to build the applied patch from disk at commit time using only positional line indices captured earlier. Instead: (1) capture and pass through the exact diff object (or its content hash) that was used to compute the current `DiffSelection`, and (2) before calling `applyPatchToIndex`, re-verify that the working-tree file's diff still matches what the selection was computed against (e.g., compare a hash of the diff text or hunk headers). If it does not match, refuse to stage the partial commit and force the UI to re-render the diff and require the user to re-select, rather than silently applying stale indices to new content.

### Proof of Concept
1. In a repository, modify `file.txt` and open it in the Desktop Changes view; a diff with two hunks is shown.
2. Select only the addition in hunk 2 (deselect hunk 1) — this creates a `DiffSelection` whose diverging line indices are tied to the hunk layout of *this* diff (`unifiedDiffStart` values from `formatPatch`/`patch-formatter.ts`).
3. Before clicking "Commit," an external process (e.g., an editor auto-format-on-save, or a `post-checkout`/file-watcher script bundled in a cloned malicious repository) rewrites `file.txt`, shifting line numbers/hunk boundaries (e.g., adding blank lines earlier in the file) without the user reselecting anything in Desktop's UI (which may not immediately re-render if the file watcher event is coalesced/delayed).
4. Click "Commit." `stageFiles` → `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` against the *new* on-disk content, producing a diff whose hunk/line layout differs from the one the selection was made against.
5. `formatPatch` applies the old `DiffSelection.isSelected(absoluteIndex)` positions to the new hunks/lines (`app/src/lib/patch-formatter.ts:143-161`), including/excluding lines that do not correspond to what the user actually selected, and this patch is applied to the index and committed — producing a commit whose content silently diverges from user intent.

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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

**File:** app/src/lib/git/diff.ts (L342-401)
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
  const successExitCodes = new Set([0])
  const isSubmodule = file.status.submoduleStatus !== undefined

  // For added submodules, we'll use the "default" parameters, which are able
  // to output the submodule commit.
  if (
    !isSubmodule &&
    (file.status.kind === AppFileStatusKind.New ||
      file.status.kind === AppFileStatusKind.Untracked)
  ) {
    // `git diff --no-index` seems to emulate the exit codes from `diff` irrespective of
    // whether you set --exit-code
    //
    // this is the behavior:
    // - 0 if no changes found
    // - 1 if changes found
    // -   and error otherwise
    //
    // citation in source:
    // https://github.com/git/git/blob/1f66975deb8402131fbf7c14330d0c7cdebaeaa2/diff-no-index.c#L300
    successExitCodes.add(1)
    args.push('--no-index', '--', '/dev/null', file.path)
  } else if (file.status.kind === AppFileStatusKind.Renamed) {
    // NB: Technically this is incorrect, the best kind of incorrect.
    // In order to show exactly what will end up in the commit we should
    // perform a diff between the new file and the old file as it appears
    // in HEAD. By diffing against the index we won't show any changes
    // already staged to the renamed file which differs from our other diffs.
    // The closest I got to that was running hash-object and then using
    // git diff <blob> <blob> but that seems a bit excessive.
    args.push('--', ensureRelativePath(file.path))
  } else {
    args.push('HEAD', '--', ensureRelativePath(file.path))
  }

  const { stdout, stderr } = await git(
    args,
    repository.path,
    'getWorkingDirectoryDiff',
    { successExitCodes, encoding: 'buffer' }
  )
  const lineEndingsChange = parseLineEndingsWarning(stderr)

  return buildDiff(stdout, repository, file, 'HEAD', 'HEAD', lineEndingsChange)
}
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

**File:** app/src/lib/stores/updates/changes-state.ts (L41-60)
```typescript
  // Attempt to preserve the selection state for each file in the new
  // working directory state by looking at the current files
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
    })
```
