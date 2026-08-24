### Title
Partial-commit line selection is applied to a re-fetched diff with no content-identity check, allowing silent corruption of what gets committed - (File: `app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop lets a user review a diff, select individual lines/hunks to include, and then commit only those lines. The line selection is stored as a set of purely numeric line indices (`DiffSelection`), decoupled from any hash or content fingerprint of the diff it was created against. When the commit is actually written, Desktop re-fetches the diff from disk and blindly re-applies the old numeric selection to the new diff. If the working-tree file changes between "user reviews/selects lines" and "commit is executed", the same line indices now point at different content, and the user silently commits/stages lines they never selected.

### Finding Description
The partial-commit staging path is:

1. `stageFiles()` iterates the files to commit and, for any file with a `Partial` selection, calls `applyPatchToIndex(repository, file)`. [1](#0-0) 

2. `applyPatchToIndex` re-fetches the diff for the file **at staging time**, independent of whatever diff the user actually looked at when making the selection: [2](#0-1) 

3. That freshly fetched diff is fed into `formatPatch(file, diff)`, which walks the *new* diff's lines and decides inclusion purely by numeric `absoluteIndex` (`hunk.unifiedDiffStart + lineIndex`) via `file.selection.isSelected(absoluteIndex)`: [3](#0-2) 

4. `DiffSelection.isSelected` is a pure index/set lookup - it carries no reference to the content, hash, or hunk header of the diff it was derived from: [4](#0-3) 

5. The resulting patch is applied directly to the index with `git apply --cached`: [2](#0-1) 

6. `createCommit` orchestrates this: it clears the index, stages the files (running the above re-diff/re-apply), and commits - there is no step that re-validates the selection against the diff that produced it: [5](#0-4) 

**Broken invariant:** the invariant "a line-index selection is only valid for the exact diff it was computed against" is not enforced anywhere in the pipeline. The selection is captured against diff A (shown to the user in the UI at line-selection time via `getWorkingDirectoryDiff`, used in `updateChangesWorkingDirectoryDiff`), but applied against diff B (re-computed independently inside `applyPatchToIndex` at commit time). Nothing compares hunk headers, file hashes, or line counts between A and B before reusing the selection's indices.

This mirrors the reported DeFi bug's structure exactly: a value used for an "accounting" decision (extra-reward-per-share / here, "which lines to include") is computed from state captured *before* a change, but is consumed *after* the state has already mutated, with the two ends of the operation never being reconciled by the code in between.

**Why existing guards don't stop it:**
- `_commitIncludedChanges` only guards against a *different file selection* changing mid-flight (via `selection.getSelectionType()`), not against the *content* of an already-selected file changing between review and staging - there is no re-diff/compare step there either.
- Elsewhere in the codebase (e.g. `updateChangesWorkingDirectoryDiff`) Desktop *does* implement a "before/after" staleness check by comparing `selectedFileIDs` before and after an async load, discarding results if state changed: [6](#0-5) 
  No equivalent check exists on the commit/staging path in `apply.ts`/`patch-formatter.ts` for the diff *content* itself, only for file identity/selection existence.

### Impact Explanation
If the working-tree content of a file changes between the time the user visually selects lines to commit and the time `applyPatchToIndex` actually re-diffs and re-applies that selection (e.g., because a git hook such as `post-checkout`/`smudge` filter rewrites the file, a background refresh triggers a checkout/pull/merge that touches the file, or any external/tooling process modifies the file during the window the Commit button is pressed), the numeric line-index selection from the stale diff is silently reinterpreted against new content. This can cause the user to commit and push lines/content they never reviewed or intended to include - i.e., silent corruption of what the user commits or pushes, which is explicitly listed as valid impact for this class of report. In the worst case this could be leveraged by a malicious repository shipping a clean/smudge filter or hook that races the commit flow to sneak attacker-chosen content into a legitimate-looking partial commit that the victim believes only contains their reviewed lines.

### Likelihood Explanation
The window is real (async diff fetch/status refresh/checkout operations run concurrently with the UI in `app-store.ts`, and filters/hooks execute synchronously during git's own I/O), but requires a way to mutate the working file's content in that specific window, most plausibly via a malicious repository's `.gitattributes`-declared filter or hook, or a race with an autosave/background refresh. This is a timing-dependent condition, not exploitable at will over the network, so likelihood is moderate rather than trivially reproducible remotely.

### Recommendation
Do not decouple the line selection from the diff it was computed against. Either:
- Store (or re-derive and compare) a content fingerprint (e.g., hash of the diff hunks/patch text, or `unifiedDiffStart`/hunk headers) alongside the `DiffSelection`, and re-validate it against the diff fetched in `applyPatchToIndex` before calling `formatPatch`; abort/re-prompt the user if they differ, mirroring the "before/after" comparison pattern already used in `updateChangesWorkingDirectoryDiff`.
- Alternatively, thread through and reuse the exact diff object the user selected lines against (captured once, e.g. in `_commitIncludedChanges`) instead of letting `applyPatchToIndex` independently re-fetch a new diff at staging time.

### Proof of Concept
1. Open a repository in Desktop with a tracked file `f.txt` containing lines `A`, `B`, `C`.
2. Modify `f.txt` to add lines, view the diff, and select only the addition of line `X` at (line-index N) for partial commit; leave the rest of the working tree diff unselected.
3. Before finalizing the commit, have an external process (simulating a malicious `clean`/`smudge` filter defined by the repo's `.gitattributes`, or a concurrent Desktop-triggered checkout/refresh) rewrite `f.txt` so that the content at line-index N is now a different line `Y` (e.g., attacker-injected content), while keeping the file's overall diff-selectable structure superficially similar.
4. Click Commit. `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) re-fetches the diff (now reflecting the tampered content) and `formatPatch` (`app/src/lib/patch-formatter.ts:157`) applies the old numeric selection (`isSelected(N)`) to this new diff, staging/committing line `Y` instead of `X` - content the user never reviewed or explicitly selected - which is then committed and can be pushed.

### Citations

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

**File:** app/src/lib/patch-formatter.ts (L143-170)
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

**File:** app/src/lib/stores/app-store.ts (L3450-3464)
```typescript
    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

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
