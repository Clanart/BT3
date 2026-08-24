## Title
Copilot conflict reassembly matches resolutions to conflict markers by scan-order, not by validated identity, allowing attacker-crafted marker-like text to silently misapply merged code - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`reassembleResolvedFile()` splices each per-hunk AI resolution into the on-disk conflicted file by re-scanning the raw file for `<<<<<<<`/`=======`/`>>>>>>>` marker blocks with a simple regex and incrementing a local `hunkIndex` counter for every block it encounters, live, during that scan. This is the same bug shape as the `Goldilend.repay()` report: a running counter/index that is supposed to track one specific quantity (the resolution that was generated and validated for conflict *N*) is instead advanced from a different, independently-computed source (the *Nth marker block found by a fresh regex scan*). If the two counting mechanisms ever diverge, the "increment"/index used to pick the applied content is wrong, and the wrong resolution text gets silently written into the file that is about to be committed.

### Finding Description
`reassembleResolvedFile` is documented as matching "by order, not by line number": [1](#0-0) 

The implementation walks the raw file, finds every `<<<<<<<` line (via `reassemblyOursMarker`), looks ahead for a `=======` and `>>>>>>>`, and — for every such block found — pulls `hunkResolutions[hunkIndex]` and increments `hunkIndex`: [2](#0-1) 

Before this runs, `validateResolutionPaths` only checks that the *count* of hunks returned by the model equals `expectedHunkCounts`, which is derived from `IFileConflictContext.hunks.length` — a count produced during the earlier context-extraction phase (`copilot-conflict-context.ts`, not included in the excerpts reviewed here): [3](#0-2) 

The vulnerability is that **two independent mechanisms decide "how many conflicts are in this file"**:
1. The context-extraction phase (whatever markers/parsing it uses to build `IFileConflictContext.hunks`), which drives `expectedHunkCounts` and thus what the LLM is told and asked to return.
2. `reassembleResolvedFile`'s own live, permissive regex scan (`^<{7}(?:\s|$)` / `^={7}$` / `^>{7}(?:\s|$)`), which drives the actual index used to pick which resolution text gets spliced where.

`validateResolutionPaths` only compares the *count* from source (1) against the length of the array returned by the model — it never re-derives or cross-checks against the count that `reassembleResolvedFile` will independently compute from the raw file at splice time. If a conflicted file (fully attacker-controlled content, since it comes from a branch/commit the user merges, rebases onto, or cherry-picks) contains any line that *incidentally* matches the reassembly regex (e.g. a source file, README, or test fixture that legitimately contains 7+ `<`, `=`, or `>` characters at the start of a line — common in files that document or test git conflict markers, in generated diff/patch files, or in code using chained comparison/shift operators formatted at column 0) but that the context-extraction phase does *not* treat as a real conflict block (or vice-versa, e.g. it filters out already-resolved-looking blocks differently), the two hunk counts will still validate as "equal in total count" while being *misaligned in position*. From that point on, `hunkIndex` in the reassembly loop drifts, and every subsequent real conflict in the file gets the *wrong* AI-generated resolution spliced into it — content that was reasoned about for a different conflict, potentially discarding one side's real changes or inserting unrelated code — with no error, no warning, and no diff review step that would catch a mismatch of this kind before the file is staged (`git(['add', ...])`) and committed.

### Impact Explanation
This directly matches the requested "silent corruption of what the user commits" impact class. A user merging/rebasing a branch that contains attacker-crafted content and using GitHub Desktop's Copilot conflict-resolution feature could have code silently swapped between conflict regions in a resolved file — e.g., accepting the wrong side of a security-relevant conflict, or splicing unrelated resolved content into a sensitive block — without any indication that anything is wrong, since the reassembled content is treated as fully resolved and gets staged and committed as-is.

### Likelihood Explanation
This requires: (a) the victim to use the AI conflict-resolution feature, and (b) a conflicted file whose content includes marker-like text that the context-extraction and reassembly-scan phases classify differently. Condition (b) is plausible but not trivially guaranteed — I was not able to inspect `app/src/lib/copilot-conflict-context.ts` (the module that builds `IFileConflictContext.hunks`) in this session to confirm exactly how it detects/parses conflict blocks and whether its detection logic is provably identical to the regexes used in `reassembleResolvedFile`. This is the main open uncertainty in this analysis; the divergence is architecturally possible (two independently-implemented "find the conflict blocks" passes) but I could not fully verify a concrete parsing difference between the two given tool-call limits in this session.

### Recommendation
Have `reassembleResolvedFile` (or its caller) use the *same* hunk-identification data structure/positions produced during context extraction (e.g., explicit byte/line offsets recorded in `IFileConflictContext.hunks`) rather than an independent regex re-scan, so there is a single source of truth for "where does hunk N begin/end." At minimum, after reassembly, re-count the marker blocks found by the live scan and hard-fail (`CopilotValidationError`) if that count does not match `hunkResolutions.length`, instead of silently trusting index alignment.

### Proof of Concept
Conceptual PoC (not executed, since this requires a live LLM round-trip and full Desktop app to exercise end-to-end):
1. Attacker pushes a branch that, when merged into the victim's branch, produces a real git conflict in `file.ts`, plus the file also contains an unrelated block of text at content that happens to look like a second conflict per the reassembly regex but is *not* treated as a conflict hunk by the context-extraction phase (e.g. a code/test fixture literally containing lines starting with `<<<<<<<` / `=======` / `>>>>>>>`, such as a unit test asserting on conflict-marker parsing, similar to the test fixtures already present in `app/test/unit/copilot-conflict-resolution-test.ts`).
2. `expectedHunkCounts` for `file.ts` is computed from the context-extraction phase as, say, `1` real conflict; the model is asked for and returns exactly `1` hunk resolution; `validateResolutionPaths` passes.
3. `reassembleResolvedFile` scans the raw file and finds `2` matching marker blocks (the real one and the incidental look-alike), consuming `hunkResolutions[0]` for whichever block it encounters first in file order — potentially the *wrong* one if extraction ordering differs from scan ordering, or leaving the second block's real conflict markers un-spliced (since `hunkIndex` exceeds `hunkResolutions.length` for the trailing block) so it's stripped away with no content at all.
4. The resulting `resolvedContent` is written to disk and staged via `git add`, and the corrupted result becomes part of the next commit without the user reviewing the actual bytes written.

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-548)
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
 */
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
