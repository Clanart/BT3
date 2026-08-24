## Title
Copilot merge-conflict reassembly matches resolutions to conflict blocks by naive line-order scan, allowing pre-existing "look-alike" marker text to desynchronize hunk indices and silently corrupt committed file content - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
`reassembleResolvedFile` (used by the Copilot "Resolve conflicts" feature) walks the on-disk file text line-by-line and treats *any* well-formed `<<<<<<<` / `=======` / `>>>>>>>` sequence as a conflict block to splice model output into, matching purely by encounter order rather than by verified provenance from the actual git conflict parser. [1](#0-0) 

### Finding Description
`reassembleResolvedFile` scans `rawContent` for any line matching `/^<{7}(?:\s|$)/`, then looks ahead for a `=======` and `>>>>>>>` line, and if found treats the whole span as hunk `hunkIndex` and replaces it with `hunkResolutions[hunkIndex].resolvedContent`, incrementing `hunkIndex` regardless of source: [2](#0-1) 

The `hunkResolutions` array, however, is produced by the Copilot model based on a *separate*, presumably more precise, upstream extraction of the real git conflict hunks (`IFileConflictContext.hunks`), whose count is enforced against the model's output in `validateResolutionPaths` by comparing `resolution.hunks.length` to `expectedFiles` hunk counts. [3](#0-2) 

This creates an invariant mismatch: `reassembleResolvedFile`'s notion of "hunk N" (Nth regex match found by scanning the file top-to-bottom) is assumed to be identical to the upstream extractor's notion of "hunk N" (the Nth real conflict as git materialized it), but nothing enforces that equivalence. If the repository file already contains, prior to any real conflict, a byte sequence that satisfies all three regexes (`^<{7}(?:\s|$)`, `^={7}$`, `^>{7}(?:\s|$)`) — e.g. a documentation/tutorial file about resolving git conflicts, ASCII art, a fixture/test file containing example conflict markers, or a string literal embedded in source used to detect/lint conflict markers — that block is indistinguishable to this function from a real git-inserted conflict. Such content is entirely attacker-controlled: it only needs to be committed to the repository (a normal PR/commit) ahead of time.

When a real conflict later occurs in that same file during a merge/rebase/cherry-pick that the user resolves via Copilot, the on-disk raw file (`ctx.rawContent`, passed straight into `reassembleResolvedFile` from `reassembleResolutions`) contains both the pre-existing "fake" block and git's real conflict markers. [4](#0-3) 

If the fake block appears earlier in the file than the real conflict, the scanner consumes `hunkResolutions[0]` (the model's resolution intended for the real conflict) and splices it into the fake/benign block, while the real conflict — now at index 1 — has no corresponding entry (`hunkIndex < hunkResolutions.length` becomes false for a single-hunk file), so the actual `<<<<<<<`/`=======`/`>>>>>>>` markers are left completely untouched and copied verbatim into `resultLines`.

### Impact Explanation
This is a silent-corruption-of-what-the-user-commits bug that matches the "decimal error" bug class in the seed report: a business-logic index/count assumption (git hunk N == regex-scan hunk N) is violated by attacker-supplied input, producing an output that diverges from the invariant the code assumes holds. Concretely:
- Real merge-conflict markers can be left in the file and subsequently committed/pushed by the user, who believes Copilot fully resolved the conflict (the dialog and write path treat `resolvedContent` as fully resolved, markers-free content).
- Benign, non-conflicted content elsewhere in the file gets silently overwritten with unrelated model-generated text, without the user's knowledge or explicit review of that specific hunk.
- Because raw conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) inside a committed source file are syntactically invalid in nearly all languages, this can break builds; more subtly, if the "look-alike" region were engineered as valid syntax that merely resembles markers loosely, wrong code could land silently in a commit that gets pushed.

### Likelihood Explanation
Requires no local/physical access, admin rights, or leaked credentials — only that the attacker's crafted file (containing conflict-marker-like text) be present in the repository the victim works in (a normal contribution scenario for OSS/team repos), and that the victim later use the Copilot "resolve with AI" feature on a real conflict touching that same file. The validation in `validateResolutionPaths` only compares hunk *counts* between the model's output and the upstream extractor's count — it does not verify that `reassembleResolvedFile`'s independent, naive line-scan produces the same count or ordering, so a mismatch in either direction is never detected before the file is written to disk.

### Recommendation
Do not re-derive conflict-block boundaries via an independent regex scan in `reassembleResolvedFile`. Instead, splice resolutions using the exact line ranges already computed by the upstream conflict parser that produced `IFileConflictContext.hunks` (the same source of truth used for prompting and count-validation), so the "hunk N" definitions used for validation and for splicing are guaranteed to be the same object, not two independently-inferred indices. If a positional/regex scan must be kept as a fallback, add a runtime check that the number of well-formed marker blocks found during reassembly equals `hunkResolutions.length`, and fail closed (skip the file / surface an error to the user) rather than silently misaligning content on mismatch.

### Proof of Concept
1. Attacker commits `docs/conflict-guide.md` (or any tracked file) to the repository containing literal text that satisfies all three marker regexes but is not a real conflict, e.g.:
   ```
   Example of a conflict block:
   <<<<<<< example
   some benign line
   =======
   another benign line
   >>>>>>> example
   ```
2. Later, the victim merges/rebases a branch that produces a real conflict earlier is not required — it only needs to land in the *same file* being resolved, anywhere before the real conflict when read top-to-bottom, or anywhere in the file, since `hunkIndex` increments globally per file, not per verified conflict.
3. The victim runs Copilot's "Resolve with AI" on the conflicted file; the model returns one `IHunkResolution` intended for the real conflict (`hunks: [{ resolvedContent: "<merged real content>" }]`), validated by `validateResolutionPaths` against the real hunk count (1), which passes.
4. `reassembleResolutions` calls `reassembleResolvedFile(ctx.rawContent, raw.hunks)` — the scanner meets the fake block first, treats it as hunk 0, splices in the real conflict's intended resolution there, then reaches the real `<<<<<<<...>>>>>>>` block as hunk 1, finds `hunkIndex (1) >= hunkResolutions.length (1)`, and copies the actual conflict markers through unchanged.
5. The resulting `resolvedContent` — containing raw `<<<<<<<`/`=======`/`>>>>>>>` markers plus a benign section silently overwritten — is written to disk and offered to the user as the "resolved" file, ready to be staged and committed. [2](#0-1)

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L628-636)
```typescript
    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
```
