### Title
Decoy conflict-marker text in attacker-controlled merge content desynchronizes Copilot hunk splicing, leaking unresolved raw content into the committed file - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile` locates the boundaries of each git conflict block using a line-by-line regex lookahead for `=======` and `>>>>>>>` markers, rather than parsing the real conflict block structurally. Because the "ours"/"theirs" content that makes up the conflict body originates directly from repository content the attacker controls (a branch/PR that the user merges or fetches), the attacker can embed a line that matches the closing-marker regex (`^>{7}(?:\s|$)`) inside their own content. This spoofed marker is indistinguishable from the real one to the lookahead loop, causing the parser to believe the conflict block ends earlier than it actually does — exactly mirroring the source bug class: a validation/classification check that is trivially satisfiable by adversarial input, so the "guard" silently stops protecting the invariant it was meant to enforce.

### Finding Description
`reassembleResolvedFile` scans the raw on-disk file (which still contains real `<<<<<<<` / `=======` / `>>>>>>>` markers from a merge/rebase) to find each conflict block so it can splice in the model's per-hunk resolution: [1](#0-0) 

The boundary detection is purely regex-based: [2](#0-1) [3](#0-2) 

The loop searches forward for a line matching `reassemblySeparatorMarker` (`^={7}$`) to set `hasSeparator`, and then for the *first* line matching `reassemblyTheirsMarker` (`^>{7}(?:\s|$)`) to set `closingIndex`, immediately breaking. It does not verify this is the corresponding `>>>>>>>` for the currently-open `<<<<<<<`, nor does it check that no earlier `<<<<<<<`-like line intervenes, nor that the git-produced label suffix (branch name/ref) after `>>>>>>>` matches what git actually emitted. If the "theirs" (attacker-supplied) side of the conflict contains a line that happens to match `^>{7}(?:\s|$)` — e.g. a code comment divider, an embedded example of a merge conflict in documentation/test fixtures, ASCII art, or a line crafted specifically for this purpose — the loop treats that decoy as the real closing marker and sets `i = closingIndex + 1` there.

Consequences of this desync:
1. Everything between the decoy `>>>>>>>` and the real one (which still contains the tail of "theirs" content plus the actual `>>>>>>> <ref>` line) is now treated as ordinary, non-conflicted file content in the `else` branch and copied through **verbatim** into `resultLines` — including the literal git marker text `>>>>>>> <ref>` that git itself wrote.
2. `hunkResolutions[hunkIndex]` — the model's carefully vetted, marker-free resolution for that conflict (validated at [4](#0-3)  to *not* contain conflict markers) — is spliced in place of only the truncated (decoy-bounded) region instead of the full conflict block.
3. All subsequent hunk indices shift by one relative to their intended conflict blocks, because `hunkIndex` is incremented once per detected block regardless of whether the detected block was the genuine one. Every later hunk in the file gets matched to the wrong location — the same "matched by order, not by identity" design noted in the function's own doc comment: [5](#0-4) .

None of the existing guards catch this:
- `validateResolutionPaths` only checks that the *count* of hunks per file matches the number of conflicts the app itself detected up front [6](#0-5) ; it has no way to know the reassembly step mis-located a boundary, since the count is unaffected.
- The per-hunk "no leftover conflict markers" check only inspects the *model's* `resolvedContent` [4](#0-3) , not the final reassembled file, so a real `>>>>>>> <ref>` line that leaks through via the desync is never inspected or rejected.
- `reassembleResolvedFile`'s own "malformed marker" fallback (skip the block, copy verbatim) exists only for the case of *no* separator/closer found at all — it does nothing to protect against a decoy that satisfies the pattern early.

### Impact Explanation
This is a silent corruption of what the user commits, triggered purely by attacker-controlled repository content (the incoming/"theirs" branch of a merge, rebase, or cherry-pick) — no local access, credentials, or social engineering required beyond the normal, expected act of merging a branch that contains a conflict. The result:
- Literal git conflict marker text (e.g. `>>>>>>> feature-branch`) can be written into the final committed file, producing syntactically broken code or unnoticed markers that ship to production.
- Every conflict hunk after the desynchronization point receives content intended for a *different* hunk, meaning the user's review of the AI-generated resolution (and any diff review in the resulting PR) reflects resolved content that is silently misapplied relative to the actual code regions, defeating the purpose of the "MINIMAL changes" / per-hunk review guarantee the feature advertises.
- Because the corruption happens after the model's per-hunk safety validation (marker check, hunk-count check), the app has no signal that anything went wrong and will present a clean, "successfully resolved" UI to the user while writing corrupted content to disk and, subsequently, to the commit that gets pushed.

### Likelihood Explanation
Exploitation requires only that the attacker control one side of a conflicting merge (a public/forked branch, a PR the user pulls, or content on a remote the user fetches) and place an innocuous-looking line matching `^>{7}(?:\s|$)` inside the conflicting hunk of a file that will legitimately conflict against the user's local changes. This is a low-effort, purely textual precondition (e.g., a comment banner using `>>>>>>>` as a divider, a documentation snippet demonstrating git conflict markers, or a deliberately placed decoy) with no need for special tooling or timing, making it a realistic, low-cost attack for any contributor whose branch the victim merges.

### Recommendation
Replace the ad-hoc regex lookahead with structural parsing that:
- Tracks nested/overlapping marker state explicitly and only accepts a `>>>>>>>` as the closing marker for the currently-open `<<<<<<<` if no other `<<<<<<<`/`=======` belonging to a *different* block is misattributed, and ideally validates the marker against the exact ref/label text git wrote for that block (obtained when the app first parsed the conflicted file to build `IFileConflictContext`).
- After reassembly, re-scans the final file to assert no conflict-marker patterns (`<{7}`, `={7}`, `>{7}`) remain outside of legitimately preserved code, failing closed (falling back to manual resolution) rather than silently emitting corrupted content.
- Cross-checks hunk boundaries detected during reassembly against the boundaries recorded when the conflict context was originally built (same source of truth used to construct the prompt), rather than re-deriving them independently via regex a second time.

### Proof of Concept
1. Attacker pushes a branch `feature` whose file `banner.ts` contains, inside the section that will become the "theirs" side of a conflict, a decorative comment line consisting of exactly `>>>>>>> notes` (7 `>` chars followed by a space, matching `reassemblyTheirsMarker`), followed later by more attacker content and only then the real git-generated `>>>>>>> feature` marker.
2. Victim merges `main` into `feature` (or vice versa) locally, producing a genuine conflict in `banner.ts` with the structure:
   ```
   <<<<<<< HEAD
   ...ours...
   =======
   ...decoy line: ">>>>>>> notes"...
   ...more theirs content...
   >>>>>>> feature
   ```
3. Victim invokes Copilot conflict resolution. Copilot returns a valid, marker-free `resolvedContent` for this single hunk, passing all of `parseCopilotConflictResolution`'s and `validateResolutionPaths`'s checks.
4. In `reassembleResolvedFile`, the lookahead loop hits the decoy `>>>>>>> notes` line first, sets `closingIndex` there, and splices the model's resolution into the truncated region. The remaining real "theirs" content plus the literal `>>>>>>> feature` marker are copied verbatim into the output file.
5. The file written to disk (and subsequently committed/pushed) now contains the model's resolution followed by leftover raw conflict content and a literal `>>>>>>> feature` marker, even though the dialog reported the file as fully resolved — demonstrating silent corruption of the user's commit.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-448)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L533-536)
```typescript
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
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
