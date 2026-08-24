Based on my investigation, I found a plausible Desktop analog to the underlying bug class — a value written back based on a stale, earlier-captured snapshot without verifying it against the current state, silently discarding intervening changes.

### Title
Stale Copilot Conflict Resolutions Are Written To Disk Without Re-Validating They Still Match The Current Conflict Content - (File: `app/src/lib/stores/app-store.ts`)

### Summary
Copilot conflict resolution in GitHub Desktop computes `resolvedContent` for each conflicted file once, early in the flow, by splicing the model's per-hunk output into a *snapshot* of the file's raw content (`ctx.rawContent`) captured when the resolution request was built [1](#0-0) . When the user clicks "Continue Merge," `_applyCopilotConflictResolutions` writes that stale `resolvedContent` to disk for any file that still looks conflicted, but the only staleness check performed is "does the file still have unresolved conflict markers" — it never re-derives or compares the current on-disk conflicted content against the snapshot the resolution was generated from [2](#0-1) .

### Finding Description
This mirrors the reported bug class exactly: a tracked value (`amountInShelter`) is captured at one point in time, and a later state transition (`exitShelter`) blindly overwrites/loses anything that doesn't match that earlier snapshot, without checking for legitimate value added in between (`donate`). Here, the "amount" is the file's true, current conflicted content, and the "shelter deactivation" is `_applyCopilotConflictResolutions`.

The resolution content is generated asynchronously — the SDK round trip to Copilot can take a while, during which git state can change (a retried checkout, a background fetch that updates a linked worktree, another in-flight operation, or the user re-running `git merge`/`git rebase --continue` externally could alter conflict hunks). The only guard against writing stale content is:

```
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
``` [3](#0-2) 

This check only distinguishes "still has conflict markers" vs "user manually resolved it (no markers left)." It does **not** verify that the specific conflict markers/hunks Copilot analyzed are the *same* ones currently on disk. If the file is still reported as conflicted but its content has changed since `ctx.rawContent` was captured (different hunk count, different surrounding code, or an entirely different conflicting commit due to a retried/updated operation), `reassembleResolvedFile` already spliced the model's hunk-ordered output into the old snapshot [4](#0-3) , and that stale, mismatched full-file content is written wholesale over whatever is now on disk and staged with `git add` [5](#0-4) .

### Impact Explanation
This can silently corrupt what the user commits/pushes: newer legitimate conflict content (e.g., from a race with a fetch/checkout retry, or from the operation being restarted against an updated ref) is discarded and replaced with the AI's resolution computed against an outdated view of the file, without any hash/identity check tying the write back to the exact content that was analyzed. This is the same broken invariant as the Concur report — a later "reset" operation (`exitShelter` / `_applyCopilotConflictResolutions`) discards value accumulated after a snapshot was taken (`donate` / file content changing mid-flight), because the code only checks a coarse boolean state ("activated"/"still conflicted") rather than reconciling against the actual current value.

### Likelihood Explanation
The existing changelog entry — "Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349" [6](#0-5)  — confirms this exact write path has already produced at least one class of silent data-loss bug, indicating the "trust the stale snapshot" pattern in this flow is a recurring source of real defects rather than a purely theoretical one. I could not fully verify from the index how easily an attacker-controlled remote could reliably win the race window between resolution generation and user confirmation (this requires further investigation into worktree/fetch interleaving and is not something I could confirm from the available file contents), so likelihood should be treated as moderate/uncertain pending a live reproduction with a controlled timing race.

### Recommendation
Before writing `resolution.resolvedContent`, re-read the current on-disk conflicted content and verify it matches (e.g., via hash) the `ctx.rawContent` snapshot the resolution was generated from. If it no longer matches, skip the stale resolution (treat it like the "already resolved externally" case) and surface it to the user as needing re-resolution, rather than silently overwriting.

### Proof of Concept
Not independently reproduced — this is a code-level analysis of the write path. A concrete PoC would require: (1) triggering Copilot conflict resolution on a file, (2) altering the conflicted file's content on disk while the SDK request is in flight but before the file becomes fully non-conflicted, and (3) confirming "Continue Merge," at which point the stale `resolvedContent` overwrites the newer conflicted content without any mismatch detection, per the code paths cited above.

### Citations

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L628-641)
```typescript
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

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```
