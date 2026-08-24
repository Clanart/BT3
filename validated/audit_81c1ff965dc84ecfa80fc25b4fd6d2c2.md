[1](#0-0) 

### Title
Copilot conflict-resolution reassembly matches per-hunk model output to on-disk conflict blocks purely by order, letting attacker-influenced hunk-count/order mismatches silently corrupt committed merge content - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`reassembleResolvedFile` splices the AI model's per-hunk resolved content into the on-disk file by counting conflict-marker blocks and matching them **positionally** (`hunkIndex`) against the model's `hunkResolutions` array, never verifying that the model actually returned one resolution per real conflict block, in the right order. This mirrors the root cause of the Solidity report: a downstream consumer trusts a returned value/shape from an external, attacker-influenceable process (`ODOS_ROUTER`'s `amountOut` there; Copilot's JSON `hunks` array here) instead of validating it against the actual ground truth (`balanceBefore`/`balanceAfter` there; the real conflict-marker blocks found in the file here).

### Finding Description
`buildConflictContext` (`app/src/lib/copilot-conflict-context.ts:367-469`) reads each conflicted file from disk and calls `extractConflictHunks` to enumerate the real `<<<<<<<`/`=======`/`>>>>>>>` blocks. This context — including file content, PR/commit metadata pulled from the GitHub API, and surrounding code — is sent to the Copilot SDK, which returns a JSON payload parsed by `parseCopilotConflictResolution` (`app/src/lib/copilot-conflict-resolution.ts:379-466`). That parser validates only that `hunks` is a non-empty array of objects with a `resolvedContent` string free of leftover markers — it never checks that `rawHunks.length` equals the number of real conflict blocks in the file, nor that resolutions are in the correct order.

`reassembleResolutions` → `reassembleResolvedFile` then walks the raw on-disk content and, for every well-formed conflict block it encounters, pulls the next resolution off the array by incrementing `hunkIndex`: [2](#0-1) 

There is no cross-check against the file's actual hunk count (which `buildConflictContext`/`extractConflictHunks` already computed and could have been carried through for validation). Consequences of a mismatched `hunks` array:
- If the model returns **fewer** hunks than exist in the file, every block from that point on is filled with the *wrong* resolution (an off-by-one shift), and the last real block gets nothing inserted at all — its content is silently deleted.
- If the model returns hunks **out of order** relative to their on-disk position, resolved content is spliced into the wrong location.

Because a repository's file content, commit messages, and PR title/body (all attacker-controlled when the user resolves conflicts against a fetched/foreign branch or PR) are fed verbatim into the model's context in `buildConflictContext`, a crafted PR description or in-file comment is a classic prompt-injection vector that can steer the model into emitting a malformed/mismatched `hunks` array, deterministically triggering this positional-matching flaw.

### Impact Explanation
The reassembled `resolvedContent` is written directly to disk and staged in `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7268`), then committed as part of the merge/rebase/cherry-pick. This is "silent corruption of what the user commits or pushes": the app confidently reports each file as "resolved by Copilot," but the actual staged content can have swapped or dropped hunks — e.g., re-introducing code the user believed was replaced, or dropping a hunk's changes (including potential security-relevant fixes) without any error, warning, or exception anywhere in the pipeline.

### Likelihood Explanation
The user must explicitly invoke "Resolve with Copilot" on a real merge/rebase/cherry-pick conflict, which is a supported, expected workflow (not an "unnatural" step) whenever merging a branch/PR containing attacker-supplied content. No local access, credentials, or admin rights are required — only that the user resolves conflicts against a repository/PR where the attacker controls file content, commit messages, or PR metadata. The bug is purely a code-side invariant gap (missing count/order validation) reachable any time the model's structured-output contract is violated, whether by injection or ordinary model unreliability.

### Recommendation
Validate `rawHunks.length` against the count of real conflict blocks computed by `extractConflictHunks` for that file before accepting a resolution (reject/flag mismatches as `CopilotValidationError`, same pattern already used for malformed payload shapes). Ideally, have the model return an explicit anchor (e.g., a hash or index of the specific conflict block) rather than relying on array order, so reassembly can validate identity rather than trusting position.

### Proof of Concept
1. A hostile PR/branch is merged; one conflicted file has 3 conflict-marker blocks.
2. Model context includes attacker-controlled PR body/commit message text (prompt injection) instructing the model to only emit 2 `hunks` entries for that file, or reorder them.
3. `parseCopilotConflictResolution` accepts the payload because it only checks each hunk object's shape, not the count (`app/src/lib/copilot-conflict-resolution.ts:397-427`).
4. `reassembleResolvedFile` walks the 3 real conflict blocks but only has 2 resolutions; the first block gets resolution[0], the second gets resolution[1], and the third gets nothing inserted — its content vanishes silently (`app/src/lib/copilot-conflict-resolution.ts:584-591`).
5. The user clicks "Continue Merge"; `_applyCopilotConflictResolutions` writes this corrupted content to disk and `git add`s it, and it is committed/pushed without any indication of the missing/misplaced hunk.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-599)
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
