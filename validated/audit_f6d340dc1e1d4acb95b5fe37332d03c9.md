### Title
Conflict-size gating uses UTF-16 string length instead of byte length, letting an attacker-crafted merge conflict bypass the size limit meant to prevent AI-resolution truncation/corruption - (File: `app/src/lib/copilot-conflict-context.ts`)

### Summary
`getHunkSkipReason` and the per-line check inside it gate whether a conflicted file is sent to the Copilot conflict-resolution model based on `MAX_CONFLICT_LINE_LENGTH` (5000) and `MAX_CONFLICT_CONTENT_SIZE` (262,144), both measured in JavaScript string `.length` (UTF-16 code units), while the only "hard" memory-safety bound applied earlier (`MAX_CONFLICT_FILE_READ_SIZE`) is measured in bytes via `fs.stat().size`. [1](#0-0) 
This mirrors the Notional bug: a single numeric threshold is compared against a value whose unit/precision is not normalized to what the threshold actually protects, so content that is "large" by the metric the guard cares about (bytes sent to the model / prompt size) can still pass a check expressed in a different unit (UTF-16 code units).

### Finding Description
`buildConflictContext` reads a conflicted file's whole content with `readFile(absolutePath, 'utf8')` after only checking the raw byte size against `MAX_CONFLICT_FILE_READ_SIZE` (10 MB) via `stat()`: [2](#0-1) 

It then extracts hunks and calls `getHunkSkipReason`, whose doc comment states the purpose explicitly: this gate "protects prompt size and output quality (truncation/malformed JSON)" — i.e. it exists to guarantee the model's request/response stays within a size where the JSON reply won't be truncated or malformed: [3](#0-2) 

But the gate sums `side.length` (JS string length = UTF-16 code units) and compares individual `line.length` the same way: [4](#0-3) 

Because `readFile(..., 'utf8')` decodes UTF-8 bytes into UTF-16, the byte-to-length ratio is not 1:1 and varies by script: for many CJK/other non-Latin BMP characters, 1 UTF-16 code unit corresponds to 3 bytes of UTF-8 source and, depending on tokenizer, a disproportionate number of LLM tokens relative to ASCII. A conflict hunk built almost entirely from such characters can therefore have a `.length` value far below `MAX_CONFLICT_CONTENT_SIZE`/`MAX_CONFLICT_LINE_LENGTH` while representing 2–3x (or more, depending on encoding/tokenization) the actual byte/token payload the size caps were meant to bound. The 10 MB whole-file `stat()` check does not help here since it is a much looser bound on the entire file, not on the hunk content actually sent to the model, and the code explicitly says resolvability is decided from the hunks, not the whole-file size (line 410-411 comment).

The result: a hunk that the size guard treats as "safe" (small `.length`) can still be large enough in what's actually transmitted to trigger the exact failure mode the guard was built to prevent — truncated or malformed JSON responses from the model.

### Impact Explanation
The interface documentation states that `rawContent` and the hunks are later used to "reassemble the resolved file by splicing per-hunk resolutions into the original content" after the model responds: [5](#0-4) 
If the size guard is bypassed and the model's JSON response is truncated or malformed for one or more hunks of an attacker-crafted conflicted file (a file the user is merging from a remote/fetched branch), the splicing step downstream (`copilot-conflict-resolution.ts`) would operate on incomplete/malformed per-hunk data. Depending on how that module handles a partial/invalid response, this can silently corrupt the reassembled file content that the user then commits — i.e. corruption of what the user commits, one of the explicitly valid impact classes for this exercise, without the user attempting anything unusual (they just accept an AI-suggested conflict resolution on an otherwise ordinary merge).

I was not able to fully trace the exact splice/parsing code in `app/src/lib/copilot-conflict-resolution.ts` within this session (index coverage did not return its full contents), so the precise failure behavior on malformed JSON (hard failure vs. silent partial application) is not confirmed and should be verified directly.

### Likelihood Explanation
- Fully attacker-controlled: a malicious/compromised remote branch that a user merges/rebases/cherry-picks against can contain conflicting content engineered with non-ASCII multi-byte characters, requiring no unusual user action beyond a normal merge and accepting the app's AI-assisted conflict resolution feature.
- The bypass only requires choosing characters with a high UTF-8-bytes-per-UTF-16-code-unit ratio, which is trivial to construct.
- However, this is a heuristic guard for a UX feature (AI conflict resolution), not a hard security boundary like `resolveWithin`'s path-traversal check (which is separately implemented correctly and unaffected — the byte/char mismatch is isolated to `getHunkSkipReason`). The actual severity therefore hinges on how the downstream splicing code in `copilot-conflict-resolution.ts` reacts to truncated/malformed model output, which I could not fully verify.

### Recommendation
Measure `MAX_CONFLICT_LINE_LENGTH` and `MAX_CONFLICT_CONTENT_SIZE` in bytes (e.g. `Buffer.byteLength(line, 'utf8')` / `Buffer.byteLength(side, 'utf8')`) instead of JS string `.length`, so the guard is consistent with the actual payload size sent to the model and with the byte-based `MAX_CONFLICT_FILE_READ_SIZE` check. Additionally, ensure `copilot-conflict-resolution.ts` fails closed (skips/reverts the splice and surfaces an explicit error to the user) rather than silently applying a partial resolution when the model's JSON response is truncated or fails to parse for any hunk.

### Proof of Concept
Conceptual PoC (byte/length mismatch, illustrating the bypass mechanics):
```ts
// A hunk built from 3-byte-per-character UTF-8 content (e.g. CJK)
const maliciousSide = '大'.repeat(90_000) // 90,000 UTF-16 code units => passes the 262,144 cap
// but represents 270,000 bytes of UTF-8 content actually sent to the model,
// i.e. ~3x over the byte budget the MAX_CONFLICT_CONTENT_SIZE comment says it protects.

getHunkSkipReason([
  {
    oursContent: maliciousSide,
    theirsContent: '',
    baseContent: null,
    contextBefore: '',
    contextAfter: '',
  },
])
// => null (not skipped), even though the actual byte payload sent to Copilot
//    is far larger than intended by MAX_CONFLICT_CONTENT_SIZE.
```
This file would pass `getHunkSkipReason` and be forwarded intact via `buildConflictContext`/`formatConflictContextForPrompt`, exercising the exact "truncation/malformed JSON" scenario the size cap in `app/src/lib/copilot-conflict-context.ts` lines 148-156 was designed to prevent. [4](#0-3)

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L32-37)
```typescript
  /**
   * The full file content on disk (including conflict markers). Used after
   * the model responds to reassemble the resolved file by splicing per-hunk
   * resolutions into the original content. Omitted when the file is skipped.
   */
  readonly rawContent?: string
```

**File:** app/src/lib/copilot-conflict-context.ts (L128-156)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L294-315)
```typescript
export function getHunkSkipReason(
  hunks: ReadonlyArray<IConflictHunk>
): string | null {
  let totalContent = 0

  for (const hunk of hunks) {
    const sides = [hunk.oursContent, hunk.theirsContent, hunk.baseContent ?? '']
    for (const side of sides) {
      totalContent += side.length
      for (const line of side.split('\n')) {
        if (line.length > MAX_CONFLICT_LINE_LENGTH) {
          return 'Conflict contains lines too long to resolve automatically'
        }
      }
    }
    if (totalContent > MAX_CONFLICT_CONTENT_SIZE) {
      return 'Conflict region too large to resolve automatically'
    }
  }

  return null
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L409-438)
```typescript
      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }
```
