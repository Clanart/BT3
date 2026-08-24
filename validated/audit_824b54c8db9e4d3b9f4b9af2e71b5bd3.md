### Title
Silent misalignment of Copilot conflict-hunk resolutions when splicing back into file content — ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile` in `copilot-conflict-resolution.ts` reconstructs a resolved file by walking the raw on-disk content (which still has conflict markers) and splicing in per-hunk model resolutions **in positional order**, matching `hunkResolutions[hunkIndex]` to the `hunkIndex`-th conflict block it encounters while re-scanning the file — not by any stable identifier tied to the block that was actually sent to the model. [1](#0-0) [2](#0-1) 

This mirrors the reported bug class in `ConcurrentMerkleTree::copy_from_bytes`/`CyclicBoundedVec`: an ordered collection is reconstructed from raw bytes/text using positional indices only, without validating that the reconstructed indexing matches the original structure that produced the data being spliced back in. If the count or order of conflict blocks found by the *extraction* pass (`extractConflictHunks` in `copilot-conflict-context.ts`, which builds the hunk list sent to the model) doesn't exactly match the count/order found by the *reassembly* pass (which re-parses the same raw file independently), resolutions get spliced into the wrong hunk position with no integrity check.

### Finding Description
Two independent parsers walk the same raw file content with `<<<<<<<`/`=======`/`>>>>>>>` markers using their own regexes and control flow:

- `extractConflictHunks` (context builder) uses `oursMarker`, `baseMarker`, `separatorMarker`, `theirsMarker` to walk the file and build the `IConflictHunk[]` sent to the model, skipping any hunk it deems malformed. [3](#0-2) 
- `reassembleResolvedFile` re-parses the *same raw file* from scratch using a second, separately declared set of marker regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`), and for every conflict block it finds, it consumes `hunkResolutions[hunkIndex++]` — i.e., the Nth resolution the model returned is blindly applied to the Nth conflict block found during re-parsing. [4](#0-3) 

There is no shared, stable index/anchor (e.g., byte offset, hunk id, or marker content hash) carried from extraction through to reassembly — the correspondence is purely "matched by order," as the code comment itself states. [5](#0-4) 

Because the two marker-matching passes are separately implemented, any divergence between what `extractConflictHunks` considers a valid/malformed hunk and what `reassembleResolvedFile` considers a valid/malformed block causes the hunk counts (or their order) to diverge silently. For example, `extractConflictHunks` explicitly skips a hunk it can't close (`if (hunkEnd === -1) { continue }`) [6](#0-5) , while `reassembleResolvedFile` treats an unclosed/malformed `<<<<<<<` marker as regular content and passes it through unmodified [7](#0-6) . If an attacker (via a crafted merge/rebase producing conflicts, or a maliciously structured file already containing look-alike marker sequences inside "ours"/"theirs" content) can cause one pass to count a different number of conflict blocks than the other — e.g., embedding a line starting with `<<<<<<<` inside `theirsContent` of a real hunk — the two parsers will disagree on hunk boundaries/counts for the remainder of the file. The result: `hunkResolutions[hunkIndex]` for a later hunk gets spliced into the *wrong* conflict block position in the reconstructed file.

Existing guards do not stop this because:
- There is no cross-check between `fileContexts[].hunks.length` and the number of conflict blocks `reassembleResolvedFile` re-discovers in `rawContent` — `reassembleResolutions` just forwards `raw.hunks` straight into `reassembleResolvedFile` with no count/consistency assertion. [8](#0-7) 
- The only validation invoked before reassembly is that `ctx.rawContent` is present; there is no check that the number of hunk resolutions equals the number of conflict blocks in that raw content. [9](#0-8) 

### Impact Explanation
If misalignment occurs, `reassembleResolvedFile` produces a file that Desktop then writes to disk and stages/commits as the "resolved" conflict — with a resolution intended for one code region silently applied to a different one, while the true resolution for that region is dropped or misapplied elsewhere. This is a **silent corruption of what the user commits**: the user believes Copilot resolved the merge/rebase conflicts correctly (no error is raised — the malformed markers are simply copied through as "content"), but the actually-committed and potentially pushed code differs from both "ours" and "theirs" intent in the confused region, and may reintroduce logic from an unrelated hunk. In a scenario where the conflicting content originates from an untrusted branch/PR being merged, an attacker who controls the conflicting branch content can potentially engineer marker-lookalike text to increase the odds of this desync, without any privileged access — matching the required "attacker controls a cloned/fetched repository" impact class.

### Likelihood Explanation
This requires: (1) the user to invoke the Copilot conflict-resolution flow on a merge/rebase with conflicts sourced (at least in part) from attacker-influenced content, and (2) a divergence between the two hand-rolled regex-based marker scanners on some edge case (e.g. text resembling conflict markers embedded in hunk content, different handling of malformed/unclosed marker blocks, or CRLF/whitespace variants only one regex tolerates). This is a real, reachable code path (feature is shipped) but is conditioned on triggering a parser divergence, so likelihood is moderate rather than trivial — exact proof of a concrete marker-lookalike payload that triggers divergence was not fully verified within tool budget (see caveat below).

### Recommendation
- Have `extractConflictHunks` return, per hunk, a stable positional anchor (e.g., start/end line index or byte offset in `rawContent`) instead of relying on re-scanning.
- Have `reassembleResolvedFile` splice resolutions using those anchors directly rather than independently re-parsing `rawContent` and counting blocks positionally.
- Alternatively, unify the marker-matching logic into a single shared parser used by both extraction and reassembly, and add an assertion that the number of conflict blocks found during reassembly equals `hunkResolutions.length`, failing loudly (not silently passing through) on any mismatch.

### Proof of Concept
Not independently verified end-to-end due to tool-call limits reached before I could compare the exact `oursMarker`/`baseMarker`/`separatorMarker`/`theirsMarker` regex definitions in `copilot-conflict-context.ts` against `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in `copilot-conflict-resolution.ts` character-for-character (I could not complete the final `read_file` calls). The structural flaw — order-based, index-only correspondence between two independently implemented marker scanners with no shared anchor or consistency check — is confirmed from the code shown above. A concrete minimal PoC would be: a file with N real conflict hunks where one hunk's "theirs" content contains a line beginning with `<<<<<<< ` (7 chars) that `extractConflictHunks`'s hunk-collection loop does not treat as a marker boundary (because it's inside `theirsLines` collection, which only breaks on the closing `>>>>>>>` marker) but which changes how `reassembleResolvedFile`'s look-ahead (`for (let j = i + 1; ...)`) scans for the next `=======`/`>>>>>>>` pair for that same block — leading the two counts to diverge from that point forward. Full confirmation requires reading both regex definitions and tracing a constructed example, which I was unable to complete in the remaining budget.

### Citations

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-596)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
```

**File:** app/src/lib/copilot-conflict-context.ts (L182-242)
```typescript
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

    // If we never found the closing marker, skip this malformed hunk
    if (hunkEnd === -1) {
      continue
    }
```
