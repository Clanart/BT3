### Title
Copilot conflict resolution writes back a stale in-memory file snapshot, silently discarding on-disk changes made during the (multi-minute) AI round trip - ([File: app/src/lib/stores/app-store.ts])

### Summary
GitHub Desktop's "Resolve with Copilot" merge/rebase/cherry-pick conflict flow captures the full content of each conflicted file once, before sending it to the Copilot API, and then — after an asynchronous round trip that the app itself instruments as sometimes exceeding 120 seconds — splices the model's suggested hunks into that *original, captured* content and overwrites the file on disk. The only re-validation performed against the live working directory is a coarse "does this file still show as unresolved-conflicted" check. Nothing re-reads the file's current bytes or diffs them against what was captured before overwriting it. Any change made to the file's on-disk content during the wait — while conflict markers are still present — is silently discarded when the user clicks "Continue Merge."

### Finding Description
The flow is:

1. `buildConflictContext` reads each conflicted file from disk and stores its full text in `IFileConflictContext.rawContent`: [1](#0-0) 

2. That snapshot is handed off to the Copilot SDK, and the app waits for a full model turn. The code explicitly tracks and buckets runs that take over 15/30/60/120 seconds, confirming this is a long-lived, unattended asynchronous window: [2](#0-1) 

3. When the model responds, `reassembleResolutions` splices the model's per-hunk output into the **originally captured** `ctx.rawContent` — not into a freshly read copy of the file — producing the final `resolvedContent`: [3](#0-2) 

4. When the user confirms ("Continue Merge"), `_applyCopilotConflictResolutions` writes that `resolvedContent` straight to disk. The *only* freshness check performed is whether the file's git status still shows unresolved conflict markers (`isConflictedFileStatus` + `hasUnresolvedConflicts`), evaluated against a `state` object captured at the very top of the function, before any of the per-file `resolveWithin`/`writeFile` awaits: [4](#0-3) 

The invariant that's supposed to hold — "what we write to disk reflects the file's current content, only with the model's hunks applied" — is broken the same way the report's `assetBalance` invariant was broken: intermediate/asynchronous steps (the LLM round trip) are allowed to complete and mutate the tracked value (`resolvedContent`) based on a value (`rawContent`) that was fixed before a long window during which the *real* state (the file on disk) can diverge, and the final write path has no check that would detect that divergence — the `hasUnresolvedConflicts` check only inspects whether conflict-marker syntax is still present, not whether the underlying bytes changed.

### Impact Explanation
If anything modifies the conflicted file on disk after `buildConflictContext` runs but before the user clicks "Continue Merge" — while conflict markers remain present so the coarse guard doesn't trip — that modification is silently and irreversibly overwritten with content derived from the stale snapshot when Desktop writes and stages (`git add`) the file. Because the write happens transparently as part of confirming the merge, the user has no indication their intervening edits (or edits injected by tooling bundled in the repository, e.g. build scripts, formatters-on-save integrations, or `.gitattributes` clean/smudge filters that run during git operations in this same working tree) were dropped. This is exactly the class of impact called out as valid: silent corruption of what the user ultimately commits/pushes, sourced from an untrusted, fetched repository's conflicting branch content and the multi-minute unattended window the AI flow introduces.

### Likelihood Explanation
This requires a mechanism to touch the conflicted file's bytes during the Copilot wait without removing the conflict markers Desktop checks for (e.g., an editor auto-save, a lint/format-on-save hook, a build tool, or a git clean/smudge filter shipped in the malicious/fetched repository and configured to run during the merge). This is plausible in normal developer workflows (opening the conflicted file in an editor while waiting) and is made more likely by the app's own admission that resolution can take well over a minute. It does not require local/physical access, admin rights, or prior malware — only a repository whose conflicting content and tooling are attacker-influenced (a malicious PR/branch a victim merges), consistent with the fetched/cloned-repository threat model. I could not verify from the index whether any additional freshness check exists elsewhere (e.g. a content hash comparison) — this should be confirmed against the full source before treating this as conclusively exploitable.

### Recommendation
Before writing `resolvedContent` to disk in `_applyCopilotConflictResolutions`, re-read the file's current on-disk content and either (a) diff it against the `rawContent` captured at context-build time and abort/warn if they differ outside the resolved hunks, or (b) re-run `reassembleResolvedFile` against the freshly read current content instead of the stale `ctx.rawContent`, so the model's hunks are spliced into what is actually on disk at apply time rather than into a minutes-old snapshot.

### Proof of Concept
1. Start a merge/rebase against a branch that produces conflicts in `foo.ts`.
2. Click "Resolve with Copilot" and let the request run (can take well over a minute per the app's own telemetry buckets).
3. While the "loading" dialog is showing, open `foo.ts` in an external editor and make an edit within the conflict-marked region (markers remain present, so `hasUnresolvedConflicts` still reports true) — e.g. add a line only your editor knows about.
4. When Copilot's result appears and you click "Continue Merge," observe that `_applyCopilotConflictResolutions` overwrites `foo.ts` using `resolvedContent` built from the pre-edit `rawContent` captured in step 2, silently discarding your step-3 edit with no diff shown or warning surfaced, at [5](#0-4) .

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

**File:** app/src/lib/stores/app-store.ts (L7258-7258)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
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
