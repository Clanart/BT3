### Title
Stale working-directory snapshot lets Copilot conflict resolution silently overwrite a user's newer manual conflict fix - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions` reads the repository's working-directory file list **once** at the top of the function and then reuses that single, increasingly stale snapshot as the "has the user already resolved this file?" guard for every file it writes over the course of a multi-file, multi-`await` loop. This mirrors the Locke.sol bug: a value (`redeemedDepositTokens`) that is captured/derived at one point in time is used later, after other state-changing actions have occurred, to gate a critical operation (`recoverTokens`) — producing an incorrect decision. Here the un-refreshed `state.changesState.workingDirectory.files` is used to gate whether Copilot's AI-generated resolution should be written to disk, even though the loop has already performed several asynchronous steps (and per-file disk writes/`git add`) by the time later files in the list are processed.

### Finding Description
`_applyCopilotConflictResolutions` captures state a single time: [1](#0-0) 

It then iterates over every AI-produced `copilotResolutions` entry, performing an `await resolveWithin(...)` per file and, for files that pass the guard, an `await writeFile(...)`: [2](#0-1) 

The guard that is supposed to prevent clobbering a manually-resolved file explicitly says it protects against the user resolving a file "externally ... while the result dialog was open": [3](#0-2) 

But the check reads from `state.changesState.workingDirectory.files` — the single snapshot captured at line 7172, before the loop began — instead of re-reading `this.repositoryStateCache.get(repository)` for each file. Because the loop runs sequentially across potentially many files (earlier Copilot resolution logic chunks up to 15–20 resolvable files at a time, see `app/src/lib/stores/copilot-store.ts:1284-1343`), and performs disk I/O and `git add` calls along the way, there is a real window during which the on-disk/working-directory state can change (the user saves a fix to a *different* conflicted file, or git status changes) without that change ever being observed by the stale `state` object. Any file whose resolution is processed after that point will still be evaluated against the old snapshot, so the "already resolved externally" skip never fires for it, and Copilot's stored `resolvedContent` — content generated from an LLM prompt built out of the (potentially attacker-influenced, since it derives from the fetched repository's own conflicting content) conflict markers — silently overwrites the user's newer, real resolution.

### Impact Explanation
This corrupts exactly "what the user commits or pushes": the user's manually-fixed conflict resolution can be silently discarded and replaced by AI-generated content without any diff review, because the write path immediately follows with `pathsToStage.push(resolution.path)` and the file is `git add`ed: [4](#0-3) 
Since conflicted file content originates from a merged-in branch/remote that could be attacker-influenced (e.g. a malicious PR branch being merged), this creates a path where content the user believes they've overridden ends up staged and committed anyway, silently corrupting the final commit.

### Likelihood Explanation
The window requires processing more than one file in a single Copilot "Continue Merge" apply — a normal, expected usage pattern for real merge conflicts (the chunking logic explicitly anticipates dozens of files) — combined with the user editing/saving a different conflicted file while resolutions are still being written out. This is a realistic, unprivileged race that doesn't require local/admin access or malware; it only requires normal use of the Copilot conflict-resolution feature on an ordinary multi-file merge.

### Recommendation
Re-fetch `this.repositoryStateCache.get(repository)` (or at least the `workingDirectory.files` slice) immediately before the on-disk-file guard check inside the loop, rather than relying on the snapshot captured before the loop started, so every file's "already resolved externally" check reflects the true, current on-disk state at write time.

### Proof of Concept
1. Trigger a merge/rebase with several conflicted files and let Copilot generate resolutions for all of them (chunked in batches of ~15–20 files per `app/src/lib/stores/copilot-store.ts:1284-1343`).
2. Open the Copilot conflicts result dialog, then click "Continue Merge" to invoke `_applyCopilotConflictResolutions`.
3. While the loop is writing earlier files to disk (`app/src/lib/stores/app-store.ts:7196-7259`), manually edit and save one of the *later* files in `copilotResolutions` in an external editor, removing its conflict markers (mirroring the exact scenario the code comment says it guards against).
4. Because `state` was captured once at function entry (line 7172) and never refreshed, the guard at lines 7247-7256 still evaluates that later file against the pre-loop snapshot and does not detect the user's edit; `writeFile` overwrites the user's manual resolution with Copilot's stored content, which is then staged via `git add`, silently corrupting the file that will be committed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7169-7181)
```typescript
  public async _applyCopilotConflictResolutions(
    repository: Repository
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { copilotResolutions, step } = multiCommitOperationState
    if (copilotResolutions === null || copilotResolutions.length === 0) {
      return
    }
```

**File:** app/src/lib/stores/app-store.ts (L7233-7268)
```typescript
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
    }
```
