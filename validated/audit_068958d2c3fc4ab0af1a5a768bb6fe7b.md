### Title
Copilot conflict resolution silently overwrites conflicted files with a stale snapshot, discarding intervening edits - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/copilot-conflict-resolution.ts`, `app/src/lib/copilot-conflict-context.ts`)

### Summary
The Hyperdrive report's root cause is a guard that is evaluated against a single stale snapshot (the "latest checkpoint") instead of the live state at the moment of the guarded action, leaving a window in which an attacker-influenced state change is not accounted for. GitHub Desktop's Copilot merge-conflict auto-resolution flow has the same structural defect: the file content used to reconstruct the final resolved file (`rawContent`) is captured once, before an asynchronous, potentially long-running LLM call, and is never re-validated for content-level staleness before being written back to disk. The only staleness check present (`hasUnresolvedConflicts`) detects only the case where conflict markers are *completely gone*; it does not detect the case where the file still contains conflict markers but its content differs from the snapshot that was sent to the model. As a result, an attacker who can craft a merge/rebase/cherry-pick source (a remote branch, PR, or repository content the user merges) that produces many/large conflicts can force a long resolution window, during which the reconstructed content silently clobbers whatever is on disk when `writeFile` executes — without re-reading or diffing current disk state against the snapshot used for hunk placement.

### Finding Description
`buildConflictContext` in `app/src/lib/copilot-conflict-context.ts` reads each conflicted file exactly once at the start of the flow: [1](#0-0) [2](#0-1) 

That `rawContent` snapshot — the "checkpoint" — is stored and later used, after the (async, batched, possibly slow) model call returns, to splice the model's per-hunk resolutions back into the file by **positional/ordinal matching only**, explicitly not by re-reading or diffing the current on-disk content: [3](#0-2) [4](#0-3) 

The write-back path in `app-store.ts` re-validates the target path (`resolveWithin`) and checks whether the on-disk file's *status* still shows unresolved conflicts, but this check only distinguishes "fully resolved externally" vs. "still conflicted" — it does not compare the current disk bytes against `ctx.rawContent` used for reassembly: [5](#0-4) 

The comment at that call site even acknowledges the narrow scope of the guard ("If the user resolved this file externally... git status will report it with no remaining conflict markers... skip it"), confirming that any state change short of *complete* resolution is not detected — exactly the gap the Hyperdrive report calls out: the guard is evaluated against one stale reference point and doesn't account for movement that happens inside the window between snapshot and enforcement.

This mirrors the `_addLiquidity` circuit breaker: there, `weightedSpotAPR` of the "latest checkpoint" is stale relative to trades executed milliseconds before enforcement; here, `rawContent`/hunk positions are stale relative to any file mutation that happens during the LLM round-trip, and the only staleness check implemented catches a single specific case (full external resolution) rather than the general one.

### Impact Explanation
If the on-disk conflicted file changes between the snapshot read and the resolution write — because the working tree is mutated by another process/tool while the (batched, multi-file, potentially minutes-long for `SinglePromptFileLimit = 20` files across `MaxConcurrentChunks = 5` chunks) Copilot call is in flight — the final `writeFile` call unconditionally overwrites the file using positions computed from the stale snapshot. This falls into the explicitly valid impact category of "silent corruption of what the user commits or pushes": the write is unconditional (`await writeFile(absolutePath, resolution.resolvedContent, 'utf8')`) and is immediately staged (`git add`), so the corrupted content can be committed and pushed without further review, especially since the flow is designed to auto-apply AI resolutions with minimal human re-diffing.

### Likelihood Explanation
The window is realistically sized: LLM resolution is inherently network-bound and can take seconds; the code explicitly designs for multi-file, multi-chunk batches (`SinglePromptFileLimit`, `MaxConcurrentChunks`), meaning the largest, most complex merges — which are also the ones most likely to be steered by a hostile branch/PR the user is merging — have the longest exposure window. The severity/likelihood, like the Hyperdrive finding, depends on repository/merge configuration (number and size of conflicts) and is not a guaranteed hit on every merge, which matches the original report's caveat that severity "may vary depending on ... configuration."

### Recommendation
Before writing the reassembled content, re-read the file from disk at write time and verify it is byte-identical to `ctx.rawContent` (the snapshot used for reassembly); if it differs, skip the automated write and route the file back into manual conflict resolution (the same fallback already used for "resolved externally" and for `absolutePath === null`). This closes the gap generically instead of only for the fully-resolved special case, analogous to widening the Hyperdrive circuit breaker to consider more than just the single latest checkpoint.

### Proof of Concept
1. Trigger a merge/rebase with a crafted incoming branch that produces several conflicted files with `SinglePromptFileLimit`-scale content, sized so the Copilot resolution call takes several seconds.
2. While the "Resolving conflicts with Copilot" operation is in flight, have any other process/tool (e.g., an editor auto-format extension, a build tool, or a second Desktop-driven git operation) modify one of the still-conflicted files on disk (content edit that keeps conflict markers, e.g., partially resolving one hunk manually) — `isConflictedFileStatus` remains true and `hasUnresolvedConflicts` remains true, so the app-store staleness guard at `app/src/lib/stores/app-store.ts:7250-7256` does not skip the file.
3. When the Copilot response returns, `reassembleResolutions`/`reassembleResolvedFile` splice the model's hunks into the original (`ctx.rawContent`) snapshot, and `writeFile` at `app/src/lib/stores/app-store.ts:7258` overwrites the file, discarding the intervening manual edit without warning, then stages it for commit via `git add`. [6](#0-5)  shows the guard is designed only to catch the fully-resolved case, and [7](#0-6)  confirms reassembly is purely positional against the stale snapshot with no re-validation against current disk content.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-438)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L460-461)
```typescript
      return { path: file.path, hunks, rawContent: content }
    })
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-551)
```typescript
/**
 * Reassemble a fully resolved file by splicing per-hunk resolutions into
 * the original file content (which still has conflict markers on disk).
 *
 * Walks the original file line-by-line. Non-conflicted lines are copied
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
 *
 * A `<<<<<<<` line that is not followed by both a `=======` separator and
 * a closing `>>>>>>>` before EOF is treated as regular file content (not a
 * conflict block) and copied through unchanged to avoid data loss from
 * malformed or stray markers.
 *
 * @param rawContent - The full file content on disk, including conflict markers
 * @param hunkResolutions - Per-hunk resolved content, in the order they appear in the file
 * @returns The reassembled file with all conflicts resolved
 */
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
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
```

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
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
```
