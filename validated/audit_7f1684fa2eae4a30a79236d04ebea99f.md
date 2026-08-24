### Title
Order-based (non-anchored) hunk reassembly in Copilot conflict resolution can misapply resolved content to the wrong conflict block, silently corrupting committed code - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The HAL-01 report's underlying flaw is that two different code paths use *inconsistent units/counts* when computing a value that is later combined (`rewardPerToken` scales by LP-token decimals, `earned` unscales by reward-token decimals), and nothing enforces that the two are the same, so values can be silently corrupted. The closest analog in GitHub Desktop is in the Copilot merge-conflict auto-resolution feature: the number/identity of "conflict hunks" is established once when building the prompt context, and a second, independent pass re-derives conflict blocks from the raw on-disk file using a bare regex scan when splicing the model's resolutions back in. The two counts are validated for length equality only, never anchored to the same underlying block, so any divergence between what was counted originally and what `reassembleResolvedFile` finds causes the model's resolved content for conflict *N* to be spliced into the wrong conflict block in the file that Desktop subsequently writes and lets the user commit.

### Finding Description
`reassembleResolvedFile` walks the raw file content line-by-line and locates conflict blocks purely with regexes: [1](#0-0) 

It matches `hunkResolutions[hunkIndex]` to the `i`‑th conflict block it discovers **by traversal order only** — there is no anchor (no hash of the original marker block, no comparison against the "ours"/"theirs" text that was actually sent to the model): [2](#0-1) 

Malformed marker blocks (an opening `<<<<<<<` line without both a following `=======` and `>>>>>>>` before EOF) are explicitly *not* treated as conflicts and are copied through as ordinary content: [3](#0-2) [4](#0-3) 

The only integrity check performed before reassembly is `validateResolutionPaths`, which compares the **count** of hunks the model returned against `expectedHunkCounts` built from the file-conflict context (`IFileConflictContext`) that was assembled earlier (in `copilot-conflict-context.ts`, which I was not able to fully inspect in this session): [5](#0-4) 

This is the same class of bug as HAL-01: one function (`rewardPerToken`) computes a value using one implicit convention (pool decimals) while a second function (`earned`) consumes it under a different, unverified convention (reward-token decimals), and only a superficial invariant is checked, not that the two computations are actually expressed in the same terms. Here, the "convention" is *how many conflict blocks exist and in what order*, computed once by the context builder and a second time, independently, by `reassembleResolvedFile`'s bare-regex scan. If those two independent scans of the same physical file content ever disagree in count or ordering — e.g., a file whose non-conflicted regions legitimately contain 7-character marker-like lines (common in files that document git conflicts, in patch/diff fixtures, or in nested/nested nested nested conflicts), or a malformed marker block that the context builder counted as a conflict but that `reassembleResolvedFile`'s look-ahead treats as ordinary content copy-through — the length check in `validateResolutionPaths` can still pass (both sides report N hunks) while the *identity* mapping between hunk index and physical block is wrong, because the two counts were derived by different code from different assumptions.

### Impact Explanation
Because Desktop uses `reassembleResolvedFile`'s output as the new, complete file content that gets written to disk and staged for a user-approved commit, a hunk/marker misalignment causes the model's resolution for one conflict to be silently written into a different, unrelated location in the file. This is exactly the "silent corruption of what the user commits" impact class called out as valid: the user reviews a resolution dialog believing hunk-N content was applied to conflict-N, but the actual bytes written to the working tree (and subsequently committed/pushed) can be a different piece of resolved code than the one intended for that location, with no application-level check tying resolved content back to its originating block.

### Likelihood Explanation
This requires no privileged access — only an attacker-influenced repository (a branch/PR whose merge produces a conflicted file containing content that confuses the bare regex conflict-block scanner, e.g., embedded marker-like text or malformed marker blocks) combined with the user invoking Desktop's "Resolve with Copilot" feature. The existing safeguards (`validateResolutionPaths`) only check **counts**, not identity/order correspondence against the same parse used to build context, so they would not catch a scenario where the context-builder and the reassembly scanner disagree on how many/which blocks exist. I was unable to fully verify, in this session, whether `copilot-conflict-context.ts` uses the exact same marker-scanning regexes as `reassembleResolvedFile`; if it does use identical logic, the two counts will always agree and this issue reduces to a lower-likelihood edge case restricted to genuinely malformed/edge-case marker content. I could not confirm this with certainty due to running out of tool iterations before reading that file's contents in full, so this should be treated as a **plausible, not fully confirmed**, analog.

### Recommendation
- Anchor each resolved hunk to a specific marker block using an identity marker (e.g., a numbered/opaque marker string per conflict inserted for the round trip, or a hash of the original block content) rather than purely relying on traversal order.
- Ensure the same single parsing routine (not two independently maintained regex passes) is used both to build the `IFileConflictContext` sent to the model and to reassemble the file, so any change to marker-parsing edge cases (malformed markers, diff3 `|||||||` base markers, marker-like text in non-conflicted regions) affects both consistently.
- Add a stricter validation step that recomputes the conflict-block count/positions from the raw file at reassembly time and hard-fails (rather than silently proceeding) if it does not exactly match the context used to prompt the model.

### Proof of Concept
Conceptual PoC (not executed, based on static code reading):
1. Craft a repository/branch such that a merge produces a conflicted file with an opening `<<<<<<<` marker line whose corresponding `=======`/`>>>>>>>` is missing before EOF in one conflict region, while the file also contains other legitimate, well-formed conflict blocks after it.
2. If the upstream context builder (`copilot-conflict-context.ts`) parses this file differently (e.g., counts the malformed block as a conflict, or handles the malformed content differently) than `reassembleResolvedFile`'s look-ahead loop — which explicitly copies malformed blocks through as regular content — the hunk-index-to-block mapping shifts by one for every subsequent well-formed conflict block in the file.
3. `validateResolutionPaths` only checks `resolution.hunks.length !== expectedCount`, which can still match in total count even though hunks are now offset by one, so the check passes.
4. `reassembleResolvedFile` splices `hunkResolutions[0]` into the wrong (shifted) conflict block, `hunkResolutions[1]` into another wrong block, etc., producing a file that Desktop writes to disk and presents as "resolved," silently containing swapped/misapplied merge content that the user then commits.

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L540-544)
```typescript
 * A `<<<<<<<` line that is not followed by both a `=======` separator and
 * a closing `>>>>>>>` before EOF is treated as regular file content (not a
 * conflict block) and copied through unchanged to avoid data loss from
 * malformed or stray markers.
 *
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
