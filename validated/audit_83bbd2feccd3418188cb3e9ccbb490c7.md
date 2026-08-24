### Title
Copilot conflict resolution treats attacker-controlled conflict-marker-like text as real merge conflicts, causing silent corruption of committed content - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The Copilot-assisted merge-conflict resolution feature extracts "conflict hunks" from a file purely by regex-matching lines that look like Git conflict markers (`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts`), then later splices the model's per-hunk resolutions back into the file purely by counting matched marker blocks in file order (`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts`). Neither function verifies that a marker block was actually inserted by Git for a real conflict versus incoming/attacker-controlled file content that merely contains marker-like text (e.g. `<<<<<<<`/`=======`/`>>>>>>>` sequences embedded in documentation, test fixtures, generated diffs, or string literals). Because reassembly is purely index/position based, any such look-alike block inside a conflicted file gets silently replaced with LLM-generated content — content the user never actually reviewed as a "conflict" — corrupting what ends up staged and committed.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) walks a file's lines and treats *any* line matching `oursMarker = /^<{7}(?:\s|$)/` as the start of a conflict hunk: [1](#0-0) 

This detection has no relationship to Git's actual conflict state (e.g., it isn't restricted to the lines Git itself wrote, isn't validated against `git status`/index stage information beyond "this file is reported conflicted"). A file that is genuinely conflicted by Git but which *also* contains unrelated marker-like text elsewhere (documentation about Git conflicts, example/test fixtures such as those literally embedded in this repo's own tests, generated diff/patch content, etc.) will have those look-alike blocks parsed as additional "conflicts" and sent to the model: [2](#0-1) 

The raw file content (`rawContent`) is preserved and later replayed against by `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`), which walks the same loose regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) and, for every matched block — real or look-alike — deletes the entire block and splices in `hunkResolutions[hunkIndex]` purely by positional order: [3](#0-2) 

`validateResolutionPaths` only checks that the *count* of hunks returned by the model matches the count extracted from the file — it never validates the *content* or the semantic legitimacy of each hunk: [4](#0-3) 

This is the same broken invariant as the reported smart-contract bug: a downstream operation (splicing resolved content / crediting rewards) is driven by a positional/count-based value (`hunkIndex` / `balanceOf`) rather than the actual authoritative value it should correspond to (the true conflict region / the approved `amount_`), and the code that "clears" or replaces state proceeds even though the underlying assumption (this block is a genuine conflict; this balance equals `amount_`) can be false. Just as `GaugeV1` clears `s_claimableRewardsByGauge` while never verifying the transferred amount matches what was recorded, `reassembleResolvedFile` overwrites file regions and treats the file as "resolved" without verifying each matched block was an authentic Git conflict.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A user who fetches/clones a repository containing crafted marker-like text (e.g., in documentation, code comments, string literals, or files that legitimately discuss/contain Git conflict-marker syntax) and later hits a real merge/rebase/cherry-pick conflict in that same file will, upon using "Resolve with Copilot," have unrelated regions of the file silently rewritten by the LLM and applied to disk/the index — without the user ever being shown that those regions were treated as "conflicts." Because the UI's review surface is oriented around the model's per-file `reasoning` and diff, not an explicit warning that N marker-like regions were detected beyond the real conflicts, a user could commit and push content they never intended to change, potentially including remotely-influenced logic changes.

### Likelihood Explanation
Requires: (1) the victim clones/fetches a repository (fully within the threat model — "attacker controls a cloned/fetched repository"), (2) that repository contains a file with both a real, unresolved Git conflict and unrelated but marker-shaped text, and (3) the user invokes the Copilot resolve-conflicts feature on that file. No local access, admin rights, or social engineering beyond normal repository consumption is required; likelihood depends on how often files with real conflicts also carry marker-like text, but an attacker can deliberately engineer this scenario (e.g. planting a "test fixture" or "example" file containing marker syntax, or crafting a file whose eventual merge will conflict) to weaponize it.

### Recommendation
- Do not rely solely on regex text scanning of the on-disk file. Cross-reference detected conflict regions against Git's own conflict metadata (e.g., `git diff --check`/`ls-files -u` stage information, or parse via `git merge-file`/`git ls-files --unmerged`) to ensure each detected `<<<<<<<`/`=======`/`>>>>>>>` block corresponds to an actual unmerged blob boundary, not incidental file content.
- In `reassembleResolvedFile`, validate that the `oursContent`/`theirsContent` extracted for a given hunk index matches what was actually sent to and referenced by the model for that same index before splicing, rather than trusting position/order alone.
- Surface an explicit UI warning (and require confirmation) whenever the number of detected marker blocks in a file exceeds what Git itself reports as conflicted regions for that path, so users are not silently exposed to unreviewed rewrites.

### Proof of Concept
1. Attacker prepares a repository/branch such that a shared file (e.g. `docs/merge-guide.md` or a test fixture) contains legitimate content that includes literal lines beginning with 7 `<`/`=`/`>` characters (as this repo's own tests do, e.g. `app/test/unit/copilot-conflict-resolution-test.ts:536-552`), positioned near code the attacker wants altered.
2. Victim merges/rebases and the file legitimately conflicts elsewhere (unrelated hunk).
3. Victim opens the conflict resolution dialog and selects "Resolve with Copilot."
4. `buildConflictContext` → `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) treats the attacker's marker-like text as an additional conflict hunk and sends it to the model.
5. The model returns a resolution for that "hunk" (it has no way to know it isn't a real conflict).
6. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:559-596`) deletes the attacker-designated block and splices in the model's generated content, all without any Git-conflict-state cross-check.
7. The victim applies the resolution and commits/pushes, unknowingly including content they never reviewed as a conflict.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-165)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/

/**
 * Absolute upper bound (in bytes) on a conflicted file we'll read into memory.
 *
 * This is a memory-safety guard only, not a resolvability heuristic — we only
 * ever send the *conflict hunks* to the model, never the whole file, so a large
 * file with a small conflict is still perfectly resolvable. Files above this
 * size are skipped before reading to avoid loading pathological blobs (e.g. a
 * multi-megabyte generated lockfile) into a string.
 */
const MAX_CONFLICT_FILE_READ_SIZE = 10_485_760 // 10MB

/**
 * Maximum length (in characters) of any single line within a conflict hunk.
 *
 * Mirrors the diff renderer's `MaxCharactersPerLine`. Conflicts containing a
 * line longer than this are almost always minified/generated content where a
 * line-oriented resolution is meaningless, so we skip them rather than sending
 * an enormous single line to the model.
 */
const MAX_CONFLICT_LINE_LENGTH = 5000

/**
 * Maximum combined size (in characters) of the actual conflict content in a
 * single file — the sum of the ours/base/theirs text across every hunk.
 *
 * Unlike a whole-file cap, this measures what we actually send to the model, so
 * it protects prompt size and output quality (truncation/malformed JSON)
 * without penalising large files whose conflicts are small.
 */
const MAX_CONFLICT_CONTENT_SIZE = 262_144 // 256KB

function isConflictMarker(line: string): boolean {
  return (
    oursMarker.test(line) ||
    baseMarker.test(line) ||
    separatorMarker.test(line) ||
    theirsMarker.test(line)
  )
}
```

**File:** app/test/unit/copilot-conflict-resolution-test.ts (L536-552)
```typescript
  it('handles multi-line resolved content', () => {
    const raw = [
      'start',
      '<<<<<<< HEAD',
      'a',
      '=======',
      'b',
      '>>>>>>> feature',
      'end',
    ].join('\n')

    const result = reassembleResolvedFile(raw, [
      { resolvedContent: 'line1\nline2\nline3' },
    ])

    assert.equal(result, ['start', 'line1', 'line2', 'line3', 'end'].join('\n'))
  })
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
