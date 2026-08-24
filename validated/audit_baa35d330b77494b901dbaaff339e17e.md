### Title
Stale conflict-status snapshot lets attacker-controlled merge content bypass the "don't clobber externally resolved file" guard in Copilot conflict resolution - (File: app/src/lib/stores/app-store.ts)

### Summary
`_applyCopilotConflictResolutions` writes Copilot's AI-generated resolution content over each conflicted file in the working tree. It guards against clobbering a file the user has already fixed by hand by checking a single `state` snapshot captured once at function entry, then looping asynchronously over every file, awaiting `resolveWithin` and `writeFile` for each. The correctness of the whole write path silently depends on that one stale status snapshot remaining valid for every iteration.

### Finding Description
The function takes a single snapshot of repository state up front: [1](#0-0) 

and then, for every resolution, decides whether to skip writing based on that same unrefreshed snapshot: [2](#0-1) 

The comment at line 7241 makes the intent explicit: "If the user resolved this file externally … while the result dialog was open, git status will report it with no remaining conflict markers … Overwriting it with Copilot's stored content would silently clobber their work, so skip it." The guard, however, only fires when `onDiskFile` is still present in the *original* `workingDirectory.files` list **and** is still flagged `isConflictedFileStatus` **and** `hasUnresolvedConflicts` returns false. Because `state` is fetched once before the `for` loop, and the loop `await`s a `resolveWithin` (which itself performs `realpath` filesystem I/O) and a `writeFile` per file, any resolution that appears later in `copilotResolutions` is checked against filesystem/status information that is now out of date relative to what's actually happened on disk during the earlier iterations of the same loop. The invariant broken is the same class as CVE-2022-27778: the code computes a decision (which file is safe to touch) against one piece of state, then acts using state that has since diverged, and the guard that is supposed to prevent an unwanted side effect is bypassed on exactly that divergence, not on the common case.

### Impact Explanation
If the guard is bypassed, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` overwrites a file the user has already hand-resolved during a merge/rebase/cherry-pick, and the corrupted content is subsequently staged via `git add` in the same function. This is a silent-corruption-of-what-the-user-commits scenario: the user believes their manual resolution (or an accepted AI resolution reviewed in the dialog) is what gets committed, but the file that actually lands in the index/commit differs from what they saw, with no error surfaced.

### Likelihood Explanation
Exploiting this reliably requires a timing window that is hard to control deterministically from a purely remote/repo-content angle — the attacker (who controls the conflicting merge content) cannot directly cause the user to edit files mid-loop. I could not fully verify from static analysis alone whether `repositoryStateCache` is refreshed by a concurrent background status poll during the `_applyCopilotConflictResolutions` call (which would make the window realistically triggerable) or whether it is fully static until `_refreshRepository` is explicitly invoked afterward. This materially affects likelihood, and I was not able to confirm it within the available search budget.

### Recommendation
Re-fetch (or re-derive) the on-disk conflict status for each file immediately before deciding whether to write it, rather than relying on a single snapshot taken before an async loop that itself performs multiple filesystem operations per file. At minimum, re-check `hasUnresolvedConflicts` against a fresh `readFile`/`git status` result for that specific path right before the `writeFile` call in each iteration.

### Proof of Concept
Not fully constructible from static review alone: reproducing this requires confirming whether `this.repositoryStateCache.get(repository)` can reflect updated working-directory status mid-loop (e.g., via a background poller) while `_applyCopilotConflictResolutions` is executing, which I was unable to verify with the tools available. Given this open question, confidence in this being a concretely exploitable issue (rather than a purely theoretical one) is limited.

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

**File:** app/src/lib/stores/app-store.ts (L7241-7259)
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

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
