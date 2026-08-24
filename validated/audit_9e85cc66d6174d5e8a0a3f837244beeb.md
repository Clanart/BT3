## Title
Malformed/malicious conflict-marker blocks silently corrupt the resolved file written to disk when using Copilot AI conflict resolution - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The external report's broken invariant is: code assumes a strict, well-formed shape from an external/untrusted producer, and when that assumption is violated, the mismatch is either force-parsed or silently absorbed instead of safely rejected, corrupting the operation's outcome. The closest verifiable analog in this codebase is `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts`, which reassembles the AI-resolved file by scanning the **original on-disk file** (attacker-influenced, since it comes from a cloned/fetched/merged repository) for conflict-marker blocks and splicing in the model's hunk resolutions positionally.

### Finding Description
`reassembleResolvedFile` walks the raw file content line by line, looking for `<<<<<<<` / `=======` / `>>>>>>>` conflict markers using three narrow regexes: [1](#0-0) 

It matches hunk resolutions to marker blocks strictly **by order of appearance**, not by any positional or content anchor: [2](#0-1) 

The function's own comment acknowledges the ambiguity it accepts as "safe": a malformed/stray marker block (e.g. one `<<<<<<<` without a matching `=======`/`>>>>>>>`) is passed through untouched, but any block that merely resembles a genuine conflict block by having a well-formed `<<<<<<< / ======= / >>>>>>>` triple is treated as a real hunk, regardless of its actual origin. Because a **remote/fetched branch or a crafted file already committed by a collaborator can contain literal conflict-marker-shaped text** (e.g. inside a code comment, a string literal, a markdown code sample about Git, or a deliberately crafted decoy block), an attacker who controls content merged into the repository can inject extra `<<<<<<<...=======...>>>>>>>` blocks. This desynchronizes the ordinal mapping between the AI's `hunks[]` array (produced against `getConflictContext`'s hunk enumeration) and the actual marker blocks found by `reassembleResolvedFile`, causing hunk N's resolved content to be spliced into the wrong block — silently discarding real conflict resolution content or splicing another hunk's content (potentially attacker-authored) into a block the user believes was resolved correctly.

Compounding this, `validateResolutionPaths` only checks that hunk **counts** match per file — not their positions/content — so a count-preserving but positionally-shifted attack is invisible to validation: [3](#0-2) 

This is structurally the same class of bug as the ERC20 report: a strict format/ordering assumption is imposed on data influenced by an external, less-trusted party (there: the token contract's return ABI; here: the byte content of a merged/fetched file), and no independent verification confirms the assumption held before the result is trusted and applied.

### Impact Explanation
The corrupted output of `reassembleResolvedFile` becomes the file content that is written to the working directory and ultimately staged/committed/pushed by the user via the conflict-resolution flow, i.e. exactly the "silent corruption of what the user commits or pushes" impact category. A malicious collaborator or a maliciously crafted upstream/fork branch that the victim merges/rebases against could smuggle decoy marker-shaped text into an otherwise innocuous file (e.g. a `README.md` code sample or a string constant) to desynchronize hunk splicing, causing legitimate resolved code to be dropped or mismatched content to land in the final commit without any error or warning to the user.

### Likelihood Explanation
Exploitability requires: (1) the victim uses the Copilot-assisted conflict resolution feature, (2) a conflict occurs against content the attacker controls (a branch, PR, or file the attacker previously contributed), and (3) that content contains a well-formed marker-shaped block that is not an actual conflict. This is a non-trivial precondition but well within the described "attacker controls a cloned/fetched repository" threat model, and the flaw is a pure logic/data-mapping gap in the local codebase (no local access, admin rights, or social engineering required beyond a normal merge/rebase against attacker-influenced content).

### Recommendation
Do not rely on ordinal position alone to match model hunks to file marker blocks. Anchor each hunk resolution to the original marker block's content/hash (e.g., verify the "ours"/"theirs" content extracted from the raw file for that block matches what was sent to the model in `IFileConflictContext.hunks[i]` before splicing), and reject/flag the reassembly if any block's captured content diverges from what was presented to the model. Additionally, extend `validateResolutionPaths` to validate hunk *content* correspondence, not just hunk *count*.

### Proof of Concept
Not independently executed; the flaw is demonstrated structurally from the code:
1. Suppose a file has one genuine conflict block and, elsewhere in the file (e.g., inside a code comment documenting git conflict markers), a second syntactically well-formed `<<<<<<< foo\n=======\n>>>>>>> bar` sequence that is not an actual VCS conflict (git only emits real conflict markers in the actual conflicting region, but nothing prevents a committed file from itself containing marker-shaped text).
2. `getConflictContext`/hunk gathering (not fully explored due to file truncation, see note below) enumerates only the real conflict for the prompt sent to the model, so the model returns exactly one `hunks[]` entry.
3. `reassembleResolvedFile`, however, scans the raw file top-to-bottom and treats **both** marker-shaped blocks as real conflict blocks (its only well-formedness check is the presence of the three marker lines, at lines 560–579).
4. If the decoy block appears before the real conflict in the file, the model's single resolution is spliced into the decoy block instead of the real one, leaving the real conflict either unresolved-looking or filled with unrelated text, silently corrupting the file that gets committed.

Note: I was unable to fully inspect the hunk-context-gathering code (`copilot-conflict-context.ts`) within the available iterations to confirm exactly how conflict blocks are enumerated for the prompt versus how `reassembleResolvedFile` re-scans the file, so the precise conditions under which counts would still line up (masking the desync) could not be fully verified from the index. A Devin session with full file access would be needed to construct and run a concrete end-to-end PoC.

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L524-526)
```typescript
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
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
