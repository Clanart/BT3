Given the available evidence, I found a structurally analogous pattern in GitHub Desktop's Copilot-assisted merge-conflict resolution pipeline, though I want to flag upfront that I was not able to fully inspect `extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` (the counterpart hunk-boundary detector) before running out of iterations, so the exact marker-matching logic used there is unverified. The finding below is supported by what I could confirm in `app/src/lib/copilot-conflict-resolution.ts`.

### Title
Count-only validation of Copilot conflict-hunk resolutions permits silent misassembly of committed file content - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
The Otter Audits report describes a broken invariant: an "early stop" validation checks for the presence of one expected value (`earlyStopKey`) instead of confirming the *complete* set of expected keys was observed, letting a stale/incomplete iteration pass validation. The Desktop analog is in the Copilot conflict-resolution pipeline: `validateResolutionPaths` only validates *aggregate counts* (file paths present, hunk counts per file) rather than validating that each hunk actually corresponds to the specific conflict block it is spliced into, and `reassembleResolvedFile` performs its own independent, line-by-line regex-based re-scan of the raw on-disk content to locate conflict blocks and splices resolutions in strictly by order.

### Finding Description
`validateResolutionPaths` [1](#0-0)  enforces only that:
- the set of returned file paths equals the expected set,
- the number of hunks returned per file equals `expectedHunkCounts` (a count derived elsewhere, from `IFileConflictContext.hunks.length`).

It performs no verification that hunk *N* in the model's response is semantically or positionally tied to conflict block *N* as actually delimited in the raw file. That correspondence is entirely re-derived, independently, by `reassembleResolvedFile`, which walks `rawContent` line-by-line using its own local regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) to find `<<<<<<<` / `=======` / `>>>>>>>` sequences and splices `hunkResolutions[hunkIndex]` in encounter order [2](#0-1) .

This is the same class of flaw as the report: the guard that's supposed to certify "the resolved hunks match reality" only checks a coarse aggregate (count), not full correspondence with the actual conflict-block boundaries used at write time. If the raw file content contains anything that causes `reassembleResolvedFile`'s naive text-based marker scan to diverge from the block count/boundaries that were used to build the prompt sent to the model (i.e., whatever logic extracted hunks and computed `expectedHunkCounts`), the count-based check in `validateResolutionPaths` would still pass (because it only compares counts, not positions), while the splice indices actually shift. Whatever originally-conflicted or attacker-planted text falls between the "real" boundary and the diverging boundary in the naive scan gets silently copied through as regular content, and remaining resolved hunks get spliced into the wrong conflict blocks for the rest of the file [3](#0-2) .

The write path then commits this content to disk and stages it without any further check of correctness — only checking whether the user externally already resolved the file, not whether the reassembled content is internally consistent with the true conflict boundaries [4](#0-3) .

### Impact Explanation
If the divergence is achievable (unverified — see Likelihood), the impact is exactly the class the task requires: silent corruption of what the user commits/pushes. A malicious contributor could craft a repository/branch such that, upon a real merge conflict, one side's conflicting content contains attacker-chosen text resembling conflict-marker syntax. This would not change the *count* of blocks reported to `validateResolutionPaths`, but could shift where `reassembleResolvedFile`'s independent scan believes block boundaries are, causing legitimately-resolved hunks to land in the wrong location, or attacker content to be silently retained verbatim in the final committed file — without the user or reviewer noticing, since the "reasoning" text and dialog summary are generated from the model's own (correct) view of the conflicts, not from what `reassembleResolvedFile` actually produced.

### Likelihood Explanation
Unconfirmed. This hinges entirely on whether `extractConflictHunks` (in `app/src/lib/copilot-conflict-context.ts`, which I was unable to fully read) uses conflict-marker detection logic identical to `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in `copilot-conflict-resolution.ts`. If both use the exact same regexes and traversal logic, the two block-counting passes will always agree and there is no divergence — in that case this is not exploitable and the count check is sufficient in practice (though still architecturally fragile, since it validates an aggregate rather than the specific correspondence). I could not verify this either way before the iteration budget was exhausted.

### Recommendation
- Have `reassembleResolvedFile` reuse the exact same hunk-extraction function/logic as the one used to build `expectedHunkCounts` and the prompt sent to the model (single source of truth for conflict-block boundaries), rather than maintaining a second, independent marker-detection implementation.
- Strengthen `validateResolutionPaths` beyond a raw count comparison — e.g., have it operate on the same extracted hunk objects used for reassembly so that "N hunks expected" and "N hunks found at splice time" are guaranteed to reference the same underlying block set, closing the same class of gap the original report calls out (validate against the full expected set, not just an aggregate).
- Add a post-reassembly sanity check (e.g., re-run `extractConflictHunks` on the *reassembled* output and assert zero remaining conflict markers, and diff line-count/structure sanity) before writing to disk in `_applyCopilotConflictResolutions`.

### Proof of Concept
Not constructed — reproducing this requires confirming that `extractConflictHunks`'s marker-detection differs from `reassembleResolvedFile`'s regexes (unverified). A concrete PoC would require: (1) reading `copilot-conflict-context.ts`'s `extractConflictHunks` implementation to identify any divergence, and (2) crafting a conflicted file where a real conflict's "ours"/"theirs" content contains literal `<<<<<<<`/`=======`/`>>>>>>>`-shaped lines that `extractConflictHunks` treats as non-block content but `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` treats as a genuine (nested) block boundary, shifting `hunkIndex` in `reassembleResolvedFile` [5](#0-4) .

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-599)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/

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
