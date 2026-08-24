### Title
Stale Copilot-generated conflict resolutions can be applied to conflict markers from a different or updated merge, silently corrupting committed content - ([File: app/src/lib/stores/app-store.ts])

### Summary
The bug-class in the report is a **stale-decision-execution mismatch**: a validation/decision is computed asynchronously against state at time T1 (`createdBlock`), but the corresponding guard only checks a coarse invariant at execution time T2 (whether the order is still pending/whether the TP/SL blocks it), not whether the state that produced the decision is still the state being acted on. GitHub Desktop's Copilot merge-conflict-resolution flow has the same structural gap: an AI-computed resolution is built from a snapshot of conflicted file content taken at one point in time, but is applied later using a check that only verifies the file is *still conflicted* — not that it is *still conflicted for the same reason/operation* that produced the resolution.

### Finding Description
`buildConflictContext()` reads the on-disk contents of each conflicted file and extracts conflict-marker hunks to send to the AI model: [1](#0-0) 

The resulting `copilotResolutions` (one resolved-content blob per file, computed against that specific snapshot of conflict markers) are stored on `multiCommitOperationState` and later applied when the user accepts them: [2](#0-1) 

The only staleness guard present is: [3](#0-2) 

This guard skips a file **only if it has no remaining conflict markers** (i.e., the user manually finished resolving it in an editor). It does **not** verify:
- that the on-disk conflict markers are the *same* ones that were sent to the model (no content hash / hunk comparison),
- that the current multi-commit operation is the *same* merge/rebase/cherry-pick instance that triggered `buildConflictContext` (no operation id, branch tip, or `MERGE_HEAD`/`REBASE_HEAD` sha check).

If the underlying conflict changes between the time the AI request is issued and the time the resolution is accepted — e.g. the user aborts and restarts the operation against updated remote content (a force-pushed/updated PR branch, which is exactly "a fetched repository" or "GitHub API object" controlled by a remote party), or the merge source changes — the file will still report `isConflictedFileStatus && hasUnresolvedConflicts === true`, so the guard passes, and the stale, unrelated `resolution.resolvedContent` is written verbatim to disk and staged: [4](#0-3) 

This mirrors the report's exact invariant break: a decision is bound to a stale reference point (the original conflict snapshot / `createdBlock`) rather than to the live state being finalized, and the only safety check present is coarse ("cancellation still pending" / "still has conflicts") rather than exact ("is this still the same order/conflict").

### Impact Explanation
The write path (`writeFile` + `git add`) directly determines what ends up staged and eventually committed/pushed. If a resolution computed from one conflict scenario is silently applied to a file now in a different conflict state, the resulting commit can contain code the user never reviewed or approved — potentially re-introducing code from an attacker-influenced branch, dropping a legitimate fix, or otherwise silently corrupting what the user commits and pushes upstream. This matches the "silent corruption of what the user commits or pushes" impact category explicitly listed as valid.

### Likelihood Explanation
This requires a timing window between when Copilot's conflict-resolution request is issued and when the user accepts the result, during which the underlying conflicted content changes (e.g., aborting/restarting the operation against updated remote branch content, or the target PR branch being force-pushed and re-fetched). This is a plausible but not fully deterministic sequence, and it depends on user interaction (accepting resolutions after switching operations) rather than being purely attacker-triggered. I was not able to fully verify, within the available iterations, whether aborting a multi-commit operation (`_abortRebase`/equivalent) synchronously discards `copilotResolutions` before a new operation can start, which would materially affect how easy this window is to trigger — this should be verified directly in a full session.

### Recommendation
Bind each `copilotResolutions` entry to the specific operation instance and conflict content it was computed from (e.g., store and re-check `MERGE_HEAD`/rebase-in-progress sha and/or a hash of the original conflict hunks alongside each resolution). Before writing `resolution.resolvedContent`, re-verify that the current on-disk conflict markers match what was analyzed; if they differ, discard the stale resolution and require a fresh Copilot pass instead of silently overwriting.

### Proof of Concept
Conceptual reproduction path (exact repro steps require a live session to confirm timing details):
1. Start a merge/rebase against a branch (e.g. a PR from a fork) that produces a conflicted file `X`.
2. Trigger Copilot conflict resolution; the AI request is issued with `X`'s current conflict markers via `buildConflictContext`.
3. Before the AI response returns, abort the operation and start a different merge/rebase (e.g. the fork owner force-pushed new content, or the user picks a different source branch) that also conflicts in file `X` but with different content.
4. When the delayed Copilot resolution arrives and the user clicks "Accept," the guard at `app-store.ts:7247-7256` only checks that `X` is still conflicted — which it is — and proceeds to overwrite `X` with content computed against the *original* (now-irrelevant) conflict, then stages it via `git add`.
5. The user commits, unknowingly locking in content that does not reflect the actual, current merge conflict resolution.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L390-440)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
```

**File:** app/src/lib/stores/app-store.ts (L7194-7267)
```typescript
    const pathsToStage: string[] = []

    for (const resolution of copilotResolutions) {
      if (manualResolutions.has(resolution.path)) {
        continue
      }

      // Delete-vs-modify conflicts are resolved by setting a manual
      // resolution (ours/theirs) rather than writing file content.
      // The existing stageManualConflictResolution flow handles the
      // actual git checkout --ours/--theirs and staging at commit time.
      if (resolution.deleteConflictAction !== undefined) {
        const file = state.changesState.workingDirectory.files.find(
          f => f.path === resolution.path
        )
        if (file === undefined) {
          continue
        }
        const deletedSide = getDeletedSideFromStatus(file)
        if (deletedSide === undefined) {
          continue
        }
        // "keep" → choose the non-deleted side, "delete" → choose the deleted side
        const manualChoice =
          resolution.deleteConflictAction === 'keep'
            ? deletedSide === 'ours'
              ? ManualConflictResolution.theirs
              : ManualConflictResolution.ours
            : deletedSide === 'ours'
            ? ManualConflictResolution.ours
            : ManualConflictResolution.theirs
        this._updateManualConflictResolution(
          repository,
          resolution.path,
          manualChoice
        )
        continue
      }

      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

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
