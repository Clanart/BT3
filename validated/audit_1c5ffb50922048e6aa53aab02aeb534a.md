## Analog Found: Order-Based Conflict-Hunk Splicing Without Provenance Validation

### Title
Attacker-Crafted Conflict-Marker Look-Alikes Cause Silent Misplacement of Copilot-Resolved Content During File Reassembly - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The Solidity report's root cause is an algorithm that assumes a single, consistent stream is responsible for populating an ordered list, and blindly walks that list positionally without verifying which "bucket" actually produced each entry. The structural analog in GitHub Desktop is `reassembleResolvedFile()`, which reconstructs the final file that gets committed by scanning the raw file text for conflict-marker-shaped line sequences and splicing in the *i*-th model-provided hunk resolution for the *i*-th marker block it finds — purely by positional order, with no verification that a matched block is a real git conflict block as opposed to attacker-controlled file content that merely resembles one.

### Finding Description
`reassembleResolvedFile` walks the file line-by-line, and whenever it sees a line matching the "ours" marker regex `/^<{7}(?:\s|$)/`, it looks forward for *any* subsequent line matching the separator `/^={7}$/` and *any* later line matching the "theirs" marker `/^>{7}(?:\s|$)/`, and treats everything between them as one conflict block to be replaced by `hunkResolutions[hunkIndex]`: [1](#0-0) 

The matching logic:
- Does not verify that the detected `=======`/`>>>>>>>` pair genuinely belongs to a git-generated conflict (it accepts the first subsequent lines matching those bare regexes anywhere later in the file).
- Correlates model hunks to marker blocks purely by encounter order (`hunkIndex++`), not by any stable identity (e.g., original line offsets, hashes of the "ours"/"theirs" content).
- Only guards *count* of hunks per file, not *identity/position* correctness: `validateResolutionPaths` only checks that `resolution.hunks.length === expectedCount`, never that each hunk actually corresponds to the marker block it will be spliced into. [2](#0-1) 

This is the same broken invariant as `_searchApproxIndex()`: the code assumes there is exactly one legitimate "bucket" (real conflict markers produced by git) feeding the ordered list, when in fact the input file — which can come from a remote branch, a fetched/cloned malicious repository, or an incoming PR the user is merging — is attacker-controlled content that can inject additional marker-shaped lines (e.g. a string literal, a code comment, or a legitimately-looking but crafted diff fragment containing `<<<<<<<`/`=======`/`>>>>>>>` sequences). Because the matcher does not distinguish "true" conflict-block boundaries from look-alike text and does not tie a specific hunk resolution to a specific block by anything other than encounter order, a single well-placed lookalike sequence anywhere earlier in the file shifts every subsequent `hunkIndex` mapping, causing the model's resolution for conflict N to be spliced into a different (or spurious) location than the one it was written for.

### Impact Explanation
The output of `reassembleResolvedFile` becomes `IFileResolution.resolvedContent`, which is written to disk and ultimately committed/pushed by the user via the write path that consumes `reassembleResolutions()`: [3](#0-2) 

If an attacker controls the content of one side of the merge (a malicious branch/fork the user merges, or content pulled via `git fetch`), they can engineer marker-lookalike text so that AI-resolved hunks are misapplied — e.g., causing security-relevant code (auth checks, dependency pins, CI config) to be silently dropped, duplicated, or replaced with attacker-favorable content, while the user believes the reassembly faithfully applied Copilot's per-hunk resolutions. This matches the "silent corruption of what the user commits or pushes" impact class explicitly called out as valid.

### Likelihood Explanation
This requires the Copilot conflict-resolution feature to be enabled/used and requires the attacker to control input content that becomes part of a real merge/rebase/cherry-pick conflict the user resolves with this feature — a plausible but non-trivial precondition (an attacker-controlled branch, fork, or PR the victim merges). No local access, admin rights, or social engineering beyond "user merges attacker's branch" (a normal, expected git workflow) is needed. Existing guards (`validateResolutionPaths`) check only aggregate hunk counts, not per-hunk positional correctness, so they do not stop this path.

### Recommendation
Do not rely on encounter-order alone to correlate marker blocks to model hunks. Instead:
- Compute conflict-block boundaries independently from git itself (or a single trusted parse pass) before ever showing content to the model, and tag each hunk resolution with a stable identifier (e.g., a hash of its original `oursContent`/`theirsContent`, or its 1-based index *and* a content fingerprint) that is validated against the actual block content immediately before splicing.
- Reject/log any file where the number of detected "well-formed" marker blocks doesn't exactly match the number of conflict hunks recorded when the context was gathered (`IFileConflictContext.hunks.length`), rather than only checking the model's echoed hunk count.
- Consider stricter marker matching that only accepts blocks bounded exactly at the byte offsets recorded during context gathering.

### Proof of Concept
1. Set up a merge/rebase conflict in a real conflicted file that Copilot will attempt to resolve, `file.ts`, containing one genuine conflict block.
2. On the attacker-controlled side of the merge, additionally include, earlier in the same file (outside any real conflict), literal text lines that match the marker regexes but are not part of a real conflict, e.g. a multi-line string/comment such as:
   ```
   // decoy:
   <<<<<<< not-a-real-marker
   =======
   >>>>>>> also-not-real
   ```
3. When the user resolves the merge with Copilot, `parseCopilotConflictResolution` returns one `IHunkResolution` per real conflict (as expected, count matches `expectedHunkCounts`), but `reassembleResolvedFile` (lines 559–596 in `copilot-conflict-resolution.ts`) encounters the decoy `<<<<<<<` first, finds the decoy `=======`/`>>>>>>>` as a "well-formed" block, and consumes/splices `hunkResolutions[0]` into the decoy location instead of the real conflict, shifting `hunkIndex` for all subsequent real blocks.
4. The reassembled file written to disk (and subsequently committed/pushed by the user) now silently contains misplaced/dropped resolved content, without any error being raised by `validateResolutionPaths`, which only checks hunk *counts*, not positions.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-520)
```typescript
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
