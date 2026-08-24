### Title
Copilot conflict-resolution engine matches resolved hunks to conflict blocks by position, not identity, allowing a single malformed/ambiguous marker anywhere in a file to silently swap resolution content between unrelated conflicts - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The `_liquidateUser()` bug is fundamentally about a single shared parameter (`minAssetAmount`) being applied across multiple independent items in a batch, where per-item validation is too coarse (count/threshold-only) to catch item-specific mismatches — causing either silent wrong outcomes or a spurious total failure. GitHub Desktop's AI merge-conflict resolver has the same broken invariant: it validates only that the *count* of hunks returned for a file matches the *count* of conflict markers found on disk, then splices the resolved content back in **purely by array order**. Nothing ties a specific `resolvedContent` entry to the specific conflict block it was generated for.

### Finding Description
`extractConflictHunks` walks a conflicted file and builds an ordered list of `IConflictHunk` using only regex marker detection (`<{7}`, `|{7}`, `={7}`, `>{7}`) with no cross-check against git's actual conflict-region metadata: [1](#0-0) [2](#0-1) 

The resulting hunk count is the only thing later checked for correctness. `validateResolutionPaths` enforces that the number of resolutions returned by Copilot for a file equals `expectedFiles` hunk count — it never checks that resolution `i` actually corresponds to conflict block `i`: [3](#0-2) 

Then `reassembleResolvedFile` re-scans the raw on-disk content independently (using its own copy of the same regexes) and splices `hunkResolutions[hunkIndex]` into the `hunkIndex`-th marker block it encounters, purely by traversal order: [4](#0-3) 

Because both the extraction and the reassembly rely solely on textual pattern-matching for `<<<<<<<`/`=======`/`>>>>>>>`/`|||||||` with no semantic anchor (no diff3 base-hash, no per-hunk id, no content fingerprint), any line elsewhere in the file that happens to match `oursMarker`/`separatorMarker`/`theirsMarker` (e.g., a documentation string illustrating git conflict syntax, a vendored `.diff`/`.patch` file, a leftover unresolved marker from an earlier bad merge, or content deliberately crafted by a remote contributor in a merged/rebased branch) is indistinguishable from a genuine conflict boundary. If such a line sits between two real conflicts, the model still returns the correct total hunk count (satisfying `validateResolutionPaths`), but the *positional correspondence* between "hunk N as understood when building the prompt" and "hunk N as encountered during reassembly" can shift — attaching one conflict's resolved content to a different, unrelated conflict block. This is the exact bug-class from H-20: a shared, coarse validation (count/threshold) is substituted for per-item identity validation across a batch of independent items (each conflict hunk / each liquidation), so the aggregate check passes while individual items are silently mismatched.

### Impact Explanation
If resolution content is spliced into the wrong conflict block, the file that GitHub Desktop reports as "resolved by Copilot" and stages for commit contains merged code that does not correspond to the actual intent the model reasoned about for that region. This is a silent corruption of what the user commits and pushes — the class of impact explicitly listed as in-scope ("silent corruption of what the user commits or pushes"), driven by content in a fetched/merged repository that the user does not fully control (their collaborator's branch, a rebased/cherry-picked commit, or a repo containing tutorial/vendored text with marker-like lines).

### Likelihood Explanation
This requires no local access, no admin rights, and no prior compromise — only that a real merge/rebase/cherry-pick conflict occurs in a file that also happens to contain another line matching the same 7-character marker regexes (a realistic occurrence in documentation, changelogs, vendored patch files, or repos that teach git internals), which is a plausible but not everyday scenario. It also depends on the Copilot conflict-resolution feature being enabled and used, and I was not able to fully trace, within the available context, the exact write-to-disk call site that consumes `reassembleResolvedFile`'s output or confirm whether any pre-commit diff review step would surface the swap to the user before staging — this limits certainty about end-to-end exploitability and should be verified against the full source (the index used here may not include every relevant file).

### Recommendation
Anchor each hunk to a stable identity beyond ordinal position — e.g., include a content hash or the original marker's line offset in the payload sent to Copilot and require it to be echoed back, and validate on reassembly that the resolution being spliced into position `i` actually matches the `oursContent`/`theirsContent` extracted at that same position (not just that counts match). Additionally, treat marker lines found outside an actual git-reported conflicted region (cross-checked via `git status`/index stage information) as ordinary content rather than conflict boundaries.

### Proof of Concept
Not independently exploited in this session (ask-only investigation); the mechanism is demonstrated by tracing the code paths above:
1. A file has two real conflicts plus one incidental line elsewhere that matches `oursMarker`/`theirsMarker` (e.g., a code comment or embedded example showing `<<<<<<< HEAD`).
2. `extractConflictHunks` and the independent regex walk in `reassembleResolvedFile` may segment the file differently depending on how that incidental line interacts with the marker state machine, shifting `hunkIndex` for genuine conflicts that follow it.
3. `validateResolutionPaths` only checks `resolution.hunks.length !== expectedCount` [5](#0-4) , so a positional shift that preserves the total count is undetected, and the wrong `resolvedContent` is spliced into the wrong location by `reassembleResolvedFile` [6](#0-5) .

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
