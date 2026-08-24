### Title
Copilot merge-conflict "resolve with AI" feature can silently corrupt committed file content due to a divergent conflict-marker parser - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The Copilot conflict-resolution flow validates the model's response against a hunk count computed by one parser (`IFileConflictContext.hunks`, built when the app first scans a conflicted file), but then applies the model's per-hunk resolutions using a **second, independent** marker scanner in `reassembleResolvedFile()` [1](#0-0) . Because these two parsers can disagree about how many/which `<<<<<<<`/`=======`/`>>>>>>>` blocks exist in the raw file, the reassembly step can splice AI-generated resolution text into the wrong location (or leave real conflict content unresolved) without any error, silently corrupting the file the user then commits/pushes.

### Finding Description
`validateResolutionPaths()` only checks that the *count* of hunks returned by the model matches `expectedHunkCounts`, a map built from `f.hunks.length` on the original `IFileConflictContext` list [2](#0-1) . It never re-validates against the actual marker structure of the raw file at the point resolutions are applied.

The actual application of resolutions happens later in `reassembleResolvedFile()`, which independently re-scans the *entire* raw file content line-by-line for anything matching:
- `^<{7}(?:\s|$)` as an opener
- `^={7}$` as a separator
- `^>{7}(?:\s|$)` as a closer [3](#0-2) 

This scan is purely textual and has no ties to git's actual conflict boundaries — it treats **any** line in the file that happens to match those 7-character marker patterns as a real conflict block, and splices in resolutions "matched by order, not by line number" (`hunkResolutions[hunkIndex]`) [4](#0-3) .

The upstream count-check in `validateResolutionPaths` is computed from a *separate* conflict-hunk extraction routine used to build the LLM prompt (`IFileConflictContext.hunks`, in `copilot-conflict-context.ts`), not from re-scanning `rawContent` the way `reassembleResolvedFile` does. If an attacker crafts either side of a merge (a branch/PR the victim fetches and merges/rebases/cherry-picks) so that a genuinely conflicting file also contains attacker-controlled lines that happen to match the marker regexes (e.g. a string literal, test fixture, or documentation snippet containing `<<<<<<<`, `=======`, `>>>>>>>` sequences unrelated to the real git conflict), the two parsers can disagree on the number/location of "conflict blocks" while still agreeing on the total block **count** by coincidence, or the mismatch can go undetected because the count check only compares against the LLM-response's own hunk array, not against a fresh scan of the raw file. The result: `reassembleResolvedFile` walks past the real conflict into the attacker-planted fake marker region (or vice versa), consumes hunk resolutions out of order, and silently drops or misplaces both AI-resolved and legitimate code — with no error surfaced to the user.

This mirrors the report's broken invariant: a function proceeds to "distribute"/apply a result computed against one accounting of state (`expectedHunkCounts` from the LLM-prompt parser) without verifying that the actual runtime operation (the naive re-scan in `reassembleResolvedFile`) consumed the same set of items, silently producing wrong output built from mismatched inputs.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." The reassembled content is written directly to the working tree and then presented to the user as the resolved merge; if the user trusts the AI resolution (the entire feature's purpose) and stages/commits it, the resulting commit can contain corrupted code — e.g. real conflicting code silently dropped, duplicated, or placed at the wrong location in the file — without any validation error being raised. Because the attacker only needs to control content on one side of a merge/rebase/cherry-pick (a remote branch or PR the victim merges), this satisfies the "attacker controls a cloned/fetched repository" primitive without requiring local access, admin rights, or social engineering beyond a normal PR/branch merge.

### Likelihood Explanation
Requires: (1) the victim uses the AI conflict-resolution feature on a merge/rebase/cherry-pick involving attacker-influenced content, and (2) the conflicting file (or file near the conflict) contains attacker-planted content matching 7-character marker sequences. This is a plausible but non-trivial precondition — it needs a specific crafted file layout, similar in spirit to the original report's "prerequisite of squeeth shortage in the provided orders" being a specific but reachable precondition. No existing check re-verifies the marker structure against the raw file at splice time, so once the precondition is met there is no guard preventing the corruption.

### Recommendation
- Have `reassembleResolvedFile` and the original conflict-hunk extractor (`copilot-conflict-context.ts`) share a single, canonical marker-parsing implementation so they can never disagree.
- After reassembly, re-verify that the reassembled file no longer contains any of the three marker patterns and that the total marker-block count consumed during reassembly matches the originally reported `hunks.length` for that specific file (not just the LLM's own count), throwing `CopilotValidationError` on mismatch instead of silently proceeding.
- Consider anchoring conflict-block boundaries to git's own byte offsets/positions (as already tracked for the "Conflict 1 of N" prompt construction) rather than re-scanning raw text, to eliminate ambiguity from marker-like content that isn't part of a real conflict.

### Proof of Concept
1. Attacker pushes a branch where a file (e.g. `notes.ts`) contains, outside of any real conflict, a string/test fixture such as:
   ```
   const example = `<<<<<<< HEAD
   foo
   =======
   bar
   >>>>>>> feature`
   ```
2. Victim merges this branch into a local branch that also modifies `notes.ts` in a genuinely conflicting way elsewhere in the file, producing one real git conflict block plus the attacker's fake marker-like block in the same file.
3. Victim invokes GitHub Desktop's "Resolve with Copilot" feature. The original hunk-extraction logic (used to build the prompt) reports N real conflicts (say 1) based on git's actual conflict info; the model is asked to resolve 1 hunk and returns 1 resolution, which passes `validateResolutionPaths`'s count check.
4. `reassembleResolvedFile` re-scans the raw file text and encounters the attacker's fake marker block before (or in addition to) the real one, treats it as a conflict block, and splices `hunkResolutions[0]` into that location instead of (or in addition to) the real conflict — while the real conflict marker text either remains unresolved and unremoved, or receives no resolution at all (since `hunkIndex` has already been incremented past the intended target).
5. The resulting file is written to disk and presented as fully resolved; if the user commits it, real conflicting code is silently dropped, duplicated, or corrupted without any error, warning, or leftover conflict markers to alert the user (assuming both marker blocks are individually well-formed per the malformed-marker fallback check).

Note: I could not fully trace the exact original hunk-extraction implementation in `copilot-conflict-context.ts` line-by-line within the available context, so the precise conditions under which the two parsers' counts coincidentally match (versus differ and get caught by `validateResolutionPaths`) are not fully confirmed — this should be verified against that file's full source before treating this as fully proven.

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
