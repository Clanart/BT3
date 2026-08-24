### Title
Stale-state check in Copilot conflict-resolution write path can silently overwrite/stage a user's already-resolved merge conflict - (File: `app/src/lib/stores/app-store.ts`, function `_applyCopilotConflictResolutions`)

### Summary
`_applyCopilotConflictResolutions` decides whether it is safe to overwrite a conflicted file with AI-generated content by checking a single `IRepositoryState` snapshot captured once at function entry, then loops over every resolution performing `await writeFile(...)` and eventually `git add`. Just like the ThrusterTreasure `enterTickets()` bug — which gated a new action on a check of only one element (`winningTickets[currentRound_][0]`) while other, already-changed state (prizes at higher indices) was ignored — this function gates a batch of writes on one point-in-time read of `state.changesState.workingDirectory.files` and never re-validates it as the async loop progresses or before the actual disk write, even though the comment at [1](#0-0)  explicitly acknowledges the resolved-externally race it's supposed to prevent.

### Finding Description
The relevant code: [2](#0-1) [3](#0-2) 

`state` is fetched exactly once at line 7172 (`this.repositoryStateCache.get(repository)`). The loop then iterates every `resolution` in `copilotResolutions` and, for each one:
1. Resolves the path safely with `resolveWithin` (path-traversal is handled correctly here).
2. Looks up `onDiskFile` in the **same, function-entry** `state.changesState.workingDirectory.files` to decide if the conflict was already resolved outside Desktop (`hasUnresolvedConflicts`).
3. If the stale check doesn't flag it as resolved, `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` unconditionally overwrites the file, and the path is queued for `git add`.

Between the moment the Copilot result dialog appeared (when `state` was effectively frozen for the purposes of this check) and the moment the user clicks "Continue Merge" — which can be an arbitrarily long interval, since the whole point of the result dialog is for the user to review AI reasoning first — the user may resolve one or more conflicted files themselves (in an editor, via CLI `git checkout --ours/--theirs`, or by staging manually). None of that is reflected in the `state` object used by the loop, because it is captured a single time and never refreshed from live git status before the destructive `writeFile` call. This is the same class of bug as the C4 report: a check that only validates one, now-outdated slice of state (`winningTickets[...][0]` / a pre-loop `workingDirectory.files` snapshot) is used to gate an action against the current, evolved reality (prizes distributed for other indices / the file's on-disk resolution changing), and the guard added specifically to prevent this ("If the user resolved this file externally ... skip it and let their resolution stand") is defeated by its own staleness.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the AI-suggested resolution (whose content is derived from the conflicting file text/commit messages present in a cloned/fetched, potentially attacker-influenced branch) can clobber a conflict resolution the user already made and considered final, and that clobbered content is what gets staged via `git add` — and subsequently what the user commits and pushes — without any further diff review forcing the discrepancy to surface. Because the merge/rebase content that produces the conflict originates from the fetched remote branch, an attacker who crafts a branch guaranteed to conflict with sensitive files can increase the odds that Copilot's write silently reintroduces or preserves attacker-favored code even after the victim manually fixed it, since the stale check can no longer detect the user's intervening resolution.

### Likelihood Explanation
The window is realistic and not "unnatural": the Copilot conflict-resolution flow is explicitly designed to let the user review the AI's reasoning/summary in a dialog before confirming ("Continue Merge"), and it is a normal workflow for a developer to jump into their editor to manually finish resolving a stubborn conflict while that dialog is still open, or to run `git checkout --ours`/`--theirs` from a terminal in parallel. No admin rights, local malware, or leaked credentials are required — only ordinary use of Desktop's own AI conflict-resolution feature against a branch/repository that produces conflicts.

### Recommendation
Re-fetch (or re-derive) the working-directory/conflict status for each `resolution.path` immediately before calling `writeFile`, instead of relying on a `state` object captured once at function entry. At minimum, call `getStatus`/refresh the changes state right before the write loop begins, and ideally re-check per-file status just prior to each individual `writeFile` call so that any resolution the user completed during the (potentially long) Copilot review/confirmation window is correctly detected and skipped.

### Proof of Concept
1. Merge/rebase a branch that produces a conflict in `important-file.ts`.
2. Start Copilot conflict resolution; wait for the result dialog to appear (the `state` snapshot inside `_applyCopilotConflictResolutions` has not been captured yet — it is captured only when "Continue Merge" is later invoked, but the underlying repository state can still change between when the result dialog was populated and when the state cache is actually read).
3. While the result dialog is open, manually resolve `important-file.ts` in an external editor and save it (this is a completely valid resolution, e.g. keeping "ours").
4. Click "Continue Merge" before Desktop's background refresh has updated `repositoryStateCache` for the repository (or trigger the race by resolving the file at the same instant the confirm click fires).
5. `_applyCopilotConflictResolutions` reads a `state` in which `onDiskFile` for `important-file.ts` still reports it as an unresolved conflict (or the entry is simply absent from the stale snapshot), so the guard at [4](#0-3)  does not trigger, and `writeFile` overwrites the user's manual resolution with Copilot's suggested content, which is then staged via `git add` — silently corrupting the file the user will subsequently commit.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7169-7196)
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

    // Respect any manual overrides the user chose in the result dialog
    const manualResolutions =
      step.kind === MultiCommitOperationStepKind.ShowCopilotConflicts
        ? step.conflictState.manualResolutions
        : new Map<string, ManualConflictResolution>()

    this.statsStore.increment('copilotConflictResolutionAcceptedCount')
    if (manualResolutions.size > 0) {
      this.statsStore.increment('copilotConflictResolutionWithOverridesCount')
    }

    const pathsToStage: string[] = []

    for (const resolution of copilotResolutions) {
```

**File:** app/src/lib/stores/app-store.ts (L7233-7260)
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
```
