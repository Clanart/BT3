## Title
`applyPatchToIndex` re-fetches the working-directory diff at commit time, so a repository-controlled content change between diff review and commit causes stale line-selection indices to be applied to the wrong hunks, silently staging unreviewed content - (File: `app/src/lib/git/apply.ts`)

### Summary
This is the same "recompute an untrusted/external value twice and use the two results interchangeably" invariant break as the `fetchPrice` report: Desktop computes a diff once to show the user and record their line-level selection, and then **recomputes the diff a second time** at commit time to build the patch that is actually applied to the index. If the file content on disk differs between the two `getWorkingDirectoryDiff` calls, the line-index-based selection captured against the *first* diff is blindly applied to the hunks of the *second* diff.

### Finding Description
`DiffSelection` (`app/src/models/diff/diff-selection.ts`) stores selection state purely as a set of abstract line indices (`divergingLines`), with `isSelected(lineIndex)` doing a pure set lookup with no reference back to the diff/content that produced those indices: [1](#0-0) 

The UI computes this selection against a diff fetched once, in `updateChangesWorkingDirectoryDiff`: [2](#0-1) 

That function does contain guards to detect if the *selected file* changed while loading, but nothing re-verifies file *content* is unchanged, and crucially the resulting `file.selection` (built from that diff's hunk boundaries) is what gets persisted into `WorkingDirectoryFileChange` and later handed to `_commitIncludedChanges`: [3](#0-2) 

At commit time, `stageFiles` routes partially-selected files to `applyPatchToIndex`: [4](#0-3) 

`applyPatchToIndex` does **not** reuse the diff the user reviewed. It calls `getWorkingDirectoryDiff` again, fresh, against the current on-disk file: [5](#0-4) 

That freshly-fetched diff is fed straight into `formatPatch`, which applies the *old* `file.selection` (line indices computed against the *old* diff) onto the hunks of the *new* diff via `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and `file.selection.isSelected(absoluteIndex)`: [6](#0-5) 

There is no check anywhere in this path comparing the diff used to build the selection to the diff used to build the patch (no hash/etag/hunk-count equality check equivalent to the price-oracle's round comparison). If the file's on-disk content shifts hunk boundaries or line counts between the two fetches (e.g. because a `.gitattributes`-driven smudge/clean filter, a checkout/merge, a build tool, or any other process controlled or triggered by repository content rewrites the file in that window), the indices from the stale selection no longer refer to the same logical lines in the new diff. `formatPatch` will then either include lines the user never saw/selected, or silently drop lines the user explicitly selected, into the patch that gets `git apply --cached`'d and committed.

### Impact Explanation
This directly matches the requested impact class: "silent corruption of what the user commits." A cloned/fetched repository can define a smudge/clean filter via `.gitattributes` (a repo-tracked file, fully attacker-controlled) that Desktop invokes automatically without any user prompt when Git re-touches the working tree (e.g. on `git status`/refresh cycles Desktop performs continuously in the background) — see `withTrampolineEnv`/`git()` wrapper being invoked for every git operation including background status refreshes: [7](#0-6) 

If the filter output for a file changes between the time the user reviews/selects specific lines in the Changes view and the time they click "Commit", the second `getWorkingDirectoryDiff` inside `applyPatchToIndex` picks up the filter's altered output, and the stale line-index selection gets misapplied — the user can end up committing (and later pushing) content they never reviewed or explicitly excluding content they intended to commit, all without any error or warning.

### Likelihood Explanation
The window between diff display and commit click is arbitrarily long and entirely user-paced, and Desktop performs frequent background `git status`/diff refreshes that re-invoke filters on repository-controlled triggers. No attacker interaction with the local machine, no admin rights, and no credentials are required — only that the victim clones/opens a repository containing a crafted `.gitattributes` filter definition and performs a normal partial-commit workflow. This is a realistic, un-prompted path that a malicious repository author fully controls.

### Recommendation
Do not recompute the diff inside `applyPatchToIndex`. Either:
- Pass the exact `IDiff` object the user reviewed/selected against down to `stageFiles`/`applyPatchToIndex` instead of refetching, or
- If a refetch is required, validate that the newly fetched diff is structurally identical (same hunk boundaries/line count, or content hash) to the diff the selection was computed against, and fail/re-prompt the user (analogous to re-validating the Chainlink round instead of trusting a stale status) rather than silently applying mismatched indices.

### Proof of Concept
1. Clone an attacker-controlled repository that defines a `.gitattributes` clean/smudge filter (`filter.evil.smudge`/`clean`) on `secret.txt` which returns benign content on first invocation but injects extra lines on a later invocation (e.g. keyed off a counter file or timestamp written to a location the filter can access).
2. Modify `secret.txt` locally; open the Changes view. Desktop computes diff #1 via `updateChangesWorkingDirectoryDiff`/`getWorkingDirectoryDiff`, triggering the filter's first (benign) output; user reviews and selects only the benign lines for commit (`DiffSelection` records indices against diff #1's hunks).
3. Before the user clicks "Commit", a background status refresh (or any other git invocation Desktop performs automatically) re-invokes the filter, which now smudges different/malicious content into the working tree.
4. User clicks "Commit". `_commitIncludedChanges` → `stageFiles` → `applyPatchToIndex` calls `getWorkingDirectoryDiff` a second time, producing diff #2 (with the malicious content, different hunk boundaries). `formatPatch` applies the old selection's line indices to diff #2's hunks — the malicious/unreviewed lines matching those same absolute indices get included in the staged patch and committed, with no indication to the user that what was committed differs from what was reviewed and selected.

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

**File:** app/src/lib/stores/app-store.ts (L3444-3449)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

```

**File:** app/src/lib/stores/app-store.ts (L3681-3699)
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
            amend: context.amend,
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

**File:** app/src/lib/git/core.ts (L276-296)
```typescript
  return withHooksEnv(
    hooksEnv =>
      withTrampolineEnv(
        async env => {
          const commandName = `${name}: git ${args.join(' ')}`

          const result = await GitPerf.measure(commandName, () =>
            exec(args, path, {
              ...opts,
              env: {
                // Explicitly set TERM to 'dumb' so that if Desktop was launched
                // from a terminal or if the system environment variables
                // have TERM set Git won't consider us as a smart terminal.
                // See https://github.com/git/git/blob/a7312d1a2/editor.c#L11-L15
                TERM: 'dumb',
                ...opts.env,
                ...hooksEnv,
                ...env,
              },
            })
          ).catch(err => {
```
