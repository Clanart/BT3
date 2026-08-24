### Title
Divergent conflict-marker boundary detection between `extractConflictHunks` and `reassembleResolvedFile` can silently splice AI-resolved content into the wrong region of a committed file - (File: app/src/lib/copilot-conflict-context.ts, app/src/lib/copilot-conflict-resolution.ts)

### Summary
GitHub Desktop's Copilot-assisted merge-conflict resolution feature parses conflict markers twice, using two independently-implemented, non-equivalent state machines: `extractConflictHunks()` (used to build the model prompt and to compute the expected hunk count) and `reassembleResolvedFile()` (used to splice the model's per-hunk resolution back into the on-disk file before it is written and eventually staged/committed). Because a repository under the attacker's control can contain ordinary content that happens to match the `=======` (7-equals) separator regex, the two parsers can disagree about where the "ours" section ends and the "theirs" section begins within a single conflict block, while still agreeing on the overall hunk count and outer marker boundaries. The result is that content that the model resolved based on a mislabeled ours/theirs view gets spliced, with high confidence and no additional validation of positional correctness, into the real conflict region of the file the user is about to commit.

### Finding Description
`extractConflictHunks` in [1](#0-0)  collects the "ours" side of a conflict block by scanning forward and stopping at the **first** line that matches either `baseMarker` or `separatorMarker`: [2](#0-1) 

It only tests for `theirsMarker`, not for a possible embedded/extra `separatorMarker`, while collecting the "theirs" side: [3](#0-2) 

By contrast, `reassembleResolvedFile` in [4](#0-3)  determines the boundaries of the same conflict block with a different lookahead: it scans forward from the `<<<<<<<` marker and does **not** stop on the first `=======`-looking line — it simply records `hasSeparator = true` and keeps scanning until it finds the real `>>>>>>>` closer: [5](#0-4) 

Both functions correctly find the same **outer** boundaries (the real `<<<<<<<` and `>>>>>>>`), so the hunk count sent to the model and expected back (`validateResolutionPaths` in [6](#0-5) ) is unaffected. What differs is which lines inside that one hunk `extractConflictHunks` labels as `oursContent` vs `theirsContent` whenever the genuine "ours" or "base" text of the conflicting file itself contains a line consisting of exactly seven `=` characters (a common pattern in ASCII dividers, Markdown horizontal rules, YAML front-matter, or code comments). In that case `extractConflictHunks` prematurely truncates `oursContent`/`baseContent` and folds the remainder — including what is actually still "ours"/"base" text — into `theirsContent`. The model is asked to resolve a conflict based on that mislabeled content, but the accepted resolution is still spliced wholesale into the real, correctly-bounded conflict region by `reassembleResolvedFile`, because that function trusts positional/ordinal correspondence between hunks (`hunkIndex < hunkResolutions.length`) rather than re-validating the sub-content boundaries it used for parsing (comment at [7](#0-6)  explicitly documents that matching is "by order, not by line number").

The invariant broken is: *the content shown to (and reasoned about by) the model must match the content that is ultimately committed for that same conflict region*. The guard that would normally catch this — validating hunk boundaries/derived content against the original file before write — does not exist; only hunk **count** and top-level marker presence are checked (`validateResolutionPaths`, `getHunkSkipReason`).

### Impact Explanation
A malicious or compromised remote/branch can be crafted so that ordinary, legitimate-looking file content participates in a conflict against the user's local changes, and that content contains a line of exactly `=======`. When the user resolves this conflict with Desktop's Copilot conflict-resolution feature, the model is fed a mislabeled ours/theirs split, and its resolution is spliced into the file at the correct outer position but built from the wrong understanding of which side is which. The user reviews (at best) a diff that looks plausible but is derived from swapped/mixed ours-theirs semantics, and the corrupted content is written to disk and can be staged and committed/pushed without the user realizing the underlying resolution logic was fed incorrect data. This matches "silent corruption of what the user commits or pushes" driven purely by attacker-controlled repository content (a file the user is merging/rebasing against).

### Likelihood Explanation
Requires: (1) the user to hit a real merge/rebase conflict against attacker-influenced content, and (2) the user to invoke the Copilot-assisted conflict resolution feature on that file. No local access, no special privileges, and no unnatural steps beyond normal use of an advertised feature are needed. The triggering condition (a `=======`-shaped line appearing in normal file content near a conflict) is plausible for documentation, changelogs, or generated files with dividers, making this a realistic, if not universal, trigger. I was not able to trace, within the available tool budget, the exact UI/store call site that invokes `reassembleResolvedFile` and performs the subsequent file write/stage — this analysis is based on the parsing/reassembly logic itself and its accompanying tests/docs, so likelihood should be treated as an estimate pending confirmation of the full call chain.

### Recommendation
Make `reassembleResolvedFile`'s hunk-boundary detection identical to (or reuse) `extractConflictHunks`'s boundary logic so both stages of the pipeline agree on where "ours"/"base"/"theirs" begin and end for a given hunk. Additionally, before splicing a resolution back into the file, re-derive the ours/theirs content for that same hunk index from the original file and confirm it matches what was actually sent to the model (or store the exact matched substrings/offsets from `extractConflictHunks` and reuse them directly in `reassembleResolvedFile` instead of re-parsing independently).

### Proof of Concept
1. Attacker pushes/maintains a branch whose file (e.g. `NOTES.md`) contains, among unrelated content, a line consisting of exactly `=======` inside a section that will end up on the "ours" side of a future conflict, e.g.:
```
Some heading
=======
More real "ours" text that should stay
<<<<<<< inserted only to illustrate — real conflict created via merge
```
2. Victim merges/rebases against this branch, producing a real conflict block:
```
<<<<<<< HEAD
Some heading
=======
More real "ours" text that should stay
>>>>>>> feature
```
3. `extractConflictHunks` (app/src/lib/copilot-conflict-context.ts:201-237) truncates `oursContent` to `"Some heading"` (stopping at the embedded `=======`) and incorrectly classifies `"More real \"ours\" text that should stay"` as `theirsContent`.
4. Copilot resolves the (mislabeled) conflict and returns `resolvedContent` based on the wrong understanding of which text is "ours" vs "theirs".
5. `reassembleResolvedFile` (app/src/lib/copilot-conflict-resolution.ts:559-596) correctly finds the real `<<<<<<<`/`>>>>>>>` boundaries (its lookahead does not stop at the embedded separator) and splices the mis-derived `resolvedContent` into that exact region, producing a file whose content silently differs from what an accurate ours/theirs split would have produced — which the user may then stage and commit unaware of the mismatch.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-237)
```typescript
export function extractConflictHunks(
  fileContent: string,
  contextLines: number = 3
): ReadonlyArray<IConflictHunk> {
  const lines = fileContent.split(/\r?\n/)
  const hunks: Array<IConflictHunk> = []

  let i = 0
  while (i < lines.length) {
    if (!oursMarker.test(lines[i])) {
      i++
      continue
    }

    const oursStart = i + 1
    const oursLines: Array<string> = []
    const baseLines: Array<string> = []
    let hasBase = false
    const theirsLines: Array<string> = []
    let hunkEnd = -1

    i = oursStart
    // Collect ours content
    while (i < lines.length) {
      if (baseMarker.test(lines[i])) {
        hasBase = true
        i++
        break
      }
      if (separatorMarker.test(lines[i])) {
        i++
        break
      }
      oursLines.push(lines[i])
      i++
    }

    // If diff3, collect base content until separator
    if (hasBase) {
      while (i < lines.length) {
        if (separatorMarker.test(lines[i])) {
          i++
          break
        }
        baseLines.push(lines[i])
        i++
      }
    }

    // Collect theirs content until closing marker
    while (i < lines.length) {
      if (theirsMarker.test(lines[i])) {
        hunkEnd = i
        i++
        break
      }
      theirsLines.push(lines[i])
      i++
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L529-547)
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
