### Title
Divergent conflict-marker parsing between `extractConflictHunks` and `reassembleResolvedFile` allows attacker-controlled file content to silently corrupt Copilot-resolved merges - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The external report's bug class is "two places count/validate the same logical array differently, so a length check that's supposed to gate correctness either always fails or silently accepts a mismatched state." In GitHub Desktop's Copilot merge-conflict-resolution feature, the same class of bug exists between the function that counts conflict hunks to build the model prompt (`extractConflictHunks`) and the function that later splices the model's per-hunk resolutions back into the file (`reassembleResolvedFile`). Both scan the same raw file content for `<<<<<<<`/`=======`/`>>>>>>>` markers, but with different state machines. A conflicted file whose "ours"/"theirs" content itself contains marker-like lines (fully attacker-controlled, since it comes from a cloned/fetched repository) can make the two scanners disagree on hunk boundaries while still agreeing on hunk *count*, defeating the only check that exists (`validateResolutionPaths`) and causing Copilot's resolved content to be spliced into the wrong location.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) walks a conflicted file line-by-line with a strict sequential state machine: from an `<<<<<<<` marker it collects "ours" lines until it sees `|||||||` or `=======`, then optionally "base" lines until `=======`, then "theirs" lines until `>>>>>>>` [1](#0-0) . This hunk list is what's sent to the model and is also used to compute `expectedHunkCounts` in `validateResolutionPaths` [2](#0-1) .

`reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) independently re-scans the *same* raw content it was originally given, using a looser lookahead: from an `<<<<<<<` line it scans forward, setting `hasSeparator = true` on any `=======`-looking line without stopping, and treats the *first* `>>>>>>>`-looking line as the closing marker [3](#0-2) . It then increments `hunkIndex` and splices in `hunkResolutions[hunkIndex]` for every marker block it finds, matching purely by order, not by content boundaries.

Because both scanners key off simple `^<{7}`/`^={7}`/`^>{7}` regexes with no escaping or nesting awareness, a file whose legitimate "ours" or "theirs" content happens to contain a line that itself starts with 7+ `<`, `=`, or `>` characters (e.g. a documentation file about git conflict markers, a diff/patch fixture, a minified separator comment, or any generated file using `>>>>>>>`-style banners) will be parsed differently by the two functions:
- `extractConflictHunks`'s sequential state machine may terminate a hunk early at the embedded fake marker, producing a different hunk count/boundary set than the "real" merge conflict has.
- `reassembleResolvedFile`'s more permissive lookahead may find a different set of well-formed blocks in the same text.

Since `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:509-520`) only checks that `resolution.hunks.length` matches the count `extractConflictHunks` produced, it provides no protection against the two functions disagreeing about *where* the hunk boundaries are, only about how many there nominally are. If the counts still match (which is plausible for a single embedded fake marker offsetting one boundary without changing the total count), the check passes, but `reassembleResolvedFile` will splice the model's resolutions into the wrong marker block — silently corrupting the file that the user then commits.

### Impact Explanation
This directly corrupts "what the user commits or pushes" without any error or user-visible warning — the Copilot conflict-resolution dialog would present a seemingly successful, well-formed resolution, but the reassembled file content would have Copilot's resolved text spliced at the wrong offset relative to the true conflict blocks (e.g., attaching one hunk's resolution to a different hunk's surrounding code, or leaving genuine conflict markers un-stripped while an unrelated code fragment gets replaced). Since the feature's entire purpose is unattended reassembly of trusted-looking merged code, this is a realistic supply-chain-style corruption vector: a malicious repository/branch can be crafted so that, when merged locally and auto-resolved via Copilot, the user unknowingly commits/pushes subtly wrong code.

### Likelihood Explanation
Exploitation requires only that the attacker control content of a file that ends up in a real conflict (e.g., via a malicious branch, PR, or fork the victim merges) and that this content contains a marker-like line unrelated to the real conflict boundaries — a low bar given many text/documentation/example files legitimately contain such sequences. No local access, elevated privileges, or unusual user action is required beyond the normal AI-assisted-merge workflow (which the feature explicitly automates). The main uncertainty is how consistently a *count-preserving* boundary shift can be engineered, since `validateResolutionPaths` will still catch mismatches where the total hunk count differs, so a reliable end-to-end PoC would need to be validated by actually running both parsers with a crafted fixture to confirm a count-preserving divergence.

### Recommendation
Make `reassembleResolvedFile` and `extractConflictHunks` share a single hunk-boundary-detection implementation (not just marker regexes) so both stages always agree on where hunks start/end, not merely how many there are. Additionally, `validateResolutionPaths`/reassembly should verify hunk *boundaries* (e.g., by having `extractConflictHunks` return line offsets and having reassembly consume exactly those offsets) rather than relying on order-matched counts alone.

### Proof of Concept
Conceptual fixture (would need to be validated by running both functions against it):
```
<<<<<<< HEAD
some ours code
=======
theirs code containing an unrelated embedded banner line:
>>>>>>> not-a-real-marker-but-matches-the-regex
more theirs code
=======
>>>>>>> theirsbranch
```
Feeding this to `extractConflictHunks` and to the inline scanner in `reassembleResolvedFile` and comparing the returned hunk boundaries/counts would confirm whether they diverge while the aggregate hunk count stays equal — the condition needed to bypass `validateResolutionPaths` and cause misplaced splicing. This PoC construction was reasoned from the source but not executed against the actual test harness (`app/test/unit/copilot-conflict-resolution-test.ts` / `copilot-conflict-context-test.ts`), so the exact fixture needed to keep counts equal while boundaries differ should be verified there before relying on it as a fully confirmed exploit.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L200-242)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-591)
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
```
