## Analog Found: Stale diff-selection state is not re-validated before being applied to the index at commit time

### Title
Partial-commit selection state is matched against a freshly re-fetched diff without validating line/content identity, allowing a commit to silently include different content than what the user reviewed — ([File: `app/src/lib/git/apply.ts`])

### Summary
The external report's broken invariant is: an approval ("proposal") is captured at one point in time but executed later without re-validating that the state it operates on still matches what was approved, allowing execution against a changed context. The closest verified analog in GitHub Desktop is the partial-commit ("stage selected lines") flow: the user reviews a diff and selects specific lines/hunks to include in a commit; when the commit actually happens, Desktop does not re-diff-and-confirm that the previously selected line indices still correspond to the same content the user approved — it just re-fetches a diff and blindly re-applies the old index-based selection to it.

### Finding Description
When a user partially stages a file, the app records a `DiffSelection` as a set of **absolute line indices** relative to a diff object that was loaded into the UI at some earlier point [1](#0-0) . When the commit is finally created, `createCommit` clears the index and calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex` [2](#0-1) [3](#0-2) .

`applyPatchToIndex` re-fetches the diff fresh from disk (`getWorkingDirectoryDiff`) right before formatting the patch [4](#0-3) , then builds the patch by calling `file.selection.isSelected(absoluteIndex)` against the **hunks of this newly fetched diff**, using the **same line-index selection object that was computed against the old diff** [5](#0-4) . There is no check that the line at a given absolute index still contains the same text it did when the user selected it. The app itself acknowledges this class of drift exists — the comment in `updateChangesWorkingDirectoryDiff` explicitly says the diff can change dramatically and the app only prunes indices that are no longer selectable, without validating whether *remaining* selected indices still map to the same content [6](#0-5) . Critically, that reconciliation only runs as a side effect of the *UI* re-rendering the diff; it is not re-run immediately before `_commitIncludedChanges` builds the patch, so any drift that happens between the last UI diff refresh and the moment "Commit" is clicked is committed as-is [7](#0-6) .

Because commits by default run `pre-commit`/`prepare-commit-msg` hooks (`noVerify` must be explicitly set) [8](#0-7) , and because git honors any `clean`/`smudge`/`text=auto` attribute-driven content transforms tracked in `.gitattributes`, a cloned repository can arrange for tracked-file content to be rewritten between the time the diff was displayed and the time the user presses Commit (e.g., via a formatter or lint-fix hook wired up through a normal `npm install`/`prepare` step, which is a common and expected workflow for a freshly cloned JS/TS project). The rewritten content shifts hunk boundaries and line offsets, but the previously computed absolute-index selection is still applied verbatim against the new hunks.

### Impact Explanation
This is a "silent corruption of what the user commits" scenario. The lines actually staged and committed can diverge from the lines the user visually reviewed and explicitly selected in the diff viewer, without any warning to the user. A malicious repository author could rely on this to get a contributor to unknowingly commit/push content they never approved (e.g., re-including a line the user explicitly deselected, or excluding a security-relevant line the user meant to include), because the tool's guarantee — "what's shown in the diff and checked by the user is what gets committed" — is violated.

### Likelihood Explanation
Exploitation requires a normal, expected developer action (opening/building a freshly cloned project, which commonly triggers formatter or hook installation via `npm install`/`prepare` scripts) combined with the ordinary act of partially staging lines and committing — no elevated privileges, local malware, or unnatural steps are required beyond the standard "clone and open a project" flow. The race window (time between diff render and clicking Commit) is realistic since Desktop does not lock or re-validate the selection immediately before staging.

### Recommendation
Before applying `file.selection` to the freshly-fetched diff in `applyPatchToIndex`, validate that the selection is still consistent with the new diff's content (e.g., by comparing line hashes/content rather than raw absolute indices), and abort/require re-confirmation from the user if a mismatch is detected instead of silently applying a possibly stale selection.

### Proof of Concept
1. Clone a repository containing a `prepare`/`postinstall` script that installs a pre-commit or file-watcher hook which rewrites a tracked file's content shortly after checkout/install (e.g., a formatter that reflows lines).
2. In GitHub Desktop, modify the file, open the diff, and select only specific lines to include in the commit (deselecting others).
3. Trigger the content rewrite (e.g., run the install step, or let the watcher fire) before pressing "Commit".
4. Click "Commit"; observe that `applyPatchToIndex` re-fetches the diff and applies the old `DiffSelection` indices to the new hunk layout [9](#0-8) , producing a commit whose content does not match what was shown/selected in the UI at commit time.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/commit.ts (L39-65)
```typescript
  if (options?.noVerify) {
    args.push('--no-verify')
  }

  if (options?.signOff) {
    args.push('--signoff')
  }

  if (options?.allowEmpty) {
    args.push('--allow-empty')
  }

  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
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

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```
