Based on my investigation, I found a structural analog to the reported bug class in the Copilot AI-assisted conflict resolution pipeline. The core invariant broken in the original report is: *a value is computed/decided using a "before" snapshot, and later applied against a check that only validates a narrower "after" condition, allowing the two to diverge silently.* The same pattern appears in how `reassembleResolutions`/`reassembleResolvedFile` reassembles AI-resolved conflict content against the *original* on-disk snapshot rather than the *current* on-disk state at apply-time.

### Title
Stale on-disk snapshot used to splice AI conflict resolutions can silently discard concurrent working-directory edits during merge/rebase - (File: app/src/lib/stores/app-store.ts)

### Summary
`buildConflictContext` reads each conflicted file's full raw content once, up front, to build the Copilot prompt [1](#0-0) . The model's per-hunk resolutions are later spliced back into that same captured `rawContent` by `reassembleResolvedFile`, which walks the *original* snapshot line-by-line and replaces marker blocks by hunk order, not by matching current content [2](#0-1) . When the resolutions are later written to disk in `app-store.ts`, the guard only skips a file if it has **no remaining conflict markers** at all (`!hasUnresolvedConflicts`) — it does not verify the file content is unchanged from the snapshot that was actually sent to the model [3](#0-2) .

### Finding Description
The write path in `app-store.ts` explicitly documents awareness of the staleness risk only for the "fully resolved externally" case:
```
// If the user resolved this file externally (e.g. in their editor) while
// the result dialog was open, git status will report it with no remaining
// conflict markers. Overwriting it with Copilot's stored content would
// silently clobber their work, so skip it and let their resolution stand.
``` [4](#0-3) 

That check, `isConflictedFileStatus(onDiskFile.status) && !hasUnresolvedConflicts(onDiskFile.status)`, only detects the case where conflict markers are *entirely gone*. It does not detect the case where the file **still has conflict markers** but the surrounding content changed since the snapshot was captured (e.g. the user hand-edited context lines outside the marker block, or a hook/filter mutated the file between context capture and the (potentially long-running) async Copilot resolution call in `_generateCommitMessage`-style flows). Because `reassembleResolvedFile` splices resolutions into the *original snapshot* (`ctx.rawContent`) rather than re-reading and re-diffing the current on-disk file, any such intervening edit is discarded when `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` runs [5](#0-4) , and the corrupted result is then staged with `git add` [6](#0-5) .

This mirrors the reported bug's broken invariant precisely: a downstream write/consistency check (`registerTrade`'s post-fee `trade.collateral` vs. the price-impact calc's pre-fee value in the report; here, the current on-disk file vs. the pre-AI-call `rawContent` snapshot) is performed against a value that was already stale by the time of the actual state mutation, and the existing guard (fee-deduction ordering there; `hasUnresolvedConflicts` here) does not cover the full staleness window.

### Impact Explanation
If exploited (attacker crafts a branch/PR whose merge/rebase against the victim's repo produces a conflict, banking on the AI-resolution window plus a race with the user's own edits, or on the model's slow response time during which the user edits the conflicted file to resolve part of it manually while leaving markers for another hunk), the final commit content can silently diverge from what the user actually intended to commit — satisfying the "silent corruption of what the user commits or pushes" criterion. Because the write happens with no diff shown to the user beyond the original AI resolution-summary dialog, an intervening edit is dropped without any warning.

### Likelihood Explanation
Likelihood is constrained: it requires the async window between `buildConflictContext` snapshot capture and the user (or Desktop, e.g. via a slow Copilot roundtrip) accepting the resolution, during which the user must edit the *same conflicted file* without fully clearing its markers. This is a real but narrow race condition rather than a directly attacker-triggerable primitive — the attacker cannot force the timing, they can only shape the conflict content to make manual partial-editing more likely.

### Recommendation
Before writing `resolution.resolvedContent`, re-read the file's current on-disk content and compare it (or at least the non-conflict-marker regions) against the `rawContent` snapshot used to generate the resolution. If they differ, treat the file the same way as the "resolved externally" case — skip the write and surface a warning — rather than only checking for the absence of conflict markers.

### Proof of Concept
1. Start a rebase/merge that conflicts a file with two separate conflict hunks (A and B).
2. Trigger Copilot conflict resolution; while the request is in flight (or before accepting the result dialog), manually resolve hunk A in the editor but leave hunk B's markers intact, also editing an unrelated line adjacent to hunk A.
3. Accept the Copilot resolution in the dialog.
4. Observe: `onDiskFile` still has unresolved markers (hunk B), so the skip-guard does not trigger; `writeFile` overwrites the file using `reassembleResolvedFile(ctx.rawContent, ...)`, which is built from the pre-edit snapshot — discarding the user's manual edit to hunk A and the adjacent line, with no warning.

**Note on confidence:** I could not fully trace the exact timing window of `_generateCommitMessage`/conflict-resolution async duration or confirm whether an additional freshness check exists elsewhere in the dispatcher layer that I didn't reach due to tool-call limits (the file `app/src/lib/copilot-conflict-resolution.ts` was truncated at 1000 lines and I could not view the remainder, nor the full dispatcher call chain that invokes the write function shown at `app-store.ts:7171`). If such a check exists elsewhere, this finding would be mitigated; a Devin session with full file access would be needed to conclusively verify.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-440)
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

**File:** app/src/lib/stores/app-store.ts (L7262-7268)
```typescript
    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
