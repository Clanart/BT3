## Analog Found

### Title
Copilot conflict-resolution writer trusts unverified `path`/`resolvedContent` from AI-derived structured output instead of validating it against the actual conflicted files - ([File: app/src/lib/stores/app-store.ts])

### Summary
The reported IndexVaultV1 bug is a case of "trust the counterparty's declared/expected value instead of verifying the actual, ground-truth result of an operation," causing state corruption. The equivalent broken invariant in GitHub Desktop is `_applyCopilotConflictResolutions`, which writes file content to disk and stages it for commit based on the `path` and `resolvedContent` fields returned by the Copilot conflict-resolution SDK, without verifying that `path` actually corresponds to one of the files that had a real merge conflict on disk.

### Finding Description
`_applyCopilotConflictResolutions` iterates over `copilotResolutions` (produced by `_resolveConflictsWithCopilot`, which is populated by AI output derived from repository content — see `buildConflictContext`, which feeds conflicted-file text into the model) [1](#0-0) .

For each `resolution`, the code:
1. Resolves the path safely within the repository root via `resolveWithin(repository.path, resolution.path)`, which does correctly guard against path traversal and symlink escapes outside the repo [2](#0-1) .
2. Looks up `onDiskFile` in `state.changesState.workingDirectory.files` to check whether the file is still conflicted, in order to avoid clobbering a user's manual edits [3](#0-2) .
3. **Unconditionally** calls `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and stages the path with `git add` [4](#0-3) .

The skip condition in step 2 only fires when `onDiskFile !== undefined && isConflictedFileStatus(...) && !hasUnresolvedConflicts(...)` — i.e., it only protects a file that is *known* to be a currently-conflicted file that the user has since resolved externally. If `onDiskFile` is `undefined` — meaning `resolution.path` does not correspond to any file with pending changes in the working directory at all — the loop does **not** skip; it proceeds straight to `writeFile`. This is the exact analog of the reported bug: the code trusts the AI-declared `path`/`resolvedContent` (the "declared LP amount") in place of a verified value (the "actual conflicted file set", i.e. what git itself reports as an unmerged/conflicted path), the same kind of value the vault code should have used from the target vault instead of the caller-supplied collateral figure.

Because the model's output is influenced by the raw content of files in the repository being merged/rebased/cherry-picked (`buildConflictContext` sends that content to Copilot), an attacker who controls a branch/commit being merged can attempt prompt injection inside a conflicting file's content to try to make the model emit a resolution object naming a path other than one of the genuinely conflicted files (e.g. a build/config/hook file within the repo) together with attacker-chosen content. `resolveWithin` will happily allow any path inside the repository tree — it only guards the tree boundary, not membership in the conflict set. There is no independent server-side or client-side check that `resolution.path` is a subset of the actual `getUnmergedFiles(workingDirectory)` list before writing/staging.

### Impact Explanation
If a resolution names a path outside the actual conflict set, Desktop silently writes attacker-influenced content into an existing tracked file inside the repository and `git add`s it, so it becomes part of what the user commits/pushes without any diff review specific to that file being surfaced to them as a "conflict resolution" (the user only reviews the copilot-conflicts dialog, which is built from `copilotResolutions`, i.e., the same untrusted list). This falls under "silent corruption of what the user commits or pushes" — the strongest category in the valid-impact list — because the mismatch between the declared resolution set and the true conflict set is never independently checked against ground truth (`git status`/`getUnmergedFiles`) at write time.

### Likelihood Explanation
Exploitation requires: (1) the Copilot-conflict-resolution feature is enabled and the user has an eligible Copilot account, (2) the user triggers a merge/rebase/cherry-pick against attacker-influenced content and chooses "Resolve with Copilot," and (3) the model, when processing attacker-crafted conflict content, can be steered into emitting a resolution object with a `path` outside the real conflict set. Step 3 depends on how robustly the SDK response is parsed/filtered upstream (in `_resolveConflictsWithCopilot`), which was not directly inspected here — this is a caveat: it's possible the SDK layer already restricts response paths to the requested file set, in which case this would only be a defense-in-depth gap rather than an end-to-end exploitable path. Absent confirmation of that upstream filter, the writer function itself contains no such check.

### Recommendation
In `_applyCopilotConflictResolutions`, validate `resolution.path` against the authoritative list of currently conflicted paths (e.g. `getUnmergedFiles(state.changesState.workingDirectory)`) before calling `writeFile`/staging, and skip (with a warning log, as already done for the out-of-repo case) any resolution whose path is not part of that set — mirroring the existing `resolveWithin` guard but checking conflict-set membership instead of just repo-tree membership.

### Proof of Concept
1. Enable Copilot conflict resolution and configure an eligible account.
2. Craft a branch whose conflicting file content contains a prompt-injection payload attempting to make the resolution model return an additional/incorrect `path` (e.g. `package.json`, `.husky/pre-commit`) with attacker-controlled `resolvedContent`, alongside the legitimate conflict resolutions.
3. Trigger a merge/rebase/cherry-pick with conflicts against this branch, click "Resolve with Copilot," then "Continue Merge."
4. Inspect `_applyCopilotConflictResolutions` (app/src/lib/stores/app-store.ts:7169-7269): because `onDiskFile` lookup for the injected path returns `undefined`, the skip condition is not met, so `writeFile` executes and the path is staged via `git add`, becoming part of the resulting commit.

Note: full end-to-end confirmation that the Copilot SDK layer (`_resolveConflictsWithCopilot`) does not already filter responses to the requested file set was not possible within available tooling/index coverage; that upstream validation should be checked to confirm whether this is exploitable end-to-end or only a missing defense-in-depth check in the writer.

### Citations

**File:** app/src/lib/stores/app-store.ts (L6905-6914)
```typescript
  /**
   * Orchestrate Copilot conflict resolution: call the API, emit progress
   * updates, and transition to the result dialog on success. File writes are
   * deferred until the user confirms (see _applyCopilotConflictResolutions).
   *
   * This shouldn't be called directly. See `Dispatcher`.
   */
  public async _startCopilotConflictResolution(
    repository: Repository
  ): Promise<void> {
```

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/stores/app-store.ts (L7241-7256)
```typescript
      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }
```

**File:** app/src/lib/stores/app-store.ts (L7258-7267)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```
