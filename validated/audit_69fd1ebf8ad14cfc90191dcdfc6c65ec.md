### Title
Stale Copilot conflict resolutions can be silently spliced into changed on-disk content, corrupting committed data - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`_applyCopilotConflictResolutions` writes AI-generated conflict resolutions to disk by re-splicing per-hunk content into whatever file currently sits on disk, matching hunks **by order, not by content or line number**. The only staleness check performed is whether the file is *still marked as conflicted*; there is no check that the file's conflict-hunk layout is the same one the model actually analyzed. If the on-disk conflicted content changes between the time Copilot builds its resolution (which can take up to and beyond 120 seconds per the app's own telemetry buckets) and the time the user clicks "Continue Merge", the reassembly step will still overwrite the file with content built from a stale hunk mapping — producing output with no conflict markers that the user will unknowingly commit or push.

### Finding Description
`buildConflictContext` (app/src/lib/copilot-conflict-context.ts:367-469) reads each conflicted file off disk and extracts conflict hunks at time *T1*, before sending them to the Copilot backend. [1](#0-0) 

The resolution flow can run for a long time — the app tracks buckets up to and beyond 120 seconds of elapsed resolution time — after which the user reviews the result and clicks "Continue Merge": [2](#0-1) 

When the user confirms, `_applyCopilotConflictResolutions` (time *T2*) re-reads the working-directory file status only to check whether it is *still conflicted*, then unconditionally calls `reassembleResolvedFile` against **whatever raw content is currently on disk**: [3](#0-2) 

`reassembleResolvedFile` explicitly documents that hunks are matched "by order, not by line number", assuming the original file (read at T1) and the file being reassembled (T2) contain the exact same conflict-hunk sequence: [4](#0-3) 

The existing guard only protects against the case where the *user* manually resolved the file (no more markers at all): [5](#0-4) 

It does **not** protect against the case where the file is *still* conflicted but its content/hunk layout has changed — e.g., because the underlying operation (rebase/cherry-pick applying multiple commits, or a fetch that updates a shared/tracking ref mid-operation) caused Git to regenerate the conflict markers for that path with a different number or ordering of hunks than what the model saw. There is no hash, size, or hunk-count comparison between the context that was sent to Copilot and the file being patched at apply time.

### Impact Explanation
This breaks the invariant that "what the AI reviewed is what gets written." A file can be overwritten with content assembled from mismatched hunk boundaries with zero conflict markers remaining, so the working tree looks fully resolved. The user is very likely to trust the AI-authored result and commit/push it. This falls squarely under "silent corruption of what the user commits or pushes" in the valid-impact list — it doesn't require local/physical access, admin rights, or prior malware; it only requires that the conflicted file's content differ between the two points in time the flow reads it, which is plausible in a multi-commit operation (rebase/cherry-pick applies several commits sequentially, potentially re-touching the same path) or when remote state changes during a long-running AI call.

### Likelihood Explanation
Requires a specific timing window (T1 read vs T2 write) and a scenario where the same path is reconflicted with a different hunk shape in between — realistic during multi-commit rebases/cherry-picks that touch the same file across several steps, or slow Copilot responses (the app itself buckets resolution latency past 120s, indicating this is not a rare edge case). No special user action beyond the normal "Resolve with Copilot" → "Continue Merge" flow is needed, and no additional confirmation ties the applied resolution to the specific file state it was generated from.

### Recommendation
Before reassembly, verify that the on-disk conflicted content at apply time matches (e.g., via a hash of the raw content, or hunk markers/boundaries) what was captured in `buildConflictContext` for that file. If it differs, treat the resolution as stale, skip auto-apply, and require the user to re-run resolution (or fall back to manual resolution) for that file rather than silently splicing hunks positionally.

### Proof of Concept
1. Start a multi-commit operation (rebase) across several commits that repeatedly modify the same file, causing conflicts at multiple steps.
2. Trigger "Resolve with Copilot" for the first conflicting step; the app builds `buildConflictContext` from the file's current hunks and sends them to the model.
3. While the (potentially slow, 15–120s+) Copilot request is in flight, let the underlying git operation state for that file change such that when the user returns to the result dialog, the file is still marked conflicted but its hunk structure has shifted (e.g., due to sequential commit application touching that path again).
4. Click "Continue Merge." `_applyCopilotConflictResolutions` only checks `isConflictedFileStatus`/`hasUnresolvedConflicts`, passes the check, and `reassembleResolvedFile` splices the stale, positionally-matched hunk resolutions into the new raw content — producing a marker-free but incorrectly merged file that gets staged and committed.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-441)
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
```

**File:** app/src/lib/stores/app-store.ts (L7093-7107)
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
    } catch (e) {
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-538)
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
```
