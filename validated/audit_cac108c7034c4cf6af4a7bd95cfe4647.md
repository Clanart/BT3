## Analog Found: Copilot merge-conflict resolution splices LLM hunk output using a marker scan independent from the one that built the prompt

### Title
Silent corruption of merge-conflict resolutions from unsynchronized conflict-marker re-scanning in `reassembleResolvedFile` - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The original report's broken invariant is: a function assumes a downstream structural property ("the tier that was reached is the one intended") that was never actually verified against the structure it was derived from, and the mismatch is only detectable by exhausting a resource. The Desktop analog replaces the resource-exhaustion angle with a **structural-trust** angle: Desktop's AI conflict-resolution feature builds the list of conflict hunks shown to the model with one marker parser, then re-locates those same hunks in the on-disk file with a second, independently written marker parser when splicing the model's answer back in. Only the hunk *count* is cross-checked, never hunk *position*.

### Finding Description
`buildConflictContext` reads each conflicted file from disk and calls `extractConflictHunks`, which scans for `<<<<<<<` / `|||||||` / `=======` / `>>>>>>>` lines to build the ordered hunk list sent to the model: [1](#0-0) 

Later, `reassembleResolvedFile` independently re-scans the *same raw file content* with its own, separately-declared marker regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) to find where to splice each of the model's per-hunk resolutions back into the file, matching purely by **positional order**, not by content identity: [2](#0-1) 

The only cross-check performed between the model's response and the original context is `validateResolutionPaths`, which verifies that the *number* of hunks returned for a path equals the *number* of hunks originally extracted — it never verifies that the two independent scans agree on where those hunks are located in the file: [3](#0-2) 

Because the marker-detection regexes are declared and maintained separately in two different files (`copilot-conflict-context.ts` vs `copilot-conflict-resolution.ts`), any file whose content legitimately contains column-0, 7-character marker-like lines (documentation of Git conflict markers, generated fixtures, embedded diff snippets, etc.) sitting near a real conflict is parsed twice, by two pieces of logic that were not written to be provably identical. As long as the two scans happen to agree on the *count* of hunks (which is easy — both trivially count `<<<<<<<`/`>>>>>>>` pairs the same way in the common case) but could disagree on *which lines* delimit hunk *N*, the reassembly step will splice the model's resolution for hunk *N* into the wrong span of the file, silently, with no validation catching it and no error surfaced to the user.

### Impact Explanation
The result is exactly the excluded-vs-included boundary in the task's impact list: **silent corruption of what the user commits**. The user resolves a merge/rebase/cherry-pick conflict via the Copilot dialog, sees a plausible-looking diff, accepts it, and commits/pushes a file whose content differs from both what the model actually decided and what a correct reassembly would have produced — with no error, warning, or validation failure, because `validateResolutionPaths` only checks counts. Since `buildConflictContext` operates on files from the attacker's side of the merge (`ourCommits`/`theirCommits`, i.e., content pulled in via clone/fetch), the attacker fully controls the file bytes that drive both scans.

### Likelihood Explanation
This requires the file to contain marker-like lines outside of genuine conflicts, colocated with a real conflict, in a repository the victim fetches/merges and then resolves using Desktop's Copilot-assisted conflict resolution feature — no admin rights, no local access, no pre-existing malware, and no unusual user steps beyond the feature's normal intended use (accepting an AI-suggested resolution). This is a moderate-likelihood, architecture-level defect: the duplicated, unsynchronized marker-parsing logic is a durable weakness independent of any single crafted file, since any future edge case where the two regex sets diverge (diff3 handling, CRLF handling, marker-with-trailing-content handling, etc.) reproduces the same silent-corruption class of bug.

### Recommendation
Eliminate the duplicate parsing implementation. `reassembleResolvedFile` should not re-scan the raw file for markers at all — it should reuse the exact hunk boundaries (line ranges) already computed by `extractConflictHunks` when the context was built, threading that structural information through to the reassembly step instead of re-deriving it from a second regex set. If re-scanning is unavoidable, `validateResolutionPaths` (or an equivalent) must verify that the re-scan's hunk boundaries are byte-for-byte identical to those originally extracted, not merely that the counts match, and should hard-fail (not silently proceed) on any mismatch.

### Proof of Concept
Conceptual PoC (cannot be executed here, but follows directly from the cited code):
1. Attacker crafts `README.md` in a branch that will be merged, containing a standalone documentation block using literal Git conflict markers at column 0 (e.g. explaining how to resolve conflicts), positioned a few lines above/below an unrelated real conflicting region in the same file.
2. Victim merges this branch, gets a real conflict in `README.md`, and asks Copilot to resolve it via the Desktop conflict-resolution dialog.
3. `buildConflictContext`/`extractConflictHunks` and `reassembleResolvedFile` each independently scan the file for marker lines; because they are separate implementations, an edge case (e.g., how each treats the boundary between the documentation block and the real conflict, or trailing-whitespace/CRLF variants) causes hunk index *N* to correspond to a different line-range in each scan while the *count* still matches.
4. `validateResolutionPaths` passes (hunk counts equal), `reassembleResolvedFile` splices the model's resolution for hunk *N* into the wrong span, and the victim commits/pushes a corrupted file without any indication of error.

Note: I was not able to fully diff the exact regex definitions in `copilot-conflict-context.ts` (`oursMarker`, `baseMarker`, `separatorMarker`, `theirsMarker`) against `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in `copilot-conflict-resolution.ts` byte-for-byte due to running out of tool iterations — the existence of two separately declared marker-regex sets driving a count-only validation is confirmed from the code above, but I could not confirm a concrete divergent input in this session. A Devin session with full file access could pull both regex definitions side-by-side and construct a concrete divergent test file to prove the exploit deterministically.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L429-460)
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
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
```

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
