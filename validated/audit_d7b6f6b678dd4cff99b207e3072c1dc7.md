### Title
Decoy Conflict-Marker Text Causes Silent Misapplication of AI Merge Resolutions - ([File: app/src/lib/copilot-conflict-context.ts], [File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
GitHub Desktop's Copilot-assisted merge-conflict resolution feature extracts conflict "hunks" from a conflicted file using a purely textual, position-based scan (`extractConflictHunks`), sends them to the model, and then splices the model's per-hunk responses back into the file by **array index order, not by content or line-number identity** (`reassembleResolvedFile`). Any line sequence in a real, git-conflicted file that merely *looks like* a conflict marker block (`<<<<<<<`, `=======`, `>>>>>>>` at column 0) — even if it is ordinary tracked content unrelated to the actual merge — is indistinguishable from a genuine conflict to both functions. This lets a maliciously crafted repository file shift the hunk index alignment between extraction and reassembly, causing the wrong AI-generated resolution to be spliced into the real conflict location while a fabricated/unrelated block silently overwrites benign file content.

### Finding Description
`extractConflictHunks` in [1](#0-0)  walks a conflicted file line-by-line and treats *any* line matching the marker regexes (`oursMarker`, `baseMarker`, `separatorMarker`, `theirsMarker`) as the start/end of a conflict hunk, with no validation that the block was actually produced by git's merge machinery (e.g. cross-checking against `git status`/index stage count for that file). As confirmed by the project's own test suite, only column position and exact marker length matter — content-embedded look-alikes are excluded, but standalone marker-syntax lines are always treated as real conflicts [2](#0-1) .

The extracted hunks are shipped to the model with an explicit contract that resolutions are returned "in order," matched positionally, not by content: [3](#0-2) .

Reassembly then performs the same naive, purely positional walk over the raw file content, incrementing `hunkIndex` for every well-formed marker block found and splicing `hunkResolutions[hunkIndex]` into place, explicitly documented as "matched by order, not by line number": [4](#0-3) .

Because a repository can contain a tracked file with a real merge conflict in one region and a *pre-existing, static* block of text elsewhere in that same file that happens to satisfy the marker regex (e.g., a code comment, README, or test fixture documenting conflict-marker syntax as literal example lines), that decoy block is counted as an additional "hunk" by `extractConflictHunks`, shifting all subsequent hunk indices. The LLM has no way to know the decoy block isn't a real conflict — it will produce a plausible-looking resolution for it per the system prompt's blanket instruction to resolve every detected hunk [5](#0-4) . On reassembly, this misalignment causes the model's resolution intended for the real conflict to be spliced into the decoy's position (silently discarding/altering unrelated static content) while the decoy's fabricated resolution is spliced into the real conflict's location — producing merge output the user did not actually author or approve, which the app then writes to disk and stages/commits as the "resolved" version, entirely on the model's untrusted, index-based ordering.

### Impact Explanation
This breaks the invariant that Desktop-assisted conflict resolution only ever replaces the *actual* conflicted regions of a file with content, human-reviewed or not, that corresponds to that same region. Instead, an attacker who controls file content merged into a victim's repository (via a branch, PR, or file the victim's tooling produces during a real merge) can cause:
- Silent corruption of what gets committed: unrelated file regions get overwritten with AI-hallucinated content, and the real conflict resolution ends up in the wrong place.
- A vector for content smuggling/logic corruption in a spliced, un-reviewed manner, especially since the whole exchange runs through a network round trip (Copilot session) with no structural tie-back to the original marker boundaries at commit time.

This matches the report's underlying class: an approximation/shortcut (index-based matching instead of exact positional/content identity) that silently produces an incorrect result under an edge condition (decoy markers) that the existing checks (`validateResolutionPaths`, hunk-count parity) do not catch, because hunk *count* still matches — only *identity* is wrong.

### Likelihood Explanation
Requires only that the victim (a) has GitHub Desktop's Copilot conflict-resolution feature enabled and used on a real merge/rebase conflict, and (b) that conflict occurs in a file which also contains a properly-formatted decoy marker block placed by the attacker (via any commit reachable by the victim's branches, e.g. a doc/fixture file demonstrating conflict-marker syntax). No local access, no elevated privileges, and no unnatural user action beyond using the feature as intended are needed. The main uncertainty is whether the UI's post-resolution diff review would let an attentive user catch the misplacement before committing — this could not be fully verified within the explored code, but the swap is subtle (both regions still look like plausible "resolved" code), which reduces the odds a user reviewing a diff catches a wrong-content splice versus a wrong-marker-count error.

### Recommendation
Tie the model's `IHunkResolution` entries to their originating marker block by stable identity (e.g., line offsets/marker byte ranges captured at extraction time) rather than pure array order, and validate at reassembly time that the exact same conflict-marker block (same `oursContent`/`theirsContent` hash) is present at the position being spliced. Additionally, cross-check hunk counts/positions extracted by `extractConflictHunks` against the actual conflicted ranges reported by `git`/the index (e.g., via `git diff --check` or stage-2/3 blob diffs) rather than relying solely on textual marker pattern matching.

### Proof of Concept
1. Attacker adds `conflict-example.ts` to a shared branch containing, verbatim at column 0:
   ```
   // Example of a git conflict for documentation purposes:
   <<<<<<< HEAD
   decoyOurs();
   =======
   decoyTheirs();
   >>>>>>> feature
   ```
   elsewhere untouched by either branch, plus a genuine line further down in the file that both branches modify differently (creating a real conflict).
2. Victim merges the branch; `git` reports `conflict-example.ts` as conflicted (`UU`) due to the real, unrelated line change; the decoy block remains verbatim, untouched, in the file.
3. Desktop invokes Copilot conflict resolution: `buildConflictContext` → `extractConflictHunks` [6](#0-5)  finds 2 "hunks" — the decoy and the real conflict, in file order.
4. The model returns 2 `resolvedContent` entries in order per prompt contract [7](#0-6) ; `validateResolutionPaths` only checks hunk *count* parity, not identity [8](#0-7) .
5. `reassembleResolvedFile` splices resolution[0] into the decoy block and resolution[1] into the real conflict purely by encounter order [9](#0-8)  — if the model, confused by the decoy's ambiguous surrounding context, swaps its intended real-conflict fix into hunk[0]'s slot (e.g. because it reasoned about hunk ordering differently than the code's line-order scan, or the decoy content influenced its output for the wrong slot), the real conflict gets the decoy's resolution and vice versa, silently, with no error raised.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-242)
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

    // If we never found the closing marker, skip this malformed hunk
    if (hunkEnd === -1) {
      continue
    }
```

**File:** app/src/lib/copilot-conflict-context.ts (L440-447)
```typescript
      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }
```

**File:** app/test/unit/copilot-conflict-context-test.ts (L326-344)
```typescript
    it('does not treat markers inside content as boundaries', () => {
      // Conflict markers must start at column 0 with exactly 7 characters
      const content = [
        '<<<<<<< HEAD',
        'const s = "<<<<<<< not a real marker"',
        '=======',
        'const s = ">>>>>>> also not real"',
        '>>>>>>> feature',
      ].join('\n')

      const hunks = extractConflictHunks(content)

      assert.equal(hunks.length, 1)
      assert.equal(
        hunks[0].oursContent,
        'const s = "<<<<<<< not a real marker"'
      )
      assert.equal(hunks[0].theirsContent, 'const s = ">>>>>>> also not real"')
    })
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L190-245)
```typescript
export const ConflictResolutionSystemPrompt = `
Respond ONLY with valid JSON in the format specified below. Do NOT use tools.

You are an expert Git conflict resolver. Analyze conflicts from merge, rebase, or cherry-pick operations and produce correct, clean resolutions.

You will receive:
- Labels for both sides (branch names or commit refs)
- Conflict markers from each file (ours, theirs, optionally base)
- Context lines surrounding each conflict
- Delete-vs-modify conflicts where one side deleted a file and the other modified it
- When available: recent commit messages and/or PR title/description for intent

Your job:
1. Understand the INTENT behind each side's changes
2. Resolve each conflict by producing the correct merged content for each conflict hunk
3. For delete-vs-modify conflicts, recommend whether to keep or delete the file
4. Explain your reasoning per file — terse but specific enough to verify the decision
5. Produce a brief markdown summary orienting the user to the conflict and resolution

Resolution guidelines:
- Make MINIMAL changes — do not refactor, reformat, or alter code outside conflicted regions
- When both sides add complementary code (e.g., different imports), combine them
- When both sides modify the same code differently, use commit messages and PR context to decide
- When one side deletes code the other modifies, check whether the content was relocated rather than simply removed — accept the deletion only when it was intentional
- When conflicts involve dependency manifests or lock files, ensure version constraints and entries remain consistent across the resolved file
- Preserve correctness: imports, types, formatting must remain valid
- When in doubt, prefer backward compatibility

Response format:
{
  "summary": "### Conflicting changes\\n<1-2 sentences: what each side did and where they collided, attributing each to its #PR or short SHA>\\n\\n### Resolution\\n<1 sentence: how you resolved it; if a side was dropped, bold that trade-off>",
  "references": [
    { "type": "pullRequest", "id": "1234" },
    { "type": "commit", "id": "abc1234" }
  ],
  "resolutions": [
    {
      "path": "relative/file/path.ts",
      "hunks": [
        { "resolvedContent": "merged content that replaces conflict 1" },
        { "resolvedContent": "merged content that replaces conflict 2" }
      ],
      "reasoning": "What each side changed in this file, what you kept, and what you dropped or overrode."
    },
    {
      "path": "deleted-or-modified/file.ts",
      "action": "keep",
      "hunks": [],
      "reasoning": "The file was modified with important changes; the deletion was part of an incomplete refactor."
    }
  ]
}

Field rules:

hunks: An ordered array with one entry per conflict in the file, matching the "Conflict 1 of N", "Conflict 2 of N" order from the input. Each entry's resolvedContent is ONLY the merged content that replaces that specific conflict marker block (the region between <<<<<<< and >>>>>>>). Do NOT include surrounding non-conflicted code — the application splices each resolution into the original file automatically. If the resolution is to accept one side entirely, return that side's content verbatim. For an intentional deletion, use an empty string. For delete-vs-modify conflicts, hunks must be an empty array.
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-599)
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

  return resultLines.join(eol)
}
```
