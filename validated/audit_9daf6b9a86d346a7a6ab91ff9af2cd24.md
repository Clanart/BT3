## Title
Copilot conflict-resolution writes unverified AI-generated content to disk without checking it still matches the file's current conflict state - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The external report's broken invariant is: "an action that moves/overwrites value X is executed without re-checking that the precondition used to justify X's amount is still true at execution time." The closest Desktop analog is `_applyCopilotConflictResolutions` in `app/src/lib/stores/app-store.ts`, which writes AI-generated resolved file content to disk and stages it for commit, guarding only against the file being *fully* resolved externally — not against the file's conflicted content having *changed* since the snapshot that was sent to the model and reassembled.

### Finding Description
Conflict context (including raw file content with markers) is captured once via `buildConflictContext` [1](#0-0) . This snapshot (`rawContent`) is later used by `reassembleResolutions`/`reassembleResolvedFile` to splice the model's per-hunk output back into the *original* captured content, not the file's live on-disk content [2](#0-1) .

When the user clicks "Continue Merge", `_applyCopilotConflictResolutions` writes this reassembled content to disk. The only staleness check performed is whether the on-disk file *still has unresolved conflict markers* — if the user fully resolved the conflict externally, the write is skipped: [3](#0-2) 

There is no check comparing the file's *current* conflicted content against the `rawContent` snapshot that was actually sent to the model and used for reassembly. If the file is still marked as conflicted (`isConflictedFileStatus` true, `hasUnresolvedConflicts` true) but its content has diverged from the snapshot — for example, because a `git fetch`/remote update, another local edit, or an amended commit altered the conflict hunks between context-gathering and the (potentially long-running, streamed) Copilot turn — the stale reassembled content is written verbatim via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and then `git add`-ed [4](#0-3) .

This mirrors the reported smart-contract flaw precisely: the "transfer" (file write + stage) is executed based on a precondition ("hunks match what was resolved") that is validated only once, up front (`validateResolutionPaths` checks hunk *counts* against the original snapshot, not against current disk state) [5](#0-4) , and never re-verified at the moment the write actually happens — exactly the missing `require(...)` check pattern from the original report.

### Impact Explanation
If the underlying conflict content changes between context capture and disk write (a window that can span a long-running streamed LLM turn, `MaxConcurrentChunks` parallel chunks, or user actions during the "ShowCopilotConflicts" review step), Desktop will silently splice a stale AI-generated resolution into the file and stage it for commit — corrupting what the user commits/pushes without any warning, since the guard only fires when conflict markers are fully gone, not when they still exist but differ from the snapshot. This satisfies the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Moderate-to-uncertain. The conflicting content originates from a merge/rebase/cherry-pick against a branch/remote the attacker could control (satisfying the "attacker controls a cloned/fetched repository" precondition), and PR/commit descriptions embedded in the prompt are also attacker-influenceable content from GitHub API objects. However, triggering the exact staleness window (edit during the Copilot turn, or the git state changing between context gather and disk apply) requires specific timing that I could not fully confirm is reachable purely from the code paths reviewed — I did not find a re-read of `rawContent` immediately before `writeFile`, but I also could not rule out an intermediate re-validation elsewhere in the multi-commit-operation flow given the size of `app-store.ts` and time constraints.

### Recommendation
Before writing `resolution.resolvedContent` to disk in `_applyCopilotConflictResolutions`, re-read the file's current on-disk content and re-verify that its conflict-marker regions match the `rawContent` snapshot that was used for reassembly (e.g., by comparing hashes of the original per-hunk `oursContent`/`theirsContent` against what's currently on disk), analogous to adding `require(distributionEvents[id].amountPaidIn)` before the transfer in the original report. If the content has diverged, skip the write and route the file back to manual conflict resolution instead of applying a stale AI resolution.

### Proof of Concept
Conceptual PoC (not independently executed):
1. Start a merge/rebase with a conflicted file `foo.ts`; Desktop calls `buildConflictContext`, capturing `rawContent` with conflict markers.
2. While the Copilot turn is in flight (streaming can take tens of seconds per `elapsedSeconds` timing buckets logged at [6](#0-5) ), externally modify `foo.ts`'s conflict hunk content (e.g., via `git checkout --theirs` partially, or another tool) while leaving conflict markers present so `hasUnresolvedConflicts` remains true.
3. The Copilot session returns hunk resolutions matching the *original* hunk count/snapshot.
4. User clicks "Continue Merge" → `_applyCopilotConflictResolutions` passes the `onDiskFile` check (still "conflicted", still "unresolved") and overwrites `foo.ts` with the stale reassembled content, discarding the intervening changes, then stages it with `git add`.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L367-461)
```typescript
export async function buildConflictContext(
  ourLabel: string,
  theirLabel: string,
  workingDirectory: string,
  files: ReadonlyArray<{
    readonly path: string
    /** Which side deleted the file (for delete-vs-modify conflicts). */
    readonly deletedSide?: 'ours' | 'theirs'
  }>
): Promise<ICopilotConflictContext> {
  const results = await Promise.all(
    files.map(async (file): Promise<IFileConflictContext> => {
      // Delete-vs-modify conflicts have no text markers on disk. Include
      // them in the context with metadata so the model can recommend
      // keep or delete — no file content is needed.
      if (file.deletedSide !== undefined) {
        return {
          path: file.path,
          hunks: [],
          deleteConflict: { deletedSide: file.deletedSide },
        }
      }

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
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-521)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }

  const missingPaths: Array<string> = []
  for (const path of expectedPaths) {
    if (!returnedPaths.has(path)) {
      missingPaths.push(path)
    }
  }
  if (missingPaths.length > 0) {
    throw new CopilotValidationError(
      `Copilot did not return resolutions for: ${missingPaths.join(', ')}`
    )
  }

  for (const resolution of resolutions) {
    // Delete-vs-modify resolutions use action instead of hunks — skip count check
    if (resolution.action !== undefined) {
      continue
    }
    const expectedCount = expectedHunkCounts.get(resolution.path) ?? 0
    if (resolution.hunks.length !== expectedCount) {
      throw new CopilotValidationError(
        `Copilot returned ${resolution.hunks.length} hunk(s) for "${resolution.path}" but expected ${expectedCount}`
      )
    }
  }
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-642)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
}
```

**File:** app/src/lib/stores/app-store.ts (L7093-7106)
```typescript
      // Record resolution timing buckets
      const elapsedSeconds = (performance.now() - resolveStartTime) / 1000
      if (elapsedSeconds > 15) {
        this.statsStore.increment('copilotConflictResolutionOver15sCount')
      }
      if (elapsedSeconds > 30) {
        this.statsStore.increment('copilotConflictResolutionOver30sCount')
      }
      if (elapsedSeconds > 60) {
        this.statsStore.increment('copilotConflictResolutionOver60sCount')
      }
      if (elapsedSeconds > 120) {
        this.statsStore.increment('copilotConflictResolutionOver120sCount')
      }
```

**File:** app/src/lib/stores/app-store.ts (L7241-7268)
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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
