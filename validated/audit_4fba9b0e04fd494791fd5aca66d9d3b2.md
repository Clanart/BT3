## Analysis

The report's broken invariant is: an accounting/repair mechanism (burning fee tokens) desynchronizes tracked state (debt ledger) from the actual on-disk value (token supply), silently making some state permanently unreconcilable while looking normal to the rest of the system. The closest analog in this Desktop codebase is in the **Copilot conflict-resolution reassembly pipeline**, where `reassembleResolvedFile` splices model-provided hunk resolutions back into attacker-influenced on-disk file content using a **regex-based conflict-marker scanner** rather than parsing against the actual git conflict structure. If the malicious/incoming branch content contains lines that incidentally match the marker regexes (`^<{7}`, `^={7}$`, `^>{7}`), the splicing walk can be fooled into treating ordinary content as a marker boundary (or vice versa), silently producing a resolved/committed file that does not correspond to what the hunks were computed against — a corruption of what the user commits, without any error surfaced.

### Title
Regex-based conflict-marker reassembly can silently splice mismatched content into a committed file when a remote branch contains marker-like lines - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile` [1](#0-0)  reconstructs the final file to be written to disk (and later committed) by scanning line-by-line for regex-matched conflict markers and splicing in the model's per-hunk resolution by **positional index**, not by verifying the spliced region corresponds to the same hunk boundaries `extractConflictHunks` [2](#0-1)  used to build the prompt. Both functions rely purely on line-level regexes (`^<{7}`, `^={7}$`, `^>{7}`) to detect markers in file content that originates from an attacker-controlled incoming branch/commit.

### Finding Description
Two independent passes over the same conflicted file content use loose, line-anchored regex matching for conflict markers:
- `extractConflictHunks` builds the hunk list sent to Copilot from the *ours*/*theirs* text and any lines matching `oursMarker`/`separatorMarker`/`theirsMarker`/`baseMarker` [3](#0-2) .
- `reassembleResolvedFile` walks the *same raw file content* again independently, using its own copies of the marker regexes, to decide where to splice in the model's returned `resolvedContent` for each hunk, matched **by order, not by content or line offset** [4](#0-3) .

Because the incoming ("theirs") side of a merge/rebase/cherry-pick is attacker-controlled (a remote branch, PR head, or fetched ref), an attacker can craft file content whose *ours* or *theirs* payload contains lines that are only near-misses of the marker patterns (e.g., an intentionally malformed 7-char run, or an actual valid marker sequence embedded in a code string/comment/heredoc that legitimately needs to appear literally, such as documentation about git conflict markers, or crafted minified content). The malformed-marker fallback path explicitly treats a `<<<<<<<` not followed by both `=======` and `>>>>>>>` as ordinary content and copies it through unchanged [5](#0-4) , and `extractConflictHunks` silently drops any block for which no closing marker is found [6](#0-5) . This means the two passes can disagree on how many "real" hunks exist in the file depending on subtly different placement of marker-like lines (one accepts a hunk the other treats as noise, or the counted hunk order shifts), yet `validateResolutionPaths` only checks that the *hunk count* returned by the model matches the count `extractConflictHunks` computed [7](#0-6)  — it does not re-verify that `reassembleResolvedFile`'s independent walk over the same content will align hunk index N with the same block that `extractConflictHunks` labeled hunk N. Because both scanners are pure regexes operating on attacker-supplied bytes rather than a shared, single source of truth for hunk boundaries, a hunk-count match does not guarantee identical hunk *boundaries* between the two passes.

The existing "still contains conflict markers" guard on the model output [8](#0-7)  only inspects the model's own returned string, not the original file, so it does nothing to protect against marker-boundary drift caused by the original repository content itself.

### Impact Explanation
If the splice boundaries drift, the file that Desktop ultimately writes to disk and stages/commits on the user's behalf silently mixes unrelated code regions with the model's `resolvedContent`, producing a corrupted commit that does not match either side of the merge nor what the user reviewed/approved in the dialog. This is a silent corruption of what the user commits — the exact impact class called out as valid (corruption of what a user commits/pushes), driven entirely by content in a repository the user merged/fetched from an untrusted remote, with no local access or privilege required.

### Likelihood Explanation
This requires the attacker to control the incoming branch/commit content that later conflicts with the victim's local branch during merge/rebase/cherry-pick, and for the victim to invoke the Copilot conflict-resolution feature on that conflict. It further depends on the parser regexes disagreeing between the two passes for genuinely crafted marker-adjacent content — plausible with deliberately engineered payloads (e.g. templated files, generated lockfiles, or files that legitimately discuss/contain marker-like strings) but not something an attacker can trivially trigger by chance. I could not run the reassembly/extraction code against a concrete crafted payload in this environment, so I cannot confirm a working divergent input in this session — this should be validated with a live PoC before being treated as fully proven.

### Recommendation
Make `extractConflictHunks` and `reassembleResolvedFile` operate over a single shared representation of hunk boundaries (e.g., have `extractConflictHunks` return the line ranges/offsets of each hunk, and have `reassembleResolvedFile` consume those offsets directly instead of independently re-scanning the raw text with its own copies of the marker regexes). Additionally, validate after reassembly that removing conflict markers from `rawContent` and diffing against the reassembled result only differs within the exact spliced hunk regions, rejecting/falling back to manual resolution if not.

### Proof of Concept
Conceptual (not executed in this session):
1. Attacker pushes a branch/commit whose changed region contains a line that is a false-positive marker under one regex path but not the other — e.g. a line consisting of exactly `=======` inside a multi-line string/heredoc/YAML block that is *not* part of a real conflict, combined with a genuine conflict elsewhere in the file such that `extractConflictHunks`'s look-ahead binds the separator to the wrong `<<<<<<<`/`>>>>>>>` pair than `reassembleResolvedFile`'s independent walk does.
2. Victim merges/rebases against this branch, gets a real conflict in the same file, and runs "Resolve with Copilot."
3. `extractConflictHunks` sends hunk boundaries to the model based on its scan; `reassembleResolvedFile` later splices the model's `hunks[i].resolvedContent` into boundaries computed by its own, separately-scanning walk.
4. `validateResolutionPaths` only checks hunk *count* equality [9](#0-8) , so a count match masks a boundary mismatch, and the file written to disk/staged for commit contains spliced content misaligned with the actual conflicting regions.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-449)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
      validatedHunks.push({ resolvedContent: rc })
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-599)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/

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

**File:** app/src/lib/copilot-conflict-context.ts (L179-279)
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

    // The ours marker line is at oursStart - 1
    const markerStart = oursStart - 1
    const contextStart = Math.max(0, markerStart - contextLines)
    const contextEnd = Math.min(lines.length - 1, hunkEnd + contextLines)

    // Clamp context to not include conflict markers from adjacent hunks
    const contextBeforeLines: Array<string> = []
    for (let j = markerStart - 1; j >= contextStart; j--) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextBeforeLines.unshift(lines[j])
    }

    const contextAfterLines: Array<string> = []
    for (let j = hunkEnd + 1; j <= contextEnd; j++) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextAfterLines.push(lines[j])
    }

    const contextBefore = contextBeforeLines.join('\n')
    const contextAfter = contextAfterLines.join('\n')

    hunks.push({
      oursContent: oursLines.join('\n'),
      theirsContent: theirsLines.join('\n'),
      baseContent: hasBase ? baseLines.join('\n') : null,
      contextBefore,
      contextAfter,
    })
  }

  return hunks
}
```
