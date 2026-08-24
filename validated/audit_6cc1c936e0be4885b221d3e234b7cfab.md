# Vulnerability Analog Assessment

## Title
Copilot conflict-resolution splicing applies stale, position-matched hunk resolutions without revalidating the on-disk conflict structure - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

## Summary
The `resetMiddle`/`calculateWithdrawShares` bug is fundamentally an **index/accumulator desynchronization**: a position marker (`withdrawQueue.middle`) is advanced and consumed as if it always corresponds to the state that produced it, and nothing re-validates that correspondence before the stale state is applied. The closest analog in this codebase is the Copilot AI conflict-resolution pipeline, where per-file hunk resolutions are matched to conflict-marker blocks **by ordinal position only** (`hunkIndex` in `reassembleResolvedFile`) and are written to disk later, in `_applyCopilotConflictResolutions`, using only a coarse boolean check rather than revalidating that the resolutions still correspond to the current on-disk conflict structure.

## Finding Description
`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` splices AI-produced `resolvedContent` into a file's conflict-marker blocks strictly by encounter order: [1](#0-0) [2](#0-1) 

This reassembly happens once, when the model's response is parsed (`reassembleResolutions`), producing a `resolvedContent` string per file that is cached in `multiCommitOperationState.copilotResolutions`: [3](#0-2) 

Later, when the user clicks "Continue Merge", `_applyCopilotConflictResolutions` writes this cached `resolvedContent` verbatim to disk and stages it: [4](#0-3) 

The only guard against staleness is: [5](#0-4) 

This check only detects the case where the file has **no remaining conflict markers at all** (`!hasUnresolvedConflicts`). It does **not** detect the case where the file is still conflicted but its marker structure has changed since the resolution was generated — e.g., the number, order, or content of hunks differs because:
- the working tree was mutated between generation and apply time (external tool, hook, or a `git fetch`/`git pull` that ran concurrently and altered refs used to regenerate conflict context), or
- during a multi-commit operation (rebase/cherry-pick/squash), the operation silently advanced to a different commit in the queue (Desktop auto-skips commits with no changes via `git rebase --skip` / `--continue` in `continueRebase`/`continueCherryPick`) between the time the AI resolution was captured for "the current conflict" and the time it is applied.

Because `reassembleResolvedFile` matches by position, not by content identity of each conflict block, any mismatch between the hunk set the model reasoned about and the hunk set now on disk causes wrong merged text to be spliced into the wrong region, or into a conflict from a different commit in the queue — exactly the class of bug in the report: state is tracked by an index/position but consumed later as if it were still synchronized to the same underlying data, with no mechanism to detect drift.

## Impact Explanation
If the desync occurs, `_applyCopilotConflictResolutions` silently writes attacker-influenced or simply mismatched content into the user's working tree, stages it (`git add`), and it becomes part of the commit produced by the in-progress rebase/cherry-pick/merge/squash operation — i.e., silent corruption of what the user commits and, subsequently, pushes. Since staging happens automatically and the write path has no per-hunk provenance check, the user has no visibility that the applied text no longer corresponds to the conflict they were shown.

## Likelihood Explanation
This requires a narrow timing window between conflict-context capture and application, and Desktop already closes the most obvious gap (fully-resolved files are skipped). The remaining path — conflict structure changing while still "conflicted" during a multi-commit queue operation, or via a background repository mutation — is plausible but not trivially triggerable by a remote attacker without some cooperation from operation sequencing (e.g., relying on Desktop's auto-skip-empty-commit behavior in `continueRebase`/`continueCherryPick`). I could not find a stronger, more directly remote-triggerable analog for the `resetMiddle` accounting-desync pattern within the indexed portion of the codebase.

## Recommendation
In `_applyCopilotConflictResolutions`, re-derive the current conflict-hunk count/content for each file immediately before writing and compare it against the hunk set the cached resolution was generated from (e.g., store a hash or count derived from `IFileConflictContext.rawContent` alongside `IRawFileResolution` and re-check it at apply time). If they differ, discard the stale resolution and re-run resolution for that file (or fall back to manual resolution) rather than writing positionally-matched content unconditionally.

## Proof of Concept
Not independently reproducible from the indexed code alone — the trigger requires timing the on-disk conflict-marker structure to change between Copilot resolution generation (`reassembleResolutions`) and application (`_applyCopilotConflictResolutions`), e.g. by racing a multi-commit operation's auto-skip/continue behavior in `continueRebase`/`continueCherryPick`. Reference code paths for verification: [6](#0-5) [7](#0-6) 

**Uncertainty note:** I was unable to confirm from the indexed files a fully remote-attacker-reachable trigger that requires no cooperating local timing/race condition. Given the index size limits noted in my tooling, some files relevant to the multi-commit-operation queue advancement and Copilot conflict-context regeneration logic may not have been fully available to me. If you need a definitive determination, I'd recommend starting a full Devin session with complete repository access to trace the exact call sequence between conflict-context capture and `_applyCopilotConflictResolutions` across a multi-file, multi-commit rebase/cherry-pick.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L529-538)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-596)
```typescript
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

**File:** app/src/lib/git/rebase.ts (L516-529)
```typescript
  if (trackedFilesAfter.length === 0) {
    log.warn(
      `[rebase] no tracked changes to commit for ${rebaseCurrentCommit}, continuing rebase but skipping this commit`
    )

    const result = await git(
      ['rebase', '--skip', ...(opts?.noVerify ? ['--no-verify'] : [])],
      repository.path,
      'continueRebaseSkipCurrentCommit',
      options
    )

    return parseRebaseResult(result)
  }
```

**File:** app/src/lib/git/cherry-pick.ts (L451-466)
```typescript
  if (trackedFilesAfter.length === 0) {
    log.warn(
      `[cherryPick] no tracked changes to commit, continuing cherry-pick but skipping this commit`
    )

    // This commits the empty commit so that the cherry picked commit still
    // shows up in the target branches history.
    const result = await git(
      ['commit', '--allow-empty'],
      repository.path,
      'continueCherryPickSkipCurrentCommit',
      options
    )

    return parseCherryPickResult(result)
  }
```
