### Title
Merge-conflict resolutions can be silently spliced into the wrong conflict block during Copilot auto-resolution - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot-powered merge-conflict resolver parses a conflicted file's markers twice with two independently-written parsers, and only cross-checks a **count**, never the actual splice points. `extractConflictHunks` (in `copilot-conflict-context.ts`) builds the hunk list that is (a) sent to the model and (b) used as the "expected" count for validation. Later, `reassembleResolvedFile` (in `copilot-conflict-resolution.ts`) re-scans the *same raw file content* on disk with its own, differently-behaved marker matcher to decide where to splice each hunk's LLM-generated resolution back in. Nothing verifies that the two scans agree on the number or location of conflict blocks. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`validateResolutionPaths` only checks that `resolution.hunks.length` equals the count produced by `extractConflictHunks` for that file: [4](#0-3) 

This is exactly the class of bug in the report: a check that validates an aggregate/count value instead of validating the real per-item correspondence. The count that is validated comes from `extractConflictHunks`, a bounded, sequential state machine that silently drops malformed blocks (e.g. a `<<<<<<<` with no matching `=======`/`>>>>>>>` before EOF or before it runs into another marker) via a bare `continue`, without ever recording that a block existed: [5](#0-4) 

`reassembleResolvedFile`, however, independently re-parses the *same original `rawContent`* using its own, more permissive lookahead (it scans **all remaining lines** for a separator and closing marker, not bounded the same way as `extractConflictHunks`): [6](#0-5) 

and splices `hunkResolutions` into whatever blocks *it* finds, purely by positional order (`hunkIndex`), with no check at the end that the number of blocks it spliced matches `hunkResolutions.length`: [7](#0-6) 

Because the two marker-parsers can disagree about how many "hunks" exist in a conflicted file (any file containing conflict-marker-like text that is malformed from `extractConflictHunks`'s point of view but well-formed from `reassembleResolvedFile`'s point of view — or vice versa), the validated hunk count and the actual number of splice points used during reassembly can diverge. When that happens, the LLM's resolution for conflict *N* gets written into the position of conflict *N±k*, with no exception thrown anywhere in the pipeline (`validateResolutionPaths` already passed on the earlier, now-stale count).

### Impact Explanation
The corrupted value is the reassembled file content that Desktop writes to disk and that the user subsequently stages/commits — i.e. the resolver silently mis-merges attacker-influenced repository content (a conflicted file the user is merging/rebasing, which can originate from a fetched branch/PR) into the wrong location without any error surfaced to the user. This matches the accepted impact category "silent corruption of what the user commits or pushes": the user sees a conflict-resolution dialog claiming success, but the committed file mixes unrelated resolved content into the wrong conflict region, potentially reintroducing code the user believed was replaced, or dropping code that was supposed to be kept — with no diagnostic that anything went wrong.

### Likelihood Explanation
Exploitation requires a merge/rebase/cherry-pick against a branch or PR (attacker-controlled, e.g. from a fork) whose changes produce a conflicted file containing marker sequences that are ambiguous between the two parsers' handling of malformed blocks (unbalanced `<<<<<<<`/`=======`/`>>>>>>>`, or literal marker-like lines from file content itself, e.g. a file about git conflicts, a patch file, or a doc/test fixture). This is plausible without any local access, admin rights, or social engineering — the user simply merges a branch or PR as part of normal workflow and invokes the Copilot conflict-resolution feature. The specific malformed-marker input needed to trigger the divergence is unverified against a live build (I could not run the parsers), so likelihood is best characterized as depending on discoverable edge-case inputs to two hand-written state machines rather than a universally-triggerable one-liner.

### Recommendation
Have `reassembleResolvedFile` reuse the exact same hunk-boundary list produced by `extractConflictHunks` (pass the parsed hunk boundaries through, rather than re-scanning `rawContent` with separate marker logic), and add an explicit invariant check that the number of conflict blocks found during reassembly equals `hunkResolutions.length`, throwing a `CopilotValidationError` (and refusing to write the file) if they disagree — mirroring how the recommended Solidity fix requires comparing the true aggregate (`sum(values[])`) against `msg.value` rather than trusting a per-call proxy check.

### Proof of Concept
Conceptual (not verified against a running build, since only static code was available):
1. Create a merge that leaves a conflicted file containing two conflict-marker regions, where the first region is malformed in a way `extractConflictHunks` treats as "no valid hunk" (e.g., missing `>>>>>>>` before the next `<<<<<<<`) but which `reassembleResolvedFile`'s unbounded lookahead treats as a valid, closed block (because it keeps scanning past the second `<<<<<<<` looking for any `=======`/`>>>>>>>` pair).
2. `extractConflictHunks` reports `hunks.length = 1` (only the second, well-formed region); this is what's sent to Copilot and what `validateResolutionPaths` checks against.
3. Copilot returns exactly 1 hunk resolution, which passes `validateResolutionPaths`.
4. `reassembleResolvedFile` re-scans the raw file and finds 2 splice points (its own more permissive parse), so it splices the single model resolution into the *first* (bogus) block it finds and leaves the real, second conflict's markers un-replaced or misplaced in the output — all without throwing any error.

Confidence caveat: I was not able to execute the parsers to confirm a concrete malformed-marker input that reliably produces divergent counts between `extractConflictHunks` and `reassembleResolvedFile`; this would need to be verified in a running Desktop build/test harness (e.g. `app/test/unit/copilot-conflict-resolution-test.ts`) to fully confirm exploitability.

### Citations

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

**File:** app/src/lib/copilot-conflict-context.ts (L179-279)
```typescript
export function extractConflictHunks(
  fileContent: string,
  contextLines: number = 3
): ReadonlyArray<IConflictHunk> {
  const lines = fileContent.split(/\r?\n/)
  const hunks: Array<IConflictHunk> = []

  let i = 0
  while (i < lines.length) {
    if (!oursMarker.test(lines[i])) {
      i++
      continue
    }

    const oursStart = i + 1
    const oursLines: Array<string> = []
    const baseLines: Array<string> = []
    let hasBase = false
    const theirsLines: Array<string> = []
    let hunkEnd = -1

    i = oursStart
    // Collect ours content
    while (i < lines.length) {
      if (baseMarker.test(lines[i])) {
        hasBase = true
        i++
        break
      }
      if (separatorMarker.test(lines[i])) {
        i++
        break
      }
      oursLines.push(lines[i])
      i++
    }

    // If diff3, collect base content until separator
    if (hasBase) {
      while (i < lines.length) {
        if (separatorMarker.test(lines[i])) {
          i++
          break
        }
        baseLines.push(lines[i])
        i++
      }
    }

    // Collect theirs content until closing marker
    while (i < lines.length) {
      if (theirsMarker.test(lines[i])) {
        hunkEnd = i
        i++
        break
      }
      theirsLines.push(lines[i])
      i++
    }

    // If we never found the closing marker, skip this malformed hunk
    if (hunkEnd === -1) {
      continue
    }

    // The ours marker line is at oursStart - 1
    const markerStart = oursStart - 1
    const contextStart = Math.max(0, markerStart - contextLines)
    const contextEnd = Math.min(lines.length - 1, hunkEnd + contextLines)

    // Clamp context to not include conflict markers from adjacent hunks
    const contextBeforeLines: Array<string> = []
    for (let j = markerStart - 1; j >= contextStart; j--) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextBeforeLines.unshift(lines[j])
    }

    const contextAfterLines: Array<string> = []
    for (let j = hunkEnd + 1; j <= contextEnd; j++) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextAfterLines.push(lines[j])
    }

    const contextBefore = contextBeforeLines.join('\n')
    const contextAfter = contextAfterLines.join('\n')

    hunks.push({
      oursContent: oursLines.join('\n'),
      theirsContent: theirsLines.join('\n'),
      baseContent: hasBase ? baseLines.join('\n') : null,
      contextBefore,
      contextAfter,
    })
  }

  return hunks
}
```
