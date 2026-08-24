### Title
Copilot conflict resolution reassembly splices per-hunk fixes by position only, with no content binding to the conflict they resolve — silent corruption of merged/committed code - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The Immunefi report's underlying flaw is: a security-relevant value is tracked and consumed using the wrong "dimension" (token identity) instead of being bound to the entity it actually belongs to, so a count/index-based accounting operation silently produces a wrong result that existing checks don't catch. The closest analog in this codebase is `reassembleResolvedFile`, which splices Copilot's per-hunk conflict resolutions into a file **by positional index only**, and `validateResolutionPaths`, which only verifies that the **count** of hunks matches, never that hunk *N*'s resolution content actually corresponds to conflict-block *N*. This lets a compromised/malicious model response (reachable via the documented BYOK custom-endpoint feature) silently apply the wrong fix to the wrong conflict, corrupting what the user commits, while every existing validation step reports success.

### Finding Description
`reassembleResolvedFile` walks the raw on-disk conflicted file, and for every well-formed `<<<<<<<`/`=======`/`>>>>>>>` block it splices in `hunkResolutions[hunkIndex].resolvedContent`, incrementing `hunkIndex` for every block encountered — with no check that this resolution's content is actually the one the model intended for that block: [1](#0-0) 

The only pre-flight validation, `validateResolutionPaths`, checks that the returned resolution list has the correct file paths and that the **count** of hunks per file equals the expected count — it never checks hunk identity, content shape correspondence, or ordering: [2](#0-1) 

The function's own docstring documents this design choice explicitly: resolutions are "matched by order, not by line number": [3](#0-2) 

`parseCopilotConflictResolution` validates only that each hunk is `{resolvedContent: string}` and rejects it only if leftover conflict markers appear in the text — it performs no semantic tie between a hunk entry and the specific conflict block it is meant to resolve: [4](#0-3) 

This is the same broken-invariant shape as the report: `bondWithdrawal`/`getBond` tracked amounts by the wrong key (any token) instead of binding to the specific token being withdrawn, and `slash()`'s naive subtraction had no cross-check that the amount belonged to the right accounting bucket. Here, "hunk resolution N" is bound to "conflict block N" purely by array position, with no cross-check that the content actually pertains to that block (e.g. by hashing/comparing against the `oursContent`/`theirsContent` recorded when the context was built, which is available as `IFileConflictContext.hunks[].oursContent/theirsContent` per `extractSymbols`'s usage): [5](#0-4) 

### Impact Explanation
If the ordering of the `hunks` array returned by the model backend does not match the true left-to-right order of conflict blocks in the file — whether from an adversarial/compromised model endpoint (the wiki documents a user-configurable "BYOK" Copilot backend under section 6.3, meaning the JSON payload consumed here can originate from a third-party server the attacker controls) or from a bug/prompt-injection in the model's reasoning triggered by attacker-crafted conflicting branch content — `reassembleResolvedFile` will silently graft resolution content meant for one conflict onto a different, unrelated conflict location. Because `validateResolutionPaths` only checks hunk *counts*, this passes validation cleanly. The result is a "silent corruption of what the user commits or pushes": the user is shown a resolved file, believes it reflects the intended merge, and commits/pushes code that actually swaps or misapplies fixes between unrelated conflict regions (e.g., a security check kept in the wrong place, or a hunk fix intended for a different function silently landing elsewhere).

### Likelihood Explanation
Exploitability depends on the attacker's ability to influence the ordering/content of the JSON `hunks` array returned to `reassembleResolutions`/`reassembleResolvedFile`. This is architecturally plausible given the documented BYOK feature (custom AI backend endpoint), but I was not able to fully trace the BYOK request/response path (`CopilotStore`/BYOK code) within the available iterations to confirm the exact attacker reachability and whether any additional response-shape validation exists upstream. This is a design-level gap (the reassembly logic has zero content-identity binding by construction, as its own comment concedes) rather than a confirmed end-to-end exploit chain — additional investigation of the BYOK plumbing (`app/src/lib/stores/copilot-store.ts` and related BYOK settings code) would be needed to fully confirm attacker reachability.

### Recommendation
Bind each hunk resolution to the specific conflict block it claims to resolve rather than relying on array position: include a stable identifier (e.g., a hash or line-range fingerprint of the original `oursContent`/`theirsContent`/`baseContent` for that hunk) in the request/response contract, and have `reassembleResolvedFile` verify that identifier before splicing, rejecting (or falling back to `ICopilotSkippedFile`) on any mismatch instead of silently proceeding by position. Extend `validateResolutionPaths` to validate hunk identity, not merely hunk count.

### Proof of Concept
1. Trigger a merge/rebase with two or more real conflicts in a single file.
2. Have the Copilot conflict-resolution response (e.g., via a user-configured BYOK endpoint the attacker controls, or a crafted response) return the correct number of hunks for the file but with hunk `0`'s and hunk `1`'s `resolvedContent` swapped.
3. `validateResolutionPaths` passes (hunk count for the file is unchanged) — see [6](#0-5) .
4. `reassembleResolvedFile` splices hunk 0's resolution into the first conflict block location and hunk 1's resolution into the second, i.e. content intended for conflict 2 is written into conflict 1's location and vice versa — see [7](#0-6) .
5. The user reviews and commits/pushes the file, believing Copilot resolved each conflict correctly, but the merged code silently contains swapped/incorrect fixes with no error or warning surfaced anywhere in the pipeline.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L429-450)
```typescript
    const validatedHunks: Array<IHunkResolution> = []
    for (let j = 0; j < rawHunks.length; j++) {
      const hunkEntry: unknown = rawHunks[j]
      if (!isPlainObject(hunkEntry)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk at index ${j} of file "${path}" must be an object`
        )
      }
      const hunkObj = hunkEntry as Record<string, unknown>
      if (typeof hunkObj.resolvedContent !== 'string') {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: "resolvedContent" at hunk ${j} of file "${path}" must be a string`
        )
      }
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
      validatedHunks.push({ resolvedContent: rc })
    }
```

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L820-840)
```typescript
export function extractSymbols(file: IFileConflictContext): {
  readonly exports: ReadonlySet<string>
  readonly importPaths: ReadonlySet<string>
  readonly references: ReadonlySet<string>
} {
  const exports = new Set<string>()
  const importPaths = new Set<string>()
  const references = new Set<string>()

  const textParts: Array<string> = []
  for (const hunk of file.hunks) {
    textParts.push(
      hunk.oursContent,
      hunk.theirsContent,
      hunk.contextBefore,
      hunk.contextAfter
    )
    if (hunk.baseContent !== null) {
      textParts.push(hunk.baseContent)
    }
  }
```
