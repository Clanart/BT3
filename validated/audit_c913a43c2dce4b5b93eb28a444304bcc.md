### Title
Copilot conflict-resolution reassembly can silently leave literal conflict markers in the "resolved" file that gets written and committed - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
Union-Finance's `AssetManager.withdraw()` always signals success even when only part of the requested amount was retrieved, and every caller then commits its own accounting as if the withdrawal fully succeeded — permanently corrupting the accounting for the shortfall. The same "declare completion without verifying it actually happened" pattern exists in GitHub Desktop's AI conflict-resolution pipeline: `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` [1](#0-0)  can copy an unclosed/malformed conflict-marker block through **unchanged**, yet the function's caller, `reassembleResolutions`, unconditionally packages the result as a fully resolved `IFileResolution.resolvedContent` [2](#0-1)  with no re-check that the emitted content is actually free of conflict markers. Nothing downstream re-verifies this before the content is written to disk and the file is staged/marked resolved.

### Finding Description
The reassembly path is: `buildConflictContext` reads the on-disk file once and calls `extractConflictHunks` to enumerate conflict hunks and stash the full `rawContent` [3](#0-2) . The Copilot model is prompted with those hunks and returns per-hunk `resolvedContent` strings, validated to individually not contain conflict markers [4](#0-3) . `validateResolutionPaths` additionally checks the *count* of hunks matches what was sent [5](#0-4) .

None of that validates the **final spliced file**. `reassembleResolvedFile` walks the original `rawContent` line-by-line and, for every `<<<<<<<`-looking line, re-scans forward to confirm a `=======` separator and a `>>>>>>>` closer exist before treating it as a real conflict block. If that lookahead fails ("malformed marker"), the code takes this branch:

```
if (!hasSeparator || closingIndex === -1) {
  // Malformed marker — copy through as regular content
  resultLines.push(lines[i])
  i++
  continue
}
``` [6](#0-5) 

This is a *content-preserving* fallback for genuinely unrelated lines that happen to start with `<<<<<<<`, but it is applied with a regex-based heuristic that only checks for well-formed marker syntax, not for genuine git conflict provenance — and, more importantly, applies identically to a **truly unresolved real conflict block whose original `>>>>>>>` marker got shifted or altered** (e.g. by attacker-controlled content elsewhere in the same file that this same regex-based scanner in `extractConflictHunks` also misparses, since both use the same three marker regexes and same line-oriented, no-nesting-aware algorithm). In particular, if the file's genuine conflict is preceded by attacker-controlled lines that happen to match `^={7}$` or `^>{7}` (fenced code, ASCII art, a markdown divider, or a deliberately crafted decoy in a merged branch), `extractConflictHunks`'s single-pass parser (which does not distinguish real markers from repository content that merely matches the same 7-character regex) can bind the wrong `=======`/`>>>>>>>` to an `ours` marker, shifting or dropping the real hunk boundary. Because `extractConflictHunks` and `reassembleResolvedFile` are two independently-written parses of the same `rawContent` (defined in two different files with duplicated marker constants — `oursMarker`/`separatorMarker`/`theirsMarker` in `copilot-conflict-context.ts` [7](#0-6)  vs. `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in `copilot-conflict-resolution.ts` [8](#0-7) ), any divergence between the two parses (or any input where the first parse decides "N hunks" but a genuine marker in the file is not well-formed by the *second* parser's stricter lookahead) results in `reassembleResolvedFile` falling back to "copy through as regular content" for what was actually a real, still-open conflict block, while `hunkIndex` for the *rest* of the file has already been (or will be) advanced against the model's hunk list — misaligning subsequent splices, or leaving raw `<<<<<<<...=======...>>>>>>>` markers embedded verbatim in the "resolved" content.

Exactly like `AssetManager.withdraw()` returning `true` regardless of `remaining`, this function returns a string that the rest of the pipeline treats as "fully resolved" (`IFileResolution.resolvedContent`, documented as "the fully resolved file content (all conflict markers removed)" [9](#0-8) ) without ever re-scanning the output to confirm that invariant actually holds.

### Impact Explanation
If the reassembled `resolvedContent` retains literal git conflict markers or has a misaligned hunk splice, the write path (which the code comments describe as consuming this value directly for "UI, write path") writes that content to the working file and the multi-commit-operation flow proceeds to stage/commit it as a resolved merge/rebase/cherry-pick — exactly the "silent corruption of what the user commits or pushes" impact class called out in scope. A user could end up committing and pushing a file that still contains raw conflict-marker syntax (broken code, or code from the wrong side of the merge), discovering it only after the fact, with no diff review catching it because the dialog and commit flow assume the AI fully resolved the file.

### Likelihood Explanation
Triggering this requires an attacker-controlled branch/PR (a "cloned/fetched repository" per the valid-impact scope) that produces a merge/rebase conflict in a file containing lines that coincidentally or deliberately resemble conflict-marker syntax (`=======` as a markdown divider, decorative `>>>>>>>`/`<<<<<<<` banners, or genuinely nested/malformed markers from a prior bad merge) adjacent to a real conflict. This is a plausible but not everyday occurrence — it depends on the exact byte layout of the conflicting file and requires the user to invoke the Copilot conflict-resolution feature on that file, so likelihood is moderate, matching the original report's "conditional on a specific but not-rare precondition" framing.

### Recommendation
After reassembly, re-scan the final `resolvedContent` for any of the three conflict-marker regexes and treat their presence as a hard failure (throw `CopilotValidationError`) rather than silently shipping content that still contains markers or was affected by a parse mismatch. Additionally, unify the marker-parsing logic between `extractConflictHunks` (`copilot-conflict-context.ts`) and `reassembleResolvedFile` (`copilot-conflict-resolution.ts`) into a single shared parser so that hunk counting and hunk splicing can never diverge on the same input, closing the "counted N but spliced differently" gap analogous to requiring `AssetManager.withdraw()`'s actual amount to equal the requested one rather than trusting a boolean return value.

### Proof of Concept
1. Set up a merge/rebase where the conflicted file's non-conflicting content includes decorative lines that match the conflict-marker regexes but are not real git markers (e.g. a code comment banner using `=======` for section dividers, or a documentation file literally showing example conflict markers as prose), positioned so they interleave with a real, genuine conflict block.
2. Trigger GitHub Desktop's "Resolve with Copilot" flow on this file. `buildConflictContext`/`extractConflictHunks` parses the file into what it believes are N hunks.
3. During `reassembleResolutions` → `reassembleResolvedFile`, construct (via controlling the exact byte offsets in the branch content) a case where the lookahead for the real conflict's `>>>>>>>` fails the `hasSeparator && closingIndex !== -1` check because a decorative `=======`/`>>>>>>>` line intervenes — the function falls into the "malformed marker" branch for a real, still-conflicted block.
4. Observe that `reassembleResolvedFile` returns a string containing the literal, unresolved `<<<<<<<`/`=======`/`>>>>>>>` markers for that block, yet `reassembleResolutions` returns it as a normal `IFileResolution` with no error, and no downstream code re-checks for marker presence before writing/staging the file — the user's next commit or push silently includes broken/conflict-marker-laden content.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L27-41)
```typescript
export interface IFileResolution {
  /** Repository-relative file path that was resolved. */
  readonly path: string
  /** The fully resolved file content (all conflict markers removed). */
  readonly resolvedContent: string
  /** Human-readable explanation of how and why conflicts were resolved this way. */
  readonly reasoning: string
  /**
   * For delete-vs-modify conflicts: the model's recommendation.
   * When present, `resolvedContent` is not meaningful — the resolution
   * is applied as a `ManualConflictResolution` (keep = non-deleted side,
   * delete = deleted side).
   */
  readonly deleteConflictAction?: 'keep' | 'delete'
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L438-449)
```typescript
      if (typeof hunkObj.resolvedContent !== 'string') {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: "resolvedContent" at hunk ${j} of file "${path}" must be a string`
        )
      }
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
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

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
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
