### Title
Stale line-selection bitmap applied to a re-fetched working-directory diff silently corrupts partial commits - ([File: app/src/lib/git/apply.ts])

### Summary
`AutoCompoundingPodLp.withdraw/redeem` computed `assets/shares` from stale state before a later step (`_processRewardsToPodLp`) mutated that state, causing users to receive/burn the wrong amount. The equivalent broken invariant in GitHub Desktop is: the `DiffSelection` bitmap a user builds while reviewing a partial-commit diff in the renderer is *positional* (keyed by absolute line index), but the *content* it is later applied to is not the same diff object — it is independently re-fetched from disk right before staging. If the working tree changes between the moment the user selects lines and the moment the app stages the commit, the old index-based selection is silently reinterpreted against new content.

### Finding Description
When a user stages a partial commit, the renderer computes a diff (`updateChangesWorkingDirectoryDiff` in `app/src/lib/stores/app-store.ts`) and lets the user pick individual lines via `DiffSelection`, which records selection purely by absolute line index (see usages across `app/src/ui/diff/side-by-side-diff-row.tsx` and `app/src/models/diff/diff-selection.ts`).

`_commitIncludedChanges` [1](#0-0)  snapshots `state.changesState.workingDirectory.files` (each file carrying that index-based `selection`) and then performs asynchronous work — `formatCommitMessage`, git hook execution (`onHookProgress`/`onHookFailure`) — before the actual staging happens [2](#0-1) .

Staging is performed by `createCommit` → `stageFiles` → `applyPatchToIndex` [3](#0-2) [4](#0-3) . Critically, `applyPatchToIndex` does **not** reuse the diff the user reviewed in the UI — it re-derives a brand-new diff from the current on-disk file right before formatting the patch:

```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
``` [5](#0-4) 

`formatPatch` then walks this freshly-fetched diff's hunks and decides what to include purely by calling `file.selection.isSelected(absoluteIndex)` [6](#0-5)  — there is no check that the hunk/line content at `absoluteIndex` still corresponds to what the user actually saw and selected when the selection was created. If the file's content on disk has shifted (lines added/removed elsewhere in the file, or content rewritten by a build tool, formatter, git hook, or any process reacting to a freshly cloned/checked-out repository) in the window between diff-render time and this staging call, the same numeric indices now point to different lines. `git apply --cached` will happily accept the resulting patch as long as the hunk headers/context match the new file, so no error is surfaced.

### Impact Explanation
This corrupts *what the user actually commits or pushes* without any error or warning: lines the user explicitly excluded can be staged, and lines they explicitly included can be dropped, all silently. Because Desktop trusts the working directory content of an attacker-influenced or externally-modified repository (e.g., a repo with a `post-checkout`/`pre-commit` hook, a build script, a linter/formatter integration, or any other file-watcher-driven regeneration triggered right after clone/checkout), this falls squarely in the "silent corruption of what the user commits or pushes" impact category — a supply-chain-style pathway where an attacker who controls repository content/tooling can cause the victim's own commit to contain unintended (possibly attacker-favorable) content while displaying/summarizing a different, reviewed diff.

### Likelihood Explanation
Likelihood is Medium: it requires a real (not necessarily adversarial) time gap between diff computation and staging plus a concurrent modification of the working tree file — which is achievable via git hooks bundled in a cloned repository, editor/format-on-save integrations, or long-running commit-message generation (the code path awaits `formatCommitMessage`, which can involve AI/Copilot calls) before `createCommit` executes. The existing code has no re-validation step (e.g., diff hash/etag comparison) between the point selection is made and the point it is applied, so no existing guard stops this path.

### Recommendation
Before calling `formatPatch`/`applyPatchToIndex`, verify that the diff used to build `file.selection` is still valid against the current working-directory content (e.g., store and compare a content hash or the exact diff object/hunk boundaries used at selection time), and refuse/re-prompt the user if the underlying file has changed since the diff was rendered, rather than silently re-mapping line indices onto new content.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file so it has multiple hunks.
2. In the Changes view, partially select specific lines to include in the commit (this builds a `DiffSelection` bitmap tied to the diff rendered at that moment).
3. Before clicking "Commit" (or during the async window while a commit message is being generated, or via a `post-checkout`/file-watcher script bundled in the repo), have another process rewrite the file so the number/position of lines shifts (e.g., insert lines above the hunks you selected).
4. Click Commit. `_commitIncludedChanges` uses the stale `selection` object, and `applyPatchToIndex` re-fetches the diff fresh from the now-modified file [5](#0-4) , applying the old index-based selection to the new content.
5. Inspect the resulting commit: it will contain different lines than what was visually selected in step 2, with no error reported to the user.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3681-3690)
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

```

**File:** app/src/lib/stores/app-store.ts (L3693-3714)
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
        },
        { gitContext: { kind: 'commit' }, repository }
      )
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
