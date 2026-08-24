### Title
AI conflict-resolution reassembly matches resolved hunks by naive positional marker scan, allowing crafted file content to misalign resolutions and silently corrupt the committed merge/rebase result - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The external report's broken invariant is that a value is computed/allocated based on an *intermediate, order-based snapshot* instead of the true, verified final state, letting an attacker-influenced input hijack the mapping between contribution and reward. The same class of bug exists in Desktop's Copilot conflict-resolution reassembly path: `reassembleResolvedFile` [1](#0-0)  splices the model's per-hunk resolutions into the on-disk conflicted file purely by **positional/order matching** — it walks the file line-by-line, treats any line matching `^<{7}(?:\s|$)` as the start of a conflict block, and assigns `hunkResolutions[hunkIndex]` in the order these blocks are encountered [2](#0-1) . This scan is a much weaker/independent parser than whatever logic originally produced `IFileConflictContext`'s hunk list that the model was prompted with and that `validateResolutionPaths` uses to check hunk *counts* [3](#0-2) . Because only the **count** of hunks is validated, not their exact boundaries/content, a repository containing file content that merely resembles conflict markers can desynchronize the "order" the resolver assumed from the order the naive splicer detects — causing an AI-generated hunk resolution to be spliced into the wrong location in the file that Desktop then writes to disk and lets the user commit.

### Finding Description
`reassembleResolvedFile` is the single place that turns the model's abstract "resolution for hunk N" into literal file bytes: [4](#0-3) . Its own doc comment concedes the mechanism is fragile: "matched by order, not by line number," and malformed markers are "copied through unchanged to avoid data loss" [5](#0-4) . The detector for a conflict block is a simple regex test per line (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) with a forward scan for the next `=======`/`>>>>>>>` [6](#0-5) [7](#0-6) . It performs no cross-check against the original hunk boundaries captured earlier in `IFileConflictContext` (the structured hunks that were actually sent to the model and whose *count* is enforced by `validateResolutionPaths` [8](#0-7) ) — only the number of hunks is compared, never their positions or content.

This is the exact same broken-invariant shape as the `VoterV3._notifyRewardAmount` bug: an allocation (`hunkResolutions[hunkIndex]` ↔ `resolvedContent`) is committed based on the *order encountered during a partial/independent scan* rather than a *verified, final correspondence* to the actual conflict structure. In `VoterV3` an attacker could exploit a mismatch between "current index snapshot" and "final vote state." Here, an attacker who controls the content of a cloned/fetched repository (e.g., a file that legitimately or maliciously contains lines beginning with `<<<<<<<`, `=======`, or `>>>>>>>` — for example documentation, ASCII banners, embedded diff/patch examples, or test fixtures that quote git conflict-marker syntax at the start of a line) can cause the naive reassembly scanner to see a different number/position of "conflict blocks" than the structured extraction that generated `IFileConflictContext.hunks` and prompted the model. Because `validateResolutionPaths` only checks that `resolution.hunks.length === expectedCount` [9](#0-8) , an equal count with misaligned positions passes validation silently. The result: the AI's resolution for conflict 2 gets spliced into the location the naive scanner thinks is conflict 1 (or into a fake "conflict block" formed by attacker-controlled non-conflict text), while the real second conflict block is treated as "malformed" and copied through with its raw git conflict markers still present, or vice versa — corrupting the file that Desktop writes and that the user then stages/commits/pushes, believing it reflects the reviewed AI summary/reasoning.

### Impact Explanation
This falls squarely in the accepted impact category "silent corruption of what the user commits or pushes," triggered purely by the *content of a cloned/fetched repository* — no local/physical access, no admin rights, no prior malware, and no unnatural user steps beyond a normal merge/rebase/cherry-pick that hits a conflict and using the built-in "Resolve with Copilot" feature. The user reviews a `reasoning`/`summary` string that describes the intended, correct resolution [10](#0-9) , but the actual bytes written to disk can diverge from that description because of the positional mismatch, and the divergence is invisible unless the user manually re-diffs every hunk against the raw markers. In the worst case this can leave literal, unresolved git conflict markers (`<<<<<<<`/`=======`/`>>>>>>>`) embedded in code that gets committed and pushed, or silently drop/duplicate code from one side of the conflict.

### Likelihood Explanation
Any public or crafted-and-shared repository can embed content that starts with 7-character conflict-marker sequences at the beginning of a line (common in git tutorials, patch/diff fixtures, CI logs checked into the repo, or files that document conflict-resolution workflows). The only precondition is that the file with such content also has a genuine merge/rebase/cherry-pick conflict and the user invokes Copilot conflict resolution on it — a standard, expected Desktop workflow, not a contrived one. The vulnerability requires no elevated trust and reuses the normal AI-conflict-resolution feature path, making it a realistic, moderate-likelihood scenario for any project incorporating adversarial or third-party content.

### Recommendation
Replace the "match by order" positional splice with a boundary that is derived from the same structured hunk extraction used to build `IFileConflictContext` (i.e., reuse exact byte/line offsets recorded when the hunks were parsed, rather than re-scanning the raw text with an independent line-based regex). At minimum, `reassembleResolvedFile` should validate that the number of well-formed conflict blocks it detects exactly equals `hunkResolutions.length` from `validateResolutionPaths`'s expected count *and* that each detected block's raw "ours"/"theirs" text matches the corresponding structured hunk's `oursContent`/`theirsContent` byte-for-byte before splicing; any mismatch should hard-fail (as `CopilotValidationError`) rather than silently falling back to "copy through unchanged."

### Proof of Concept
1. Attacker publishes/contributes to a repository containing a file, e.g. `docs/conflict-guide.md`, with content such as:
   ```
   Example of a conflict marker:
   <<<<<<< HEAD
   example ours
   =======
   example theirs
   >>>>>>> feature
   ```
   as ordinary, non-conflicted, committed documentation text (perfectly legitimate content, present on both branches identically so it's not itself part of any diff).
2. The victim clones this repo in GitHub Desktop, and separately creates a real, unrelated conflict in the same file (e.g., editing a different section on two branches) so the file ends up with **one real** git conflict block appended after the example text, giving two `<<<<<<<`-prefixed line matches in the file even though only one is a true git-generated conflict.
3. The victim triggers "Resolve with Copilot." The structured extraction used to build the prompt only recognizes the single true conflict (since it derives hunk boundaries from actual git conflict state), so the model returns exactly one hunk resolution, satisfying `validateResolutionPaths`'s count check [3](#0-2) .
4. `reassembleResolvedFile`'s naive scanner, however, encounters the fake example block first (lines 560–579), and — because the example block is itself syntactically well-formed with a `=======` and `>>>>>>>` — treats it as `hunkIndex 0`, splicing the model's real resolution into the documentation example instead of the real conflict, while the genuine conflict block, now seen as `hunkIndex 1` with no resolution available (`hunkIndex < hunkResolutions.length` is false, per line 585), is silently dropped/left with markers intact [11](#0-10) .
5. The victim commits and pushes a file containing corrupted documentation and an unresolved (or wrongly resolved) real conflict, without any warning, because the "reasoning" text shown to them still describes the correct, intended fix for the true conflict.

Note: I could not fully trace the exact structured hunk-boundary extraction logic in `app/src/lib/copilot-conflict-context.ts` (only grep hits were available, not full file contents) due to index/tool limits, so the precise offset-tracking mechanism used to build `IFileConflictContext.hunks` is not fully confirmed — a Devin session with full file access should verify this file to confirm the exact extraction method and refine the fix accordingly.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L27-41)
```typescript
export interface IFileResolution {
  /** Repository-relative file path that was resolved. */
  readonly path: string
  /** The fully resolved file content (all conflict markers removed). */
  readonly resolvedContent: string
  /** Human-readable explanation of how and why conflicts were resolved this way. */
  readonly reasoning: string
  /**
   * For delete-vs-modify conflicts: the model's recommendation.
   * When present, `resolvedContent` is not meaningful — the resolution
   * is applied as a `ManualConflictResolution` (keep = non-deleted side,
   * delete = deleted side).
   */
  readonly deleteConflictAction?: 'keep' | 'delete'
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-520)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

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
