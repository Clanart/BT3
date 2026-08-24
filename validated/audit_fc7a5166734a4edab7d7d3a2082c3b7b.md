Confirmed: `DiffSelection` (app/src/models/diff/diff-selection.ts:53-136) stores only raw numeric `lineIndex`/`divergingLines` positions with no binding to file content, hash, or the specific diff/hunk instance that produced those indices. This confirms the corrupted-value mechanism below.

### Title
Line-index diff selections are replayed against a re-fetched working-directory diff at stage time, allowing silent content substitution in partial commits - (File: app/src/lib/git/apply.ts)

### Summary
Pyth's bug is a TOCTOU: voter weight is derived from a mutable "total supply" that is measured once (at vote time) and re-measured later (at finalization), and the attacker can shift the underlying value between the two measurements to flip the outcome. The same broken invariant — "a value is captured once, then blindly reused against a different underlying state without re-validation" — recurs in GitHub Desktop's partial-commit staging path.

### Finding Description
When a user partially stages a file, Desktop's UI computes a diff, and the user's line selections are stored purely as integer offsets (`divergingLines: Set<number>`) in `DiffSelection`, with no content hash or binding to the specific diff instance shown to the user [1](#0-0) .

At actual commit/stage time, `applyPatchToIndex` does **not** reuse the diff the user reviewed. It re-fetches a brand new diff straight from the working directory via `getWorkingDirectoryDiff` and then formats a patch by indexing into that fresh diff using the stale `file.selection` indices from the UI: [2](#0-1) 

`formatPatch` walks the newly-fetched diff's hunks/lines and calls `file.selection.isSelected(absoluteIndex)` to decide which lines go into the patch that is applied to the index: [3](#0-2) 

This chain is triggered on every commit: `_commitIncludedChanges` passes the currently-selected `WorkingDirectoryFileChange` objects (with their UI-era selections) into `createCommit`, which calls `stageFiles` → `applyPatchToIndex`: [4](#0-3) [5](#0-4) [6](#0-5) 

Nowhere in this path is the working-tree content or diff re-validated against what the user actually reviewed before the index-position-based selection is reapplied.

### Impact Explanation
If the working-tree file changes between the moment the diff is rendered/selected in the UI and the moment `applyPatchToIndex` re-fetches the diff (e.g., an asynchronous process triggered by opening/cloning the repository — a build tool, format-on-save extension, git `clean`/`smudge` filter defined by a malicious `.gitattributes` in a cloned repo, or any file watcher acting on the working tree), the hunk structure and line offsets can shift. The same numeric indices that meant "commit line 12 (my intended change)" in the old diff can now point at a different line in the newly generated diff. This produces a **silent corruption of what the user commits**: content the user never selected/reviewed can be included in the commit (or vice versa, reviewed content silently dropped), with no warning, hash check, or diff-consistency validation anywhere in `applyPatchToIndex`, `stageFiles`, or `createCommit`.

### Likelihood Explanation
This requires the attacker to control content of a cloned/fetched repository (e.g., via a `.gitattributes`-driven filter, a build/format step invoked by opening the project, or another automated process operating on tracked files) such that the working tree changes between diff display and commit — a scenario consistent with the allowed attacker model (attacker controls a cloned/fetched repository). It does not require local/physical access, admin rights, or pre-existing malware; it only requires the victim to review a diff, make a partial selection, and commit while an attacker-controlled/triggered process mutates the file in that window. No existing guard (no content hash, no hunk fingerprint, no "diff changed, please re-review" check) exists in `applyPatchToIndex` to prevent this.

### Recommendation
Bind `DiffSelection` to the exact diff instance it was derived from (e.g., a hash of the hunk boundaries/content) and validate that the diff re-fetched in `applyPatchToIndex` matches the one the selection was created against before applying `formatPatch`; if it doesn't match, abort and force the user to re-review/re-select rather than remapping stale indices onto new hunks.

### Proof of Concept
1. Attacker sets up a repo with a `.gitattributes` filter (or relies on an existing build/format script) that rewrites a tracked file's content shortly after checkout/open.
2. Victim clones/opens the repo in Desktop, edits the file, and views the diff; Desktop computes hunks and the victim selects specific lines to stage (`DiffSelection.withRangeSelection`) via `app/src/models/diff/diff-selection.ts`.
3. Before the victim clicks Commit, the attacker-controlled filter/process rewrites the file on disk, shifting line offsets.
4. Victim commits; `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff via `getWorkingDirectoryDiff` (app/src/lib/git/apply.ts:60) and applies the victim's original numeric-index selection to the new hunks via `formatPatch` (app/src/lib/patch-formatter.ts:143-171).
5. The resulting commit silently contains different lines than what the victim reviewed and intended to stage.

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

**File:** app/src/lib/stores/app-store.ts (L3693-3711)
```typescript
    return this.withIsCommitting(repository, async () => {
      const result = await gitStore.performFailableOperation(
        async () => {
          const message = await formatCommitMessage(repository, context)
          let aborted = false
          return createCommit(repository, message, selectedFiles, {
            amend: context.amend,
            onHookProgress: this.onHookProgress(repository),
            onHookFailure: this.onHookFailure(() => (aborted = true)),
            onTerminalOutputAvailable: subscribeToCommitOutput => {
              this.repositoryStateCache.update(repository, state => ({
                ...state,
                subscribeToCommitOutput,
              }))
            },
            noVerify: state.skipCommitHooks,
            signOff: state.signOffCommits,
            allowEmpty: state.allowEmptyCommit,
          }).catch(err => (aborted ? undefined : Promise.reject(err)))
```

**File:** app/src/lib/git/commit.ts (L26-32)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)

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
