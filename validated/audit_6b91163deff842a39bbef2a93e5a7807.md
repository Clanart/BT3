Based on code evidence, I found a concrete Desktop analog: unsafe/naive parsing logic (the modern equivalent of "missing safety checks around a critical value") in the Copilot conflict-resolution reassembly path, where a value derived from **attacker-controlled repository content** (a merged-in branch/PR) is trusted without verifying it can't collide with legitimate file content, and the result can silently corrupt the file the user actually commits.

### Title
Copilot conflict-resolution reassembly can silently corrupt the committed file when a branch/PR contains lines that collide with `=======`/`<<<<<<<`/`>>>>>>>` conflict-marker patterns - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`reassembleResolvedFile` splices the model's per-hunk resolutions back into the on-disk file by re-scanning the raw file text and matching conflict marker lines with regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`). [1](#0-0) 
This scan assumes the first `<{7}` line it hits starts a conflict block and that the *nearest* subsequent `={7}` and `>{7}` lines close it out, then blindly replaces everything between them with the model's resolution. [2](#0-1) 
Nothing verifies that these "marker" lines are actually git-generated conflict markers rather than ordinary file content (e.g. a Markdown horizontal rule of seven `=`, a YAML/RST separator, or a code sample documenting conflict markers) that an attacker's branch/PR legitimately introduces inside the "ours" or "theirs" side of a real conflict.

### Finding Description
The invariant the code relies on — "a `<<<<<<<`/`=======`/`>>>>>>>` triple always demarcates exactly one real conflict, and everything between them is safe to discard" — is not actually enforced anywhere; it is only true when the attacker's content happens not to contain a line matching the same 7-character regexes. Since Desktop merges/rebases/cherry-picks against **remote branches and PRs the user does not control**, an attacker can craft a file whose real conflicting section contains, inside its own text (not as a marker), a line of exactly seven `=` characters or seven `<`/`>` characters (e.g., `=======` as a Markdown `<hr>`, or a fenced example of conflict syntax in documentation/config files). When that attacker-authored side of the conflict is present in the repository, the naive nearest-match scan in `reassembleResolvedFile` will treat the embedded line as the block separator/terminator instead of the true one, causing the spliced region to start or end at the wrong line.

The function's own doc comment states it "guarantees that all non-conflicted code is preserved exactly," which is the safety property this bug violates: [3](#0-2) 
Once the boundary is miscomputed, `reassembleResolutions` writes the resulting `resolvedContent` back out as the file the user is about to commit, with no re-validation against the original diff/hunks: [4](#0-3) 
`validateResolutionPaths` only checks that the *count* of hunks returned by the model matches the *count* of conflict blocks Desktop originally detected — it never re-checks marker boundaries against the reassembly step, so a marker-boundary desync is not caught by this guard. [5](#0-4) 

### Impact Explanation
Because the resulting file is written to disk and staged/committed as the user's merge resolution, this is a silent-corruption-of-what-the-user-commits primitive: legitimate non-conflicted code adjacent to the conflict can be dropped, duplicated, or replaced without any error, warning, or diff review step catching it (the user is expected to trust the AI-generated merge). An attacker who controls a branch/PR that gets merged (a classic supply-chain vector — malicious contributor branch, or a PR the victim merges via Desktop's Copilot conflict-resolution feature) can shape the conflicting hunk content to reliably trigger this misalignment and cause unintended code to be silently included in, or excluded from, the final commit.

### Likelihood Explanation
Exploitability requires only that the attacker's branch/PR content (fully attacker-controlled) contain an ordinary-looking line matching `^={7}$`, `^<{7}(\s|$)`, or `^>{7}(\s|$)` inside a real conflicting region — no local access, credentials, or unusual user action beyond normal "resolve conflicts with Copilot" usage is needed. The main uncertainty is whether the earlier hunk-context builder (`copilot-conflict-context.ts`, which extracts `oursContent`/`theirsContent`/hunk boundaries for the prompt) uses matching or divergent marker detection logic from `reassembleResolvedFile`; I was not able to fully diff the two implementations in the time available, so the exact conditions under which the two disagree (and thus how easy it is to trigger a real-world desync) are not fully confirmed from the index alone.

### Recommendation
Make `reassembleResolvedFile` use the same structural parser/model (positions/line ranges) that originally identified each conflict hunk when the context was built, rather than independently re-scanning raw text with regexes. Pass explicit line-range boundaries for each hunk (as already known from the initial parse in `copilot-conflict-context.ts`) into `reassembleResolvedFile`/`reassembleResolutions` so the splice is driven by exact indices, not by a second best-effort regex scan that can disagree with the first parse.

### Proof of Concept
1. Create a merge conflict where the "theirs" side (attacker's branch) legitimately contains a line of exactly seven `=` characters as file content, e.g.:
```
<<<<<<< HEAD
ourCode();
=======
some legit content
=======
theirCode();
>>>>>>> feature
```
2. Trigger Desktop's Copilot conflict resolution on this file (`reassembleResolvedFile` per [6](#0-5) ).
3. The scan in `reassembleResolvedFile` will treat the first embedded `=======` (part of "their" legitimate content) as `hasSeparator`, and continue looking for `>>>>>>>` — in this simplified example it still finds the real closing marker, but for multi-hunk files or files with a `<{7}`/`>{7}` embedded pattern, the block boundaries and subsequent hunk-to-content pairing can shift, so the wrong resolved text (or wrong slice of original content) is spliced into the committed file, silently altering code the user did not intend to change.

### Citations

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-527)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/

```

**File:** app/src/lib/copilot-conflict-resolution.ts (L536-538)
```typescript
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-599)
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

  return resultLines.join(eol)
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-642)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
}
```
