## Analysis

This maps cleanly onto the reported bug class: a value is snapshotted once, then an async loop with several `await` points runs, and a safety check that's supposed to re-validate current on-disk/state right before a destructive action instead consults the stale snapshot. In the original report, `bounty` is fetched before `_checkForUpgrade()` can run; here, `state` (containing `workingDirectory.files`) is fetched once at function entry and never refreshed while the function performs several `await`s per file (`resolveWithin`, `writeFile`).

### Title
Stale working-directory snapshot lets Copilot conflict-resolution writes silently clobber a user's manual conflict fix - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_applyCopilotConflictResolutions` is invoked when the user clicks "Continue Merge" after reviewing AI-suggested conflict resolutions [1](#0-0) . It takes a single snapshot of `repositoryStateCache` at the top of the function and then loops over every conflicted file, doing an `await resolveWithin(...)` and `await writeFile(...)` per file [2](#0-1) . Before writing, it checks the *same, unrefreshed* `state.changesState.workingDirectory.files` snapshot to detect whether the user already resolved the file externally (e.g. in their editor), specifically to avoid overwriting the user's own fix [3](#0-2) . Because this check never re-reads current status between/after the `await` points in the loop, any status change that happens concurrently with this multi-`await` operation (a user finishing/undoing a manual resolution in their editor, or the working-directory status refreshing mid-loop) is invisible to the check for files later in the loop, or even for the same file if the surrounding operation yields the event loop.

### Finding Description
The root cause is the classic "check happens against data captured before the async work, not data current at the time of the destructive write" pattern. `state` is captured once at line 7172 [4](#0-3) , and used for every iteration of the `for (const resolution of copilotResolutions)` loop, even though each iteration performs `await`s that yield control back to the JS event loop (path resolution and disk I/O) [2](#0-1) . The comment at lines 7241-7246 explicitly acknowledges the intended invariant — "if the user resolved this file externally... overwriting it with Copilot's stored content would silently clobber their work, so skip it" — but the implementation validates that invariant against a point-in-time snapshot rather than the live repository state at write time [3](#0-2) . There is no re-fetch of `repositoryStateCache` or a fresh `git status` call inside the loop before each `writeFile`, so the guard is only as fresh as the moment the function was entered, not the moment each individual file is about to be overwritten.

### Impact Explanation
The conflicted content that seeds the Copilot resolution originates from a merge/rebase/cherry-pick against a fetched branch or remote that an attacker can fully control (the "theirs" side of the conflict). If the attacker crafts a conflicting hunk designed to produce an undesirable but plausible-looking resolution from the model, and the user notices something wrong and manually fixes the file in their editor while the "Continue Merge" write loop is executing (a plausible window since the loop performs disk I/O for potentially many files), the stale-state check can fail to detect the user's fix for files it hasn't yet reached in the loop, and the user's correction is silently overwritten by the attacker-influenced Copilot content, which is then `git add`-ed and becomes part of what the user commits/pushes [5](#0-4) . This is a silent corruption of what the user commits, matching the "attacker controls a fetched repository... result is silent corruption of what the user commits or pushes" impact class — no elevated privileges, malware, or unnatural user steps are required beyond normal use of the Copilot conflict-resolution feature during a merge from an untrusted branch.

### Likelihood Explanation
The race window is real but narrow: it requires the user to interleave a manual edit with the multi-file write loop that runs after clicking "Continue Merge." Because the loop performs sequential disk writes for every conflicted file (each an `await` yield point), the window grows with the number of conflicted files, making it more exploitable in larger/multi-file conflict resolutions or slower disks. This is a moderate-likelihood, developer-not-attacker-triggered race, but it directly reflects the report's core bug class of "safety check reads stale state across an await boundary."

### Recommendation
Re-validate the on-disk/conflict status immediately before each `writeFile`, not once at function entry — either re-read `repositoryStateCache.get(repository).changesState.workingDirectory.files` (or run a fresh `git status`) inside the loop right before deciding whether to write each file, or perform the check-and-write atomically per file so no `await` gap exists between validating "still conflicted" and writing the resolution.

### Proof of Concept
1. Fetch/merge a branch controlled by an attacker that creates conflicts in multiple files (`fileA`, `fileB`, ...).
2. Run "Resolve with Copilot"; when the result dialog appears, click "Continue Merge" to trigger `_applyCopilotConflictResolutions`.
3. While the write loop is iterating (e.g., before it reaches `fileB`), manually resolve `fileB` correctly in an external editor (removing the attacker's malicious/undesired content) so git status no longer reports unresolved conflict markers for `fileB`.
4. Because `state` was snapshotted before the loop began, the check at lines 7247-7256 still sees `fileB` as containing unresolved conflicts (stale), so `writeFile` overwrites the user's manual fix with the Copilot-generated (attacker-influenced) content, which is then staged via `git add` [5](#0-4) .

### Citations

**File:** app/src/lib/stores/app-store.ts (L7162-7181)
```typescript
  /**
   * Write Copilot-resolved file contents to disk and stage them.
   * Called when the user clicks "Continue Merge" from the Copilot conflicts
   * result dialog.
   *
   * This shouldn't be called directly. See `Dispatcher`.
   */
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

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
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
```
