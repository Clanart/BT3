### Title
Order-based hunk splicing in Copilot conflict resolution allows silent corruption of committed file content - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The Permit2 report's broken invariant is: a value crossing a trust boundary (an amount) is narrowed/mapped without verifying it still represents the same value, and the mismatch is applied silently in an authorized (signed) operation. The equivalent invariant in GitHub Desktop's Copilot conflict-resolution feature is that per-hunk resolutions produced by an LLM are spliced back into the working file **purely by ordinal position/count**, with no check that hunk *i* in the model's output actually corresponds to conflict hunk *i* in the file. Because the file being merged is attacker-influenced (the "theirs" side of a merge/rebase/cherry-pick from a cloned/fetched repository, plus commit messages fed into the prompt), a crafted repository can cause the model to reorder, drop-and-pad, or otherwise misalign hunk resolutions while still satisfying the only structural check performed (`validateResolutionPaths`, which checks path membership and hunk *count* only). The result is silently written to disk and, once the user accepts it, committed/pushed — corrupting the user's commit without any error being raised.

### Finding Description
`reassembleResolvedFile` walks the raw on-disk file (still containing `<<<<<<<`/`=======`/`>>>>>>>` markers) and replaces each detected conflict block with `hunkResolutions[hunkIndex]`, incrementing `hunkIndex` for every block encountered, matching "by order, not by line number": [1](#0-0) [2](#0-1) 

The only validation performed before reassembly is `validateResolutionPaths`, which checks that the returned file paths match expected ones and that the **count** of hunks per file matches — it never checks that hunk content/order corresponds to the actual conflict it claims to resolve: [3](#0-2) 

`parseCopilotConflictResolution` similarly only validates shape (string type, no residual `<<<<<<<`/`=======` markers) of each hunk's `resolvedContent` — it does not verify that content is consistent with the specific conflict hunk it is meant to replace: [4](#0-3) 

Crucially, the model's input includes attacker-influenced data: recent commit messages and PR titles/descriptions from the "theirs" side of the merge are explicitly fed into the prompt for "intent," per the system prompt: [5](#0-4) 

An attacker who controls a branch/fork that a victim merges, rebases onto, or cherry-picks from (i.e., a "cloned/fetched repository" scenario) fully controls the conflicting hunk content and the commit messages that accompany it. Because the reassembly step trusts ordinal position rather than verifying which resolution belongs to which marker block, a prompt-injection payload embedded in the attacker's commit message or conflicting hunk content (e.g., instructing the model to "reorder resolutions" or to return a benign-looking resolution for hunk 1 that actually contains the payload intended for hunk 2, while keeping the total hunk count identical) can cause the wrong resolved content to be spliced into the wrong location in the victim's file — with no error, since `validateResolutionPaths` and `parseCopilotConflictResolution` both pass.

### Impact Explanation
This matches the "silent corruption of what the user commits or pushes" impact category: the victim reviews a conflict-resolution dialog believing hunk N's reasoning/content corresponds to conflict N, applies it, and commits/pushes a file whose actual content differs from what was intended or reviewed. Depending on the crafted payload, this can silently reintroduce vulnerable code, remove security checks, or plant malicious code changes into a repository the victim controls, entirely through content in a repo the attacker controls (branch, PR, or fork the victim merges/fetches from) — no local access, admin rights, or prior compromise of the victim's machine is required.

### Likelihood Explanation
The path requires the victim to (a) have the Copilot conflict-resolution feature enabled, (b) merge/rebase/cherry-pick a branch containing attacker-crafted conflicting content and commit messages, and (c) accept the AI-generated resolution. This is a natural workflow (resolving conflicts from a PR or fork) rather than an unusual user action, and the trust boundary crossed (feeding foreign repository content into an LLM prompt whose output is spliced back with no content-integrity check) is exactly analogous to the "cast without a bounds check" pattern in the seed report — the guard that exists (`validateResolutionPaths`'s count check) does not stop a count-preserving reordering/substitution attack, just as `type(uint160).max` bound-checking would not have caught a validly-bounded-but-wrong value.

### Recommendation
Bind each hunk resolution to identifying evidence from the corresponding conflict block rather than trusting positional order alone — e.g., have the model echo back a stable per-hunk identifier or a hash/fingerprint of the original conflict markers it is resolving, and verify that identifier in `reassembleResolvedFile`/`validateResolutionPaths` before splicing. At minimum, surface a diff of the actual pre/post content for each hunk to the user for review (not just the model's prose "reasoning"), so silent reordering or substitution is visually detectable before the user commits.

### Proof of Concept
1. Attacker creates a fork/branch whose file `foo.ts` has two conflicting hunks with the "theirs" side crafted so an LLM, given the system prompt's instruction to use "commit messages ... for intent," is nudged (via prompt injection in the commit message or in a code comment inside the conflicting hunk) into returning `resolutions[0].hunks` in swapped order relative to the actual file's hunk order, while keeping `hunks.length` equal to the expected count.
2. Victim clones/fetches this branch and merges it into their own branch, triggering the same conflict.
3. Victim invokes Copilot Resolve Conflicts; `parseCopilotConflictResolution` accepts the response (correct shape, no leftover markers) and `validateResolutionPaths` passes (correct path, correct hunk count).
4. `reassembleResolvedFile` splices hunk 0's content into conflict block 0 and hunk 1's content into conflict block 1 by position — but because the model swapped them, block 0's actual code is replaced with the resolution intended for block 1 (and vice versa).
5. Victim reviews the summary/reasoning text (which describes the intended, not actual, mapping), accepts, and commits — silently pushing incorrect/malicious code with no error surfaced at any validation step. [6](#0-5)

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L195-201)
```typescript
You will receive:
- Labels for both sides (branch names or commit refs)
- Conflict markers from each file (ours, theirs, optionally base)
- Context lines surrounding each conflict
- Delete-vs-modify conflicts where one side deleted a file and the other modified it
- When available: recent commit messages and/or PR title/description for intent

```

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-521)
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
