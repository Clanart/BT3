### Title
Copilot conflict-resolution auto-write uses a stale working-directory snapshot to decide whether a file is "still safe to overwrite" - ([File: app/src/lib/stores/app-store.ts])

### Summary
The Copilot merge-conflict resolution flow validates whether a conflicted file is still safe to auto-write (i.e. not already resolved by the user) against a working-directory status snapshot that was captured before the (long-running) AI round trip, then uses that stale check result to decide whether to overwrite the file with AI-generated content. This mirrors the M-12 pattern: a solvency/safety check is performed against an "old" value while the operation it gates is executed against the "adjusted"/current value, so the check can pass even though the real, current state would have failed it.

### Finding Description
`buildConflictContext` reads each conflicted file's content from disk and hands it to the Copilot SDK to generate a resolution <cite repo="Annirich/desktop--005" path="app/src/lib/copilot-conflict-context.ts" start="429="438" end="438" />, a step that necessarily performs a network/LLM round trip and can take a non-trivial amount of wall-clock time.

When the resolutions come back, the apply step in `app-store.ts` (around the handler that iterates `copilotResolutions`) starts by grabbing a single snapshot of repository state: [1](#0-0) 

For each resolution it re-derives the safe on-disk path with `resolveWithin` (good, path traversal is handled) [2](#0-1) , but the "is it still safe to overwrite" check is performed against `state.changesState.workingDirectory.files`, i.e. the same snapshot captured at line 7172, before the loop's own writes and well after the (potentially long) AI generation delay: [3](#0-2) 

Only then does it perform the actual write with the AI-authored content: [4](#0-3) 

The invariant this code intends to enforce is: "never blindly overwrite a file whose conflicts the user has already resolved by hand." The guard comment even states this explicitly (lines 7241-7246). But the check is evaluated against `state`, a `repositoryStateCache` read taken once at function entry — not a fresh `git status`/`workingDirectory` read taken immediately before each write. Everything that happened between (a) the original `buildConflictContext` read that was sent to the model, and (b) the write in this loop — including the user editing/saving the file, running `git checkout --ours/--theirs`, or another background operation touching the same repository — is invisible to this check unless a status refresh happened to land in the cache in between. Exactly like the Solidity bug ("insolvency check performed against old locked values instead of the adjusted/actual locked values"), the decision here is made against a value (`state`) that predates the real, current on-disk state that will actually be mutated.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the AI-authored resolution can be written over a file whose true current content differs from what the stale snapshot implied, silently discarding conflict resolution the user performed manually (in their editor, via `git checkout --ours/--theirs`, or by resolving via a different tool) during the AI round trip. The corrupted result is then staged via `git add` and can end up committed [5](#0-4)  without the user necessarily noticing that their own edits were clobbered — the class of impact explicitly called out as valid ("silent corruption of what the user commits or pushes").

### Likelihood Explanation
This requires no local/physical access, no malware, and no unnatural user steps: it only requires a normal user workflow (start Copilot conflict resolution on a repository with genuine or attacker-crafted merge conflicts, edit a conflicted file while waiting for the AI, then accept the resulting suggestions) combined with a repository whose conflicting content is attacker-influenced (e.g., a maliciously crafted fork/branch merged in). The race window is bounded by the AI response latency, which can be several seconds, making the window realistic rather than theoretical.

### Recommendation
Re-fetch the current working-directory status (or at minimum re-stat/re-read the specific file) immediately before each write in the loop, rather than relying on the `state` snapshot captured at function entry, so the "already resolved externally" check is evaluated against the actual current on-disk content at write time — analogous to performing the insolvency check against the adjusted locked values rather than the stale ones.

### Proof of Concept
1. Trigger a merge/rebase/cherry-pick that produces conflicts in Desktop and start "Resolve with Copilot."
2. While the AI request is in flight (`buildConflictContext` → Copilot SDK round trip), manually resolve the conflicted file yourself in an external editor and save it (or run `git checkout --ours -- file` from a terminal), producing content you intend to keep.
3. When the Copilot response returns, accept its resolutions. Because `state` was captured at line 7172 before your manual edit occurred, and no fresh status check is performed for that specific file before line 7258's `writeFile`, the flow either treats the file as still-conflicted (missing the fact you already resolved it) and overwrites your manually resolved content with the AI's — or, depending on exact snapshot timing/status refresh cadence, silently discards your changes.
4. `git add`/staging of `pathsToStage` then means the corrupted content is what gets committed.

Note: I could not fully trace whether `repositoryStateCache` receives an intermediate refresh during the Copilot round trip in all code paths (e.g., via file-system watcher events), which would narrow or widen the exploitable window; this would need further verification with a background Devin session that can run the app and instrument the exact timing.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7171-7178)
```typescript
  ): Promise<void> {
    const state = this.repositoryStateCache.get(repository)
    const { multiCommitOperationState } = state
    if (multiCommitOperationState === null) {
      return
    }

    const { copilotResolutions, step } = multiCommitOperationState
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

**File:** app/src/lib/stores/app-store.ts (L7247-7256)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
