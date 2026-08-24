Given the constraints of this environment, I've reached the point where I can identify the closest structural analog to the reported issue: a **time-of-check-to-time-of-use (TOCTOU) gap between when a computed value is derived from repository state and when it is committed to disk**, where the guard checking "is this still valid?" is too coarse to catch drift, leading to silent corruption of what is written to the repository.

### Title
Stale Copilot merge-conflict resolutions can silently overwrite differently-resolved file content because the "already resolved" guard checks conflict status kind, not content - (File: app/src/lib/stores/app-store.ts)

### Summary
`_applyCopilotConflictResolutions` writes AI-generated conflict resolutions (`resolution.resolvedContent`) to disk based on conflict hunks/content captured earlier during `buildConflictContext`. Before writing, it only checks whether the file's `AppFileStatusKind` is still "conflicted with unresolved markers" — it never re-verifies that the on-disk content still matches what the resolution was computed from.

### Finding Description
The write path is:
1. `buildConflictContext` reads working-directory file content once (`app/src/lib/copilot-conflict-context.ts:429-460`) and extracts conflict hunks to send to the model. [1](#0-0) 
2. Later, when the user clicks "Continue Merge," `_applyCopilotConflictResolutions` iterates the stored `copilotResolutions` and, for each file, only skips the write if the file's status is no longer conflicted at all: [2](#0-1) 
3. If the on-disk file still reports as "conflicted" (e.g., the user partially edited it in an external editor, a merge/smudge driver touched it, or any other process modified it, but git still shows conflict markers/unresolved status), the stale `resolution.resolvedContent` computed from the earlier snapshot is written over the current content and staged with `git add`, with no diff/hash comparison against the current file.

This mirrors the reported class of bug precisely: a value (`resolvedContent`) computed from state at time T1 is used unconditionally at time T2 if a coarse-grained guard (`hasUnresolvedConflicts`, analogous to the on-chain "is this still bad debt" check) still returns true, even though the underlying value it was derived from has since drifted. Just as `calcTotalDebt` increasing between calculation and execution silently breaks the "pay off exactly" invariant, the file content changing between context-build and apply-time silently breaks the "commit exactly what the user resolved" invariant.

### Impact Explanation
The corrupted value here is the committed file content itself: a user's own in-progress manual resolution (or any external tool's edits) to a conflicted file can be silently discarded and replaced by outdated AI-generated content, which then gets staged and committed/pushed without further prompt, since `pathsToStage` is fed directly into `git add`. This is exactly the "silent corruption of what the user commits or pushes" impact category, and there is no secondary confirmation dialog re-showing the diff that will actually be written.

### Likelihood Explanation
This requires the user to actively use the Copilot conflict-resolution feature during a merge/rebase with unresolved conflicts and to touch the same file (or have some other process touch it) between the AI's analysis and clicking "Continue Merge." It's a race that depends on user/timing behavior rather than a pure remote-attacker primitive, similar to how the original report's window depends on transaction propagation delay rather than direct attacker control — the guard exists but is insufficient, and no on-disk verification closes the gap.

### Recommendation
Before writing `resolution.resolvedContent`, re-read the current on-disk content (or compare against the `oldContents`/hash captured when the resolution was generated, similar to what `getResolutionDiff` already computes) and only proceed if it's unchanged from what the resolution was based on. If it changed, skip the write (same as the "already resolved" case) and surface this to the user instead of silently overwriting.

### Proof of Concept
1. Start a merge/rebase in Desktop that produces conflicts in `file.txt`.
2. Use "Resolve with Copilot" to generate a resolution; do not click "Continue Merge" yet.
3. While the result dialog is open, manually edit `file.txt` in an external editor to a different (but still conflict-marker-containing, e.g. partially resolved) state.
4. Click "Continue Merge."
5. Observe: `_applyCopilotConflictResolutions` (app/src/lib/stores/app-store.ts:7169-7269) overwrites the manual edits with the stale `resolvedContent` because `hasUnresolvedConflicts(onDiskFile.status)` still returns true, and stages the overwritten file for commit. [2](#0-1) [1](#0-0)

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-447)
```typescript
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
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
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
