### Title
Silent commit corruption from positional (not identity-based) hunk matching in Copilot conflict resolution reassembly - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
`reassembleResolvedFile` and `reassembleResolutions` in `app/src/lib/copilot-conflict-resolution.ts` splice AI-model-produced hunk resolutions back into a conflicted file **by array order**, not by any identity/count check tying a resolution to the specific conflict block it was generated for. If the number or order of hunk resolutions returned by the model does not exactly match the number/order of `<<<<<<<...=======...>>>>>>>` blocks physically present in the file on disk, the wrong resolved text is silently spliced into the wrong conflict block. The result is then written straight to disk and `git add`-ed, becoming part of the user's commit without any diff review forcing them to notice the swap. This mirrors the report's root defect: a function assumes two conceptually related quantities (here, "hunk index in the model's response" and "hunk index in the file") always line up, and uses one where it should verify equivalence with the other, leading to a wrong value being used downstream with no correctness check. [1](#0-0) 

### Finding Description
`reassembleResolvedFile` walks the raw on-disk file content, and for each well-formed conflict marker block it encounters, pulls the next item off `hunkResolutions` purely by incrementing `hunkIndex`: [2](#0-1) 

There is no check that `hunkResolutions.length` equals the number of actual conflict blocks found in `rawContent`, and no per-hunk identifier (e.g., a hash of the original `oursContent`/`theirsContent`, or a stable hunk id) is used to confirm that `hunkResolutions[hunkIndex]` really corresponds to the block currently being processed.

`reassembleResolutions` is the caller that feeds `raw.hunks` (the model's raw, out-of-band JSON response) straight into this positional splice, again with no cross-validation against the number of hunks originally extracted from the file (`ctx.rawContent`, produced earlier by `extractConflictHunks` in `copilot-conflict-context.ts`): [3](#0-2) 

The only defensive check in the whole pipeline is a path-traversal guard (`resolveWithin`) applied before writing the file, plus a check that the file hasn't been externally resolved. Neither of these validates that the *content* being spliced in actually corresponds to the *hunk* it's being spliced into: [4](#0-3) 

Since the resolved content ultimately comes from an LLM response driven by attacker-influenced repository content (the conflicting branch/commit the user is merging, rebasing onto, or cherry-picking — content the attacker fully controls as the "theirs" side of a merge), a conflicted file engineered to contain multiple structurally similar conflict hunks (e.g., near-duplicate blocks, or blocks whose markers are subtly malformed so the model's hunk count diverges from the true count) can cause the model's response to be off-by-one or reordered relative to the file's actual conflict blocks. Because matching is purely positional, this silently swaps resolved content between hunks — corrupting the file that gets `git add`-ed and eventually committed, without any indication to the user that the wrong hunk received the wrong content.

### Impact Explanation
The corrupted content is written directly to the working tree and staged (`writeFile` + `git add`) as part of completing a merge/rebase/cherry-pick, then normally committed by the user believing they reviewed/accepted "Copilot's suggestion." This is a silent corruption of what the user commits and pushes — exactly the class of impact called out as valid (unprivileged, attacker controls the conflicting/fetched branch content, result is silent corruption of committed data). Depending on the swapped content, this could reintroduce vulnerable code, remove security checks, or silently revert a fix — all without the user's informed consent, since the diff view groups per-file rather than necessarily flagging a hunk-level mismatch to a non-expert reviewer.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the Copilot conflict-resolution feature to be enabled and used by the victim, (2) an attacker-controlled branch/PR/commit that produces multiple conflict hunks in a single file, and (3) the underlying LLM emitting hunk resolutions in a count/order that doesn't exactly match the file's real hunks (own miscounting, hallucination, or hunks deliberately crafted to be visually/structurally confusable). Because this is contingent on non-deterministic LLM output, it's not a fully deterministic exploit like the original TOKEN/report bug, but the code path has zero structural safeguard (no count check, no identity binding) that would catch or prevent the divergence once it occurs — the invariant "model hunk order == file hunk order" is assumed and never verified.

### Recommendation
- In `reassembleResolutions`/`reassembleResolvedFile`, validate that `raw.hunks.length` equals the number of conflict blocks actually present in `ctx.rawContent` before splicing; if they don't match, treat the file as unresolved/skipped rather than silently misapplying resolutions.
- Bind each hunk resolution to its source hunk by identity (e.g., include the original `oursContent`/`theirsContent` snippet or a stable hunk id in the model's per-hunk schema) and match by that identity rather than by array position.
- Surface a hard error (not a silent fallback) in `_applyCopilotConflictResolutions` when reassembly detects a hunk-count mismatch, forcing the file back to manual resolution.

### Proof of Concept
Conceptual PoC (cannot be fully executed without the Copilot conflict-resolution feature and a live model call, so this describes the deterministic part of the bug reachable purely from local code):
1. Create a repository where a merge introduces a file with two textually similar conflict hunks, e.g.:
```
<<<<<<< HEAD
credentialCheck = true
=======
credentialCheck = false
>>>>>>> feature

...

<<<<<<< HEAD
debugMode = false
=======
debugMode = true
>>>>>>> feature
```
2. Simulate `reassembleResolvedFile(rawContent, hunkResolutions)` where `hunkResolutions` is supplied in swapped order (e.g., the resolution intended for the second hunk is placed first) — this can be reproduced directly as a unit test against `reassembleResolvedFile` without any model call, since the function has no way to detect the swap: [5](#0-4) 
3. Observe that the function happily produces a file where `credentialCheck = true` (attacker's intended "harmless" hunk 1 resolution) is spliced into the `debugMode` hunk and vice versa — with no error, no warning, and the caller (`_applyCopilotConflictResolutions`) writes this result straight to disk and stages it for commit.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-547)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-599)
```typescript
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
): string {
  const eol = rawContent.includes('\r\n') ? '\r\n' : '\n'
  const lines = rawContent.split(/\r?\n/)
  const resultLines: Array<string> = []
  let hunkIndex = 0
  let i = 0

  while (i < lines.length) {
    if (reassemblyOursMarker.test(lines[i])) {
      // Look ahead to verify this is a well-formed conflict block:
      // must have a ======= separator and a >>>>>>> closing marker.
      let hasSeparator = false
      let closingIndex = -1
      for (let j = i + 1; j < lines.length; j++) {
        if (reassemblySeparatorMarker.test(lines[j])) {
          hasSeparator = true
        } else if (reassemblyTheirsMarker.test(lines[j])) {
          closingIndex = j
          break
        }
      }

      if (!hasSeparator || closingIndex === -1) {
        // Malformed marker — copy through as regular content
        resultLines.push(lines[i])
        i++
        continue
      }

      // Skip through the entire conflict marker block
      i = closingIndex + 1

      // Splice in the resolved content for this hunk
      if (hunkIndex < hunkResolutions.length) {
        const resolved = hunkResolutions[hunkIndex].resolvedContent
        if (resolved.length > 0) {
          resultLines.push(...resolved.split(/\r?\n/))
        }
      }
      hunkIndex++
    } else {
      resultLines.push(lines[i])
      i++
    }
  }

  return resultLines.join(eol)
}
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
