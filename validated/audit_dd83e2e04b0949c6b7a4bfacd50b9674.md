Based on my investigation, I found a structurally similar class of bug in Desktop's Copilot merge-conflict resolution pipeline: a two-step process where a per-hunk "check/response" is computed against one parsing of the data, but the final "apply" step re-parses the same data independently and blindly trusts index alignment — exactly the same "check the partial step, not the actual final outcome" pattern as the Launchpad slippage bug.

### Title
Copilot conflict-resolution reassembly can silently drop conflicted content when hunk count diverges between extraction and reassembly - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
`buildConflictContext`/`extractConflictHunks` (in `app/src/lib/copilot-conflict-context.ts`) parses a conflicted file into an ordered list of hunks that gets sent to the Copilot model. `reassembleResolvedFile` (in `app/src/lib/copilot-conflict-resolution.ts`) later re-parses the *same raw file content* with an independently duplicated marker-detection implementation, and splices the model's per-hunk resolutions back in **purely by array index**, not by any correlation to the actual marker text found in the file.

<cite repo="Annirich/desktop--021" path="app/src/lib/copilot-conflict-resolution.ts" start="528="/> [1](#0-0) 

### Finding Description
Two independent parsers exist for the same conflict-marker syntax:

- `extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` (lines 179-279), which decides how many hunks exist and what content is sent to the model, and explicitly drops a trailing malformed hunk via `continue` when no closing marker is found before EOF. [2](#0-1) 

- `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` (lines 549-599), which re-scans the raw file independently with its own copies of the marker regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) to decide where to splice resolved content back in. [3](#0-2) 

The splice logic matches resolutions to markers "by order, not by line number" (as the code comment states), and — critically — if the reassembly scanner finds **more** real conflict blocks than there are entries in `hunkResolutions`, the excess block is simply skipped with `hunkIndex < hunkResolutions.length` evaluating false, meaning nothing is pushed for that block: the entire conflicted region (both `ours` and `theirs` content) disappears from the final file with no error, no marker, and no fallback. [4](#0-3) 

Because the two parsers are separate reimplementations of the same conflict-marker grammar (duplicated constants for what should be one shared source of truth), any repository content that causes them to disagree on hunk boundaries — e.g. a file that legitimately contains marker-like text (`<<<<<<<`, `=======`, `>>>>>>>`) as literal content in comments, docs, or string literals adjacent to a real conflict — can make `extractConflictHunks` report fewer hunks (and thus a shorter model response) than `reassembleResolvedFile` finds real conflict blocks for. The mismatch is entirely attacker-controlled: an attacker only needs to craft the content of a file in a branch that a victim later merges/rebases/cherry-picks against and resolves "with Copilot."

The final write path (`_applyCopilotConflictResolutions` in `app/src/lib/stores/app-store.ts`, lines 7169-7268) has no validation that the resolved content still contains an equal or greater number of resolved segments than conflict blocks existed — it simply writes `resolution.resolvedContent` to disk and stages it. [5](#0-4) 

### Impact Explanation
This is analogous to the audited bug: a slippage/validity check is enforced on a partial state (hunks reported at extraction time), but the final committed artifact is produced from a different, larger state (the full re-scanned file), and no invariant re-verifies that the two are consistent before the result is accepted and persisted. The practical effect is silent corruption of what the user commits — an entire conflict resolution (both sides' code) can vanish from the merged file with no error surfaced, and the change is then `git add`'ed and becomes part of the merge/rebase commit.

### Likelihood Explanation
Medium-to-low confidence without live reproduction. The vulnerability requires an attacker to control the content of a branch/file the victim merges and then choose to run Copilot conflict resolution on it — this fits the allowed "attacker controls a cloned/fetched repository" primitive and needs no unusual user action beyond the normal "resolve with Copilot" flow. However, I was not able to fully verify (due to remaining iteration budget) whether `extractConflictHunks`'s and `reassembleResolvedFile`'s marker-detection logic can be forced to diverge with a concrete byte-for-byte example, since both scanners implement very similar regex-based rules; constructing a definitive divergent input needs runtime testing that I could not perform.

### Recommendation
Replace the two independent marker parsers with a single shared implementation, and have `reassembleResolvedFile` assert that the number of conflict blocks it finds equals `hunkResolutions.length` exactly, throwing a `CopilotValidationError` (already used elsewhere in this file) rather than silently dropping content on mismatch.

### Proof of Concept
Not independently reproduced; this report is derived from static code review of `app/src/lib/copilot-conflict-context.ts` and `app/src/lib/copilot-conflict-resolution.ts`. A concrete PoC would require crafting a two-branch merge where the conflicted file contains conflict-marker-like literal text positioned so that `extractConflictHunks` and `reassembleResolvedFile`'s independent scans disagree on hunk count, then verifying via `reassembleResolvedFile` unit tests (`app/test/unit/copilot-conflict-resolution-test.ts`) that a conflict block is dropped when `hunkResolutions.length` is less than the actual number of markers in `rawContent`.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L534-538)
```typescript
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L581-591)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L238-242)
```typescript

    // If we never found the closing marker, skip this malformed hunk
    if (hunkEnd === -1) {
      continue
    }
```

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
