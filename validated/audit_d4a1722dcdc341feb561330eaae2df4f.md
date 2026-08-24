I have enough evidence to write up the finding.

### Title
Selective ("partial") commit staging re-fetches the diff instead of reusing the reviewed one, letting the working file mutate between line selection and staging so unreviewed content is silently committed — ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop lets a user review a diff, select individual lines/hunks to include (`DiffSelection`, addressed by *absolute line index*), and click "Commit". The index positions the user selected are computed against the diff that was rendered in the UI at review time. However, at staging time the app does **not** reuse that reviewed diff — it independently re-runs `git diff` and reapplies the same index-based selection to whatever diff comes back at that moment.

### Finding Description
`_commitIncludedChanges` in `app-store.ts` takes the files currently selected in `state.changesState.workingDirectory.files` (each carrying a `file.selection` built from absolute line indices into the diff that was rendered for the user) and passes them straight to `createCommit`. [1](#0-0) 

`createCommit` clears the index and calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex`. [2](#0-1) [3](#0-2) 

Crucially, `applyPatchToIndex` does not take the diff the UI already showed the user — it calls `getWorkingDirectoryDiff` again, fresh, at staging time, and builds the patch from *that* diff plus the old `file.selection` index set: [4](#0-3) 

`formatPatch` then interprets `file.selection.isSelected(absoluteIndex)` against the hunks of this newly-fetched diff, where `absoluteIndex` is `hunk.unifiedDiffStart + lineIndex` of the *new* diff, not the one the user actually looked at: [5](#0-4) 

The broken invariant is the same as in the report: a value (the fee rate / here, the mapping of "selected line index → content") is fixed by the user at decision time but is silently re-evaluated against fresh, possibly different, state at execution time. If the tracked file on disk changes between the moment the user reviews the diff and selects lines and the moment they click "Commit" (e.g. a build tool, formatter, linter, IDE, or a script installed by an attacker-controlled repository — such as a `post-checkout`/`husky` hook wiring up a watcher, or a bundled dev-server with hot-reload/codegen that rewrites tracked source files), the hunk layout and line offsets shift. The stale `DiffSelection` indices, still keyed to the old layout, are then applied to the new diff's hunks, so `formatPatch` includes/excludes lines the user never reviewed. `git apply --cached` will accept this reinterpreted patch as long as it is a syntactically valid unified diff; there is no re-validation that the resulting patch still matches what was shown on screen.

Existing safeguards do not close this: `seamless-diff-switcher.tsx`'s `isSameFile`/`isSameDiff` checks only guard the *rendering* path (deciding whether to redraw), not the staging/commit path, so they provide no protection here. [6](#0-5) 

### Impact Explanation
This causes silent corruption of what the user commits and subsequently pushes: content the user explicitly deselected can end up staged and committed, or content they thought they selected can be dropped, without any warning or diff re-confirmation. In a supply-chain context, a malicious/compromised repository that arranges for tracked files to mutate in the background (build scripts, generators, formatters wired via package.json/hook installs) can cause a legitimate contributor to unknowingly commit and push attacker-influenced content under their own identity/signature, or to omit a security-relevant deselected hunk, believing it was excluded.

### Likelihood Explanation
The window between reviewing a diff and pressing "Commit" is real and often non-trivial (typing a commit message, reviewing multiple files). Any process that touches the working tree during that window — including project tooling that ships with the repository itself — triggers the mismatch. No git-level guard rejects it because `git apply --cached` only requires a structurally valid patch, not one matching a specific historical diff snapshot.

### Recommendation
Do not re-fetch the diff at staging time. Persist and reuse the exact diff (or its content hash / line-content mapping) that was shown to the user when the selection was made, and pass it through to `applyPatchToIndex`/`formatPatch`. If the working file has changed since that diff was generated (compare mtime/hash or the diff's own content), fail the staging step for that file and force the UI to refresh the diff and require the user to re-review/re-select before committing, rather than silently reapplying stale indices to new content.

### Proof of Concept
1. Clone/open a repository in GitHub Desktop and modify a tracked file so it has multiple hunks.
2. In the Changes view, open the diff and select only a subset of lines/hunks for inclusion (leaving others unselected) — do not commit yet.
3. While the commit summary box has focus (simulating the review→commit window), have another process (e.g. a `postinstall`/`post-checkout` hook-launched watcher, a formatter-on-save, or simply `git checkout -- <file>` from another terminal followed by rewriting the file with shifted line counts) modify the same tracked file so hunk boundaries/line offsets shift.
4. Click "Commit". `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` (app/src/lib/git/apply.ts:60) against the now-different file and reapplies the stale `DiffSelection` indices via `formatPatch` (app/src/lib/patch-formatter.ts:143-161).
5. Inspect the resulting commit: it contains lines that differ from what was shown/selected in the UI at step 2, demonstrating the silent mismatch between reviewed intent and committed content.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3685-3698)
```typescript
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

**File:** app/src/lib/git/commit.ts (L26-31)
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

**File:** app/src/lib/patch-formatter.ts (L143-161)
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
```

**File:** app/src/ui/diff/seamless-diff-switcher.tsx (L301-325)
```typescript
    // Are we currently loading file contents for this file and is the diff
    // still the same? If so we can wait for that to load
    if (
      this.loadingState !== null &&
      isSameFile(this.loadingState.file, fileToLoad) &&
      isSameDiff(this.loadingState.diff, diff)
    ) {
      return
    }

    this.loadingState = { file: fileToLoad, diff }

    const fileContents = await getFileContents(
      this.props.repository,
      fileToLoad
    )

    this.loadingState = null

    // Has the file changed while we've been reading it?
    if (!isSameFile(fileToLoad, this.props.file)) {
      return
    }

    this.applyFileContents(diff, fileContents)
```
