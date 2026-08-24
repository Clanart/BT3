## Title
Attacker-crafted conflict-marker-like text in a merged file causes Copilot AI conflict resolution to mis-detect hunk boundaries and silently leave leftover git conflict markers / drop real changes in the committed file - ([File: app/src/lib/copilot-conflict-context.ts](app/src/lib/copilot-conflict-context.ts), [File: app/src/lib/copilot-conflict-resolution.ts](app/src/lib/copilot-conflict-resolution.ts))

## Summary
GitHub Desktop's "Resolve merge conflicts with Copilot" feature (introduced in 3.6.0) extracts conflict hunks from a conflicted file with `extractConflictHunks`, sends each hunk to the Copilot model, and then splices the model's per-hunk resolutions back into the original file with `reassembleResolvedFile`. Both functions locate conflict-block boundaries with simple line-by-line regex scans that are not aware of nested/embedded text that merely *looks like* Git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`). Since both scans always resolve to the *first* matching separator/closing-marker line, a file that legitimately contains such marker-like text (test fixtures, documentation, ASCII banners, sample code) positioned before or around a real conflict causes the hunk boundary detected during extraction/prompting to diverge from the boundary consumed during splicing. The net effect is that literal, unresolved `=======`/`>>>>>>>` conflict-marker lines and real code from one side of the merge can be left in — or silently dropped from — the file that Copilot claims to have "resolved," without any validation catching it.

## Finding Description
`extractConflictHunks` (app/src/lib/copilot-conflict-context.ts:179-279) parses a conflicted file into `IConflictHunk`s by scanning forward from each `<{7}` marker, using the first `|{7}`/`={7}` line to close the "ours" side and the first `>{7}` line found afterward to close the hunk: [1](#0-0) 

`reassembleResolvedFile` (app/src/lib/copilot-conflict-resolution.ts:549-599) independently re-scans the same raw file, but with a subtly different algorithm: from a `<{7}` line, it scans forward for *any* `={7}` line (setting `hasSeparator`) and stops at the *first* `>{7}` line it meets: [2](#0-1) 

Both routines terminate a "conflict block" at the first `>>>>>>>`-looking line they encounter, with no concept of nesting. If a file contains marker-like text before the real closing marker of an actual git conflict — for example a nested/embedded `<<<<<<< / ======= / >>>>>>>` snippet inside one side's content (documentation about Git conflicts, a test fixture such as this repo's own `app/test/unit/copilot-conflict-context-test.ts`/`copilot-conflict-resolution-test.ts`, or any sample code containing that text) — both functions stop at the *inner* fake `>>>>>>>` line instead of the real outer one.

Consequences:
- `extractConflictHunks` builds an incorrect, truncated "ours"/"theirs" pair for the prompt and never surfaces the real trailing separator/theirs/closing-marker text as part of any hunk (it's silently treated as ordinary file content, since the outer scan loop only starts a new hunk on `oursMarker` — app/src/lib/copilot-conflict-context.ts:187-191). The model is therefore never shown, and never resolves, the true conflict content.
- `validateResolutionPaths` (app/src/lib/copilot-conflict-resolution.ts:473-521) only checks that the *count* of returned hunks matches the (already wrong) count computed by `extractConflictHunks` — it does not compare content or re-derive boundaries, so this mismatch passes validation silently: [3](#0-2) 
- `reassembleResolvedFile` then splices the model's resolution using the same wrong (inner) closing index, so the genuine `=======`, remaining "theirs" content, and genuine `>>>>>>>` line — which were never sent to or handled by the model — are pushed through verbatim into the final file via the `else` branch that copies non-marker-matching lines through unchanged (app/src/lib/copilot-conflict-resolution.ts:592-595).

The result: the file Desktop writes to disk and offers for commit after "successful" Copilot resolution can contain leftover raw conflict-marker syntax and/or be missing real changes from one side of the merge, while the UI reports the conflict as fully resolved with a plausible-sounding reasoning string generated from the truncated (wrong) hunk content.

## Impact Explanation
This is a silent corruption of what the user commits, directly matching the accepted impact category. An attacker who contributes to a shared branch/fork (a completely normal collaborative git action — no local access, no credentials, no social engineering beyond a routine merge) can craft file content containing conflict-marker-like text so that, when a legitimate merge/rebase against that file also produces a real conflict, Desktop's Copilot conflict-resolution feature mis-parses the hunk boundary and leaves invalid/broken conflict markers or drops code changes in the resulting commit — without any error being surfaced to the user, since JSON/hunk-count validation is content-blind and the model's own reasoning text is generated from the (already wrong) truncated hunk it was shown.

## Likelihood Explanation
Files containing marker-like text are not exotic: code samples, tutorials, git-tooling test fixtures (this very repository's own test files contain many literal `<<<<<<<`/`=======`/`>>>>>>>` strings), or ASCII banners are common in real repositories. An attacker only needs to ensure such content sits in a file that also ends up with a genuine conflict on merge — an easily engineered, low-effort setup that requires no elevated privileges, matching a "moderate" likelihood for a feature (Copilot AI conflict resolution) that is opt-in but expected to run unattended over arbitrary repository content.

## Recommendation
Make hunk-boundary detection nesting-aware and consistent between `extractConflictHunks` and `reassembleResolvedFile`:
- Track marker nesting depth (increment on an inner `<<<<<<<`, and only treat a `>>>>>>>` as a genuine close when depth returns to the outermost level), or reuse the exact same boundary-detection routine in both places instead of two independently maintained scanners.
- After reassembly, verify the output file no longer contains any conflict-marker-pattern lines before treating the resolution as complete; if it does, fail closed (treat the file as unresolved) rather than silently emitting a corrupted commit.

## Proof of Concept
1. Create a file `demo.md` on `main` containing a sample of a Git conflict for documentation purposes, e.g.:
```
Example of a conflict:
<<<<<<< nested
sample ours
=======
sample theirs
>>>>>>> nested
```
2. On branch `feature`, modify a line above and below that block (a normal, unrelated edit) so a real merge conflict is produced around it — i.e., the real git-generated markers wrap the whole passage:
```
<<<<<<< HEAD
Example of a real conflict:
<<<<<<< nested
sample ours
=======
sample theirs
>>>>>>> nested
=======
Example of a real conflict (feature edit):
<<<<<<< nested
sample ours
=======
sample theirs
>>>>>>> nested
>>>>>>> feature
```
3. Merge `feature` into `main` in Desktop, triggering a real conflict in `demo.md`, then invoke "Resolve with Copilot."
4. Both `extractConflictHunks` and `reassembleResolvedFile` will terminate the block at the inner `>>>>>>> nested` line rather than the real `>>>>>>> feature` line (per the code paths cited above).
5. Inspect the resulting file after Copilot reports resolution: it will still contain the leftover literal `=======`, the second "theirs" text, and `>>>>>>> feature` line spliced in as plain content — an unresolved/corrupted conflict silently ready to be committed.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L200-237)
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
