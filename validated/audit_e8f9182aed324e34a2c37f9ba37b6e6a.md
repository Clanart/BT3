## Title
Copilot conflict-resolution reassembly can silently drop AI resolutions and leave raw conflict markers in a "resolved" file - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The Mochi report's broken invariant is: *when an input exceeds/violates a bound, the code should revert, but instead it silently proceeds with a different, unintended result* (borrowing to `maxMinted` instead of reverting with `>cf`). Desktop's AI-assisted merge-conflict resolution feature has the same class of bug: the module that *counts* conflict hunks (`extractConflictHunks`) and the module that *splices* the model's resolutions back into the file (`reassembleResolvedFile`) use two different, inconsistent algorithms for identifying where a conflict block ends. When a conflicted file (which can come from an attacker-controlled branch/PR/fetched remote) contains diff3 base content with a line that happens to match the `>>>>>>>` marker pattern, the two parsers disagree about hunk boundaries even though they agree on hunk *count*. The count-only validation (`validateResolutionPaths`) passes, but the actual splice silently no-ops for that hunk, and the file is written back to disk with the model's fix ignored and raw `<<<<<<<`/`=======`/`>>>>>>>` markers still present, even though the app reports the conflict resolved.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) parses conflict markers and, for diff3 (three-way) conflicts, collects "base" content between `|||||||` and `=======` by scanning forward and breaking **only** on `separatorMarker` (`=======`): [1](#0-0) 

It never checks for a stray `>>>>>>>`-looking line while collecting base content, so a base section that happens to contain a line matching the theirs-marker pattern (7 `>` chars) is still correctly consumed as part of the hunk, and the hunk is counted normally.

`reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`), which is used later to splice the model's per-hunk resolutions back into the original on-disk content, uses a different lookahead that ignores the base marker entirely and breaks on the **first** line matching `theirsMarker`, regardless of whether the real `=======` separator has been seen yet: [2](#0-1) 

If a base section contains a line that matches the `>>>>>>>` pattern *before* the real `=======` line, this lookahead sets `closingIndex` to that impostor line and finds `hasSeparator === false` at that point, so the block is classified as **malformed** and copied through verbatim instead of being spliced: [3](#0-2) 
Crucially, in the malformed branch `hunkIndex` is **not incremented**, so the model's resolution for that hunk is silently dropped and the raw conflict markers remain in the reassembled file.

The only safety check between context-gathering and reassembly is a hunk-**count** comparison in `validateResolutionPaths`: [4](#0-3) 
This only verifies `resolution.hunks.length === expectedCount`; it does not verify that the two parsers agree on *where* the hunk boundaries are. Because `extractConflictHunks` still counted the crafted hunk as one hunk, and the model dutifully returns exactly one hunk resolution, the count check passes even though `reassembleResolvedFile` will never actually consume the resolution for that block.

### Impact Explanation
The reassembled "resolved" content is written back to the file and reported to the user as successfully AI-resolved (part of the Copilot conflict-resolution flow driven from `app-store.ts`/`copilot-store.ts`). If the user proceeds to commit, the committed file still contains literal `<<<<<<<`, `|||||||`, `=======`, `>>>>>>>` conflict-marker text instead of the intended merged content — silent corruption of what the user commits, exactly the impact category called out as valid (silent corruption of what the user commits/pushes). This can break builds, ship broken/unintended code, or hide unresolved logic differences between branches while the UI asserts the conflict was handled.

### Likelihood Explanation
The trigger is fully within the attacker's control if the attacker controls the "theirs" branch/PR/fetched content (or crafts a file whose diff3 base region legitimately contains a documentation/example string matching the `>>>>>>>` pattern, e.g. sample conflict-marker text, embedded patch/diff snippets, or leftover unresolved markers from a prior merge). No local access, privileges, or social engineering beyond a normal "resolve conflicts with Copilot" click by the victim is required — this matches the unprivileged, attacker-controlled-repository threat model described in the task.

### Recommendation
Unify the boundary-detection logic between `extractConflictHunks` and `reassembleResolvedFile` (ideally share a single parser/AST for conflict blocks) so both functions agree on exactly which lines constitute a conflict block, not just how many. Additionally, `reassembleResolvedFile` should assert/throw (rather than silently fall back to "copy through") whenever the number of successfully-spliced hunks does not match `hunkResolutions.length`, so a parser disagreement fails loudly (analogous to reverting with `>cf`) instead of silently emitting a file that still contains unresolved conflict markers.

### Proof of Concept
1. Attacker pushes/prepares a branch such that merging it produces a diff3 conflict in a file where the base (`|||||||...=======`) section contains a line matching `^>{7}(?:\s|$)`, e.g.:
```
<<<<<<< HEAD
our change
||||||| base
some text
>>>>>>> impostor-does-not-belong-here
more base text
=======
their change
>>>>>>> feature
```
2. Victim triggers "Resolve with Copilot" on this merge conflict. `extractConflictHunks` correctly parses this as **1** hunk and sends it to the model; the model returns exactly 1 hunk resolution.
3. `validateResolutionPaths` sees `resolution.hunks.length (1) === expectedCount (1)` and passes.
4. `reassembleResolvedFile` scans forward from `<<<<<<<`, hits the impostor `>>>>>>>` line first, finds `hasSeparator === false` at that point, treats the whole block as malformed, and copies it through **verbatim**, never incrementing `hunkIndex` and never using the model's resolution.
5. The file written back to disk (and subsequently committed by the user) still contains the raw `<<<<<<<`/`|||||||`/`=======`/`>>>>>>>` markers, despite the dialog reporting a successful resolution. [5](#0-4) 
This confirms the intended-but-under-tested "malformed marker → copy through" fallback path exists and behaves exactly as analyzed; the gap is that this fallback can be reached via a boundary-detection disagreement with `extractConflictHunks`, which is not covered by existing tests.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L216-226)
```typescript
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
```

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L560-579)
```typescript
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
```

**File:** app/test/unit/copilot-conflict-resolution-test.ts (L591-606)
```typescript
  it('treats unclosed markers (missing >>>>>>>) as regular content', () => {
    const raw = [
      'line 1',
      '<<<<<<< HEAD',
      'some content',
      '=======',
      'other content',
      'line 2',
    ].join('\n')

    // No >>>>>>> closing → not a valid conflict block, copy through
    const result = reassembleResolvedFile(raw, [])

    assert.equal(result, raw)
  })
})
```
