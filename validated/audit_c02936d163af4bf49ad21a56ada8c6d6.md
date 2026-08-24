### Title
Silent loss of unresolved merge-conflict hunks in `reassembleResolvedFile()` when model hunk count/order desyncs from on-disk markers - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot-based conflict resolver reassembles a resolved file by walking the original on-disk content and replacing each `<<<<<<<...=======...>>>>>>>` marker block with the `i`-th entry of the model's `hunkResolutions` array, matched **purely by array order**, not by content or line number [1](#0-0) . This is structurally the same "capped/partial application against an ordered sequence" pattern as the Peapods `_swapV2` bug: a fixed intermediate quantity (there: `maxSwap`, here: `hunkResolutions.length`) is consumed positionally against a longer real sequence (there: multi-hop path, here: the file's actual conflict-marker blocks), and whatever falls outside that count is silently dropped rather than causing a hard failure at the point of use.

### Finding Description
`reassembleResolvedFile` scans `rawContent` line-by-line. For every well-formed conflict block it finds, it does:
```
if (hunkIndex < hunkResolutions.length) {
  const resolved = hunkResolutions[hunkIndex].resolvedContent
  if (resolved.length > 0) {
    resultLines.push(...resolved.split(/\r?\n/))
  }
}
hunkIndex++
``` [2](#0-1) 

If `hunkIndex` reaches or exceeds `hunkResolutions.length`, the code still consumes/skips the marker block (`i = closingIndex + 1`) but appends **nothing** — not the conflict markers, not "ours", not "theirs", nothing. The conflict content for that block silently vanishes from the resulting file, with no error, no warning, and no marker left behind for the user to notice.

There is a guard, `validateResolutionPaths`, that throws when `resolution.hunks.length !== expectedCount` for a file [3](#0-2) . This closes the *count*-mismatch case, similar to how a naive fix might check `maxSwap` against total swap need. However this only validates **count**, not **order or identity**. `reassembleResolvedFile`'s own doc comment states resolutions are "matched by order, not by line number" [4](#0-3) , and nothing anywhere cross-checks that `hunkResolutions[k]` actually corresponds to conflict block `k` in the file that was re-read from disk at reassembly time versus the file that was scanned at context-build time. Because the "expected hunks" count used for validation is computed from `IFileConflictContext.hunks`, which was extracted via `extractConflictHunks` from disk content read once, and reassembly re-parses `rawContent` with the same array — if the on-disk file changes between context extraction and reassembly (e.g., a concurrently running git operation, a hook, or another conflict-resolution pass touching the same working directory), the counted/expected hunk boundaries can diverge from what `reassembleResolvedFile` walks over. Since `hunkIndex < hunkResolutions.length` is the only bound check, any conflict block encountered beyond the validated count is dropped with zero signal — exactly the "leftover/stuck" cap-truncation failure mode from the seed report, but manifesting as **silent deletion of both sides of an unresolved conflict from the file** rather than as stuck tokens.

The attacker-reachable input into this pipeline is fully repo-controlled: a maliciously crafted repository (cloned/fetched) can contain files engineered to produce ambiguous or malformed conflict-marker sequences (nested/adjacent marker-like text, mixed diff2/diff3 markers, marker text embedded in string literals) designed to desynchronize the "how many real conflict blocks exist" count between the extraction pass and the reassembly pass, or to induce the LLM into producing a resolutions array whose *length* happens to match but whose ordering/mapping is not what a human reviewer would expect.

### Impact Explanation
If a conflict block is silently dropped during reassembly, the final `resolvedContent` written to disk and then staged/committed by the user is **missing intentional code from both branches for that hunk**, without conflict markers and without any indication in the diff/summary UI that anything was lost. This falls squarely under "silent corruption of what the user commits or pushes" — the user reviews a summary and a diff that (per the design intent) is supposed to represent the model's replacement of only the marked regions, but a dropped hunk means arbitrary application logic silently disappears from the merged result. This is a correctness/integrity bug with security-relevant consequences (e.g., silently dropping an added security check, an updated dependency pin, or a permission gate that was part of one side's conflicting change).

### Likelihood Explanation
Likelihood is moderate-to-low to trigger *reliably* as a repo-controlled attack because the primary guard (`validateResolutionPaths` hunk-count equality check) does catch the common case of the model returning too few/many hunks. The residual risk is narrower: it requires either (a) a race/mutation of on-disk content between context extraction and reassembly, or (b) crafting conflict content that causes `extractConflictHunks` (context-build time) and `reassembleResolvedFile`'s own marker-scanning (reassembly time) to disagree on the number of well-formed blocks despite passing the length check — e.g., by including malformed/ambiguous marker sequences that one parser treats as N blocks and the other treats differently. I could not fully verify `extractConflictHunks`'s exact boundary-detection rules against `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in the time available, so whether such a divergence is currently reachable purely from repo content is unconfirmed and would need dedicated fuzzing of both parsers with adversarial marker sequences.

### Recommendation
- Make `reassembleResolvedFile` fail loudly (throw) instead of silently continuing when `hunkIndex >= hunkResolutions.length` is hit at a real conflict block, rather than only checking aggregate counts upstream.
- Re-derive the "expected hunk count" for validation from the *same* parse pass used by `reassembleResolvedFile` (i.e., share one single source of truth for what counts as a conflict block) instead of maintaining two independent marker-scanning implementations (`extractConflictHunks` vs. the regexes in `reassembleResolvedFile`).
- Re-read and re-verify the file's conflict markers immediately before splicing (not just count them at context-build time), and abort the whole-file resolution if the file changed on disk since context extraction.

### Proof of Concept
Conceptual PoC (not confirmed executable given available context, since full `extractConflictHunks` internals were not inspected):
1. Prepare a merge conflict in a file with two conflict blocks.
2. Cause the Copilot response to validate its hunk count against `IFileConflictContext.hunks.length` (e.g., 2) via `validateResolutionPaths`, so validation passes.
3. Arrange the on-disk content at reassembly time (or the marker-scanning logic in `reassembleResolvedFile`) to actually contain a conflict block that `reassembleResolvedFile`'s scanner treats as index ≥ 2 (e.g. via a malformed/edge-case marker pattern that `extractConflictHunks` folds differently than the reassembly scanner does).
4. Observe that `reassembleResolvedFile` drops that block's content entirely (no markers, no ours, no theirs) in the file that gets written and eventually committed, producing a silently corrupted merge result with no user-visible warning: [2](#0-1) .

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-538)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L584-591)
```typescript
      // Splice in the resolved content for this hunk
      if (hunkIndex < hunkResolutions.length) {
        const resolved = hunkResolutions[hunkIndex].resolvedContent
        if (resolved.length > 0) {
          resultLines.push(...resolved.split(/\r?\n/))
        }
      }
      hunkIndex++
```
